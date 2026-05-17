# cta/data_code/

## 主要做什么

**原始行情数据的下载、转换、校验**。本目录只产出落盘到 `cta/data/origin/<interval>/<ALPHA_PREFIX>/<YYYY-MM-DD>.parquet` 的原始价格数据；特征计算在 [cta/feature/](../feature/)，**严禁混入**。

## 关键文件

| 文件 | 作用 |
|---|---|
| [futures_downloader.py](futures_downloader.py) | 商品期货主连/连续合约日线+分钟线下载（Tushare 主数据源） |
| [financial_futures_downloader.py](financial_futures_downloader.py) | 金融期货（IF/IH/IC/IM/TF/T/TS/TL）下载 |
| [index_downloader.py](index_downloader.py) | 商品/股指指数行情下载 |
| [expand_minute.py](expand_minute.py) | 1min → 5/15/30/60min 重采样 |
| [download_all.py](download_all.py) | 一键全量下载入口 |
| [download_all_dispatch.py](download_all_dispatch.py) | 分品种/分日期段调度，断点续跑 |
| [download_all_progress.py](download_all_progress.py) | 写入 `finished.csv` 进度 |
| [tushare_client.py](tushare_client.py) | Tushare API 封装（限速、重试、Token 注入） |
| [_tushare_utils.py](_tushare_utils.py) | Tushare 通用工具：分页/拼接/合约切换 |
| [cross_source_audit.py](cross_source_audit.py) | 跨数据源对账（Tushare vs 其他）找异常 |
| [validate.py](validate.py) | 落盘后字段/连续性/重复检查 |
| `finished.csv` / `empty.csv` | 进度跟踪：哪些任务跑完、哪些返回空 |

## 详细过程（典型流水线）

```
1. 准备 Tushare token：环境变量 TUSHARE_TOKEN 或 ~/.tushare/token
2. python -m cta.data_code.download_all_dispatch
   ├─ 读取品种列表（从 cta/config/futures_meta.py）
   ├─ 按 (symbol, interval, date_range) 切分任务
   ├─ 调 futures_downloader / financial_futures_downloader 下载
   ├─ 调 validate.validate_one() 做基础校验
   └─ 写入 cta/data/origin/<interval>/<PFX>/<date>.parquet
3. python -m cta.data_code.expand_minute  # 1min → 5/15/30/60min
4. python -m cta.data_code.cross_source_audit  # 可选：对账
```

## 注意事项

- **数据只读边界**：本目录写出去的数据落到 [cta/data/](../data/) 后**默认只读**。不要在 `cta/data/origin/` 之外的代码里改这些 parquet。
- **不允许硬编码 token**：所有 API key 走环境变量 `TUSHARE_TOKEN`；代码里出现明文 token 会被 review 卡掉。
- **断点续跑**：所有任务必须可被中断后重跑（`finished.csv` / `empty.csv`），不要写"全跑成功才落盘"的死路径。
- **空数据兼容**：上游返回空集 → 写 `empty.csv`，**不要**抛错。下游消费方应当容忍 parquet 文件缺失。
- **跨交易所符号扩展**：CZCE 同时存在 `CZC` / `CZCE` 写法；下载/落盘必须按 `cta/config/futures_meta.py` 里的规范名落，不要在 downloader 里就地 `.replace("CZC", "CZCE")`。
- **校验失败处理**：`validate.py` 报错的样本应当落到 `empty.csv` 或专门的 error log，不要静默吞掉。
- **不在本目录算特征**：哪怕是简单的 `pct_change`，也属于 [cta/feature/](../feature/)。本目录只负责"把卖方原始数据搬到磁盘"。
- **测试**：跑 `pytest cta/data_code/tests/ -v`；其中 `test_*_split_contract.py` 验证连续合约切换不会漏 bar。
