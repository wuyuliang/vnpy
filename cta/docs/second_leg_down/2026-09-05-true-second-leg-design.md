# 真·第二段下跌：补回"第一段"与"回抽"

## 1. 现在的规则漏了什么

`design.md` 实现的是"一段够强的下推 → 跟进做空"。它里面**没有第一段**——
不看之前有没有跌过、有没有回抽，只要在均线空头排列下出现一条强腿就开火。

问题是：一条已经走完的强腿，本身就是**衰竭**的高发地。实测印证了这一点
（5 分钟，21 品种，2026H1，91 个样本）：

| 拆分维度 | n | 中位 MFE | 中位 MAE | MFE−MAE |
| --- | ---: | ---: | ---: | ---: |
| 全样本 | 91 | 0.30R | 0.40R | **−0.10** |
| 突破结构低点 | 80 | 0.29R | 0.38R | −0.09 |
| 未突破 | 11 | 0.35R | 0.57R | −0.22 |
| 腿 < 6 ATR | 74 | 0.31R | 0.38R | −0.07 |
| **腿 ≥ 6 ATR** | 17 | 0.28R | 0.57R | **−0.29** |
| 放量 ≥ 3× | 82 | 0.31R | 0.38R | −0.07 |

**每一个子样本的 MFE−MAE 都是负的，而且腿越长越差。**拉到 30 根（2.5 小时）
也只有 36% 摸到 0.5R、8% 摸到 1R。这不是样本不够，是方向反了：这个口径在做
"追衰竭"，而不是"跟趋势"。

`leg_5min` 亏 14,848、`leg_n4` 亏 30,409，胜率 25% / 37.5%，与上表一致。

## 2. Brooks 的第二段是三段结构

```
        ╱╲  ← ② 回抽（反弹 20%~90%，2~6 根）
       ╱  ╲
──────╱    ╲___ ← ③ 第二段：跌破回抽起点 → 入场
  ╲  ╱
   ╲╱  ← ① 第一段：创新低
```

关键差别：**入场根不需要很大**。真正做多空判断的是"第一段确立了方向、回抽
没吃掉它、然后价格重新往下走"这个**结构**，而不是某一根 K 线的暴力程度。

这同时解决了两个问题：

- **机会**：不再要求罕见的巨型腿，普通阴线就能触发
- **位置**：在回抽的高点附近做空，而不是在腿的末端

## 3. 实测对照（同一批 5 分钟数据）

| | 现口径（单腿） | 本方案（第一段+回抽） |
| --- | ---: | ---: |
| 样本数（21 品种 / 半年） | 91 | **631** |
| 折算 40 品种 | 173 | **1,201**（≈200/月） |
| MFE ≥ 0.5R | 26% | **54%** |
| MFE ≥ 1R | 5% | **24%** |
| 中位 MFE | 0.30R | **0.55R** |
| 中位 MAE | 0.40R | 0.50R |
| **MFE − MAE** | **−0.10** | **+0.05** |

13 倍的机会，而且是试过的所有口径里**唯一一个中位 MFE 超过 MAE 的**。

> 两个必须说清的口径差异：
> 1. 本方案的 R 是"回抽高点 − 入场价"，比现口径的 R 小，所以 R 倍数**不能
>    跨表直接比**。上表内部（同一列口径一致）的比较才成立。
> 2. 这是**无成本的前瞻统计**，没有撮合、手续费、滑点和资金上限。+0.05 的
>    中位优势很薄，扣掉成本未必还在。它证明的是"这个方向值得跑一轮完整回测"，
>    不是"这个策略能赚钱"。

参数稳健性也扫过：第一段门槛 1.5~2.5 ATR、回抽 2~4 / 2~6 / 2~10 根、入场根
1.0~1.5 ATR、放量 1.2~1.5×、回看 20/40 根——`n` 在 300~900 之间变化，
MFE−MAE 始终在 0 附近且不翻号。**没有单点尖峰，是一个区域**。

## 4. 信号定义（严格因果）

全部在已完成的**信号周期** K 线上判定（`signal_timeframe_minutes`，
1 或 5）。第 t 根收盘时决策，订单从下一根 **1 分钟** K 线生效。

记号同 `design.md`：`ATR(i)` 为 60 根简单平均并 `shift(1)`。

### 4.1 趋势过滤（不变）

`ema5(t) < ema10(t) < ema20(t)`

### 4.2 ① 第一段：确立方向

在 `t` 之前找一个**摆动低点** `j`（`t - pullback_max <= j <= t - pullback_min`）：

```
low(j) == min(low over [j - first_leg_lookback, j])      # j 是这段的最低点
first_leg = max(high over [j - first_leg_lookback, j]) - low(j)
first_leg >= first_leg_atr_mult * ATR(t)                 # 默认 1.5
```

`j` 必须与 `t` 同一个 session segment——跨休市的"第一段"不算数。

### 4.3 ② 回抽：反弹但没走完

```
rally_high = max(high over (j, t))                       # j 之后到 t-1
rally = rally_high - low(j)
0.20 * first_leg <= rally <= 0.90 * first_leg
```

下限挡住"根本没回抽、只是连续下跌"（那是同一段，不是第二段）；
上限挡住"回抽吃掉整段"（趋势已经被否定）。

### 4.4 ③ 第二段：入场根

```
body(t) < 0
abs(body(t)) >= entry_body_atr_mult * ATR(t)             # 默认 1.0，不是 4.0
close(t) - low(t) <= entry_lower_wick_ratio * abs(body(t))   # 默认 0.4，收在低点附近
low(t) < low(j)                                          # 跌破第一段的低点
volume(t) >= entry_volume_mult * baseline(t)             # 默认 1.2
```

`baseline` 与现口径一致：前 60 根、剔除每个 segment 的首根、样本不足 15 则判否。

### 4.5 时间窗（沿用刚修好的口径）

判的是**下单那一刻**，不是形态收尾那一根：

```
minutes_since_open(t) + 1 > entry_block_minutes_after_open    # 默认 10
minutes_until_close(t) - 1 >= entry_block_minutes_before_close # 默认 20
```

## 5. 仓位与出场

### 5.1 R 的定义（与现口径不同）

```
trigger    = close(t) - 1 tick
stop_price = rally_high + 1 tick
R          = stop_price - trigger
```

止损放在**回抽的高点**上方——这是结构止损的本意：回抽高点被突破，说明第二段
的假设已经被证伪。它比现口径的"形态区间最高价"更贴身，R 更小，同样的资金风险
下手数更多，所以 `max_capital_share` 那道门会更常起作用，要盯着
`capital_basis` / `capital_share` 两列看。

### 5.2 手数

`size_for_risk_band` 不变：0.2%~0.5% 权益，10% 资金占用封顶，不足 1 手买 1 手。

### 5.3 出场

止盈地板机制不变（`arm 0.5R / giveback 0.5R`）。两个时间止损的**参数要重标**：

现口径下只有 26% 能在 5 根内摸到 0.5R，所以 `no_progress_bars=5` 砍掉了 75% 的
仓位。本方案是 54%，但仍应按新分布重标——先跑一轮，用 `position_trace.csv`
和 `trades.csv` 的 `mfe_r` 分布定，**不要沿用旧值**。

## 6. 配置

新增 `SecondLegDownConfig` 字段（`entry_mode` 默认先留 `single_leg`，
A/B 通过 `--config-override` 切换，稳定后再改默认）：

```python
entry_mode: str = "single_leg"        # single_leg | pullback
first_leg_lookback: int = 20
first_leg_atr_mult: float = 1.5
pullback_min_bars: int = 2
pullback_max_bars: int = 6
pullback_min_ratio: float = 0.20
pullback_max_ratio: float = 0.90
entry_body_atr_mult: float = 1.0
entry_lower_wick_ratio: float = 0.4
entry_volume_mult: float = 1.2
```

`entry_mode="single_leg"` 时现有 `body_mode`/`leg_*` 一族照常工作，
两套口径互不影响——这样一次回测只变一个东西。

## 7. 候选表附加列

```
entry_mode                pullback
first_leg_index           j（相对信号根的偏移）
first_leg_atr             first_leg / ATR(t)
pullback_bars             t - j
pullback_ratio            rally / first_leg
rally_high
entry_body_atr            abs(body(t)) / ATR(t)
entry_volume_ratio        volume(t) / baseline(t)
break_of_swing_low        low(j) - low(t)
```

有了这些列，第一轮结果出来就能直接按回抽深度、第一段强度、入场根大小分层看，
不用再改代码重跑。

## 8. 实现顺序

1. `cta/strategy/common/bar_shapes.py` 加摆动低点与回抽的原语（向量化，
   现在的 `second_leg` 参考实现是逐根 Python 循环，40 品种半年会很慢）
2. `rules.py` 加 `match_pullback_pattern()`，与 `match_second_leg_pattern()`
   并列，由 `entry_mode` 分派
3. `strategy.py` 出候选，附加列按 §7
4. 因果性测试：信号之后的 K 线随便改，候选与 `trigger`/`stop_price`/`quantity`
   逐字节不变
5. 单品种离线冒烟 → 全量 → 按 `mfe_r` 分布重标两个时间止损

## 9. 验收

- `entry_mode="single_leg"` 时，`trades.csv` 与本次改动前**逐字节一致**
- `entry_mode="pullback"` 全量跑出 ≈1,000 量级候选（低于 500 说明实现比
  参考口径严，要逐门对照 `cta/analysis/second_leg_down_funnel_scan.py`）
- pytest 全绿

## 10. 参考实现

原型在 `cta/analysis/`（`second_leg_down_funnel_scan.py` 的扩展）。它是
**独立于生产代码**的向量化复刻，从 K 线间隔重推交易时段，可以用来交叉验证
生产实现的命中数——两边差得多就说明有一道门理解得不一样。
