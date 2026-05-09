# CTA Model Pipeline Report

- symbol: `RB0.SHFE`
- interval: `minute60`
- side mode: `both`
- date range: `2010-01-01` -> `2019-12-31`
- by_signal_type: `True`
- max_walk_forward_windows: `3`
- window_mode: `expanding`
- candidate_count: `4142`
- negative_candidate_count: `2344`
- negative_ratio: `0.5659`
- signal_frames_count: `4`

## Metrics
| signal_type                    |   window_id | split   | model             | model_kind             |        auc |   accuracy |   precision |     recall |         f1 |   macro_f1 |   weighted_f1 |   mfe_mae_mae |    mae_mae |   mfe_rmse |   mae_rmse |     mfe_r2 |     mae_r2 |
|:-------------------------------|------------:|:--------|:------------------|:-----------------------|-----------:|-----------:|------------:|-----------:|-----------:|-----------:|--------------:|--------------:|-----------:|-----------:|-----------:|-----------:|-----------:|
| atr_breakout                   |           0 | train   | trade_filter      | hist_gradient_boosting |   1.000000 |   1.000000 |    1.000000 |   1.000000 |   1.000000 | nan        |    nan        |    nan        | nan        | nan        | nan        | nan        | nan        |
| atr_breakout                   |           0 | train   | regime_classifier | random_forest          | nan        |   0.987654 |  nan        | nan        | nan        |   0.662518 |      0.981520 |    nan        | nan        | nan        | nan        | nan        | nan        |
| atr_breakout                   |           0 | train   | mfe_mae           | random_forest          | nan        | nan        |  nan        | nan        | nan        | nan        |    nan        |      1.962094 |   0.836976 |   6.234981 |   1.390425 |   0.370704 |   0.557931 |
| atr_breakout                   |           0 | valid   | trade_filter      | hist_gradient_boosting |   0.675000 |   0.705882 |    0.769231 |   0.833333 |   0.800000 | nan        |    nan        |    nan        | nan        | nan        | nan        | nan        | nan        |
| atr_breakout                   |           0 | valid   | regime_classifier | random_forest          | nan        |   0.970588 |  nan        | nan        | nan        |   0.655914 |      0.956357 |    nan        | nan        | nan        | nan        | nan        | nan        |
| atr_breakout                   |           0 | valid   | mfe_mae           | random_forest          | nan        | nan        |  nan        | nan        | nan        | nan        |    nan        |      2.431909 |   1.162074 |   3.299593 |   1.452684 |  -3.077687 |  -0.035706 |
| atr_breakout                   |           0 | test    | trade_filter      | hist_gradient_boosting |   0.496795 |   0.457447 |    0.432836 |   0.690476 |   0.532110 | nan        |    nan        |    nan        | nan        | nan        | nan        | nan        | nan        |
| atr_breakout                   |           0 | test    | regime_classifier | random_forest          | nan        |   0.957447 |  nan        | nan        | nan        |   0.652063 |      0.936636 |    nan        | nan        | nan        | nan        | nan        | nan        |
| atr_breakout                   |           0 | test    | mfe_mae           | random_forest          | nan        | nan        |  nan        | nan        | nan        | nan        |    nan        |      2.166172 |   1.816420 |   3.046374 |   2.609661 |  -0.408966 |  -0.172417 |
| breakout_pullback_continuation |           0 | train   | trade_filter      | hist_gradient_boosting |   1.000000 |   1.000000 |    1.000000 |   1.000000 |   1.000000 | nan        |    nan        |    nan        | nan        | nan        | nan        | nan        | nan        |
| breakout_pullback_continuation |           0 | train   | regime_classifier | random_forest          | nan        |   0.995086 |  nan        | nan        | nan        |   0.664835 |      0.992642 |    nan        | nan        | nan        | nan        | nan        | nan        |
| breakout_pullback_continuation |           0 | train   | mfe_mae           | random_forest          | nan        | nan        |  nan        | nan        | nan        | nan        |    nan        |      0.960602 |   0.375251 |   1.431557 |   0.674095 |   0.652028 |   0.516843 |
| breakout_pullback_continuation |           0 | valid   | trade_filter      | hist_gradient_boosting |   0.944099 |   0.892308 |    0.807692 |   0.913043 |   0.857143 | nan        |    nan        |    nan        | nan        | nan        | nan        | nan        | nan        |
| breakout_pullback_continuation |           0 | valid   | regime_classifier | random_forest          | nan        |   1.000000 |  nan        | nan        | nan        |   1.000000 |      1.000000 |    nan        | nan        | nan        | nan        | nan        | nan        |
| breakout_pullback_continuation |           0 | valid   | mfe_mae           | random_forest          | nan        | nan        |  nan        | nan        | nan        | nan        |    nan        |      1.214670 |   0.654908 |   1.588319 |   0.791081 |   0.019563 |  -0.364932 |
| breakout_pullback_continuation |           0 | test    | trade_filter      | hist_gradient_boosting |   0.826087 |   0.777778 |    0.724138 |   0.913043 |   0.807692 | nan        |    nan        |    nan        | nan        | nan        | nan        | nan        | nan        |
| breakout_pullback_continuation |           0 | test    | regime_classifier | random_forest          | nan        |   1.000000 |  nan        | nan        | nan        |   1.000000 |      1.000000 |    nan        | nan        | nan        | nan        | nan        | nan        |
| breakout_pullback_continuation |           0 | test    | mfe_mae           | random_forest          | nan        | nan        |  nan        | nan        | nan        | nan        |    nan        |      1.396847 |   0.680810 |   1.685811 |   0.881905 |   0.006036 |  -0.195207 |
| donchian_breakout              |           0 | train   | trade_filter      | hist_gradient_boosting |   1.000000 |   1.000000 |    1.000000 |   1.000000 |   1.000000 | nan        |    nan        |    nan        | nan        | nan        | nan        | nan        | nan        |
| donchian_breakout              |           0 | train   | regime_classifier | random_forest          | nan        |   0.989104 |  nan        | nan        | nan        |   0.662952 |      0.983690 |    nan        | nan        | nan        | nan        | nan        | nan        |
| donchian_breakout              |           0 | train   | mfe_mae           | random_forest          | nan        | nan        |  nan        | nan        | nan        | nan        |    nan        |      1.190099 |   0.741359 |   4.612879 |   1.352037 |   0.335308 |   0.517909 |
| donchian_breakout              |           0 | valid   | trade_filter      | hist_gradient_boosting |   0.836280 |   0.745902 |    0.576271 |   0.850000 |   0.686869 | nan        |    nan        |    nan        | nan        | nan        | nan        | nan        | nan        |
| donchian_breakout              |           0 | valid   | regime_classifier | random_forest          | nan        |   0.991803 |  nan        | nan        | nan        |   0.663866 |      0.987739 |    nan        | nan        | nan        | nan        | nan        | nan        |
| donchian_breakout              |           0 | valid   | mfe_mae           | random_forest          | nan        | nan        |  nan        | nan        | nan        | nan        |    nan        |      1.968164 |   0.865844 |   2.397854 |   1.112071 |  -0.584521 |  -0.022231 |
| donchian_breakout              |           0 | test    | trade_filter      | hist_gradient_boosting |   0.845688 |   0.738255 |    0.500000 |   0.743590 |   0.597938 | nan        |    nan        |    nan        | nan        | nan        | nan        | nan        | nan        |
| donchian_breakout              |           0 | test    | regime_classifier | random_forest          | nan        |   1.000000 |  nan        | nan        | nan        |   1.000000 |      1.000000 |    nan        | nan        | nan        | nan        | nan        | nan        |
| donchian_breakout              |           0 | test    | mfe_mae           | random_forest          | nan        | nan        |  nan        | nan        | nan        | nan        |    nan        |      1.859200 |   1.575691 |   2.579909 |   2.034842 |  -0.103416 |  -0.165480 |
| tight_range_breakout           |           0 | train   | trade_filter      | hist_gradient_boosting |   0.997706 |   0.980000 |    0.994709 |   0.846847 |   0.914842 | nan        |    nan        |    nan        | nan        | nan        | nan        | nan        | nan        |
| tight_range_breakout           |           0 | train   | regime_classifier | random_forest          | nan        |   0.999429 |  nan        | nan        | nan        |   0.999416 |      0.999428 |    nan        | nan        | nan        | nan        | nan        | nan        |
| tight_range_breakout           |           0 | train   | mfe_mae           | random_forest          | nan        | nan        |  nan        | nan        | nan        | nan        |    nan        |      0.977895 |   0.587623 |   1.784085 |   0.823041 |   0.549562 |   0.662767 |
| tight_range_breakout           |           0 | valid   | trade_filter      | hist_gradient_boosting |   0.826718 |   0.860627 |    0.588235 |   0.434783 |   0.500000 | nan        |    nan        |    nan        | nan        | nan        | nan        | nan        | nan        |
| tight_range_breakout           |           0 | valid   | regime_classifier | random_forest          | nan        |   0.979094 |  nan        | nan        | nan        |   0.979823 |      0.979199 |    nan        | nan        | nan        | nan        | nan        | nan        |
| tight_range_breakout           |           0 | valid   | mfe_mae           | random_forest          | nan        | nan        |  nan        | nan        | nan        | nan        |    nan        |      1.432858 |   1.117710 |   1.674695 |   1.494979 |  -0.268685 |  -0.151524 |
| tight_range_breakout           |           0 | test    | trade_filter      | hist_gradient_boosting |   0.881481 |   0.846154 |    0.636364 |   0.777778 |   0.700000 | nan        |    nan        |    nan        | nan        | nan        | nan        | nan        | nan        |
| tight_range_breakout           |           0 | test    | regime_classifier | random_forest          | nan        |   1.000000 |  nan        | nan        | nan        |   1.000000 |      1.000000 |    nan        | nan        | nan        | nan        | nan        | nan        |
| tight_range_breakout           |           0 | test    | mfe_mae           | random_forest          | nan        | nan        |  nan        | nan        | nan        | nan        |    nan        |      1.787514 |   2.105554 |   2.253335 |   3.068692 |   0.218312 |  -0.408092 |

- candidates_csv: `/Users/wuyuliang/code/vnpy/cta/report/backtest/20260427_RB0_minute60_both_model_pipeline/20260427_RB0_minute60_both_candidates.csv`
- feature_table_csv: `/Users/wuyuliang/code/vnpy/cta/report/backtest/20260427_RB0_minute60_both_model_pipeline/20260427_RB0_minute60_both_feature_table.csv`
- predictions_csv: `/Users/wuyuliang/code/vnpy/cta/report/backtest/20260427_RB0_minute60_both_model_pipeline/20260427_RB0_minute60_both_predictions.csv`
- metrics_csv: `/Users/wuyuliang/code/vnpy/cta/report/backtest/20260427_RB0_minute60_both_model_pipeline/20260427_RB0_minute60_both_metrics.csv`
