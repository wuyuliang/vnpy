# MA-cross 趋势过滤 + Regime-aware Short Filter 设计文档

> **状态**：v1 设计稿（2026-05-20）。OOT gate 层新增两条独立、默认关闭、按
> (cluster, interval) 灰度启用的过滤器，治本 2024 INDEX 牛市 short bias。
>
> **本文档定位**：黑盒规范（spec）+ 接口契约。所有代码改动按本文档落地，文档先行。
>
> **关联文档**：
> - [block_reason.md](./block_reason.md) — block_reason 全量 review
> - [portfolio_logic_design.md](./portfolio_logic_design.md) — 组合层设计
> - [oot_output.md](./oot_output.md) — OOT 评估产出 schema

---

## §0 修订记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1 | 2026-05-20 | 初稿。新增两个 gate 与对应 block_reason，默认 off |

---

## §1 概览与动机

### §1.1 2024 INDEX day short bias 复盘

2026-05-19 OOT 诊断（基于 2024-01-01 ~ 2025-12-31 walk-forward）发现：

| 指标 | 数值 | 备注 |
|---|---|---|
| INDEX day OOT 候选笔数 | 13 | 全部 short |
| 触发 hard_stop 比例 | 100% (13/13) | 1% 止损全被打穿 |
| 同期 IF/IH/IC/IM 指数 | +24% ~ +41% | 牛市，short 方向错 |
| 候选 side 分布（全簇 2024） | 69% short / 31% long | 策略 short bias |

诊断结论：

- **不是 stop_loss 过紧**：放宽到 P90 (3.25%) 后单笔最大亏损金额变大，
  v3 net_pnl -4808 < v1 net_pnl -4003，反而恶化
- **不是模型 trade_filter 失效**：trade_filter 是二分类校准，对牛市 short
  bias 无对抗能力
- **不是 regime_gate 失效**：现有 regime_gate 用 `pred_regime_label`（模型预测），
  2024 训练样本不足导致模型把牛市误判为 `range`，被 `allow_range_in_regime_gate=True`
  默认放行

根因是**策略层 short bias**：[baseline_setup_detection.py](../strategy/baseline_setup_detection.py)
在突破收口 / 高位反转等场景下天然偏 short，2024 单边上涨期没有对应的
"上涨势能确认"过滤，short 信号原样进入 OOT 评估。

### §1.2 为何不能只靠放宽 stop_loss

放宽 stop_loss 在 short 方向错的前提下只会让单笔亏损更大：

| 配置 | INDEX day 2024 net_pnl | 平均单笔亏损 | 持仓天数 |
|---|---|---|---|
| 全局 1% (v1) | -4003 | -77.0 | 1.5d |
| INDEX day 3.25% (v3) | -4808 | -123.3 | 13.8d |

止损只是**事后兜底**，方向治本必须在**事前**拦截。

### §1.3 设计目标

| # | 目标 | 实现 |
|---|---|---|
| G1 | 治本 — 在 short 方向错时事前拦截 | OOT gate 层加 ma_cross / regime_short_filter |
| G2 | 灰度 — 不能一次性影响所有 cluster/interval | dict `*_enabled_by_cluster_interval` 显式 opt-in |
| G3 | 默认 off — 现有 OOT 回测口径不变 | `use_*=False` + 空 dict |
| G4 | 与现有 gate 互补 — 不替换 regime_gate | regime_gate 用 pred_regime_label；本设计用真实 regime_label，并列存在 |
| G5 | OOT / sim / live 一致 — 三层共用 cfg | 两个 filter 函数纯函数式实现，不依赖运行时状态 |
| G6 | 不动训练样本分布 — 避免训练集漂移 | 只在 OOT 评估期拦截，candidate 生成保持原状 |

---

## §2 设计原则

### §2.1 OOT-only

本设计**只动 OOT 评估期**（[pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py)），
**不动**候选生成期（[baseline_setup_detection.py](../strategy/baseline_setup_detection.py)）。

理由：

- 候选生成期过滤会减少训练样本数（short bias 样本被剔除），影响 trade_filter
  二分类标签分布，等于"用未来信息影响训练"
- OOT-only 保证训练集与历史一致，过滤逻辑可独立 A/B 测试
- 如后续证明 OOT 拦截效果稳定，再考虑下沉到训练期（separate change）

### §2.2 默认 off + (cluster, interval) opt-in

两 gate 默认全关：

```python
use_ma_cross_gate: bool = False
ma_cross_enabled_by_cluster_interval: dict[str, bool] = {}

use_regime_short_filter: bool = False
regime_short_filter_enabled_by_cluster_interval: dict[str, bool] = {}
```

启用方式（仅 INDEX day）：

```python
OotEvaluationConfig(
    use_ma_cross_gate=True,
    ma_cross_enabled_by_cluster_interval={"index|day": True},
    use_regime_short_filter=True,
    regime_short_filter_enabled_by_cluster_interval={"index|day": True},
)
```

灰度策略见 [§8](#§8-灰度策略)。

### §2.3 真实 regime vs 预测 regime

| Gate | regime 来源 | 列名 | 优点 | 缺点 |
|---|---|---|---|---|
| 现有 `regime_gate` | 模型预测 | `pred_regime_label` | 与训练 pipeline 一致 | 训练样本不足时误判（如 2024 牛市判为 range） |
| 新 `regime_short_filter` | 真实 feature 列 | `regime_label` | 不依赖模型推理 | 启发式（[_infer_regime_label](../strategy/baseline_setup_detection.py)）|

两个 gate **并列存在、互补**，调用顺序见 [§3.6](#§36-与-trade_filter--regime_gate-串行顺序)。

### §2.4 OOT / sim / live 一致性

两 gate 函数 `apply_ma_cross_gate` / `apply_regime_short_filter` 是**纯函数式**
helper，输入 `df + cfg` → 输出 `(df, gate_by_legacy, model_block_reason)`，
不依赖运行时状态。OOT 评估、sim 回放、live 实盘都从同一 `OotEvaluationConfig`
实例读取，过滤逻辑天然同源。

---

## §3 MA-cross gate

### §3.1 数学定义

复用 [cta/feature/trend.py:152 ma_alignment](../feature/trend.py)：

```python
def ma_alignment(close: pd.Series, periods: list[int] | None = None) -> pd.Series:
    """返回 1=多头排列(短>中>长), -1=空头排列(短<中<长), 0=混合."""
    if periods is None:
        periods = [5, 10, 20, 60]
    # 按递增 sort 后逐对比较：mas[0] > mas[1] > ... > mas[-1] → bullish
```

默认 periods `[5, 10, 20, 60]` 已是 trend.py 缺省值。设计上不在本 gate 中
重新计算 MA，**优先读 candidate 行携带的 `ma_alignment` 列**；该列由
[feature/composite.py](../feature/composite.py) / day-level feature pipeline 生成。

### §3.2 输入数据

| 列名 | 来源 | 缺失处理 |
|---|---|---|
| `ma_alignment` | `cta/feature/trend.py:269` 写入 | gate 全 pass，warn 一次 |
| `side` | candidate 必备 | gate 全 pass，warn 一次 |
| `symbol`, `interval` | candidate 必备 | 用 `_cluster_series` / `_interval_series` 推断 |

不在 gate 内"现算 ma_alignment"——理由：

- 现算需要把整段历史 OHLC 重新拉过来，与 feature pipeline 重复劳动
- candidate 行已经携带了 entry_datetime 之前的所有 feature，列缺失意味着
  feature pipeline 没跑全，这是上游问题，gate 不应当默默修复
- 缺失时 gate fallback pass-all 并记录 warning，便于上游修复

### §3.3 过滤规则表

| `ma_alignment` | `side` | 行为 | block_reason |
|---|---|---|---|
| `1`（多头排列）| `long` | pass | — |
| `1`（多头排列）| `short` | **block** | `blocked_ma_cross_trend` |
| `-1`（空头排列）| `long` | **block** | `blocked_ma_cross_trend` |
| `-1`（空头排列）| `short` | pass | — |
| `0`（混合）| `long`/`short` | pass | — |
| NaN/missing | 任意 | pass | — |

仅在 `(cluster, interval) ∈ ma_cross_enabled_by_cluster_interval` 且 value=True
时启用；否则该行行为为 pass。

### §3.4 配置字段

放在 [cta/config/model_oot_eval_config.py](../config/model_oot_eval_config.py) 中：

```python
# MA-cross 趋势过滤（默认 off，按 (cluster, interval) 灰度启用）
use_ma_cross_gate: bool = False
ma_cross_fast_window: int = 5          # 预留：未来若需要在 gate 内现算用
ma_cross_slow_window: int = 20         # 预留：未来若需要在 gate 内现算用
# 默认读取 generic_ 前缀列，兼容旧表会自动回退到 ma_alignment
ma_cross_alignment_column: str = "generic_ma_alignment"
ma_cross_enabled_by_cluster_interval: dict[str, bool] = {}
```

`__post_init__` 校验：

- key 必须形如 `"cluster|interval"`（两段非空字符串）
- value 必须是 bool

### §3.5 emit block_reason

新增 canonical block_reason：`blocked_ma_cross_trend`，定义在
[cta/model/oot/block_reasons.py](../model/oot/block_reasons.py)：

```python
BR_BLOCKED_MA_CROSS_TREND = "blocked_ma_cross_trend"
```

同步更新 `BlockReason` Literal / `CANONICAL_BLOCK_REASONS` tuple / `__all__`。

### §3.6 与 trade_filter / regime_gate 串行顺序

[pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py) 中的串行顺序：

```
1. apply_trade_filter_gate          → blocked_trade_filter
2. regime_gate (pred_regime_label)  → blocked_regime_gate
3. apply_ma_cross_gate              → blocked_ma_cross_trend         ← 新增
4. apply_regime_short_filter        → blocked_regime_short_filter    ← 新增
5. mfe_mae_gate                     → blocked_mfe_mae_gate
6. stacking_gate                    → blocked_final_decision_gate
```

**前面的 reason 优先**：一行被打多重 reason 时，details.csv 看到的是最早被
触发的。这与 [block_reason.md §3](./block_reason.md) 的"触发顺序优先级"一致。

把新 gate 放在 trade_filter 之后、mfe_mae 之前的理由：

- trade_filter 是"模型基础筛"，应该最先；统计 blocked_trade_filter 时不希望
  被 ma_cross 覆盖
- mfe_mae / stacking 是"模型最终边际"，应该最后；ma_cross 是"方向硬约束"，
  逻辑上更"硬"，放前面减少 mfe_mae 阶段无谓计算

---

## §4 Regime-aware short filter

### §4.1 regime_label 来源

candidate 行的 `regime_label` 列来自
[cta/strategy/baseline_setup_detection.py:15 _infer_regime_label](../strategy/baseline_setup_detection.py)：

```python
def _infer_regime_label(row: pd.Series) -> str:
    raw = row.get("regime_label", "")
    ...
```

底层 regime 计算来自 [cta/feature/regime.py:39 compute_regime_features](../feature/regime.py)，
输出 6 个标签：

- `trend_up` / `trend_down`：方向性趋势
- `range`：横盘
- `compression`：收敛波动
- `expansion`：放量
- `transition`：切换中

### §4.2 为何不用 pred_regime_label

现有 `regime_gate`（[oot_gates.py:49-58](../model/oot/oot_gates.py)，并 inline
在 [pipeline_oot_evaluation.py:139-143](../model/oot/pipeline_oot_evaluation.py)）
用 `pred_regime_label`（模型预测）。问题：

- 2024 INDEX 牛市 train_end=2023-12-31，模型没见过 2024 9.24 暴涨级别的样本
- 模型把 IF 2024-09 急涨判为 `range`（confidence 不足、震荡放大）
- `allow_range_in_regime_gate=True` 默认放行 range，short 单全部通过

解决：本 filter 用**真实 regime_label**（candidate 当时计算的，不依赖模型），
直接判 trend_up 拦 short。

### §4.3 过滤规则

| `regime_label` | `side` | 行为 | block_reason |
|---|---|---|---|
| `trend_up` | `short` | **block** | `blocked_regime_short_filter` |
| `trend_up` | `long` | pass | — |
| `trend_down` | `long`/`short` | pass | （不在 block_labels 里）|
| `range`/`compression`/`expansion`/`transition` | 任意 | pass | — |
| 空/NaN/未知 | 任意 | pass | — |

仅在 `(cluster, interval) ∈ regime_short_filter_enabled_by_cluster_interval`
且 value=True 时启用；否则该行行为为 pass。

`regime_short_block_labels` 默认 `("trend_up",)`，可扩展为
`("trend_up", "expansion")` 等。

### §4.4 配置字段

```python
# Regime-aware short filter（默认 off）
use_regime_short_filter: bool = False
regime_short_filter_label_column: str = "regime_label"
regime_short_block_labels: tuple[str, ...] = ("trend_up",)
regime_short_filter_enabled_by_cluster_interval: dict[str, bool] = {}
```

`__post_init__` 校验同 §3.4：key 必须形如 `"cluster|interval"`，value bool。

### §4.5 emit block_reason

新增 canonical block_reason：`blocked_regime_short_filter`：

```python
BR_BLOCKED_REGIME_SHORT_FILTER = "blocked_regime_short_filter"
```

### §4.6 与 §3 的关系

`apply_ma_cross_gate` 和 `apply_regime_short_filter` **独立、可叠加**：

- 同时开 → 任一拦截即拦截，block_reason 按调用顺序优先（ma_cross 先于 regime_short_filter）
- 只开其一 → 不影响另一
- 都不开 → 等价于默认 off

两者覆盖的场景**互补**：

| 场景 | ma_cross 拦 | regime_short_filter 拦 |
|---|---|---|
| 牛市初期（均线刚多头排列，regime 仍 transition） | ✓ | ✗ |
| 牛市中段（均线多头 + regime=trend_up） | ✓ | ✓ |
| 区间震荡转 down（短线下穿长线、regime=range）| ✓（拦 long）| ✗ |
| 牛市末期 high volatility（均线尚多头、regime=expansion）| ✓ | 取决于 block_labels |

---

## §5 接口契约

### §5.1 helper 函数签名

仿 `apply_trade_filter_gate` 风格：

```python
def apply_ma_cross_gate(
    df: pd.DataFrame,
    *,
    cfg: Any,
    gate_by_legacy: pd.Series,
    model_block_reason: pd.Series,
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """按 ma_alignment 判定方向，拦截与趋势相反的 side。"""


def apply_regime_short_filter(
    df: pd.DataFrame,
    *,
    cfg: Any,
    gate_by_legacy: pd.Series,
    model_block_reason: pd.Series,
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """按真实 regime_label 在 trend_up / 用户指定标签下拦截 short。"""
```

两函数都做：

1. 检查 `cfg.use_*_gate=True` 且 `*_enabled_by_cluster_interval` 非空
2. 任一条件不满足 → 直接返回原值（no-op）
3. 计算 `cluster|interval` key，过滤出"启用"行
4. 应用规则表（§3.3 / §4.3），更新 `gate_by_legacy` 和 `model_block_reason`
5. 缺列时 fallback pass-all + warn

### §5.2 输入 DataFrame 必备列

| gate | 必备 | 可选 |
|---|---|---|
| ma_cross | `side`, `symbol`, `interval` | `ma_alignment` |
| regime_short_filter | `side`, `symbol`, `interval` | `regime_label` |

可选列缺失时 gate fallback pass-all，不抛错。

### §5.3 输出列变化

不引入新列。`gate_by_legacy` 和 `model_block_reason` 由调用方维护，本 helper
原地更新。

---

## §6 配置示例（建议初始灰度）

### §6.1 INDEX day（已知 short bias 重灾区）

```python
from cta.config.model_oot_eval_config import OotEvaluationConfig

cfg = OotEvaluationConfig(
    use_ma_cross_gate=True,
    ma_cross_enabled_by_cluster_interval={"index|day": True},
    intrabar_stop_loss_pct_by_cluster_interval={"index|day": 0.0325},
    use_regime_short_filter=True,
    regime_short_filter_enabled_by_cluster_interval={"index|day": True},
    regime_short_block_labels=("trend_up",),
)
```

实际启用时还有一层保护：只有 `interval == "day"` 且该
`cluster|day` 的有效 `intrabar_stop_loss_pct` 大于 1% 时，MA-cross gate
和 regime short filter 才会生效。分钟级信号或仍使用默认 1% 止损的组合会
自动降级为 pass-all，避免把短周期噪音或快进快出的止损口径混进趋势过滤。

### §6.2 后续滚动启用清单

每月评估一次，按"误杀率 + net_pnl 改善"决定是否扩 cluster：

| 优先级 | cluster | interval | 启用条件 |
|---|---|---|---|
| P1 | index | day | 2024 牛市 13/13 hard_stop（已知重灾） |
| P2 | precious | day | AU/AG 2024 黄金牛市同样 short bias 风险 |
| P3 | black | day | RB/HC 2024 H2 上涨期 |
| P4 | metal | day | CU/AL 趋势性较强 |
| 不建议 | bond | day | 利率债趋势弱，过滤可能误杀 mean-reversion 信号 |
| 不建议 | * | 30min/60min | 分钟级 ma_alignment 噪音大，未实测 |

`regime_short_block_labels` 需要按 cluster 灰度调参：PRECIOUS 牛市通常先用
`("trend_up",)`；BLACK 若处在高波动上涨段，可在复盘后扩展为
`("trend_up", "expansion")`。当前 `OotEvaluationConfig` 是全局 tuple，若要
同时跑多 cluster 且标签集合不同，建议拆成 cluster 专属配置分别评估。

---

## §7 测试清单

### §7.1 单测（必跑）

`cta/config/tests/test_ma_cross_gate.py`：

| # | 测试名 | 验证点 |
|---|---|---|
| 1 | test_default_disabled | `use_ma_cross_gate=False` 时所有行 pass |
| 2 | test_blocks_short_in_uptrend | alignment=1 + side=short → blocked |
| 3 | test_blocks_long_in_downtrend | alignment=-1 + side=long → blocked |
| 4 | test_allows_both_in_mixed_regime | alignment=0 → 全 pass |
| 5 | test_only_applies_to_enabled_clusters | 启用 index\|day，bond\|day 不受影响 |
| 6 | test_missing_alignment_column_passes_all | graceful degrade |
| 7 | test_post_init_rejects_bad_keys | `"foo_bar"`（无 \|）/ `"foo\|day\|extra"` raise |

`cta/config/tests/test_regime_short_filter.py`：

| # | 测试名 | 验证点 |
|---|---|---|
| 1 | test_default_disabled | `use_regime_short_filter=False` 时全 pass |
| 2 | test_blocks_short_in_trend_up | regime_label=trend_up + side=short → blocked |
| 3 | test_allows_long_in_trend_up | regime_label=trend_up + side=long → pass |
| 4 | test_respects_block_labels_tuple | block_labels=("trend_up","expansion") |
| 5 | test_only_applies_to_enabled_clusters | 同 ma_cross #5 |
| 6 | test_missing_label_column_passes_all | graceful degrade |

### §7.2 集成测试

| 测试 | 命令 |
|---|---|
| canonical block_reason 集合 | `pytest cta/run/tests/test_all_emitted_block_reasons_are_canonical.py -q` |
| OOT pipeline 不退化 | `pytest cta/model/tests/test_pipeline_oot_evaluation.py -q` |
| stop_loss override 不受影响 | `pytest cta/config/tests/test_intrabar_stop_loss_override.py -q` |
| docs sync | `pytest cta/run/tests/test_docs_sync.py -q` |

### §7.3 端到端 A/B（在 /tmp，不污染主 report）

```bash
# v0: 全 off（baseline）
python -m cta.run.runner --cluster index --interval day \
  --start 2015-01-01 --end 2025-12-31 \
  --train-end 2023-12-31 --valid-end 2024-06-30 \
  --output-dir /tmp/ma_cross_v0

# v1: 只开 ma_cross
python -m cta.run.runner --cluster index --interval day \
  --start 2015-01-01 --end 2025-12-31 \
  --train-end 2023-12-31 --valid-end 2024-06-30 \
  --override use_ma_cross_gate=True \
    ma_cross_enabled_by_cluster_interval='{"index|day":true}' \
  --output-dir /tmp/ma_cross_v1

# v2: ma_cross + regime_short_filter
python -m cta.run.runner ... \
  --override use_ma_cross_gate=True use_regime_short_filter=True \
    ma_cross_enabled_by_cluster_interval='{"index|day":true}' \
    regime_short_filter_enabled_by_cluster_interval='{"index|day":true}' \
  --output-dir /tmp/ma_cross_v2
```

### §7.4 验收指标

| 指标 | 期望 |
|---|---|
| 2024-2025 short 笔数 | 显著下降（>50%） |
| blocked_ma_cross_trend 计数 | 非零，且与 alignment×side 矩阵一致 |
| blocked_regime_short_filter 计数 | 非零，集中在 2024 牛市段 |
| long 笔数 | 不被误伤（仅 alignment=-1 时被拦） |
| INDEX day 2024 net_pnl | 不恶化（最好转正） |
| 整体 trade_count | 下降但合理（不至于归零） |

---

## §8 灰度策略

### §8.1 第一周：INDEX day 只开 MA-cross

```python
use_ma_cross_gate=True
ma_cross_enabled_by_cluster_interval={"index|day": True}
```

观察：blocked_ma_cross_trend 数量、INDEX day 2024 短头笔数下降幅度。

### §8.2 第二周：INDEX day 加 regime_short_filter

```python
use_ma_cross_gate=True
use_regime_short_filter=True
ma_cross_enabled_by_cluster_interval={"index|day": True}
regime_short_filter_enabled_by_cluster_interval={"index|day": True}
```

观察：两 reason 的覆盖重合度（应有但不完全重合）。

### §8.3 第三周：PRECIOUS / BLACK day 滚动启用

按 §6.2 优先级表，每次只加一个 cluster，观察对照组 net_pnl 漂移。

### §8.4 退出条件

任一 cluster 启用后：

- 若 net_pnl **恶化 >10%** → 立即回退该簇（从 dict 删 key）
- 若 long 笔数 **被误伤 >20%**（alignment=-1 误判过多）→ 回退该簇并检查
  ma_alignment 列质量
- 若 trade_count **下降 >70%** → 回退该簇，gate 过严

---

## §9 已知限制

### §9.1 ma_alignment warm-up

`ma_alignment` 用 `[5, 10, 20, 60]` 默认 periods，最长 SMA(60) warm-up 60 bars。
新合约 / 历史数据短的 symbol 前 60 bar 该列为 NaN，gate fallback pass-all。

不影响 OOT 评估：OOT split 一般在 train_end 之后（≥3 年历史），不会落入
warm-up 区间。

### §9.2 regime_label 在 candidate 行可能为空

历史 candidate 在 feature pipeline 改造前可能没写 `regime_label` 列；
此时本 gate fallback pass-all + warn。

修复：上游 [baseline_candidate_gen.py:340](../strategy/baseline_candidate_gen.py)
已经写入 regime_label，新跑的 candidate 不应缺列。

### §9.3 分钟级未验证

day 级别 ma_alignment 的信号-噪音比可以接受，但 30min / 60min 上
`[5, 10, 20, 60]` SMA 的多/空头排列会高频翻转，可能造成过度拦截。

§6.2 配置建议表明确**不建议**在分钟级启用本 gate，等后续在分钟级单独
验证 ma_cross_fast_window / ma_cross_slow_window 调参后再考虑。

### §9.4 训练集分布漂移风险

本 gate **不**改训练样本，所以训练分布不变。但**长期看**，OOT 阶段拦截后
模型 trade_filter 在二分类目标上的 recall 会被人为限制：

- short 候选在牛市被全拦后，模型不会"知道"这是 bad short
- 下一轮 train 时模型仍会在牛市 short 上输出高 prob

不影响短期效果，但**长期需要把过滤逻辑下沉到训练标签生成**（separate
change），形成"拒单的 short 也标 negative"的标签反馈。

### §9.5 与 portfolio_logic 的关系

本设计**不动** [cta/portfolio_logic/](../portfolio_logic/)。`HtfGate` 仍然
按现有逻辑工作（基于 `htf_intervals` 的多 timeframe consensus）。

如果未来 `HtfGate._regime_to_side_set` 与本设计的 `regime_short_block_labels`
出现语义冲突，以本设计为准（本设计直接读真实 regime_label，更准确）。

---

## §10 file:line 索引

| 关键代码 | 位置 |
|---|---|
| MA 原语 | [cta/feature/trend.py:152 ma_alignment](../feature/trend.py) |
| Regime 特征 | [cta/feature/regime.py:39 compute_regime_features](../feature/regime.py) |
| Candidate 行 regime_label 写入 | [cta/strategy/baseline_setup_detection.py:15 _infer_regime_label](../strategy/baseline_setup_detection.py) |
| OOT gate 模块 | [cta/model/oot/oot_gates.py](../model/oot/oot_gates.py) |
| OOT pipeline 入口 | [cta/model/oot/pipeline_oot_evaluation.py:139-143](../model/oot/pipeline_oot_evaluation.py) |
| 配置类 | [cta/config/model_oot_eval_config.py](../config/model_oot_eval_config.py) |
| canonical block_reasons | [cta/model/oot/block_reasons.py](../model/oot/block_reasons.py) |
