# cta/feature/tests/

## 主要做什么

[cta/feature/](..) 通用特征引擎的单元 + 集成测试。重点确认（1）batch 与 online 计算字节级一致；（2）连续合约切换不漏特征；（3）所有特征模块 import 期无副作用。

## 关键测试

| 文件 | 覆盖 |
|---|---|
| [test_compute_pipeline.py](test_compute_pipeline.py) | `compute.py` 顶层端到端 smoke：从 OHLCV → 特征 parquet |
| [test_feature_modules_smoke.py](test_feature_modules_smoke.py) | 所有特征模块（calendar/momentum/pattern/...）import 与 build sanity |
| [test_feature_loader_local_data.py](test_feature_loader_local_data.py) | `feature_loader.py` 读 parquet 的接口稳定性 |
| [test_loader_and_scheduler.py](test_loader_and_scheduler.py) | loader + dispatch scheduler 协作 |
| [test_macro_feature.py](test_macro_feature.py) | macro 特征模块 |
| [test_online_api.py](test_online_api.py) | **online 单 bar 增量计算必须与 batch 字节级一致**（实盘正确性硬关卡） |
| [test_price_action_split_contract.py](test_price_action_split_contract.py) | 价格行为类特征在主连换月日不断裂 |
| [test_real_data_compute.py](test_real_data_compute.py) | 用真实落盘数据跑一遍特征链 |
| [test_run_all_features_split_contract.py](test_run_all_features_split_contract.py) | 全特征 + 连续合约边界 |

## 注意事项

- **test_online_api.py 是硬关卡**：online 与 batch 不一致 → 实盘与回测就会逐渐漂移。挂了**必须**当场修。
- **新加特征必须配 test**：`test_feature_modules_smoke.py` 会失败如果模块 import 失败，但语义正确性需要自己再加单测。
- **真实数据测试**：`test_real_data_compute.py` 依赖 `cta/data/origin/` 有最小数据集；CI 跑前先 `python -m cta.data_code.download_all` 准备样本。
- **跑测试**：`pytest cta/feature/tests/ -v`；快冒烟 `pytest cta/feature/tests/test_feature_modules_smoke.py -v`。
