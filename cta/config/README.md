# cta/config/

## 主要做什么

集中存放整套 CTA 流水线的**参数与口径配置**，所有跨模块共享的常量都从这里 import，禁止在策略/模型/回测代码里硬编码。

## 关键文件

| 文件 | 作用 | 核心字段 |
|---|---|---|
| [baseline_skill_suite_config.py](baseline_skill_suite_config.py) | baseline 策略组（窄幅突破等）共用的 ATR、horizon、label 参数 | `LABEL_HORIZON_BARS`、`LABEL_MAE_PENALTY`、`label_stop_loss_pct` |
| [skill_tight_range_breakout_config.py](skill_tight_range_breakout_config.py) | 单一窄幅整理突破策略的可调参数 | 突破阈值、整理 N 日、放量倍数 |
| [model_oot_eval_config.py](model_oot_eval_config.py) | OOT 评估 + portfolio_logic runtime 全部口径 | `OotEvaluationConfig` / `DEFAULT_OOT_EVAL_CONFIG`、`trade_filter_gate_mode`、`trade_filter_percentile_threshold`、`stacking_score_threshold`、止损率一致性校验 |
| [futures_meta.py](futures_meta.py) | 合约元信息：交易所、保证金率、tick、乘数、滑点 | `FUTURES_META` 字典 |
| [symbol_cluster_config.py](symbol_cluster_config.py) | 品种 → cluster 映射（INDEX/METAL/AGRI/...） | `infer_symbol_cluster`、`CLUSTER_BY_SYMBOL` |
| [symbol_disable.py](symbol_disable.py) | 禁用品种 manifest 读取 | `load_symbol_disable_manifest` |
| [trading_session_config.py](trading_session_config.py) | 交易时段（含夜盘）+ 涨跌停档位 | `get_trading_session(symbol)` |

## 详细过程（典型调用链）

```
strategy/baseline_skill_suite.py
    └─ from cta.config.baseline_skill_suite_config import LABEL_HORIZON_BARS, LABEL_MAE_PENALTY

model/pipeline_orchestrator_*.py
    └─ from cta.config.model_oot_eval_config import DEFAULT_OOT_EVAL_CONFIG
    └─ from cta.config.symbol_cluster_config import infer_symbol_cluster

skills/data_backtest/event_driven_backtest.py
    └─ from cta.config.futures_meta import FUTURES_META  # margin / tick / slippage
    └─ from cta.config.trading_session_config import get_trading_session
```

## 注意事项

- **止损率一致性**：`intrabar_stop_loss_pct`（OOT 执行）必须与 `baseline_skill_suite.generate_candidate_opportunities` 的 `label_stop_loss_pct` 默认值一致，否则训练 label 与 OOT 口径漂移。`OotEvaluationConfig.__post_init__` 会在 `enforce_stop_loss_consistency=True` 时校验，见 [test_stop_loss_pct_consistency.py](tests/test_stop_loss_pct_consistency.py)。
- **trade_filter 门槛口径**：生产默认 `DEFAULT_OOT_EVAL_CONFIG.trade_filter_gate_mode="cluster_interval_percentile"`，按 `cluster+interval` 内的 `trade_filter_prob_pctl` 做门槛，避免 INDEX/day 这类 raw probability 分布偏低的组被全局 `trade_filter_threshold=0.62` 系统性误杀。`trade_filter_prob_pctl` 必须由 pipeline 在非 OOT 的 `train+valid` 校准分布上生成；OOT evaluator 不允许用 OOT 自身分布现场 rank。若要单独调 INDEX/day，可配置 `trade_filter_percentile_threshold_by_cluster_interval={"index|day": 65.0}`；若必须沿用 raw probability，则设 `trade_filter_gate_mode="raw"` 并用 `trade_filter_raw_threshold_by_cluster_interval={"index|day": 0.45}` 覆盖。
- **dataclass 全部 `frozen=True`**：要修改 config 实例须用 `dataclasses.replace(...)`，直接 `cfg.x = ...` 会抛 `FrozenInstanceError`。pipeline/OOT 代码已拆为正常 `.py` 模块，配置改动需要同步检查 `pipeline_orchestrator_cli.py` 与 `pipeline_oot_evaluation.py`。
- **修改默认值前**：先看是否有 OOT 复盘或 review 文档（[cta/docs/review/](../docs/review/)）依赖原值；新阈值要在 `cta/report/change_log.md` 留痕。
- **禁止增加硬编码路径**：任何 `Path("/abs/path")` 都应改为相对 `cta/data/` 或通过环境变量注入。
- **测试覆盖**：[tests/](tests/) 下覆盖了"config 实例化 + 字段校验 + 默认值不漂移"。改字段后跑 `pytest cta/config/tests/ -v`。
