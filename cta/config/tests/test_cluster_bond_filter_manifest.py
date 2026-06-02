"""cluster_bond trade_filter 严格阈值 manifest 测试。

历史背景（2026-05-24 OOT 诊断）：
  - cluster_bond 在 OOT 单簇 cost/|gross| = 152%（before）/ 120%（after），整簇净亏。
  - bond T/TF/TS 单笔毛利极小（p95 net_return 仅 0.16%），是"给券商打工"的典型反例。
  - 全局 trade_filter_percentile_threshold=70 对 bond 太松；本 manifest 单独抬高到 85+。

manifest 不动默认 cfg，由用户在 CLI 或 cfg 装配时显式喂入：

```python
from cta.config.cluster_bond_filter_manifest import STRICT_BOND_PERCENTILE_THRESHOLDS
cfg = OotEvaluationConfig(
    trade_filter_percentile_threshold_by_cluster_interval=STRICT_BOND_PERCENTILE_THRESHOLDS,
    ...
)
```

测试覆盖：
  1) manifest 字段存在且非空
  2) 所有 key 是 bond|* 格式
  3) 所有 value 都 ≥ 全局默认 70（即"提高"）
  4) 灌入 OotEvaluationConfig 后 post_init 通过且字段被 MappingProxy 冻结
  5) 提供 day/60min/30min 三个 interval 覆盖
"""
from __future__ import annotations

import unittest

from cta.config.cluster_bond_filter_manifest import (
    STRICT_BOND_PERCENTILE_THRESHOLDS,
    STRICT_BOND_RAW_THRESHOLDS,
)
from cta.config.model_oot_eval_config import OotEvaluationConfig


class TestClusterBondFilterManifest(unittest.TestCase):
    def test_percentile_manifest_non_empty(self) -> None:
        self.assertGreater(len(STRICT_BOND_PERCENTILE_THRESHOLDS), 0)

    def test_raw_manifest_non_empty(self) -> None:
        self.assertGreater(len(STRICT_BOND_RAW_THRESHOLDS), 0)

    def test_all_keys_are_bond_cluster(self) -> None:
        for k in STRICT_BOND_PERCENTILE_THRESHOLDS:
            self.assertTrue(k.startswith("bond|"), f"unexpected key {k}")
        for k in STRICT_BOND_RAW_THRESHOLDS:
            self.assertTrue(k.startswith("bond|"), f"unexpected key {k}")

    def test_percentile_values_above_global_default(self) -> None:
        """阈值必须严格 > 全局默认 70 才算"提高"。"""
        for k, v in STRICT_BOND_PERCENTILE_THRESHOLDS.items():
            self.assertGreater(v, 70.0, f"{k}={v} not strictly above default 70")
            self.assertLessEqual(v, 100.0, f"{k}={v} > 100 invalid")

    def test_raw_values_above_global_default(self) -> None:
        """raw 阈值必须严格 > 0.62（全局默认）。"""
        for k, v in STRICT_BOND_RAW_THRESHOLDS.items():
            self.assertGreater(v, 0.62, f"{k}={v} not strictly above 0.62")
            self.assertLessEqual(v, 1.0, f"{k}={v} > 1 invalid")

    def test_intervals_covered(self) -> None:
        """manifest 至少覆盖 day / 60min / 30min。"""
        intervals_pctl = {k.split("|")[1] for k in STRICT_BOND_PERCENTILE_THRESHOLDS}
        self.assertIn("day", intervals_pctl)
        self.assertIn("60min", intervals_pctl)
        self.assertIn("30min", intervals_pctl)

    def test_loaded_into_cfg_passes_post_init(self) -> None:
        """灌进 OotEvaluationConfig 后 post_init 校验应通过且冻结。"""
        cfg = OotEvaluationConfig(
            trade_filter_percentile_threshold_by_cluster_interval=dict(
                STRICT_BOND_PERCENTILE_THRESHOLDS
            ),
            trade_filter_raw_threshold_by_cluster_interval=dict(
                STRICT_BOND_RAW_THRESHOLDS
            ),
        )
        for k, v in STRICT_BOND_PERCENTILE_THRESHOLDS.items():
            self.assertEqual(
                float(cfg.trade_filter_percentile_threshold_by_cluster_interval[k]),
                float(v),
            )

    def test_cfg_field_is_frozen(self) -> None:
        cfg = OotEvaluationConfig(
            trade_filter_percentile_threshold_by_cluster_interval=dict(
                STRICT_BOND_PERCENTILE_THRESHOLDS
            ),
        )
        with self.assertRaises(TypeError):
            cfg.trade_filter_percentile_threshold_by_cluster_interval["bond|day"] = 0.0


if __name__ == "__main__":
    unittest.main()
