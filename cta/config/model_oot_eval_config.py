"""OOT real-execution evaluation config for model pipeline."""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field

from cta.config.baseline_skill_suite_config import LABEL_MAE_PENALTY
from cta.portfolio_logic.config import PortfolioLogicConfig


@dataclass(frozen=True)
class OotEvaluationConfig:
    """Configuration for OOT monthly return + sharpe evaluation.

    口径：
    - 仅使用预测表中的 test split（OOT）；
    - 仅统计真实已成交样本（is_executed==1）；
    - 先通过模型 gating（trade/regime/mfe_mae）再汇总月度收益。
    """

    use_test_split_only: bool = True
    use_last_window_only: bool = False
    require_executed_only: bool = True

    # P1.3: 以最终决策模型（stacking）为主 gate；缺列时回退到三段 gate。
    use_stacking_gate: bool = True
    stacking_score_column: str = "final_decision_score"
    stacking_score_threshold: float = 0.55
    stacking_gate_overrides_individual_gates: bool = True

    use_trade_filter_gate: bool = True
    # 0.55 是 sklearn 默认二分类决策线，但 CTA 突破策略上 trade_filter 0.55-0.6 区间
    # 过宽（commission/slip 占 gross 50%+，需要更精的信号才能吃成本）。
    # 0.62 经验值：在 20260514 复盘里 win_rate 42.5%、毛利正、净亏损是成本问题；
    # 把阈值提到 0.62 期望 trade_count 减少 30-40%、win_rate 提升至 50%+、
    # 总 commission/slip 同步下降，net_pnl 转正。
    trade_filter_threshold: float = 0.62

    use_regime_gate: bool = True
    # long: block trend_down; short: block trend_up
    allow_range_in_regime_gate: bool = True

    use_mfe_mae_gate: bool = True
    mae_penalty: float = LABEL_MAE_PENALTY
    min_pred_edge_atr: float = 0.0

    # 资金与交易成本参数（净值口径）
    initial_capital: float = 1_000_000.0
    risk_per_trade_pct: float = 0.002
    max_single_loss_pct: float = 0.001
    commission_pct_per_trade: float = 0.0002
    slippage_pct_per_trade: float = 0.0001
    # 仓位 sizing：按“每笔最大可亏损金额”反推仓位
    use_position_sizing: bool = True
    min_pred_mae_atr_for_sizing: float = 0.5
    # 单笔仓位名义金额相对当时权益的上限。
    # 0.10 = 单笔最多吃 10% 权益，避免反推 sizing 在 pred_mae 较小时打满整笔。
    max_position_scale: float = 0.10
    # 单品种"未平仓累计名义金额 / 当时权益"上限，避免同 symbol 连续触发信号把组合堆爆。
    max_symbol_notional_pct: float = 0.30
    # 单品种同时在仓笔数上限。
    max_concurrent_positions_per_symbol: int = 3
    # 组合内同时在仓笔数上限。
    max_concurrent_positions_total: int = 10
    # OOT逐笔成交详情：是否基于分钟级路径做止损跟踪与真实退出时刻回放
    use_intrabar_stop_tracking: bool = True
    # 是否启用 portfolio_logic 运行时（W2/W3/W4 分阶段接入）。
    # 为保持向后兼容默认关闭；开启后会在 intrabar 路径上启用 trailing / htf / ranker 等新逻辑。
    use_portfolio_logic_runtime: bool = False
    # 支持 day/60min/30min/15min/5min/min，默认用 60min 保证多品种可用
    intrabar_tracking_interval: str = "60min"
    # 主 interval 缺数据时，按顺序回退（去重后生效）
    intrabar_tracking_fallback_intervals: tuple[str, ...] = ("30min", "15min", "5min", "min")
    # 价格止损幅度（相对入场价）。**必须与 baseline_skill_suite.generate_candidate_opportunities
    # 的 label_stop_loss_pct 默认值保持一致**，否则训练 label 与 OOT 执行口径错位。
    # 0.01 = 1%（CTA 60min 上 ~1 倍 ATR 的常见止损量级），与 P0.5 label 修正同步。
    intrabar_stop_loss_pct: float = 0.01

    # 换月/展期成本建模（默认关闭，因为我们的回测用的是"连续主连价格序列"
    # cta/data/origin/，价格本身已经过 back/ratio adjust 处理，PnL 已隐含展期价差）。
    # 启用 use_roll_cost=True 会在已含展期 PnL 的价格上叠加 roll_cost，**双重计算**
    # 导致回测过度悲观（20260514 复盘：roll_cost 占 gross 70%，把所有 alpha 吃光）。
    # 仅当上游切换到"单合约价格 + 显式 rollover"流程时才把它打开。
    # 计费口径：roll_cost = notional * (annual_pct / 12) * n_roll_dates_in_holding_period
    use_roll_cost: bool = False
    default_roll_cost_pct_per_year: float = 0.05
    roll_cost_day_of_month: int = 14
    # 历史字段保留以避免破坏存量配置（不再实际使用，仅为向后兼容）。
    roll_cost_day_count: float = 365.0

    # 组合资金约束（更贴近真实 CTA 组合环境）
    use_portfolio_constraints: bool = True
    # 保证金占用比例：required_margin = notional * margin_rate
    margin_rate: float = 0.12
    # 组合总杠杆上限：sum(open_notional) / equity <= max_total_leverage
    max_total_leverage: float = 2.0
    # 单日累计新开仓名义金额上限（相对当日开盘权益）
    max_daily_new_notional_pct: float = 1.0
    # 周回撤约束（按周内权益峰值计，默认 0.03=3%）。
    # 已从 0.04 收敛到 0.03；触发后靠 weekly_dd_position_scale_after_breach 缩仓
    # 而非完全停手，避免反复触发→停手→错过反弹的死循环。常用范围 2%-5%。
    weekly_max_drawdown_pct: float = 0.03
    # 周回撤触发后的缩仓系数（0.5=半仓），用于“缩仓不停手”。
    weekly_dd_position_scale_after_breach: float = 0.5
    # 若周回撤触达阈值，是否暂停该周后续新开仓
    block_new_entries_on_weekly_dd_breach: bool = True
    # 开仓前按剩余周回撤预算裁剪仓位（保证周回撤不超阈值）
    enforce_weekly_dd_budget_on_entry: bool = True
    # 月回撤硬熔断（按月内权益峰值计）
    monthly_max_drawdown_pct: float = 0.08
    block_new_entries_on_monthly_dd_breach: bool = True

    # 超额收益基准（年化）
    benchmark_annual_return: float = 0.02
    risk_free_annual_return: float = 0.02

    annualization_factor: float = 12.0
    # portfolio_logic.interval_gate.fallback_when_htf_missing:
    #   "skip"（默认，严格）：缺 HTF 共识即拒单，emit block_reason=htf_missing。
    #   "both"              ：缺 HTF 时中性放行（视为 long/short 皆可）。
    # 单 interval 跑批（如 --interval day）一般不需要手动改这里 ——
    # cta/model/pipeline_oot_evaluation_source.py.txt 的 Fix-A 会按
    # htf_reference 实际包含的 interval 自动窄化 htf_intervals，避免误判 htf_missing。
    # 仅当某 interval 数据**应当存在但偶发缺失**时才考虑切到 "both"。
    # 详见 cta/docs/block_reason.md §4-§6。
    portfolio_logic: PortfolioLogicConfig = field(default_factory=PortfolioLogicConfig)
    stop_loss_consistency_tolerance: float = 0.005
    # 研究/单测场景经常会临时扫不同止损；默认不强制。
    # 生产默认配置（DEFAULT_OOT_EVAL_CONFIG）会显式开启，避免训练与 OOT 口径漂移。
    enforce_stop_loss_consistency: bool = False

    def __post_init__(self) -> None:
        def _check(name: str, value: float, lo: float, hi: float) -> None:
            v = float(value)
            if not (lo <= v <= hi):
                raise ValueError(f"{name} out of range [{lo}, {hi}]: {v}")

        _check("stacking_score_threshold", self.stacking_score_threshold, 0.0, 1.0)
        _check("trade_filter_threshold", self.trade_filter_threshold, 0.0, 1.0)
        _check("risk_per_trade_pct", self.risk_per_trade_pct, 0.0, 1.0)
        _check("max_single_loss_pct", self.max_single_loss_pct, 0.0, 1.0)
        _check("commission_pct_per_trade", self.commission_pct_per_trade, 0.0, 0.02)
        _check("slippage_pct_per_trade", self.slippage_pct_per_trade, 0.0, 0.02)
        _check("max_position_scale", self.max_position_scale, 0.0, 1.0)
        # 止损率合理区间：CTA 60min 上 0.5%-5%（小于 0.5% 必被噪音打掉，大于 5% 太松）
        _check("intrabar_stop_loss_pct", self.intrabar_stop_loss_pct, 0.001, 0.10)
        _check("stop_loss_consistency_tolerance", self.stop_loss_consistency_tolerance, 0.0, 0.05)
        _check("weekly_max_drawdown_pct", self.weekly_max_drawdown_pct, 0.0, 1.0)
        _check("weekly_dd_position_scale_after_breach", self.weekly_dd_position_scale_after_breach, 0.0, 1.0)
        _check("monthly_max_drawdown_pct", self.monthly_max_drawdown_pct, 0.0, 1.0)
        _check("max_total_leverage", self.max_total_leverage, 0.0, 10.0)
        if bool(self.use_intrabar_stop_tracking) and bool(self.enforce_stop_loss_consistency):
            self._validate_stop_loss_pct_consistency()

    @staticmethod
    def _default_label_stop_loss_pct() -> float:
        from cta.strategy.baseline_skill_suite import generate_candidate_opportunities

        sig = inspect.signature(generate_candidate_opportunities)
        param = sig.parameters.get("label_stop_loss_pct")
        if param is None or param.default is inspect.Parameter.empty:
            raise RuntimeError("generate_candidate_opportunities missing label_stop_loss_pct default")
        return float(param.default)

    def _validate_stop_loss_pct_consistency(self) -> None:
        label = float(self._default_label_stop_loss_pct())
        oot = float(self.intrabar_stop_loss_pct)
        diff = abs(label - oot)
        if diff > float(self.stop_loss_consistency_tolerance):
            raise ValueError(
                "intrabar_stop_loss_pct ({:.6f}) != label_stop_loss_pct ({:.6f}); "
                "diff={:.6f} > tolerance={:.6f}".format(
                    oot, label, diff, float(self.stop_loss_consistency_tolerance)
                )
            )


DEFAULT_OOT_EVAL_CONFIG = OotEvaluationConfig(enforce_stop_loss_consistency=True)


__all__ = [
    "OotEvaluationConfig",
    "DEFAULT_OOT_EVAL_CONFIG",
]
