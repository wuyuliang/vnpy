# Code & Doc Review v2（2026-05-16）

> 第二轮综合 review：覆盖 (a) cta/ 实际代码、(b) [portfolio_logic_design.md](portfolio_logic_design.md) v4、(c) [tushare_financial_data_integration.md](tushare_financial_data_integration.md)、(d) [review_claude_20260516.md](review_claude_20260516.md) v1 补遗。
>
> 严重程度：
> - **C (Critical)**：拷过去直接报错或行为错。**必须修**。
> - **H (High)**：实现层会被迫做大量猜测，或留下安全隐患
> - **M (Medium)**：边界 / 鲁棒性 / 容错。建议在 W1-W3 实施时修
> - **L (Low)**：表述、命名、注释；可批量打扫

合计 **42 项**（C:7 / H:11 / M:14 / L:10）。

---

# Part A: portfolio_logic_design.md v4 自检（v4 编辑引入的新问题）

## C-A-1. §4.5.4 `has_open_or_picked` 是错误的 Python 语法

**位置**：[portfolio_logic_design.md:232-237](portfolio_logic_design.md#L232)

```python
def has_open_or_picked(self, sym_key, direction) -> bool:
    if (sym_key, direction) in self._tentative_picked_sym_dir if self._tentative_active else False:
        return True
    return (sym_key, direction) in {(s, d) for (s, _), d in
            [(k, k[1]) for k in self.positions_by_sym_dir.keys()]}
```

两个问题：
1. **Line 234** 的条件表达式优先级歧义。Python 解析为 `(sym_key, direction) in (self._tentative_picked_sym_dir if self._tentative_active else False)`——`False` 不能作 `in` 的右操作数 → `TypeError`
2. **Line 236-237** 把 `positions_by_sym_dir.keys()`（其结构本就是 `((sym, exch), direction)`）走一遍 namedtuple 解包再重组，是 no-op；可直接 `in self.positions_by_sym_dir`

**修复**：
```python
def has_open_or_picked(self, sym_key, direction) -> bool:
    if self._tentative_active and (sym_key, direction) in self._tentative_picked_sym_dir:
        return True
    return (sym_key, direction) in self.positions_by_sym_dir
```

## C-A-2. §5 主循环 `cfg.caps` / `cfg.pyramid` vs `cfg.portfolio_logic.caps` 命名不一致

**位置**：[portfolio_logic_design.md:323-360](portfolio_logic_design.md#L323)

主循环写 `cfg.caps`、`cfg.pyramid`、`cfg.trailing`，但 §12 配置整合里这些是 `PortfolioLogicConfig` 的字段，外层包在 `OotEvaluationConfig.portfolio_logic`。

调用方 cfg 是 `OotEvaluationConfig`，正确路径应是 `cfg.portfolio_logic.caps`。

**修复**：§5 主循环全部改为 `cfg.portfolio_logic.caps` 等，或在主循环开头加 `plc = cfg.portfolio_logic`，后续用 `plc.caps`、`plc.pyramid`。

## C-A-3. §5 与 §9.5 之间循环引用

**位置**：[portfolio_logic_design.md:980-996](portfolio_logic_design.md#L980)

- §5 注释写"详见 §9.5"
- §9.5 又写"详见 §5"

两节互相 reference，新读者无法定位真正定义。

**修复**：§9.5 保留为 "B 阶段 layer 退出的细节展开"（不是完整主循环）；§5 是唯一主循环源。修订 §9.5 开头："本节展开 §5 B 阶段的 layer 粒度退出细节，主循环代码请直接参考 §5"。

## C-A-4. §5 主循环使用了未定义的 `caps_for(...)` 辅助函数残留

**位置**：[portfolio_logic_design.md:984](portfolio_logic_design.md#L984) 与某些残留 v3 段落

`caps_for(throttle_level)` 是 v3 草稿用的占位符；v4 已经改为 `risk_throttle.apply_to_caps(...)` + `apply_to_pyramid(...)`，但 §9.5 老段落里仍有 `caps_for(throttle_level)`。

**修复**：grep `caps_for` 全部替换。

## C-A-5. §9.2 引用了未定义的 `interval_to_minutes()` / `compute_profit_atr()` / `htf_aligned_with()`

**位置**：[portfolio_logic_design.md:887, 895, 900](portfolio_logic_design.md#L887)

3 个 helper 在 §9.2 出现但全文未定义。

**修复**：在 §9.2 前补一节"§9.0 辅助函数"：

```python
INTERVAL_MINUTES = {"day": 240, "60min": 60, "30min": 30, "15min": 15, "5min": 5, "min": 1}
def interval_to_minutes(interval: str) -> int:
    return INTERVAL_MINUTES.get(interval, 30)  # default 30min

def compute_profit_atr(pos: PyramidPosition, current_price: float) -> float:
    """以加权平均成本为基准，atr 用第一个 layer 的（保守）。"""
    if not pos.active_layers: return 0.0
    avg_entry = pos.avg_entry_price
    atr_pct_ref = pos.active_layers[0].atr_pct_at_entry
    atr_abs = atr_pct_ref * avg_entry
    if atr_abs <= 0: return 0.0
    if pos.direction == "long":
        return (current_price - avg_entry) / atr_abs
    return (avg_entry - current_price) / atr_abs

def htf_aligned_with(direction: str, gate_entry: dict | None) -> bool:
    """gate_entry = {'state': 'long_only'|'short_only'|'both'|'none', 'computed_at': ts}"""
    if gate_entry is None: return False
    s = gate_entry.get("state")
    if s == "both": return True
    if direction == "long" and s == "long_only": return True
    if direction == "short" and s == "short_only": return True
    return False
```

## C-A-6. §21 PortfolioState.to_dict 引用了未定义的 `PyramidPosition.to_dict`

**位置**：[portfolio_logic_design.md:1770](portfolio_logic_design.md#L1770)

```python
"positions": {pid: pos.to_dict() for pid, pos in self.positions.items()},
```

`PyramidPosition.to_dict()` 在 §9.1 没定义。Layer 也没有。

**修复**：在 §9.1 末尾补：
```python
# PyramidPosition
def to_dict(self) -> dict:
    return {
        "pos_id": self.pos_id, "symbol": self.symbol, "exchange": self.exchange,
        "direction": self.direction,
        "layers": [l.to_dict() for l in self.layers],
        "running_high": self.running_high, "running_low": self.running_low,
        "planned_exit_bar_idx": self.planned_exit_bar_idx,
        "extensions_used": self.extensions_used,
        "base_interval_minutes": self.base_interval_minutes,
    }

@classmethod
def from_dict(cls, d: dict) -> "PyramidPosition":
    pos = cls(...)
    pos.layers = [Layer.from_dict(ld) for ld in d["layers"]]
    return pos

# Layer
def to_dict(self) -> dict: ...
@classmethod
def from_dict(cls, d) -> "Layer": ...
```

## C-A-7. §10.2.4 `transform` 函数体写 `...` 占位，没有给出完整实现

**位置**：[portfolio_logic_design.md:1238](portfolio_logic_design.md#L1238)

```python
def transform(self, df: pd.DataFrame) -> pd.DataFrame:
    if "cluster" not in df.columns:
        df = df.copy()
        df["cluster"] = df["symbol"].apply(infer_symbol_cluster)
    # 逐行映射，避免在长 df 上 .apply 慢的话改 vectorized 实现
    ...
```

codex 看到 `...` 不知道要不要写 `apply` 逐行还是 vectorized。

**修复**：给出最小可执行实现：
```python
def transform(self, df: pd.DataFrame) -> pd.DataFrame:
    if "cluster" not in df.columns:
        df = df.copy()
        df["cluster"] = df["symbol"].apply(infer_symbol_cluster)
    out = df.copy()
    # trade_filter
    if "trade_filter_prob" in out.columns:
        out["trade_filter_prob_pctl"] = out.apply(
            lambda r: self.to_percentile(r["cluster"], r["interval"], "trade_filter", r["trade_filter_prob"]),
            axis=1,
        )
    # final_decision_stack
    if "final_decision_score" in out.columns:
        out["final_decision_score_pctl"] = out.apply(
            lambda r: self.to_percentile(r["cluster"], r["interval"], "final_decision_stack", r["final_decision_score"]),
            axis=1,
        )
    # mfe_mae 用 z-score
    if {"pred_mfe_atr", "pred_mae_atr"}.issubset(out.columns):
        out["raw_edge"] = out["pred_mfe_atr"] - 0.7 * out["pred_mae_atr"]
        out["edge_z"] = out.apply(
            lambda r: self.to_edge_z(r["cluster"], r["interval"], r["raw_edge"]),
            axis=1,
        )
        out["edge_norm"] = (out["edge_z"] + 2.0) / 6.0
    return out
```

---

# Part B: cta/ 实际代码（来自 explore agent + 我的补充）

## H-B-1. `cluster_model_registry.py` 非 cluster 分组的 fallback 是 silent skip

**位置**：[cluster_model_registry.py:191-192](cta/model/cluster_model_registry.py:191)

文档说"按 symbol → cluster → model_dir 路由"，但实际在 `group_by != "cluster"`（如 `group_by="tier"`）时返回空字符串，导致全部 NaN 但**没有 logger.warning**。

**修复**：
```python
def resolve_group(self, symbol: str) -> str | None:
    if self.group_by != "cluster":
        logger.warning(
            f"ClusterModelRegistry initialized with group_by={self.group_by}, "
            f"resolve_group for symbol={symbol} will return None"
        )
        return None
    # ... 现有逻辑
```

或者更激进：`from_registry_json` 在 `group_by != "cluster"` 时直接 raise，强制用户去掉 `--group-by tier` 路径（既然已经默认 cluster）。

## H-B-2. `cluster_model_registry.py` registry 缺失/空 entries 时 silent

**位置**：[cluster_model_registry.py:142-143](cta/model/cluster_model_registry.py:142)

JSON 解析成功但 `entries=[]` → 返回空 registry，所有 predict_proba 都跑出 NaN。生产环境会产生"一切静默失效"的灾难。

**修复**：
```python
entries = data.get("entries", [])
if not entries:
    logger.error(f"cluster_registry.json at {path} has empty entries — registry is unusable")
    # 不抛异常，但调用方能感知到 len(registry.entries)==0
```
ClusterModelRegistry 也可加 `__bool__` 与 `__len__` 让调用方 `if not registry: skip` 显式判断。

## H-B-3. intrabar_stop_loss_pct 与 label_stop_loss_pct 的一致性无运行时检查

**位置**：[model_oot_eval_config.py:76](cta/config/model_oot_eval_config.py:76) 注释 vs [baseline_skill_suite.py:973](cta/strategy/baseline_skill_suite.py:973)

注释说"必须与 baseline 一致"，但没有 assertion。已有 `test_stop_loss_pct_consistency.py` 但只在 pytest 时跑，**model_pipeline 启动时不强制校验**。

**修复**：在 `model_pipeline.main()` 入口加：
```python
def _validate_stop_loss_pct_consistency() -> None:
    from cta.config.model_oot_eval_config import OotEvaluationConfig
    from cta.strategy.baseline_skill_suite import generate_candidate_opportunities
    import inspect
    sig = inspect.signature(generate_candidate_opportunities)
    label_default = sig.parameters["label_stop_loss_pct"].default
    oot_default = OotEvaluationConfig().intrabar_stop_loss_pct
    if abs(label_default - oot_default) > 0.005:
        raise RuntimeError(
            f"intrabar_stop_loss_pct({oot_default}) ≠ label_stop_loss_pct({label_default})；"
            f"若有意改动请同步修改两处"
        )

# main 第一行调用
_validate_stop_loss_pct_consistency()
```

## H-B-4. `cluster_model_registry.predict_proba` 中 mfe_mae 路径 NaN 处理脆弱

**位置**：[cluster_model_registry.py:336-340](cta/model/cluster_model_registry.py:336)

```python
edge = pred_mfe_atr - 0.7 * pred_mae_atr
```

若 pred 返回全 NaN，edge 也是 NaN。后续 fill 进 DataFrame 仍然成功，**只在下游 ranker 用 edge_norm 时才会显现"全没机会"**。

**修复**：
```python
edge = pred_mfe_atr - 0.7 * pred_mae_atr
if np.all(np.isnan(edge)):
    logger.warning(f"mfe_mae returns all-NaN for {len(sub_df)} rows in cluster={group_name}")
```

## H-B-5. `symbol_disable.py` 异常捕获过宽

**位置**：[symbol_disable.py:51-54](cta/config/symbol_disable.py:51)

`except Exception` 把 pandas 解析错误、内存错误、键盘中断（除 KeyboardInterrupt 外）都吞了。manifest 格式错误时只返回空 set，操作员可能完全不知道 manifest 已损坏。

**修复**：
```python
try:
    df = pd.read_csv(manifest_path)
except FileNotFoundError:
    return set()                                         # fail-open
except pd.errors.ParserError as e:
    logger.error(f"symbol_disable_manifest.csv 解析失败: {e}；按 fail-closed 处理")
    raise                                                 # 不应静默
```

## H-B-6. test 缺少损坏 JSON / empty entries / schema 缺列的鲁棒性测试

**位置**：[test_cluster_model_registry.py](cta/model/tests/test_cluster_model_registry.py) + [test_symbol_disable.py](cta/config/tests/test_symbol_disable.py)

缺测试用例：
- `cluster_registry.json` 是 invalid JSON
- `cluster_registry.json` 缺 `entries` 字段
- `entries = []`
- 一个 cluster 的 model_dir 不存在（其它 cluster 应能正常路由）
- `symbol_disable_manifest.csv` 缺 `reason` 列

**修复**：补 6 个 unit test，模板：
```python
def test_from_registry_json_empty_entries(self):
    rj = Path(td) / "cluster_registry.json"
    rj.write_text('{"entries":[],"group_by":"cluster"}')
    reg = ClusterModelRegistry.from_registry_json(rj)
    self.assertEqual(len(reg), 0)
    # predict_proba 应返回 NaN，不抛异常
    df = pd.DataFrame({"symbol":["RB0"], "interval":["60min"], "signal_type":["x"]})
    out = reg.predict_proba(df)
    self.assertTrue(pd.isna(out["trade_filter_prob"]).all())
```

## M-B-1. `model_pipeline.py` 某 interval 完全 skip 时无 warning

**位置**：[model_pipeline.py:3409-3472](cta/model/model_pipeline.py:3409)

若某 interval 的所有 group 都失败（没有 entry），该 interval 的 registry 为空，但没有日志。运营无法快速定位"为什么 60min 模型在 OOT 全 NaN"。

**修复**：
```python
for interval in intervals:
    interval_records = collect_group_pool_for_interval(interval, ...)
    if not interval_records:
        logger.warning(f"interval={interval} 无任何 group 训练成功，registry 该 interval 为空")
    registry_records.extend(interval_records)
```

## M-B-2. tradefilter_model.load 的 estimator_tree fallback 风险

**位置**：[trade_filter_model.py:336-338](cta/model/trade_filter_model.py:336)

`out.estimator_tree = obj.get("estimator_tree", out.estimator)` 在加载旧 joblib（B7 fix 前）时把 estimator 当树用。若旧 joblib 中 estimator 实际是 linear 模型（而不是树），会被误判为树并影响 stacking。

**修复**：
```python
if "estimator_tree" not in obj:
    logger.warning(f"loaded legacy joblib at {path}, estimator_tree fallback may be inaccurate")
out.estimator_tree = obj.get("estimator_tree", out.estimator)
```

## M-B-3. `auto_flag_persistent_loss_symbols.py` 没记录 source run_tag

**位置**：[auto_flag_persistent_loss_symbols.py](cta/model/tools/auto_flag_persistent_loss_symbols.py)

输出 `symbol_disable_manifest.candidate.csv` 时 `source` 列填什么？审阅人无法追溯"这个候选品种是哪次 OOT 推荐 disable 的"。

**修复**：CLI 接受 `--source-tag` 参数，每行 `source` 列填 `oot_{run_tag}_{interval}`。

---

# Part C: tushare_financial_data_integration.md 检查

## C-C-1. §5.1 `IndexDownloader.fetch_all` 默认参数语法错

**位置**：[tushare_financial_data_integration.md:?](tushare_financial_data_integration.md)（§5.1 接口代码）

```python
def fetch_all(self, symbols: list[tuple[str, str]] | None = None, start: str = ..., end: str = ...) -> None:
```

Python 中 `=...`（Ellipsis）作为默认值在编辑器里能跑但 mypy 会报，且语义不清"必填还是选填"。

**修复**：明确给字符串默认值或改为必填：
```python
def fetch_all(self, symbols=None, *, start: str, end: str) -> None:
    # 强制 kwarg
```

## H-C-1. ranking csv 加 7 行后 `TOP_N=18` 默认不会带

**位置**：tushare 文档 §6 + [run.sh:49](cta/run.sh:49) `TOP_N="${TOP_N:-18}"`

文档加了 IF0..TS0 的 rank=71-77，但 run.sh 默认 TOP_N=18，**新加的金融期货完全不会被默认管道带进训练**。

**修复**：两种方案
- A: 把 IF0/IH0/IC0/T0 rank 改成 19-22（插队商品后），TOP_N 调到 25
- B: 在 run.sh 把 TOP_N 默认提到 77，并加注释"覆盖商品+金融"
- C: 加 `INCLUDE_FINANCIAL_PREFIXES` 环境变量，独立于 max_rank

推荐 B 最简单，但要审查现有商品训练资源够不够。

## H-C-2. macro_feature.py join 列名错配

**位置**：tushare 文档 §8.2

```python
candidates.merge(macro_df.reset_index().rename(columns={"trade_date": "candidate_trade_date"}), ...)
```

但 candidate_training_dataset.py 输出的不一定有 `candidate_trade_date` 列。实际列名是 `candidate_datetime` 或 `entry_datetime`。

**修复**：先在 candidate_training_dataset 加 `candidate_trade_date = entry_datetime.dt.normalize().dt.date.astype(str)`，再 merge。或在 macro_feature.py 内部做 normalize。

## M-C-1. 商品期货夜盘到 02:30 的细化未处理

**位置**：tushare 文档 §7.2

文中明确说"第一版用统一 23:00"，但当前 `validate.py` 在 cu/au/ag 这些品种（夜盘到 02:30）上会误报"凌晨多余 bar"。

**修复**：tushare 文档应明确 W3.2 之后必须再做一个"商品夜盘细分"task，或者第一版直接给出 3 档商品 session：
```python
COMMODITY_NIGHT_23 = {...}     # 大宗：rb/hc/...
COMMODITY_NIGHT_01 = {...}     # 化工：ru/ta/...
COMMODITY_NIGHT_0230 = {...}   # 贵金属：au/ag、有色：cu/al/zn
```

## M-C-2. INDEX 文件名 `.` → `_` 后下游 ID 匹配怎么做

**位置**：tushare 文档 §5.1 `ts_code_safe = ts_code.replace('.', '_')`

`000001_SH.csv` 落盘后，macro_feature.py 怎么 ID 它是上证指数？

**修复**：在 `cta/feature/index_reference_symbols.csv` 加 `file_safe_name` 列：
```csv
ts_code,file_safe_name,name,role
000001.SH,000001_SH,上证指数,macro_reference
```
macro_feature.py 用 file_safe_name 索引，避免重复转换。

## M-C-3. tushare API `fut_basic` / `index_daily` 限速档位未对齐

**位置**：tushare 文档 §4.3

`_tushare_utils.py` 抽出后，financial_futures 与 index 共享 RateLimiter，但 tushare 不同接口实际限速档不同（pro_bar vs ft_mins vs index_daily），同一 limiter 450/min 可能浪费配额。

**修复**：暂时接受（行为偏保守），但在 § "风险" 中记录"未来按接口分 limiter"。

---

# Part D: Cross-cutting（跨文档与代码）

## C-D-1. v4 §22 灰度 Day1-5 enable_score_calibration=True 但 enable_ranker=False 时 _pctl 列不被消费

**位置**：[portfolio_logic_design.md:1850](portfolio_logic_design.md#L1850)（§22 灰度阶段表）

如果 enable_ranker=False，candidate 表多出来的 `_pctl` 列没人读、白算。

**澄清**：在 §22 表后补一段："Day 1-5 验证目标是确保**校准管道本身不破坏现有流程**，决策仍走 baseline。`_pctl` 列产生但不消费——这是预期行为。"

## H-D-1. v4 §15.1 加了 test_oot_sim_parity / test_backward_compat 但 §19.1 文件清单没加

**位置**：[portfolio_logic_design.md:1640](portfolio_logic_design.md#L1640) §19.1 关键文件总览

§19.1 列了 9 个 test 文件，但 v4 新增的 2 个 (`test_oot_sim_parity.py`、`test_backward_compat.py`) 没列进去。

**修复**：§19.1 末尾补：
```
cta/portfolio_logic/tests/test_oot_sim_parity.py
cta/portfolio_logic/tests/test_backward_compat.py
```

并把 §18 验证清单中 "9 + 9" 改成 "9 核心 + 11 test"。

## H-D-2. v4 §4.5.5 调用约定表与 §5 主循环实际调用顺序不一致

**位置**：[portfolio_logic_design.md:280-294](portfolio_logic_design.md#L280) vs [portfolio_logic_design.md:309-358](portfolio_logic_design.md#L309)

§4.5.5 表说"主循环 D) 入场后 → state.add_position 或 record_add"，但 §5 代码块里是先 add_position 再 commit_allocation。

具体问题：commit_allocation 是在所有 picks 都 add_position 之后才调用一次。但 tentative_apply 实际改了 `_tentative_*`，而 add_position 不影响计数（因为 commit_allocation 还没调）。**期间 state.per_symbol_count 仍是旧值**——下个 ranker.allocate 调用前必须保证 commit 完成。

**修复**：把 §4.5.5 表里这一行改成：
| 主循环 D) 入场最后 | 一次性 | `state.commit_allocation()`（picks 全部处理完后；commit 之前 add_position 不影响主计数） |

并在 §4.5.2 commit_allocation 注释里强调："add_position / record_add 在 commit 之前调用是可以的，因为它们改 positions dict（对象引用）但不动 per_* 计数；commit 把 _tentative_* per_* 拷回主状态"。

## M-D-1. tushare 文档与 portfolio_logic 之间无交叉引用

两个 doc 各自独立。但 portfolio_logic 的 score_calibration 依赖 candidate_training_dataset 的输入（macro_feature 是新加的列）。如果 macro feature join 改了，calibration percentile_values 范围会变。

**修复**：portfolio_logic_design.md §20.2 "强制契约" 加一条："候选 DataFrame schema 改动（如加 macro_* 列）必须触发 calibration 重新 fit"。

## L-D-1. 两个文档的"v4 修 P0-X" / "v3 修 ..." 标注风格不一致

portfolio_logic_design 用 `v4 修 P0-1` 内嵌注释，可读；tushare 文档没有 review 引用机制。

**修复**：tushare 文档加一节"修订记录"对齐风格，方便未来 v2 review。

---

# Part E: review_claude_20260516.md v1 补遗（v1 漏的）

## H-E-1. v1 没指出"OOT 输出 trade_details.csv schema 变更兼容性"

新增 `layer_id`、`exit_reason ∈ {trailing_stop, hard_stop, horizon_exit}` 等列，下游消费 trade_details 的脚本（report / monitor）需要更新。

**待修**：scan `grep -rn "trade_details" cta/` 找出所有消费方，建一份"下游消费方升级清单"。

## H-E-2. v1 没列 "regime_classifier 训练数据来源是否已包含 trend_up/down/range 标签"

§6 假设 regime model 已经输出 `trend_up / trend_down / range`。但实际 [regime_classifier_model.py](cta/model/regime_classifier_model.py) 训练用的 label 是从哪里来的？labels 在 candidate dataset 怎么生成？

**待查**：grep `regime_label` 在 candidate_training_dataset.py 看 label 生成逻辑，确认与 §6 假设的"trend_up / trend_down / range"三类一致。

## M-E-1. v1 没指出 EquityTracker 初始 equity 没明确

新建 PortfolioState 时 `equity=...` 必须给值。OOT 是 `initial_capital`（在 `OotEvaluationConfig` 里），sim/live 是 broker 接口。

**待修**：§4.5.1 字段表里给 `equity: float` 注释 "初始值：OOT=cfg.initial_capital, sim/live=broker.get_account_equity()"。

## M-E-2. v1 没指出 mfe_mae 模型校准用的"raw_edge"列是新概念

§7.1 评分公式定义了 `raw_edge = pred_mfe_atr - 0.7 * pred_mae_atr`，但这个 raw_edge 在 candidate / OOT DataFrame 里是不存在的列。需要 ranker.score 计算时临时构造。

**待修**：明确"ranker.score 内部第一步先 `df["raw_edge"] = df["pred_mfe_atr"] - 0.7 * df["pred_mae_atr"]`"。

## L-E-1. v1 没指出 cluster_registry.json 的 schema 文档化

`from_registry_json` 解析的 JSON 结构没有 schema 文档。新人加字段时容易破坏 backward compat。

**待修**：在 [cluster_model_registry.py](cta/model/cluster_model_registry.py) docstring 里加完整 schema 例子。

---

# Part F: 文档质量（L 类批量打扫）

## L-F-1. portfolio_logic_design v4 中 "P0-X" / "P1-X" / "P2-X" / "P3-X" 标签命名混入正文，阅读时令人困惑

部分章节里"v4 修 P1-2 双 stop"这种内嵌注释很多。这适合 reviewer 看，但 codex / 新人会被噪声干扰。

**修复**：把全部 "v4 修 PX-Y" 改成 footnote 或 sidebar，或抽到统一的 §11 表里"按章节列 v4 标签"。但成本不小，可选。

## L-F-2. CFFEX vs 中金所 vs ZJSO 命名混用

tushare 文档说 exchange="CFFEX"，但 vnpy 主仓的 Exchange enum 可能用 "CFFEX" 或别的代码。

**待查**：grep `Exchange.CFFEX` 在 vnpy/trader/constant.py。

## L-F-3. portfolio_logic_design §6 表里 "trend_up / trend_down / range" 是规整字符串还是枚举？

§6.4 IntervalGateConfig 没定义 regime label 集合。如果 regime_classifier 实际输出 `0/1/2` 数字，gate 比较会失败。

**修复**：补 `RegimeLabel = Literal["trend_up", "trend_down", "range"]` 类型，并 assert 训练数据 label 在这个集合里。

## L-F-4. portfolio_logic_design §4.5.6 stateless 契约缺反例

"模块必须 stateless"是规则，但没说"以下情况是违反"。补一条反例：
> 错误示范：`HtfGate.__init__` 里存 `self._last_filter_result = None`，下次调用复用 → 状态泄漏到 instance，OOT vs sim 不可重现

## L-F-5. portfolio_logic v4 §22 灰度阶段表 Day 0 baseline 列与现有 OOT 字段对应不明

"baseline 行为字节级一致" 怎么 verify？

**修复**：在 §22 加一行 "verify by: test_backward_compat 比对 trade_details.csv 的所有字段（用 pandas.testing.assert_frame_equal）"。

## L-F-6. portfolio_logic v4 §11 编号到 55，但 §11.5 改正 #50-#55 实际只有 6 项（缺失内容）

55 - 49 = 6，对得上。但 §11.5 表头说 "(4 个新章节 + 2 个 test)"，是 6 项。OK，编号一致，**无问题**，仅注释含糊。

## L-F-7. portfolio_logic v4 §0 修订记录 v4 条目过长

5 个 bullet（P0/P1/P2/P3/缺失），每条都长。可压成"消化 review_claude_20260516.md 的全部 31 项 + 缺失 7 项，共 38 项修复，分布见 §11"。

## L-F-8. portfolio_logic v4 §23 高敏感参数表 "调整方向" 列只是"升高/降低 → 效果"，缺"推荐起点 + 调整步长"

例如 `atr_multiplier=4.0` 应该给"建议每次 ±0.5 调，先尝试 3.5 / 4.5；不要直接跳到 6.0"。

## L-F-9. tushare 文档 §10 实施顺序里没说 _tushare_utils.py 抽取 W1.1 的回归测试要求

`_tushare_utils` 把 commodity 的 splice 函数抽出来，必须保证 commodity 下载行为字节级不变。建议显式列回归 test。

## L-F-10. review_claude_20260516.md v1 与 v2 之间需要 cross-reference

v1 末尾应该加一句"v4 修订完后请运行 v2 review（本文件）"。

---

# 总结与建议执行顺序

## 立即修（C 级，7 项 — 都是 portfolio_logic_design.md v4 自检发现的）

按代码触发到执行顺序：
1. **C-A-1** `has_open_or_picked` 语法错（拷过去直接 TypeError）
2. **C-A-2** `cfg.caps` vs `cfg.portfolio_logic.caps` 命名
3. **C-A-3** §5/§9.5 循环引用
4. **C-A-4** 残留 `caps_for` 占位符
5. **C-A-5** `interval_to_minutes / compute_profit_atr / htf_aligned_with` 未定义
6. **C-A-6** `PyramidPosition.to_dict` 未定义
7. **C-A-7** `transform` 函数体 `...` 占位
8. **C-C-1** `IndexDownloader.fetch_all` 默认参数语法

## W1 之前（H 级，11 项）

代码层：H-B-1 ~ H-B-6（registry warning + stop_loss consistency + 测试覆盖）
文档层：H-C-1 (TOP_N 默认), H-C-2 (macro join), H-D-1 (test 文件清单), H-D-2 (调用顺序)
review 补遗：H-E-1, H-E-2

## 各 Pillar 实施时同步（M 级，14 项）

M-B-1 ~ M-B-3 / M-C-1 ~ M-C-3 / M-D-1 / M-E-1, M-E-2

## 一次性打扫（L 级，10 项）

L-D-1, L-E-1, L-F-1 ~ L-F-10

---

## 给 codex 的总检查清单

实施代码前，确保下列文档项已被 codex 修复或者 codex 自己实现时绕过：

- [ ] §4.5.4 has_open_or_picked 用正确 Python
- [ ] §5 主循环用 `cfg.portfolio_logic.caps`
- [ ] §9.0 补 3 个 helper（interval_to_minutes / compute_profit_atr / htf_aligned_with）
- [ ] §9.1 PyramidPosition / Layer 都加 to_dict / from_dict
- [ ] §10.2.4 transform 给出完整函数体
- [ ] model_pipeline.main 入口加 `_validate_stop_loss_pct_consistency()`
- [ ] ClusterModelRegistry 加 empty entries warning
- [ ] symbol_disable.py 区分 FileNotFoundError vs ParserError
- [ ] candidate_training_dataset 加 `candidate_trade_date` 列以便 macro merge
- [ ] run.sh `TOP_N` 默认值与 ranking 新行数对齐

每条都附原始 review 编号，便于回溯。

完文。
