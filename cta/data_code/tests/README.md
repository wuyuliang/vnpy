# cta/data_code/tests/

## 主要做什么

[cta/data_code/](..) 下载器、调度器、校验器、跨数据源对账的单元/集成测试。重点防"连续合约切换漏 bar"和"Tushare 客户端误吞错"。

## 关键测试

| 文件 | 覆盖 |
|---|---|
| [test_futures_downloader_split_contract.py](test_futures_downloader_split_contract.py) | 商品期货连续合约切换（主连换月）时不漏 bar、不重 bar |
| [test_financial_futures_downloader.py](test_financial_futures_downloader.py) | 金融期货（IF/IH/IC/IM/TF/T/TS/TL）下载与字段映射 |
| [test_download_all_split_contract.py](test_download_all_split_contract.py) | 全品种调度时连续合约边界 |
| [test_download_cu0_import_safe.py](test_download_cu0_import_safe.py) | import 期不触发副作用（不连 Tushare、不读磁盘） |
| [test_download_all_dispatch.py](test_download_all_dispatch.py) | 任务切分、断点续跑、`finished.csv` / `empty.csv` 状态机 |
| [test_index_downloader.py](test_index_downloader.py) | 指数行情下载与字段一致性 |
| [test_tushare_client_split.py](test_tushare_client_split.py) | Tushare 客户端分页/重试/限速 |
| [test_validate.py](test_validate.py) | 落盘后字段/连续性/重复检查 |
| [test_cross_source_audit.py](test_cross_source_audit.py) | 跨数据源对账（Tushare vs 其他） |

## 注意事项

- **不调真 Tushare API**：所有测试必须 mock `tushare_client`，禁止依赖网络。
- **连续合约边界**：split-contract 类测试是最重要的回归门——主连换月日的 bar 数据来源切换不能漏/重，挂了立刻看 `cta/data_code/futures_downloader.py:_resolve_split_dates`。
- **跑测试**：
  ```bash
  pytest cta/data_code/tests/ -v
  ```
  smoke：`pytest cta/data_code/tests/ -k split_contract -v`
