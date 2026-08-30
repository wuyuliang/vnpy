# Cycle V1 Automatic Candidate Charts Design

## Goal

Every `cycle_v1` backtest report automatically includes one review chart for
every row in `candidates.csv`. Each chart contains exchange-trade-date daily,
completed 60-minute, and raw 1-minute panels.

## Scope

- Preserve `cta/strategy/brooks/config/cycle_v1.yaml` unchanged.
- Draw every candidate, whether it later becomes a plan, order, fill, round
  trip, or candidate-level rejection.
- Support every loaded symbol in a run instead of retaining the current AG-only
  and single-symbol restrictions.
- Keep the existing standalone chart CLI available for regenerating artifacts.
- Do not change candidate generation, cycle classification, execution, risk, or
  official performance.

## Integration

`runner.run_from_args` continues to write the canonical report bundle first.
It then calls the generalized candidate chart generator with the report path
and the run's minute-data root. The generator reads the persisted audit tables,
loads each candidate symbol with the exact run metadata cache, and writes chart
artifacts under the same report directory.

The generated files are:

```text
candidate_charts/
  index.csv
  001_<timestamp>_<symbol>_<setup>_<direction>.png
  ...
CANDIDATE_CHARTS.md
```

`report.md` receives a short Candidate Charts section linking to
`CANDIDATE_CHARTS.md` and `candidate_charts/index.csv`.

## Candidate Outcome

Each candidate is assigned one display outcome from persisted audit tables.
The most advanced observed state wins in this order:

```text
ROUND_TRIP > FILLED > ORDERED > ELIGIBLE_PLAN > candidate rejection > CANDIDATE_RETAINED
```

A candidate-level rejection remains visible with its exact `reason_code` and
`detail`. Missing optional downstream rows are not treated as missing market
data. Duplicate candidate-level rejection rows or conflicting candidate
identities fail chart generation rather than silently selecting one.

## Chart Semantics

Each PNG is one 1680x1240 review card with three candlestick panels:

1. Exchange-trade-date daily bars around the signal.
2. Completed 60-minute bars around the signal, explicitly labeled as visual
   context unless 60 minutes is the configured strategy timeframe.
3. Raw 1-minute bars around the signal.

Every panel marks `signal_time` and `active_time`, plus visible entry, stop, and
target levels. Bars to the right of `SIGNAL` are shaded and labeled
`POST-EVENT REVIEW ONLY`; they are never described as evidence available to the
strategy decision. Titles use the candidate's actual symbol rather than a
hard-coded instrument.

The chart loader starts at the symbol's recorded `effective_warmup_starts`
date, so the daily and hourly panels can use available pre-request context. It
uses the run-scoped effective-dated metadata cache and does not impute market
mechanics.

## Empty And Error Cases

When a report has no candidates, generation succeeds with a header-only
`candidate_charts/index.csv`, an explanatory `CANDIDATE_CHARTS.md`, and no PNG
files. When candidates exist, missing source bars, metadata cache, required
columns, or symbol mappings are errors. The runner does not silently report a
smaller image count than the candidate count.

Repeated generation is idempotent: stale PNG files in `candidate_charts/` are
removed before the new complete set is written, while unrelated report files
are untouched.

## Verification

Automated tests must prove:

- all candidates are indexed without requiring `LARGE_CYCLE_UNAVAILABLE`;
- rejected and progressed candidates receive the correct display outcome;
- multiple symbols use their own daily, 60-minute, and 1-minute contexts;
- one PNG is written per candidate and the report links are present;
- zero candidates produce a valid empty chart bundle;
- stale candidate PNGs do not survive regeneration;
- signal and active markers retain causal timestamps;
- the runner invokes chart generation after writing the report bundle;
- existing `cycle_v1` tests, Ruff, and compile checks remain green.

The final real-data verification reruns the AG `2026-01-01..2026-02-05`
backtest and checks that the image count equals the generated candidate count.
