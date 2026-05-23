# 盈利/趋势品种自适应放宽设计文档（profit_aware_trend_adaptive）

> 范围：`cta/portfolio_logic/`、`cta/strategy/`、`cta/feature/`、`cta/config/`、`cta/model/`
> 风格：与 [ma_cross_regime_aware_design.md](./ma_cross_regime_aware_design.md)、
> [cross_sectional_momentum_rotation_design.md](./cross_sectional_momentum_rotation_design.md)
> 对齐。snake_case、默认 off、按 `(cluster, interval)` 灰度启用。
> **本文档面向 codex / claude 实施**，§13 给出 TDD TODO 清单。

---

## §0 修订记录

| 日期 | 作者 | 变更 |
|---|---|---|
| 2026-05-23 | claude | 初稿。基于 2025 金银漏赚案例提出 5 模块（trailing TP + 4 自适应放宽）。 |
| 2026-05-23 | codex | P0-P3 首版实现完成（TDD）：新增配置/模块与 OOT 接入，默认 off。 |

---

## §1 概览与动机

### §1.1 触发案例：2025 金银漏赚

2026-05-23 OOT 复盘（[oot_20260523_034107_cluster_both](../report/backtest/oot_20260523_034107_cluster_both/)）：

| 维度 | 数值 |
|---|---|
| **AU0 2025 价格涨幅** | +56.8%（647.64 → 1015.38） |
| **AG0 2025 价格涨幅** | +125.8%（8264 → 18659） |
| 裸 long 50/50 配置应得 | **+91%** |
| 策略 AU+AG 2025 实际收益 | **+9.0%** |
| **漏赚倍数** | **~10×** |
| AU/AG 候选 → executed 通过率 | **14 / 110 = 12.7%** |
| 被 trade_filter 拦截 | 51% |
| 被 ranker 拦截 | 30% |

**核心矛盾**：策略在金银已经持仓盈利且趋势强劲时，**反而把后续同向信号拒之门外**。
trade_filter 拦截 51%、horizon_exit 30 天强平、setup 单 signal_type 互斥等机制，
让赢家无法继续跑。

### §1.2 设计哲学：「让赢家跑，让输家斩」

风控的核心不是"少做"，是"做对的事多做、错的事少做"：

| 阶段 | 当前 | 改进方向 |
|---|---|---|
| 入场前（候选） | 统一阈值，趋势品种和震荡品种用同一 trade_filter 阈值 | 趋势明显时**临时放低**阈值 |
| 入场后（持仓盈利） | horizon_exit 30 天强制平仓 | 持仓盈利+趋势未变 → **放宽至 90 天** |
| 入场后（持仓盈利） | 单笔 trailing stop 仅锁回撤 % | 加 **trailing take-profit**（更激进锁盈） |
| 信号生成 | 全 universe 用同一 ATR/Donchian 窗口 | 小波动品种**缩短**窗口（20→10） |
| 同向加仓 | 同 signal_type 互斥 | 持仓盈利时**允许**多 signal 并存（同向加仓） |

### §1.3 五模块速览

| 模块 | 核心机制 | 影响阶段 | 默认 off |
|---|---|---|---|
| **A** | trailing take-profit（追踪止盈） | 持仓中 | ✅ |
| **B** | trend-aware trade_filter 阈值松绑 | 入场前 | ✅ |
| **C** | profit-aware horizon_exit 放宽 | 持仓中 | ✅ |
| **D** | low-vol symbol ATR/Donchian 窗口收紧 | 信号生成 | ✅ |
| **E** | winning-position setup 多样性放开 | 入场前 | ✅ |

五模块**完全独立**，可单独 opt-in；共享同一份"盈利/趋势"状态来源
（[portfolio_logic/portfolio_state.py](../portfolio_logic/portfolio_state.py)
+ [feature/trend.py](../feature/trend.py) `ma_alignment` + [feature/regime.py](../feature/regime.py)）。

---

## §2 设计原则

### §2.1 OOT / sim / live 一致

所有模块共用同一份 cfg；OOT 评估、仿真、实盘三层都按同一规则。
新增的"盈利状态"判定必须基于**实时持仓 PnL**（不是预测），避免 lookahead。

### §2.2 默认 off

所有 5 个模块默认 `use_*=False` + `*_enabled_by_cluster_interval={}`。
未显式 opt-in 时，OOT 行为与本文档不存在完全一致。

### §2.3 状态对象共享

定义统一的 `PositionTrendState` 用于驱动模块 A/C/E：

```python
@dataclass(frozen=True)
class PositionTrendState:
    """单条持仓的盈利与趋势状态快照（每 bar 更新）。"""
    symbol: str
    side: str  # "long" or "short"
    entry_price: float
    current_price: float
    current_pnl_pct: float           # = (current - entry)/entry * sign(side)
    bars_held: int
    ma_alignment: int                # -2 ~ +2 from feature/trend.py
    regime_label: str                # from feature/regime.py
    realized_vol_20d: float
    trend_score: float               # composite score [-1, +1]
```

`trend_score` 复合定义（§3 用）：

```
trend_score = 0.5 * sign(ma_alignment) * min(|ma_alignment|/2, 1)
            + 0.3 * regime_score(regime_label)
            + 0.2 * sign(current_pnl_pct) * min(|current_pnl_pct|/0.05, 1)
```

`regime_score`：`trend_up=+1, trend_down=-1, range=0, compression=0, expansion=±0.5`。

### §2.4 漏赚 vs 抢跑权衡

| 模块 | 漏赚风险 | 抢跑风险 |
|---|---|---|
| A trailing TP | 锁盈过早 → 大行情吃不到 | 锁盈过晚 → 回吐 |
| B 阈值放宽 | 拒绝太多 → 错过趋势 | 阈值过低 → 噪音入场 |
| C horizon 放宽 | 强平太快 → 错过末段 | 持仓过久 → 趋势反转 |
| D 窗口缩短 | 窗口过长 → 信号稀疏 | 窗口过短 → 假突破 |
| E setup 多样性 | 互斥太严 → 错过加仓 | 多并发 → 单品种集中风险 |

**核心安全网**：每个模块都引入"反转触发器"——一旦趋势反转，立刻收紧到默认行为。

---

## §3 模块 A：盈利仓位 trailing take-profit（追踪止盈）

### §3.1 问题

现有 [trailing_exit.py](../portfolio_logic/trailing_exit.py) 实现的是 trailing **stop**
（锁回撤防爆仓），不是 trailing **take-profit**（主动锁盈）。

trailing stop 的语义：浮盈达 X% 后，回撤 Y% 触发离场。Y 通常较宽（如 30%）。
trailing TP 的语义：浮盈达 X% 后，**新高 ± Z%** 即离场。Z 较窄（如 5-10%）。

当 AG0 浮盈 80% 时，trailing stop 允许它回撤 30% 才离场（=锁 50% 盈利）；
trailing TP 允许只回撤 8%（=锁 72% 盈利）。**对大盈利仓位，trailing TP 更激进**。

### §3.2 数学定义

```python
@dataclass(frozen=True)
class TrailingTakeProfitConfig:
    use_trailing_take_profit: bool = False
    activation_pnl_pct: float = 0.10        # 浮盈 ≥10% 启动 trailing TP
    trail_distance_pct_initial: float = 0.08  # 距 highwater 8% 触发
    trail_distance_pct_after_huge_gain: float = 0.04  # 浮盈 ≥50% 后改 4%
    huge_gain_threshold: float = 0.50
    # 分级收紧
    tiers: tuple[tuple[float, float], ...] = (
        (0.10, 0.08),  # 浮盈 10-20% → 距 8%
        (0.20, 0.06),  # 浮盈 20-50% → 距 6%
        (0.50, 0.04),  # 浮盈 ≥50% → 距 4%
    )
    require_trend_confirmed: bool = True    # 仅在 trend_score>0 时启用 trailing TP
    enabled_by_cluster_interval: dict[str, bool] = field(default_factory=dict)
```

### §3.3 触发逻辑

```python
def evaluate_trailing_take_profit(
    state: PositionTrendState,
    highwater_price: float,
    cfg: TrailingTakeProfitConfig,
) -> ExitDecision | None:
    if not cfg.is_enabled(cluster_of(state.symbol), interval):
        return None
    if state.current_pnl_pct < cfg.activation_pnl_pct:
        return None  # 还没达到激活阈值
    # 决定 trail distance：根据当前浮盈所在 tier
    trail_pct = cfg.trail_distance_pct_initial
    for threshold, tier_trail in sorted(cfg.tiers):
        if state.current_pnl_pct >= threshold:
            trail_pct = tier_trail
    # 趋势反转保护
    if cfg.require_trend_confirmed and state.trend_score < 0:
        # 趋势已反转 → 不放宽 trailing TP，直接按默认 stop
        return None
    # 距离 highwater 触发
    if state.side == "long":
        trigger_price = highwater_price * (1.0 - trail_pct)
        if state.current_price <= trigger_price:
            return ExitDecision("trailing_take_profit", price=state.current_price)
    elif state.side == "short":
        trigger_price = highwater_price * (1.0 + trail_pct)
        if state.current_price >= trigger_price:
            return ExitDecision("trailing_take_profit", price=state.current_price)
    return None
```

### §3.4 与 trailing_exit / hard_stop / horizon_exit 的串联

```
每 bar 持仓评估：
  1. hard_stop（防爆仓）
  2. trailing_take_profit（本模块；锁盈）
  3. trailing_stop（现有；防回撤）
  4. horizon_exit（时间）
  5. mfe_mae_realtime_exit
```

trailing TP 在 trailing stop **之前**评估：若 trailing TP 触发即离场，跳过 trailing stop。

### §3.5 emit `exit_reason`

新增 `exit_reason = "trailing_take_profit"`，加入 canonical exit_reason 集合。

---

## §4 模块 B：趋势品种 trade_filter 阈值松绑

### §4.1 问题

当前 [oot_trade_filter_gate.py](../model/oot/oot_trade_filter_gate.py) 对所有候选用统一
`trade_filter_percentile_threshold`（默认 70）。在金银 2025 大牛市这种**强趋势** regime 下，
模型 trade_filter_prob 可能因训练样本里历史涨幅多次回吐而保守，导致连续拒绝有效信号。

**复盘证据**：AU/AG 2025 候选 110 个，56 个被 `blocked_trade_filter` 拦截（51%）。
其中相当部分被拦时 AU/AG 价格仍在快速攀升 → 这些被拦的样本如果放行，可能贡献几十万 net_pnl。

### §4.2 设计

引入 **trend-aware threshold delta**：当一个 `(cluster, interval)` 同时满足
"`ma_alignment` 多头排列" + "regime=trend_up" + "近 20 日实现波动率分位 > 中位"
三个条件时，临时把该 cluster 的 trade_filter 阈值下调 `trend_delta`。

```python
@dataclass(frozen=True)
class TrendAwareTradeFilterConfig:
    use_trend_aware_trade_filter: bool = False
    # 趋势条件（必须全部满足才下调阈值）
    require_ma_alignment_magnitude: int = 2        # |alignment| >= 2
    require_regime_labels: tuple[str, ...] = ("trend_up", "trend_down", "expansion")
    require_vol_rank_above: float = 0.50           # 实现波动率分位 ≥ 0.5
    # 趋势确认后的阈值偏移
    trend_threshold_delta_pctl: float = -10.0      # 百分位阈值下调 10 pp
    trend_threshold_delta_raw: float = -0.05       # raw 阈值下调 0.05
    max_consecutive_relaxed_bars: int = 60         # 最多连续放宽 60 bar 后强制回归
    enabled_by_cluster_interval: dict[str, bool] = field(default_factory=dict)
```

### §4.3 与 H4 已修的 `trade_filter_bypass_signal_types` 区别

| 维度 | bypass | trend-aware delta |
|---|---|---|
| 粒度 | signal_type | (cluster, interval) + 趋势状态 |
| 强度 | 完全跳过 | 临时下调阈值 |
| 触发 | signal_type ∈ list | 趋势条件全部满足 |
| 安全网 | 无 | 60 bar 后强制回归 + regime 反转立即关闭 |

bypass 是"完全相信新信号自带筛选"；trend-aware delta 是"趋势强时模型可以更激进"，
两者正交可同时启用。

### §4.4 接入点

```python
# cta/model/oot/oot_trade_filter_gate.py
def apply_trade_filter_gate(...):
    ...
    threshold = base_threshold
    if cfg.use_trend_aware_trade_filter:
        delta = _compute_trend_aware_delta(row, cfg)
        threshold = max(0.0, base_threshold + delta)
    ...
```

`_compute_trend_aware_delta` 检查 `ma_alignment` / `regime_label` / `realized_vol_rank`
三个列（candidate.csv 中已存在或可补）。

### §4.5 安全网

1. **`max_consecutive_relaxed_bars`**：同一 `(cluster, interval)` 连续放宽 ≥60 bar 即冷却 30 bar
2. **regime 反转立即关闭**：当 regime_label 切换到反向 trend，本次 bar 立刻按默认阈值
3. **未触发的情况下完全无影响**：默认 cfg 让 delta=0 → 等价当前行为

---

## §5 模块 C：盈利仓位 horizon_exit 放宽

### §5.1 问题

[portfolio_logic/config.py](../portfolio_logic/config.py) 的
`horizon_extension`（HorizonExtensionConfig）已有"在 trend regime 下延长"机制，
但默认 off，且 max_extensions=3。

**复盘证据**：AU/AG 2025 大单都是 25-32 天 horizon_exit。AG0 12 月一笔
2025-12-01 → 2025-12-26 + $48,473 后强平；如果延长到 60 天到 1 月底，按价格趋势可能
再涨 30%（额外 +$30k+）。

### §5.2 设计

引入 `profit_aware_horizon_extension`：当 trailing TP 未触发 + 趋势未反转 + 已浮盈
≥ activation_pnl_pct 时，**自动延长** horizon。

```python
@dataclass(frozen=True)
class ProfitAwareHorizonConfig:
    use_profit_aware_horizon: bool = False
    activation_pnl_pct: float = 0.05                  # 浮盈 ≥5% 才考虑延长
    base_max_holding_bars_by_interval: dict[str, int] = field(default_factory=lambda: {
        "day": 60, "60min": 60, "30min": 60, "15min": 60, "5min": 60, "min": 60,
    })
    extended_max_holding_bars_by_interval: dict[str, int] = field(default_factory=lambda: {
        "day": 90, "60min": 90, "30min": 80, "15min": 80, "5min": 70, "min": 70,
    })
    require_trend_confirmed: bool = True               # 趋势确认才延长
    cap_total_holding_bars: int = 120                  # 总持仓硬上限（兜底）
    enabled_by_cluster_interval: dict[str, bool] = field(default_factory=dict)
```

### §5.3 触发逻辑

```python
def resolve_max_holding_bars(
    state: PositionTrendState,
    interval: str,
    cfg: ProfitAwareHorizonConfig,
) -> int:
    if not cfg.is_enabled(cluster_of(state.symbol), interval):
        return cfg.base_max_holding_bars_by_interval.get(interval, 60)
    base = cfg.base_max_holding_bars_by_interval.get(interval, 60)
    if state.current_pnl_pct < cfg.activation_pnl_pct:
        return base
    if cfg.require_trend_confirmed and state.trend_score <= 0:
        return base
    extended = cfg.extended_max_holding_bars_by_interval.get(interval, base)
    return min(extended, cfg.cap_total_holding_bars)
```

### §5.4 与现有 horizon_extension 关系

| 机制 | 触发 | 范围 | 状态 |
|---|---|---|---|
| `HorizonExtensionConfig`（已有） | trend regime + use_model_recommendation | extension_bars=20 步进 | 复杂 |
| **本模块**（新） | 浮盈 + 趋势 | base→extended 一次性切换 | 简单 |

两者可共存：先评估本模块（粗粒度），再让 HorizonExtension 做细粒度调整。
建议优先用本模块（简单且可观测）。

---

## §6 模块 D：小波动品种 ATR/Donchian 窗口收紧

### §6.1 问题

[strategy/baseline_setup_detection.py](../strategy/baseline_setup_detection.py) 的
`atr_breakout` / `donchian_breakout` 默认 lookback 20 bar。这适合中波动品种
（黑色系 / 化工），但对**低波动品种**（金银、国债、股指）信号太稀疏。

**复盘证据**：2025 整 universe 候选 3,273 笔，AU+AG 仅 110 笔（3.36%）。candle
波动差异是结构性的：AU0 日内 ATR 约 0.5-0.8%，RB0 是 1.5-2.0%，差 3 倍。
统一 20 lookback 让 AU/AG 一年只触发零星几次。

### §6.2 设计

按 cluster + interval 配置 lookback 倍数：

```python
@dataclass(frozen=True)
class AdaptiveSetupWindowConfig:
    use_adaptive_setup_window: bool = False
    base_atr_window: int = 20
    base_donchian_window: int = 20
    # cluster-specific 倍数（< 1.0 = 缩短，> 1.0 = 拉长）
    window_multiplier_by_cluster_interval: dict[str, float] = field(default_factory=lambda: {
        "precious|day": 0.5,    # 20 → 10
        "precious|60min": 0.5,
        "bond|day": 0.7,        # 20 → 14
        "index|day": 0.7,
        "black|day": 1.0,       # 不变
        "metal|day": 1.0,
        "chemical|day": 1.0,
        "agri|day": 1.0,
    })
    min_window: int = 5          # 硬下限，防过短产生噪音
    max_window: int = 60
    enabled_by_cluster_interval: dict[str, bool] = field(default_factory=dict)
```

### §6.3 触发逻辑

```python
def resolve_setup_window(
    symbol: str,
    interval: str,
    setup_type: str,   # "atr_breakout" / "donchian_breakout"
    cfg: AdaptiveSetupWindowConfig,
) -> int:
    if not cfg.use_adaptive_setup_window:
        return cfg.base_atr_window if setup_type == "atr_breakout" else cfg.base_donchian_window
    cluster = infer_symbol_cluster(symbol)
    key = f"{cluster}|{normalize_portfolio_interval(interval)}"
    multiplier = cfg.window_multiplier_by_cluster_interval.get(key, 1.0)
    base = cfg.base_atr_window if setup_type == "atr_breakout" else cfg.base_donchian_window
    window = int(round(base * multiplier))
    return max(cfg.min_window, min(cfg.max_window, window))
```

### §6.4 与现有 setup 算子的对接

[baseline_setup_detection.py](../strategy/baseline_setup_detection.py) 内部硬编码
`window=20`；需新增 `lookback_override` 参数让外部可注入。

---

## §7 模块 E：盈利仓位 setup 多样性放开

### §7.1 问题

[baseline_candidate_gen.py](../strategy/baseline_candidate_gen.py) 当前一个 bar
对同一 (symbol, side) 通常**只允许一个 setup_type 进入候选**（priority dedup）。
这是避免一个时点重复加仓的合理保护。

但对**已持仓且盈利 + 趋势延续**的品种，禁止同方向的不同 setup_type 加仓
反而错失加仓机会。AG0 2025-12 +48k 大单期间，模型应该看到了 multiple breakout / pullback
信号，但只取了最强那个。

### §7.2 设计

引入 `winning_position_setup_diversity` 开关：当一个品种当前**已有盈利持仓** + 趋势延续时，
允许同 bar 同向多个 setup_type 候选并存。

```python
@dataclass(frozen=True)
class WinningPositionSetupDiversityConfig:
    use_winning_position_setup_diversity: bool = False
    activation_pnl_pct: float = 0.05                # 浮盈 ≥5% 才放开多样性
    max_concurrent_signal_types_per_symbol: int = 3 # 同 bar 同向最多并存 3 个
    require_trend_confirmed: bool = True
    # 允许并存的 signal_type 组合（白名单，防止 mean_reversion 和 breakout 同时出）
    compatible_groups: tuple[tuple[str, ...], ...] = (
        ("atr_breakout", "donchian_breakout", "bull_pullback_continuation"),  # 趋势族
        ("breakout_pullback_continuation", "trend_acceleration_breakout"),     # 突破回撤族
    )
    enabled_by_cluster_interval: dict[str, bool] = field(default_factory=dict)
```

### §7.3 触发逻辑

```python
def filter_candidates_with_diversity(
    candidates: pd.DataFrame,         # 同 bar 的同 (symbol, side) 候选
    current_position: Position | None,
    state: PositionTrendState | None,
    cfg: WinningPositionSetupDiversityConfig,
) -> pd.DataFrame:
    if not cfg.use_winning_position_setup_diversity:
        return _apply_default_priority_dedup(candidates)
    # 没持仓 → 默认 dedup
    if current_position is None or state is None:
        return _apply_default_priority_dedup(candidates)
    # 持仓未浮盈 / 趋势反转 → 默认 dedup
    if state.current_pnl_pct < cfg.activation_pnl_pct:
        return _apply_default_priority_dedup(candidates)
    if cfg.require_trend_confirmed and state.trend_score <= 0:
        return _apply_default_priority_dedup(candidates)
    # 放开：按 compatible_groups 过滤，取至多 max_concurrent
    return _expand_compatible_signal_types(candidates, cfg)
```

### §7.4 安全网

- **`max_concurrent_signal_types_per_symbol`**: 硬上限 3 个，防止极端分散
- **`compatible_groups`**: 白名单只允许同向同性质的 signal_type 并存
- **`require_trend_confirmed`**: 趋势反转立即回到默认 dedup
- **portfolio cap 仍然生效**: 即使候选多了，总名义敞口仍受 `max_symbol_notional_pct` 限制

---

## §8 接口契约

### §8.1 共享 state 计算

```python
# cta/portfolio_logic/position_trend_state.py（新增）
def compute_position_trend_state(
    position: Position,
    bar: Bar,
    *,
    ma_alignment_lookup: Callable[[str, pd.Timestamp], int],
    regime_label_lookup: Callable[[str, pd.Timestamp], str],
    realized_vol_lookup: Callable[[str, pd.Timestamp], float],
) -> PositionTrendState: ...
```

### §8.2 模块 A 接口

```python
# cta/portfolio_logic/trailing_take_profit.py（新增）
class TrailingTakeProfitEvaluator:
    def __init__(self, cfg: TrailingTakeProfitConfig): ...
    def update(self, state: PositionTrendState, highwater_price: float) -> ExitDecision | None: ...
```

### §8.3 模块 B 接入

`apply_trade_filter_gate` 内部读取 `cfg.use_trend_aware_trade_filter` + 候选行的
`ma_alignment` / `regime_label` / `realized_vol_rank` 列。需在 candidate.csv 保留
这三列（与 [ma_cross_regime_aware_design.md](./ma_cross_regime_aware_design.md) §4 已加的列对齐）。

### §8.4 模块 C/D/E 接入

| 模块 | 接入点 |
|---|---|
| C | [portfolio_logic/trailing_exit.py](../portfolio_logic/trailing_exit.py) `simulate_trailing_exit` 内部读 `cfg.profit_aware_horizon` |
| D | [strategy/baseline_setup_detection.py](../strategy/baseline_setup_detection.py) 各 setup 算子读 `cfg.adaptive_setup_window` |
| E | [strategy/baseline_candidate_gen.py](../strategy/baseline_candidate_gen.py) 的 candidate dedup 阶段读 `cfg.winning_position_setup_diversity` |

### §8.5 emit 新列

为支持复盘，trade 表 / candidate 表新增列：

- `trailing_tp_active`（bool）：A 模块是否在跟踪
- `trailing_tp_highwater`（float）：A 模块 highwater 价
- `trend_aware_threshold_delta`（float）：B 模块本次实际 delta
- `horizon_extended_to`（int）：C 模块延长后的 max_holding_bars
- `adaptive_window_used`（int）：D 模块实际 window
- `diversity_signal_types_count`（int）：E 模块同 bar 同向 signal_type 数

---

## §9 配置示例（建议初始灰度）

### §9.1 第一周（最保守，仅模块 A 单独灰度）

```python
PortfolioLogicConfig(
    ...,
    trailing_take_profit=TrailingTakeProfitConfig(
        use_trailing_take_profit=True,
        activation_pnl_pct=0.10,
        enabled_by_cluster_interval={"precious|day": True, "index|day": True},
    ),
    # 其他模块仍 off
)
```

**为何先开 A**：trailing TP 只影响持仓退场，不增加候选，风险最低。

### §9.2 第二周（开启 C，让赢家跑得更久）

```python
profit_aware_horizon=ProfitAwareHorizonConfig(
    use_profit_aware_horizon=True,
    activation_pnl_pct=0.05,
    enabled_by_cluster_interval={"precious|day": True, "index|day": True},
)
```

### §9.3 第三周（开启 D，让小波动品种信号更密）

```python
adaptive_setup_window=AdaptiveSetupWindowConfig(
    use_adaptive_setup_window=True,
    enabled_by_cluster_interval={"precious|day": True, "bond|day": True},
)
```

### §9.4 第四周（开启 B，让趋势品种更易入场）

```python
trend_aware_trade_filter=TrendAwareTradeFilterConfig(
    use_trend_aware_trade_filter=True,
    trend_threshold_delta_pctl=-10.0,
    enabled_by_cluster_interval={"precious|day": True, "index|day": True},
)
```

### §9.5 第五周（开启 E，最激进的"加仓"模块）

```python
winning_position_setup_diversity=WinningPositionSetupDiversityConfig(
    use_winning_position_setup_diversity=True,
    activation_pnl_pct=0.05,
    enabled_by_cluster_interval={"precious|day": True},
)
```

---

## §10 测试清单

### §10.1 单元测试（按模块）

**模块 A**：
- `test_default_disabled`
- `test_activates_when_pnl_above_threshold`
- `test_no_trigger_below_activation`
- `test_tier_based_trail_distance_tightens_with_huge_gain`
- `test_trend_reversal_disables_trailing_tp`
- `test_short_side_symmetric`
- `test_exit_reason_trailing_take_profit`

**模块 B**：
- `test_default_no_delta_applied`
- `test_trend_confirmed_lowers_threshold`
- `test_max_consecutive_relaxed_bars_cooldown`
- `test_regime_flip_immediately_closes_relaxation`
- `test_required_columns_missing_falls_back_to_default`

**模块 C**：
- `test_default_disabled_returns_base_holding`
- `test_extended_when_profit_and_trend_confirmed`
- `test_no_extension_when_trend_reversed`
- `test_cap_total_holding_bars_enforced`
- `test_per_interval_extension_table`

**模块 D**：
- `test_default_window_returned_when_disabled`
- `test_precious_day_window_halved_when_enabled`
- `test_min_window_floor_enforced`
- `test_unknown_cluster_uses_multiplier_1`

**模块 E**：
- `test_default_priority_dedup_preserved`
- `test_unwined_position_no_diversity`
- `test_winning_position_emits_multi_signal_types`
- `test_compatible_groups_filter_incompatible_types`
- `test_max_concurrent_cap`
- `test_trend_reversal_returns_to_dedup`

### §10.2 端到端 A/B（`/tmp` 输出）

```bash
# v0: baseline (全 off)
python3 -m cta.model.model_pipeline ... --output-dir /tmp/profit_v0

# v1: 仅 A (trailing TP)
python3 -m cta.model.model_pipeline ... \
  --enable-trailing-take-profit \
  --trailing-tp-enabled-cells precious|day \
  --output-dir /tmp/profit_v1

# v2: A + C
... --enable-trailing-take-profit --trailing-tp-enabled-cells precious|day \
    --enable-profit-aware-horizon --profit-aware-horizon-enabled-cells precious|day ...

# v3: A + B + C + D + E（全开）
... --enable-trailing-take-profit --trailing-tp-enabled-cells precious|day \
    --enable-profit-aware-horizon --profit-aware-horizon-enabled-cells precious|day \
    --enable-trend-aware-trade-filter --trend-aware-trade-filter-enabled-cells precious|day ...
```

**验收指标**（基于 [oot_20260523_034107](../report/backtest/oot_20260523_034107_cluster_both/) 复盘）：

| 指标 | baseline | 目标 |
|---|---|---|
| AU+AG 2025 net_pnl | $89,927 (+9.0%) | **≥ $200,000 (+20%)** |
| AU+AG 2025 executed / candidates | 14 / 110 (12.7%) | ≥ 30 / 110 (27%) |
| AG0 2025-12 单笔最大 net_pnl | $48,473 | ≥ $80,000（trailing TP + horizon 放宽） |
| 整 OOT 总收益 | +43.85% | **≥ +55%** |
| max_drawdown | -4.59% | ≤ -6.0%（允许小恶化） |
| monthly_sharpe | 1.69 | ≥ 1.5 |

---

## §11 灰度策略

### §11.1 滚动启用日历

| 周次 | 启用模块 | cluster\|interval | 退出条件 |
|---|---|---|---|
| W1 | A 单独 | precious\|day, index\|day | trailing TP 触发率 > 50% 或 monthly_sharpe 恶化 ≥ 0.3 → 回退 |
| W2 | A + C | 同上 | horizon_extension 触发率 > 80% 或 hold 平均拉长 > 50% 但 net_pnl 恶化 → 回退 C |
| W3 | A + C + D | 同上 + bond\|day | setup 候选量增长 > 200% 但 win_rate 恶化 > 5pp → 回退 D |
| W4 | A + B + C + D | 同上 | trade_filter 通过率 > 70%（vs 基线 30%）但 net_pnl 不显著改善 → 回退 B |
| W5 | A + B + C + D + E | precious\|day only | 单 symbol concurrent positions > 3 持续 1 周 → 回退 E |
| W6 | 扩展到 metal\|day, black\|day | — | 同上 |

### §11.2 全局退出条件

任一 (cluster, interval, module) 出现以下任一情况立即回退：
- 月度 net_pnl 恶化 > 15%（vs 前 60 日基准）
- max_drawdown 恶化 > 20%
- 模块特有的可观测指标异常分布（见 §8.5）

---

## §12 已知限制

### §12.1 模块 A（trailing TP）

- 大行情中可能锁盈过早。`huge_gain_threshold=0.50` 后 trail 收紧到 4%，
  对短期暴涨（如 AG0 单周 +15%）可能在第二周就触发离场，错过后续。
- 解决方向：考虑 ATR-based trail（trail = 2*ATR）而非固定百分比。

### §12.2 模块 B（trade_filter 松绑）

- 趋势识别依赖 ma_alignment + regime_label，这两个列本身有滞后性（10-20 bar）。
  趋势识别滞后 → 在 trend 末期才放宽阈值，但此时已经接近反转 → 噪音入场风险。
- 解决方向：把 ma_alignment 改成短窗口（5/10/20 而非 10/20/50）。

### §12.3 模块 C（horizon 放宽）

- 90 天 max_holding 太长可能跨越 regime 切换。`cap_total_holding_bars=120` 是硬底线。
- 解决方向：在持仓 ≥ 60 天后每 5 天重新评估 trend_score；trend_score 转负即不再延长。

### §12.4 模块 D（窗口缩短）

- 窗口太短（< 10）会导致**真假突破频繁切换**，commission 吃利润。
  `min_window=5` 是硬下限。
- 解决方向：把 window 改为浮动参考（如 `window = max(5, int(20 / vol_ratio))`），
  让窗口随实时波动自适应。

### §12.5 模块 E（setup 多样性）

- 同 bar 同向多 setup_type 并存会**放大单品种集中风险**。`max_concurrent=3` +
  `max_symbol_notional_pct` 是兜底。
- 解决方向：要求多 setup_type 之间的 IC 相关性 < 0.5，否则视为重复信号。

### §12.6 跨模块

- 5 个模块同时启用时，PnL 归因变复杂：trailing TP 触发离场后 horizon 不再适用，
  diversity 加仓的 trade 再次进入 trailing TP 监控等。需 §8.5 完整列输出做精确归因。
- 训练样本变化（D 改窗口）会改变 candidate 分布 → 需要重新训练下游模型。

---

## §13 实施 TODO（codex / claude 执行清单）

按 P0 → P3 顺序，每步先写测试再写实现（TDD）。

### P0 — 共享 state 与模块 A（最高价值）

| 任务 | 文件 | 测试 |
|---|---|---|
| 编写 `PositionTrendState` dataclass + `trend_score` 计算 | `cta/portfolio_logic/position_trend_state.py` | `cta/portfolio_logic/tests/test_position_trend_state.py` |
| 编写 `TrailingTakeProfitConfig` + `__post_init__` 校验 | `cta/config/trailing_take_profit_config.py` | `cta/config/tests/test_trailing_take_profit_config.py` |
| 编写 `TrailingTakeProfitEvaluator` | `cta/portfolio_logic/trailing_take_profit.py` | `cta/portfolio_logic/tests/test_trailing_take_profit.py` |
| 接入 [trailing_exit.py](../portfolio_logic/trailing_exit.py) 串行调用顺序 | `cta/portfolio_logic/trailing_exit.py` | 既有测试 + 1 个新增 case |
| `exit_reason="trailing_take_profit"` 加入 canonical | `cta/portfolio_logic/exit_reasons.py`（如有） | `cta/portfolio_logic/tests/test_exit_reasons_canonical.py` |

### P1 — 模块 C（让赢家跑得更久）

| 任务 | 文件 | 测试 |
|---|---|---|
| 编写 `ProfitAwareHorizonConfig` + 校验 | `cta/config/profit_aware_horizon_config.py` | `cta/config/tests/test_profit_aware_horizon_config.py` |
| 实现 `resolve_max_holding_bars` | `cta/portfolio_logic/profit_aware_horizon.py` | `cta/portfolio_logic/tests/test_profit_aware_horizon.py` |
| 接入 [trailing_exit.py](../portfolio_logic/trailing_exit.py) 的 horizon_exit 逻辑 | 同上 | 既有 horizon_exit 测试加 case |

### P2 — 模块 D（小波动品种信号密化）

| 任务 | 文件 | 测试 |
|---|---|---|
| 编写 `AdaptiveSetupWindowConfig` | `cta/config/adaptive_setup_window_config.py` | `cta/config/tests/test_adaptive_setup_window_config.py` |
| 实现 `resolve_setup_window` helper | `cta/feature/adaptive_setup_window.py` | `cta/feature/tests/test_adaptive_setup_window.py` |
| 在 [baseline_setup_detection.py](../strategy/baseline_setup_detection.py) 接入 lookback_override | 同上 | 既有 setup 测试加 case |

### P3 — 模块 B + 模块 E（最激进）

| 任务 | 文件 | 测试 |
|---|---|---|
| 编写 `TrendAwareTradeFilterConfig` | `cta/config/trend_aware_trade_filter_config.py` | `cta/config/tests/test_trend_aware_trade_filter_config.py` |
| 在 [oot_trade_filter_gate.py](../model/oot/oot_trade_filter_gate.py) 加 delta | 同上 | `cta/model/tests/test_bull_mode_trade_filter_gate.py` 加 case |
| 编写 `WinningPositionSetupDiversityConfig` | `cta/config/winning_position_setup_diversity_config.py` | `cta/config/tests/test_winning_position_setup_diversity_config.py` |
| 在 [baseline_candidate_gen.py](../strategy/baseline_candidate_gen.py) 接入 dedup 分支 | 同上 | `cta/strategy/tests/test_baseline_candidate_gen.py` 加 case |

### P3 — 共同的 column emit

| 任务 | 文件 |
|---|---|
| 在 trade 表 / candidate 表新增 §8.5 列 | `cta/model/oot/pipeline_oot_evaluation.py` |
| 在 [block_reason.md](./block_reason.md) / canonical exit_reason 加 `trailing_take_profit` | `cta/docs/block_reason.md` + `cta/portfolio_logic/exit_reasons.py`（如有） |

### 验证命令（codex / claude 完成后执行）

```bash
# 1) 全部单测
python3 -m pytest \
  cta/portfolio_logic/tests/test_position_trend_state.py \
  cta/portfolio_logic/tests/test_trailing_take_profit.py \
  cta/portfolio_logic/tests/test_profit_aware_horizon.py \
  cta/feature/tests/test_adaptive_setup_window.py \
  cta/config/tests/test_trailing_take_profit_config.py \
  cta/config/tests/test_profit_aware_horizon_config.py \
  cta/config/tests/test_adaptive_setup_window_config.py \
  cta/config/tests/test_trend_aware_trade_filter_config.py \
  cta/config/tests/test_winning_position_setup_diversity_config.py -q

# 2) 既有 pipeline 不退化
python3 -m pytest cta/model/tests/test_pipeline_oot_evaluation.py -q

# 3) canonical exit_reason
python3 -m pytest -k canonical -q

# 4) 端到端 smoke（仅 precious|day + 模块 A）
python3 -m cta.model.model_pipeline \
  --group-pool --group-by cluster --interval day \
  --only-clusters precious \
  --start 2024-01-01 --end 2025-12-31 \
  --enable-trailing-take-profit \
  --trailing-tp-enabled-cells precious|day \
  --output-dir /tmp/profit_aware_smoke
```

---

## §14 不变量（执行时不可破坏）

- **默认 off**：所有 5 个模块默认 `use_*=False`，OOT 行为与本文档不存在完全一致。
- **不动训练样本分布**：模块 D 改 setup 窗口会影响 candidate 分布；启用 D 时**必须**
  完整重训下游模型，不允许新旧数据混用。
- **不动 portfolio cap**：5 个模块复用现有 cluster_cap / symbol_cap / leverage，
  不允许通过本文档绕开。
- **OOT / sim / live 一致**：三层共用同一 cfg；sim/live 入口未 wire 时不允许启用。
- **可观测**：§8.5 列必须随每次启用模块时同步 emit，便于事后归因。
- **TDD**：所有新文件先写测试。
- **文档先行**：本文是 spec；实施过程中接口变更需先更新 §0 修订记录。

---

## §15 风险与回退

| # | 风险 | 应对 |
|---|---|---|
| R1 | trailing TP 锁盈过早 → 错过暴涨 | tier-based 分级 + huge_gain_threshold 后才收紧；可灰度回退到当前 trailing_stop |
| R2 | 阈值松绑后噪音入场，连续亏损 | max_consecutive_relaxed_bars + regime_flip 立即关闭 + 灰度 |
| R3 | horizon 90 天跨越 regime 切换 | cap_total_holding_bars=120 硬上限 + 趋势监控 |
| R4 | 窗口缩短产生假突破链 | min_window=5 + 模型重训 + 灰度 |
| R5 | setup 多样性放大单品种集中风险 | max_concurrent=3 + portfolio cap 仍生效 |
| R6 | 5 模块叠加效应不可预测 | §11 滚动启用日历 + 单一模块退出条件 + 完整 §8.5 列归因 |
| R7 | "盈利状态" 的 NaN / 缺值 | PositionTrendState.is_valid() 校验；invalid 时所有模块 fallback 到默认行为 |

---

## §16 参考实现链路

- 现有 trailing_stop（防回撤，不是锁盈）：[portfolio_logic/trailing_exit.py](../portfolio_logic/trailing_exit.py)
- 现有 horizon_extension（已有，但复杂且默认 off）：[portfolio_logic/config.py](../portfolio_logic/config.py) `HorizonExtensionConfig`
- 现有 trade_filter gate：[model/oot/oot_trade_filter_gate.py](../model/oot/oot_trade_filter_gate.py)
- 现有 setup detection：[strategy/baseline_setup_detection.py](../strategy/baseline_setup_detection.py)
- 现有 candidate dedup：[strategy/baseline_candidate_gen.py](../strategy/baseline_candidate_gen.py)
- 现有 cluster 配置：[config/symbol_cluster_config.py](../config/symbol_cluster_config.py)
- 现有 regime label：[feature/regime.py](../feature/regime.py) `compute_regime_features`
- 现有 ma_alignment：[feature/trend.py](../feature/trend.py:152) `ma_alignment`
- 同款"按 (cluster, interval) 灰度"模式：[ma_cross_regime_aware_design.md](./ma_cross_regime_aware_design.md)、
  [cross_sectional_momentum_rotation_design.md](./cross_sectional_momentum_rotation_design.md)
- 触发本文档的复盘：[oot_20260523_034107_cluster_both](../report/backtest/oot_20260523_034107_cluster_both/)
- block_reason / exit_reason canonical：[block_reason.md](./block_reason.md)

---

## §17 v2 后续扩展

1. **ATR-based trailing TP**：trail 距离用 `2*ATR / current_price` 而非固定百分比。
2. **dynamic trend_score weight**：根据 regime 长度自适应 ma_alignment / regime / pnl 的权重。
3. **horizon adaptive scaling**：90 天再延 30 天为 120 天的硬上限改为软上限（趋势 score 仍 > 0.8 时允许）。
4. **cluster-specific min_window**：min_window 也按 cluster 配置（precious 可降到 3）。
5. **diversity 跨 side 放开**：允许 long 持仓 + short hedge 候选同时存在（market neutral micro-structure）。
6. **机器学习驱动**：把"是否放宽"作为二分类目标，训一个 meta-model 决定 5 模块的 on/off。
