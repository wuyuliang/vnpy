# cta/strategy/brooks/core/features/

## 主要做什么

把 [cta/feature/](../../../../feature/) 计算落盘的 ~100 个 pa_* 特征统一接口供 [Brooks core](..) 各 signal 模块消费。**不重算特征**，只读 + 适配。

## 关键文件

| 文件 | 作用 |
|---|---|
| [adapter.py](adapter.py) | `fetch(symbol, ts, intervals)` 接口，按 (symbol, timestamp, interval) 拼出对齐的特征 DataFrame |

## 注意事项

- **只读**：禁止在本目录写新特征。新特征应当先在 [cta/feature/](../../../../feature/) 实现并落盘，再通过 adapter 暴露。
- **跨周期 join**：adapter 要保证 HTF（day）特征用"上一根已收盘"，MTF（60min）/ LTF（5min）也同理，防止穿越。
- **缺数据兜底**：parquet 缺失或为空时返回空 DataFrame（schema 一致），不抛异常。
