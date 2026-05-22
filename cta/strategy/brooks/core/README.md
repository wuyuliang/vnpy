# cta/strategy/brooks/core/

## 主要做什么

**Brooks v3 策略的核心逻辑**：离线（backtest）与在线（live/sim）共享的同一套代码。无 vnpy 依赖，纯 Python，方便单元测试和跨环境复用。

## 关键文件 / 子目录

| 路径 | 作用 |
|---|---|
| [strategy.py](strategy.py) | `BrooksV3Core` 组合器：把 features / signal / risk / model 串成一个完整的决策类 |
| [trade_log.py](trade_log.py) | `TradeRecord` + parquet writer，逐笔成交落盘 |
| [features/](features/) | 特征统一接口（消费 `cta/feature/` 的 pa_* 特征） |
| [signal/](signal/) | 三层信号：HTF / MTF / LTF |
| [risk/](risk/) | 单笔 sizing / 止损 / 组合层面 |
| [model/](model/) | XGBoost 评分门控（labeler / dataset / train / score_gate） |

## 详细过程

```
BrooksV3Core.on_bar(bar)
    │
    ├─ features.adapter.fetch(symbol, ts, intervals=[day, 60min, 5min])
    │       → 从 cta/data/feature/ 拼出 ~100 个 pa_* 特征
    │
    ├─ signal.htf_bias(day_features)          → 多头/空头方向
    │   signal.mtf_setup(min60_features)      → setup 是否成立
    │   signal.ltf_entry(min5_features)       → 5min 触发点
    │
    ├─ model.score_gate.predict(features)     → XGBoost 评分
    │       → 低于阈值直接 reject
    │
    ├─ risk.sizing(equity, atr, stop_pct)     → 单笔仓位
    │   risk.stops.update(position, bar)      → 止损更新
    │   risk.portfolio.check(portfolio_state) → 组合层 cap
    │
    └─ trade_log.append(trade_record)
```

## 注意事项

- **无 vnpy 依赖**：本目录禁止 `import vnpy`；这是离线/在线复用的硬约束。`brooks/online/live_strategy.py` 才负责包装 vnpy CtaTemplate。
- **shift 防穿越**：消费 features 时严格用"截至当前 bar 已收盘"的数据；HTF 特征要用上一根已收盘的 day bar。
- **XGBoost 模型独立训练**：本目录的 model/ 只做训练 / 推理；模型文件落盘到 [../models/](../models/) 而非这里。
- **配置入口**：所有运行时参数从 [../config/strategy.yaml](../config/strategy.yaml) → `BrooksV3Params` 注入，禁止硬编码阈值。
- **测试**：Brooks 自带 tests 散落到这里各文件旁边（没有独立 tests/），运行：`pytest cta/strategy/tests/ -k brooks -v`。
