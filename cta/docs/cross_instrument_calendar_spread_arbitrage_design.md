# 跨品种 / 跨期价差套利设计文档（cross_instrument_calendar_spread_arbitrage）

> 范围：`cta/strategy/`、`cta/feature/`、`cta/portfolio_logic/`、`cta/config/`、
> `cta/data_code/`、`cta/run/`
> 风格：与 [ma_cross_regime_aware_design.md](./ma_cross_regime_aware_design.md)、
> [cross_sectional_momentum_rotation_design.md](./cross_sectional_momentum_rotation_design.md)
> 对齐。snake_case，默认 off，按 `(pair_key, interval)` 灰度启用。
> **本文档面向 codex / claude 实施**，§13 给出可直接执行的 TDD TODO 清单。
>
> 本文同时覆盖两个相近的子策略：
> - **模块 A**：跨品种价差套利（cross-instrument，例如 RB-HC 螺纹热卷价差）
> - **模块 B**：跨期价差套利（calendar spread，例如 RB 当月 vs RB 三个月后）
>
> 二者数学结构相同（z-score mean-reversion），区别在于 leg 的定义；合并设计便于
> 复用 `SpreadPair` / `SpreadFeature` / `SpreadExecutor` 等基础设施。

---

## §0 修订记录

| 日期 | 作者 | 变更 |
|---|---|---|
| 2026-05-22 | claude | 初稿。覆盖跨品种价差 + 跨期价差两个套利子策略的完整设计。 |

---

## §1 概览

### §1.1 策略假设

价差（spread）= 一对相关品种 / 合约的价格差，其历史上围绕一个均值波动。
当 spread 偏离均值超过 N 倍标准差（z-score）时，做**反向 spread 仓**等待回归。

**核心**：相关品种的基本面联动使得**绝对价差**比单边方向更稳定可预测，
因此 **spread mean-reversion** 优于 spread directional trading。

两个子策略：

| 模块 | 配对类型 | 经典案例 | 驱动因素 |
|---|---|---|---|
| A. 跨品种 | 上下游 / 替代品 | RB-HC（螺纹/热卷）, M-RM（豆粕/菜粕）, J-JM（焦炭/焦煤）, CU-AL（铜/铝） | 产业链利润、需求替代弹性 |
| B. 跨期 | 同品种近月-远月 | RB 主力-次主力, AU 主力-次主力 | carry、储存成本、contango/backwardation |

### §1.2 **最佳应用周期**

| 模块 | interval | 适用度 | 理由 |
|---|---|---|---|
| **A. 跨品种** | **day** | ★★★★★ | spread 回归速度天 → 周；intraday 噪声占主导 |
| A. 跨品种 | 60min | ★★☆☆☆ | 仅适合极少数高频价差（如 IF-IH） |
| A. 跨品种 | 30min / 更短 | ☆☆☆☆☆ | 不建议（信号/噪声比太低） |
| **B. 跨期** | **day** | ★★★★★ | 主流配置；carry 信号变化以日为单位 |
| B. 跨期 | 60min | ★★★☆☆ | 用于流动性好的合约（如 AU 主次月）做日内调仓 |
| B. 跨期 | 30min / 更短 | ☆☆☆☆☆ | 不建议（成交对手薄） |

**两模块基线推荐**：**day-level**，60-day rolling z-score，进场 `|z| > 2`，
出场 `|z| < 0.5` 或 `|z| > 3.5`（stop）。

### §1.3 配对池（建议初始白名单）

#### §1.3.1 模块 A：跨品种白名单

| pair_key | leg1 | leg2 | cluster | 关系 | 推荐 leg 比例 |
|---|---|---|---|---|---|
| `rb_hc` | RB（螺纹钢） | HC（热轧卷板） | black | 同源（钢铁） | 1:1（吨数） |
| `m_rm` | M（豆粕） | RM（菜粕） | agri | 蛋白替代 | 1:1 |
| `j_jm` | J（焦炭） | JM（焦煤） | black | 上下游 | 1:1.3（焦炭吃 1.3 倍焦煤） |
| `cu_al` | CU（铜） | AL（铝） | metal | 有色对冲 | 1:5（按价值近似） |
| `oi_y` | OI（菜油） | Y（豆油） | agri | 油脂替代 | 1:1 |
| `i_rb` | I（铁矿石） | RB（螺纹） | black | 上下游 | 1.6:1 |
| `if_ic` | IF（沪深300） | IC（中证500） | index | 大小盘 | 1:1（按 notional） |
| `if_ih` | IF（沪深300） | IH（上证50） | index | 风格 | 1:1 |
| `au_ag` | AU（黄金） | AG（白银） | precious | 贵金属比价 | 1:80（gold-silver ratio） |
| `t_tf` | T（10y 国债） | TF（5y 国债） | bond | 期限结构 | 1:1.5 |

#### §1.3.2 模块 B：跨期白名单（同品种近月-远月）

候选基于流动性，初期只跑：

| pair_key | symbol | near_contract | far_contract | cluster |
|---|---|---|---|---|
| `rb_cal_1_3` | RB | 主力合约（M+1 个月） | M+3 个月后 | black |
| `i_cal_1_3` | I | 同上 | 同上 | black |
| `au_cal_1_3` | AU | 同上 | 同上 | precious |
| `cu_cal_1_3` | CU | 同上 | 同上 | metal |
| `if_cal_1_3` | IF | 当月 | 当季 | index |

**注意**：跨期套利需要**双合约数据**（主力 + 次主力），与现有 `RB0` 单合约
数据不一致。需在 [data_code/](../data_code/) 新增"按合约（非主力）"的数据
下载，参考 §11。

### §1.4 与现有策略的关系

| 已有 / 新增 | 名称 | 方向 | 关系 |
|---|---|---|---|
| 已有 | 单边 setup（breakout / pullback / momentum / mean_reversion） | 单合约 | 完全独立 |
| 已有 | OpportunityRanker | 单合约打分 | 完全独立 |
| **新增** | spread arbitrage | **配对** | 与单边策略低相关，分散风险 |

spread arbitrage 是**新的 trade 维度**：每个 trade 由两条单合约 leg 组成。

### §1.5 设计目标

1. **正交 alpha**：与单边 CTA 信号低相关（spread 回归是均值回归，单边是动量）。
2. **风险中性**：beta 趋近于零（多空对冲），与商品指数低相关。
3. **可解释**：z-score / 历史均值都可观测，逐笔可复盘。
4. **可灰度**：按 `(pair_key, interval)` opt-in。
5. **数据先行**：跨期模块依赖双合约数据；先建数据基础设施再上策略。

---

## §2 信号定义（数学）

### §2.1 spread 定义

```
spread(t) = log(price_leg1(t) / price_leg2(t))  # log 比价（更稳定）
# 或
spread(t) = price_leg1(t) - hedge_ratio * price_leg2(t)  # 价差
```

推荐 **log 比价**（log spread）：

- 量纲无关，跨品种可比
- 价格水平变化不影响 spread 分布
- 对 hedge_ratio 的估计误差不敏感

### §2.2 滚动均值与标准差

```python
rolling_mean(t) = spread.rolling(rolling_window_days).mean()
rolling_std(t) = spread.rolling(rolling_window_days).std(ddof=0)
zscore(t) = (spread(t) - rolling_mean(t)) / rolling_std(t)
```

`rolling_window_days` 默认 60 天（约 1 季度交易日）。

### §2.3 进场信号

- **short spread**（spread 偏高）：`zscore(t) >= z_entry`（默认 2.0）→
  short leg1, long leg2
- **long spread**（spread 偏低）：`zscore(t) <= -z_entry` → long leg1, short leg2

### §2.4 出场信号

- **mean-reversion exit**：`|zscore(t)| <= z_exit`（默认 0.5）→ 全部平仓
- **stop-loss exit**：`|zscore(t)| >= z_stop`（默认 3.5）→ 全部平仓（认为 spread
  分布漂移了）
- **time-based exit**：持仓达到 `max_holding_days`（默认 30）→ 强制平仓
- **regime-flip exit**：双 leg 中任一方 regime 切换到与 hedge 假设矛盾的 regime
  （可选，默认 off）

### §2.5 hedge ratio 估计（可选，模块 A）

跨品种 spread 用比例 ratio 而非 1:1 时，hedge_ratio 用：

```python
# 滚动 OLS 回归 leg1 = alpha + beta * leg2 + epsilon
beta = rolling_cov(leg1, leg2, window) / rolling_var(leg2, window)
hedge_ratio = beta
# 或 cointegration test 确定稳定 hedge_ratio
```

简化版：用 §1.3 表格中的固定经验比例。

---

## §3 配对池构建

### §3.1 pair 注册表

```python
# cta/config/spread_pair_registry.py
@dataclass(frozen=True)
class SpreadPair:
    pair_key: str
    pair_type: str  # "cross_instrument" or "calendar"
    leg1_symbol: str
    leg2_symbol: str
    leg1_exchange: str
    leg2_exchange: str
    hedge_ratio: float = 1.0  # leg1 -> leg2 转换比例
    cluster: str = ""
    description: str = ""

DEFAULT_CROSS_INSTRUMENT_PAIRS: tuple[SpreadPair, ...] = (
    SpreadPair("rb_hc", "cross_instrument", "RB", "HC", "SHFE", "SHFE", 1.0, "black", "螺纹/热卷"),
    SpreadPair("m_rm", "cross_instrument", "M", "RM", "DCE", "CZCE", 1.0, "agri", "豆粕/菜粕"),
    SpreadPair("j_jm", "cross_instrument", "J", "JM", "DCE", "DCE", 1.0 / 1.3, "black", "焦炭/焦煤"),
    ...
)

DEFAULT_CALENDAR_PAIRS: tuple[SpreadPair, ...] = (
    SpreadPair("rb_cal_1_3", "calendar", "RB_M1", "RB_M3", "SHFE", "SHFE", 1.0, "black", "螺纹近远"),
    ...
)
```

### §3.2 流动性过滤（与跨品种动量轮动一致）

| 过滤条件 | 默认值 |
|---|---|
| `min_avg_daily_volume_per_leg` | 30_000 手 |
| `min_avg_daily_open_interest_per_leg` | 50_000 手 |
| `min_history_days` | 252 |
| `exclude_within_rollover_window_days` | 5（比单边更保守） |

### §3.3 主力切换处理（模块 A）

跨品种用 RB0 / HC0 等连续合约，rollover 窗口扩大到 5 天。

### §3.4 跨期合约滚动（模块 B 专属）

跨期 spread 的近月合约会到期，需要 rollover 到下一组：

- **trigger**：near_contract 距离到期 < `rollover_days_before_expiry`（默认 10 天）
- **action**：平掉旧 spread（不论 z-score），开新 spread（near = 旧 far / 现新主力，
  far = 再后一个月）
- **rollover loss**：rollover 当天会产生强制平仓损益，纳入 trade log，
  exit_reason = `calendar_rollover_forced`。

---

## §4 entry / exit / stop 规则

### §4.1 entry 决策（每个 pair 每个 bar）

```python
def decide_spread_entry(
    pair: SpreadPair,
    bar_leg1: Bar,
    bar_leg2: Bar,
    spread_state: SpreadState,
    cfg: SpreadArbitrageConfig,
) -> SpreadEntryDecision | None:
    spread_now = compute_log_spread(bar_leg1.close, bar_leg2.close)
    if not spread_state.is_warmup_done():
        return None
    zscore = spread_state.zscore(spread_now)
    if zscore >= cfg.z_entry:
        return SpreadEntryDecision(
            pair=pair, side="short_spread",
            leg1_side="short", leg2_side="long",
            zscore_at_entry=zscore,
        )
    if zscore <= -cfg.z_entry:
        return SpreadEntryDecision(
            pair=pair, side="long_spread",
            leg1_side="long", leg2_side="short",
            zscore_at_entry=zscore,
        )
    return None
```

实际入场时刻：**次 bar 开盘**（与单边策略一致），加 slippage_ticks。

### §4.2 exit 决策

```python
def decide_spread_exit(
    open_spread: OpenSpreadPosition,
    bar_leg1: Bar,
    bar_leg2: Bar,
    spread_state: SpreadState,
    cfg: SpreadArbitrageConfig,
) -> ExitReason | None:
    spread_now = compute_log_spread(bar_leg1.close, bar_leg2.close)
    zscore = spread_state.zscore(spread_now)
    # mean-reversion exit
    if abs(zscore) <= cfg.z_exit:
        return "spread_mean_revert"
    # stop-loss exit
    if abs(zscore) >= cfg.z_stop:
        return "spread_zscore_stop"
    # time exit
    if (current_time - open_spread.entry_time).days >= cfg.max_holding_days:
        return "spread_time_stop"
    # calendar rollover (仅模块 B)
    if pair.pair_type == "calendar" and pair.near_contract.days_to_expiry < cfg.rollover_days_before_expiry:
        return "calendar_rollover_forced"
    return None
```

### §4.3 stop-loss 与单边 stop 的关系

spread arbitrage 用 **z-score stop**（`|z| >= 3.5`）而非单边 % stop。理由：

- spread 是中心化指标，3.5 sigma 是分布尾部，仍是均值回归框架内
- 用 % stop（如 5%）容易在正常波动中触发 → 错失均值回归
- 但**每条 leg 单独应该有 hard_stop**（默认 10%）防止极端单边行情

---

## §5 仓位管理 / 风控

### §5.1 spread 名义敞口

每个 spread trade 的 notional：

```python
notional_per_spread = equity * notional_pct_per_spread  # 默认 0.05（5%）
leg1_notional = notional_per_spread
leg2_notional = notional_per_spread * pair.hedge_ratio
```

两 leg 名义大致相等（市场中性）。

### §5.2 vol-target（可选）

按 spread 的历史波动反比缩放：

```python
notional_per_spread *= (target_spread_vol / realized_spread_vol(60d))
```

低波动 spread（如 IF-IH）可放大仓位，高波动 spread（如 J-JM）缩小仓位。

### §5.3 同时持仓上限

- `max_concurrent_spreads = 5`（同时最多 5 个 spread）
- `max_concurrent_per_cluster = 2`（同 cluster 最多 2 个，避免黑色系全 spread 集中）

### §5.4 与现有 cap 串接

- **single leg notional**：每条 leg 占用单品种 cap（`max_symbol_notional_pct`）。
- **cluster cap**：spread 双 leg 同 cluster 时，按 max(leg1, leg2) 计入 cluster cap；
  跨 cluster 时分别计入。
- **gross leverage**：spread 占用 ~2x 单边名义（long 1x + short 1x）；总 leverage
  仍受 `max_total_leverage` 限制。

### §5.5 kill switch

策略累计回撤 > `kill_switch_dd_pct`（默认 0.10）→ 暂停所有新 spread 开仓，
现有 spread 按正常 exit 规则平仓。

---

## §6 配置字段

```python
# cta/config/spread_arbitrage_config.py
@dataclass(frozen=True)
class SpreadArbitrageConfig:
    use_spread_arbitrage: bool = False
    rolling_window_days: int = 60
    z_entry: float = 2.0
    z_exit: float = 0.5
    z_stop: float = 3.5
    max_holding_days: int = 30
    use_log_spread: bool = True
    use_dynamic_hedge_ratio: bool = False  # True 时用 rolling OLS 估计
    hedge_ratio_window_days: int = 120

    notional_pct_per_spread: float = 0.05
    use_vol_target_weighting: bool = False
    target_spread_vol: float = 0.01
    max_concurrent_spreads: int = 5
    max_concurrent_per_cluster: int = 2

    leg_hard_stop_pct: float = 0.10
    kill_switch_dd_pct: float = 0.10

    rollover_days_before_expiry: int = 10  # 模块 B
    exclude_within_rollover_window_days: int = 5

    min_avg_daily_volume_per_leg: int = 30_000
    min_avg_daily_open_interest_per_leg: int = 50_000
    min_history_days: int = 252

    enabled_pairs: tuple[str, ...] = ()  # 显式 opt-in 的 pair_key 列表
    enabled_by_pair_interval: dict[str, bool] = field(default_factory=dict)
    # 格式：{"rb_hc|day": True, "m_rm|day": True}
```

`__post_init__` 校验：
- `0 < z_exit < z_entry < z_stop`
- `0 < notional_pct_per_spread <= 0.20`
- 所有 window / days 必须 > 0
- `enabled_by_pair_interval` 的 key 形如 `pair_key|interval`，value 必须是 bool

---

## §7 接口契约

### §7.1 spread feature 计算

```python
# cta/feature/spread_features.py
def compute_log_spread(price1: pd.Series, price2: pd.Series) -> pd.Series: ...

def compute_spread_zscore(
    spread: pd.Series,
    *,
    rolling_window_days: int,
) -> pd.Series: ...

def compute_dynamic_hedge_ratio(
    price1: pd.Series, price2: pd.Series,
    *, window_days: int,
) -> pd.Series: ...
```

### §7.2 spread 状态对象

```python
# cta/strategy/spread_state.py
@dataclass
class SpreadState:
    pair: SpreadPair
    spread_history: pd.Series  # 历史 spread
    rolling_mean: float
    rolling_std: float

    def is_warmup_done(self) -> bool: ...
    def zscore(self, current_spread: float) -> float: ...
    def update(self, new_spread: float) -> None: ...
```

### §7.3 套利 executor

```python
# cta/strategy/spread_arbitrage_strategy.py
class SpreadArbitrageStrategy:
    def __init__(self, cfg: SpreadArbitrageConfig, pairs: tuple[SpreadPair, ...]) -> None: ...

    def step(
        self,
        t: pd.Timestamp,
        bars_by_symbol: dict[str, pd.DataFrame],
        portfolio_state: PortfolioState,
    ) -> list[OrderIntent]:
        """返回当 bar 应下发的 spread 入场/出场订单。
        每个 OrderIntent 含 leg_id（leg1/leg2）、pair_key、side。"""
```

### §7.4 trade 表 schema 增列

为支持 spread trade 的归因，trade DataFrame 增加：

- `spread_pair_key` (str)：所属 pair
- `spread_side` (str)：long_spread / short_spread
- `spread_leg_id` (str)：leg1 / leg2
- `spread_zscore_at_entry` (float)
- `spread_zscore_at_exit` (float)
- `spread_pnl_pct` (float)：spread 维度的回报（两 leg 合算）

新增 `signal_type = "spread_arbitrage"`，新增 `exit_reason`：
`spread_mean_revert` / `spread_zscore_stop` / `spread_time_stop` /
`calendar_rollover_forced`。

---

## §8 与现有 baseline 的关系

### §8.1 共存

spread arbitrage 的 trade 与单边 trade 在同一 trade 表中，通过 `signal_type` 和
`spread_pair_key` 区分。

### §8.2 OOT 评估

spread trade 经过 [pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py)
的同一评估链路。

- **trade_filter / regime_gate / ma_cross_gate**：默认对 spread 候选**禁用**
  （spread 已经做了均值回归筛选，不需要这些方向 gate）。复用 §4.4 的
  `trade_filter_bypass_signal_types`，加入 `"spread_arbitrage"`。
- **portfolio cap / kill switch / risk_throttle**：所有 spread leg 都参与统计。
- **margin / leverage**：单笔 spread 占用 2x 单边的 leverage，
  `max_total_leverage` 受影响。

### §8.3 与 cross_sectional_momentum_rotation 的关系

二者完全独立，可同时启用。spread arbitrage 的 trade 用 `signal_type` 区分。
共享 §5.5 的 kill switch 计数，不共享分配权重。

---

## §9 测试清单

### §9.1 单元测试

`cta/feature/tests/test_spread_features.py`：

- `test_log_spread_basic` — 简单序列计算结果与手算一致
- `test_log_spread_handles_nan` — leg 中含 NaN 时输出 NaN，不 raise
- `test_zscore_basic` — 已知均值 / std 输出 z 一致
- `test_zscore_warmup_returns_nan` — 前 N 个值为 NaN
- `test_dynamic_hedge_ratio_basic` — 完美线性数据返回斜率 = 真值

`cta/strategy/tests/test_spread_arbitrage_strategy.py`：

- `test_default_disabled` — `use_spread_arbitrage=False` 不出 order
- `test_entry_short_spread_when_zscore_high` — `z >= 2` → short_spread
- `test_entry_long_spread_when_zscore_low` — `z <= -2` → long_spread
- `test_no_entry_in_mid_zone` — `|z| < 2` 不入场
- `test_exit_mean_revert` — 持仓中 `|z| < 0.5` → 平仓
- `test_exit_zscore_stop` — `|z| > 3.5` → stop
- `test_exit_time_stop` — 持仓 30 天强制平仓
- `test_calendar_rollover_forced_exit` — 近月到期 < 10 天强平
- `test_pair_disabled_by_cluster_interval` — `enabled_by_pair_interval` 之外不出 order
- `test_max_concurrent_spreads_cap` — 已开 5 个 spread 时不开新
- `test_max_concurrent_per_cluster_cap` — black cluster 已开 2 时不开第 3 个 black spread
- `test_hedge_ratio_applied_correctly` — leg1/leg2 名义按 hedge_ratio 缩放
- `test_kill_switch_pauses_new_entries` — 累计回撤 > 10% 不开新
- `test_signal_type_emitted` — trade 行 `signal_type == 'spread_arbitrage'`
- `test_exit_reasons_canonical` — `exit_reason` 在 canonical set 中

`cta/config/tests/test_spread_arbitrage_config.py`：

- `test_post_init_validates_z_order` — `z_exit >= z_entry` raise
- `test_post_init_rejects_bad_pair_interval_key` — `"foo_bar"` 无 `|` raise
- `test_post_init_normalizes_interval_alias` — `"day"` / `"d"` 同源

### §9.2 端到端 A/B（`/tmp` 输出）

```bash
# baseline 全 off
python3 -m cta.run.runner --start 2018-01-01 --end 2025-12-31 \
  --output-dir /tmp/spread_v0

# v1：仅跨品种 black 系
python3 -m cta.run.runner ... \
  --override use_spread_arbitrage=True \
    enabled_by_pair_interval='{"rb_hc|day":true,"j_jm|day":true,"i_rb|day":true}' \
  --output-dir /tmp/spread_v1

# v2：跨品种 + 跨期 RB
python3 -m cta.run.runner ... \
  --override use_spread_arbitrage=True \
    enabled_by_pair_interval='{"rb_hc|day":true,"rb_cal_1_3|day":true}' \
  --output-dir /tmp/spread_v2
```

**验收指标**：

- v1 vs v0：rotation trade 年化 sharpe > 0.8，max_drawdown < 8%
- v2 vs v1：calendar spread 单独不亏，且与 v1 月度相关性 < 0.3
- 任一版本：spread mean-revert exit 比例 > 60%（其他为 stop / time）

---

## §10 灰度策略

### §10.1 滚动启用日历

| 周次 | 启用 pair | 退出条件 |
|---|---|---|
| W1 | `rb_hc\|day` 单 pair | 月度净 pnl < 0 → 回退 |
| W2 | + `j_jm\|day`, `i_rb\|day`（black 链） | 同上 |
| W3 | + `m_rm\|day`, `oi_y\|day`（agri 替代） | 同上 |
| W4 | + `cu_al\|day`（metal） | 同上 |
| W5 | + 跨期 `rb_cal_1_3\|day` | 数据稳定性确认（无 rollover_forced 异常占比 > 20%） |
| W6 | + 更多跨期 | 同上 |

### §10.2 退出条件

- spread 累计 net_pnl < 0 持续 90 天 → 回退该 pair
- 单 pair 月度 sharpe < 0 持续 3 个月 → 回退
- `calendar_rollover_forced` exit 占比 > 30% → 该 calendar pair 回退（说明
  rollover 设置有问题）

---

## §11 数据基础设施需求

### §11.1 跨品种（模块 A）— 已就绪

复用现有连续合约数据：

- `cta/data/origin/day/RB0/`, `HC0/`, ... — 主力连续合约
- `cta/data/feature/day/RB0/`, ... — 特征 parquet

### §11.2 跨期（模块 B）— **需新增数据下载**

跨期需要"按合约（非主力）"的历史数据，即 `RB2401` / `RB2405` 等具体合约。

**新增任务**（[data_code/](../data_code/) 层）：

1. 在 [futures_downloader.py](../data_code/futures_downloader.py) 增加按合约下载
   接口（已有主力，新增 explicit contract）。
2. 新增 `cta/data/origin/contract/<symbol>/<YYYYMM>.parquet` 目录结构。
3. 实现"主力 / 次主力月份解析"：每天每个品种解析出主力（M1）和次主力（M3）合约
   代码。
4. 实现 calendar spread state 维护：spread 持仓时记录两条 leg 的具体合约代码，
   rollover 时知道切换到哪。

### §11.3 数据完整性测试

`cta/data_code/tests/test_contract_downloader.py`：

- `test_download_explicit_contract_basic` — 下载 RB2401 单合约数据
- `test_parse_main_and_secondary_contract` — 给定日期返回 (M1, M3) 合约代码
- `test_rollover_continuity` — 主力切换日的合约代码变化正确

---

## §12 已知限制

### §12.1 hedge_ratio 不稳定（模块 A）

部分 spread 的最优 hedge_ratio 随时间漂移（例如 J-JM 受焦化利润影响），固定 1.3
比例可能在某些时段失配。**对策**：开启 `use_dynamic_hedge_ratio=True`（性能更
好但实现复杂）；保守做法是定期手动重估并更新 `DEFAULT_CROSS_INSTRUMENT_PAIRS`。

### §12.2 cointegration 漂移

`spread = log(P1/P2)` 假设 P1/P2 是 cointegrated。但极端事件（例如 2022 镍逼仓）
会让 spread 长期失效。**对策**：z_stop = 3.5 是兜底，但回退后该 pair 应被人为
disable 一段时间。

### §12.3 跨期合约流动性

非主力合约成交量通常比主力低 1-2 个数量级，滑点显著。**对策**：
`min_avg_daily_volume_per_leg=30_000` 已经较严；初期只跑 RB / I / IF 等高流动品种。

### §12.4 跨期 carry 的 regime 依赖

contango / backwardation 切换时 spread 均值漂移巨大。**对策**：用更长的
rolling window（120 天）平滑，或加 regime-aware 双均值模式（默认 off）。

### §12.5 与单边持仓的对手风险

如果系统已经在单边 long RB（来自 momentum_setup），又叠加 short RB 的 spread
leg → 净 RB 仓位接近 0。这是 OK 的（账户 net 为 0，但占用保证金）。但要
确保 leg netting 在仿真 / 实盘正确处理（不要重复算手续费）。

---

## §13 实施 TODO（codex / claude 执行清单）

按 P0 → P3 顺序，每步先写测试再写实现。

### P0 — 数据与特征

| 任务 | 文件 | 测试 |
|---|---|---|
| 编写 `compute_log_spread` / `compute_spread_zscore` | `cta/feature/spread_features.py` | `cta/feature/tests/test_spread_features.py` |
| 编写 `SpreadPair` dataclass + DEFAULT_*_PAIRS 注册表 | `cta/config/spread_pair_registry.py` | `cta/config/tests/test_spread_pair_registry.py` |
| 编写 `SpreadArbitrageConfig` + `__post_init__` 校验 | `cta/config/spread_arbitrage_config.py` | `cta/config/tests/test_spread_arbitrage_config.py` |

### P1 — 模块 A（跨品种）

| 任务 | 文件 | 测试 |
|---|---|---|
| 编写 `SpreadState` | `cta/strategy/spread_state.py` | `cta/strategy/tests/test_spread_state.py` |
| 编写 `SpreadArbitrageStrategy.step`（模块 A only） | `cta/strategy/spread_arbitrage_strategy.py` | `cta/strategy/tests/test_spread_arbitrage_strategy.py` |
| 接入 portfolio_logic（cap / kill switch / signal_type） | `cta/portfolio_logic/spread_executor.py` | `cta/portfolio_logic/tests/test_spread_executor.py` |
| `trade_filter_bypass_signal_types` 添加 `"spread_arbitrage"` | `cta/model/oot/oot_gates.py` | 已有 oot_gates 测试加 case |

### P2 — 模块 B（跨期，**数据基建依赖项**）

| 任务 | 文件 | 测试 |
|---|---|---|
| 增加 explicit contract 下载 | `cta/data_code/futures_downloader.py` | `cta/data_code/tests/test_contract_downloader.py` |
| 主力 / 次主力解析 | `cta/data_code/main_secondary_resolver.py` | 同上 |
| Calendar spread state（含 rollover） | `cta/strategy/calendar_spread_state.py` | `cta/strategy/tests/test_calendar_spread_state.py` |
| `SpreadArbitrageStrategy` 扩展支持 calendar pair | 复用 `spread_arbitrage_strategy.py` | 已有测试加 calendar case |

### P3 — 文档与运维

| 任务 | 文件 | 测试 |
|---|---|---|
| `run.md` / `run.sh` 增加 `step_spread_arbitrage` step | `cta/run.md` / `cta/run.sh` |  |
| `block_reason.md` / `signal_type` canonical 增条目 | `cta/docs/block_reason.md` |  |
| `change_log.md` 写入变更 | `cta/report/change_log.md` |  |
| canonical exit_reason 集合更新（4 条新 reason） | `cta/portfolio_logic/exit_reasons.py`（如有） |  |

### 验证命令

```bash
# 1) 全部单测
python3 -m pytest \
  cta/feature/tests/test_spread_features.py \
  cta/strategy/tests/test_spread_arbitrage_strategy.py \
  cta/strategy/tests/test_spread_state.py \
  cta/strategy/tests/test_calendar_spread_state.py \
  cta/portfolio_logic/tests/test_spread_executor.py \
  cta/config/tests/test_spread_pair_registry.py \
  cta/config/tests/test_spread_arbitrage_config.py \
  cta/data_code/tests/test_contract_downloader.py -q

# 2) 既有 pipeline 不退化
python3 -m pytest cta/model/tests/test_pipeline_oot_evaluation.py -q

# 3) docs / canonical
python3 -m pytest cta/run/tests/test_docs_sync.py -q
python3 -m pytest -k canonical -q

# 4) 端到端 smoke（模块 A，最简单）
python3 -m cta.run.runner \
  --interval day --start 2020-01-01 --end 2025-12-31 \
  --override use_spread_arbitrage=True \
    enabled_by_pair_interval='{"rb_hc|day":true}' \
  --output-dir /tmp/spread_rbhc_smoke

# 5) 端到端 smoke（模块 B，需先确认数据就绪）
python3 -m cta.run.runner \
  --interval day --start 2020-01-01 --end 2025-12-31 \
  --override use_spread_arbitrage=True \
    enabled_by_pair_interval='{"rb_cal_1_3|day":true}' \
  --output-dir /tmp/spread_rbcal_smoke
```

---

## §14 不变量

- **默认 off**：`use_spread_arbitrage=False` 且 `enabled_by_pair_interval={}` 时，
  所有 trade 行为完全等同于不存在 spread 模块。
- **不动训练样本分布**：spread 是 candidate 生成层的新增 signal_type，不修改
  其他 setup 的 candidate。
- **不动现有 cap**：spread trade 占用现有 cluster_cap / symbol_cap / leverage 限制，
  不允许通过 spread 绕开。
- **OOT / sim / live 一致**：三层共用同一 cfg。
- **TDD**：所有新文件先写测试。
- **数据先行（模块 B）**：跨期模块在 §11.2 数据下载完成前不允许启用。
- **canonical**：新增 4 个 `exit_reason`、1 个 `signal_type` 必须进入对应 canonical 集合。

---

## §15 风险与回退

| # | 风险 | 应对 |
|---|---|---|
| R1 | spread 长期失效（cointegration 漂移） | z_stop=3.5 兜底 + 灰度回退该 pair |
| R2 | hedge_ratio 误差大 | 动态 hedge 选项 / 定期手动重估 |
| R3 | 非主力合约流动性差导致滑点失控 | `min_avg_daily_volume_per_leg=30_000` + slippage 配置 |
| R4 | calendar rollover 切换 bug → 持仓状态混乱 | `test_calendar_rollover_forced_exit` + 强 invariant 检查 |
| R5 | 双 leg 同时下单失败（一条成交，另一条没成）→ 单边裸仓 | sim/live 必须保证 atomic：要么两 leg 都进，要么都不进；订单失败时立即对冲 |
| R6 | spread 收益被 commission 吃光（双 leg 双手续费） | base_notional_pct 较小（0.05）+ z_entry 较高（2.0）保证胜率 |
| R7 | 与单边持仓的 netting bug → 重复算手续费 | 仿真/实盘必须正确处理同合约 long+short netting |

---

## §16 参考实现链路

- 现有 spread 数学基础：[feature/trend.py](../feature/trend.py)（rolling mean / std）
- 现有 cluster 配置：[config/symbol_cluster_config.py](../config/symbol_cluster_config.py)
- 现有 portfolio cap：[portfolio_logic/config.py](../portfolio_logic/config.py)
- 现有连续合约：[data_code/futures_downloader.py](../data_code/futures_downloader.py)
- canonical block_reason / exit_reason：[docs/block_reason.md](./block_reason.md)
- 同款"按 key 灰度"模式：[ma_cross_regime_aware_design.md](./ma_cross_regime_aware_design.md)、
  [cross_sectional_momentum_rotation_design.md](./cross_sectional_momentum_rotation_design.md)

---

## §17 性能指标基线（参考）

| 指标 | 目标值（按 spread trades only 评估） |
|---|---|
| 年化收益 | > 6% |
| 年化 sharpe | > 1.0（spread 应比单边更稳定） |
| 最大回撤 | < 8% |
| Calmar | > 0.75 |
| 月度胜率 | > 60% |
| 与全商品指数相关性 | < 0.2 |
| 平均持仓天数 | 7-20 |
| mean-revert exit 占比 | > 60% |
| stop exit 占比 | < 15% |

---

## §18 v2 后续扩展

1. **三 leg spread**：例如 J-JM-RB 焦化利润链，或 M-RM-OI 三油脂。复杂度高但
   对冲效果更好。
2. **机器学习 z-score 阈值**：用 LightGBM 训练"当前 z-score 是否会回归"二分类，
   动态调整 z_entry / z_exit。
3. **正交化 spread basket**：把多个低相关 spread 组合成单一组合，用 PCA / RMT
   降维。
4. **跨市场 spread**：A 股商品 vs 外盘（CME / LME）。需新增外盘数据。
5. **期权对冲 spread tail**：用对应品种的 option 对冲 spread 极端尾部风险（仅
   AU / CU / IF 等有期权的品种）。
6. **regime-aware spread**：在不同 vol_regime / contango/backwardation regime 下用
   不同 z_entry / window，类似 [voi_momentum_config.py](../config/voi_momentum_config.py)。
