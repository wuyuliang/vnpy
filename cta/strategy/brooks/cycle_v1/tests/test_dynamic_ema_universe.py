from __future__ import annotations

from datetime import date

import pandas as pd

from cta.strategy.brooks.cycle_v1.backtest.universe import build_dynamic_ema_universe


def _daily(closes: list[float], contracts: list[str] | None = None) -> pd.DataFrame:
    count = len(closes)
    return pd.DataFrame(
        {
            "root_symbol": "RB",
            "exchange_trade_date": pd.date_range(
                "2026-01-05", periods=count, freq="B"
            ).date,
            "contract_code": contracts or ["RB2605.SHF"] * count,
            "bar_end": pd.date_range(
                "2026-01-05 15:00", periods=count, freq="B", tz="Asia/Shanghai"
            ),
            "close": closes,
        }
    )


def test_ema_filter_uses_only_the_previous_completed_daily_bar() -> None:
    result = build_dynamic_ema_universe(_daily([10, 11, 12, 13, 14, 15]))

    first_eligible = result.loc[result["eligible"]].iloc[0]
    assert first_eligible["exchange_trade_date"] == date(2026, 1, 12)
    assert first_eligible["ema_asof_trade_date"] == date(2026, 1, 9)
    assert first_eligible["ema1"] == 14
    assert first_eligible["ema1"] > first_eligible["ema3"] > first_eligible["ema5"]


def test_ema_filter_is_prefix_invariant_when_future_close_changes() -> None:
    original = _daily([10, 11, 12, 13, 14, 15, 16])
    changed = original.copy()
    changed.loc[changed.index[-1], "close"] = 1_000

    left = build_dynamic_ema_universe(original).iloc[:-1].reset_index(drop=True)
    right = build_dynamic_ema_universe(changed).iloc[:-1].reset_index(drop=True)

    pd.testing.assert_frame_equal(left, right)


def test_contract_roll_resets_ema_warmup() -> None:
    contracts = ["RB2605.SHF"] * 5 + ["RB2610.SHF"] * 5
    result = build_dynamic_ema_universe(
        _daily([10, 11, 12, 13, 14, 20, 21, 22, 23, 24], contracts)
    )

    new_contract = result.loc[result["contract_code"].eq("RB2610.SHF")]
    assert not new_contract["eligible"].any()
    assert set(new_contract["reason_code"]) == {"EMA_WARMUP_OR_ROLL"}
