"""Rolling train/validation/locked-test folds with explicit embargo."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd


@dataclass(frozen=True)
class WalkForwardFold:
    fold_id: str
    train_start: date
    train_end: date
    validation_start: date
    validation_end: date
    test_start: date
    test_end: date
    embargo_days: int


def generate_folds(
    start: date,
    end: date,
    *,
    train_years: int,
    validation_years: int,
    test_years: int,
    step_years: int,
    embargo_days: int,
) -> list[WalkForwardFold]:
    if min(train_years, validation_years, test_years, step_years) < 1 or embargo_days < 0:
        raise ValueError("invalid walk-forward lengths")
    folds: list[WalkForwardFold] = []
    anchor = pd.Timestamp(start)
    final = pd.Timestamp(end)
    while True:
        train_start = anchor
        train_end = train_start + pd.DateOffset(years=train_years) - pd.Timedelta(days=1)
        validation_start = train_end + pd.Timedelta(days=1 + embargo_days)
        validation_end = validation_start + pd.DateOffset(years=validation_years) - pd.Timedelta(days=1)
        test_start = validation_end + pd.Timedelta(days=1 + embargo_days)
        test_end = test_start + pd.DateOffset(years=test_years) - pd.Timedelta(days=1)
        if test_end > final:
            break
        folds.append(
            WalkForwardFold(
                fold_id=f"WF{len(folds):02d}",
                train_start=train_start.date(),
                train_end=train_end.date(),
                validation_start=validation_start.date(),
                validation_end=validation_end.date(),
                test_start=test_start.date(),
                test_end=test_end.date(),
                embargo_days=embargo_days,
            )
        )
        anchor += pd.DateOffset(years=step_years)
    return folds


__all__ = ["WalkForwardFold", "generate_folds"]
