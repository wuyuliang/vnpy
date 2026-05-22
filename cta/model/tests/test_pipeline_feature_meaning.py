from __future__ import annotations

from cta.model.dataset.pipeline_feature_meaning import _feature_meaning


def test_pipeline_feature_meaning_fallback() -> None:
    assert _feature_meaning("feature_close")

