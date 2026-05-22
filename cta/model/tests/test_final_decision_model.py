from __future__ import annotations

import re
from pathlib import Path

from cta.model.training import final_decision_model as fdm


def test_quick_cv_auc_uses_time_series_split() -> None:
    source = Path(fdm.__file__).read_text(encoding="utf-8")
    assert "TimeSeriesSplit" in source
    assert "KFold(" not in source
    assert "shuffle=True" not in source


def test_meta_train_is_not_built_from_in_sample_base_predictions() -> None:
    src = Path("cta/model/orchestration/pipeline_run.py").read_text(encoding="utf-8")
    assert re.search(r"predict_proba\(train_df", src) is None
