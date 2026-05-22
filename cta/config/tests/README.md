# cta/config/tests/

## 主要做什么

[cta/config/](..) 各 dataclass / 字段约束 / 跨配置一致性的单元测试。任何 config 默认值漂移都要先过这里。

## 关键测试

| 文件 | 覆盖 |
|---|---|
| [test_model_oot_eval_config.py](test_model_oot_eval_config.py) | `OotEvaluationConfig` 字段范围校验、`__post_init__` 的边界判断 |
| [test_stop_loss_pct_consistency.py](test_stop_loss_pct_consistency.py) | `intrabar_stop_loss_pct` 与 `label_stop_loss_pct` 必须一致；防训练 / OOT 口径漂移 |
| [test_strategy_and_suite_configs.py](test_strategy_and_suite_configs.py) | `baseline_skill_suite_config` / `skill_tight_range_breakout_config` 默认值与字段类型 |
| [test_symbol_disable.py](test_symbol_disable.py) | manifest 读写、空文件/缺列容错 |
| [test_trading_session_config.py](test_trading_session_config.py) | 交易时段（含夜盘）、涨跌停档位 |

## 注意事项

- **改 config 默认值前**：先跑本目录 `pytest cta/config/tests/ -v`，确认期望的 test fail，再改默认值。
- **frozen dataclass 校验**：所有 config 都 `frozen=True`，测试里要修改字段须用 `dataclasses.replace(...)`。
- **stop_loss 一致性失败时的修法**：要么改 `cta/strategy/baseline_skill_suite.generate_candidate_opportunities` 的 `label_stop_loss_pct` 默认值，要么改 `OotEvaluationConfig.intrabar_stop_loss_pct`，两者必须同步。
