# 噪声过滤 / Noise Filtering

> 归属章节：`04_range_strategies/` · 前置：`01_market_regime/02_range_detection.md` · 关联：`cta/feature/price_action.py`

## 1. Skill 定义

专门处理震荡策略里**单 bar 杂波、假反转、幽灵吸入**问题的通用过滤器集合：时间过滤、成交量过滤、最小形态确认、二次测试。

## 2. 解决什么问题

- 痛点 1：震荡策略信号多但噪声多，直接用净 PnL 为负。
- 痛点 2：假反转比真反转多 2-3 倍。
- 痛点 3：单 bar 反转易被市商 spoof。
- 增量价值：把"信号数量"转化为"高质量信号集"，让胜率 / 盈亏比都提升。

## 3. 适用市场 / 适用场景

- **品种类别**：全部震荡品种
- **周期**：minute30 / minute15 / minute5
- **行情状态前提**：震荡
- **不适用场景**：趋势

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| 成交量确认 | signal bar vol / MA(vol, 20) | — | > 1.2 | 计算 |
| 时间过滤 | 是否在开盘 / 午盘 / 收盘附近 | 开/收 ±30 min | — | 计算 |
| inside bar / tight | 信号前是否有 inside / tight pattern | — | 有则更强 | `pa_inside_*`、`pa_tight_range_*` |
| 二次确认 | 信号后 1-2 根 bar 同向 | — | 收盘同向 | 计算 |
| 信号 bar 体量 | (close-open)/ATR | — | > 0.3 | 计算 |

## 5. 常见策略映射

本 skill 不是独立策略，而是**所有震荡策略前的过滤层**：

### 过滤器 A：成交量门槛
```text
signal_volume >= 1.2 * MA(volume, 20)
```

### 过滤器 B：时间窗
```text
bar.time ∈ [开盘, 开盘+30min] or [收盘-30min, 收盘]
```

### 过滤器 C：inside / tight 前置
```text
最近 3 bar 至少 1 根是 inside 或 tight_range
```

### 过滤器 D：二次确认
```text
signal bar 下一根 bar close 同向
```

综合：任意 ≥ 2 个过滤器通过才触发策略。

### 示例完整策略（边界反转 + 噪声过滤）
- 信号定义：`04_range_strategies/01_range_boundary_reversal.md` 的策略 A
- 额外过滤：`B + D` 必须都通过
- 其它同 01。

## 6. 代码模块设计

```text
cta/strategy/common/filters/
├── __init__.py
├── volume.py
├── time_window.py
├── pattern_prereq.py
├── secondary_confirm.py
└── combiner.py
```

```python
from dataclasses import dataclass
from typing import Callable, Iterable
import pandas as pd

@dataclass
class FilterResult:
    name: str
    passed: bool
    reason: str | None = None

Filter = Callable[[pd.DataFrame, int], FilterResult]

def combine_filters(
    df: pd.DataFrame,
    bar_idx: int,
    filters: Iterable[Filter],
    min_pass: int = 2,
) -> bool:
    """通过至少 min_pass 个 filter 才 True。"""
    ...

def make_volume_filter(ratio: float = 1.2, ma_n: int = 20) -> Filter:
    ...

def make_time_window_filter(windows: list[tuple[str,str]]) -> Filter:
    ...
```

## 7. 回测评估重点

- **必看指标（7）**：过滤后信号数 / 胜率改善 / 盈亏比改善 / 过滤后 Sharpe 改善 / PnL 绝对值变化 / 成本下降 / 单日交易次数
- **分层评估**：按过滤器（单独 / 组合）、按品种
- **稳健性检验**：
  1. `min_pass` = 1/2/3 的胜率/次数权衡
  2. 关闭单个过滤器，PnL 下降幅度
  3. 过拟合检测（IS 改善但 OOS 不改善）

## 8. 常见错误

- 过滤太狠 → 信号几乎消失
- 基于结果倒推过滤器 → 过拟合
- 多个过滤器相关性高 → 等于没加
- 忘了时间过滤的时区问题（夜盘）

## 9. 迭代方向

- v1：规则过滤
- v2：过滤器权重（不等权 combine）
- v3：ML 打分替换规则过滤（见 `09_ml_augmentation/01_trade_filter_model.md`）
- v4：过滤器自适应（按品种 / 按年份）

## 10. 与其他 Skills 的关系

- **依赖**：`01_market_regime/02_range_detection.md`
- **被依赖**：`04_range_strategies/01/02/03` 全部
- **互补**：`06_filtering_and_scoring/01_setup_quality_score.md`（把过滤变成打分）
