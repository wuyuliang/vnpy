# cta/portfolio_logic/

## 主要做什么

**组合执行运行时**：HTF gate、opportunity ranker、trailing exit、pyramid manager、risk throttle、score calibrator、portfolio state。这是 OOT 评估和实盘共用的"在收到候选信号之后、下单之前"那一段决策与状态机。

完整设计参考 [cta/docs/portfolio_logic_design.md](../docs/portfolio_logic_design.md)。

## 关键文件

| 文件 | 作用 |
|---|---|
| [config.py](config.py) | `PortfolioLogicConfig` + 各子模块 sub-config（`IntervalGateConfig` / `OpportunityRankerConfig` / `TrailingConfig` / `PyramidConfig` / `RiskThrottleConfig` / `CapsConfig` / `ScoreCalibrationConfig`）；全部 `frozen=True` |
| [interval_gate.py](interval_gate.py) | `HtfGate`：跨周期共识，emit `htf_missing` / `htf_conflict` / `htf_opposite` / `htf_unknown` 四种 block_reason |
| [opportunity_ranker.py](opportunity_ranker.py) | `OpportunityRanker`：把候选按 (trade_filter_prob, pred_mfe_atr, htf_alignment, cluster) 等特征加权打分，过 `score_pctl_threshold` |
| [trailing_exit.py](trailing_exit.py) | `simulate_trailing_exit()`：基于 ATR 的逐 bar 移动止损模拟 |
| [pyramid_manager.py](pyramid_manager.py) | `PyramidManager.decide_add_layer()`：加仓三条件（layer 数 / cooldown / 最小浮盈 ATR） |
| [risk_throttle.py](risk_throttle.py) | `RiskThrottle` + `EquityTracker`：基于日/周/月回撤的 throttle level (normal / soft / hard / halt) |
| [score_calibrator.py](score_calibrator.py) | sigmoid / isotonic 校准 trade_filter_prob 到真实胜率 |
| [portfolio_state.py](portfolio_state.py) | `PortfolioState`：open positions / margin / open_notional / weekly+monthly peak / drawdown，是 OOT 单笔决策的状态总线 |

## 详细过程（典型 OOT 单 bar 流程）

```
1. risk_throttle.compute(equity_snap, prev_level)
     ├─ snap.drawdown_pct vs halt_*_pct → 决定本 bar 的 score 阈值或 halt
     └─ 若 halt → 本 bar 所有 pending entries 直接 block_reason="blocked_throttle_halt"

2. htf_gate.compute_htf_state(htf_predictions, as_of=ts)
     └─ htf_gate.filter(candidates, htf_state, current_time=ts)
         ├─ entry is None → "htf_missing"
         ├─ state="none" → "htf_conflict"
         ├─ state matches direction → ""（通过）
         └─ state不匹配 → "htf_opposite"

3. opportunity_ranker.score(candidates, htf_state)
     └─ 评分 < score_pctl_threshold → "ranker_dropped"

4. portfolio caps check
     ├─ blocked_total_concurrent / blocked_symbol_concurrent
     ├─ blocked_symbol_cap (含 cluster cap)
     ├─ blocked_weekly_budget / blocked_daily_position
     └─ blocked_margin_cash / blocked_leverage

5. pyramid_manager.decide_add_layer (若是加仓而非首仓)
     └─ False → "blocked_pyramid_rule"

6. position_sizing → notional > 0 → executed
   否则 → "zero_notional"
```

## 注意事项

- **所有 sub-config 都 `frozen=True`**：要在运行时改字段须用 `dataclasses.replace(...)`。直接赋值抛 `FrozenInstanceError`。
- **HTF 默认严格**：`fallback_when_htf_missing="skip"` + `require_consensus=True` 是默认。单 interval 跑批由 [pipeline_oot_evaluation_source.py.txt:601-625](../model/pipeline_oot_evaluation_source.py.txt) 的 Fix-A 自动窄化 `htf_intervals`；不要手动改 `fallback="both"` 除非确实是"偶发缺失"。详见 [block_reason.md](../docs/block_reason.md) §6。
- **trailing 方向不能搞反**：long 用 `max(trail_stop, new_stop)`，short 用 `min(...)`。历史上有过反向 bug，见 [review/202605170735.md](../docs/review/202605170735.md) §C1。
- **`enable_pyramid=True` 强依赖 `enable_trailing=True`**：[config.py:302](config.py) 已经在 `__post_init__` 校验。改 enable_* 默认值要重跑 config 测试。
- **`htf_unknown` 是 bug 兜底**：正常情况下 `_state_from_regimes` 只会返回 `{both, none, long_only, short_only}` 四种；走到 `htf_unknown` 说明 regime label 有 garbage，warning 必须保留。
- **OOT 与实盘共用**：本目录所有代码都不直接发单，OOT 与 live 必须用同一份决策代码。任何"实盘特别处理"应放到 [cta/live/](../live/)，不在这里搞分支。
- **测试**：跑 `pytest cta/portfolio_logic/tests/ -v`；`test_oot_sim_parity.py` 校验 OOT 与 sim 结果一致，**不能挂**。
