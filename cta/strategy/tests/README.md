# cta/strategy/tests/

## 主要做什么

[cta/strategy/](..) 策略实现与 baseline skill suite 的单元测试。重点防（1）连续合约切换漏候选、（2）candidate schema 漂移、（3）策略入场/止损口径与 OOT 不一致。

## 关键测试

| 文件 | 覆盖 |
|---|---|
| [test_baseline_setup_detection_module.py](test_baseline_setup_detection_module.py) | setup 识别（窄幅 / 突破 / 单边）规则 |
| [test_baseline_skill_suite_part01.py](test_baseline_skill_suite_part01.py) ~ [part05.py](test_baseline_skill_suite_part05.py) | `generate_candidate_opportunities` 拆 5 个块：候选生成、label 计算、止损线、atr_warmed、跨周期 |
| [test_baseline_split_modules.py](test_baseline_split_modules.py) | baseline 各模块的拆分契约 |
| [test_skill_tight_range_backtest_rb0.py](test_skill_tight_range_backtest_rb0.py) | RB0 上的窄幅突破策略真实数据回测 |
| [test_skill_tight_range_strategy.py](test_skill_tight_range_strategy.py) | 上面策略的纯逻辑单元 |
| [test_price_action_breakout_engine.py](test_price_action_breakout_engine.py) | Brooks 价格行为突破引擎 |
| [test_cta_adapter.py](test_cta_adapter.py) | vnpy CtaTemplate 适配层 |
| [test_cta_baseline.py](test_cta_baseline.py) | 接入 vnpy 的 baseline 模板 |
| [test_cta_tight_range.py](test_cta_tight_range.py) | 接入 vnpy 的窄幅突破策略 |
| [test_auto_contract.py](test_auto_contract.py) | 自动合约切换 / 主连配置 |
| [test_demos.py](test_demos.py) | demo 策略冒烟 |

## 注意事项

- **`test_baseline_skill_suite_part0X.py` 必须全绿**：part 拆分对应 `cta/strategy/baseline_skill_suite.py` 的关键路径，挂任意一个 part 表示候选生成口径漂移，会污染下游模型训练 label。
- **split-contract 必须不漏候选**：主连换月日的 candidate 不能掉，由 `test_baseline_setup_detection_module.py` 与 `test_auto_contract.py` 覆盖。
- **策略文件顶部约定**：每个策略文件必须写策略假设 / 适用品种 / 信号 / 风控 / 手续费假设，本目录测试**不强制校验**但 PR 必检。
- **vnpy 框架依赖**：`test_cta_*.py` 系列需要装 vnpy 主框架；纯研究测试（如 `test_baseline_*`）应当不 import vnpy。
- **跑测试**：
  - 全量：`pytest cta/strategy/tests/ -v`
  - 候选生成回归：`pytest cta/strategy/tests/test_baseline_skill_suite_part*.py -v`
  - 端到端冒烟：`pytest cta/strategy/tests/test_skill_tight_range_backtest_rb0.py -v`
