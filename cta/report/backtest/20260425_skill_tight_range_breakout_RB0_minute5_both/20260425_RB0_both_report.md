# RB0 5min Pre-2020 Backtest Report

## Run Config
- Symbol: `RB0.SHFE`
- Interval: `minute5`
- Trade Side Mode: `both`
- Backtest Range: `2000-01-01` to `2019-12-31` (effective by local data availability)
- Effective Data Coverage: `2010-01-04 09:00:00` to `2019-12-30 23:00:00` (`154498` bars)
- Data Path: `cta/data/origin/minute5/RB/*.parquet`
- Command:
```bash
python3 -m cta.strategy.skill_tight_range_backtest \
  --symbol RB0 --exchange SHFE --interval 5min --trade-side-mode both \
  --start 2000-01-01 --end 2019-12-31
```

## Core Metrics
- total_pnl: `-3753549.949000001`
- total_return: `-3.753549949`
- annualized: `NaN`
- mdd: `3.753549948999992`
- sharpe: `-10.521446826613134`
- calmar: `NaN`
- winrate: `0.0462379150903741`
- pf: `0.018395976973023`
- trade_count: `2379`

## By Side
| side   |   trades |         net_pnl |   win_rate |      avg_pnl |   profit_factor |
|:-------|---------:|----------------:|-----------:|-------------:|----------------:|
| long   |     1191 | -1837642.508000 |   0.052057 | -1542.940813 |        0.026737 |
| short  |     1188 | -1915907.441000 |   0.040404 | -1612.716701 |        0.010260 |

## By Exit Year
|   exit_year |     trades |         net_pnl |   win_rate |      avg_pnl |   profit_factor |
|------------:|-----------:|----------------:|-----------:|-------------:|----------------:|
| 2010.000000 | 111.000000 |  -191247.841000 |   0.045045 | -1722.953523 |        0.061965 |
| 2011.000000 | 100.000000 |  -204545.307000 |   0.030000 | -2045.453070 |        0.005319 |
| 2012.000000 | 211.000000 |  -363840.130000 |   0.033175 | -1724.360806 |        0.010749 |
| 2013.000000 | 220.000000 |  -309431.373000 |   0.018182 | -1406.506241 |        0.006193 |
| 2014.000000 | 223.000000 |  -374046.802000 |   0.017937 | -1677.339919 |        0.004561 |
| 2015.000000 | 421.000000 | -1038089.814000 |   0.019002 | -2465.771530 |        0.002823 |
| 2016.000000 | 380.000000 |  -506002.513000 |   0.060526 | -1331.585561 |        0.022262 |
| 2017.000000 | 229.000000 |  -183452.502000 |   0.122271 |  -801.102629 |        0.086091 |
| 2018.000000 | 319.000000 |  -316941.099000 |   0.059561 |  -993.545765 |        0.032804 |
| 2019.000000 | 165.000000 |  -265952.568000 |   0.054545 | -1611.833745 |        0.023963 |

- Yearly CSV: `cta/report/backtest/20260425_skill_tight_range_breakout_RB0_minute5_both/20260425_RB0_both_yearly_stats.csv`

## Output Files
- Summary: `cta/report/backtest/20260425_skill_tight_range_breakout_RB0_minute5_both/20260425_RB0_both_summary.csv`
- Trades: `cta/report/backtest/20260425_skill_tight_range_breakout_RB0_minute5_both/20260425_RB0_both_trades.csv`
- Equity: `cta/report/backtest/20260425_skill_tight_range_breakout_RB0_minute5_both/20260425_RB0_both_equity.csv`
- Report: `cta/report/backtest/20260425_skill_tight_range_breakout_RB0_minute5_both/20260425_RB0_both_report.md`

## Known Limits
- Annualized/Calmar may become NaN when equity turns negative under current metric formula.
- Fill model is event-driven next-bar simulation, not tick-level matching.
- Transaction cost uses commission + slippage proxy model.
