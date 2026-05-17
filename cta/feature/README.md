# cta/feature/

## 主要做什么

**通用型特征计算与持久化**。从 `cta/data/origin/<interval>/<PFX>/<date>.parquet` 读 OHLCV，计算 ~400 个跨策略可共享的技术/统计/上下文特征，写到 `cta/data/feature/<interval>/<PFX>/<date>.parquet`。模型独有特征不在这里——见 [cta/model/feature/](../model/feature/)。

更详细的特征清单与因果性约束见 [FEATURES.md](FEATURES.md)。

## 关键文件

### 入口与调度
| 文件 | 作用 |
|---|---|
| [compute.py](compute.py) | 顶层 compute 入口：单 symbol × 单 date 一次计算 |
| [feature_compute_dispatch.py](feature_compute_dispatch.py) | 多品种/多日期/多 interval 批量调度，支持断点续跑 |
| [feature_interval_runner.py](feature_interval_runner.py) | 单 interval 全量批跑 |
| [loader.py](loader.py) / [feature_loader.py](feature_loader.py) | 读特征 parquet 的统一接口（向下游 strategy/model 暴露） |
| [online.py](online.py) | 实盘在线特征流：增量计算最新 bar 的特征值 |

### 特征模块（按类别）
| 文件 | 类别 |
|---|---|
| [calendar_feat.py](calendar_feat.py) | 日历特征：交易日/月/季/年内位置 |
| [minute_tod.py](minute_tod.py) | 分钟级 time-of-day（开盘后/收盘前 N 分钟等） |
| [momentum.py](momentum.py) | 动量类（roc / sharpe-ratio / regime momentum） |
| [pattern.py](pattern.py) | K 线形态（hammer / engulfing / doji 等） |
| [price_action.py](price_action.py) / [price_action_bars.py](price_action_bars.py) / [price_action_advanced.py](price_action_advanced.py) / [price_action_context.py](price_action_context.py) | Brooks 价格行为学派生特征 |
| [composite.py](composite.py) | 组合特征：把多个原子特征合并/归一化 |
| [multi_timeframe.py](multi_timeframe.py) | 跨周期特征（如 60min 的 ma20 透过到 day 上） |
| [cross_section.py](cross_section.py) | 截面排名 / 截面 z-score |
| [entry_stop.py](entry_stop.py) | 入场/止损价位类特征（ATR 倍数等） |
| [macro_feature.py](macro_feature.py) | 宏观/基本面特征 |

### Manifest / 元数据
| 文件 | 作用 |
|---|---|
| [FEATURES.md](FEATURES.md) | **特征定义清单**，含名称、公式、单位、因果性 |
| [causality_manifest.csv](causality_manifest.csv) | 因果性 manifest：每个特征的"用未来信息了吗"白名单/黑名单 |
| [index_reference_symbols.csv](index_reference_symbols.csv) | 计算截面特征时引用的指数标的列表 |
| `code_review.txt` | 历史 review 笔记 |

## 详细过程

```
[原始数据]  cta/data/origin/<interval>/<PFX>/<date>.parquet
              │
              ▼
        compute.py
              │
        ┌─ calendar_feat
        ├─ momentum (ATR / ROC / vol / ...)
        ├─ price_action (高低点 / 实体比例 / 影线 / ...)
        ├─ pattern (形态识别)
        ├─ multi_timeframe (跨周期 join)
        ├─ cross_section (截面排名)
        └─ composite (组合)
              │
              ▼
        feature_compute_dispatch.py（并行、续跑）
              │
              ▼
[落盘]   cta/data/feature/<interval>/<PFX>/<date>.parquet

[消费方]  cta/strategy/* / cta/model/* 通过 feature_loader.py 读取
```

## 注意事项

- **绝对禁止特征穿越**：任何特征都必须基于"截至当前 bar 的可见信息"。新增特征必须同步更新 [causality_manifest.csv](causality_manifest.csv) 并跑 `pytest cta/feature/tests/test_compute_pipeline.py`。
- **shift(1) 默认**：对于使用收盘价的特征，下游若要在"开盘时使用"，特征本身已经表示当前 bar 收盘价信息；消费方需要自行 `.shift(1)` 错位避免穿越。
- **`cta/data/feature/` 是只读输出**：本目录代码可以写它，但其他模块只能读。直接修改特征 parquet 是禁止的。
- **空数据/缺数据**：上游 parquet 不存在或为空时，必须返回空 DataFrame（schema 一致），**不能抛异常**让下游崩。`feature_loader.py` 已统一处理。
- **online vs offline**：[online.py](online.py) 单 bar 增量计算的逻辑必须与 batch 计算字节级一致，由 `tests/test_online_api.py` 校验。**改 batch 计算函数时同步改 online**。
- **跨周期特征**：用 [multi_timeframe.py](multi_timeframe.py) 而非自己写 join；前者已经处理了"高时间框架未收盘"的边界（必须用上一根已收盘的 bar）。
- **截面特征**：[cross_section.py](cross_section.py) 依赖 [index_reference_symbols.csv](index_reference_symbols.csv) 的品种 universe。新加品种要先同步这个 csv。
- **测试**：跑 `pytest cta/feature/tests/ -v`，重点关注 `test_real_data_compute.py`（端到端 smoke）与 `test_*_split_contract.py`（连续合约切换不漏算）。
