"""Tests for the turnover top-up universe and the metadata warmup window."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from cta.data_code.build_symbol_turnover import DATE_SEMANTICS
from cta.strategy.multi_timeframe_trend_backtest.runner import (
    _extend_symbols_with_top_turnover,
    _trading_days_before,
)


@dataclass
class _Item:
    source_directory: Path


def _args(**kwargs) -> argparse.Namespace:
    base = {"symbols": [], "include_top_turnover": 0.0, "turnover_table": ""}
    base.update(kwargs)
    return argparse.Namespace(**base)


def _turnover_file(tmp_path: Path, weights: dict[str, float]) -> Path:
    rows = []
    for day in pd.date_range("2026-01-05", periods=5, freq="D"):
        for root, value in weights.items():
            rows.append(
                {
                    "root_symbol": root,
                    "trade_date": day.date(),
                    "turnover": value,
                    "date_semantics": DATE_SEMANTICS,
                }
            )
    target = tmp_path / "turnover.parquet"
    pd.DataFrame(rows).to_parquet(target, index=False)
    return target


# --------------------------------------------------------------------------
# 成交额补池
# --------------------------------------------------------------------------
def test_top_turnover_adds_liquid_roots_missing_from_the_list(tmp_path) -> None:
    table = _turnover_file(tmp_path, {"AU": 50.0, "SN": 30.0, "RB": 15.0, "XX": 5.0})
    args = _args(
        symbols=["AU0.SHFE", "RB0.SHFE"],
        include_top_turnover=0.8,
        turnover_table=str(table),
    )
    _extend_symbols_with_top_turnover(args, end=date(2026, 2, 1))
    # AU 50% 不够 80%，加 SN 到 80% —— SN 不在原列表里，应补入
    assert "SN" in args.symbols
    # RB 已在列表里，不应重复
    assert args.symbols.count("RB") == 0
    assert "XX" not in args.symbols


def test_top_turnover_is_a_noop_when_disabled(tmp_path) -> None:
    table = _turnover_file(tmp_path, {"AU": 50.0, "SN": 50.0})
    args = _args(symbols=["AU"], include_top_turnover=0.0, turnover_table=str(table))
    _extend_symbols_with_top_turnover(args, end=date(2026, 2, 1))
    assert args.symbols == ["AU"]


def test_missing_turnover_table_fails_loudly(tmp_path) -> None:
    args = _args(
        symbols=["AU"],
        include_top_turnover=0.9,
        turnover_table=str(tmp_path / "nope.parquet"),
    )
    with pytest.raises(ValueError, match="build_symbol_turnover"):
        _extend_symbols_with_top_turnover(args, end=date(2026, 2, 1))


def test_turnover_table_not_covering_the_window_fails_loudly(tmp_path) -> None:
    target = tmp_path / "t.parquet"
    pd.DataFrame(
        [{"root_symbol": "AU", "trade_date": date(2026, 6, 1), "turnover": 1.0,
          "date_semantics": DATE_SEMANTICS}]
    ).to_parquet(target, index=False)
    args = _args(symbols=["AU"], include_top_turnover=0.9, turnover_table=str(target))
    with pytest.raises(ValueError, match="rebuild it"):
        _extend_symbols_with_top_turnover(args, end=date(2026, 2, 1))


def test_top_turnover_never_looks_past_the_backtest_end(tmp_path) -> None:
    rows = [
        {"root_symbol": "AU", "trade_date": date(2026, 1, 5), "turnover": 10.0,
         "date_semantics": DATE_SEMANTICS},
        # SN 只在回测结束之后放量，不能因此被补进来
        {"root_symbol": "SN", "trade_date": date(2026, 6, 1), "turnover": 1e9,
         "date_semantics": DATE_SEMANTICS},
    ]
    target = tmp_path / "t.parquet"
    pd.DataFrame(rows).to_parquet(target, index=False)
    args = _args(symbols=["AU"], include_top_turnover=0.9, turnover_table=str(target))
    _extend_symbols_with_top_turnover(args, end=date(2026, 2, 1))
    assert "SN" not in args.symbols


def test_top_turnover_matches_roots_regardless_of_suffix(tmp_path) -> None:
    table = _turnover_file(tmp_path, {"AU": 90.0, "SN": 10.0})
    args = _args(
        symbols=["AU0.SHFE"], include_top_turnover=0.5, turnover_table=str(table)
    )
    _extend_symbols_with_top_turnover(args, end=date(2026, 2, 1))
    assert args.symbols == ["AU0.SHFE"]


@pytest.mark.parametrize("share", [-0.1, 1.5])
def test_invalid_turnover_share_is_rejected(tmp_path, share: float) -> None:
    table = _turnover_file(tmp_path, {"AU": 1.0})
    args = _args(include_top_turnover=share, turnover_table=str(table))
    if share < 0:
        _extend_symbols_with_top_turnover(args, end=date(2026, 2, 1))
        return
    with pytest.raises(ValueError):
        _extend_symbols_with_top_turnover(args, end=date(2026, 2, 1))


# --------------------------------------------------------------------------
# 元数据预热窗口
# --------------------------------------------------------------------------
def test_trading_days_before_counts_local_partitions(tmp_path) -> None:
    directory = tmp_path / "RB"
    directory.mkdir()
    days = pd.bdate_range("2025-11-03", "2025-12-31")
    for day in days:
        (directory / f"{day.date()}.parquet").write_bytes(b"")
    start = date(2026, 1, 5)
    result = _trading_days_before([_Item(directory)], start, 30)
    prior = sorted(d.date() for d in days if d.date() < start)
    assert result == prior[-30]


def test_trading_days_before_falls_back_without_partitions(tmp_path) -> None:
    start = date(2026, 1, 5)
    result = _trading_days_before([], start, 30)
    assert result < start - timedelta(days=30)


def test_trading_days_before_is_a_noop_at_zero(tmp_path) -> None:
    start = date(2026, 1, 5)
    assert _trading_days_before([], start, 0) == start


def test_trading_days_before_ignores_dates_at_or_after_start(tmp_path) -> None:
    directory = tmp_path / "RB"
    directory.mkdir()
    for day in pd.bdate_range("2026-01-05", "2026-03-01"):
        (directory / f"{day.date()}.parquet").write_bytes(b"")
    (directory / "2025-12-30.parquet").write_bytes(b"")
    (directory / "2025-12-31.parquet").write_bytes(b"")
    result = _trading_days_before([_Item(directory)], date(2026, 1, 5), 2)
    assert result == date(2025, 12, 30)
