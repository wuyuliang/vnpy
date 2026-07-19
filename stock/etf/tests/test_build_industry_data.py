import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

import pandas as pd

from stock.etf.build_industry_data import atomic_write_csv, build_industry_csv
from stock.etf.industry import INDUSTRY_OUTPUT_COLUMNS


class BuildIndustryDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.daily_path = self.root / "daily.csv"
        self.metadata_path = self.root / "metadata.csv"
        self.output_path = self.root / "industry.csv"

        dates = pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-05"])
        pd.DataFrame(
            {
                "symbol": ["A.SH"] * 3,
                "datetime": dates,
                "pre_close": [10.0, 10.0, 10.5],
                "open": [10.0, 10.2, 10.6],
                "high": [10.2, 10.7, 10.8],
                "low": [9.9, 10.1, 10.4],
                "close": [10.0, 10.5, 10.7],
                "volume": [10.0, 11.0, 12.0],
                "turnover": [1_000.0, 1_100.0, 1_200.0],
            }
        ).to_csv(self.daily_path, index=False)
        pd.DataFrame(
            {
                "symbol": ["A.SH"],
                "industry": ["materials"],
                "list_date": ["2025-01-01"],
            }
        ).to_csv(self.metadata_path, index=False)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_build_is_offline_and_omits_market_value_fields(self) -> None:
        with patch(
            "stock.etf.build_industry_data.TushareEtfDownloader",
            side_effect=AssertionError("Tushare must not be initialized"),
            create=True,
        ) as downloader:
            result = build_industry_csv(
                daily_path=self.daily_path,
                metadata_path=self.metadata_path,
                output_path=self.output_path,
            )

        written = pd.read_csv(self.output_path)
        downloader.assert_not_called()
        self.assertEqual(result.columns.tolist(), INDUSTRY_OUTPUT_COLUMNS)
        self.assertEqual(written.columns.tolist(), INDUSTRY_OUTPUT_COLUMNS)
        self.assertEqual(len(written), 3)
        self.assertFalse(
            {
                "market_value_etf_count",
                "share_coverage_ratio",
                "total_market_value",
            }
            & set(written.columns)
        )

    def test_atomic_write_csv_replaces_target_without_temporary_file(self) -> None:
        self.output_path.write_text("old\n", encoding="utf-8")
        frame = pd.DataFrame({"value": [1, 2]})

        atomic_write_csv(frame, self.output_path)

        pd.testing.assert_frame_equal(pd.read_csv(self.output_path), frame)
        self.assertFalse(self.output_path.with_suffix(".csv.tmp").exists())

    def test_atomic_write_csv_uses_unique_temporary_files(self) -> None:
        barrier = Barrier(2)
        original_to_csv = pd.DataFrame.to_csv

        def synchronized_to_csv(
            frame: pd.DataFrame, path: Path, *args: object, **kwargs: object
        ) -> None:
            original_to_csv(frame, path, *args, **kwargs)
            barrier.wait(timeout=5)

        frames = [pd.DataFrame({"value": [1]}), pd.DataFrame({"value": [2]})]
        with (
            patch.object(pd.DataFrame, "to_csv", new=synchronized_to_csv),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            futures = [
                executor.submit(atomic_write_csv, frame, self.output_path)
                for frame in frames
            ]
            for future in futures:
                future.result(timeout=5)

        self.assertIn(pd.read_csv(self.output_path).loc[0, "value"], {1, 2})
        self.assertEqual(list(self.root.glob("*.tmp")), [])
        self.assertEqual(list(self.root.glob(".*.tmp")), [])

    def test_build_rejects_missing_metadata(self) -> None:
        pd.DataFrame({"symbol": ["OTHER.SH"], "industry": ["materials"]}).to_csv(
            self.metadata_path, index=False
        )

        with self.assertRaisesRegex(ValueError, "missing industry metadata.*A.SH"):
            build_industry_csv(
                daily_path=self.daily_path,
                metadata_path=self.metadata_path,
                output_path=self.output_path,
            )

    def test_build_preserves_history_before_requested_start(self) -> None:
        result = build_industry_csv(
            daily_path=self.daily_path,
            metadata_path=self.metadata_path,
            output_path=self.output_path,
            start="2026-01-05",
        )

        self.assertEqual(result["datetime"].tolist(), [pd.Timestamp("2026-01-05")])
        self.assertAlmostEqual(result.loc[0, "close"], 107.0)
        self.assertAlmostEqual(result.loc[0, "ema_1"], 107.0)
        self.assertEqual(result.loc[0, "turnover_sum_3"], 3_300.0)

    def test_build_rejects_duplicate_symbol_dates(self) -> None:
        daily = pd.read_csv(self.daily_path)
        duplicate = daily.iloc[[0]].copy()
        duplicate["turnover"] = 9_999.0
        pd.concat([daily, duplicate], ignore_index=True).to_csv(
            self.daily_path, index=False
        )

        with self.assertRaisesRegex(ValueError, "duplicate symbol/date"):
            build_industry_csv(
                daily_path=self.daily_path,
                metadata_path=self.metadata_path,
                output_path=self.output_path,
            )


if __name__ == "__main__":
    unittest.main()
