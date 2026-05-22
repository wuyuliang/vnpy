from __future__ import annotations

from cta.model.dataset.pipeline_meta_features import _build_final_decision_features, _regime_to_code


def test_pipeline_meta_features_exports() -> None:
    assert callable(_regime_to_code)
    assert callable(_build_final_decision_features)

