from __future__ import annotations

import pandas as pd
import pytest

from stock.strategy.big_bull_mode import (
    BIG_BULL_SIGNAL_TYPE,
    BigBullConfig,
    prepare_big_bull_features,
    score_and_filter_big_bull_opportunities,
    scan_ma5_ma10_big_bull,
)


def _frame(symbol: str = "000001.SZ") -> pd.DataFrame:
    closes = (
        [8.0 + index * 0.08 for index in range(55)]
        + [12.2 - index * 0.18 for index in range(8)]
        + [11.0 + index * 0.45 for index in range(15)]
    )
    dates = pd.date_range("2024-01-02", periods=len(closes), freq="B")
    volumes = [1000.0] * 63 + [2000.0 + index * 50.0 for index in range(15)]
    pct_chg = pd.Series(closes).pct_change().fillna(0.0) * 100.0
    return pd.DataFrame(
        {
            "symbol": symbol,
            "exchange": "SZSE" if symbol.endswith(".SZ") else "SSE",
            "interval": "d",
            "datetime": dates.strftime("%Y-%m-%d 00:00:00"),
            "open": [value * 0.99 for value in closes],
            "high": [value * 1.03 for value in closes],
            "low": [value * 0.97 for value in closes],
            "close": closes,
            "pct_chg": pct_chg,
            "volume": volumes,
            "open_interest": 0.0,
            "turnover": [
                volume * close for volume, close in zip(volumes, closes, strict=True)
            ],
        }
    )


def test_scan_ma5_ma10_big_bull_emits_next_day_opportunity() -> None:
    cfg = BigBullConfig(
        min_history_bars=60,
        min_close=1.0,
        min_score=0.0,
        min_rs_60d_pct=0.0,
        max_candidates_per_day=30,
        cooldown_days=0,
        min_turnover_20_avg=0.0,
        max_close_to_ma20=2.0,
        max_return_20d=9.0,
        min_limit_up_count_120d=0,
        min_big_volume_up_count_60d=0,
    )

    result = scan_ma5_ma10_big_bull(_frame(), cfg)

    assert not result.empty
    assert set(result["signal_type"]) == {BIG_BULL_SIGNAL_TYPE}
    assert set(result["entry_action"]) == {"buy"}
    assert result["opportunity_date"].iloc[0] > result["signal_datetime"].str.slice(0, 10).iloc[0]
    assert {
        "ma5",
        "ma10",
        "ma20",
        "volume_ratio_20",
        "return_20d",
        "candidate_reason",
    }.issubset(result.columns)


def test_scan_ma5_ma10_big_bull_does_not_emit_last_bar_without_next_session() -> None:
    frame = _frame().iloc[:67].copy()

    result = scan_ma5_ma10_big_bull(
        frame,
        BigBullConfig(
            min_history_bars=60,
            min_close=1.0,
            min_turnover_20_avg=0.0,
            min_volume_ratio_20=0.0,
            min_volume_3_to_20=0.0,
            max_close_to_ma20=9.0,
            max_return_20d=9.0,
            min_limit_up_count_120d=0,
            min_big_volume_up_count_60d=0,
            min_rs_60d_pct=0.0,
            min_score=0.0,
        ),
    )

    assert result.empty


def test_limit_up_feature_uses_twenty_percent_for_growth_boards() -> None:
    frame = _frame("300750.SZ")
    frame["pct_chg"] = 0.0
    frame.loc[70, "pct_chg"] = 10.0
    frame.loc[71, "pct_chg"] = 20.0

    features = prepare_big_bull_features(frame, BigBullConfig())

    assert features.loc[70, "limit_up_count_120d"] == 0
    assert features.loc[71, "limit_up_count_120d"] == 1


def test_score_and_filter_big_bull_opportunities_keeps_daily_topn_and_cooldown() -> None:
    raw = pd.DataFrame(
        [
            {
                "symbol": "000001.SZ",
                "signal_type": BIG_BULL_SIGNAL_TYPE,
                "opportunity_date": "2024-03-01",
                "return_60d": 0.20,
                "return_120d": 0.30,
                "volume_ratio_20": 1.4,
                "volume_3_to_20": 1.2,
                "ma5": 11.0,
                "ma10": 10.8,
                "ma20": 10.0,
                "ma20_slope_5d": 0.02,
                "ma60_slope_20d": 0.01,
                "close_price": 11.0,
                "ma60": 10.5,
                "close_to_ma20": 1.10,
                "close_to_120d_high": 0.96,
                "base_compression_60d": 0.25,
                "limit_up_count_120d": 1,
                "big_volume_up_count_60d": 2,
            },
            {
                "symbol": "000002.SZ",
                "signal_type": BIG_BULL_SIGNAL_TYPE,
                "opportunity_date": "2024-03-01",
                "return_60d": 0.50,
                "return_120d": 0.70,
                "volume_ratio_20": 2.0,
                "volume_3_to_20": 1.8,
                "ma5": 12.0,
                "ma10": 11.4,
                "ma20": 10.5,
                "ma20_slope_5d": 0.04,
                "ma60_slope_20d": 0.03,
                "close_price": 12.0,
                "ma60": 10.8,
                "close_to_ma20": 1.14,
                "close_to_120d_high": 0.99,
                "base_compression_60d": 0.18,
                "limit_up_count_120d": 2,
                "big_volume_up_count_60d": 4,
            },
            {
                "symbol": "000003.SZ",
                "signal_type": BIG_BULL_SIGNAL_TYPE,
                "opportunity_date": "2024-03-01",
                "return_60d": 0.35,
                "return_120d": 0.40,
                "volume_ratio_20": 1.6,
                "volume_3_to_20": 1.4,
                "ma5": 11.5,
                "ma10": 11.1,
                "ma20": 10.4,
                "ma20_slope_5d": 0.03,
                "ma60_slope_20d": 0.02,
                "close_price": 11.5,
                "ma60": 10.7,
                "close_to_ma20": 1.10,
                "close_to_120d_high": 0.98,
                "base_compression_60d": 0.20,
                "limit_up_count_120d": 1,
                "big_volume_up_count_60d": 3,
            },
            {
                "symbol": "000002.SZ",
                "signal_type": BIG_BULL_SIGNAL_TYPE,
                "opportunity_date": "2024-03-10",
                "return_60d": 0.60,
                "return_120d": 0.80,
                "volume_ratio_20": 2.2,
                "volume_3_to_20": 1.9,
                "ma5": 13.0,
                "ma10": 12.0,
                "ma20": 11.0,
                "ma20_slope_5d": 0.05,
                "ma60_slope_20d": 0.04,
                "close_price": 13.0,
                "ma60": 11.0,
                "close_to_ma20": 1.18,
                "close_to_120d_high": 1.0,
                "base_compression_60d": 0.15,
                "limit_up_count_120d": 3,
                "big_volume_up_count_60d": 5,
            },
        ]
    )
    cfg = BigBullConfig(
        min_score=0.0,
        min_rs_60d_pct=0.0,
        max_candidates_per_day=2,
        cooldown_days=20,
    )

    result = score_and_filter_big_bull_opportunities(
        raw,
        cfg,
        market_returns_by_date=_market_returns(raw),
    )

    assert list(result["symbol"]) == ["000002.SZ", "000003.SZ"]
    assert result["big_bull_score"].is_monotonic_decreasing
    assert result["rs_60d_pct"].between(0.0, 1.0).all()


def test_score_and_filter_keeps_non_big_bull_rows_unchanged() -> None:
    raw = pd.DataFrame(
        [
            {
                "symbol": "600000.SH",
                "signal_type": "volume_spike_up",
                "opportunity_date": "2024-03-01",
            },
            {
                "symbol": "000001.SZ",
                "signal_type": BIG_BULL_SIGNAL_TYPE,
                "opportunity_date": "2024-03-01",
                "return_60d": 0.2,
            },
        ]
    )
    cfg = BigBullConfig(
        min_score=100.0,
        min_rs_60d_pct=1.0,
        max_candidates_per_day=1,
        cooldown_days=20,
    )

    result = score_and_filter_big_bull_opportunities(
        raw,
        cfg,
        market_returns_by_date=_market_returns(raw),
    )

    assert "600000.SH" in set(result["symbol"])
    assert BIG_BULL_SIGNAL_TYPE not in set(result["signal_type"])


def _rankable_row(
    symbol: str,
    opportunity_date: str,
    *,
    return_60d: float,
    volume_ratio_20: float,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "signal_type": BIG_BULL_SIGNAL_TYPE,
        "opportunity_date": opportunity_date,
        "return_20d": 0.10,
        "return_60d": return_60d,
        "return_120d": 0.30,
        "volume_ratio_20": volume_ratio_20,
        "volume_3_to_20": 1.4,
        "ma5": 12.0,
        "ma10": 11.5,
        "ma20": 11.0,
        "ma60": 10.0,
        "ma20_slope_5d": 0.02,
        "close_price": 12.0,
        "close_to_ma20": 1.09,
        "close_to_120d_high": 0.98,
        "base_compression_60d": 0.20,
        "limit_up_count_120d": 1,
        "big_volume_up_count_60d": 2,
    }


def _market_returns(frame: pd.DataFrame) -> dict[str, list[float]]:
    signal_types = frame.get(
        "signal_type",
        pd.Series(BIG_BULL_SIGNAL_TYPE, index=frame.index),
    ).astype(str)
    big = frame[signal_types == BIG_BULL_SIGNAL_TYPE]
    result: dict[str, list[float]] = {}
    for opportunity_date, day in big.groupby("opportunity_date"):
        values = pd.to_numeric(day["return_60d"], errors="coerce").dropna()
        result[str(opportunity_date)] = [float(value) for value in values]
    return result


def test_score_uses_all_market_relative_strength_distribution() -> None:
    raw = pd.DataFrame(
        [_rankable_row("000001.SZ", "2024-03-01", return_60d=-0.10, volume_ratio_20=1.5)]
    )

    result = score_and_filter_big_bull_opportunities(
        raw,
        BigBullConfig(min_score=0.0, min_rs_60d_pct=0.70, cooldown_days=0),
        market_returns_by_date={"2024-03-01": [-0.50, -0.20, -0.10, 0.0, 0.10]},
    )

    assert result.empty


def test_daily_topn_backfills_after_symbol_cooldown() -> None:
    raw = pd.DataFrame(
        [
            _rankable_row("000001.SZ", "2024-03-01", return_60d=0.50, volume_ratio_20=2.5),
            _rankable_row("000002.SZ", "2024-03-01", return_60d=0.20, volume_ratio_20=1.4),
            _rankable_row("000001.SZ", "2024-03-10", return_60d=0.60, volume_ratio_20=2.8),
            _rankable_row("000003.SZ", "2024-03-10", return_60d=0.30, volume_ratio_20=1.6),
        ]
    )

    result = score_and_filter_big_bull_opportunities(
        raw,
        BigBullConfig(
            min_score=0.0,
            min_rs_60d_pct=0.0,
            max_candidates_per_day=1,
            cooldown_days=20,
        ),
        market_returns_by_date=_market_returns(raw),
    )

    assert list(result["symbol"]) == ["000001.SZ", "000003.SZ"]


def test_score_uses_configured_not_overheated_thresholds() -> None:
    raw = pd.DataFrame(
        [_rankable_row("000001.SZ", "2024-03-01", return_60d=0.30, volume_ratio_20=1.5)]
    )

    loose = score_and_filter_big_bull_opportunities(
        raw,
        BigBullConfig(
            min_score=0.0,
            min_rs_60d_pct=0.0,
            cooldown_days=0,
            max_close_to_ma20=2.0,
            max_return_20d=1.0,
        ),
        market_returns_by_date=_market_returns(raw),
    )
    strict = score_and_filter_big_bull_opportunities(
        raw,
        BigBullConfig(
            min_score=0.0,
            min_rs_60d_pct=0.0,
            cooldown_days=0,
            max_close_to_ma20=1.05,
            max_return_20d=0.05,
        ),
        market_returns_by_date=_market_returns(raw),
    )

    assert loose["big_bull_score"].iloc[0] == strict["big_bull_score"].iloc[0] + 10.0


def test_score_rejects_invalid_big_bull_opportunity_dates() -> None:
    raw = pd.DataFrame(
        [_rankable_row("000001.SZ", "not-a-date", return_60d=0.30, volume_ratio_20=1.5)]
    )

    with pytest.raises(ValueError, match="valid opportunity_date"):
        score_and_filter_big_bull_opportunities(raw)


def test_score_requires_market_wide_relative_strength_distribution() -> None:
    raw = pd.DataFrame(
        [_rankable_row("000001.SZ", "2024-03-01", return_60d=0.30, volume_ratio_20=1.5)]
    )

    with pytest.raises(ValueError, match="market_returns_by_date"):
        score_and_filter_big_bull_opportunities(raw)
