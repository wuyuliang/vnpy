import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from PIL import Image

from stock.etf import run_ema_trend_allocation as runner
from stock.etf.ema_trend_allocation_strategy import TrendAllocationConfig
from stock.etf.optimize_ema_trend_allocation import OptimizationResult
from stock.etf.run_ema_trend_allocation import (
    calculate_buy_hold_metrics,
    run_and_write,
)


def test_calculate_buy_hold_metrics_uses_n_minus_one_return_intervals() -> None:
    bars = pd.DataFrame(
        {
            "datetime": pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"]),
            "close": [100.0, 110.0, 99.0],
        }
    )

    metrics = calculate_buy_hold_metrics(bars, "2026-01-05", "2026-01-07")

    returns = pd.Series([0.0, 0.1, -0.1])
    expected_sharpe = returns.mean() / returns.std(ddof=0) * np.sqrt(252)
    assert metrics["total_return"] == pytest.approx(-0.01)
    assert metrics["annual_return"] == pytest.approx(0.99 ** (252 / 2) - 1)
    assert metrics["max_drawdown"] == pytest.approx(-0.1)
    assert metrics["sharpe"] == pytest.approx(expected_sharpe)
    assert metrics["annual_one_way_turnover"] == 0.0
    assert metrics["trade_count"] == 0


@pytest.mark.parametrize("price", [0.0, -1.0, np.nan, np.inf, -np.inf])
def test_calculate_buy_hold_metrics_rejects_non_positive_or_non_finite_prices(
    price: float,
) -> None:
    bars = pd.DataFrame(
        {
            "datetime": pd.to_datetime(["2026-01-05", "2026-01-06"]),
            "close": [100.0, price],
        }
    )

    with pytest.raises(ValueError, match="finite and positive"):
        calculate_buy_hold_metrics(bars, "2026-01-05", "2026-01-06")


def _write_input_files(root: Path) -> tuple[Path, Path, Path]:
    date_parts = [
        pd.bdate_range("2017-08-14", periods=35),
        pd.bdate_range(end="2022-12-30", periods=35),
        pd.bdate_range("2023-01-02", periods=35),
        pd.bdate_range(end="2024-12-31", periods=35),
        pd.bdate_range("2025-01-02", periods=35),
        pd.bdate_range(end="2026-07-17", periods=35),
    ]
    dates = pd.DatetimeIndex(np.concatenate([part.values for part in date_parts]))
    phase = np.arange(len(dates), dtype=float)
    close = 10.0 + phase * 0.015 + np.sin(phase / 6.0) * 1.2
    open_ = close + np.cos(phase / 4.0) * 0.08
    daily = pd.DataFrame(
        {
            "symbol": "159915.SZ",
            "datetime": dates,
            "open": open_,
            "high": np.maximum(open_, close) + 0.2,
            "low": np.minimum(open_, close) - 0.2,
            "close": close,
            "volume": 1_000_000.0 + phase,
        }
    )
    benchmark_close = 3_500.0 + phase * 1.5 + np.sin(phase / 10.0) * 80.0
    benchmark = pd.DataFrame(
        {
            "symbol": "000300.SH",
            "datetime": dates,
            "close": benchmark_close,
        }
    )
    metadata = pd.DataFrame(
        {
            "symbol": ["159915.SZ"],
            "name": ["易方达创业板ETF"],
            "fund_type": ["股票型ETF"],
        }
    )
    daily_path = root / "daily.csv"
    metadata_path = root / "metadata.csv"
    benchmark_path = root / "benchmark.csv"
    daily.to_csv(daily_path, index=False)
    metadata.to_csv(metadata_path, index=False)
    benchmark.to_csv(benchmark_path, index=False)
    return daily_path, metadata_path, benchmark_path


def _candidate_results() -> pd.DataFrame:
    rows = []
    for index, (slow, confirmation, slope) in enumerate(
        (
            (20, 1, 3),
            (20, 1, 5),
            (20, 2, 3),
            (20, 2, 5),
            (30, 1, 3),
            (30, 1, 5),
            (30, 2, 3),
            (30, 2, 5),
        )
    ):
        rows.append(
            {
                "slow": slow,
                "confirmation": confirmation,
                "slope": slope,
                "train_total_return": 0.10,
                "train_max_drawdown": -0.10,
                "train_annual_one_way_turnover": 1.0,
                "validation_total_return": 0.08 - index / 1000,
                "validation_max_drawdown": -0.12,
                "validation_annual_one_way_turnover": 1.2,
                "train_feasible": True,
                "validation_feasible": True,
                "rank": index + 1,
                "selected": index == 0,
            }
        )
    return pd.DataFrame(rows)


def test_run_and_write_hides_oos_from_selection_and_writes_complete_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily_path, metadata_path, benchmark_path = _write_input_files(tmp_path)
    output_dir = tmp_path / "output"
    observed_selection_end: pd.Timestamp | None = None

    def fake_selection(
        selection_bars: pd.DataFrame,
        base_config: TrendAllocationConfig,
    ) -> OptimizationResult:
        nonlocal observed_selection_end
        observed_selection_end = pd.to_datetime(selection_bars["datetime"]).max()
        return OptimizationResult(
            _candidate_results(),
            TrendAllocationConfig(
                symbol=base_config.symbol,
                initial_capital=base_config.initial_capital,
                commission_rate=0.0,
                min_commission=0.0,
                slippage_rate=0.0,
                slow_period=20,
                confirmation_days=1,
                slope_lookback=3,
            ),
        )

    monkeypatch.setattr(runner, "select_ema_trend_allocation", fake_selection)

    summary = run_and_write(
        daily_csv=daily_path,
        metadata_csv=metadata_path,
        benchmark_csv=benchmark_path,
        output_dir=output_dir,
        config=TrendAllocationConfig(
            initial_capital=100_000.0,
            commission_rate=0.0,
            min_commission=0.0,
            slippage_rate=0.0,
        ),
        overwrite=True,
    )

    assert observed_selection_end is not None
    assert observed_selection_end <= pd.Timestamp("2024-12-31")
    for filename in (
        "candidate_results.csv",
        "selected_parameters.json",
        "summary.json",
        "signals.csv",
        "trades.csv",
        "positions.csv",
        "equity_curve.csv",
    ):
        assert (output_dir / filename).is_file(), filename
    chart = output_dir / "charts/0001_159915_SZ_易方达创业板ETF.png"
    assert chart.is_file()
    with Image.open(chart) as image:
        assert image.size == (1680, 1000)
    assert (output_dir / "charts/index.csv").is_file()
    assert (output_dir / "charts/render_summary.json").is_file()
    render_summary = json.loads(
        (output_dir / "charts/render_summary.json").read_text(encoding="utf-8")
    )
    assert render_summary["output_dir"] == str(output_dir / "charts")
    assert summary["selection_cutoff"] == "2024-12-31"
    assert summary["candidate_count"] == 8
    assert summary["actual_start_date"] == "2017-08-14"
    assert summary["actual_end_date"] == "2026-07-17"
    for comparison in (
        "candidate",
        "current_strategy",
        "etf_buy_hold",
        "csi300_buy_hold",
    ):
        assert set(summary[comparison]) == {"training", "validation", "oos", "full"}
    strict_json = json.loads(
        (output_dir / "summary.json").read_text(encoding="utf-8"),
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
    )
    assert strict_json == summary
    selected = json.loads(
        (output_dir / "selected_parameters.json").read_text(encoding="utf-8")
    )
    assert selected["config"]["slow_period"] == 20
    assert selected["release"]["status"] in {"accepted", "rejected"}

    with pytest.raises(FileExistsError, match="already contains output"):
        run_and_write(
            daily_csv=daily_path,
            metadata_csv=metadata_path,
            benchmark_csv=benchmark_path,
            output_dir=output_dir,
        )


def test_run_and_write_records_no_feasible_candidate_before_failing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily_path, metadata_path, benchmark_path = _write_input_files(tmp_path)
    output_dir = tmp_path / "rejected"
    candidates = _candidate_results().assign(selected=False, rank=pd.NA)

    monkeypatch.setattr(
        runner,
        "select_ema_trend_allocation",
        lambda selection_bars, base_config: OptimizationResult(candidates, None),
    )

    with pytest.raises(RuntimeError, match="no feasible candidate"):
        run_and_write(
            daily_csv=daily_path,
            metadata_csv=metadata_path,
            benchmark_csv=benchmark_path,
            output_dir=output_dir,
            overwrite=True,
        )

    assert (output_dir / "candidate_results.csv").is_file()
    selected = json.loads(
        (output_dir / "selected_parameters.json").read_text(encoding="utf-8")
    )
    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    assert selected["status"] == "rejected"
    assert selected["release"]["failed_checks"] == ["no_feasible_candidate"]
    assert summary["release"]["status"] == "rejected"
    assert summary["candidate"] is None
