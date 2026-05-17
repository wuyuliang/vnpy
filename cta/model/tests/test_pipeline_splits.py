from __future__ import annotations

from cta.model.pipeline_splits import _normalize_intervals


def test_pipeline_splits_normalize_intervals() -> None:
    out = _normalize_intervals(("day", "60min", "minute30", "min"))
    assert out
    assert "day" in out

