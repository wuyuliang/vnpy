from __future__ import annotations

import numpy as np
import pandas as pd

from cta.model.training.bull_regime_strength_model import (
    BullRegimeStrengthModel,
    _derive_bull_attack_label,
)
from cta.model.training.pyramid_eligibility_model import (
    PyramidEligibilityModel,
    _derive_pyramid_label,
)
from cta.model.training.trend_persistence_model import (
    TrendPersistenceModel,
    _derive_hold_extend_label,
)


def _mk_df(n: int = 48) -> pd.DataFrame:
    idx = np.arange(n, dtype=float)
    return pd.DataFrame(
        {
            "feature_trend_score": np.sin(idx / 7.0) + 0.6,
            "feature_breakout_score": np.cos(idx / 9.0) + 0.4,
            "feature_tr_range_atr": 0.8 + 0.05 * np.sin(idx / 11.0),
            "feature_volume": 1000.0 + 20.0 * idx,
            "is_executed": (idx % 3 != 0).astype(int),
            "future_mfe_atr": 0.8 + 0.03 * idx,
            "future_mae_atr": 0.3 + 0.01 * (idx % 5),
            "regime_label": np.where(idx % 4 == 0, "trend_down", "trend_up"),
        }
    )


def test_bull_regime_strength_model_fit_predict() -> None:
    df = _mk_df()
    feats = ["feature_trend_score", "feature_breakout_score", "feature_tr_range_atr", "feature_volume"]
    model = BullRegimeStrengthModel(random_state=7).fit(df, feature_columns=feats)
    pred = model.predict(df, feature_columns=feats)
    assert {"bull_strength_score", "bull_mode"} <= set(pred.columns)
    assert pred["bull_strength_score"].between(0.0, 1.0).all()


def test_trend_persistence_model_fit_predict() -> None:
    df = _mk_df()
    feats = ["feature_trend_score", "feature_breakout_score", "feature_tr_range_atr"]
    model = TrendPersistenceModel(random_state=7).fit(df, feature_columns=feats)
    pred = model.predict(df, feature_columns=feats)
    assert {"hold_extend_score", "recommended_horizon_extension_bars"} <= set(pred.columns)
    assert (pred["recommended_horizon_extension_bars"] >= 0).all()


def test_pyramid_eligibility_model_fit_predict() -> None:
    df = _mk_df()
    feats = ["feature_trend_score", "feature_breakout_score", "feature_tr_range_atr"]
    model = PyramidEligibilityModel(random_state=7).fit(df, feature_columns=feats)
    pred = model.predict(df, feature_columns=feats)
    assert {"pyramid_add_score", "pyramid_size_mult"} <= set(pred.columns)
    assert (pred["pyramid_size_mult"] >= 0.0).all()


def test_pyramid_eligibility_model_respects_configurable_size_thresholds() -> None:
    df = _mk_df()
    feats = ["feature_trend_score", "feature_breakout_score", "feature_tr_range_atr"]
    model = PyramidEligibilityModel(
        random_state=7,
        add_score_low_threshold=0.1,
        add_score_high_threshold=0.2,
        size_mult_low=0.3,
        size_mult_high=0.7,
    ).fit(df, feature_columns=feats)
    pred = model.predict(df, feature_columns=feats)
    vals = set(np.round(pred["pyramid_size_mult"].astype(float).to_numpy(), 6).tolist())
    assert vals <= {0.0, 0.3, 0.7}


def test_module_label_helpers_accept_configurable_edge_thresholds() -> None:
    df = pd.DataFrame(
        {
            "is_executed": [1],
            "future_mfe_atr": [1.0],
            "future_mae_atr": [0.0],
            "regime_label": ["trend_up"],
        }
    )

    assert _derive_bull_attack_label(df, edge_threshold=1.5).tolist() == [0]
    assert _derive_hold_extend_label(df, edge_threshold=1.5).tolist() == [0]
    assert _derive_pyramid_label(df, edge_threshold=1.5).tolist() == [0]


def test_hold_extend_label_is_candidate_level_not_executed_only() -> None:
    df = pd.DataFrame(
        {
            "is_executed": [0, 0],
            "future_mfe_atr": [2.0, 0.2],
            "future_mae_atr": [0.1, 0.5],
            "regime_label": ["trend_up", "trend_up"],
        }
    )
    label = _derive_hold_extend_label(df, edge_threshold=0.8).tolist()
    assert label == [1, 0]
