"""cluster_bond 严格 trade_filter 阈值 manifest。

为什么单独提高 bond 阈值（2026-05-24 OOT 诊断）：
  - OOT `oot_20260523_034107` cluster_bond cost/|gross| = 152.6%（BEFORE），
    `oot_20260523_154428` 改善到 120.4%（AFTER），但仍 > 100%；
  - 75-89 笔 bond 全期总 net = -775 ~ -453 元，全部"给券商打工"；
  - bond T/TF/TS 单笔 p95 net_return 仅 0.16%，毛利天花板太低；
  - 全局 trade_filter_percentile_threshold = 70 对 bond 太松——
    需要 ≥ 80 才能让通过的 bond 候选预期净利能覆盖成本。

数值理由：
  - bond p95 ≈ 0.16% / 单边成本 ≈ 0.08% → 净利 0.08%，要求 prob 显著高才能博正期望；
  - 用 percentile 80（top 20% 信号）作为 bond 全 interval 默认（2026-05-24 第二轮调整：
    最初取 85/88 偏严可能直接吃完 bond 信号，降到 80 留出更多通过空间，下轮 OOT 观察后再调）；
  - raw 阈值给 0.70 作为 fallback（mode=raw 时生效）。

用法（在 CLI 或 cfg 装配时显式合并）：

```python
from cta.config.cluster_bond_filter_manifest import (
    STRICT_BOND_PERCENTILE_THRESHOLDS,
    STRICT_BOND_RAW_THRESHOLDS,
)

cfg = OotEvaluationConfig(
    trade_filter_percentile_threshold_by_cluster_interval={
        **other_overrides,
        **STRICT_BOND_PERCENTILE_THRESHOLDS,
    },
    trade_filter_raw_threshold_by_cluster_interval={
        **STRICT_BOND_RAW_THRESHOLDS,
    },
)
```

不修改默认 cfg：保持其他 cluster 行为不变，避免一次性大改影响诊断口径。
"""
from __future__ import annotations

from typing import Final


# percentile mode（cluster_interval_percentile）下的 bond 严格阈值
STRICT_BOND_PERCENTILE_THRESHOLDS: Final[dict[str, float]] = {
    "bond|day":   80.0,
    "bond|60min": 80.0,
    "bond|30min": 80.0,
    "bond|15min": 80.0,
}

# raw mode 下的 bond 严格阈值（trade_filter_gate_mode="raw" 时生效）
STRICT_BOND_RAW_THRESHOLDS: Final[dict[str, float]] = {
    "bond|day":   0.70,
    "bond|60min": 0.70,
    "bond|30min": 0.70,
    "bond|15min": 0.70,
}


__all__ = [
    "STRICT_BOND_PERCENTILE_THRESHOLDS",
    "STRICT_BOND_RAW_THRESHOLDS",
]
