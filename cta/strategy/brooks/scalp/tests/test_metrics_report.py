from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml
from PIL import Image, ImageDraw

from cta.strategy.brooks.scalp.config import load_config
from cta.strategy.brooks.scalp.metrics import (
    REQUIRED_RULE_DIRECTIONS,
    RiskScoreInput,
    block_bootstrap_loss_probability,
    build_group_metrics,
    calculate_risk_score,
    compute_trade_metrics,
    equity_metrics,
    historical_var_es,
)
from cta.strategy.brooks.scalp.report import (
    AtomicReportPublisher,
    ReportValidationError,
    _draw_candlestick_panel,
    _resample_ohlcv,
)


def _trade_rows(count: int, *, wins: int, win_pnl: float = 11.0, loss_pnl: float = -10.0) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    rules = list(REQUIRED_RULE_DIRECTIONS)
    for index in range(count):
        rule, direction = rules[index % len(rules)]
        rows.append(
            {
                "trade_id": f"T{index:04d}",
                "candidate_id": f"C{index:04d}",
                "symbol": "RB0.SHFE" if index % 2 == 0 else "CU0.SHFE",
                "contract_code": "RB2601.SHF" if index % 2 == 0 else "CU2601.SHF",
                "direction": direction,
                "rule_id": rule,
                "net_pnl": win_pnl if index < wins else loss_pnl,
                "net_r": (win_pnl if index < wins else loss_pnl) / 10.0,
                "mfe_r": 1.2,
                "mae_r": -0.5,
                "holding_1m_bars": 5,
                "total_fee": 1.0,
                "total_slippage": 1.0,
                "session_id": "20260105:day",
                "session_type": "day",
                "exchange_trade_date": pd.Timestamp("2026-01-05") + pd.Timedelta(days=index),
                "exit_time": pd.Timestamp("2026-01-05 10:00", tz="Asia/Shanghai")
                + pd.Timedelta(days=index),
            }
        )
    return pd.DataFrame(rows)


def test_trade_metrics_include_scratch_in_denominator_and_use_net_pnl() -> None:
    trades = _trade_rows(10, wins=8)
    trades.loc[9, "net_pnl"] = 0.0
    metrics = compute_trade_metrics(trades)

    assert metrics["sample_count"] == 10
    assert metrics["net_win_rate"] == pytest.approx(0.80)
    assert metrics["net_average_payoff"] == pytest.approx(1.10)
    assert metrics["profit_factor"] == pytest.approx(8.8)


def test_chart_resampling_preserves_true_ohlcv_candle() -> None:
    index = pd.date_range(
        "2026-01-05 09:01",
        periods=5,
        freq="min",
        tz="Asia/Shanghai",
    )
    minute = pd.DataFrame(
        {
            "open": [100.0, 101.0, 99.0, 102.0, 103.0],
            "high": [102.0, 103.0, 101.0, 104.0, 106.0],
            "low": [99.0, 98.0, 97.0, 100.0, 102.0],
            "close": [101.0, 99.0, 100.0, 103.0, 105.0],
            "volume": [1.0, 2.0, 3.0, 4.0, 5.0],
        },
        index=index,
    )

    candle = _resample_ohlcv(minute, "5min").iloc[0]
    assert candle["open"] == 100.0
    assert candle["high"] == 106.0
    assert candle["low"] == 97.0
    assert candle["close"] == 105.0
    assert candle["volume"] == 15.0


def test_candlestick_panel_accepts_fixed_slots_and_external_price_levels() -> None:
    candles = pd.DataFrame(
        {
            "open": [100.0, 101.0],
            "high": [102.0, 103.0],
            "low": [99.0, 100.0],
            "close": [101.0, 102.0],
            "volume": [10.0, 12.0],
        },
        index=pd.date_range(
            "2026-01-05 09:05",
            periods=2,
            freq="5min",
            tz="Asia/Shanghai",
        ),
    )
    image = Image.new("RGB", (500, 300), "white")

    _draw_candlestick_panel(
        ImageDraw.Draw(image),
        (10, 10, 490, 290),
        candles,
        label="fixed slots",
        plot_slots=[2, 3],
        plot_slot_count=5,
        price_levels=[95.0, 110.0],
    )

    assert image.getbbox() == (0, 0, 500, 300)


@pytest.mark.parametrize(
    ("wins", "win_pnl", "loss_pnl", "expected"),
    [
        (79, 10.2, -10.0, "FAILED"),
        (80, 10.2, -10.0, "PASSED"),
        (80, 10.199, -10.0, "FAILED"),
        (80, 12.001, -10.0, "FAILED"),
    ],
)
def test_group_threshold_boundaries_are_strict(
    wins: int,
    win_pnl: float,
    loss_pnl: float,
    expected: str,
) -> None:
    trades = _trade_rows(100, wins=wins, win_pnl=win_pnl, loss_pnl=loss_pnl)
    grouped = build_group_metrics(
        trades,
        minimum_trades=100,
        minimum_win_rate=0.80,
        payoff_min=1.02,
        payoff_max=1.20,
    )
    portfolio = grouped.loc[grouped["group_type"] == "portfolio"].iloc[0]
    assert portfolio["status"] == expected


def test_all_six_frozen_groups_are_emitted_even_when_empty() -> None:
    trades = _trade_rows(2, wins=2)
    grouped = build_group_metrics(trades)
    frozen = grouped.loc[grouped["group_type"] == "required_setup_direction"]

    assert len(frozen) == 6
    assert {
        "average_daily_return",
        "average_monthly_return",
        "max_drawdown",
    }.issubset(grouped.columns)
    assert set(zip(frozen["rule_id"], frozen["direction"], strict=True)) == set(
        REQUIRED_RULE_DIRECTIONS
    )
    assert set(frozen["status"]) == {"INCONCLUSIVE"}


def test_required_symbol_setup_direction_groups_include_zero_trade_symbols() -> None:
    grouped = build_group_metrics(
        pd.DataFrame(columns=["symbol", "rule_id", "direction", "net_pnl"]),
        required_symbols=("RB0.SHFE", "CU0.SHFE"),
    )
    frozen = grouped.loc[
        grouped["group_type"].eq("required_symbol_setup_direction")
    ]
    assert len(frozen) == 12
    assert set(frozen["symbol"]) == {"RB0.SHFE", "CU0.SHFE"}
    assert set(frozen["status"]) == {"INCONCLUSIVE"}


def test_var_es_are_losses_and_bootstrap_is_deterministic() -> None:
    returns = pd.Series([0.01] * 990 + [-0.02] * 10)
    var99, es99 = historical_var_es(returns)
    assert var99 >= 0.0
    assert es99 >= var99

    probability_a = block_bootstrap_loss_probability(returns, simulations=100, seed=20260809)
    probability_b = block_bootstrap_loss_probability(returns, simulations=100, seed=20260809)
    assert probability_a == probability_b
    assert block_bootstrap_loss_probability(pd.Series([0.0] * 499)) is None


def test_first_day_return_and_drawdown_use_explicit_initial_equity() -> None:
    metrics = equity_metrics(
        pd.DataFrame({"date": ["2026-01-05"], "equity": [180_000.0]}),
        initial_equity=200_000.0,
    )
    assert metrics["average_daily_return"] == pytest.approx(-0.10)
    assert metrics["average_monthly_return"] == pytest.approx(-0.10)
    assert metrics["max_drawdown"] == pytest.approx(0.10)
    assert metrics["worst_daily_return"] == pytest.approx(-0.10)


def _passing_score_input() -> RiskScoreInput:
    return RiskScoreInput(
        max_drawdown=0.02,
        max_margin_usage=0.20,
        margin_breach_count=0,
        bootstrap_loss_probability=0.0,
        worst_daily_return=-0.01,
        var99=0.005,
        es99=0.008,
        fee_stress_net_pnl=1.0,
        fee_stress_profit_factor=1.1,
        slippage_stress_net_pnl=1.0,
        slippage_stress_profit_factor=1.1,
        gap_limit_margin_breaches=0,
        risk_budget_violations=0,
        circuit_breaker_violations=0,
        session_close_residuals=0,
        all_years_positive=True,
        positive_rolling_quarter_ratio=0.8,
        rb_cu_force_failures=0,
        causal_audit_passed=True,
        rollover_audit_passed=True,
        ledger_reconciliation_passed=True,
        future_leakage=False,
        continuous_contract_execution=False,
        costs_missing=False,
        rejected_open_fills=0,
        unresolved_entry_risk_breaches=0,
        opens_without_daily_limits=0,
    )


def test_risk_score_threshold_and_veto_override_points() -> None:
    result = calculate_risk_score(_passing_score_input(), minimum_score=90)
    assert result.score == 100
    assert result.status == "PASSED"

    vetoed = calculate_risk_score(
        _passing_score_input().__class__(
            **{**_passing_score_input().__dict__, "future_leakage": True}
        ),
        minimum_score=90,
    )
    assert vetoed.score == 100
    assert vetoed.status == "REJECTED"
    assert "future_leakage" in vetoed.vetoes


def test_atomic_publish_keeps_previous_report_when_validation_fails(tmp_path: Path) -> None:
    target = tmp_path / "run"
    target.mkdir()
    (target / "sentinel.txt").write_text("old", encoding="utf-8")
    publisher = AtomicReportPublisher(target, required_files={"summary.json", "report.md"})

    with pytest.raises(ReportValidationError):
        with publisher.staging_directory() as staging:
            (staging / "summary.json").write_text("{}", encoding="utf-8")

    assert (target / "sentinel.txt").read_text(encoding="utf-8") == "old"

    with publisher.staging_directory() as staging:
        (staging / "summary.json").write_text('{"status":"INCONCLUSIVE"}', encoding="utf-8")
        (staging / "report.md").write_text("# report\n", encoding="utf-8")
    assert not (target / "sentinel.txt").exists()
    assert (target / "summary.json").exists()


def test_atomic_publish_rejects_unknown_extra_files(tmp_path: Path) -> None:
    publisher = AtomicReportPublisher(tmp_path / "run", required_files={"summary.json"})
    with pytest.raises(ReportValidationError, match="unknown report files"):
        with publisher.staging_directory() as staging:
            (staging / "summary.json").write_text("{}", encoding="utf-8")
            (staging / "unexpected.txt").write_text("not frozen", encoding="utf-8")


def test_atomic_publish_decodes_png_instead_of_trusting_header(tmp_path: Path) -> None:
    publisher = AtomicReportPublisher(tmp_path / "run", required_files={"chart.png"})
    with pytest.raises(ReportValidationError, match="invalid PNG"):
        with publisher.staging_directory() as staging:
            (staging / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\nnot-an-image")


def test_atomic_publish_rejects_duplicate_trade_ids(tmp_path: Path) -> None:
    publisher = AtomicReportPublisher(tmp_path / "run", required_files={"trades.csv"})
    with pytest.raises(ReportValidationError, match="duplicate primary key"):
        with publisher.staging_directory() as staging:
            pd.DataFrame(
                [
                    {"trade_id": "T1", "entry_time": "2026-01-05T09:00:00+08:00"},
                    {"trade_id": "T1", "entry_time": "2026-01-05T09:01:00+08:00"},
                ]
            ).to_csv(staging / "trades.csv", index=False)


def test_atomic_publish_rejects_config_snapshot_hash_mismatch(tmp_path: Path) -> None:
    publisher = AtomicReportPublisher(
        tmp_path / "run",
        required_files={"config_snapshot.yaml", "summary.json"},
    )
    with pytest.raises(ReportValidationError, match="config hash mismatch"):
        with publisher.staging_directory() as staging:
            (staging / "config_snapshot.yaml").write_text(
                yaml.safe_dump(load_config().to_dict(), sort_keys=False),
                encoding="utf-8",
            )
            (staging / "summary.json").write_text(
                '{"config_sha256":"incorrect"}',
                encoding="utf-8",
            )
