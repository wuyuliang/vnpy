"""Tests for range mean-reversion candidate generation."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

import numpy as np
import pandas as pd

from cta.config.mean_reversion_setup_config import MeanReversionSetupConfig
from cta.model.feature.candidate_training_dataset import _mean_reversion_cfg_from_enabled_cells
from cta.strategy.baseline_candidate_gen import generate_candidate_opportunities
from cta.strategy.baseline_setup_detection import _build_raw_setup_candidates
from cta.strategy.baseline_strategies import create_baseline_strategy
from cta.strategy.mean_reversion_range_setup import MeanReversionRangeSetupGenerator
from cta.strategy.skill_tight_range_breakout import ContractSpec


def _bars() -> pd.DataFrame:
    dt = pd.date_range("2024-01-01", periods=4, freq="D")
    return pd.DataFrame(
        {
            "datetime": dt,
            "symbol": "IF0",
            "open": [100.0, 102.0, 98.0, 101.0],
            "high": [101.0, 105.0, 99.0, 103.0],
            "low": [99.0, 101.0, 95.0, 100.0],
            "close": [100.0, 104.0, 96.0, 101.0],
            "volume": [1000.0] * 4,
            "open_interest": [10_000.0] * 4,
            "atr14": [2.0] * 4,
            "regime_label": ["range"] * 4,
            "mr_sma": [100.0] * 4,
            "mr_zscore": [0.0, 2.5, -2.5, 0.0],
            "mr_bb_upper": [104.0] * 4,
            "mr_bb_lower": [96.0] * 4,
            "mr_rsi": [50.0, 75.0, 25.0, 50.0],
            "mr_adx": [10.0, 15.0, 15.0, 10.0],
            "mr_signal_strength": [0.0, -1.0, 1.0, 0.0],
        }
    )


def _enabled_cfg() -> MeanReversionSetupConfig:
    return MeanReversionSetupConfig(
        use_mean_reversion_setup=True,
        enabled_by_cluster_interval={"index|day": True},
    )


class TestMeanReversionRangeSetup(unittest.TestCase):
    def test_default_disabled(self) -> None:
        out = MeanReversionRangeSetupGenerator(MeanReversionSetupConfig()).generate(
            _bars(),
            cluster="index",
            interval="day",
        )
        self.assertTrue(out.empty)

    def test_generator_emits_long_and_short_with_target_and_stop(self) -> None:
        out = MeanReversionRangeSetupGenerator(_enabled_cfg()).generate(
            _bars(),
            cluster="index",
            interval="day",
        )
        self.assertEqual(set(out["side"]), {"long", "short"})
        self.assertTrue((out["signal_type"] == "mean_reversion_range").all())

        short = out.loc[out["side"] == "short"].iloc[0]
        self.assertLess(float(short["target_price"]), float(short["entry_price"]))
        self.assertLess(float(short["entry_price"]), float(short["stop_price"]))

        long = out.loc[out["side"] == "long"].iloc[0]
        self.assertLess(float(long["stop_price"]), float(long["entry_price"]))
        self.assertLess(float(long["entry_price"]), float(long["target_price"]))

    def test_generator_blocks_high_adx_non_range_and_disabled_cluster(self) -> None:
        bars = _bars()
        bars.loc[1, "mr_adx"] = 30.0
        bars.loc[2, "regime_label"] = "trend_down"
        got = MeanReversionRangeSetupGenerator(_enabled_cfg()).generate(
            bars,
            cluster="index",
            interval="day",
        )
        self.assertTrue(got.empty)

        off_cluster = MeanReversionRangeSetupGenerator(_enabled_cfg()).generate(
            _bars(),
            cluster="black",
            interval="day",
        )
        self.assertTrue(off_cluster.empty)

    def test_raw_setup_detection_emits_mean_reversion_candidate(self) -> None:
        raw = _build_raw_setup_candidates(
            _bars(),
            1,
            "mean_reversion_range",
            SimpleNamespace(tick_size=0.2),
            "both",
            mean_reversion_cfg=_enabled_cfg(),
            cluster="index",
            interval="day",
        )
        self.assertEqual(len(raw), 1)
        self.assertEqual(raw[0]["side"], "short")
        self.assertEqual(raw[0]["order_type"], "market")

    def test_candidate_table_carries_mean_reversion_features(self) -> None:
        out = generate_candidate_opportunities(
            frame=_bars(),
            symbol="IF0",
            exchange="CFFEX",
            interval="day",
            signal_type="mean_reversion_range",
            mean_reversion_cfg=_enabled_cfg(),
            feature_columns=("mr_zscore", "mr_rsi", "mr_adx", "mr_signal_strength"),
        )
        self.assertFalse(out.empty)
        self.assertEqual(set(out["signal_type"]), {"mean_reversion_range"})
        self.assertIn("feature_mr_signal_strength", out.columns)

    def test_candidate_dataset_cli_builds_opt_in_config_from_cells(self) -> None:
        cfg = _mean_reversion_cfg_from_enabled_cells(["index|day,metal|60min"])
        self.assertTrue(cfg.is_enabled("index", "day"))
        self.assertTrue(cfg.is_enabled("metal", "minute60"))
        self.assertFalse(cfg.is_enabled("black", "day"))

    def test_strategy_factory_passes_explicit_interval_to_gray_gate(self) -> None:
        cfg = MeanReversionSetupConfig(
            use_mean_reversion_setup=True,
            enabled_by_cluster_interval={"index|60min": True},
        )
        contract = ContractSpec(
            symbol="IF0",
            exchange="CFFEX",
            multiplier=300.0,
            tick_size=0.2,
            commission_rate=0.0001,
            slippage_ticks=1.0,
        )
        strategy = create_baseline_strategy(
            "mean_reversion_range",
            _bars(),
            contract,
            "both",
            mean_reversion_cfg=cfg,
            interval="60min",
        )
        orders = strategy.on_bar(1, _bars().iloc[1], position=0)
        self.assertEqual(len(orders), 1)
        self.assertEqual(str(orders[0]["side"]), "short")


if __name__ == "__main__":
    unittest.main()
