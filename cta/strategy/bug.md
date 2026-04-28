> ⚠️ **历史草稿（2026-04-26 第三轮 code review 中间稿）**
>
> 本文档记录的 bug 编号（B2 / B16 / B17 / B19 / B20）已在
> `cta/report/change_log.md` 的 **2026-04-26 (二) 第三轮 R1–R10**、
> **2026-04-26 按 cta/bug.md code review 回归修复** 与
> **2026-04-27 (二) candidate_events 第二轮 code review C1–C10** 三批修复中处理。
> 当前主线代码与本文不再一一对应（行号已经漂移、ATR 口径已统一、equity 周期已校准等）。
>
> 仅保留作为历史轨迹，**不是 bug 跟踪源**。新一轮 code review 的产出请直接写
> `cta/report/change_log.md`，不再回填本文。

---

## 任务理解

Code review 范围：
- `cta/strategy/baseline_skill_suite.py`（含 `prepare_master_feature_frame`、`generate_candidate_opportunities`、`build_training_samples_from_trade_log`、`run_baseline_suite`）
- `cta/config/baseline_skill_suite_config.py`
- `cta/model/feature/training_feature_builder.py`
- `cta/model/trade_filter_model.py` / `regime_classifier_model.py` / `mfe_mae_model.py`
- `cta/model/model_pipeline.py`
- 对应 4 个测试文件
- 引用的 `cta/skills/data_backtest/event_driven_backtest.py`、`cta/strategy/skill_tight_range_breakout.py::prepare_strategy_frame`

测试现状：15/15 绿。

---

## 潜在 Bug 清单（按严重度排序）

### 🔴 P0 — 影响正确性 / 标签污染 / 模型行为

**B2. `generate_candidate_opportunities` ATR 归一化口径与 feature 不一致 → label/feature 信息泄漏**
- 文件：`cta/strategy/baseline_skill_suite.py:855` 与 `:902`
- `feature_atr14 = bar["atr14"]`（i 位 = signal bar）；
  `mfe_atr / mae_atr = mfe / entry_bar.atr14`（i+1 位 = entry bar）。
- 训练样本里特征用一个 ATR 做归一化对齐，但标签用另一个 ATR；signal-bar 看不到的 ATR 进入 label 后就构成轻微未来信息（虽然 ATR 演变缓慢，但 entry_bar 的 ATR 已含 entry bar 的 high/low/close）。
- 修法：`atr_v = _safe_float(bar.get("atr14", np.nan))`（与 feature 同源）。

**B16. `prepare_master_feature_frame` 写入 `out["volume"] = ...astype(float)` 后再传给 `compute_donchian` 等下游函数 — 若下游有 `bars.copy()` 就 OK；若没有，会原地改**
- 文件：`cta/strategy/baseline_skill_suite.py:148-225`
- 需要逐一确认（`compute_donchian / compute_atr_channel / detect_breakout_pullback / detect_tight_range / score_breakout`）是否 copy；理论上 `_validate_ohlcv` 后 `out = bars.copy()`，问题不大，但 `compute_donchian` 直接用 `bars` 时不一定 copy。
- 中等优先级：增加一行 `bars_copy = out.copy()` 再传入下游会更安全。

**B17. `run_baseline_suite` `equity_curve` 取 `out["equity_curve"] + initial_capital` 得到资金曲线，但这是 `len(bars)` 长的累计 PnL**，与 trade-level Sharpe 不一致
- 文件：`cta/strategy/baseline_skill_suite.py:984` + `_compute_metrics`
- `summarize_trades` 内部 `rets = eq.diff()` 把每根 bar 当一个收益周期；当 trade 跨 N 根 bar 持仓时，eq 在持仓段不变，diff 给 0，Sharpe 偏低。
- 实际 readme 已经声明 periods_per_year，但分子用 bar-level 的 mean/std 仍偏差大。属于 metric 选择问题，不算代码 bug，但对用户很 misleading。

**B19. `_build_synthetic_candidate` 中 `signal_code = arange(n) % len(signal_types)` 让标签与 signal_type 一一对应**
- 文件：`cta/model/model_pipeline.py:67-89`
- 按 signal_type split 时每个组的样本规模 = n/4，但因为是 `% len`，每组样本顺序是稀疏的；时间分割会出现某些 window 某个 signal_type 没有数据。可能导致 walk-forward 某些 window 抛空。
- 现已被空 split 的 `if train_df.empty: continue` 兜住，但合成数据应更贴近真实分布。

**B20. `_compute_atr14` 与 `_validate_ohlcv` 缺失校验**
- 文件：`cta/strategy/baseline_skill_suite.py:92-105`
- 如果 high/low/close 列存在但全是 NaN，结果全 NaN，后续 `score_breakout` 会触发除零（已被 `1e-9` 防御）。OK 但建议加 warning。

### 🟢 P3 — 测试覆盖盲区（不是 bug，但应补）
- T5：缺一个测试断言 `_build_walk_forward_windows` 在 train_end>valid_end 时不会无限循环或返回空。
- T9：缺一个测试断言 retro label 阈值与 generator 阈值一致（暴露 B5）。
