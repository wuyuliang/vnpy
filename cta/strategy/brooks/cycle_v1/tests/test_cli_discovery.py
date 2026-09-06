from __future__ import annotations

from pathlib import Path

import pandas as pd

from cta.strategy.brooks.cycle_v1.backtest.data_loader import discover_symbols


def _write_source(path: Path, contract: str, exchange: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "ts_code": [contract],
            "exchange": [exchange],
            "datetime": ["2026-01-05 09:01:00"],
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.0],
            "volume": [1.0],
            "open_interest": [1.0],
            "turnover": [100.0],
        }
    ).to_parquet(path, index=False)


def test_discovery_deduplicates_alias_directories_and_keeps_best_coverage(
    tmp_path: Path,
) -> None:
    _write_source(tmp_path / "CU0.SHF" / "2026-01-05.parquet", "CU2603.SHF", "SHFE")
    _write_source(tmp_path / "CU0.SHF" / "2026-01-06.parquet", "CU2603.SHF", "SHFE")
    _write_source(tmp_path / "CU_small" / "2026-01-05.parquet", "CU2603.SHF", "SHFE")
    _write_source(tmp_path / "RB" / "2026-01-05.parquet", "RB2605.SHF", "SHFE")

    discovered = discover_symbols(tmp_path)

    assert [item.root_symbol for item in discovered] == ["CU", "RB"]
    cu = discovered[0]
    assert cu.vt_symbol == "CU0.SHFE"
    assert cu.source_directory == tmp_path / "CU0.SHF"
    assert cu.alias_directories == (tmp_path / "CU_small",)


def test_discovery_normalizes_tushare_zce_suffix_to_czce(tmp_path: Path) -> None:
    _write_source(
        tmp_path / "MA" / "2026-01-05.parquet",
        "MA605.ZCE",
        "CZCE",
    )

    discovered = discover_symbols(tmp_path)

    assert discovered[0].root_symbol == "MA"
    assert discovered[0].exchange == "CZCE"
    assert discovered[0].vt_symbol == "MA0.CZCE"


def test_discovery_skips_auxiliary_parquet_without_contract_identity(
    tmp_path: Path,
) -> None:
    auxiliary = tmp_path / "OI" / "0000-metadata.parquet"
    auxiliary.parent.mkdir(parents=True)
    pd.DataFrame({"note": ["legacy auxiliary file"]}).to_parquet(
        auxiliary,
        index=False,
    )
    _write_source(
        tmp_path / "OI" / "2026-01-05.parquet",
        "OI605.ZCE",
        "CZCE",
    )
    _write_source(
        tmp_path / "RB" / "2026-01-05.parquet",
        "RB2605.SHF",
        "SHFE",
    )

    discovered = discover_symbols(tmp_path)

    assert [item.root_symbol for item in discovered] == ["OI", "RB"]
