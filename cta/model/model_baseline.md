# Baseline 策略与模型详解（`baseline_model.md`）

本文系统解读 `cta/strategy/` 下 4 个 baseline 策略的信号生成、入场/出场、参数与
工程实现，以及它们如何与 `cta/model/` 训练的三类 ML 模型组合在一起。
配套阅读：[`cta/feature/FEATURES.md`](../feature/FEATURES.md)、
[`cta/model/model.md`](model.md)、[`cta/run.md`](../run.md)。

---

## 1. 总览

四个 baseline 策略覆盖了 CTA 中最常见的两类形态：

| 策略 | 类型 | 形态归宿 | 触发逻辑 | 出场逻辑 | 入场单类型 |
|------|------|---------|---------|---------|-----------|
| **DonchianBreakout**     | 趋势跟随 | 长周期高低点突破     | 收盘 > N 日最高 / < N 日最低 | 收盘跌破 / 升过反向 N 日通道 | stop（下根 K 线高+tick 触发）|
| **ATRBreakout**          | 趋势跟随 | 价格通道突破        | 收盘穿越 MA±k·ATR 通道（前根未穿越，本根穿越）| 收盘回到中轨 MA | market（次根开盘成交）|
| **TightRangeBreakout**   | 价格行为 | 窄幅整理后突破      | tight-range 有效 + breakout-quality 通过 + （可选）顺趋势 | ATR 跟踪止损 / 时间止损（max_holding_bars） | stop |
| **BreakoutPullback**     | 价格行为 | 突破后回踩延续      | 收盘穿越 Donchian → 限期内回踩 ≤ k·ATR → 收盘再次确认 | ATR 跟踪止损 / 时间止损 | stop |

四者共享同一根 `cta.strategy.baseline_skill_suite.prepare_master_feature_frame`
预处理：一次性把 ATR14 / Donchian / ATR-Channel / TightRange / Trend / BreakoutQuality
/ Pullback 全部特征列写到 frame，每个策略各取所需。

---

## 2. 共用基础

### 2.1 一次性预处理 `prepare_master_feature_frame(bars, interval)`

输出列（按出现顺序）：
- `atr14` — 14 周期 ATR（Wilder EMA）
- Donchian：`don_upper_entry`(55)、`don_lower_entry`(55)、`don_upper_exit`(20)、`don_lower_exit`(20)、`don_atr20`
- ATR Channel：`atr_ma`(20)、`atr_value`(14)、`atr_upper`(=ma+2.5·atr)、`atr_lower`(=ma−2.5·atr)
- TightRange：`tr_valid`、`tr_upper`、`tr_lower`、`tr_range_atr`、`tr_count`、`tr_direction_bias`
- Trend：`trend_score`、`trend_dir`、`trend_strength`、`trend_maturity`
- BreakoutQuality：`breakout_score`、`breakout_pass`
- Pullback：`bp_valid`、`bp_direction`、`bp_breakout_level`、`bp_pullback_low`、`bp_bars_since_breakout`、`bp_confirmed`

所有特征都是**因果**的（`.shift(1)`/`rolling(...)` 写法保证），可放心用于实盘流式
计算。

### 2.2 `ContractSpec`

```python
ContractSpec(symbol="RB0", exchange="SHFE",
             multiplier=10.0, tick_size=1.0,
             commission_rate=0.0001, slippage_ticks=1.5)
```

- `multiplier / tick_size` —— 实盘下单时优先从 `cta_engine.get_size / get_pricetick`
  取真实合约元数据（见 `cta_baseline._build_contract`），缺失时回退此 setting
- `commission_rate / slippage_ticks` —— 仅回测用，实盘通过 vnpy `RiskGuard` /
  网关返回的真实成本

### 2.3 通用订单 dict（`_entry_order`）

```python
{"side": "long|short|flat",
 "lots": int,
 "order_type": "market|stop",
 "price": float,        # stop 单触发价
 "symbol": "RB0.SHFE",
 "multiplier": 10.0, "commission_rate": 1e-4, "tick_size": 1.0}
```

`LegacyCtaAdapter._dispatch_order` 会把它翻译为 vnpy 的
`buy/sell/short/cover(price, volume, stop=...)`。

---

## 3. 策略详解

### 3.1 DonchianBaselineStrategy

**信号**

```python
ue = bar["don_upper_entry"]         # 过去 55 日最高（不含今日）
le = bar["don_lower_entry"]         # 过去 55 日最低
ux = bar["don_upper_exit"]          # 过去 20 日最高
lx = bar["don_lower_exit"]          # 过去 20 日最低
```

**入场（position == 0）**
- `close > ue` 且做多许可 → 发 `stop` 单：`trigger = high + tick`（下一根 K 线
  突破当根高点 + 1 tick 即成交）
- `close < le` 同理触发 short

**出场**
- 多单：`close < lx` → market 平仓
- 空单：`close > ux` → market 平仓

**默认参数**：N_entry=55、N_exit=20，硬编码在 `prepare_master_feature_frame` 中；
若要改，需要直接编辑常数（`run_model_pipeline` / `run_baseline_suite` 不暴露）。

**适用环境**：长趋势品种（黑色、有色、贵金属）；震荡市频繁假突破。
**典型陷阱**：每次完整突破到反向 20 日通道才出，回撤可能 20%+。配合
`BreakoutPullback` 或 `TightRangeBreakout` 可以早一点入场更接近趋势起点。

**对应 CtaTemplate 子类**：[cta.strategy.cta_baseline.DonchianCta](../strategy/cta_baseline.py)

---

### 3.2 ATRBreakoutBaselineStrategy

**信号**

```python
up = bar["atr_upper"];   pup = prev["atr_upper"]
lo = bar["atr_lower"];   plo = prev["atr_lower"]
ma = bar["atr_ma"]       # 20-MA
```

**入场（必须**穿越事件**，不是持续越界）**
- `close > up` 且 `prev_close <= prev_up` 且做多许可 → market 单（次根开盘成交）
- `close < lo` 且 `prev_close >= prev_lo` 同理 short

**出场**
- 多单：`close < ma` → market 平仓
- 空单：`close > ma` → market 平仓

**默认参数**：MA=20、ATR=14、k=2.5（即 ATR 通道宽度 ±2.5σ，约 1% 触发分位数）

**适用环境**：稳定波动率品种；ATR 突变品种（黑色暴涨）会过早出场。
**典型陷阱**：用 close 而非 high/low 判断穿越，跳空开盘越界但收盘回到通道内不入场，
对部分跳空趋势品种漏单。

**对应 CtaTemplate 子类**：[cta.strategy.cta_baseline.AtrBreakoutCta](../strategy/cta_baseline.py)

---

### 3.3 BreakoutPullbackBaselineStrategy

**两阶段信号**

阶段一（在 `prepare_master_feature_frame` 中预先标注，特征列 `bp_*`）：
- 锚点 = 收盘穿越 Donchian 入场通道的那根 K 线（同 §3.1 信号）
- 锚点之后 ≤ 20 根 K 线内出现回踩，回踩深度 ≤ 1.5×ATR14（用 atr at anchor）
- 回踩后 close 重新穿过 breakout level → `bp_confirmed = True`

阶段二（策略 `on_bar` 实时触发）：
- `bp_valid AND bp_confirmed` 且方向被 trade_side_mode 允许
- 用 `pullback_entry_trigger(setup, bar, tick_size)` 出 stop 单：
  - long：`trigger = high + tick`，`stop = pullback_low − tick`
  - short：`trigger = low − tick`，`stop = pullback_low + tick`

**有状态出场**（与前两策略不同，类有内部状态）

```python
self._high_since_entry / self._low_since_entry   # 持仓期间极值
trail = high_since_entry − trailing_stop_atr_mult * atr14  # 多
self._stop_price = max(stop_price, trail)        # 单调上移
```

退出条件（任一满足）：
1. **跟踪止损**：低点跌破 `_stop_price`（多）/ 高点穿过（空）
2. **时间止损**：持仓 bar 数 >= `max_holding_bars`

**默认参数**
- `max_bars_since_brk=20`、`max_pullback_atr=1.5`（特征构造时硬编码）
- `max_holding_bars=30`、`trailing_stop_atr_mult=2.0`、`initial_stop_atr_mult=1.2`
  （策略实例化时可调，亦由 `BreakoutPullbackCta.parameters` 暴露）

**适用环境**：趋势确立后做"二次进场"，避免突破当根的高滑点；适合中频（30min/60min）
**典型陷阱**：信号稀疏，单品种全年 < 30 笔；需要多 symbol POOL 训练 trade_filter
才能在样本足够的前提下做模型加权。

**对应 CtaTemplate 子类**：[cta.strategy.cta_baseline.BreakoutPullbackCta](../strategy/cta_baseline.py)

---

### 3.4 SkillTightRangeBreakoutStrategy

不在 `baseline_skill_suite.py` 中，独立实现于 [`cta/strategy/skill_tight_range_breakout.py`](../strategy/skill_tight_range_breakout.py)。
信号链是四个里面**最完整**的（窄幅整理 + 突破质量打分 + 趋势对齐）。

**信号链**（均在 `prepare_strategy_frame` 中预算）

1. **TightRange 检测** —— 过去 N 根 K 线（默认 lookback=10）的 `(high.shift(1).max
   − low.shift(1).min) / atr14 < α`（默认 1.5），且累计满足次数 ≥ `min_count`
   （默认 5），方向 bias 由短期 close 趋势 inferred
2. **趋势对齐** —— `compute_trend_state(out)` 输出 `trend_dir ∈ {-1, 0, +1}`；
   开 `align_trend_direction=True` 时方向必须与 trend_dir 一致
3. **Breakout Quality 打分** —— `score_breakout(...)` 综合振幅/位置/收盘相对
   level 等给出 0–1 分；`breakout_quality_gate(quality, min_breakout_score)` 决定
   是否通过门槛（默认 0.30）

**入场触发**：`resolve_breakout_trigger(setup, bar, tick)`
- close > tr_upper → long stop @ `high+tick`，stop @ `low−tick`
- close < tr_lower → short stop @ `low−tick`，stop @ `high+tick`
- 否则按 `direction_bias` 给出预期方向 + 区间上沿（仅当 `breakout_pass` 为 True 时该方向才发）

**出场**（与 BreakoutPullback 类似，有状态 trailing）
- ATR 跟踪止损：`high_since_entry − trailing_stop_atr_mult × atr14`（默认 2.2）
- 时间止损：`max_holding_bars`（默认 20）

**仓位管理（独有）** —— `compute_lots(atr_value)` 把单笔风险预算
`capital_base × risk_per_trade_pct` 除以 `atr × initial_stop_atr_mult × multiplier`
得"理想手数"，与硬底 `lots` 取较大值。

**默认参数**（见 `cta.config.skill_tight_range_breakout_config.StrategyConfig`）
| 字段 | 默认 | 说明 |
|------|------|------|
| `lookback` | 10 | tight-range 滚动窗口 |
| `alpha` | 1.5 | range/ATR 阈值 |
| `min_count` | 5 | 滚动窗口内 tight 计数 |
| `min_breakout_score` | 0.30 | breakout-quality 门槛 |
| `lots` | 1 | 单笔最小手数 |
| `risk_per_trade_pct` | 0.005 | 单笔风险占资金比例 |
| `initial_stop_atr_mult` | 1.5 | 初始止损 = ATR × 这个倍数 |
| `trailing_stop_atr_mult` | 2.2 | 跟踪止损宽度 |
| `max_holding_bars` | 20 | 时间止损 |
| `align_trend_direction` | True | 是否要求顺趋势 |
| `trade_side_mode` | "both" | 多空开关 |

**对应 CtaTemplate 子类**：[cta.strategy.cta_tight_range.SkillTightRangeBreakoutCta](../strategy/cta_tight_range.py)

---

## 4. 三类 ML 模型

`cta.model.model_pipeline` 在每条候选事件（同一根 K 线、同一信号类型对应一条
样本）上训练三个模型，分别承担不同职责：

### 4.1 TradeFilterModel

- **目标**：二分类 — 这条候选事件**是否值得开仓**
- **输入特征**：`feature_*` 列（在 `cta/data/feature/*.parquet` 离线生成的 ~400 个
  通用特征，来自 `cta.feature.run_all_features`）+ 当前信号上下文（atr14、tr_*、
  bp_* 等）
- **标签**（`label_class`）：与 `future_mfe_atr / future_mae_atr` 配合，由
  `_resolve_candidate_entry` 在生成候选时计算 — 简化定义：N bar 内 mfe/mae 比例
  足以盈利则 1、否则 0
- **输出**：每个候选事件的 `predict_proba`（≈ 0–1）
- **应用**：仿真/实盘用 `cta.live.model_filter.make_trade_filter(model_path,
  threshold=0.55)` 加在 `adapter.order_filter` 上 — 模型概率不到阈值则跳过下单
- **典型阈值**：从 OOT IC ≥ 0.02、hit-rate ≥ 0.52 反推；先用 0.5，再按 OOT
  decile 收益曲线收紧

### 4.2 RegimeClassifierModel

- **目标**：多分类 — 当前市场处于什么状态（趋势 / 震荡 / 破坏）
- **标签**（`regime_label`）：由 `_infer_regime_label(row)` 基于 trend_dir /
  trend_strength / volatility 等离散化得到
- **输出**：每个候选的 regime 类别 + 概率
- **应用**（当前主要用于离线分析）：
  - 把 trade_filter 在不同 regime 下的命中率分组对比；只在 regime 概率足够确定的
    候选上下单
  - 也可以作为 trade_filter 的输入特征（将分类概率拼进特征列）
- **目前没有现成的 in-loop hook**：要在实盘联用，需要按 trade_filter 类似的
  pattern 写一个 `make_regime_filter(...)`，决定哪些 regime 跳过下单。

### 4.3 MfeMaeModel

- **目标**：回归 — 预测每条候选未来 N bar 的 `future_mfe_atr` 与 `future_mae_atr`
- **标签**：由 `_resolve_candidate_entry` 提前算好
- **输出**：(预期最大有利位移, 预期最大不利位移)
- **应用**：
  - **动态止损**：`stop = entry − k × predicted_mae_atr × atr14`（替代硬编码
    `initial_stop_atr_mult=1.2`）
  - **风险预算**：`lots = capital × pct / (predicted_mae × multiplier)`
- 同样 **目前未在策略 `_dispatch_order` 中接入**，要求修改 `BreakoutPullbackCta` 或
  `SkillTightRangeBreakoutCta`，把模型预测注入 `_pending_initial_stop` 字段。

---

## 5. 组合搭配模式

下述表格按"工程化程度 / 实盘可用性"由低到高排列：

| 模式 | 策略 | 模型 | 数据范围 | 适用场景 |
|------|------|------|---------|---------|
| **A. 纯规则** | 任意单一 | 无 | 单 symbol 单 interval | 入门验证、breakout 风格基线 |
| **B. 4 策略并行** | Donchian + ATR + TightRange + BreakoutPullback | 无 | 单 symbol 多 interval | 多形态互补、降低单策略 drawdown |
| **C. 加 trade_filter** | 任一 baseline | TradeFilterModel | 单 symbol | 信号充足品种（分钟级 RB0/I0），减少假突破 |
| **D. POOL trade_filter** | 任一 baseline | TradeFilterModel（POOL） | 多 symbol 共享 | day 等单品种样本不足时（强推荐）|
| **E. 全栈** | 任一 baseline | trade_filter + regime + mfe_mae | 多 symbol 共享 | 实盘成熟期，每类模型各司其职 |

### 5.1 模式 A — 纯规则单策略

```bash
python3 -m cta.strategy.skill_tight_range_backtest \
    --symbol RB0 --interval 60min \
    --start 2018-01-01 --end 2024-12-31 --trade-side-mode both
```

或用 CtaTemplate 子类（仿真/实盘共享代码）：

```python
from cta.run.cta_backtester import run_via_event_driven
from cta.strategy.cta_baseline import DonchianCta
res = run_via_event_driven(
    strategy_class=DonchianCta, vt_symbol="rb888.SHFE",
    setting={"trade_side_mode": "both"}, bars=bars, out_dir="report/...",
)
```

### 5.2 模式 B — 4 策略并行多 vt_symbol

每个 `(symbol, strategy)` 各启动一个 strategy 实例；vnpy `MainEngine` 单进程可承载
~50 个策略实例。`cta.run.multi_runner.run_multi` 提供回测端的批量入口（见
`run.md §5.4`）。仿真/实盘端用 `run_sim` 循环：

```python
from cta.sim.sim_runner import SimRunConfig, SimnowSetting, run_sim
from cta.strategy.cta_baseline import (DonchianCta, AtrBreakoutCta, BreakoutPullbackCta)
from cta.strategy.cta_tight_range import SkillTightRangeBreakoutCta

main_engine = None
for sym in ["rb888.SHFE", "hc888.SHFE", "i888.DCE"]:
    for cls in (DonchianCta, AtrBreakoutCta, BreakoutPullbackCta, SkillTightRangeBreakoutCta):
        cfg = SimRunConfig(
            strategy_class=cls,
            strategy_name=f"{cls.__name__}_{sym.split('.')[0]}",
            vt_symbol=sym,
            setting={"trade_side_mode": "both"},
            warmup_days=10,
        )
        main_engine = run_sim(cfg, simnow,
            main_engine_factory=(lambda me=main_engine: me) if main_engine else None)
```

> 风控：所有策略共用一个 `RiskGuard` 实例，单 symbol 仓位上限按 `MaxPositionLimit
> (limits={...})` 字典分发，避免 4 策略对同一 symbol 共同打满。

### 5.3 模式 C — 加 trade_filter（单 symbol）

训练（per-symbol，分钟级）：

```bash
python3 -m cta.model.model_pipeline \
    --symbol RB0 --exchange SHFE --interval 60min \
    --start 2018-01-01 --end 2024-12-31 \
    --train-end 2022-12-31 --valid-end 2023-12-31 \
    --by-signal-type --generic-mode auto
```

仿真/实盘加挂：

```python
from cta.live.model_filter import make_trade_filter
from cta.live.online_feature import OnlineFeatureLoader
from cta.live.risk import make_risk_filter

loader = OnlineFeatureLoader()  # 默认从 cta/data/feature/ 加载完整特征
mf = make_trade_filter(
    "cta/backtest/{run}/models/trade_filter_RB0_60min.joblib",
    threshold=0.55, feature_provider=loader,
)
rf = make_risk_filter(guard, capital=1_000_000,
                     daily_pnl_provider=adapter.pnl_tracker.get_pnl)
adapter.order_filter = lambda o, a: rf(o, a) and mf(o, a)
```

### 5.4 模式 D — POOL trade_filter（推荐 day interval）

```bash
python3 -m cta.model.model_pipeline \
    --top-n-symbols 18 \
    --symbols-ranking-path cta/feature/symbols_research_ranking.csv \
    --interval day --start 2010-01-01 --end 2025-12-31 \
    --train-end 2020-12-31 --valid-end 2023-12-31 \
    --by-signal-type --generic-mode auto \
    --pool                                      # ← 关键
```

输出 `cta/backtest/{date}_POOL_day_both_model_pipeline/`。
应用：与模式 C 完全一致，模型路径改成 POOL 目录下的 `trade_filter_*.joblib`，
跨品种推理由 `OnlineFeatureLoader` 按 `adapter.vt_symbol` 自动路由特征。

> POOL 模型最大优势：day interval 单品种 ~150 笔候选 → 训练欠拟合；POOL 18 品种
> ~2700 笔 → 模型可学到跨品种共性（突破前的窄幅、放量等特征），泛化更稳。

### 5.5 模式 E — 全栈（trade_filter + regime + mfe_mae）

需要在 BreakoutPullbackCta / SkillTightRangeBreakoutCta 内部把三个模型集成：

```python
class FullStackCta(SkillTightRangeBreakoutCta):
    """演示三模型集成；实际接入需 fork 或子类化。"""
    parameters = SkillTightRangeBreakoutCta.parameters + [
        "trade_filter_path", "regime_path", "mfe_mae_path", "min_filter_prob",
    ]
    trade_filter_path: str = ""
    regime_path: str = ""
    mfe_mae_path: str = ""
    min_filter_prob: float = 0.55

    def on_init(self):
        super().on_init()
        from cta.live.model_filter import make_trade_filter
        from cta.live.online_feature import OnlineFeatureLoader
        loader = OnlineFeatureLoader()
        if self.trade_filter_path:
            self.order_filter = make_trade_filter(
                self.trade_filter_path,
                threshold=float(self.min_filter_prob),
                feature_provider=loader,
            )
        # regime / mfe_mae 接入需要修改 inner 策略 stop 计算 — 当前框架未提供，
        # 需要派生 inner v1 类、把模型预测注入 _pending_initial_stop 等字段。
```

模式 E 的 regime / mfe_mae 集成属于**用户工程化任务**，框架只到 trade_filter 层
默认提供 hook；mfe_mae 需要修改 inner 策略 stop 公式，regime 需要外层投票。

---

## 6. 调参与避坑

### 6.1 信号充足度自检

跑完 baseline 后查看候选样本数：

```bash
wc -l cta/backtest/*_model_pipeline/*_candidates.csv
# day interval：单 symbol < 200 笔 → 必须 POOL 训练
# 60min：单 symbol 1000+ 笔 → 可以 per-symbol
# 5min：单 symbol 5000+ 笔 → per-symbol 信号丰富
```

### 6.2 prepare_master_feature_frame 是因果的

所有 rolling/shift 都用 `.shift(1)`；新增特征时务必沿用同一约定，否则会引入向前看
偏差（详见 `cta/skills/filtering_scoring/breakout_quality.py` 的
`_allow_future` 守门设计、change_log 2026-05-08 第四轮 leak 审计）。

### 6.3 多策略间避免**重复持仓**

DonchianCta / TightRangeCta 在同一根 K 线可能同时给出 long 信号；如果共享 vt_symbol，
risk_guard 的 `MaxPositionLimit` 会按总持仓限制；不同策略独立账户的话，需要业务
层去重（例如锁定首个开仓的策略）。

### 6.4 trade_filter 阈值不要照搬训练日 OOT 指标

线上数据分布会漂移。建议：
1. 用 `cta.run.cta_backtester.run_via_event_driven` 在最近 6 个月数据上重跑
   带 filter 的回测
2. 调阈值使**每月成交笔数 ≥ 3**（避免月度噪声放大）

### 6.5 BreakoutPullback 跟踪止损偏紧时易被洗出

`trailing_stop_atr_mult=2.0` 是默认中位；震荡剧烈品种（焦煤 JM、铁矿 I）建议改成
2.5–3.0；日内品种（rb 1m）可以 1.5–1.8。该参数在 `BreakoutPullbackCta.parameters`
中可通过 setting 字典覆盖。

### 6.6 ATR-Channel 的"穿越事件"要求 prev_close 在通道内

意味着持续越界但慢慢爬升的趋势会**漏单**。可以增加一个互补策略（DonchianCta 默认
就是持续越界触发）做对冲。

---

## 7. 模型训练数据流回顾

```
原始 OHLCV (cta/data/origin/)
    └─→ cta.feature.run_all_features
        └─→ cta/data/feature/{interval}/{prefix}/{date}.parquet  (~400 列)
                └─→ cta.model.feature.candidate_training_dataset
                    └─→ cta/data/model_feature/                    (候选 + 标签)
                        └─→ cta.model.model_pipeline [--pool]
                            └─→ cta/backtest/{date}_*_model_pipeline/
                                ├── *_candidates.csv  / pool_members.csv
                                ├── *_feature_table.csv
                                ├── *_metrics.csv
                                ├── *_predictions.csv
                                ├── *_top10_feature_importance.csv
                                ├── *_last_oot_decile_returns.csv
                                └── models/
                                    ├── trade_filter_*.joblib  + _features.csv
                                    ├── regime_classifier_*.joblib
                                    └── mfe_mae_*.joblib
```

**应用阶段流向**

```
vnpy 行情 → adapter.on_bar
    ├─→ adapter._buffer (BarData 累积)
    ├─→ prepare_frame → adapter._frame (baseline 内嵌 ~30 列特征)
    ├─→ inner.on_bar(i, last_bar, pos) → 候选订单 dict
    └─→ adapter._dispatch_order
        ├── adapter.order_filter (可选)
        │   ├── make_risk_filter (RiskGuard + KillSwitch)
        │   └── make_trade_filter (TradeFilterModel + OnlineFeatureLoader)
        └── 通过则 buy/sell/short/cover → vnpy gateway → 市场
```

---

## 8. 参考

- Donchian / ATR / Tight-range / Pullback 详细 skill 文档：
  - [`cta/skills/cta_skills/03_trend_strategies/01_donchian_breakout.md`](../skills/cta_skills/03_trend_strategies/01_donchian_breakout.md)
  - [`cta/skills/cta_skills/03_trend_strategies/02_atr_breakout.md`](../skills/cta_skills/03_trend_strategies/02_atr_breakout.md)
  - [`cta/skills/cta_skills/02_price_action/01_tight_range_breakout.md`](../skills/cta_skills/02_price_action/01_tight_range_breakout.md)
  - [`cta/skills/cta_skills/02_price_action/03_breakout_pullback_continuation.md`](../skills/cta_skills/02_price_action/03_breakout_pullback_continuation.md)
- 模型管道总览：[`cta/model/model.md`](model.md)
- 特征清单：[`cta/feature/FEATURES.md`](../feature/FEATURES.md)
- 端到端运行：[`cta/run.md`](../run.md) / [`cta/run.sh`](../run.sh)
