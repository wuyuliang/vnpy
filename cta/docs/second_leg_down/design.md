# 第二段下跌策略 — 设计与实现规格

面向实现者（Codex）。目标是新增一个 1 分钟做空策略，与
`multi_timeframe_trend` 并行，**不改变后者任何一个输出**。

---

## 1. 策略是什么

Al Brooks 的经典做空形态：市场已经在下跌趋势中（均线空头排列），出现一段
**放量的连续大阴线**打穿最近的结构低点，说明卖方接管；在这段推进的末端跟进
做空，赌"第二段下跌"。这类形态的特征是**要么马上走，要么不走**——所以出场
规则里有两条时间止损，比止损位本身更重要。

方向：**只做空**（direction = -1）。

---

## 2. 架构原则：并行，不分叉

### 2.1 原样复用（一行都不要抄）

| 复用的东西 | 位置 |
| --- | --- |
| 分钟数据下载 / 发现 / 缺口诊断 | `cta/strategy/brooks/cycle_v1/backtest/runner.py`、`market_data_update.py` |
| 执行元数据（手续费、保证金、涨跌停、交易日历） | `prepare_backtest_metadata`、`vendor_metadata_cache.py` |
| 选池（`--top-n` / `--include-top-turnover`） | `download_universe.py`、`cta/data_code/build_symbol_turnover.py` |
| **组合回放引擎** | `cta/strategy/multi_timeframe_trend_backtest/engine.py::replay_trend_portfolio` |
| 报表 / 图表 / 聚合缓存 / 逐根埋点 | 同包的 `report.py`、`charts.py`、`aggregation_cache.py`、`engine_components/tracing.py` |
| 候选契约 | `multi_timeframe_trend_strategy.CANDIDATE_COLUMNS` |

**引擎已经是方向无关的**（`engine.py` 里有 52 处带方向符号的表达式，
`direction` 取 ±1）。做空不需要新的撮合、计费或保证金逻辑。

**新策略必须产出与 `CANDIDATE_COLUMNS` 完全相同的候选表**（多出来的列追加在
后面即可）。只要守住这个契约，报表、图表、成交额门槛、板块集中度、日内熔断
全都白拿。

### 2.2 必须先"通用化"再复用的四处

这四处目前写死了"多头趋势策略"的假设。**先做无行为变更的重构，跑一遍
`multi_timeframe_trend` 的黄金对比（§10.3）确认 `trades.csv` 逐字节一致，
再开始写新策略。**

**(a) 配置类型**

引擎函数签名上写死 `MultiTimeframeTrendConfig`。抽一个共享基类：

```
cta/config/replay_common.py
    @dataclass(frozen=True)
    class BaseReplayConfig:   # 引擎读到的字段，全部搬到这里
        risk_per_trade / max_risk_per_trade
        entry_blocked_session_windows
        daily_circuit_breaker_*
        max_positions_per_sector
        pre_break_*
        order_max_recess_minutes
        profit_floor_*
        drawdown_scale_*
        turnover_share_threshold / turnover_lookback_days
        ...
```

`MultiTimeframeTrendConfig(BaseReplayConfig)` 与
`SecondLegDownConfig(BaseReplayConfig)` 各自继承。

**硬约束：现有字段名、默认值、`__post_init__` 的每一条校验都不许变。**
`--config-override` 靠字段名工作，改名会静默破坏所有历史复跑命令。

**(b) 引擎里的多头专属簿记**

`filled_bull_trend_ids`、`_record_bull_trend_fill`、
`FIRST_TREND_ENTRY_DAILY_BREAKOUT_BUFFER_NOT_MET`、
`DAILY_DIRECTION_INVALID` 这一族，都依赖候选表里的 `daily_bull_trend_id` /
`daily_direction`。

改法：重命名为方向中性（`filled_trend_segment_ids`、
`_record_trend_segment_fill`），**并且按"列是否存在且非空"决定是否启用**，
而不是按方向判断。新策略不提供 `daily_bull_trend_id`，这些门自然全程放行。
不要用 `if direction > 0` 来绕——那会在将来做多做空混跑时再炸一次。

**(c) `five_minute_context` 的必填列**

`_context_lookup`（`engine.py:2413`）要求 `bar_end` + `daily_direction`。
新策略是纯 1 分钟的，没有日线方向。

改法：把 `daily_direction` 变成**可选**——缺列时按"不约束"处理，
而不是抛错。新策略把 1 分钟上下文帧直接当 `five_minute_context` 传进去
（引擎只用它按 `bar_end` 查当根上下文，不关心它是几分钟的）。

> **这是本次实现的头号风险点。** 先单独写一个最小用例：构造一个不含
> `daily_direction` 的上下文帧喂给 `replay_trend_portfolio`，确认能跑通、
> 且 `multi_timeframe_trend` 的行为不变。

**(d) 仓位计算的风险区间**

`multi_timeframe_trend_rules.size_for_risk` 硬校验
`risk_per_trade ∈ [0.01, 0.02]`（1%–2%）。本策略要 0.2%–0.5%。

**不要放宽这个校验**——它守着趋势策略。新写一个函数（§5.2），放在共享的
`cta/strategy/second_leg_down/sizing.py`，趋势策略的那个保持不动。

---

## 3. 文件清单

新策略的代码**全部收在 `cta/strategy/second_leg_down/` 一个包里**，
和 `multi_timeframe_trend_backtest/` 平级：

```
cta/strategy/second_leg_down/
    __init__.py
    config.py              SecondLegDownConfig（继承 BaseReplayConfig）
    bar_shapes.py          K 线形态原语（§4.2）
    session_edges.py       距 segment 开盘/收盘的分钟数（§4.4）
    sizing.py              风险区间 + 资金占用封顶（§5）
    rules.py               1 分钟特征与形态判定
    strategy.py            候选生成（产出 CANDIDATE_COLUMNS）
    backtest/
        __init__.py
        runner.py          CLI + 编排
    tests/
        __init__.py
        test_bar_shapes.py
        test_session_edges.py
        test_sizing.py
        test_rules.py
        test_strategy.py       含因果性测试（§10.2）
        test_exits.py          §6.3 / §6.4
```

包内自带 `tests/` 与 `brooks/cycle_v1/tests`、`brooks/scalp/tests` 的既有
惯例一致。**注意仓库没有配 `testpaths`**，跑全量时要显式带上这个目录：

```bash
python3 -m pytest cta/strategy/tests cta/strategy/second_leg_down/tests -q
```

### 3.1 为什么有两样东西不放在这个包里

**`cta/config/replay_common.py`（`BaseReplayConfig`，§2.2a）** ——
它是从现有 `MultiTimeframeTrendConfig` 里抽出来的公共基类，
`multi_timeframe_trend_config.py` 要 `import` 它。放进
`second_leg_down/` 会让趋势策略反过来依赖做空策略的包，依赖方向就倒了。
它和它的兄弟 `multi_timeframe_trend_config.py` 待在 `cta/config/`。

**引擎改动** —— §2.2b/c 和 §6.3/§6.4 改的是共享的
`multi_timeframe_trend_backtest/engine.py`，两个策略都跑它。

> 引擎眼下住在 `multi_timeframe_trend_backtest/` 里，名字上像是趋势策略专属，
> 实际是共享组件。**这次不要动它的位置**——搬包会把黄金对比（§10.3）
> 和所有历史 `RUN_COMMAND.sh` 一起搅乱。等两个策略都稳定跑起来之后，
> 再单独提一次"把 engine 提升为 `cta/strategy/replay/`"的重构。

### 3.2 修改清单（都要保持 multi_timeframe_trend 行为不变）

```
cta/config/replay_common.py                     新增：BaseReplayConfig
cta/config/multi_timeframe_trend_config.py      改为继承 BaseReplayConfig
cta/strategy/multi_timeframe_trend_backtest/engine.py
      §2.2b 多头簿记重命名 + 按列存在与否启用
      §2.2c daily_direction 变可选
      §6.3/§6.4 两个新出场原因（配置开关，默认关）
cta/strategy/multi_timeframe_trend_backtest/engine_components/models.py
      _Position 增加新出场需要的状态字段
cta/strategy/tests/test_replay_config_base.py   新增：§2.2a 的重构守卫
```

`backtest/runner.py` 的 CLI **照抄** `multi_timeframe_trend_backtest/runner.py`
的参数名（`--start/--end/--symbols/--top-n/--download-minute-data/
--config-override/--trace-candidate/--allow-missing-symbols/...`），
两条命令行可以互换记忆。真正能共用的编排代码优先从
`multi_timeframe_trend_backtest/runner.py` 里抽成模块级函数就地复用；
抽不动的宁可显式重复，也不要做半吊子的继承或参数化。

## 4. 入场信号（严格因果）

**全部定义在已完成的 1 分钟 K 线上。** 在第 t 根收盘时做判定，订单从 t+1 根
开始生效。任何用到 t 之后信息的写法都是穿越。

记号：`body(i) = close(i) - open(i)`，阴线即 `body < 0`。
`ATR(i)` = 1 分钟 ATR14（`cta/feature/volatility.atr`），**必须 `shift(1)`**，
即第 i 根用的是截至 i-1 的 ATR。

### 4.1 趋势过滤

1 分钟 EMA（`cta/feature/trend.ema`，close 上计算）在第 t 根满足：

```
ema5(t) < ema10(t) < ema20(t)
```

### 4.2 形态

两种，任一满足即可。设"第一根" = F，"最后一根" = L，L 恒为 t。

**形态 A（两根）**：F = t-1, L = t，两根都是阴线。

**形态 B（三根）**：F = t-2, M = t-1, L = t。F、L 是阴线；M 是小实体
（十字星），判定为
`abs(body(M)) <= small_body_atr_mult * ATR(M)`（默认 0.5）
**且** `high(M) <= high(F)`（不许向上突破，否则不是回踩而是反转）。

两种形态共同要求：

```
abs(body(F)) >= big_body_atr_mult * ATR(F)      # N，默认 3.0
abs(body(L)) >= big_body_atr_mult * ATR(L)
```

**影线约束**（"最高价跟开盘价挨得很近" / "最低价与收盘价很近"）：

```
high(F) - open(F) <= wick_body_ratio * abs(body(F))     # 默认 0.15
close(L) - low(L) <= wick_body_ratio * abs(body(L))
```

> 用实体的比例、而不是 ATR 的比例，是因为大阴线的实体本身就是量纲：
> 一根 5×ATR 的阴线带 0.5×ATR 的上影线，相对它自己是干净的。

### 4.3 放量

对 F 和 L 两根**分别**要求：

```
volume(i) >= volume_surge_mult * baseline(i)          # M，默认 2.0
```

`baseline(i)` = 第 i 根之前 20 根已完成 1 分钟 K 线成交量的**均值**，
**剔除每个交易时段的第一分钟**（开盘那一分钟的集合竞价量会把基线抬得没法用）。
有效样本 < `volume_baseline_min_samples`（默认 15）时**判为不满足**（fail
closed）——放量是这个形态的核心，样本不够时宁可不做。

> 实现提示：`cta/data/origin/minute` 里每个时段的第一分钟标注为
> `09:01` / `21:01`（`21:00` 那根开盘竞价目前在加载阶段就被丢了，见
> `cta/strategy/docs/2026-09-05-profit-floor-session-open-bar-trace.md`）。
> 剔除规则要按"该根是否是所属 segment 的第一根"判断，不要写死时间字面量。

### 4.4 交易时间窗

- 每个 session segment **开盘后 10 分钟内**不入场
- 每个 segment **收盘前 20 分钟内**不入场

新建 `cta/strategy/second_leg_down/session_edges.py`，从 `SessionSpec.segments`
算出每根 K 线距本 segment 开盘 / 收盘的分钟数：

```python
def minutes_since_segment_open(bar_end, sessions) -> int | None
def minutes_until_segment_close(bar_end, sessions) -> int | None
```

**不要**复用 `entry_blocked_session_windows`（那是按墙上时钟写死的窗口，
对夜盘收盘时间不一的品种是错的）。夜盘有 23:00 / 01:00 / 02:30 三种收盘，
必须按 segment 相对位置算。

### 4.5 加分项：突破结构

```
structure_low = min(low) over the 60 completed bars before F
structure_break = 1 if low(L) < structure_low else 0
```

**默认不做硬过滤**，只写进候选表当作可 A/B 的列。
`require_structure_break`（默认 `False`）打开后才变成入场条件。

### 4.6 组合

以上 4.1–4.4 全部满足 → 生成一个候选，`structure_break` 作为附加列。

---

## 5. 仓位

### 5.1 止损位与 R（已定）

```
stop_price   = open(F)                          # 形态区间第一根 K 线的开盘价
entry_price  = close(L) - 1 tick                # §5.4
R            = stop_price - entry_price
             = open(F) - close(L) + 1 tick      # 做空，stop 在上方
```

止损放在**第一根大阴线的开盘价**，不是形态区间的最高价：按 §4.2
`high(F) - open(F) <= 0.15 * abs(body(F))`，`open(F)` 之上那一小截上影线是
噪音，把止损抬到 `high(F)` 只是白白放大 R。`open(F)` 是这段推进的实体顶部，
被打回去就说明这段推进被否定了。

`stop_price > entry_price` 是形态本身保证的：F 与 L 都是阴线，
`open(F) > close(F) ≈ open(L) > close(L)`，所以 R 至少是两根大实体之和，
量级约 `2 * big_body_atr_mult * ATR`（默认 ≈ 6×1min ATR）。**这是一个偏宽的
止损**，配合 §5.3 会直接决定手数由谁封顶，见下。

配置 `stop_mode`，用于敏感性对比，默认 `pattern_open`：

| 取值 | stop_price |
| --- | --- |
| `pattern_open`（默认） | `open(F)` |
| `pattern_high` | `max(high over pattern bars) + 1 tick` |
| `atr` | `entry_price + stop_atr_mult * ATR`（默认 1.5） |

### 5.2 手数

```
loss_per_lot = R * multiplier + stressed_round_trip_cost   # 含手续费与滑点
E            = 当前权益

q = floor(max_risk_pct * E / loss_per_lot)                 # max 0.005
if q * loss_per_lot < min_risk_pct * E:                    # min 0.002
    q = ceil(min_risk_pct * E / loss_per_lot)
if q < 1 or q * loss_per_lot > max_risk_pct * E:
    q = 1                                                  # "不足 1 手时买 1 手"
```

再套名义/保证金上限（§5.3），取小。写在
`cta/strategy/second_leg_down/sizing.py::size_for_risk_band()`，签名显式带
`min_risk_pct` / `max_risk_pct`，**不要**去动
`multi_timeframe_trend_rules.size_for_risk`。

### 5.3 资金占用上限

单笔占用 ≤ `max_capital_share`（默认 0.10）× 权益。
元数据能提供保证金率时按**保证金**算，否则按**名义**
（`entry * multiplier * q`）算，并在候选表里记 `capital_basis` 列
（`margin` / `notional`）说明用的哪个口径，免得两台机器跑出不同手数还查不出来。

> **这一条在本策略里不是兜底，而是主要的手数决定者，两个口径差一个数量级。**
> 以 100 万权益、RB、1 分钟 ATR≈2 点为例：R ≈ 6×ATR = 12 点，
> `loss_per_lot ≈ 12 × 10 = 120` 元，风险区间给出
> `floor(0.5% × 1e6 / 120) ≈ 41` 手。
> - 名义口径：41 手名义 ≈ 123 万 ≫ 10 万上限 → **压到 3 手左右**
> - 保证金口径（保证金率 ~10%）：10 万保证金 ≈ 100 万名义 → **33 手左右**
>
> 所以元数据里保证金率有没有，直接决定这个策略是 3 手还是 33 手。实现时
> 必须把 `capital_basis` 和 `capital_share` 都写进候选表，并且在
> `summary.json` 里统计两种口径各占多少笔——第一版结果出来后，
> 这是首先要看的数字。

### 5.4 入场价

```
trigger = close(L) - 1 tick
```

做空的追空触发单：后续某根 `low <= trigger` 时成交，成交价
`min(open, trigger)` 再按不利方向取整加基础滑点——与多头逻辑完全镜像，
直接复用引擎现有的撮合，不要另写。

订单有效期沿用 `order_max_recess_minutes`（不得跨越长休市）与
`order_expire_at`。建议 `order_ttl_bars` 默认 3 根：这个形态过了三分钟就不是
它了。

---

## 6. 出场

### 6.1 止损

`stop_price`（§5.1），即 1R。

### 6.2 移动跟踪止盈 —— 直接复用现成机制

"最高盈利 > 0.5R 后，最大盈利回撤 0.5R 出场"，正是现有的止盈地板：

```
profit_floor_enabled     = True
profit_floor_arm_r       = 0.5
profit_floor_giveback_r  = 0.5
profit_floor_giveback_pct = 0.0      # 只用固定 0.5R，不要按比例回吐
```

`engine_components/drawdown.py::_refresh_profit_floor` 的
`max(giveback_r, giveback_pct * peak_r)` 在 `pct=0` 时退化成固定 0.5R。
**一行引擎代码都不用改。** 峰值走 1 分钟 K 线最高/最低价，触发后下一根开盘
市价成交并加 `profit_floor_extra_slippage_ticks`（默认 1 跳）——语义与趋势
策略一致。

> 已知缺陷：每个时段开盘那一分钟的集合竞价 K 线在加载阶段被丢弃，
> 会让跨休市的峰值晚一根确认。见
> `cta/strategy/docs/2026-09-05-profit-floor-session-open-bar-trace.md`。
> 本策略是 1 分钟高频出场，受这个缺陷影响比趋势策略更大，实现时先记着，
> 那个问题独立修。

### 6.3 无进展时间止损（新）

> 入场后 `no_progress_bars`（默认 5）根 1 分钟 K 线内，浮盈峰值从未
> ≥ `no_progress_min_r`（默认 0.5R）→ 第 5 根收盘后，下一根开盘市价清仓。

新出场原因 `NO_PROGRESS_TIME_STOP`。

### 6.4 跟随不足（新）

> 入场后 `follow_through_bars`（默认 3）根内没有出现一根"实体大阴线"
> （判定同 §4.2 的 `big_body_atr_mult`，可用独立的
> `follow_through_body_atr_mult`，默认 2.0，比入场松一档）
> → 把目标降为 1R：此后浮盈一旦 ≥ 1R 就清仓，不再等跟踪止盈。

新出场原因 `NO_FOLLOW_THROUGH_TARGET`。注意这条是**降级目标**，不是立即
平仓；6.3 才是立即平仓。两条同时命中时 6.3 优先（更早）。

### 6.5 出场优先级

同一根 K 线上多个条件命中时：

```
1. STOP（保护性止损，最坏情况优先成交）
2. NO_PROGRESS_TIME_STOP
3. PROFIT_FLOOR
4. NO_FOLLOW_THROUGH_TARGET
5. 收盘/跨休市等既有强制平仓
```

与现有引擎一致：**击穿只挂起，下一根开盘成交**，不许回填到触发根内。

### 6.6 引擎改动方式

6.3 / 6.4 两条都加在 `_manage_open_position_impl` 里，**由配置开关控制，
默认关闭**（`BaseReplayConfig` 里默认 `no_progress_bars = 0` 表示禁用），
这样 `multi_timeframe_trend` 完全不受影响。

`_Position` 需要新增：`bars_since_entry`、`peak_r_seen`、
`follow_through_seen: bool`。`bars_since_entry` 用现成的 `entry_bar_index`
与当根 `_bar_index` 相减即可，不要另存计数器（跨休市时计数器会漂）。

---

## 7. 配置

`cta/strategy/second_leg_down/config.py`：

```python
@dataclass(frozen=True)
class SecondLegDownConfig(BaseReplayConfig):
    # 4.1 趋势
    ema_fast: int = 5
    ema_mid: int = 10
    ema_slow: int = 20
    # 4.2 形态
    atr_period: int = 14
    big_body_atr_mult: float = 3.0          # N
    small_body_atr_mult: float = 0.5
    wick_body_ratio: float = 0.15
    allow_three_bar_pattern: bool = True
    # 4.3 放量
    volume_surge_mult: float = 2.0          # M
    volume_baseline_bars: int = 20
    volume_baseline_min_samples: int = 15
    # 4.4 时间窗
    entry_block_minutes_after_open: int = 10
    entry_block_minutes_before_close: int = 20
    # 4.5 结构
    structure_lookback_bars: int = 60
    require_structure_break: bool = False
    # 5 仓位
    stop_mode: str = "pattern_open"         # pattern_open | pattern_high | atr
    stop_atr_mult: float = 1.5
    min_risk_pct: float = 0.002
    max_risk_pct: float = 0.005
    max_capital_share: float = 0.10
    order_ttl_bars: int = 3
    # 6 出场
    profit_floor_enabled: bool = True
    profit_floor_arm_r: float = 0.5
    profit_floor_giveback_r: float = 0.5
    profit_floor_giveback_pct: float = 0.0
    no_progress_bars: int = 5
    no_progress_min_r: float = 0.5
    follow_through_bars: int = 3
    follow_through_body_atr_mult: float = 2.0
```

`__post_init__` 逐条校验（正数、`min_risk_pct < max_risk_pct`、
`ema_fast < ema_mid < ema_slow`、`stop_mode` ∈ {pattern_open, pattern_high, atr}……），与现有配置类
同风格。所有 bool/int/float 字段自动可被 `--config-override NAME=VALUE` 覆盖。

---

## 8. 候选表附加列

在 `CANDIDATE_COLUMNS` 之后追加，方便事后做条件筛选：

```
pattern_type              two_bar | three_bar
first_body_atr            abs(body(F)) / ATR(F)
last_body_atr             abs(body(L)) / ATR(L)
first_upper_wick_ratio
last_lower_wick_ratio
first_volume_ratio        volume(F) / baseline(F)
last_volume_ratio
volume_baseline_samples
structure_low
structure_break           0 | 1
minutes_since_open
minutes_until_close
capital_basis             margin | notional
capital_share             实际占用 / 权益
```

---

## 9. 命令行

```bash
# 单品种离线冒烟
python3 -m cta.strategy.second_leg_down.backtest.runner \
  --start 2026-01-01 --end 2026-04-01 --initial-equity 1e+06 \
  --symbols RB --meta-root <meta_cache/…> --no-auto-metadata \
  --chart-outcomes none --run-id sld_smoke

# 全量
python3 -m cta.strategy.second_leg_down.backtest.runner \
  --start 2026-01-01 --end 2026-07-01 --initial-equity 1e+06 \
  --download-minute-data --top-n 40 --include-top-turnover 0.8 \
  --allow-missing-symbols --run-id sld_v1
```

输出目录：`cta/strategy/report/second_leg_down/<run_id>/`，
表结构与 `multi_timeframe_trend` 一致（`trades.csv` / `candidates.csv` /
`rejections.csv` / `daily_equity.csv` / `metadata_gaps.csv` / `summary.json` /
`RUN_COMMAND.sh` / `opportunity_charts/`）。

---

## 10. 测试要求

### 10.1 形态原语（手搓 K 线，逐条断言）

每个谓词都要有**刚好满足**和**刚好不满足**两个用例：
大实体阈值、小实体十字星、上下影线比、放量倍数、基线样本不足、
剔除时段首根、EMA 排列、结构突破。

### 10.2 因果性

对每个候选：把信号时刻之后的所有 K 线**改成任意值**重跑，候选集合与
`trigger` / `stop_price` / `quantity` **必须逐字节不变**。这是本仓库
`multi_timeframe_trend` 反复踩过的坑，新策略从第一天就要有这条测试。

### 10.3 黄金对比（最重要）

§2.2 的重构做完后、写新策略之前：

```bash
# 重构前后各跑一次，同一 run 参数
python3 -m cta.strategy.multi_timeframe_trend_backtest.runner \
  --start 2026-01-01 --end 2026-04-01 --initial-equity 1e+06 \
  --symbols L --meta-root <…> --no-auto-metadata --chart-outcomes none \
  --run-id golden_before   # / golden_after
```

`trades.csv`、`candidates.csv`、`daily_equity.csv` 必须**逐字节一致**。
不一致就说明通用化改动泄漏了行为，先修再往下走。

### 10.4 出场

6.3 / 6.4 各写一组用例：命中、不命中、与 STOP / PROFIT_FLOOR 同根时的优先级。
调试用 `--trace-candidate <id>`，会输出 `position_trace.csv`
（逐根记录地板、峰值、挂起状态、判定结果）。

### 10.5 全量

`python3 -m pytest cta/strategy/tests cta/strategy/second_leg_down/tests -q`
不许变红（`cta/strategy/tests` 当前基线 577 passed）。
`test_engine_module_structure.py` 守着 `engine.py` 的行数与顶层定义数量——
新出场逻辑如果撑破上限，把它拆进 `engine_components/`，不要抬阈值。

---

## 11. 实现顺序

1. §2.2 四处通用化 + §10.3 黄金对比通过（**先做完这一步再动策略**）
2. `second_leg_down/bar_shapes.py`、`session_edges.py`、`sizing.py` + §10.1 测试
3. `second_leg_down/config.py`、`second_leg_down/rules.py`
4. `second_leg_down/strategy.py`（候选生成）+ §10.2 因果性测试
5. §6.3 / §6.4 两个新出场 + §10.4 测试
6. `second_leg_down/backtest/runner.py` + 单品种离线冒烟
7. 全量回测，出第一版结果

第 1 步和第 7 步之间的任何一步跑不通，都停下来说清楚卡在哪，
**不要为了让它跑起来去放宽既有的校验**。

---

## 12. 口径确认状态

1. **R 的价格来源 —— 已定**（§5.1）。
   `stop_price = open(F)`，`R = open(F) - close(L) + 1 tick`。
   `stop_mode` 保留 `pattern_high` / `atr` 两个取值做敏感性对比。

2. **最大10% 资金占用的口径 —— 已定**（§5.3）。
   元数据有保证金率时用保证金，没有时退回名义，口径写进候选表
   `capital_basis` 列。注意这一条实际上决定了本策略的手数量级，见 §5.3 的算例。

3. **0.2% 下限的含义 —— 已定**（§5.2）。
   "如果加到最大10%资金，单笔止损金额不低于权益的 0.2%，低于就不做"；
