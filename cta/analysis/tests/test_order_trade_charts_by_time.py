import csv
from pathlib import Path

from cta.analysis.order_trade_charts_by_time import order_charts_by_trade_time


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_order_charts_by_trade_time_groups_by_signal_type_oldest_first(tmp_path) -> None:
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
                "symbol": "AL0",
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
                "signal_type": "bull_pullback_continuation",
            },
        ],
    )
    output_dir = tmp_path / "charts_order_time"

    summary = order_charts_by_trade_time(
        index_csv=index_csv,
        trade_csv=trade_csv,
        charts_root=tmp_path,
        output_dir=output_dir,
        overwrite=True,
    )

    ordered = sorted((output_dir / "bull_pullback_continuation").glob("*.png"))
    assert summary["linked_images"] == 2
    assert summary["signal_type_counts"] == {"bull_pullback_continuation": 2}
    assert ordered[0].name == "000001_20240102_090000_AL0_long_executed_rank_001.png"
    assert ordered[1].name == "000002_20240103_210000_CU0_short_blocked_trade_filter_rank_002.png"
    assert ordered[0].read_bytes() == b"first"
    assert ordered[1].read_bytes() == b"second"
