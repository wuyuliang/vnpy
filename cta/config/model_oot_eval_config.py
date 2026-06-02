"""OOT real-execution evaluation config for model pipeline."""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from types import MappingProxyType

from cta.config.cluster_bond_filter_manifest import (
    STRICT_BOND_PERCENTILE_THRESHOLDS,
    STRICT_BOND_RAW_THRESHOLDS,
)
from cta.config.cost_manifest import build_cluster_interval_cost_dict
from cta.config.baseline_skill_suite_config import LABEL_MAE_PENALTY
from cta.config.symbol_cluster_config import SYMBOL_CLUSTER_BY_PREFIX
from cta.portfolio_logic.config import PortfolioLogicConfig, normalize_portfolio_interval
from cta.risk.config import RiskSystemConfig
from cta.risk.guards.config import (
    ConsecutiveLossGuardConfig,
    LiquidityFloorGuardConfig,
    ScoreDistributionDriftConfig,
    SignalConcentrationGuardConfig,
)


def _default_commission_pct_by_cluster_interval() -> dict[str, float]:
    """Default real-cost commission table (Action 1 default-on, 2026-05-24)."""
    return build_cluster_interval_cost_dict(kind="commission")


def _default_slippage_pct_by_cluster_interval() -> dict[str, float]:
    """Default real-cost slippage table (Action 1 default-on, 2026-05-24)."""
    return build_cluster_interval_cost_dict(kind="slippage")


def _default_trade_filter_percentile_threshold_by_cluster_interval() -> dict[str, float]:
    """Default cluster_bond strict trade_filter percentile thresholds (Action 2 default-on, 2026-05-24)."""
    return dict(STRICT_BOND_PERCENTILE_THRESHOLDS)


def _default_trade_filter_raw_threshold_by_cluster_interval() -> dict[str, float]:
    """Default cluster_bond strict trade_filter raw thresholds (Action 2 default-on, 2026-05-24)."""
    return dict(STRICT_BOND_RAW_THRESHOLDS)

_KNOWN_INTERVALS = frozenset({"day", "60min", "30min", "15min", "5min", "min"})
_KNOWN_CLUSTERS = frozenset({"other", *{str(v).lower() for v in SYMBOL_CLUSTER_BY_PREFIX.values()}})


def _normalize_cluster_interval_key(field_name: str, key: object) -> tuple[str, str, str]:
    parts = str(key).strip().lower().split("|")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"{field_name} key must be 'cluster|interval', got {key!r}")
    cluster = str(parts[0]).strip().lower()
    interval = normalize_portfolio_interval(parts[1])
    if cluster not in _KNOWN_CLUSTERS:
        raise ValueError(
            f"{field_name} unknown cluster {cluster!r}; allowed={sorted(_KNOWN_CLUSTERS)}"
        )
    if interval not in _KNOWN_INTERVALS:
        raise ValueError(
            f"{field_name} unknown interval {parts[1]!r} (normalized {interval!r}); "
            f"allowed={sorted(_KNOWN_INTERVALS)}"
        )
    return cluster, interval, f"{cluster}|{interval}"


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
    # "raw"：沿用全局 raw probability 阈值；
    # "cluster_interval_percentile"：使用 cluster+interval 内百分位，避免 index/day
    # 这类 raw prob 分布偏低的组被全局 0.62 系统性误杀。
    # 2026-05-19：默认从 "raw" 切到 "cluster_interval_percentile"，与 DEFAULT_OOT_EVAL_CONFIG
    # 长期保持一致。背景：BOND/INDEX 等簇 raw prob 中位数 ~0.48-0.51，全局 0.62 阈值
    # 导致 62% blocked_trade_filter（详见 2026-05-18 OOT 诊断）。
    trade_filter_gate_mode: str = "cluster_interval_percentile"
    # 0.55 是 sklearn 默认二分类决策线，但 CTA 突破策略上 trade_filter 0.55-0.6 区间
    # 过宽（commission/slip 占 gross 50%+，需要更精的信号才能吃成本）。
    # 0.62 经验值：在 20260514 复盘里 win_rate 42.5%、毛利正、净亏损是成本问题；
    # 把阈值提到 0.62 期望 trade_count 减少 30-40%、win_rate 提升至 50%+、
    # 总 commission/slip 同步下降，net_pnl 转正。
    trade_filter_threshold: float = 0.62
    trade_filter_percentile_threshold: float = 70.0
    # 2026-05-24：默认从空 dict 翻为 cluster_bond 严格阈值（见 cluster_bond_filter_manifest.py）。
    # 通过显式 `trade_filter_*_threshold_by_cluster_interval={}` 可回退到全局阈值，
    # 也可显式塞入自定义 cluster 的阈值（dict 整体替换，不 merge）。
    trade_filter_raw_threshold_by_cluster_interval: dict[str, float] = field(
        default_factory=_default_trade_filter_raw_threshold_by_cluster_interval
    )
    trade_filter_percentile_threshold_by_cluster_interval: dict[str, float] = field(
        default_factory=_default_trade_filter_percentile_threshold_by_cluster_interval
    )
    # 按 signal_type 调整 trade_filter 阈值（用于“增/减某类机会笔数”）。
    # 百分位模式下：threshold += delta（单位=百分点）；raw 模式下：threshold += delta（单位=概率值）。
    # 2026-05-31 目标配比版：pullback 强放量，atr/低质量类型强缩量。
    # 口径：先用 trade_filter 阈值做粗分流，再用并发/名义上限做二次限流。
    trade_filter_raw_threshold_delta_by_signal_type: dict[str, float] = field(
        default_factory=lambda: {
            "bull_pullback_continuation": -0.08,
            "breakout_pullback_continuation": -0.06,
            "cross_sectional_momentum": 0.03,
            "trend_acceleration_breakout": 0.08,
            "atr_breakout": 0.15,
            "donchian_breakout": 0.12,
            "tight_range_breakout": 0.18,
        }
    )
    trade_filter_percentile_threshold_delta_by_signal_type: dict[str, float] = field(
        default_factory=lambda: {
            "bull_pullback_continuation": -20.0,
            "breakout_pullback_continuation": -16.0,
            "cross_sectional_momentum": 6.0,
            "trend_acceleration_breakout": 15.0,
            "atr_breakout": 30.0,
            "donchian_breakout": 25.0,
            "tight_range_breakout": 35.0,
        }
    )
    # ranker 阶段的最小分位数门槛偏置（score/min_prob 侧二次分流）。
    # 作用：trade_filter 放宽后，避免核心 alpha 在 ranker 再次被统一 min_prob=60 误杀；
    # 同时可对低质量类型抬高 ranker 侧准入门槛，减少其占用有限名额。
    # 口径：effective_min_prob_pctl = base_min_prob_pctl + delta（负值=放宽，正值=收紧）。
    # 代码上等价为：effective_prob_pctl = clip(raw_prob_pctl - delta, 0, 100)。
    ranker_prob_pctl_delta_by_signal_type: dict[str, float] = field(
        default_factory=lambda: {
            "bull_pullback_continuation": -20.0,
            "breakout_pullback_continuation": -10.0,
            "cross_sectional_momentum": 8.0,
            "trend_acceleration_breakout": 15.0,
            "atr_breakout": 30.0,
            "donchian_breakout": 20.0,
            "tight_range_breakout": 30.0,
        }
    )
    # 场景阈值：cluster|interval|side|bull_mode（如 "index|day|long|attack": 65）
    trade_filter_raw_threshold_by_cluster_interval_side_bull_mode: dict[str, float] = field(default_factory=dict)
    trade_filter_percentile_threshold_by_cluster_interval_side_bull_mode: dict[str, float] = field(default_factory=dict)
    # 未命中显式 side+bull_mode override 时，attack 模式下按 side 做阈值偏移（百分位点）。
    trade_filter_percentile_threshold_attack_long_delta: float = -5.0
    trade_filter_percentile_threshold_attack_short_delta: float = 10.0
    trade_filter_raw_threshold_attack_long_delta: float = -0.03
    trade_filter_raw_threshold_attack_short_delta: float = 0.08
    # Rotation 候选若希望绕过 trade-filter，由调用方显式 opt-in（H4 修复：默认空，
    # 保证 rotation off 时无任何 bypass 副作用；rotation on 时调用方传
    # ("cross_sectional_momentum",) 显式启用）。
    trade_filter_bypass_signal_types: tuple[str, ...] = ()
    # 全链路 signal_type 黑名单（OOT/eval/sim/live 一致）：命中即直接过滤，不参与后续 gate。
    signal_type_blacklist: tuple[str, ...] = (
        "atr_breakout",
        "donchian_breakout",
        "trend_acceleration_breakout",
        "bull_volatility_contraction_breakout",
    )
    # bull_mode 识别配置（score 优先用 bull_strength_score 列，不存在时回退到 trade_filter_prob_pctl）
    bull_strength_score_column: str = "bull_strength_score"
    bull_attack_percentile_threshold: float = 70.0
    bull_late_risk_percentile_threshold: float = 35.0

    use_regime_gate: bool = True
    # long: block trend_down; short: block trend_up
    allow_range_in_regime_gate: bool = True

    use_mfe_mae_gate: bool = True
    mae_penalty: float = LABEL_MAE_PENALTY
    min_pred_edge_atr: float = 0.0

    # 资金与交易成本参数（净值口径）
    initial_capital: float = 10_000_000.0
    risk_per_trade_pct: float = 0.002
    max_single_loss_pct: float = 0.002
    commission_pct_per_trade: float = 0.0002
    slippage_pct_per_trade: float = 0.0001
    # 按 (cluster, interval) 真实费率覆盖。
    # 2026-05-24：默认从空 dict 翻为按品种分层真实费率（见 cost_manifest.py）。
    # 历史：2026-05-24 OOT 诊断发现 cost_pct 全是 3bp 硬编码，被严重低估的成本是 bond
    # 单簇亏损主因（cost/|gross|=152%）。引入此 override 后可分簇贴近真实费率，
    # 同时让 cluster_precious 等保证金大的簇免于不当高成本扣减。
    # 显式传 `commission_pct_by_cluster_interval={}` 可回退到 commission_pct_per_trade 全局值。
    commission_pct_by_cluster_interval: dict[str, float] = field(
        default_factory=_default_commission_pct_by_cluster_interval
    )
    slippage_pct_by_cluster_interval: dict[str, float] = field(
        default_factory=_default_slippage_pct_by_cluster_interval
    )
    # symbol 级真实费率 override；key 使用合约前缀（如 "EC0"），优先级高于 cluster|interval。
    commission_pct_by_symbol: dict[str, float] = field(default_factory=dict)
    slippage_pct_by_symbol: dict[str, float] = field(default_factory=dict)
    # 冲击成本：impact_cost_pct = impact_cost_k * sqrt(order_lots / adv_lots)。
    use_impact_cost: bool = False
    impact_cost_k: float = 0.10
    impact_adv_lots_column: str = "adv_lots"
    # 仓位 sizing：按“每笔最大可亏损金额”反推仓位
    use_position_sizing: bool = True
    min_pred_mae_atr_for_sizing: float = 0.5
    # 单笔仓位名义金额相对当时权益的上限。
    # 0.10 = 单笔最多吃 10% 权益；signal_type 系数在此基准上乘。
    max_position_scale: float = 0.10
    # 按 signal_type 调整单笔仓位上限：effective_cap = max_position_scale * multiplier。
    # 例如 pullback 类可放大，atr_breakout 可缩小。
    signal_type_size_multiplier: dict[str, float] = field(
        default_factory=lambda: {
            "bull_pullback_continuation": 4.0,
            "breakout_pullback_continuation": 3.0,
            "cross_sectional_momentum": 0.8,
            "trend_acceleration_breakout": 0.5,
            "atr_breakout": 0.2,
            "donchian_breakout": 0.25,
            "tight_range_breakout": 0.15,
        }
    )
    # 按 signal_type 限制同时在仓笔数，用于压高回撤类型的并发暴露。
    signal_type_max_concurrent_positions: dict[str, int] = field(
        default_factory=lambda: {
            "bull_pullback_continuation": 10,
            "breakout_pullback_continuation": 8,
            "cross_sectional_momentum": 2,
            "trend_acceleration_breakout": 1,
            "atr_breakout": 1,
            "donchian_breakout": 1,
            "tight_range_breakout": 1,
        }
    )
    # 按 signal_type 限"未平仓累计名义 / 当时权益"份额。未配置的类型不额外限制（None）。
    signal_type_max_notional_pct: dict[str, float] = field(
        default_factory=lambda: {
            "cross_sectional_momentum": 0.10,
            "atr_breakout": 0.08,
            "donchian_breakout": 0.06,
            "trend_acceleration_breakout": 0.05,
            "tight_range_breakout": 0.04,
        }
    )
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
    # 按 (cluster, interval) 覆盖全局 intrabar_stop_loss_pct。key 形如 "index|day"
    # （cluster 用 infer_symbol_cluster 的输出 lowercase，interval 用 normalize_portfolio_interval）。
    # 2026-05-20：默认暂时清空，回退到全局 1% 止损（intrabar_stop_loss_pct）。
    # 历史背景：2026-05-19 OOT 诊断把 INDEX day 13 笔全 hard_stop -1.00% 归因到 1% 过紧，
    # 用 2015-2023 历史 (high-low)/close 分布 P90 计算出每个 cluster 的推荐止损率（见下方注释）。
    # 但实测发现：放宽止损到 P90 后，策略本身的 short bias 让单笔最大亏损金额变大，
    # net_pnl 反而恶化（v3 -4808 vs v1 -4003）。在策略 short bias 治本之前，先回退到全局
    # 1% 默认；要重新启用按簇 override，参考 cta/model/tools/compute_stop_loss_manifest.py
    # 重新计算或直接复用以下历史值：
    #     "agri|day":     0.0289,   "black|day":    0.0451,
    #     "chemical|day": 0.0354,   "index|day":    0.0325,
    #     "metal|day":    0.0313,   "other|day":    0.0371,
    #     "precious|day": 0.0232,
    intrabar_stop_loss_pct_by_cluster_interval: dict[str, float] = field(default_factory=dict)
    # intrabar 成交量参与率约束：单次开/平仓手数不超过当根 bar 成交量的该比例。
    # 超过时，执行时间顺延到后续满足容量的 bar（仅在 bar 含 volume 列时生效）。
    enforce_intrabar_bar_volume_cap: bool = True
    intrabar_max_bar_volume_participation_pct: float = 0.01
    intrabar_volume_column: str = "volume"

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
    # symbol 级合约参数覆盖（用于期货手数取整/保证金更真实）：
    # {
    #   "RB0": {"contract_size": 10.0, "lot_size": 1.0, "margin_rate": 0.12},
    #   "IF0": {"contract_size": 300.0, "lot_size": 1.0, "margin_rate": 0.10},
    # }
    symbol_contract_specs: dict[str, dict[str, float]] = field(default_factory=dict)
    # 组合总杠杆上限：sum(open_notional) / equity <= max_total_leverage
    max_total_leverage: float = 2.0
    # 单日累计新开仓名义金额上限（相对当日开盘权益）
    max_daily_new_notional_pct: float = 1.0
    # 周回撤约束（按周内权益峰值计，默认 0.025=2.5%）。
    # 0.04 → 0.03 → 0.025 渐进收紧；2026-05-27 进一步从 3% 收紧到 2.5%：
    # 上一轮 OOT (oot_20260526_233900) Action 1/2/3 default-on 后笔数 4x，MDD 从 -4.6%
    # 加深到 -6.9%；笔均波动收敛后单笔回撤风险已分散，可在不损 Sharpe 的前提下
    # 进一步收紧周回撤预算。触发后靠 weekly_dd_position_scale_after_breach 缩仓
    # 而非完全停手，避免反复触发→停手→错过反弹的死循环。常用范围 1.5%-5%。
    weekly_max_drawdown_pct: float = 0.025
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
    # cta/model/pipeline_oot_evaluation.py 的 Fix-A/Fix-E 会按
    # htf_reference 实际包含的 interval 自动窄化 htf_intervals，避免误判 htf_missing。
    # 仅当某 interval 数据**应当存在但偶发缺失**时才考虑切到 "both"。
    # 详见 cta/docs/block_reason.md §4-§6。
    portfolio_logic: PortfolioLogicConfig = field(default_factory=PortfolioLogicConfig)
    # 风控编排（quantile/bucket/linear_dd/dynamic_bump）。None=关闭整个 risk orchestrator。
    risk_system: RiskSystemConfig | None = None
    # OOT 侧规则链回放（C4）：按时间顺序逐条回放候选并追加 risk_block_reason。
    use_oot_guard_chain: bool = False
    use_oot_consecutive_loss_guard: bool = False
    use_oot_signal_concentration_guard: bool = False
    use_oot_score_distribution_guard: bool = False
    oot_consecutive_loss_guard: ConsecutiveLossGuardConfig = field(
        default_factory=ConsecutiveLossGuardConfig
    )
    oot_signal_concentration_guard: SignalConcentrationGuardConfig = field(
        default_factory=SignalConcentrationGuardConfig
    )
    oot_score_distribution_guard: ScoreDistributionDriftConfig = field(
        default_factory=ScoreDistributionDriftConfig
    )
    use_liquidity_floor_guard: bool = True
    liquidity_floor_guard: LiquidityFloorGuardConfig = field(default_factory=LiquidityFloorGuardConfig)
    stop_loss_consistency_tolerance: float = 0.005
    # 默认强制训练 label 与 OOT 执行止损口径一致，避免漂移。
    # 研究/单测若需要临时放开，可显式传 enforce_stop_loss_consistency=False。
    enforce_stop_loss_consistency: bool = True

    def __post_init__(self) -> None:
        def _check(name: str, value: float, lo: float, hi: float) -> None:
            v = float(value)
            if not (lo <= v <= hi):
                raise ValueError(f"{name} out of range [{lo}, {hi}]: {v}")

        _check("stacking_score_threshold", self.stacking_score_threshold, 0.0, 1.0)
        if self.trade_filter_gate_mode not in {"raw", "cluster_interval_percentile"}:
            raise ValueError(f"unsupported trade_filter_gate_mode={self.trade_filter_gate_mode}")
        _check("trade_filter_threshold", self.trade_filter_threshold, 0.0, 1.0)
        _check("trade_filter_percentile_threshold", self.trade_filter_percentile_threshold, 0.0, 100.0)
        for key, value in self.trade_filter_raw_threshold_by_cluster_interval.items():
            _check(f"trade_filter_raw_threshold_by_cluster_interval[{key}]", float(value), 0.0, 1.0)
        for key, value in self.trade_filter_percentile_threshold_by_cluster_interval.items():
            _check(f"trade_filter_percentile_threshold_by_cluster_interval[{key}]", float(value), 0.0, 100.0)
        norm_trade_filter_raw_delta_by_signal_type: dict[str, float] = {}
        for key, value in dict(self.trade_filter_raw_threshold_delta_by_signal_type).items():
            signal_type = str(key).strip().lower()
            if not signal_type:
                continue
            _check(f"trade_filter_raw_threshold_delta_by_signal_type[{key}]", float(value), -1.0, 1.0)
            norm_trade_filter_raw_delta_by_signal_type[signal_type] = float(value)
        norm_trade_filter_percentile_delta_by_signal_type: dict[str, float] = {}
        for key, value in dict(self.trade_filter_percentile_threshold_delta_by_signal_type).items():
            signal_type = str(key).strip().lower()
            if not signal_type:
                continue
            _check(
                f"trade_filter_percentile_threshold_delta_by_signal_type[{key}]",
                float(value),
                -100.0,
                100.0,
            )
            norm_trade_filter_percentile_delta_by_signal_type[signal_type] = float(value)
        norm_ranker_prob_pctl_delta_by_signal_type: dict[str, float] = {}
        for key, value in dict(self.ranker_prob_pctl_delta_by_signal_type).items():
            signal_type = str(key).strip().lower()
            if not signal_type:
                continue
            _check(
                f"ranker_prob_pctl_delta_by_signal_type[{key}]",
                float(value),
                -100.0,
                100.0,
            )
            norm_ranker_prob_pctl_delta_by_signal_type[signal_type] = float(value)
        for key, value in self.trade_filter_raw_threshold_by_cluster_interval_side_bull_mode.items():
            _check(f"trade_filter_raw_threshold_by_cluster_interval_side_bull_mode[{key}]", float(value), 0.0, 1.0)
        for key, value in self.trade_filter_percentile_threshold_by_cluster_interval_side_bull_mode.items():
            _check(
                f"trade_filter_percentile_threshold_by_cluster_interval_side_bull_mode[{key}]",
                float(value),
                0.0,
                100.0,
            )
        _check("bull_attack_percentile_threshold", self.bull_attack_percentile_threshold, 0.0, 100.0)
        _check("bull_late_risk_percentile_threshold", self.bull_late_risk_percentile_threshold, 0.0, 100.0)
        _check("risk_per_trade_pct", self.risk_per_trade_pct, 0.0, 1.0)
        _check("max_single_loss_pct", self.max_single_loss_pct, 0.0, 1.0)
        _check("commission_pct_per_trade", self.commission_pct_per_trade, 0.0, 0.02)
        _check("slippage_pct_per_trade", self.slippage_pct_per_trade, 0.0, 0.02)
        # 按 (cluster, interval) 真实费率 override：key 形式 + 数值区间校验
        norm_commission_overrides: dict[str, float] = {}
        for key, value in dict(self.commission_pct_by_cluster_interval).items():
            _cluster, _interval, norm_key = _normalize_cluster_interval_key(
                "commission_pct_by_cluster_interval", key
            )
            _check(
                f"commission_pct_by_cluster_interval[{key}]",
                float(value),
                0.0,
                0.02,
            )
            norm_commission_overrides[norm_key] = float(value)
        norm_slippage_overrides: dict[str, float] = {}
        for key, value in dict(self.slippage_pct_by_cluster_interval).items():
            _cluster, _interval, norm_key = _normalize_cluster_interval_key(
                "slippage_pct_by_cluster_interval", key
            )
            _check(
                f"slippage_pct_by_cluster_interval[{key}]",
                float(value),
                0.0,
                0.02,
            )
            norm_slippage_overrides[norm_key] = float(value)
        norm_commission_symbol: dict[str, float] = {}
        for key, value in dict(self.commission_pct_by_symbol).items():
            sym = str(key).strip().upper()
            if not sym:
                continue
            _check(f"commission_pct_by_symbol[{key}]", float(value), 0.0, 0.02)
            norm_commission_symbol[sym] = float(value)
        norm_slippage_symbol: dict[str, float] = {}
        for key, value in dict(self.slippage_pct_by_symbol).items():
            sym = str(key).strip().upper()
            if not sym:
                continue
            _check(f"slippage_pct_by_symbol[{key}]", float(value), 0.0, 0.02)
            norm_slippage_symbol[sym] = float(value)
        _check("impact_cost_k", self.impact_cost_k, 0.0, 1.0)
        _check("max_position_scale", self.max_position_scale, 0.0, 1.0)
        norm_signal_type_size_multiplier: dict[str, float] = {}
        for key, value in dict(self.signal_type_size_multiplier).items():
            signal_type = str(key).strip().lower()
            if not signal_type:
                continue
            _check(f"signal_type_size_multiplier[{key}]", float(value), 0.0, 5.0)
            if float(value) <= 0.0:
                raise ValueError(f"signal_type_size_multiplier[{key}] must be > 0")
            norm_signal_type_size_multiplier[signal_type] = float(value)
        norm_signal_type_max_concurrent: dict[str, int] = {}
        for key, value in dict(self.signal_type_max_concurrent_positions).items():
            signal_type = str(key).strip().lower()
            if not signal_type:
                continue
            count = int(value)
            if count < 1:
                raise ValueError(
                    f"signal_type_max_concurrent_positions[{key}] must be >= 1, got {value!r}"
                )
            norm_signal_type_max_concurrent[signal_type] = count
        norm_signal_type_max_notional: dict[str, float] = {}
        for key, value in dict(self.signal_type_max_notional_pct).items():
            signal_type = str(key).strip().lower()
            if not signal_type:
                continue
            _check(f"signal_type_max_notional_pct[{key}]", float(value), 0.0, 1.0)
            if not 0.0 < float(value) <= 1.0:
                raise ValueError(
                    f"signal_type_max_notional_pct[{key}] must be in (0, 1], got {value!r}"
                )
            norm_signal_type_max_notional[signal_type] = float(value)
        # 止损率合理区间：CTA 60min 上 0.5%-5%（小于 0.5% 必被噪音打掉，大于 5% 太松）
        _check("intrabar_stop_loss_pct", self.intrabar_stop_loss_pct, 0.001, 0.10)
        norm_intrabar_stop_overrides: dict[str, float] = {}
        for key, value in dict(self.intrabar_stop_loss_pct_by_cluster_interval).items():
            _cluster, _interval, norm_key = _normalize_cluster_interval_key(
                "intrabar_stop_loss_pct_by_cluster_interval", key
            )
            _check(
                f"intrabar_stop_loss_pct_by_cluster_interval[{key}]",
                float(value),
                0.001,
                0.10,
            )
            norm_intrabar_stop_overrides[norm_key] = float(value)
        _check(
            "intrabar_max_bar_volume_participation_pct",
            self.intrabar_max_bar_volume_participation_pct,
            0.0,
            1.0,
        )
        if not str(self.intrabar_volume_column).strip():
            raise ValueError("intrabar_volume_column must be non-empty")
        _check("stop_loss_consistency_tolerance", self.stop_loss_consistency_tolerance, 0.0, 0.05)
        _check("weekly_max_drawdown_pct", self.weekly_max_drawdown_pct, 0.0, 1.0)
        _check("weekly_dd_position_scale_after_breach", self.weekly_dd_position_scale_after_breach, 0.0, 1.0)
        _check("monthly_max_drawdown_pct", self.monthly_max_drawdown_pct, 0.0, 1.0)
        _check("max_total_leverage", self.max_total_leverage, 0.0, 10.0)
        if self.risk_system is not None and not isinstance(self.risk_system, RiskSystemConfig):
            raise ValueError("risk_system must be RiskSystemConfig or None")
        if not isinstance(self.oot_consecutive_loss_guard, ConsecutiveLossGuardConfig):
            raise ValueError("oot_consecutive_loss_guard must be ConsecutiveLossGuardConfig")
        if not isinstance(self.oot_signal_concentration_guard, SignalConcentrationGuardConfig):
            raise ValueError("oot_signal_concentration_guard must be SignalConcentrationGuardConfig")
        if not isinstance(self.oot_score_distribution_guard, ScoreDistributionDriftConfig):
            raise ValueError("oot_score_distribution_guard must be ScoreDistributionDriftConfig")
        if not isinstance(self.liquidity_floor_guard, LiquidityFloorGuardConfig):
            raise ValueError("liquidity_floor_guard must be LiquidityFloorGuardConfig")
        object.__setattr__(
            self,
            "trade_filter_raw_threshold_by_cluster_interval",
            MappingProxyType(dict(self.trade_filter_raw_threshold_by_cluster_interval)),
        )
        object.__setattr__(
            self,
            "trade_filter_percentile_threshold_by_cluster_interval",
            MappingProxyType(dict(self.trade_filter_percentile_threshold_by_cluster_interval)),
        )
        object.__setattr__(
            self,
            "trade_filter_raw_threshold_delta_by_signal_type",
            MappingProxyType(norm_trade_filter_raw_delta_by_signal_type),
        )
        object.__setattr__(
            self,
            "trade_filter_percentile_threshold_delta_by_signal_type",
            MappingProxyType(norm_trade_filter_percentile_delta_by_signal_type),
        )
        object.__setattr__(
            self,
            "ranker_prob_pctl_delta_by_signal_type",
            MappingProxyType(norm_ranker_prob_pctl_delta_by_signal_type),
        )
        object.__setattr__(
            self,
            "trade_filter_raw_threshold_by_cluster_interval_side_bull_mode",
            MappingProxyType(dict(self.trade_filter_raw_threshold_by_cluster_interval_side_bull_mode)),
        )
        object.__setattr__(
            self,
            "trade_filter_percentile_threshold_by_cluster_interval_side_bull_mode",
            MappingProxyType(dict(self.trade_filter_percentile_threshold_by_cluster_interval_side_bull_mode)),
        )
        object.__setattr__(
            self,
            "trade_filter_bypass_signal_types",
            tuple(
                dict.fromkeys(
                    str(signal_type).strip().lower()
                    for signal_type in self.trade_filter_bypass_signal_types
                    if str(signal_type).strip()
                )
            ),
        )
        object.__setattr__(
            self,
            "signal_type_blacklist",
            tuple(
                dict.fromkeys(
                    self.normalize_signal_type(signal_type)
                    for signal_type in self.signal_type_blacklist
                    if self.normalize_signal_type(signal_type)
                )
            ),
        )
        object.__setattr__(
            self,
            "signal_type_size_multiplier",
            MappingProxyType(norm_signal_type_size_multiplier),
        )
        object.__setattr__(
            self,
            "signal_type_max_concurrent_positions",
            MappingProxyType(norm_signal_type_max_concurrent),
        )
        object.__setattr__(
            self,
            "signal_type_max_notional_pct",
            MappingProxyType(norm_signal_type_max_notional),
        )
        object.__setattr__(
            self,
            "intrabar_stop_loss_pct_by_cluster_interval",
            MappingProxyType(norm_intrabar_stop_overrides),
        )
        object.__setattr__(
            self,
            "commission_pct_by_cluster_interval",
            MappingProxyType(norm_commission_overrides),
        )
        object.__setattr__(
            self,
            "slippage_pct_by_cluster_interval",
            MappingProxyType(norm_slippage_overrides),
        )
        object.__setattr__(
            self,
            "commission_pct_by_symbol",
            MappingProxyType(norm_commission_symbol),
        )
        object.__setattr__(
            self,
            "slippage_pct_by_symbol",
            MappingProxyType(norm_slippage_symbol),
        )
        object.__setattr__(self, "symbol_contract_specs", MappingProxyType(dict(self.symbol_contract_specs)))
        if bool(self.use_intrabar_stop_tracking) and bool(self.enforce_stop_loss_consistency):
            self._validate_stop_loss_pct_consistency()

    @staticmethod
    def normalize_signal_type(signal_type: object) -> str:
        """Normalize signal type key for config lookup."""
        return str(signal_type or "").strip().lower()

    def is_signal_type_blacklisted(self, signal_type: object) -> bool:
        """Return whether signal_type is blocked by config blacklist."""
        key = self.normalize_signal_type(signal_type)
        return bool(key) and key in self.signal_type_blacklist

    def resolve_signal_type_size_multiplier(self, signal_type: object) -> float:
        """Resolve sizing multiplier by signal type, defaulting to 1.0."""
        key = self.normalize_signal_type(signal_type)
        return float(self.signal_type_size_multiplier.get(key, 1.0))

    def resolve_effective_position_scale_cap(self, signal_type: object) -> float:
        """Resolve effective position-scale cap after signal_type multiplier."""
        cap = float(self.max_position_scale) * float(self.resolve_signal_type_size_multiplier(signal_type))
        return float(min(max(cap, 0.0), 1.0))

    def resolve_signal_type_max_concurrent(self, signal_type: object) -> int | None:
        """Resolve optional concurrent-position cap by signal type."""
        key = self.normalize_signal_type(signal_type)
        raw = self.signal_type_max_concurrent_positions.get(key)
        if raw is None:
            return None
        return int(raw)

    def resolve_signal_type_max_notional_pct(self, signal_type: object) -> float | None:
        """Resolve optional per-signal_type notional-share cap (fraction of equity)."""
        key = self.normalize_signal_type(signal_type)
        raw = self.signal_type_max_notional_pct.get(key)
        if raw is None:
            return None
        return float(raw)

    def resolve_ranker_prob_pctl_delta(self, signal_type: object) -> float:
        """Resolve ranker min_prob percentile delta by signal type."""
        key = self.normalize_signal_type(signal_type)
        return float(self.ranker_prob_pctl_delta_by_signal_type.get(key, 0.0))

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


DEFAULT_OOT_EVAL_CONFIG = OotEvaluationConfig(
    trade_filter_gate_mode="cluster_interval_percentile",
    trade_filter_percentile_threshold=70.0,
)


__all__ = [
    "OotEvaluationConfig",
    "DEFAULT_OOT_EVAL_CONFIG",
]
