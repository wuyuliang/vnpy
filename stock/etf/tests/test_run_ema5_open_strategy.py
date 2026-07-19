import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd
from PIL import Image

from stock.etf.ema5_open_strategy import Ema5OpenConfig
from stock.etf.run_ema5_open_strategy import run_and_write


class RunEma5OpenStrategyTests(unittest.TestCase):
    def test_run_writes_audits_and_winner_holding_style_chart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            daily_path = root / "daily.csv"
            metadata_path = root / "metadata.csv"
            output_dir = root / "output"
            dates = pd.bdate_range("2026-01-02", periods=13)
            opens = [
                10.0,
                11.0,
                12.0,
                13.0,
                14.0,
                15.0,
                16.0,
                17.0,
                18.0,
                19.0,
                18.0,
                16.5,
                15.5,
            ]
            closes = [
                10.0,
                11.0,
                12.0,
                13.0,
                14.0,
                15.0,
                16.0,
                17.0,
                18.0,
                19.0,
                18.0,
                17.0,
                16.0,
            ]
            pd.DataFrame(
                {
                    "symbol": ["159915.SZ"] * 13,
                    "datetime": dates,
                    "open": opens,
                    "high": [
                        max(open_, close) + 0.2
                        for open_, close in zip(opens, closes, strict=True)
                    ],
                    "low": [
                        min(open_, close) - 0.2
                        for open_, close in zip(opens, closes, strict=True)
                    ],
                    "close": closes,
                    "volume": [1_000.0] * 13,
                }
            ).to_csv(daily_path, index=False)
            pd.DataFrame(
                {
                    "symbol": ["159915.SZ"],
                    "name": ["易方达创业板ETF"],
                    "fund_type": ["股票型ETF"],
                }
            ).to_csv(metadata_path, index=False)

            summary = run_and_write(
                daily_csv=daily_path,
                metadata_csv=metadata_path,
                output_dir=output_dir,
                config=Ema5OpenConfig(
                    initial_capital=10_000.0,
                    commission_rate=0.0,
                    min_commission=0.0,
                    slippage_rate=0.0,
                ),
                overwrite=True,
            )

            for filename in (
                "summary.json",
                "equity_curve.csv",
                "signals.csv",
                "trades.csv",
                "positions.csv",
            ):
                self.assertTrue((output_dir / filename).is_file(), filename)
            image_path = output_dir / "charts/0001_159915_SZ_易方达创业板ETF.png"
            self.assertTrue(image_path.is_file())
            with Image.open(image_path) as image:
                self.assertEqual(image.size, (1680, 1000))
            self.assertEqual(summary["actual_start_date"], "2026-01-02")
            self.assertEqual(summary["actual_end_date"], "2026-01-20")
            written_summary = json.loads(
                (output_dir / "summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(written_summary["trade_count"], 2)
            self.assertEqual(written_summary["parameters"]["ema_fast_period"], 5)
            self.assertEqual(written_summary["parameters"]["ema_slow_period"], 10)
            self.assertEqual(
                written_summary["parameters"]["entry_rule"],
                "open>previous_ema5 and previous_ema5>previous_ema10",
            )
            self.assertEqual(
                written_summary["parameters"]["exit_rule"],
                "open<previous_ema10 or previous_ema5<=previous_ema10",
            )


if __name__ == "__main__":
    unittest.main()
