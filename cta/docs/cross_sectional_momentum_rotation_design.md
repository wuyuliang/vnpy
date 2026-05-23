# 截面动量轮动设计文档（cross_sectional_momentum_rotation）

> 范围：`cta/strategy/`、`cta/feature/`、`cta/portfolio_logic/`、`cta/config/`、
> `cta/model/`、`cta/run/`
> 风格：与 [ma_cross_regime_aware_design.md](./ma_cross_regime_aware_design.md)、
> [mean_reversion_voi_regime_adaptive_design.md](./mean_reversion_voi_regime_adaptive_design.md)
> 对齐。snake_case，默认 off，按 `(cluster, interval)` 灰度启用。
> **本文档面向 codex / claude 实施**，§12 给出可直接执行的 TDD TODO 清单。

---

## §0 修订记录

| 日期 | 作者 | 变更 |
|---|---|---|
| 2026-05-22 | codex | phase-1 实现 P0/P1 核心 + P2 最小接入：rotation config、排名 helper、candidate 生成、trade-filter bypass、portfolio intent executor；训练/OOT 主流水线合流仍待下一阶段。 |
| 2026-05-22 | claude | 初稿。覆盖截面动量轮动策略的完整设计（candidate 生成 → 仓位分配 → 风控）。 |

---

## §1 概览

### §1.1 策略假设

中国商品期货市场存在**截面动量效应**：过去 N 日累计收益排名靠前的品种，
未来 K 日内继续跑赢；排名靠后的品种继续跑输。这是**横截面**（cross-sectional）
现象，与**时序**（time-series）动量正交：

- 时序动量：每个品种独立判断「自身上涨/下跌」（已存在的 `momentum_setup`）。
- 截面动量：在**所有候选品种之间**做排名，long 头部、short 尾部。

### §1.2 **最佳应用周期：day（每日重估，周度调仓）**

| interval | 适用度 | 理由 |
|---|---|---|
| **day** | ★★★★★ | 学术上 12-1 month 截面动量在月度周期最稳定；商品市场缩短为 60-day 排名 + 5-day rebalance |
| 60min | ★★☆☆☆ | 噪音大，调仓频率高 → 手续费/滑点吞掉 alpha |
| 30min | ★☆☆☆☆ | 不建议 |
| 15min / 5min / min | ☆☆☆☆☆ | 截面排名失效，纯噪声 |

**推荐基线配置**：day-level，60-day 累计收益排名，5-day（约 1 周）调仓一次，
top 5 long / bottom 5 short，等权重。

### §1.3 适用品种与 universe

候选池：**所有主力连续合约**（约 50 个，参见 [symbol_cluster_config.py](../config/symbol_cluster_config.py)）。

**选择性 cluster-neutral**（推荐开启）：在每个 cluster 内做排名，避免一个 cluster
（例如黑色系）整体上涨拉满 long 端，导致 cluster 集中风险。

cluster 内最少候选数：3 个品种（否则跳过该 cluster），见 [§5.3](#§5.3-cluster-neutral-与-cluster-cap)。

### §1.4 与现有策略的关系

| 已有/新增 | 名称 | 维度 | 关系 |
|---|---|---|---|
| 已有 | `momentum_setup` | 时序 | 单品种独立判断 |
| 已有 | `breakout_setup` / `pullback_setup` | 时序 | 单品种独立判断 |
| 已有 | `regime_gate` / `ma_cross_gate` | 时序 | OOT 入场过滤 |
| 已有 | `OpportunityRanker` | 候选打分 | 候选筛选（在 portfolio 层），打分基于多指标融合，**不是**截面排名 |
| **新增** | `cross_sectional_momentum_rotation` | **横截面** | 与 OpportunityRanker 互补：先选 universe，再排名 long-short |

### §1.5 设计目标

1. **正交 alpha**：与现有时序动量、breakout、mean reversion 三类信号低相关。
2. **风险中性**：通过 long-short 对冲市场 beta（全商品指数）。
3. **解释性**：rank 输出可观测，可在 OOT 复盘逐品种检查"为何被选中/淘汰"。
4. **可灰度**：默认 off，按 `(cluster, interval)` 启用，与 OOT 评估同源。
5. **可与 portfolio_logic 串接**：rotation 输出的 long-short 信号必须经过现有
   trade_filter / regime_gate / position_sizing / cluster_cap 等下游 gate。

---

## §2 信号定义（数学）

### §2.1 截面动量得分

对每个候选品种 `s`，每个交易日 `t`，计算：

```
momentum_score(s, t) = (close(s, t) / close(s, t - lookback_days)) - 1
```

`lookback_days` 默认 60（约 1 季度交易日）。可选 `skip_recent_days=5` 跳过最近
5 日（学术上的"短反转"对冲），即：

```
momentum_score(s, t) = (close(s, t - 5) / close(s, t - lookback_days)) - 1
```

### §2.2 截面排名

在每个 rebalance 日 `t_r`，对当前 universe `U(t_r)` 内所有品种按
`momentum_score(s, t_r)` 排名：

```
rank(s, t_r) = rank_descending(momentum_score, in U(t_r))
total = |U(t_r)|
percentile(s, t_r) = (total - rank(s, t_r) + 1) / total ∈ (0, 1]
```

### §2.3 选股规则

- **long 候选**：`percentile >= top_quantile`（默认 0.80，即 top 20%）
- **short 候选**：`percentile <= bottom_quantile`（默认 0.20，即 bottom 20%）
- **中间区间**：不交易

可选配置 `long_only_mode=True`：只交易 long 端，short 端跳过（适合不允许做空
的账户或股指/国债单边策略验证）。

### §2.4 调仓频率（rebalance cadence）

```
rebalance_days(t) = (t.weekday() == rebalance_weekday) OR (days_since_last >= max_holding_days)
```

`rebalance_weekday` 默认周一（0），`max_holding_days` 默认 5（强制周度重平衡）。

### §2.5 仓位权重

```
weight(s, t_r) = sign(percentile - 0.5) * base_weight / |selected|
```

其中 `sign = +1` for long, `-1` for short。`base_weight` 是策略总名义敞口
（默认 0.5，即净 long + short 名义合计为权益的 50%）。

**vol-targeted 权重**（可选）：

```
weight(s, t_r) *= target_vol_pct / realized_vol_pct(s)
```

将每个品种的仓位按其 60-day 实际波动率反比缩放，使每个品种贡献相近的风险。

---

## §3 universe / 候选池

### §3.1 默认 universe

```python
DEFAULT_UNIVERSE_CLUSTERS = ("black", "metal", "chemical", "agri", "precious", "index", "bond")
# 排除 "other"（小品种、流动性差）
```

每个 cluster 内的品种从 [symbol_cluster_config.py](../config/symbol_cluster_config.py)
的 `SYMBOL_CLUSTER_BY_PREFIX` 取，并按以下条件过滤：

| 过滤条件 | 默认值 | 说明 |
|---|---|---|
| `min_avg_daily_volume` | 50_000 手 | 流动性下限（60 日均成交量） |
| `min_avg_daily_open_interest` | 100_000 手 | 持仓量下限 |
| `min_history_days` | 252 | 上市/数据可用日数 |
| `exclude_disabled` | True | 用 [symbol_disable.py](../config/symbol_disable.py) 黑名单过滤 |
| `exclude_within_rollover_window_days` | 3 | 主力合约切换前后 3 天剔除 |

### §3.2 主力合约切换处理

截面动量轮动**对主力合约切换非常敏感**：rollover 当天会有人为价格跳跃，影响
`momentum_score`。规则：

1. 用**连续合约**（已 `RB0` / `AU0` 等 0 字尾），由 [data_code/](../data_code/) 的
   ranged-future 拼接逻辑保证价格连续。
2. **在 rollover 后 3 天内**，该品种从 universe 临时剔除（避免拼接尾巴的尖刺）。
3. **rollover 检测**：用 `主力换月日历` 数据（已存在于
   [futures_downloader.py](../data_code/futures_downloader.py)），或 fallback 用
   `持仓量 max contract` 切换日。

---

## §4 entry / exit / stop 规则

### §4.1 entry

- 每个 rebalance 日 `t_r` 收盘后计算 `momentum_score`，输出 long / short 候选。
- 入场时刻：**次日开盘**（market order at next bar open）。
- 入场价：`open(t_r + 1)`，加 `slippage_ticks` 的滑点（沿用 [contract_specs](../config/) 配置）。

### §4.2 exit

三种平仓触发：

1. **rebalance exit**：下一个 rebalance 日不再入选 → 次日开盘平仓。
2. **time-based exit**：持仓达到 `max_holding_days`（默认 5）→ 强制平仓。
3. **stop-loss exit**：单笔浮亏达到 `stop_loss_pct`（默认 0.05）→ 立即平仓。

### §4.3 stop

- **hard stop**：`stop_loss_pct = 0.05`（5%）。
- **trailing stop**：可选，复用 [trailing_exit.py](../portfolio_logic/trailing_exit.py)
  的实现。默认 off（截面动量本身已经周度重平衡，无需 trailing）。
- **portfolio-level kill switch**：策略累计回撤达 `kill_switch_dd_pct`（默认 0.15）
  → 暂停截面动量轮动，等下个月 1 号自动恢复。

### §4.4 与 portfolio_logic 现有 gate 的串接

```
cross_sectional_momentum_rotation.generate_candidates
  ↓ 输出 candidate.csv（含 side, signal_type='cross_sectional_momentum'）
  ↓
trade_filter / regime_gate / ma_cross_gate / regime_short_filter（已有）
  ↓ 通过的 candidate 进入下一步
  ↓
position_sizing / risk_per_trade / cluster_cap / symbol_cap（已有）
  ↓ sizing 后写入 trade
  ↓
trailing_exit / time_stop（已有）
  ↓ 持仓退场
```

**important**：rotation 的 candidate 经过 trade_filter 时**应当 bypass**（rotation
自己已经做了排名筛选）。新增 cfg 字段 `trade_filter_bypass_signal_types = ("cross_sectional_momentum",)`
告诉 trade_filter gate 跳过 rotation 候选。

---

## §5 仓位管理 / 风控

### §5.1 总敞口（gross exposure）

- `gross_exposure_target = 0.50`（long + short 名义合计为权益的 50%）
- `max_gross_exposure = 1.00`（极端市场临时上限）

### §5.2 净敞口（net exposure）

- `net_exposure_target = 0.0`（市场中性）
- `max_net_exposure_abs = 0.20`（极端不平衡时允许偏离）

当 long 端入选品种数远多于 short 端（例如 short 候选都被 disabled symbols 过滤），
按以下规则纠偏：

1. 减少 long 端权重，让 net 名义 |L - S| ≤ `max_net_exposure_abs * equity`
2. 若仍偏离 > `max_net_exposure_abs * equity * 1.2` → 整体跳过该 rebalance

### §5.3 cluster-neutral 与 cluster cap

**cluster-neutral 模式（默认 on）**：在每个 cluster 内分别做排名，再合并。
保证 long 端在所有 cluster 都有代表，避免黑色系拉满 long。

实现方式：

```python
for cluster in DEFAULT_UNIVERSE_CLUSTERS:
    sub = universe.filter(cluster=cluster)
    if len(sub) < min_cluster_size:  # 默认 3
        continue
    long_k = max(1, int(len(sub) * top_quantile_in_cluster))  # 默认 0.4
    short_k = max(1, int(len(sub) * bottom_quantile_in_cluster))
    longs += sub.top_n_by_momentum(long_k)
    shorts += sub.bottom_n_by_momentum(short_k)
```

**cluster cap**：复用 [portfolio_logic/](../portfolio_logic/) 的
`max_cluster_notional_pct`（默认 0.50），rotation 不会突破。

### §5.4 单品种 cap

- `max_symbol_notional_pct = 0.10`（单品种最多占权益 10%）。
- 若 vol-target 计算的权重超过该 cap，按 cap 截断，余量分给该 cluster 其他品种。

### §5.5 与现有 [`PortfolioLogicConfig`](../portfolio_logic/config.py) 的串接

新增 sub-config，集成进 portfolio_logic：

```python
@dataclass(frozen=True)
class CrossSectionalRotationConfig:
    use_cross_sectional_momentum_rotation: bool = False
    lookback_days: int = 60
    skip_recent_days: int = 5
    rebalance_weekday: int = 0  # Monday
    max_holding_days: int = 5
    top_quantile: float = 0.80  # 全局 top 20%
    bottom_quantile: float = 0.20
    top_quantile_in_cluster: float = 0.40  # cluster-neutral 时 top 40%
    bottom_quantile_in_cluster: float = 0.40
    cluster_neutral: bool = True
    min_cluster_size: int = 3
    long_only_mode: bool = False
    gross_exposure_target: float = 0.50
    max_gross_exposure: float = 1.00
    net_exposure_target: float = 0.0
    max_net_exposure_abs: float = 0.20
    use_vol_target_weighting: bool = True
    target_vol_pct_per_symbol: float = 0.02  # 2% 日波动目标
    realized_vol_window_days: int = 60
    max_symbol_notional_pct: float = 0.10
    stop_loss_pct: float = 0.05
    kill_switch_dd_pct: float = 0.15
    min_avg_daily_volume: int = 50_000
    min_avg_daily_open_interest: int = 100_000
    min_history_days: int = 252
    exclude_within_rollover_window_days: int = 3
    universe_clusters: tuple[str, ...] = (
        "black", "metal", "chemical", "agri", "precious", "index", "bond"
    )
    enabled_by_cluster_interval: dict[str, bool] = field(default_factory=dict)
```

---

## §6 接口契约

### §6.1 候选生成

```python
# cta/strategy/cross_sectional_momentum_rotation.py
class CrossSectionalMomentumRotation:
    def __init__(self, cfg: CrossSectionalRotationConfig) -> None: ...

    def generate_rebalance_candidates(
        self,
        date: pd.Timestamp,
        universe_bars: dict[str, pd.DataFrame],  # symbol -> bar history
    ) -> pd.DataFrame:
        """返回当日 long / short 候选 DataFrame。

        必含列：
        - symbol, exchange, side, signal_type='cross_sectional_momentum'
        - momentum_score, momentum_rank, percentile_in_universe, percentile_in_cluster
        - target_weight, target_notional, vol_target_scale
        - entry_datetime（次日开盘 timestamp）, entry_price_hint（次日 open 估计）
        - stop_price, planned_exit_datetime（max_holding_days 后）
        """
```

### §6.2 排名 helper

```python
# cta/feature/cross_sectional_rank.py
def compute_cross_sectional_momentum(
    universe_bars: dict[str, pd.DataFrame],
    *,
    lookback_days: int = 60,
    skip_recent_days: int = 5,
    as_of: pd.Timestamp,
) -> pd.DataFrame:
    """返回 long-format DataFrame：symbol / momentum_score / cluster / valid_for_ranking"""

def rank_within_cluster(
    momentum_df: pd.DataFrame,
    *,
    cfg: CrossSectionalRotationConfig,
) -> pd.DataFrame:
    """返回带 rank / percentile / selected_side / target_weight 的 DataFrame"""
```

### §6.3 portfolio 集成

```python
# cta/portfolio_logic/cross_sectional_rotation_executor.py
class CrossSectionalRotationExecutor:
    """每日检查是否到 rebalance 日；是则计算候选并下发 long/short 订单。"""
    def step(self, t: pd.Timestamp, portfolio_state: PortfolioState) -> list[OrderIntent]: ...
```

---

## §7 与现有 baseline 的关系

### §7.1 signal_type 共存

新增 `signal_type = "cross_sectional_momentum"`，与现有 5 种并列：

```
existing: breakout / pullback / momentum / mean_reversion_range（已设计）/ tight_range_breakout
new:      cross_sectional_momentum
```

### §7.2 candidate 表 schema 增列

无需增列。复用现有 candidate schema（symbol / side / signal_type / entry_price /
stop_price / signal_datetime / regime_label / ma_alignment 等）。

新增几个 rotation 专属的 feature 列（可选，便于 OOT 复盘）：

- `momentum_score_60d`（动量得分）
- `momentum_rank_global`（全局排名）
- `momentum_percentile_cluster`（cluster 内分位）
- `vol_target_scale`（vol-target 缩放因子）

### §7.3 OOT 评估

不需要新增 block_reason；rotation 候选与其他 candidate 一起进入
[pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py) 的同一条
评估链路。

---

## §8 测试清单

### §8.1 单元测试

`cta/feature/tests/test_cross_sectional_rank.py`：

- `test_momentum_score_basic` — 简单线性数据，计算结果与手算一致
- `test_skip_recent_days_changes_score` — `skip_recent_days=5` vs `0` 输出不同
- `test_ranking_within_cluster` — 同 cluster 3 个品种，rank 1 / 2 / 3 一致
- `test_warmup_returns_nan` — 数据 < lookback 时返回 NaN，不参与排名

`cta/strategy/tests/test_cross_sectional_momentum_rotation.py`：

- `test_default_disabled` — `use_cross_sectional_momentum_rotation=False` 不出 candidate
- `test_generate_candidates_emits_long_and_short` — 启用后两端都有候选
- `test_long_only_mode_skips_short` — `long_only_mode=True` short 候选 0
- `test_cluster_neutral_distributes_across_clusters` — 输出覆盖 ≥3 cluster
- `test_min_cluster_size_skip` — cluster 内 < 3 个品种时跳过
- `test_vol_target_weighting_caps_high_vol_symbols` — 高波动品种权重降低
- `test_rebalance_only_on_target_weekday` — 非周一不出 candidate
- `test_max_holding_days_forces_exit` — 持仓 5 日后 planned_exit_datetime 触发
- `test_kill_switch_pauses_rotation` — 累计回撤 > 15% 时跳过该周期
- `test_excludes_rollover_window` — 主力切换前后 3 天不入 universe
- `test_excludes_disabled_symbols` — symbol_disable 黑名单生效
- `test_signal_type_emitted` — candidate 行 `signal_type == 'cross_sectional_momentum'`

### §8.2 端到端 A/B（`/tmp` 输出）

```bash
# baseline：全 off
python3 -m cta.run.runner --start 2018-01-01 --end 2025-12-31 \
  --output-dir /tmp/xsec_v0

# v1：开启 long-only 全局排名
python3 -m cta.run.runner --start 2018-01-01 --end 2025-12-31 \
  --override use_cross_sectional_momentum_rotation=True \
    long_only_mode=True \
    enabled_by_cluster_interval='{"index|day":true,"black|day":true,"metal|day":true}' \
  --output-dir /tmp/xsec_v1

# v2：long-short + cluster-neutral + vol-target
python3 -m cta.run.runner ... \
  --override use_cross_sectional_momentum_rotation=True \
    cluster_neutral=True use_vol_target_weighting=True \
    enabled_by_cluster_interval='{"*|day":true}' \
  --output-dir /tmp/xsec_v2
```

**验收指标**：

- v1 vs v0：sharpe 提升 > 0.2，max_drawdown 不恶化 > 5%
- v2 vs v1：sharpe 提升 > 0.1，long-short 月度收益与全商品指数相关性 < 0.3
- 任一版本：rotation 单独年化 sharpe > 0.5（按 rotation trades only 评估）

---

## §9 灰度策略

### §9.1 滚动启用日历

| 周次 | 启用范围 | 退出条件 |
|---|---|---|
| W1 | long-only / 单 cluster（先 `index|day`） | 月度回撤 > 8% → 回退 |
| W2 | long-only / 3 cluster（`index|day`, `black|day`, `metal|day`） | 同上 |
| W3 | long-short / 3 cluster | 净敞口偏离 > 30% 持续 1 周 → 回退 cluster-neutral |
| W4 | long-short / 全 cluster | 同上 |

### §9.2 全局退出条件

- rotation trade 累计 net_pnl < 0 持续 60 天 → 全部回退
- 月度 sharpe < 0 持续 3 个月 → 全部回退

---

## §10 已知限制

### §10.1 主力合约换月跳价

连续合约拼接处仍可能有 1-2% 的人为跳价。`exclude_within_rollover_window_days=3`
是缓冲，但极端情况（节假日 + 换月）可能不够。**对策**：用复权拼接而非简单拼接，
或在 §3.2 的 rollover 检测中扩大窗口到 5 日。

### §10.2 小品种流动性

`min_avg_daily_volume=50_000` 已经过滤掉大部分小品种，但部分中等品种（如 EC,
BB, FB）流动性仍不稳定。**对策**：vol-target 权重已经自适应，但建议初期手动
排除（universe_clusters 不含 "other"）。

### §10.3 跨 cluster 风险暴露

cluster-neutral 不能完全对冲宏观风险（例如全商品整体上涨/下跌）。**对策**：
若资金足够，建议同时开启**全商品指数空头对冲**（一个独立的子组合）。

### §10.4 周度调仓的"周一效应"

学术研究显示周一可能有日历效应。**对策**：将 `rebalance_weekday` 设为可配置，
A/B 测试周一 / 周三 / 周五，选 sharpe 最稳的一档。

### §10.5 截面动量的 regime 失效

历史上截面动量在以下 regime 下表现差：
- **趋势反转期**（例如 2008Q4, 2020Q1 商品全线暴跌）
- **极端高波动 regime**（vol_regime = high）

**对策**：可在 §6.3 的 Executor 内加 regime gate，例如：
```python
if vol_regime == "high" and abs(rolling_market_return) > 0.10:
    return []  # 高波动 + 单边市，跳过本次 rebalance
```

---

## §11 性能指标基线（参考）

| 指标 | 目标值（按 rotation trades only 评估） |
|---|---|
| 年化收益 | > 8% |
| 年化 sharpe | > 0.8 |
| 最大回撤 | < 12% |
| Calmar | > 0.6 |
| 月度胜率 | > 55% |
| 与全商品指数相关性 | < 0.3 |
| 平均持仓天数 | 5-10 |
| 年换手率 | 1000% - 2000% |

---

## §12 实施 TODO（codex / claude 执行清单）

按 P0 → P3 顺序，每步先写测试再写实现（TDD）。

### P0 — 排名核心

| 任务 | 文件 | 测试 |
|---|---|---|
| 编写动量得分计算 | `cta/feature/cross_sectional_rank.py` | `cta/feature/tests/test_cross_sectional_rank.py` |
| 编写 cluster 内排名 | 同上 | 同上 |
| 编写 `CrossSectionalRotationConfig` dataclass + `__post_init__` 校验 | `cta/config/cross_sectional_rotation_config.py` | `cta/config/tests/test_cross_sectional_rotation_config.py` |

### P1 — 候选生成

| 任务 | 文件 | 测试 |
|---|---|---|
| 编写 `CrossSectionalMomentumRotation.generate_rebalance_candidates` | `cta/strategy/cross_sectional_momentum_rotation.py` | `cta/strategy/tests/test_cross_sectional_momentum_rotation.py` |
| 实现 rollover 窗口过滤（复用 [data_code/](../data_code/) 切换日历） | 同上 | 同上 |
| 实现 vol-target 缩放 | 同上 | 同上 |
| 把 `signal_type='cross_sectional_momentum'` 加入 canonical signal_types（如有） | `cta/strategy/signal_types.py`（如有） / 否则补 |  |

### P2 — Portfolio 集成

| 任务 | 文件 | 测试 |
|---|---|---|
| 编写 `CrossSectionalRotationExecutor.step` | `cta/portfolio_logic/cross_sectional_rotation_executor.py` | `cta/portfolio_logic/tests/test_cross_sectional_rotation_executor.py` |
| 接入 [PortfolioLogicConfig](../portfolio_logic/config.py) 的 sub-config 字段 | `cta/portfolio_logic/config.py` |  |
| 在 [pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py) 串接 rotation candidate | 同上 | 既有 `test_pipeline_oot_evaluation.py` 补 case |
| `trade_filter_bypass_signal_types` 字段实现 | `cta/model/oot/oot_gates.py` | 补 case 到 `test_oot_gates.py`（如有） |

### P3 — 文档与运维

| 任务 | 文件 | 测试 |
|---|---|---|
| 在 [run.md](../run.md) / [run.sh](../run.sh) 新增 `step_cross_sectional_rotation` step | `cta/run.md` / `cta/run.sh` |  |
| 在 [block_reason.md](./block_reason.md) §1.1 加 rotation 特有的 reason（若有） | `cta/docs/block_reason.md` |  |
| 在 [change_log.md](../report/change_log.md) 写入变更说明 | `cta/report/change_log.md` |  |

### 验证命令（codex / claude 完成后执行）

```bash
# 1) 全部单测
python3 -m pytest \
  cta/feature/tests/test_cross_sectional_rank.py \
  cta/strategy/tests/test_cross_sectional_momentum_rotation.py \
  cta/portfolio_logic/tests/test_cross_sectional_rotation_executor.py \
  cta/config/tests/test_cross_sectional_rotation_config.py -q

# 2) 既有 pipeline 不退化
python3 -m pytest cta/model/tests/test_pipeline_oot_evaluation.py -q

# 3) docs 同步
python3 -m pytest cta/run/tests/test_docs_sync.py -q  # 如有

# 4) canonical 一致性
python3 -m pytest -k canonical -q

# 5) 端到端 A/B（仅在 /tmp，必须）
python3 -m cta.run.runner \
  --interval day --start 2020-01-01 --end 2025-12-31 \
  --override use_cross_sectional_momentum_rotation=True \
    long_only_mode=True \
    enabled_by_cluster_interval='{"index|day":true}' \
  --output-dir /tmp/xsec_smoke
```

---

## §13 不变量（执行时不可破坏）

- **默认 off**：`use_cross_sectional_momentum_rotation=False` 且
  `enabled_by_cluster_interval={}` 时，所有 candidate / trade 行为完全等同于
  rotation 不存在。
- **不动训练样本分布**：rotation 是 candidate 生成层的新增 signal_type，不修改
  其他 setup 的 candidate；下游训练样本会**多出** rotation 样本，但其他 signal
  分布不变。
- **不动 portfolio cap**：rotation 复用现有的 cluster_cap / symbol_cap /
  margin_cash 限制，不允许通过 rotation 绕开。
- **OOT / sim / live 一致**：三层共用 `CrossSectionalRotationConfig`。
- **TDD**：所有新文件先写测试。
- **文档先行**：本文是 spec；实施过程中任何接口变化需先回来更新 §0 修订记录。

---

## §14 风险与回退

| # | 风险 | 应对 |
|---|---|---|
| R1 | rotation alpha 不显著（端到端 sharpe < 0.5） | 灰度阶段就能发现；默认 off 不会污染主回测 |
| R2 | cluster-neutral 实现 bug → 集中风险 | 单测覆盖 `test_cluster_neutral_distributes_across_clusters` |
| R3 | rollover 跳价没过滤干净 → 月度收益异常 | §3.2 / §10.1；可扩大窗口到 5 天 |
| R4 | 与 OpportunityRanker 行为冲突（重复入选） | OpportunityRanker 在 portfolio 层，rotation 已经独立筛选，建议 rotation 候选**绕过** OpportunityRanker（通过 §4.4 的 bypass 机制） |
| R5 | kill_switch 触发后忘记恢复 | 设计层面**自动恢复**：下个月 1 号自动重置 dd 计数器，无需人工干预 |
| R6 | 周度调仓的成交滑点拉低净收益 | vol-target 权重 + base_weight=0.5 已经压低单笔金额；可监控 commission_pct_of_pnl |

---

## §15 参考实现链路

- 现有时序动量 setup：[strategy/baseline_setup_detection.py](../strategy/baseline_setup_detection.py)
  的 `momentum_setup` 分支
- 现有 OpportunityRanker（portfolio 层打分）：[portfolio_logic/](../portfolio_logic/)
  下的 ranker 实现
- 现有 cluster 配置：[config/symbol_cluster_config.py](../config/symbol_cluster_config.py)
- 现有 symbol disable：[config/symbol_disable.py](../config/symbol_disable.py)
- 现有连续合约数据：[data_code/futures_downloader.py](../data_code/futures_downloader.py)
- 现有 portfolio cap：[portfolio_logic/config.py](../portfolio_logic/config.py) 的
  `CapConfig` / `PortfolioLogicConfig`
- 同款"按 (cluster, interval) 灰度"模式：[ma_cross_regime_aware_design.md](./ma_cross_regime_aware_design.md)

---

## §16 v2 后续扩展

1. **多周期 ensemble**：同时跑 20-day / 60-day / 120-day 三个 lookback，加权平均
   score（lookback 越短权重越低）。
2. **截面 carry**：用持仓量变化 / 持仓量 / 价差结构作为第二维度排名，与动量加权。
3. **跨 cluster ensemble**：在 cluster-neutral 之外，再加一个全市场 vector，
   各 cluster 输出加权。
4. **regime-conditional**：在不同 vol_regime / trend_regime 下用不同 lookback
   和 quantile，参考 [voi_momentum_config.py](../config/voi_momentum_config.py)。
5. **机器学习排名**：用 LightGBM / XGBoost 把多个截面因子（动量、carry、basis）
   组合成单一 score，再做 long-short。
