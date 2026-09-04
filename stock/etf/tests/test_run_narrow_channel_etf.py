import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from PIL import Image

from stock.etf.narrow_channel_etf_chart import select_chart_signal_markers
from stock.etf.run_narrow_channel_etf import run_and_write


class RunNarrowChannelEtfTests(unittest.TestCase):
    @staticmethod
    def _write_source(root: Path) -> tuple[Path, Path, Path]:
        dates = pd.bdate_range("2024-01-02", periods=180)
        trend = np.linspace(10.0, 16.0, len(dates))
        close = trend + np.sin(np.arange(len(dates)) / 4.0) * 0.20
        open_ = close - np.sin(np.arange(len(dates))) * 0.05
        source_csv = root / "signals.csv"
        pd.DataFrame(
            {
                "symbol": ["159915.SZ"] * len(dates),
                "datetime": dates,
                "open": open_,
                "high": np.maximum(open_, close) + 0.12,
                "low": np.minimum(open_, close) - 0.12,
                "close": close,
                "volume": np.linspace(1_000.0, 3_000.0, len(dates)),
                "ignored_existing_signal": [True] * len(dates),
            }
        ).to_csv(source_csv, index=False)
        audit_path = root / "source_audit.json"
        audit_path.write_text(
            json.dumps(
                {
                    "symbol": "159915.SZ",
                    "price_adjustment_mode": "point_in_time_adjusted",
                    "backtest_start_date": str(dates[0].date()),
                    "backtest_end_date": str(dates[-1].date()),
                }
            ),
            encoding="utf-8",
        )
        document_path = root / "strategy.md"
        document_path.write_text("# frozen strategy\n", encoding="utf-8")
        return source_csv, audit_path, document_path

    def test_run_writes_research_audits_and_combined_png(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_csv, audit_path, document_path = self._write_source(root)
            output_dir = root / "output"

            summary = run_and_write(
                source_csv=source_csv,
                source_audit_json=audit_path,
                strategy_document=document_path,
                output_dir=output_dir,
                overwrite=True,
            )

            image_path = output_dir / "charts/159915.SZ_周线窄通道快速验证.png"
            for path in (
                output_dir / "signals.csv",
                output_dir / "trades.csv",
                output_dir / "summary.json",
                image_path,
            ):
                self.assertTrue(path.is_file(), path)
                self.assertGreater(path.stat().st_size, 0)
            with Image.open(image_path) as image:
                self.assertEqual(image.size, (2200, 1400))
                image.verify()

            written_signals = pd.read_csv(output_dir / "signals.csv")
            feature_dates = pd.to_datetime(written_signals["max_feature_source_date"])
            signal_dates = pd.to_datetime(written_signals["datetime"])
            self.assertTrue(feature_dates.le(signal_dates).all())
            self.assertEqual(summary["execution_mode"], "research_only")
            self.assertEqual(summary["symbol"], "159915.SZ")
            self.assertEqual(summary["bar_count"], 180)
            self.assertEqual(summary["parameters"]["channel_weeks"], 6)
            self.assertEqual(summary["parameters"]["one_way_cost_rate"], 0.0008)
            self.assertEqual(len(summary["source_sha256"]), 64)
            self.assertEqual(len(summary["strategy_document_sha256"]), 64)
            written_summary = json.loads(
                (output_dir / "summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(written_summary, summary)

    def test_chart_marker_selection_keeps_ignored_long_and_short_signals(self) -> None:
        dates = pd.bdate_range("2026-01-02", periods=3)
        signals = pd.DataFrame(
            {
                "datetime": dates,
                "close": [10.0, 10.5, 10.2],
                "long_signal": [True, False, False],
                "short_signal": [False, True, False],
                "entry_status": ["ignored_position", "filled", ""],
            }
        )

        markers = select_chart_signal_markers(signals)

        self.assertEqual(markers["datetime"].tolist(), [dates[0], dates[1]])
        self.assertEqual(markers["side"].tolist(), ["LONG", "SHORT"])
        self.assertEqual(
            markers["entry_status"].tolist(),
            ["ignored_position", "filled"],
        )

    def test_run_rejects_non_point_in_time_source_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_csv, audit_path, document_path = self._write_source(root)
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            audit["price_adjustment_mode"] = "final_back_adjusted"
            audit_path.write_text(json.dumps(audit), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "point_in_time_adjusted"):
                run_and_write(
                    source_csv=source_csv,
                    source_audit_json=audit_path,
                    strategy_document=document_path,
                    output_dir=root / "output",
                    overwrite=True,
                )

    def test_failed_chart_render_keeps_previous_output_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_csv, audit_path, document_path = self._write_source(root)
            output_dir = root / "output"
            run_and_write(
                source_csv=source_csv,
                source_audit_json=audit_path,
                strategy_document=document_path,
                output_dir=output_dir,
                overwrite=True,
            )
            tracked = (
                output_dir / "signals.csv",
                output_dir / "trades.csv",
                output_dir / "summary.json",
                output_dir / "charts/159915.SZ_周线窄通道快速验证.png",
            )
            previous = {path.relative_to(output_dir): path.read_bytes() for path in tracked}
            source = pd.read_csv(source_csv)
            source.loc[source.index[-1], "close"] += 0.01
            source.loc[source.index[-1], "high"] += 0.01
            source.to_csv(source_csv, index=False)

            with patch(
                "stock.etf.run_narrow_channel_etf.render_narrow_channel_chart",
                side_effect=RuntimeError("render failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "render failed"):
                    run_and_write(
                        source_csv=source_csv,
                        source_audit_json=audit_path,
                        strategy_document=document_path,
                        output_dir=output_dir,
                        overwrite=True,
                    )

            current = {
                path.relative_to(output_dir): path.read_bytes() for path in tracked
            }
            self.assertEqual(current, previous)


if __name__ == "__main__":
    unittest.main()
