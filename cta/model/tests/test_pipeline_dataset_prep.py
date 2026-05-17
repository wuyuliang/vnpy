from __future__ import annotations

import pandas as pd

from cta.model.pipeline_dataset_prep import _ensure_training_columns


def test_pipeline_dataset_prep_ensure_training_columns() -> None:
    df = pd.DataFrame({"symbol": ["RB0"], "datetime": ["2020-01-01"], "entry_price": [100.0]})
    out = _ensure_training_columns(df)
    assert "symbol" in out.columns
    assert "candidate_status" in out.columns
    assert "future_mfe_atr" in out.columns
    assert "future_mae_atr" in out.columns
