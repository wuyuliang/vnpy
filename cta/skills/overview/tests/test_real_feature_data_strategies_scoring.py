"""Real feature-data integration tests: strategies + regime switch + scoring."""
from __future__ import annotations

from functools import lru_cache
import unittest

import numpy as np
import pandas as pd

from cta.feature.feature_loader import load_symbol_features
from cta.skills.filtering_scoring.breakout_quality import score_breakout
from cta.skills.filtering_scoring.context_score import combine_final_score, compute_context_score
from cta.skills.filtering_scoring.risk_reward_score import compute_rr, rr_gate
from cta.skills.filtering_scoring.setup_quality import score_setup
from cta.skills.market_regime.range import compute_range_state
from cta.skills.market_regime.trend import compute_trend_state
from cta.skills.market_regime.volatility import compute_vol_state
from cta.skills.range_strategies.mean_reversion import compute_zscore, mr_decision
from cta.skills.range_strategies.range_boundary_reversal import detect_boundary_reversal
from cta.skills.regime_switch.breakout_score import compute_breakout_score
from cta.skills.regime_switch.switch_machine import allowed_strategies, compute_regime
from cta.skills.regime_switch.transition_risk import transition_risk_adjustment
from cta.skills.regime_switch.volatility_transition import detect_vol_transition
from cta.skills.trend_strategies.atr_breakout import compute_atr_channel, decide_atr_channel_trade
from cta.skills.trend_strategies.cross_sectional_momentum import (
    build_xs_portfolio,
    compute_xs_momentum,
)
from cta.skills.trend_strategies.donchian_breakout import compute_donchian, decide_donchian_trade
from cta.skills.trend_strategies.ma_trend_following import compute_ma_features, ma_decision


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


@lru_cache(maxsize=1)
def _xs_panel_3symbols() -> pd.DataFrame:
    symbols = ("RB0", "CU0", "M0")
    series: list[pd.Series] = []
    for s in symbols:
        df = load_symbol_features(
            s,
            interval="day",
            start_date="2020-01-01",
            end_date="2020-12-31",
            columns=["datetime", "close"],
        )
        sr = (
            df.drop_duplicates("datetime")
            .assign(datetime=lambda x: pd.to_datetime(x["datetime"]))
            .set_index("datetime")["close"]
            .rename(s)
        )
        series.append(sr)
    panel = pd.concat(series, axis=1).sort_index()
    return panel.dropna(how="all")


class TestRealFeatureDataStrategiesScoring(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.day = _rb_day()
            cls.minute = _rb_minute()
        except Exception as exc:
            raise unittest.SkipTest(f"load local feature data failed: {exc}") from exc
        if len(cls.day) < 300:
            raise unittest.SkipTest("RB0 day feature history too short")
        if len(cls.minute) < 500:
            raise unittest.SkipTest("RB0 minute feature history too short")

    def test_trend_strategies_find_entries_on_real_day(self) -> None:
        don = compute_donchian(self.day, n_entry=55, n_exit=20, interval="day")
        don_entries = 0
        for i in range(len(don)):
            sig = decide_donchian_trade(
                don, i, current_position=None, filters={}, tick_size=1.0
            )
            if sig is not None and sig.side in {"long", "short"}:
                don_entries += 1
        self.assertGreater(don_entries, 0)

        atr = compute_atr_channel(self.day, interval="day")
        atr_entries = 0
        for i in range(1, len(atr)):
            sig = decide_atr_channel_trade(atr, i, current_position=None, filters={})
            if sig is not None and sig.side in {"long", "short"}:
                atr_entries += 1
        self.assertGreater(atr_entries, 0)

        ma = compute_ma_features(self.day, interval="day")
        sides: dict[str, int] = {"long": 0, "short": 0, "flat": 0}
        for i in range(len(ma)):
            sig = ma_decision(ma, i, require_slope=True)
            if sig is not None:
                sides[sig.side] += 1
        self.assertGreater(sides["long"] + sides["short"], 0)

    def test_range_strategies_find_entries_on_real_day(self) -> None:
        rng = compute_range_state(self.day)
        rev = detect_boundary_reversal(self.day, rng, interval="day")
        self.assertGreater(int(rev["rb_valid"].sum()), 0)

        mr = compute_zscore(self.day, interval="day")
        mr["range_score"] = rng["range_score"]
        entries = 0
        for i in range(len(mr)):
            sig = mr_decision(mr, i, z_threshold=2.0, regime_gate=True)
            if sig is not None and sig.side in {"long", "short"}:
                entries += 1
        self.assertGreater(entries, 0)

    def test_regime_switch_pipeline_on_real_day(self) -> None:
        trend = compute_trend_state(self.day)
        rng = compute_range_state(self.day)
        vol = compute_vol_state(self.day)

        vt = detect_vol_transition(
            self.day,
            interval="day",
            persistence_bars=2,
            compression_min_bars=8,
            breakout_window=20,
        )
        self.assertIn("vol_transition_state", vt.columns)
        self.assertGreater(int(vt["vol_transition_event"].sum()), 0)

        reg = compute_regime(
            self.day,
            trend_score=trend["trend_score"],
            range_score=rng["range_score"],
            vol_state=vol["vol_regime"],
            interval="day",
        )
        self.assertIn("regime_label", reg.columns)
        self.assertTrue(reg["regime_label"].isin(
            {
                "trend_up",
                "trend_down",
                "range",
                "compression",
                "expansion_trending",
                "transition",
            }
        ).all())
        self.assertGreaterEqual(float(reg["transition_risk"].min()), 0.0)
        self.assertLessEqual(float(reg["transition_risk"].max()), 1.0)

        for label in set(reg["regime_label"].head(20)):
            _ = allowed_strategies(label)

        adj_tr = transition_risk_adjustment("transition", regime_age=1)
        adj_ok = transition_risk_adjustment("trend_up", regime_age=20)
        self.assertFalse(adj_tr.allow_new_entry)
        self.assertTrue(adj_ok.allow_new_entry)
        self.assertLess(adj_tr.size_multiplier, adj_ok.size_multiplier)

    def test_scoring_family_runs_on_real_data(self) -> None:
        j = len(self.day) - 1
        sq = score_setup(self.day, j, setup_type="tight_range", interval="day")
        self.assertGreaterEqual(sq.score, 0.0)
        self.assertLessEqual(sq.score, 1.0)

        atr_v = float(self.day["atr_14"].iloc[j]) if "atr_14" in self.day.columns else 1.0
        level = float(self.day["high"].shift(1).rolling(20, min_periods=5).max().iloc[j])
        bq = score_breakout(
            self.day,
            breakout_bar_idx=j,
            breakout_level=level,
            atr=max(atr_v, 1e-6),
            interval="day",
        )
        self.assertGreaterEqual(bq.score, 0.0)
        self.assertLessEqual(bq.score, 1.0)

        htf = self.day.tail(min(200, len(self.day))).reset_index(drop=True)
        ltf = self.minute.copy()
        i = min(len(ltf) - 1, 180)
        ctx = compute_context_score(
            df_ltf=ltf,
            df_mtf=ltf,
            df_htf=htf,
            bar_idx_ltf=i,
            setup_type="tight_range",
            setup_dir="long",
            interval="minute",
        )
        self.assertGreaterEqual(ctx.score, 0.0)
        self.assertLessEqual(ctx.score, 1.0)

        final = combine_final_score(sq.score, bq.score, ctx.score)
        self.assertGreaterEqual(final, 0.0)
        self.assertLessEqual(final, 1.0)

        entry = float(self.day["close"].iloc[j])
        stop = entry - max(atr_v, 1e-6)
        rr = compute_rr(
            self.day,
            j,
            entry=entry,
            stop=stop,
            direction="long",
            interval="day",
        )
        self.assertGreater(rr.rr, 0.0)
        self.assertTrue(rr_gate(rr.rr, min_rr=1.0))

    def test_breakout_mode_scoring_on_real_minute(self) -> None:
        idx = min(180, len(self.minute) - 1)
        score = compute_breakout_score(
            self.minute,
            bar_idx=idx,
            htf_same_direction=True,
            interval="minute",
        )
        self.assertGreaterEqual(score.score, 0.0)
        self.assertLessEqual(score.score, 1.0)
        self.assertIn(score.gate_pass, {True, False})

    def test_cross_sectional_momentum_on_real_panel(self) -> None:
        panel = _xs_panel_3symbols()
        if panel.shape[1] < 3 or len(panel) < 80:
            self.skipTest("real panel not enough for xs momentum integration")
        mom = compute_xs_momentum(panel, lookback=20, vol_window=20)
        targets = build_xs_portfolio(mom, top_pct=0.34, bot_pct=0.34, gross_exposure=1.0)
        self.assertGreater(len(targets), 0)
        gross = sum(abs(v) for v in targets[-1].weights.values())
        self.assertAlmostEqual(gross, 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
