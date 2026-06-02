"""breakout_pullback.py tests."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.price_action.breakout_pullback import (
    PullbackSetup,
    detect_breakout_pullback,
    pullback_entry_trigger,
)


def _mk_df(seed: int = 5, n: int = 180) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0.1, 0.3, size=n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 0.2
    low = np.minimum(open_, close) - 0.2
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.integers(200, 600, size=n),
        }
    )


def _breakout_anchor(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    out["breakout_level"] = np.nan
    out["breakout_direction"] = ""
    out.loc[80, "breakout_level"] = float(df["high"].iloc[75:80].max())
    out.loc[80, "breakout_direction"] = "long"
    return out


class TestBreakoutPullback(unittest.TestCase):
    def test_detect_breakout_pullback(self) -> None:
        df = _mk_df()
        breakout_df = _breakout_anchor(df)
        out = detect_breakout_pullback(df, breakout_df)
        for c in ("bp_valid", "bp_direction", "bp_breakout_level", "bp_pullback_low", "bp_bars_since_breakout", "bp_confirmed"):
            self.assertIn(c, out.columns)

    def test_pullback_entry_trigger(self) -> None:
        setup = PullbackSetup(
            valid=True,
            direction="long",
            breakout_level=101.0,
            pullback_low=100.4,
            bars_since_breakout=5,
            confirmed=True,
        )
        next_bar = pd.Series({"open": 101.1, "high": 101.8, "low": 100.9, "close": 101.6})
        order = pullback_entry_trigger(setup, next_bar, tick_size=1.0)
        self.assertIsNotNone(order)
        self.assertEqual(order["side"], "long")

    def test_interval_compatibility(self) -> None:
        df = _mk_df()
        breakout_df = _breakout_anchor(df)
        for interval in ("day", "minute60", "minute30", "minute15", "minute5", "minute"):
            out = detect_breakout_pullback(df, breakout_df, interval=interval)
            self.assertEqual(len(out), len(df))

    def test_pullback_extreme_uses_only_data_up_to_confirmation_bar(self) -> None:
        """P0: bp_pullback_low 不能被确认后的未来更深 low 污染。"""
        n = 40
        dt = pd.date_range("2024-01-01", periods=n, freq="D")
        close = np.full(n, 100.0, dtype=float)
        open_ = np.full(n, 100.0, dtype=float)
        high = np.full(n, 100.2, dtype=float)
        low = np.full(n, 99.8, dtype=float)

        # breakout anchor at i0=20, breakout_level=100.2
        i0 = 20
        # i0+1: shallow pullback
        close[21], high[21], low[21] = 100.15, 100.30, 100.10
        # i0+2: confirmation close > breakout_level
        close[22], high[22], low[22] = 100.25, 100.35, 100.05
        # i0+3: confirmation 之后才出现更深 low（旧实现会错误引用到这里）
        close[23], high[23], low[23] = 99.40, 100.10, 99.00

        df = pd.DataFrame(
            {
                "datetime": dt,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": np.full(n, 500, dtype=int),
            }
        )
        breakout_df = pd.DataFrame(index=df.index)
        breakout_df["breakout_level"] = np.nan
        breakout_df["breakout_direction"] = ""
        breakout_df.loc[i0, "breakout_level"] = 100.2
        breakout_df.loc[i0, "breakout_direction"] = "long"

        out = detect_breakout_pullback(df, breakout_df, max_bars_since_brk=6, max_pullback_atr=1.5)
        # 应在确认 bar(22) 生成 setup，且 pullback_low 只能来自 [21,22]
        self.assertTrue(bool(out.loc[22, "bp_valid"]))
        self.assertEqual(str(out.loc[22, "bp_direction"]), "long")
        self.assertAlmostEqual(float(out.loc[22, "bp_pullback_low"]), 100.05, places=6)
        self.assertAlmostEqual(float(out.loc[22, "bp_breakout_level"]), 100.2, places=6)


if __name__ == "__main__":
    unittest.main()
