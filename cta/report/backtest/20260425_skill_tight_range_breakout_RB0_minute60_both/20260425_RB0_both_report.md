# RB0 60min Pre-2020 Backtest Report

## Run Config
- Symbol: `RB0.SHFE`
- Interval: `minute60`
- Trade Side Mode: `both`
- Backtest Range: `2000-01-01` to `2019-12-31` (effective by local data availability)
- Effective Data Coverage: `2010-01-04 09:00:00` to `2019-12-30 23:00:00` (`18871` bars)
- Data Path: `cta/data/origin/minute60/RB/*.parquet`
- Command:
```bash
python3 -m cta.strategy.skill_tight_range_backtest \
  --symbol RB0 --exchange SHFE --interval 60min --trade-side-mode both \
  --start 2000-01-01 --end 2019-12-31
```

## Core Metrics
- Total PnL: `-95768.731`
- Total Return: `-0.095768731`
- Annualized: `-0.0053631756460513`
- Max Drawdown: `0.095768731`
- Sharpe: `-2.0404903827837644`
- Calmar: `-0.0560013230837461`
- Win Rate: `0.2425373134328358`
- Profit Factor: `0.2201266266646123`
- Trade Count: `268`

## By Side
| side   |   trades |       net_pnl |   win_rate |     avg_pnl |
|:-------|---------:|--------------:|-----------:|------------:|
| long   |      135 | -50463.549000 |   0.281481 | -373.804067 |
| short  |      133 | -45305.182000 |   0.203008 | -340.640466 |

## By Exit Year
|   exit_year |    trades |       net_pnl |   win_rate |     avg_pnl |   profit_factor |
|------------:|----------:|--------------:|-----------:|------------:|----------------:|
| 2010.000000 |  7.000000 |   -276.968000 |   0.571429 |  -39.566857 |        0.883033 |
| 2011.000000 |  8.000000 |  -5761.185000 |   0.000000 | -720.148125 |        0.000000 |
| 2012.000000 | 27.000000 |  -9077.925000 |   0.185185 | -336.219444 |        0.233930 |
| 2013.000000 | 30.000000 |  -9093.037000 |   0.300000 | -303.101233 |        0.397955 |
| 2014.000000 | 33.000000 | -14215.308000 |   0.242424 | -430.766909 |        0.155218 |
| 2015.000000 | 46.000000 | -29949.333000 |   0.021739 | -651.072457 |        0.009011 |
| 2016.000000 | 36.000000 |  -1618.547000 |   0.500000 |  -44.959639 |        0.774675 |
| 2017.000000 | 20.000000 |  -1891.535000 |   0.400000 |  -94.576750 |        0.611932 |
| 2018.000000 | 50.000000 | -17079.237000 |   0.200000 | -341.584740 |        0.162310 |
| 2019.000000 | 11.000000 |  -6805.656000 |   0.181818 | -618.696000 |        0.172354 |

- Yearly CSV: `cta/report/backtest/20260425_skill_tight_range_breakout_RB0_minute60_both/20260425_RB0_both_yearly_stats.csv`
## Output Files
- Summary: `cta/report/backtest/20260425_skill_tight_range_breakout_RB0_minute60_both/20260425_RB0_both_summary.csv`
- Trades: `cta/report/backtest/20260425_skill_tight_range_breakout_RB0_minute60_both/20260425_RB0_both_trades.csv`
- Equity: `cta/report/backtest/20260425_skill_tight_range_breakout_RB0_minute60_both/20260425_RB0_both_equity.csv`
- Report: `cta/report/backtest/20260425_skill_tight_range_breakout_RB0_minute60_both/20260425_RB0_both_report.md`

## Known Limits
- Fill model is event-driven next-bar simulation, not tick-level matching.
- Transaction cost uses commission + slippage proxy model.
