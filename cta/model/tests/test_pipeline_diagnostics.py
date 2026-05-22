from __future__ import annotations

import pandas as pd

from cta.model.reporting.pipeline_diagnostics import _build_valid_test_gap_alerts


def test_pipeline_diagnostics_gap_alerts() -> None:
    metrics = pd.DataFrame(
        [
            {"signal_type": "a", "window_id": 0, "model": "m", "split": "valid", "auc": 0.9},
            {"signal_type": "a", "window_id": 0, "model": "m", "split": "test", "auc": 0.7},
        ]
    )
    out = _build_valid_test_gap_alerts(metrics, max_gap=0.1)
    assert len(out) == 1

