# cta/skills/price_action/tests/

## 主要做什么

[cta/skills/price_action/](..) 价格行为模式识别函数的单元测试。

## 关键测试

| 文件 | 覆盖 |
|---|---|
| [test_tight_range_breakout.py](test_tight_range_breakout.py) | 窄幅整理 + 放量突破 |
| [test_breakout_pullback.py](test_breakout_pullback.py) | 突破后回踩、不破前高 |
| [test_bull_bear_flag.py](test_bull_bear_flag.py) | 多/空头旗形 |
| [test_failed_breakout.py](test_failed_breakout.py) | 假突破识别 |
| [test_hl_structure.py](test_hl_structure.py) | HH/HL / LL/LH 高低点结构 |
| [test_channel_state.py](test_channel_state.py) | 通道状态（上升 / 下降 / 平行） |

## 注意事项

- **防穿越是硬约束**：每个识别函数测试必须 assert "传入 bars 的最后一根之后不会被读取"。建议用 `bars.iloc[:i+1]` 切片，再人工 assert 函数没尝试访问 `i+1`。
- **score ∈ [0, 1]**：所有 `detect_*` 返回的 score 必须在 [0, 1]。
- **None 容错**：未识别时返回 None，不要抛异常。
- **跑测试**：`pytest cta/skills/price_action/tests/ -v`。
