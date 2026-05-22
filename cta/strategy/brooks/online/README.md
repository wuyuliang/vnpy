# cta/strategy/brooks/online/

## 主要做什么

Brooks v3 的**在线/实盘包装**：把 [core](../core/) 装进 vnpy CtaTemplate 壳 + dry-run 回放驱动。和 [backtest/](../backtest/) 共享 core 代码，仅外层调度方式不同。

## 关键文件

| 文件 | 作用 |
|---|---|
| [live_strategy.py](live_strategy.py) | `BrooksV3LiveStrategy(CtaTemplate)`：实现 on_bar / on_tick / 风控钩子，**唯一**允许 import vnpy 的 Brooks 文件 |
| [runner.py](runner.py) | dry-run 回放驱动：用历史 bar 流模拟 live 行为，验证 live_strategy 与 backtest 结果一致 |

## 详细过程

```
[dry-run]
    python -m cta.strategy.brooks.online.runner \
        --config cta/strategy/brooks/config/strategy.yaml \
        --symbol RB0 --replay-start 2025-01-01

    runner.replay(BrooksV3LiveStrategy(params), bars)
        └─ 模拟实盘逐 bar 推进，落 trade_log
    │
    ▼
[与 backtest 对账]
    backtest.engine.run(BrooksV3Core(params), bars)
        与 online.runner.replay 的 trade_log 应当字节级一致
```

## 注意事项

- **唯一 vnpy 接入点**：本目录 `live_strategy.py` 是 Brooks 体系内**唯一**允许 import vnpy 的文件；其它 brooks 文件保持离线可测。
- **dry-run vs backtest 必须一致**：runner 跑出来的 trade_log 应当与 [../backtest/engine.py](../backtest/engine.py) 跑出来的一致；diff 即 bug，必须修。
- **接入实盘前**：先 dry-run 至少 30 天历史 + paper trading 5 天，并通过 [cta/sim/parity_check.py](../../../sim/parity_check.py) 与 OOT 对账。
- **不允许在 online 里加业务逻辑**：策略本身的决策都在 [core](../core/) 里；本目录只调用 + 适配 vnpy 接口。
