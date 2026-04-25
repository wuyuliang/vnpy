"""continuous_contract.py tests."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.skills.data_backtest.continuous_contract import build_continuous, detect_rollover


def _all_contracts() -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=10, freq="D")
    rows = []
    for i, d in enumerate(dates):
        rows.append(
            {
                "date": d,
                "contract": "rb2405",
                "open": 100 + i,
                "high": 101 + i,
                "low": 99 + i,
                "close": 100 + i,
                "volume": 1000 + i * 10,
                "oi": 1500 - i * 100,
            }
        )
        rows.append(
            {
                "date": d,
                "contract": "rb2410",
                "open": 105 + i,
                "high": 106 + i,
                "low": 104 + i,
                "close": 105 + i,
                "volume": 800 + i * 20,
                "oi": 700 + i * 120,
            }
        )
    return pd.DataFrame(rows)


class TestContinuousContract(unittest.TestCase):
    def test_detect_rollover(self) -> None:
        df = _all_contracts()
        events = detect_rollover(df, rule="oi_max")
        self.assertGreaterEqual(len(events), 1)
        self.assertIn("from_contract", events.columns)
        self.assertIn("to_contract", events.columns)

    def test_build_continuous(self) -> None:
        df = _all_contracts()
        out = build_continuous(df, symbol_root="rb", method="back")
        self.assertEqual(len(out.df), 10)
        self.assertIn("adj_factor", out.df.columns)
        self.assertGreaterEqual(len(out.roll_events), 1)

    def test_back_adjust_preserves_last_close(self) -> None:
        """标准 back-adjust：最新合约的最新 bar 价格不得被改写。"""
        df = _all_contracts()
        out = build_continuous(df, symbol_root="rb", method="back")
        # day 9 active = rb2410, raw close = 105 + 9 = 114
        last_close = float(out.df["close"].iloc[-1])
        self.assertAlmostEqual(last_close, 114.0, places=5,
                               msg="back-adjust 不得改最新 bar 的价格")

    def test_back_adjust_smooths_roll_gap(self) -> None:
        """back-adjust 之后 close 的 bar-to-bar 变化应平滑，无 roll gap。"""
        df = _all_contracts()
        out = build_continuous(df, symbol_root="rb", method="back")
        diff = out.df["close"].astype(float).diff().abs().dropna()
        # 本例每日原本变动约 1.0，拼接 gap 5 应被调整掉
        self.assertLess(diff.max(), 2.0,
                        f"back-adjust 后不应有 gap，max |Δclose|={diff.max():.2f}")

    def test_back_adjust_history_shifted(self) -> None:
        """back-adjust 把历史 bar 前移：day0 close 应 = 原 100 + 5 (gap)。"""
        df = _all_contracts()
        out = build_continuous(df, symbol_root="rb", method="back")
        day0_close = float(out.df["close"].iloc[0])
        self.assertAlmostEqual(day0_close, 105.0, places=5,
                               msg="day0 close 应被 back-adjust 抬升 +5")

    def test_back_adjust_factor_zero_at_latest(self) -> None:
        """adj_factor 语义：back 累积加法偏移，最新 bar 应为 0。"""
        df = _all_contracts()
        out = build_continuous(df, symbol_root="rb", method="back")
        self.assertAlmostEqual(float(out.df["adj_factor"].iloc[-1]), 0.0,
                               places=5,
                               msg="最新 bar adj_factor 应为 0")
        # 历史 bar 的 adj_factor 应累积非零
        self.assertGreater(abs(float(out.df["adj_factor"].iloc[0])), 0.0,
                           "历史 bar adj_factor 应累积所有后续 roll 的 shift")

    def test_ratio_adjust_preserves_last_close(self) -> None:
        df = _all_contracts()
        out = build_continuous(df, symbol_root="rb", method="ratio")
        last_close = float(out.df["close"].iloc[-1])
        self.assertAlmostEqual(last_close, 114.0, places=5)
        # ratio method：最新 bar adj_factor 应为 1.0
        self.assertAlmostEqual(float(out.df["adj_factor"].iloc[-1]), 1.0,
                               places=5)


def _three_contracts() -> pd.DataFrame:
    """构造 3 个合约 + 2 次 roll：测试累积调整。"""
    dates = pd.date_range("2024-01-01", periods=15, freq="D")
    rows = []
    for i, d in enumerate(dates):
        # rb2405 在前 5 天主导，OI 衰减
        rows.append({"date": d, "contract": "rb2405",
                     "open": 100 + i, "high": 101 + i, "low": 99 + i,
                     "close": 100 + i, "volume": 1000.0,
                     "oi": max(2000 - i * 200, 0)})
        # rb2410 在 5-9 天主导
        rows.append({"date": d, "contract": "rb2410",
                     "open": 105 + i, "high": 106 + i, "low": 104 + i,
                     "close": 105 + i, "volume": 1000.0,
                     "oi": 200 + i * 200 if i < 10 else max(2000 - (i - 10) * 200, 0)})
        # rb2501 在 10-14 天主导
        rows.append({"date": d, "contract": "rb2501",
                     "open": 112 + i, "high": 113 + i, "low": 111 + i,
                     "close": 112 + i, "volume": 1000.0,
                     "oi": 100 if i < 9 else 200 + (i - 9) * 250})
    return pd.DataFrame(rows)


class TestContinuousMultiRoll(unittest.TestCase):
    def test_multi_roll_cumulative_back_adjust(self) -> None:
        """两次 roll 后，最早 bar 的 adj_factor 应等于两次 shift 之和。"""
        df = _three_contracts()
        out = build_continuous(df, symbol_root="rb", method="back")
        # 最新 bar adj_factor = 0
        self.assertAlmostEqual(float(out.df["adj_factor"].iloc[-1]), 0.0,
                               places=5)
        # 历史 bar 累加了所有后续 roll 的 shift
        # 应包含 2 次 roll 的累计偏移
        self.assertGreater(len(out.roll_events), 1,
                           "应识别出至少 2 次 roll")
        # day0 的累积 adj 等于 sum(shift_i)，shift_i = new_open - old_close
        events = out.roll_events
        expected_shift = 0.0
        df_dt = pd.to_datetime(df["date"])
        for _, r in events.iterrows():
            d = pd.Timestamp(r["date"])
            old_row = df[(df_dt == d) & (df["contract"] == str(r["from_contract"]))]
            new_row = df[(df_dt == d) & (df["contract"] == str(r["to_contract"]))]
            expected_shift += (
                float(new_row["open"].iloc[0]) - float(old_row["close"].iloc[0])
            )
        self.assertAlmostEqual(
            float(out.df["adj_factor"].iloc[0]), expected_shift,
            places=5,
            msg="day0 累积 adj_factor 应 = 所有 roll 的 shift 之和",
        )

    def test_multi_roll_smoothness(self) -> None:
        """back-adjust 后整段 close.diff() 不得有大跳跃。"""
        df = _three_contracts()
        out = build_continuous(df, symbol_root="rb", method="back")
        diff = out.df["close"].astype(float).diff().abs().dropna()
        # 任意一根 bar 的变动不应明显大于「品种内日变动 ≈ 1」
        self.assertLess(diff.max(), 3.0,
                        f"后调整不平滑，max |Δclose|={diff.max():.2f}")

    def test_ratio_adjust_cumulative(self) -> None:
        """ratio 法：day0 adj_factor 应是各 roll new_open/old_close 的累乘。"""
        df = _three_contracts()
        out = build_continuous(df, symbol_root="rb", method="ratio")
        events = out.roll_events
        df_dt = pd.to_datetime(df["date"])
        expected_ratio = 1.0
        for _, r in events.iterrows():
            d = pd.Timestamp(r["date"])
            old_row = df[(df_dt == d) & (df["contract"] == str(r["from_contract"]))]
            new_row = df[(df_dt == d) & (df["contract"] == str(r["to_contract"]))]
            expected_ratio *= (
                float(new_row["open"].iloc[0]) / float(old_row["close"].iloc[0])
            )
        self.assertAlmostEqual(
            float(out.df["adj_factor"].iloc[0]),
            expected_ratio,
            places=5,
        )

    def test_method_none_preserves_raw_close(self) -> None:
        """method='none' 不应改任何 OHLC。"""
        df = _all_contracts()
        out = build_continuous(df, symbol_root="rb", method="none")
        # day9 active = rb2410, raw close = 105+9 = 114
        self.assertAlmostEqual(float(out.df["close"].iloc[-1]), 114.0,
                               places=5)
        self.assertAlmostEqual(float(out.df["close"].iloc[0]), 100.0,
                               places=5)
        # adj_factor 一律为 ratio identity 1.0（按 fix 中 method!='back' 的设定）
        # 此处只断言 close 未被改写


if __name__ == "__main__":
    unittest.main()

