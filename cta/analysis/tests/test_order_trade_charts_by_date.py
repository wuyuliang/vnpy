import csv
from pathlib import Path

from cta.analysis.order_trade_charts_by_date import order_charts_by_trade_date


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_order_charts_by_trade_date_groups_cta_symbols_by_day_and_signal_type(
    tmp_path,
) -> None:
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir()
    (charts_dir / "rank_001.png").write_bytes(b"first")
    (charts_dir / "rank_002.png").write_bytes(b"second")

    index_csv = tmp_path / "index.csv"
    _write_csv(
        index_csv,
        [
            {"source_row": "1", "image_path": "charts/rank_001.png"},
            {"source_row": "2", "image_path": "charts/rank_002.png"},
        ],
    )
    trade_csv = tmp_path / "trades.csv"
    _write_csv(
        trade_csv,
        [
            {
                "entry_fill_datetime": "2024-01-02 09:00:00",
                "entry_datetime": "2024-01-02 00:00:00",
                "symbol": "AG0",
                "side": "long",
                "execution_status": "executed",
                "signal_type": "bull_pullback_continuation",
            },
            {
                "entry_fill_datetime": "2024-01-03 21:00:00",
                "entry_datetime": "2024-01-03 00:00:00",
                "symbol": "CU0",
                "side": "short",
                "execution_status": "blocked_trade_filter",
                "signal_type": "breakout_pullback_continuation",
            },
        ],
    )
    output_dir = tmp_path / "20260713"

    summary = order_charts_by_trade_date(
        index_csv=index_csv,
        trade_csv=trade_csv,
        charts_root=tmp_path,
        output_dir=output_dir,
        overwrite=True,
    )

    ag_files = sorted(
        (output_dir / "20240102" / "bull_pullback_continuation").glob("*.png")
    )
    cu_files = sorted(
        (output_dir / "20240103" / "breakout_pullback_continuation").glob("*.png")
    )

    assert summary["linked_images"] == 2
    assert summary["trade_day_counts"] == {"20240102": 1, "20240103": 1}
    assert summary["signal_type_counts"] == {
        "breakout_pullback_continuation": 1,
        "bull_pullback_continuation": 1,
    }
    assert ag_files[0].name == "000001_20240102_090000_AG0_long_executed_rank_001.png"
    assert cu_files[0].name == (
        "000001_20240103_210000_CU0_short_blocked_trade_filter_rank_002.png"
    )
    assert ag_files[0].read_bytes() == b"first"
    assert cu_files[0].read_bytes() == b"second"
    assert (output_dir / "order_summary.json").exists()
    assert (output_dir / "20240102" / "index.csv").exists()
    assert (output_dir / "20240103" / "index.csv").exists()
