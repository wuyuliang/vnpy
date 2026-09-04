from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from cta.strategy.brooks.cycle_v1.backtest.download_universe import (
    load_ranked_symbols,
    select_ranked_universe,
)


def _write_ranking(path: Path) -> None:
    pd.DataFrame(
        {
            "symbol": ["CU0", "RB0", "AL0", "ZN0", "IF0"],
            "exchange": ["SHFE", "SHFE", "SHFE", "SHFE", "CFFEX"],
            "research_rank": [2, 1, 3, 4, 5],
        }
    ).to_csv(path, index=False)


def _write_day(path: Path, closes: list[float]) -> None:
    pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=len(closes), freq="D"),
            "close": closes,
        }
    ).to_csv(path, index=False)


def _contract_reference() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "root_symbol": ["LC", "IF"],
            "exchange": ["GFEX", "CFFEX"],
            "list_date": ["2023-07-21", "2010-04-16"],
            "delist_date": [None, None],
        }
    )


def test_load_ranking_sorts_and_normalizes_continuous_symbols(tmp_path: Path) -> None:
    ranking = tmp_path / "ranking.csv"
    _write_ranking(ranking)

    loaded = load_ranked_symbols(ranking)

    assert [item.root_symbol for item in loaded] == ["RB", "CU", "AL", "ZN", "IF"]
    assert [item.exchange for item in loaded] == ["SHFE", "SHFE", "SHFE", "SHFE", "CFFEX"]
    assert [item.research_rank for item in loaded] == [1, 2, 3, 4, 5]


def test_load_ranking_rejects_duplicate_roots_after_normalization(
    tmp_path: Path,
) -> None:
    ranking = tmp_path / "ranking.csv"
    pd.DataFrame(
        {
            "symbol": ["RB0", "RB"],
            "exchange": ["SHFE", "SHF"],
            "research_rank": [1, 2],
        }
    ).to_csv(ranking, index=False)

    with pytest.raises(ValueError, match="duplicate ranking root"):
        load_ranked_symbols(ranking)


def test_selection_unions_explicit_top_n_and_all_ema_eligible_stably(
    tmp_path: Path,
) -> None:
    ranking = tmp_path / "ranking.csv"
    day_root = tmp_path / "day"
    day_root.mkdir()
    _write_ranking(ranking)
    _write_day(day_root / "RB0.csv", [10, 10, 10, 10, 10, 10, 10])
    _write_day(day_root / "CU0.csv", [10, 10, 10, 10, 10, 10, 10])
    _write_day(day_root / "AL0.csv", [1, 2, 3, 4, 5, 6, 7])
    _write_day(day_root / "ZN0.csv", [7, 6, 5, 4, 3, 2, 1])
    _write_day(day_root / "IF0.csv", [10, 10, 10, 10, 10, 10, 10])

    selection = select_ranked_universe(
        explicit=["LC", "RB0.SHFE"],
        top_n=2,
        include_ema_eligible=True,
        ranking_csv=ranking,
        day_root=day_root,
        start=date(2026, 1, 5),
        end=date(2026, 1, 7),
        contract_reference=_contract_reference(),
    )

    assert [item.root_symbol for item in selection.selected] == ["LC", "RB", "CU", "AL"]
    assert [item.exchange for item in selection.selected] == ["GFEX", "SHFE", "SHFE", "SHFE"]
    assert selection.selected[0].sources == ("explicit",)
    assert selection.selected[1].sources == ("explicit", "top_n")
    assert selection.top_n == ("RB", "CU")
    assert selection.ema_eligible == ("AL",)
    assert [
        (item.root_symbol, item.reason_code) for item in selection.rejections
    ] == [
        ("RB", "NO_EMA_ELIGIBLE_DATE"),
        ("CU", "NO_EMA_ELIGIBLE_DATE"),
        ("ZN", "NO_EMA_ELIGIBLE_DATE"),
        ("IF", "NO_EMA_ELIGIBLE_DATE"),
    ]


def test_ema_selection_uses_only_previous_completed_daily_bar(tmp_path: Path) -> None:
    ranking = tmp_path / "ranking.csv"
    day_root = tmp_path / "day"
    day_root.mkdir()
    pd.DataFrame(
        {"symbol": ["AL0"], "exchange": ["SHFE"], "research_rank": [1]}
    ).to_csv(ranking, index=False)
    _write_day(day_root / "AL0.csv", [10, 10, 10, 10, 10, 100])

    selection = select_ranked_universe(
        explicit=[],
        top_n=0,
        include_ema_eligible=True,
        ranking_csv=ranking,
        day_root=day_root,
        start=date(2026, 1, 6),
        end=date(2026, 1, 6),
        contract_reference=pd.DataFrame(),
    )

    assert selection.selected == ()
    assert selection.ema_eligible == ()
    assert selection.rejections[0].reason_code == "NO_EMA_ELIGIBLE_DATE"


def test_future_daily_changes_do_not_change_earlier_ema_selection(tmp_path: Path) -> None:
    ranking = tmp_path / "ranking.csv"
    day_root = tmp_path / "day"
    day_root.mkdir()
    pd.DataFrame(
        {"symbol": ["AL0"], "exchange": ["SHFE"], "research_rank": [1]}
    ).to_csv(ranking, index=False)
    day_path = day_root / "AL0.csv"
    _write_day(day_path, [1, 2, 3, 4, 5, 6, 7])

    before = select_ranked_universe(
        explicit=[],
        top_n=0,
        include_ema_eligible=True,
        ranking_csv=ranking,
        day_root=day_root,
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        contract_reference=pd.DataFrame(),
    )
    _write_day(day_path, [1, 2, 3, 4, 5, 600, 700])
    after = select_ranked_universe(
        explicit=[],
        top_n=0,
        include_ema_eligible=True,
        ranking_csv=ranking,
        day_root=day_root,
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        contract_reference=pd.DataFrame(),
    )

    assert before.selected == after.selected
    assert [item.root_symbol for item in before.selected] == ["AL"]


def test_missing_day_data_is_audited_without_blocking_top_n(tmp_path: Path) -> None:
    ranking = tmp_path / "ranking.csv"
    day_root = tmp_path / "day"
    day_root.mkdir()
    pd.DataFrame(
        {
            "symbol": ["RB0", "AL0"],
            "exchange": ["SHFE", "SHFE"],
            "research_rank": [1, 2],
        }
    ).to_csv(ranking, index=False)
    _write_day(day_root / "RB0.csv", [1, 2, 3, 4, 5])

    selection = select_ranked_universe(
        explicit=[],
        top_n=1,
        include_ema_eligible=True,
        ranking_csv=ranking,
        day_root=day_root,
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        contract_reference=pd.DataFrame(),
    )

    assert [item.root_symbol for item in selection.selected] == ["RB"]
    assert [(item.root_symbol, item.reason_code) for item in selection.rejections] == [
        ("AL", "MISSING_DAY_DATA")
    ]


def test_bad_day_data_is_audited_without_blocking_other_symbols(
    tmp_path: Path,
) -> None:
    ranking = tmp_path / "ranking.csv"
    day_root = tmp_path / "day"
    day_root.mkdir()
    pd.DataFrame(
        {
            "symbol": ["RB0", "AL0"],
            "exchange": ["SHFE", "SHFE"],
            "research_rank": [1, 2],
        }
    ).to_csv(ranking, index=False)
    _write_day(day_root / "RB0.csv", [1, 2, 3, 4, 5])
    (day_root / "AL0.csv").write_text(
        'datetime,close\n2026-01-01,"1\n', encoding="utf-8"
    )

    selection = select_ranked_universe(
        explicit=[],
        top_n=1,
        include_ema_eligible=True,
        ranking_csv=ranking,
        day_root=day_root,
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        contract_reference=pd.DataFrame(),
    )

    assert [item.root_symbol for item in selection.selected] == ["RB"]
    assert [(item.root_symbol, item.reason_code) for item in selection.rejections] == [
        ("AL", "DAY_DATA_ERROR")
    ]


def test_unresolved_explicit_symbol_is_rejected_without_guessing(tmp_path: Path) -> None:
    ranking = tmp_path / "ranking.csv"
    day_root = tmp_path / "day"
    day_root.mkdir()
    pd.DataFrame(
        {"symbol": ["RB0"], "exchange": ["SHFE"], "research_rank": [1]}
    ).to_csv(ranking, index=False)

    selection = select_ranked_universe(
        explicit=["UNKNOWN"],
        top_n=0,
        include_ema_eligible=False,
        ranking_csv=ranking,
        day_root=day_root,
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        contract_reference=pd.DataFrame(),
    )

    assert selection.selected == ()
    assert selection.rejections[0].reason_code == "UNRESOLVED_EXCHANGE"


def test_explicit_exchange_conflicting_with_ranking_is_rejected(
    tmp_path: Path,
) -> None:
    ranking = tmp_path / "ranking.csv"
    day_root = tmp_path / "day"
    day_root.mkdir()
    pd.DataFrame(
        {"symbol": ["RB0"], "exchange": ["SHFE"], "research_rank": [1]}
    ).to_csv(ranking, index=False)

    selection = select_ranked_universe(
        explicit=["RB0.DCE"],
        top_n=1,
        include_ema_eligible=False,
        ranking_csv=ranking,
        day_root=day_root,
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        contract_reference=pd.DataFrame(),
    )

    assert selection.selected == ()
    assert selection.rejections[0].reason_code == "EXPLICIT_EXCHANGE_CONFLICT"


def test_invalid_local_exchange_is_audited_without_aborting_selection(
    tmp_path: Path,
) -> None:
    ranking = tmp_path / "ranking.csv"
    day_root = tmp_path / "day"
    day_root.mkdir()
    pd.DataFrame(
        {"symbol": ["RB0"], "exchange": ["SHFE"], "research_rank": [1]}
    ).to_csv(ranking, index=False)
    pd.DataFrame({"exchange": ["UNKNOWN"]}).to_csv(
        day_root / "LC0.csv", index=False
    )

    selection = select_ranked_universe(
        explicit=["LC"],
        top_n=1,
        include_ema_eligible=False,
        ranking_csv=ranking,
        day_root=day_root,
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        contract_reference=pd.DataFrame(),
    )

    assert [item.root_symbol for item in selection.selected] == ["RB"]
    assert selection.rejections[0].reason_code == "EXCHANGE_RESOLUTION_ERROR"


def test_duplicate_explicit_root_with_conflicting_exchanges_blocks_root(
    tmp_path: Path,
) -> None:
    ranking = tmp_path / "ranking.csv"
    day_root = tmp_path / "day"
    day_root.mkdir()
    pd.DataFrame(
        {"symbol": ["RB0"], "exchange": ["SHFE"], "research_rank": [1]}
    ).to_csv(ranking, index=False)

    selection = select_ranked_universe(
        explicit=["LC0.GFEX", "LC0.SHFE"],
        top_n=0,
        include_ema_eligible=False,
        ranking_csv=ranking,
        day_root=day_root,
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        contract_reference=pd.DataFrame(),
    )

    assert selection.selected == ()
    assert selection.explicit == ("LC",)
    assert selection.rejections[0].reason_code == "EXPLICIT_EXCHANGE_CONFLICT"
