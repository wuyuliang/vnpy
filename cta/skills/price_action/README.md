# cta/skills/price_action/

## 主要做什么

**价格行为学（Al Brooks 派系）核心模式识别**：把窄幅突破、HH/LL 结构、bull/bear flag、突破回踩、假突破等模式做成可识别 + 可标注 + 可量化的函数，供 [cta/strategy/](../../strategy/) 和 [cta/skills/trend_strategies/](../trend_strategies/) / [cta/skills/range_strategies/](../range_strategies/) 调用。

## 关键文件

| 文件 | 模式 |
|---|---|
| [tight_range_breakout.py](tight_range_breakout.py) | 窄幅整理 + 放量突破 |
| [breakout_pullback.py](breakout_pullback.py) | 突破后回踩、不破前高继续 |
| [bull_bear_flag.py](bull_bear_flag.py) | 多/空头旗形（趋势中的整理） |
| [failed_breakout.py](failed_breakout.py) | 假突破识别（突破后立即反向吞没） |
| [hl_structure.py](hl_structure.py) | 高低点结构：HH/HL（多头延续）、LL/LH（空头延续） |
| [channel_state.py](channel_state.py) | 通道状态：上升通道 / 下降通道 / 平行通道 |
| [_common.py](_common.py) | 内部共用 helper（缩进规则、最低 bar 数、ATR 归一化等） |

## 详细过程

每个文件暴露一个或多个 `detect_*` / `is_*` 函数，签名约定：

```python
def detect_tight_range_breakout(
    bars: pd.DataFrame,        # OHLCV，最新一根是当前 bar（未来不可见）
    *,
    lookback: int = 20,         # 整理区窗口
    breakout_atr_mult: float = 1.0,
    volume_mult: float = 1.5,
) -> dict | None:
    """返回 detection 字典或 None（未识别）。

    Returns:
      {"setup_bar_idx": int, "trigger_price": float, "stop_price": float,
       "score": float in [0,1], "reason": str}
    """
```

下游使用方式：
```python
det = detect_tight_range_breakout(bars.iloc[:i+1])
if det is not None:
    candidate = make_candidate(bars.iloc[i], det)
    candidates.append(candidate)
```

## 注意事项

- **严格防穿越**：函数签名约定 "bars 的最后一根 == 当前可见 bar"。函数内部禁止 `.shift(-N)` 或读取下标 `+N` 以外的索引。
- **必须可量化**：每个模式都要有数值阈值（lookback / atr_mult / volume_mult），不允许"看起来像 flag"这种主观判断。
- **score ∈ [0, 1]**：detection 返回的 score 是模式置信度，下游可用作权重。
- **新模式必须配单测**：单测要覆盖（1）典型 positive 样本（2）边界 negative 样本（3）空数据 / NaN 容错。
- **与 [cta/feature/price_action*.py](../../feature/) 的关系**：
  - `cta/feature/price_action*.py`：把价格行为信息**离散化为列**（连续值），供 ML 用
  - 本目录：把价格行为**识别为事件**（候选 setup），供策略生成 candidate
- **测试**：`pytest cta/skills/price_action/tests/ -v`。
