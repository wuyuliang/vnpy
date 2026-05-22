from __future__ import annotations

from cta.model.dataset.pipeline_symbol_ranking import SYMBOLS_RANKING_PATH


def test_pipeline_symbol_ranking_path_exists() -> None:
    assert SYMBOLS_RANKING_PATH.exists()

