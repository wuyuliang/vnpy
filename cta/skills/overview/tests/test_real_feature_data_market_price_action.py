"""Real feature-data integration tests: market regime + price action modules."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Optional
import unittest

import numpy as np
import pandas as pd

from cta.feature.feature_loader import load_symbol_features
from cta.skills.market_regime.range import compute_range_state
from cta.skills.market_regime.trend import compute_trend_state
from cta.skills.market_regime.volatility import compute_vol_state
from cta.skills.price_action.breakout_pullback import (
    PullbackSetup,
    detect_breakout_pullback,
    pullback_entry_trigger,
)
from cta.skills.price_action.bull_bear_flag import (
    FlagSetup,
    detect_flag,
    flag_breakout_trigger,
)
from cta.skills.price_action.channel_state import (
    ChannelState,
    channel_based_trailing,
    compute_channel_state,
)
from cta.skills.price_action.hl_structure import HLSignal, detect_hl_signals, resolve_hl_entry
from cta.skills.price_action.tight_range_breakout import (
    TightRangeSetup,
    detect_tight_range,
    resolve_breakout_trigger,
)


@lru_cache(maxsize=1)
def _rb_day() -> pd.DataFrame:
    return load_symbol_features(
        "RB0",
        interval="day",
        start_date="2018-01-01",
        end_date="2020-12-31",
    ).reset_index(drop=True)


@lru_cache(maxsize=1)
def _rb_minute() -> pd.DataFrame:
    return load_symbol_features(
        "RB0",
        interval="minute",
        start_date="2010-01-04",
        end_date="2010-01-31",
    ).reset_index(drop=True)


def _first_true_index(mask: pd.Series) -> Optional[int]:
    idx = np.where(mask.fillna(False).to_numpy())[0]
    if len(idx) == 0:
        return None
    return int(idx[0])


@dataclass
class _AnchorCfg:
    window: int = 20
    min_periods: int = 10


class TestRealFeatureDataMarketPriceAction(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.day = _rb_day()
            cls.minute = _rb_minute()
        except Exception as exc:
            raise unittest.SkipTest(f"load local feature data failed: {exc}") from exc
        if len(cls.day) < 300:
            raise unittest.SkipTest("RB0 day feature history too short for integration tests")
        if len(cls.minute) < 500:
            raise unittest.SkipTest("RB0 minute feature history too short for integration tests")

    def test_market_regime_real_data_has_nontrivial_states(self) -> None:
        trend = compute_trend_state(self.day)
        rng = compute_range_state(self.day)
        vol = compute_vol_state(self.day)

        self.assertEqual(len(trend), len(self.day))
        self.assertEqual(len(rng), len(self.day))
        self.assertEqual(len(vol), len(self.day))
        self.assertIn("trend_score", trend.columns)
        self.assertIn("range_score", rng.columns)
        self.assertIn("vol_regime", vol.columns)

        self.assertGreater(int((trend["trend_dir"] != 0).sum()), 10)
        self.assertGreater(int((rng["range_score"] > 0.6).sum()), 10)
        self.assertGreater(len(set(vol["vol_regime"].dropna().astype(str))), 1)

    def test_price_action_detectors_find_real_opportunities(self) -> None:
        tr = detect_tight_range(self.day, interval="day")
        flag = detect_flag(self.day, interval="day")

        anchor = pd.DataFrame(index=self.day.index)
        cfg = _AnchorCfg()
        anchor["breakout_level"] = self.day["high"].shift(1).rolling(
            cfg.window, min_periods=cfg.min_periods
        ).max()
        anchor["breakout_direction"] = "long"
        bp = detect_breakout_pullback(self.day, anchor, interval="day")

        hl = detect_hl_signals(self.day, interval="day")
        ch = compute_channel_state(self.day, interval="day")

        self.assertIn("tr_valid", tr.columns)
        self.assertIn("flag_valid", flag.columns)
        self.assertIn("bp_valid", bp.columns)
        self.assertIn("hl_kind", hl.columns)
        self.assertIn("ch_micro_count", ch.columns)

        self.assertGreater(int(tr["tr_valid"].sum()), 0)
        self.assertGreater(int(flag["flag_valid"].sum()), 0)
        self.assertGreater(int(bp["bp_valid"].sum()), 0)
        self.assertGreater(int((hl["hl_kind"] != "").sum()), 0)
        self.assertGreater(int((ch["ch_micro_count"] >= 3).sum()), 0)

    def test_price_action_trigger_builders_on_real_data(self) -> None:
        tr = detect_tight_range(self.day, interval="day")
        tr_i = _first_true_index(tr["tr_valid"])
        if tr_i is None:
            self.skipTest("no tight-range setup on selected RB0 day window")

        tr_setup = TightRangeSetup(
            valid=bool(tr["tr_valid"].iloc[tr_i]),
            upper=float(tr["tr_upper"].iloc[tr_i]),
            lower=float(tr["tr_lower"].iloc[tr_i]),
            range_atr=float(tr["tr_range_atr"].iloc[tr_i]),
            count=int(tr["tr_count"].iloc[tr_i]),
            direction_bias=int(tr["tr_direction_bias"].iloc[tr_i]),
        )
        tr_order = resolve_breakout_trigger(tr_setup, self.day.iloc[tr_i], tick_size=1.0)
        self.assertIsNotNone(tr_order)
        assert tr_order is not None
        self.assertIn("side", tr_order)

        flag = detect_flag(self.day, interval="day")
        flag_i = _first_true_index(flag["flag_valid"])
        if flag_i is None:
            self.skipTest("no flag setup on selected RB0 day window")
        kind = str(flag["flag_kind"].iloc[flag_i]).lower()
        fs = FlagSetup(
            valid=bool(flag["flag_valid"].iloc[flag_i]),
            kind="bull" if kind == "bull" else "bear",
            flag_low=float(flag["flag_low"].iloc[flag_i]),
            flag_high=float(flag["flag_high"].iloc[flag_i]),
            leg_length_atr=float(flag["flag_leg_length_atr"].iloc[flag_i]),
            pullback_ratio=float(flag["flag_pullback_ratio"].iloc[flag_i]),
            structure_tag=str(flag["flag_structure_tag"].iloc[flag_i]),
        )
        next_idx = min(flag_i + 1, len(self.day) - 1)
        flag_order = flag_breakout_trigger(fs, self.day.iloc[next_idx], tick_size=1.0)
        self.assertIsNotNone(flag_order)
        assert flag_order is not None
        self.assertIn(flag_order["side"], {"long", "short"})

        anchor = pd.DataFrame(index=self.day.index)
        anchor["breakout_level"] = self.day["high"].shift(1).rolling(20, min_periods=10).max()
        anchor["breakout_direction"] = "long"
        bp = detect_breakout_pullback(self.day, anchor, interval="day")
        bp_i = _first_true_index(bp["bp_valid"])
        if bp_i is None:
            self.skipTest("no breakout-pullback setup on selected RB0 day window")
        ps = PullbackSetup(
            valid=bool(bp["bp_valid"].iloc[bp_i]),
            direction=str(bp["bp_direction"].iloc[bp_i]).lower(),
            breakout_level=float(bp["bp_breakout_level"].iloc[bp_i]),
            pullback_low=float(bp["bp_pullback_low"].iloc[bp_i]),
            bars_since_breakout=int(bp["bp_bars_since_breakout"].iloc[bp_i]),
            confirmed=bool(bp["bp_confirmed"].iloc[bp_i]),
        )
        next_i = min(bp_i + 1, len(self.day) - 1)
        pb_order = pullback_entry_trigger(ps, self.day.iloc[next_i], tick_size=1.0)
        self.assertIsNotNone(pb_order)
        assert pb_order is not None
        self.assertIn(pb_order["side"], {"long", "short"})

        hl = detect_hl_signals(self.day, interval="day")
        hl_i = _first_true_index(hl["hl_kind"] != "")
        if hl_i is None:
            self.skipTest("no hl signal on selected RB0 day window")
        hs = HLSignal(
            kind=str(hl["hl_kind"].iloc[hl_i]),
            bar_idx=hl_i,
            reference_high=float(hl["hl_reference_high"].iloc[hl_i]),
            reference_low=float(hl["hl_reference_low"].iloc[hl_i]),
        )
        hl_order = resolve_hl_entry(hs, self.day.iloc[min(hl_i + 1, len(self.day) - 1)], tick_size=1.0)
        self.assertIsNotNone(hl_order)
        assert hl_order is not None
        self.assertIn(hl_order["side"], {"long", "short"})

        ch = compute_channel_state(self.day, interval="day")
        last = ch.iloc[-1]
        cs = ChannelState(
            micro_count=int(last["ch_micro_count"]),
            micro_direction=str(last["ch_micro_direction"]),
            trend_top=float(last["ch_trend_top"]) if pd.notna(last["ch_trend_top"]) else None,
            trend_bot=float(last["ch_trend_bot"]) if pd.notna(last["ch_trend_bot"]) else None,
            touches_top=int(last["ch_touches_top"]),
            touches_bot=int(last["ch_touches_bot"]),
        )
        trail = channel_based_trailing(
            position_side="long",
            state=cs,
            last_high=float(self.day["high"].iloc[-1]),
            last_low=float(self.day["low"].iloc[-1]),
        )
        self.assertIsNotNone(trail)


if __name__ == "__main__":
    unittest.main()
