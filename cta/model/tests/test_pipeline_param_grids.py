from __future__ import annotations

from cta.model.training.pipeline_param_grids import _auc_gap, _select_best_param_trial


def test_pipeline_param_grid_helpers_basic() -> None:
    assert abs(_auc_gap(0.9, 0.85) - 0.05) < 1e-12
    picked = _select_best_param_trial(
        [
            {"name": "a", "train_auc": 0.8, "valid_auc": 0.79},
            {"name": "b", "train_auc": 0.82, "valid_auc": 0.81},
        ],
        max_auc_gap=0.03,
    )
    assert picked["name"] == "b"

