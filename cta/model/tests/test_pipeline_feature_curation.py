from __future__ import annotations

from cta.model.pipeline_feature_curation import _safe_name


def test_pipeline_feature_curation_safe_name() -> None:
    assert _safe_name("Cluster Black") == "cluster_black"

