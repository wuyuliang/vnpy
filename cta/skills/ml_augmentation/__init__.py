"""§09 ml_augmentation implementations."""
from __future__ import annotations

from .feature_store import FeatureMeta, load_features_as_of, register_feature, verify_online_offline
from .mfe_mae_prediction import MFEMAEConfig, build_mfe_mae_labels, predict_mfe_mae, train_quantile_models
from .regime_classifier import RegimeConfig, build_regime_labels, predict_regime
from .trade_filter_model import FilterConfig, apply_gate, build_dataset, train_gate
from .walk_forward_validation import WFConfig, run_walk_forward, walk_forward_splits

__all__ = [
    "FeatureMeta",
    "FilterConfig",
    "MFEMAEConfig",
    "RegimeConfig",
    "WFConfig",
    "apply_gate",
    "build_dataset",
    "build_mfe_mae_labels",
    "build_regime_labels",
    "load_features_as_of",
    "predict_mfe_mae",
    "predict_regime",
    "register_feature",
    "run_walk_forward",
    "train_gate",
    "train_quantile_models",
    "verify_online_offline",
    "walk_forward_splits",
]

