"""OOT real-execution evaluation config for model pipeline."""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from types import MappingProxyType

from cta.config.baseline_skill_suite_config import LABEL_MAE_PENALTY
from cta.config.symbol_cluster_config import SYMBOL_CLUSTER_BY_PREFIX
from cta.feature.regime import REGIME_LABELS
from cta.portfolio_logic.config import PortfolioLogicConfig, normalize_portfolio_interval

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
    trade_filter_raw_threshold_by_cluster_interval: dict[str, float] = field(default_factory=dict)
    trade_filter_percentile_threshold_by_cluster_interval: dict[str, float] = field(default_factory=dict)
    # 场景阈值：cluster|interval|side|bull_mode（如 "index|day|long|attack": 65）
    trade_filter_raw_threshold_by_cluster_interval_side_bull_mode: dict[str, float] = field(default_factory=dict)
    trade_filter_percentile_threshold_by_cluster_interval_side_bull_mode: dict[str, float] = field(default_factory=dict)
    # 未命中显式 side+bull_mode override 时，attack 模式下按 side 做阈值偏移（百分位点）。
    trade_filter_percentile_threshold_attack_long_delta: float = -5.0
    trade_filter_percentile_threshold_attack_short_delta: float = 10.0
    trade_filter_raw_threshold_attack_long_delta: float = -0.03
    trade_filter_raw_threshold_attack_short_delta: float = 0.08
    # bull_mode 识别配置（score 优先用 bull_strength_score 列，不存在时回退到 trade_filter_prob_pctl）
    bull_strength_score_column: str = "bull_strength_score"
    bull_attack_percentile_threshold: float = 70.0
    bull_late_risk_percentile_threshold: float = 35.0

    use_regime_gate: bool = True
    # long: block trend_down; short: block trend_up
    allow_range_in_regime_gate: bool = True

    # MA-cross 趋势过滤（默认 off，按 (cluster, interval) 灰度启用）。
    # 设计文档：cta/docs/ma_cross_regime_aware_design.md
    # 规则：ma_alignment >= 1（多头排列）拦 short；<= -1（空头排列）拦 long。
    # 列源：优先 generic_ma_alignment（候选+通用特征拼接后的列名），缺失时回退 ma_alignment。
    use_ma_cross_gate: bool = False
    # 预留：未来若需要在 gate 内现算 MA 用（当前 gate 不现算）。
    ma_cross_fast_window: int = 5
    ma_cross_slow_window: int = 20
    ma_cross_alignment_column: str = "generic_ma_alignment"
    # {"index|day": True, "black|day": True, ...}；未列出/value=False 的 (cluster, interval) 不启用本 gate。
    ma_cross_enabled_by_cluster_interval: dict[str, bool] = field(default_factory=dict)

    # Regime-aware short filter（默认 off，按 (cluster, interval) 灰度启用）。
    # 用真实历史 regime_label（cta/strategy/baseline_setup_detection._infer_regime_label），
    # 不依赖模型预测的 pred_regime_label。规则：side=short AND label ∈ block_labels → 拦截。
    use_regime_short_filter: bool = False
    regime_short_filter_label_column: str = "regime_label"
    regime_short_block_labels: tuple[str, ...] = ("trend_up",)
    regime_short_filter_enabled_by_cluster_interval: dict[str, bool] = field(default_factory=dict)

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
    # cta/model/pipeline_oot_evaluation.py 的 Fix-A/Fix-E 会按
    # htf_reference 实际包含的 interval 自动窄化 htf_intervals，避免误判 htf_missing。
    # 仅当某 interval 数据**应当存在但偶发缺失**时才考虑切到 "both"。
    # 详见 cta/docs/block_reason.md §4-§6。
    portfolio_logic: PortfolioLogicConfig = field(default_factory=PortfolioLogicConfig)
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
        _check("max_position_scale", self.max_position_scale, 0.0, 1.0)
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
        # MA-cross + regime-short-filter override 校验（key 形如 "cluster|interval"，value 为 bool）。
        norm_ma_cross_enabled: dict[str, bool] = {}
        norm_regime_short_enabled: dict[str, bool] = {}
        for field_name, mapping in (
            ("ma_cross_enabled_by_cluster_interval", self.ma_cross_enabled_by_cluster_interval),
            (
                "regime_short_filter_enabled_by_cluster_interval",
                self.regime_short_filter_enabled_by_cluster_interval,
            ),
        ):
            for key, value in dict(mapping).items():
                _cluster, _interval, norm_key = _normalize_cluster_interval_key(field_name, key)
                if not isinstance(value, bool):
                    raise ValueError(
                        f"{field_name}[{key}] must be bool, got {type(value).__name__}"
                    )
                if field_name == "ma_cross_enabled_by_cluster_interval":
                    norm_ma_cross_enabled[norm_key] = bool(value)
                else:
                    norm_regime_short_enabled[norm_key] = bool(value)
        # MA-cross 窗口校验：fast < slow，且都为正整数。
        if int(self.ma_cross_fast_window) <= 0 or int(self.ma_cross_slow_window) <= 0:
            raise ValueError(
                f"ma_cross_fast_window/slow_window must be positive: "
                f"{self.ma_cross_fast_window}/{self.ma_cross_slow_window}"
            )
        if int(self.ma_cross_fast_window) >= int(self.ma_cross_slow_window):
            raise ValueError(
                f"ma_cross_fast_window ({self.ma_cross_fast_window}) must be < "
                f"slow_window ({self.ma_cross_slow_window})"
            )
        # regime_short_block_labels 不能为空且必须是合法 regime 标签子集。
        if not self.regime_short_block_labels:
            raise ValueError("regime_short_block_labels must be non-empty when use_regime_short_filter=True")
        _allowed_regimes = set(REGIME_LABELS)
        for label in self.regime_short_block_labels:
            if str(label).lower() not in _allowed_regimes:
                raise ValueError(
                    f"regime_short_block_labels contains unknown label {label!r}; "
                    f"allowed = {sorted(_allowed_regimes)}"
                )
        _check("stop_loss_consistency_tolerance", self.stop_loss_consistency_tolerance, 0.0, 0.05)
        _check("weekly_max_drawdown_pct", self.weekly_max_drawdown_pct, 0.0, 1.0)
        _check("weekly_dd_position_scale_after_breach", self.weekly_dd_position_scale_after_breach, 0.0, 1.0)
        _check("monthly_max_drawdown_pct", self.monthly_max_drawdown_pct, 0.0, 1.0)
        _check("max_total_leverage", self.max_total_leverage, 0.0, 10.0)
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
            "intrabar_stop_loss_pct_by_cluster_interval",
            MappingProxyType(norm_intrabar_stop_overrides),
        )
        object.__setattr__(
            self,
            "ma_cross_enabled_by_cluster_interval",
            MappingProxyType(norm_ma_cross_enabled),
        )
        object.__setattr__(
            self,
            "regime_short_filter_enabled_by_cluster_interval",
            MappingProxyType(norm_regime_short_enabled),
        )
        object.__setattr__(self, "symbol_contract_specs", MappingProxyType(dict(self.symbol_contract_specs)))
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


DEFAULT_OOT_EVAL_CONFIG = OotEvaluationConfig(
    trade_filter_gate_mode="cluster_interval_percentile",
    trade_filter_percentile_threshold=70.0,
)


__all__ = [
    "OotEvaluationConfig",
    "DEFAULT_OOT_EVAL_CONFIG",
]
