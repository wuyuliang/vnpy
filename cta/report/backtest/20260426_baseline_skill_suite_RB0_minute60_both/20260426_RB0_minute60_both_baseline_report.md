# Baseline Skill Suite Report

- symbol: `RB0.SHFE`
- interval: `minute60`
- trade_side_mode: `both`
- range: `2000-01-01` -> `2019-12-31`
- bars: `18871`
- strategies: `donchian_breakout, atr_breakout, tight_range_breakout, breakout_pullback_continuation`

## Summary
| signal_type                    |   total_return |   annualized |      mdd |    sharpe |    calmar |   winrate |       pf |   trade_count |
|:-------------------------------|---------------:|-------------:|---------:|----------:|----------:|----------:|---------:|--------------:|
| donchian_breakout              |       0.001062 |     0.000057 | 0.001081 |  0.141852 |  0.052464 |  0.384615 | 1.145477 |           156 |
| atr_breakout                   |      -0.002305 |    -0.000123 | 0.003030 | -0.374905 | -0.040678 |  0.319853 | 0.757580 |           272 |
| tight_range_breakout           |      -0.096933 |    -0.005432 | 0.096933 | -1.958446 | -0.056035 |  0.243636 | 0.238477 |           275 |
| breakout_pullback_continuation |      -0.002161 |    -0.000116 | 0.002161 | -0.821285 | -0.053473 |  0.295082 | 0.502227 |           183 |

- suite_summary_csv: `/Users/wuyuliang/code/vnpy/cta/report/backtest/20260426_baseline_skill_suite_RB0_minute60_both/20260426_RB0_minute60_both_suite_summary.csv`
- training_samples_csv: `/Users/wuyuliang/code/vnpy/cta/report/backtest/20260426_baseline_skill_suite_RB0_minute60_both/20260426_RB0_minute60_both_training_samples.csv`
