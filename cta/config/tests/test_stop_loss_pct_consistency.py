"""CI guard: training label stop loss MUST equal OOT execution stop loss.

This is the post-mortem of the 20260512 POOL minute60 disaster where:
  - training label was generated with stop_loss_pct=0.01 (P0.5 default)
  - OOT evaluation used intrabar_stop_loss_pct=0.001 (10x tighter)
  → 97.5% of OOT trades hit stop_loss, win_rate 2.5%, sharpe -6.45.

If anyone ever flips one default but forgets the other, this test fails loudly.
"""
from __future__ import annotations

import inspect
import unittest

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.strategy.baseline_skill_suite import generate_candidate_opportunities


def _default_label_stop_loss_pct() -> float:
    sig = inspect.signature(generate_candidate_opportunities)
    param = sig.parameters.get("label_stop_loss_pct")
    if param is None or param.default is inspect.Parameter.empty:
        raise AssertionError("label_stop_loss_pct default missing from generate_candidate_opportunities")
    return float(param.default)


class TestStopLossPctConsistency(unittest.TestCase):
    """Hard guard: label_stop_loss_pct (training) ≈ intrabar_stop_loss_pct (OOT)."""

    def test_oot_intrabar_stop_loss_matches_label_stop_loss(self) -> None:
        oot_default = float(OotEvaluationConfig().intrabar_stop_loss_pct)
        label_default = _default_label_stop_loss_pct()
        # 允许 ±0.005 容差（label 与 OOT 可以略微不同，但量级必须一致）
        diff = abs(oot_default - label_default)
        self.assertLessEqual(
            diff,
            0.005,
            f"训练 label 止损 ({label_default}) 与 OOT 评估止损 ({oot_default}) 错位 "
            f"{diff:.4f}，超过 ±0.005 容差。这是导致 OOT 几乎全部止损出场的 bug 根因。",
        )

    def test_intrabar_stop_loss_in_realistic_range(self) -> None:
        """止损率必须在 [0.5%, 5%] 这种 CTA 实战合理范围，避免再次出现 0.1% 这种太紧值。"""
        oot = float(OotEvaluationConfig().intrabar_stop_loss_pct)
        label = _default_label_stop_loss_pct()
        for name, v in (("intrabar_stop_loss_pct", oot), ("label_stop_loss_pct", label)):
            self.assertGreaterEqual(v, 0.005, f"{name}={v} 小于 0.5%，CTA 60min 上必被噪音打掉")
            self.assertLessEqual(v, 0.05, f"{name}={v} 大于 5%，止损过松失去意义")

    def test_intrabar_stop_loss_post_init_rejects_extreme_values(self) -> None:
        # 太小：不合理
        with self.assertRaises(ValueError):
            OotEvaluationConfig(intrabar_stop_loss_pct=0.0001)
        # 太大：不合理
        with self.assertRaises(ValueError):
            OotEvaluationConfig(intrabar_stop_loss_pct=0.5)
        # 合理值不应抛错
        OotEvaluationConfig(intrabar_stop_loss_pct=0.01)
        OotEvaluationConfig(
            intrabar_stop_loss_pct=0.02,
            stop_loss_consistency_tolerance=0.02,
        )


if __name__ == "__main__":
    unittest.main()
