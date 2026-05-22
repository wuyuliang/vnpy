# cta/strategy/brooks/core/signal/

## 主要做什么

Brooks v3 的**三层多周期共振**信号生成：HTF（day）方向 → MTF（60min）setup → LTF（5min）触发。任一层不满足都不入场。

## 关键文件

| 文件 | 周期 | 输出 |
|---|---|---|
| [htf_bias.py](htf_bias.py) | day | `"long"` / `"short"` / `"flat"`：日线方向偏向 |
| [mtf_setup.py](mtf_setup.py) | 60min | `MtfSetup`（含 setup 类型 / 关键位 / score）或 None |
| [ltf_entry.py](ltf_entry.py) | 5min | `LtfEntry`（含 trigger_price / stop_price）或 None |

## 详细过程

```
[HTF 检查] htf_bias.classify(day_bars)
    └─ 无明确方向 → 不入场，循环退出
    │
    ▼
[MTF 检查] mtf_setup.detect(min60_bars, htf_direction)
    └─ 无 setup → 不入场
    │
    ▼
[LTF 触发] ltf_entry.find_trigger(min5_bars, mtf_setup)
    └─ 无 trigger → 等下一根 5min bar
    │
    ▼
返回 candidate(entry_price, stop_price, direction)
```

## 注意事项

- **严格三层与的关系**：三层都通过才生成 candidate，禁止"两层通过就放行"——v3 的核心约束就是多周期共振。
- **方向一致性**：MTF setup 与 LTF entry 的方向必须与 HTF bias 一致；矛盾时 LTF 应当拒绝。
- **shift 防穿越**：每一层只能用截至当前 ts 的"已收盘 bar"。day bar 在 23:00 之前是未收盘的。
- **无 vnpy 依赖**：纯函数，方便单测。
- **测试**：[../README.md](../README.md) 末段提到的 brooks tests 集中跑 `pytest cta/strategy/tests/ -k brooks -v`。
