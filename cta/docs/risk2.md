# CTA 防爆仓 / 生存层风控 — `cta/docs/risk2.md`

> 状态：**设计 / 策略清单文档，代码未实现**（本轮仅出方案，与 [`risk.md`](risk.md) 初版同体例）。
> 视角：十几年中国商品 CTA 实盘的"生存层"经验——**先保证不死，再谈赚钱**。
> 关联：[`risk.md`](risk.md)（结构化 + alpha 保护）、[`20260602_risk_codex.md`](20260602_risk_codex.md)（单次 OOT 调参）、
> [`sim_plan.md`](sim_plan.md)、[`sim_live_integration_roadmap2.md`](sim_live_integration_roadmap2.md)。

---

## §0 定位：三份风控文档的分工

| 文档 | 关注层 | 核心问题 |
|------|--------|----------|
| [`risk.md`](risk.md)（22 节） | **alpha 保护 + 正常市况结构化风控** | 涨跌停 / 流动性 / 换月 / 节假日 / 模型漂移 / VaR 预算——"如何在正常市场里少亏、多留 alpha" |
| [`20260602_risk_codex.md`](20260602_risk_codex.md) | **单次回测调优** | 降杠杆 / 压 hard_stop / 降换手——"这一版 OOT 怎么调更稳" |
| **本文档 `risk2.md`** | **极端黑天鹅生存层（防爆仓）** | "原油负价 / 连续停板锁死时，账户**会不会被强平、会不会穿仓倒欠**" |

> **铁律（贯穿全文）：生存优先于收益。** 任何 alpha / 优化层规则（quantile 阈值、bucket 缩仓、
> ranker 打分……）**都不得放宽生存层的硬约束**。两者冲突时，永远以生存层为准。

爆仓不是"少赚一点"，而是**账户清零 + 倒欠经纪商**，是不可逆的终局风险。它的概率极低，但一次就出局——
因此它的防护逻辑与日常风控**完全不同**：日常风控做"期望优化"，生存层做"最坏情况下的存活保证"。

---

## §1 爆仓到底是怎么发生的（先讲清"怎么死的"）

中国期货爆仓 ≠ "策略亏到净值为 0"，而是下面 4 条**机制性死法**：

### §1.1 保证金不足 → 期货公司强平
- 中国是**逐日盯市 + 盘中实时风控**。当 `可用资金 < 0`（权益 < 占用保证金）时，期货公司**有权按
  结算价 / 对手价直接强平**你的仓位，不需要你同意，且通常在最不利的时点。
- 强平是"被动平仓 + 滑点 + 可能反向锁死"三重打击，往往把浮亏坐实成实亏。

### §1.2 连续涨跌停锁死 → 想平平不掉
- 行情单边封板时，**反向盘口没有成交量**：持反向仓位的人"想平平不掉、想反向开开不进"。
- 浮亏每天按停板幅度累积（黑色 ~7%/天、能化 ~8%、部分品种更宽），**多日锁死可击穿全部保证金**。
- 历史教训：2016 黑色多次连续涨停、2020 黑色 / 化工连续跌停。锁死期间止损形同虚设。

### §1.3 跳空穿透止损 → 止损价 ≠ 成交价
- 隔夜 / 跨假期跳空、停板打开瞬间的剧烈滑点，会让"挂在 X 价的止损"**实际成交在远离 X 的位置**。
- 回测里 `hard_stop` 默认"在止损价成交"，**实盘根本不保证**——这是回测乐观、实盘爆仓的经典裂缝。

### §1.4 极端负价 / 异常价 → 所有公式失效
- WTI 2020-04-20 结算 **-37.63 美元**。这一天击穿了几乎所有量化系统的隐含假设：
  - `notional = price × multiplier × lots`：price < 0 → notional 变负、符号反转。
  - **百分比止损 / ATR%**：`stop = price × (1 - x%)` 在 price ≤ 0 时方向反转或为正。
  - **保证金公式**：按合约价值百分比算的保证金在负价下无意义。
  - 期权 Black-Scholes 直接报错（log 负数）。
- 结果：多头不仅亏光本金，还**倒欠经纪商**（穿仓）。这是"模型假设被现实击穿"的终极案例。

> **结论**：防爆仓要防的，是上面 4 类——它们都**不是"概率分布里多走两个标准差"**，而是
> **"分布本身被改写"**。所以靠历史回撤统计（VaR / dd 阶梯）来防是无效的，必须用**最坏情景的存活测算**。

---

## §2 现状为何挡不住爆仓（gap 分析，引真实代码）

`risk.md` 的体系很完整，但它整体是"正常市况 + alpha 保护"层，**缺一整层"生存约束"**。逐条核对：

| # | 现有组件 | 它管什么 | **为什么挡不住爆仓** |
|---|----------|----------|----------------------|
| 1 | [`config.py:CapsConfig`](../portfolio_logic/config.py)（`max_total_notional_pct=1.5` / cluster 0.5 / symbol 0.3） | 名义敞口占权益的百分比 | **完全不建模交易所保证金 / 维持保证金 / 强平线**。150% notional 在保证金 10% 时 = 15% 占用，看似安全；但**连续 3 天停板**可让浮亏吃掉远超 15% 的权益——notional cap 与"会不会被强平"是两个维度。 |
| 2 | [`risk_throttle.py:RiskThrottle`](../portfolio_logic/risk_throttle.py)（L121；dd 5/10/15% 阶梯，`current_drawdown_pct` L80） | 按**已实现回撤**阶梯缩仓 | 黑天鹅是**瞬时跳空 / 隔夜跳空**，当 dd 真正算出来时**仓位已经在停板里出不来了**。阶梯节流是"事后减速"，不是"事前刹车"。 |
| 3 | [`position_evaluator.py`](../sim/adapters/position_evaluator.py)（hard_stop，注释"防爆仓" L5；评估 L70-92） | 每 bar 触发 `intrabar_stop_loss_pct` 离场 | 假设"在止损价成交"（§1.3）。**停板锁死 / 跳空穿透时根本成交不了**，hard_stop 失效。它防的是正常波动，不是封板。 |
| 4 | [`margin_reconciler.py`](../live/margin_reconciler.py)（`AccountSnapshot` / `MarginEstimate`） | CTP 真实资金 vs 本地估算**事后对账** | 它在**差异发生后告警**，不是**开仓前的生存约束**。等对账发现 margin 漂移，仓位已经建好了。 |
| 5 | 全链路 `price × multiplier × lots` + `%止损` / `ATR%` | 名义、止损、波动度量 | **没有 price ≤ 0 / 异常跳变的健壮性闸**（§1.4）。负价 / 价格突跳会让 notional、止损、ranker score 全部 NaN 或符号反转，**风控自己先崩**。 |
| 6 | [`kill_switch.py:KillSwitch`](../live/kill_switch.py)（手动 / 信号文件） | 运维一键禁单 | 是**开关**，没有**自动判据**——没有"权益逼近强平线 / margin_usage 越线 / 单日跌幅超阈值就自动拉闸"的逻辑。靠人盯，黑天鹅来时人反应不过来。 |

**一句话**：现有体系把"敞口"管得很细，但把"**保证金存活**"和"**最坏情景下能不能活下来**"留成了空白。
本文档 §3 就是补这层空白。

---

## §3 必须新增的"生存层"动作（按优先级）

每条给出：**背景 / 触发 / 行为 / 关键参数 / 实现路径（复用现有资产）/ 边界**。
接口约定见 §4；不变量见 §5。

| § | 组件 | 类型 | 优先级 | 防的死法 |
|---|------|------|--------|----------|
| §3.1 | MarginSurvivalGuard（保证金生存约束） | Guard | **P0** | §1.1 强平 |
| §3.2 | LimitLockStressSizer（多日停板锁死生存测算） | Sizer | **P0** | §1.2 锁死 |
| §3.3 | GapThroughStopAssumption（跳空穿透最坏成交） | Sizer 修正 | **P0** | §1.3 跳空 |
| §3.4 | NegativePriceSafeguard（负价 / 异常价健壮性） | Guard + 全链路审计 | **P0** | §1.4 负价 |
| §3.5 | AccountSurvivalKillSwitch（账户级硬熔断） | Guard | **P0** | §1.1/1.2 兜底 |
| §3.6 | ExchangeRuleChangeMonitor（交易所规则突变） | Sizer + 监控 | P1 | §1.1/1.2 |
| §3.7 | DeliveryMonthZeroExposure（交割月归零） | Guard | P1 | §1.4 诱因 |
| §3.8 | BlackSwanCorrelationStress（相关性→1） | Sizer | P1 | §1.2 聚集 |
| §3.9 | OvernightGapBudget（隔夜 / 跨假期 gap 预算） | Sizer | P1 | §1.3 |
| §3.10 | BlackSwanStressEngine（黑天鹅情景引擎） | State + 报表 | P2 | 全部（统一测算） |
| §3.11 | CapitalLayering（资金分层 / 备付冗余） | 制度 | P2 | §1.1 兜底 |
| §3.12 | DataChannelFailSafe（通道 / 数据故障安全默认） | Guard | P2 | 系统性 |

---

### §3.1 MarginSurvivalGuard — 保证金生存约束（P0）

**背景**：`CapsConfig` 管 notional%，但强平看的是**保证金占用 vs 权益**。中国交易所保证金率
(8%~15%) + **期货公司加收**(通常 +2%~5%) + **交割月阶梯上调**，三者叠加才是真实占用。开仓前必须按
真实占用判断"这一单会不会把账户推向强平线"。

**触发**（任一 → 拒开仓）：
- 开仓后预计 `margin_usage = 占用保证金 / 权益 > max_account_margin_usage`（默认 **0.40**）；
- 开仓后预计 `可用资金 / 权益 < min_available_ratio`（默认 **0.50**，即永远留一半现金做强平缓冲）；
- 单 cluster 占用 > `max_cluster_margin_usage`（默认 0.15）。

**行为**：拒绝把占用推过线的开仓；平仓永远放行（让仓位有出口）。

**关键参数**：
```python
@dataclass(frozen=True)
class MarginSurvivalConfig:
    max_account_margin_usage: float = 0.40      # 账户保证金占用上限（强平缓冲 = 1 - 0.40）
    min_available_ratio: float = 0.50           # 可用资金 / 权益 下限
    max_cluster_margin_usage: float = 0.15
    exchange_margin_pct_by_cluster: dict[str, float] = ...   # 交易所基础保证金率
    broker_addon_pct: float = 0.03              # 期货公司加收（保守上调）
    delivery_month_margin_multiplier: float = 2.0           # 交割月保证金翻倍假设
```

**实现路径**：新建 `cta/risk/guards/margin_survival_guard.py`，继承 [`cta.live.risk._BaseRule`](../live/risk.py)；
真实占用走 [`margin_reconciler.py:AccountSnapshot`](../live/margin_reconciler.py)（实盘有 CTP 真值时用真值，
OOT/sim 用 `exchange_margin_pct × broker_addon` 估算）。

**边界**：实盘以 CTP `query_account` 的 `available` 为准（最权威）；OOT/sim 用保守上调的估算（宁可低估额度）。
与 notional cap **同时生效、取更严者**——notional 防"敞口太大"，本 Guard 防"被强平"。

---

### §3.2 LimitLockStressSizer — 多日停板锁死生存测算（P0）

**背景**：§1.2 的死法——开仓时就要算清"如果这笔被**连续 N 天停板反向锁死、期间一手都平不掉**，
账户还活不活得了"。这是把"止损打不出去"显式纳入 sizing 的核心一步。

**触发**：每次开仓 sizing 时强制测算：
```
worst_loss = entry_notional × limit_pct(cluster) × lock_days
if 权益 - 现有浮亏 - worst_loss  <  维持保证金 :
    → 砍仓到刚好通过，或拒单
```

**关键参数**：
```python
@dataclass(frozen=True)
class LimitLockStressConfig:
    lock_days_by_cluster: dict[str, int] = (        # 假设最坏连续锁死天数
        {"black": 3, "chemical": 3, "metal": 2, "agri": 2,
         "index": 1, "bond": 1, "precious": 2, "other": 3}
    )
    limit_pct_by_cluster: dict[str, float] = ...    # 复用 infer_symbol_limit_pct
    include_existing_positions: bool = True          # 测算时把已有持仓的最坏一起算（组合级）
    reject_if_unsurvivable: bool = True
```

**实现路径**：新建 `cta/risk/sizing/limit_lock_stress_sizer.py`（[`cta.risk.base.PositionScaler`](../risk/base.py) 子类）；
停板幅度复用 [`symbol_cluster_config.py:infer_symbol_limit_pct`](../config/symbol_cluster_config.py)；
组合级浮亏用 [`portfolio_state.py:PortfolioState`](../portfolio_logic/portfolio_state.py)。

**边界**：lock_days 是**保守上限**而非期望值（防爆仓不能用均值）。与 §3.1 互补：§3.1 管"开仓那一刻的占用"，
本条管"开仓后最坏 N 天的累积浮亏"。

---

### §3.3 GapThroughStopAssumption — 跳空穿透止损的最坏成交假设（P0）

**背景**：§1.3——回测 hard_stop 假设在止损价成交，实盘跳空 / 停板打开会滑很远。**sizing 阶段就要用
"最坏成交价"反推单笔风险**，而不是用乐观的止损价。

**触发 / 行为**：计算单笔最大可能损失时，止损成交价不取 `intrabar_stop_loss_pct`，而取：
```
worst_exit = max( stop_loss_pct,  gap_atr_mult × ATR,  limit_pct )   # 取最坏
single_trade_risk = entry_notional × worst_exit
```
据此约束单笔 lots，使 `single_trade_risk ≤ max_single_trade_loss_pct × 权益`（默认 **1.5%**）。

**关键参数**：
```python
@dataclass(frozen=True)
class GapThroughStopConfig:
    gap_atr_mult: float = 3.0                  # 假设止损滑 3 倍 ATR
    max_single_trade_loss_pct: float = 0.015   # 单笔最坏损失占权益上限
    overnight_extra_gap_atr: float = 1.0       # 隔夜单额外 +1 ATR
```

**实现路径**：在 [`position_evaluator.py:_resolve_intrabar_stop_pct`](../sim/adapters/position_evaluator.py)（L33）
旁加一个"最坏成交"派生，喂给 sizing；**不改 hard_stop 的离场逻辑本身**（那是执行层），只改 **sizing 的风险假设**。

**边界**：这是"宁可仓位小一点也不赌止损能成交"的保守口径，会降低单笔仓位——是用收益换生存，符合 §0 铁律。

---

### §3.4 NegativePriceSafeguard — 负价 / 零价 / 异常价健壮性（P0）

**背景**：§1.4——量化系统隐含"price > 0"。一旦负价 / 零价 / 价格突跳，notional、%止损、ATR%、保证金、
ranker score **全部 NaN 或符号反转，风控自己先崩**，反而可能下出灾难性的反向 / 超量单。

**两层防护**：
1. **价格合理性闸（price sanity gate）**——开仓 / 持仓评估前校验当前价：
   - `price <= 0`、或 `|price / prev_close - 1| > sanity_jump_pct`（默认 0.30）、或 NaN/Inf
   - → **冻结该 symbol**（禁开新仓、持仓转人工 / 只允许保守平仓）+ loud 告警，**绝不**继续用异常价算单。
2. **止损改用绝对价差**——核心风险公式从"`price × (1 - x%)`"切换到"`entry_price - k × ATR`（价差制）"，
   ATR 也以**价差**而非百分比计算，从根上规避 price ≤ 0 时的符号反转。

**关键参数**：
```python
@dataclass(frozen=True)
class NegativePriceSafeguardConfig:
    allow_nonpositive_price: bool = False       # 默认：非正价一律冻结
    sanity_jump_pct: float = 0.30               # 单 bar 跳变超 30% 视为异常
    freeze_symbol_on_violation: bool = True
    use_absolute_price_stops: bool = True        # 止损 / ATR 用价差而非百分比
    block_open_on_violation: bool = True
    allow_conservative_close: bool = True        # 异常下仍允许人工 / 保守平仓
```

**实现路径**：新建 `cta/risk/guards/negative_price_safeguard.py`（[`_BaseRule`](../live/risk.py)）排在
`RiskGuard` 链**最前**；配合一次全链路审计（notional / 止损 / ATR / 保证金 / score 计算点）补 `price <= 0` 分支。

**边界**：中国商品目前无负价合约，但**外盘联动品种（原油 sc、低硫燃油、国际铜）必须按"可能负价"设防**；
这是低概率 / 高后果项，宁可误冻结也不可裸奔。

---

### §3.5 AccountSurvivalKillSwitch — 账户级硬生存熔断（P0）

**背景**：§2 gap 6——`kill_switch` 有开关无判据。生存层需要**自动拉闸**的兜底。

**触发**（任一 → 自动 `KillSwitch.activate`，停所有新开仓，只留平仓）：
- `权益 < hard_equity_floor_pct × 起始权益`（默认 0.80，即累计亏 20% 硬停）；
- `margin_usage > kill_margin_usage`（默认 0.60，逼近强平）；
- `单日跌幅 > max_daily_loss_pct`（默认 0.05）；
- §3.4 价格合理性闸在 `>= N` 个品种同时触发（系统性异常）。

**关键参数**：
```python
@dataclass(frozen=True)
class AccountSurvivalKillConfig:
    hard_equity_floor_pct: float = 0.80
    kill_margin_usage: float = 0.60
    max_daily_loss_pct: float = 0.05
    systemic_sanity_violation_count: int = 3
    auto_deactivate: bool = False        # 默认拉闸后需人工复核才恢复
```

**实现路径**：新建 `cta/risk/guards/account_survival_kill.py`，内部调
[`kill_switch.py:KillSwitch.activate(reason=...)`](../live/kill_switch.py)；与
[`soak_gate.py`](../live/soak_gate.py) 的 RollbackChecklist 对接。

**边界**：熔断**只停开仓不强平**（避免在最差时点被自己的熔断逼着割肉）；恢复默认需人工，杜绝"自动恢复→
再次触发"的反复抽打。这是与 §3.1/§3.2 **事前约束**互补的**事后兜底**。

---

### §3.6 ExchangeRuleChangeMonitor — 交易所规则突变应对（P1）

**背景**：交易所可**盘中临时**上调保证金、扩大涨跌停、限仓、强减。这些会瞬间改变 §3.1/§3.2 的所有参数。

**触发 / 行为**：每日 / 盘中拉取交易所保证金与涨跌停参数，与本地缓存对比；
- 保证金率上调 → 立即按新值重算 margin_usage，超线则降仓；
- 涨跌停扩大 → §3.2 的 `limit_pct` 同步放大、重做锁死测算；
- 出现"强减"公告 → 该品种进 §3.7 黑名单。

**实现路径**：新 state `cta/risk/state/exchange_rule_tracker.py`（缓存 + diff + 告警）；
参数源：交易所公告 / CTP 合约信息。

**边界**：节假日前、交割月前交易所例行上调保证金属于**可预测**事件，应提前在日历里预置（与 §3.7 联动）。

---

### §3.7 DeliveryMonthZeroExposure — 交割月物理交割暴露归零（P1）

**背景**：负油正是"**临近到期 + 仓储挤压 + 移仓踩踏**"的合力。普通量化账户**无交割资质**，
持仓进入交割月可能被**强制平仓 / 强制交割 / 巨额交割违约金**。

**触发 / 行为**：
- `days_to_expiry < delivery_freeze_days`（默认 **15**，比 `risk.md` §16.3 的 7 天更保守）→ 该合约禁开 + 限时清仓；
- 品种进入"临近交割高风险名单"（外盘联动 + 易挤仓品种）→ 提前更早冻结。

**实现路径**：扩展 `risk.md` §16.3 `RolloverFreezeRule` 框架，新增"**绝不进交割月**"硬约束 +
交割月品种黑名单；合约日历复用 [`contract_resolver.py`](../portfolio_logic/contract_resolver.py)。

**边界**：与 `risk.md` §16.3 不重复——那条管"换月禁交"（流动性/basis），本条管"**绝不进入交割月**"（交割/挤仓爆仓）。

---

### §3.8 BlackSwanCorrelationStress — 相关性→1 的集中度应对（P1）

**背景**：黑天鹅时"分散"是假象——黑色系、能化系内部相关性飙到 ~1，多笔同向 = 一笔巨单，
`risk.md` §17.1 的相关性 Guard 用的是**历史相关性**，黑天鹅下会严重低估。

**触发 / 行为**：sizing 的组合级最坏测算（§3.2）中，**假设所有持仓相关性 = 1（全同向不利）**，
据此校验组合级强平存活；不过 → 全局降仓。

**实现路径**：在 §3.10 stress 引擎里实现"corr=1"场景；与 `risk.md` §17.1
[`CrossClusterCorrelationGuard`](../config/symbol_cluster_config.py) 共用 cluster 分组。

**边界**：corr=1 是压力上界，不用于日常 sizing（会过度保守）；只用于**生存测算的最坏场景**。

---

### §3.9 OvernightGapBudget — 隔夜 / 跨假期 gap 风险预算（P1）

**背景**：`risk.md` §16.4 HolidayReducer 按节假日线性降仓，但没回答"**降完之后，最坏 gap 下账户还活不活**"。

**触发 / 行为**：隔夜 / 节前持仓前做一次"最坏 gap 存活测算"——
`overnight_gap = gap_atr_mult × ATR`（节假日 ×假期系数），代入组合强平测算，不过 → 强制减到通过。

**实现路径**：复用 §3.3 的 gap 假设 + §3.2 的存活测算；在 `risk.md` §16.4 HolidayReducer **之后**串联。

**边界**：与 §16.4 互补——§16.4 是"经验性线性降仓"，本条是"存活硬测算"，取更严者。

---

### §3.10 BlackSwanStressEngine — 黑天鹅情景引擎（每日事前跑，P2）

**背景**：上面 §3.1-§3.9 是分散的开仓约束；还需要一个**统一的每日事前 stress** 把它们汇总成
一句话结论："**当前组合在最坏情景下会不会被强平**"。这是机构 CTA 风控团队的标准动作。

**行为**：每日盘后 / 盘前，对**当前组合**施加一组场景，输出最坏权益与是否触强平线：

| 场景 | 冲击 |
|------|------|
| S1 单品种 3 连停板 | 最大持仓品种连续 3 天反向停板 |
| S2 全 cluster 同向停板 | 最大 cluster 内所有持仓同向停板 1 天 |
| S3 相关性=1 | 全组合同向不利 1 个停板（§3.8）|
| S4 负价冲击 | 外盘联动品种价格 → 0 / 负（§3.4）|
| S5 保证金翻倍 | 交易所盘中上调保证金 ×2（§3.6）|

每个场景输出 `worst_case_equity` 与 `survives = worst_case_equity > maintenance_margin`。
任一 `survives=False` → 告警 + 建议减仓清单，纳入每日风险表"生存"行。

**实现路径**：新子目录 `cta/risk/stress/`（scenario 定义 + runner + 报表）；
组合状态读 [`PortfolioState`](../portfolio_logic/portfolio_state.py)，停板幅度读 `infer_symbol_limit_pct`。

**边界**：stress 引擎**只读不下单**（输出建议 + 告警，由 §3.1-§3.5 的硬约束实际拦截），保持职责单一。

---

### §3.11 CapitalLayering — 资金分层 / 备付冗余（P2，制度）

**背景**：**不把全部资金放在交易账户**。极端行情下需要快速补保证金，账户里钱越多，单次黑天鹅吃掉的越多。

**做法**：交易账户只放 `trading_capital`（如总资金的 50-70%），其余作**场外备付**；
账户权益低于阈值时人工 / 半自动补充；账户上限封顶，盈利定期划转出来锁定。

**实现路径**：制度 + 运维 SOP，非代码强约束；可在每日风险表里展示"交易账户 / 总资金"比例。

**边界**：这是组织层风控，量化系统只负责**暴露指标**，不自动划转资金（资金划转必须人工）。

---

### §3.12 DataChannelFailSafe — 通道 / 数据故障安全默认（P2）

**背景**：拿不到账户 margin（CTP 断连）、行情中断、predictions 过期时，**默认动作必须是"不动 + 不开新仓"**，
而不是"用旧数据继续下单"——后者是隐蔽的爆仓诱因。

**触发 / 行为**：
- CTP `query_account` 失败 / 超时 → 禁所有新开仓（无法判断 margin 就不能开）；
- 行情心跳中断 > N 秒 → flat-and-freeze（不开新仓，持仓转保守）；
- 与 `risk.md` §18.2 PredictionStaleGuard 联动（数据过期禁开）。

**实现路径**：在 `cta/live/supervisor.py` / 心跳监控里挂安全默认；复用 §3.5 KillSwitch。

**边界**：与 `risk.md` §18.2 互补——那条管"模型分新鲜度"，本条管"账户 / 行情通道可用性"。

---

## §4 与现有代码的接口（复用，不重写）

| 新组件 | 接口 / 复用 | 现有路径 |
|--------|-------------|----------|
| 所有 Guard | 继承 `_BaseRule`，挂 `RiskGuard.rules` **链最前**（硬约束优先，承接 `risk.md` §21 不变量 6） | [`cta/live/risk.py`](../live/risk.py) |
| 所有 Sizer | 继承 `PositionScaler`，进 `RiskOrchestrator` sizing 链 | [`cta/risk/base.py`](../risk/base.py) / [`cta/risk/orchestrator.py`](../risk/orchestrator.py) |
| margin 真值 | `AccountSnapshot` / `MarginEstimate` | [`cta/live/margin_reconciler.py`](../live/margin_reconciler.py) |
| 自动熔断 | `KillSwitch.activate(reason=...)` | [`cta/live/kill_switch.py`](../live/kill_switch.py) |
| 停板幅度 / cluster | `infer_symbol_limit_pct` / `infer_symbol_cluster` | [`cta/config/symbol_cluster_config.py`](../config/symbol_cluster_config.py) |
| 交割 / 换月日历 | `RolloverFreezeRule` 框架 + contract_resolver | [`cta/portfolio_logic/contract_resolver.py`](../portfolio_logic/contract_resolver.py) |
| 组合持仓 / 浮亏 | `PortfolioState` | [`cta/portfolio_logic/portfolio_state.py`](../portfolio_logic/portfolio_state.py) |
| stress 引擎 | 新子目录（待建） | `cta/risk/stress/` |

> 关键：生存层组件**排在 alpha 层（quantile/bucket/dd）之前**——任何优化只能在"已确认生存"的前提下进行。

---

## §5 生存层不变量（铁律，不可被 alpha 层覆盖）

1. **生存优先于收益**：生存层硬约束（§3.1-§3.5）与任何 alpha / 优化规则冲突时，**永远以生存层为准**。
2. **price ≤ 0 必须 fail-safe**：任何价格相关公式遇非正价 / 异常跳变，**拒绝 + 冻结**，绝不产生反向 / 超量仓位。
3. **sizing 必须过最坏情景**：每笔开仓都要通过"N 日停板锁死 + gap 穿透 + corr=1"的组合存活测算，不过则砍仓 / 拒单。
4. **永远预留强平缓冲**：账户 `margin_usage` 有硬上限（默认 40%），可用资金永不低于权益的一半。
5. **平仓默认放行**：所有生存 Guard 默认 `block_close=False`，让持仓永远有出口（避免被自己的风控锁死）。
6. **故障即冻结**：margin / 行情 / 数据任一通道异常 → flat-and-freeze，禁开新仓。
7. **OOT = sim = live 三处一致**：生存层用同一份 cfg、同一套组件，离线评估 / 仿真 / 实盘行为必须一致（CLAUDE.local 铁律）。

---

## §6 防爆仓 KPI / 验收

| 指标 | 目标 | 含义 |
|------|------|------|
| `max_account_margin_usage` | ≤ 0.40 | 保证金占用永不越线 |
| `min_available_ratio` | ≥ 0.50 | 永远留一半现金缓冲 |
| `worst_case_equity_after_3day_limit_lock` | > 维持保证金 | §3.2 每日 stress 输出，必须为真 |
| `survival_buffer_pct`（权益距强平线） | 每日 ≥ 阈值 | 越接近 0 越危险 |
| `single_trade_worst_loss_pct` | ≤ 1.5% | §3.3 单笔最坏损失占权益 |
| `price_sanity_violation_handle_rate` | 100% | 异常价 100% 被冻结，无裸奔 |
| 负价场景单测覆盖 | 100% | notional / 止损 / ATR / margin / score 全链路有 price≤0 分支测试 |
| `daily_stress_survives_all_scenarios` | True | §3.10 五场景全部存活 |

**验收方式**：
- **单测**：构造负价 / 连续停板锁死 / margin 越线 / 故障断连场景，断言对应组件正确拦截（TDD 先行）。
- **OOT 对拍**：把生存层接入 OOT，确认极端历史段（2016 黑色、2020 化工）账户**不触强平线**。
- **sim soak**：仿真期每日跑 §3.10 stress 引擎，输出风险表"生存"行，连续 N 日全存活才放行实盘。

---

## §7 落地阶段（建议，本轮不实现）

| 阶段 | 范围 | 优先级 |
|------|------|--------|
| W1 | §3.1 MarginSurvivalGuard + §3.4 NegativePriceSafeguard + §3.5 AccountSurvivalKillSwitch | **P0** |
| W2 | §3.2 LimitLockStressSizer + §3.3 GapThroughStopAssumption | **P0** |
| W3 | §3.7 DeliveryMonthZeroExposure + §3.6 ExchangeRuleChangeMonitor | P1 |
| W4 | §3.8 相关性 stress + §3.9 隔夜 gap 预算 | P1 |
| W5 | §3.10 BlackSwanStressEngine + §3.12 DataChannelFailSafe + §3.11 资金分层制度 | P2 |

每阶段：先写测试（TDD）→ 实现 Guard/Sizer → 接 `RiskOrchestrator` / `RiskGuard` → OOT/sim 验证 → 更新 change_log。

---

## §8 关联文档
- [`risk.md`](risk.md)：风控系统骨架 + 结构化风控（本文档是其"生存层"补充，§21 不变量 6"硬约束优先"在此延伸）。
- [`20260602_risk_codex.md`](20260602_risk_codex.md)：单次 OOT 调参（降杠杆 / 压 hard_stop），与本文档"生存测算"互补。
- [`sim_plan.md`](sim_plan.md) / [`sim_live_integration_roadmap2.md`](sim_live_integration_roadmap2.md)：OOT=sim=live 一致性目标（§5 不变量 7）。
