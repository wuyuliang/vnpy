# CTA Model Pipeline Report

- symbol: `RB0.SHFE`
- interval: `minute60`
- side mode: `both`
- date range: `2000-01-01` -> `2019-12-31`
- by_signal_type: `True`
- max_walk_forward_windows: `3`
- candidate_count: `4142`
- negative_candidate_count: `2344`
- negative_ratio: `0.5659`
- signal_type_count: `4`

## Metrics
                   signal_type  window_id split             model      auc  accuracy  precision   recall       f1  macro_f1  weighted_f1  mfe_mae_mae  mae_mae  mfe_rmse  mae_rmse     mfe_r2    mae_r2
                  atr_breakout          0 train      trade_filter 1.000000  1.000000   1.000000 1.000000 1.000000       NaN          NaN          NaN      NaN       NaN       NaN        NaN       NaN
                  atr_breakout          0 train regime_classifier      NaN  0.981818        NaN      NaN      NaN  0.660376     0.972824          NaN      NaN       NaN       NaN        NaN       NaN
                  atr_breakout          0 train           mfe_mae      NaN       NaN        NaN      NaN      NaN       NaN          NaN     1.411541 0.960605  2.786635  1.659140   0.447188  0.522318
                  atr_breakout          0 valid      trade_filter 0.595109  0.676056   0.698413 0.916667 0.792793       NaN          NaN          NaN      NaN       NaN       NaN        NaN       NaN
                  atr_breakout          0 valid regime_classifier      NaN  1.000000        NaN      NaN      NaN  1.000000     1.000000          NaN      NaN       NaN       NaN        NaN       NaN
                  atr_breakout          0 valid           mfe_mae      NaN       NaN        NaN      NaN      NaN       NaN          NaN     4.066265 1.474935 15.672700  1.878026  -0.027009  0.026213
                  atr_breakout          0  test      trade_filter 0.613844  0.655738   0.688889 0.815789 0.746988       NaN          NaN          NaN      NaN       NaN       NaN        NaN       NaN
                  atr_breakout          0  test regime_classifier      NaN  0.983607        NaN      NaN      NaN  0.660000     0.975574          NaN      NaN       NaN       NaN        NaN       NaN
                  atr_breakout          0  test           mfe_mae      NaN       NaN        NaN      NaN      NaN       NaN          NaN     1.672810 1.202368  2.016450  1.552469  -0.073156 -0.045538
                  atr_breakout          1 train      trade_filter 1.000000  1.000000   1.000000 1.000000 1.000000       NaN          NaN          NaN      NaN       NaN       NaN        NaN       NaN
                  atr_breakout          1 train regime_classifier      NaN  0.987288        NaN      NaN      NaN  0.662118     0.980982          NaN      NaN       NaN       NaN        NaN       NaN
                  atr_breakout          1 train           mfe_mae      NaN       NaN        NaN      NaN      NaN       NaN          NaN     2.381942 0.886767  7.452600  1.489006   0.328006  0.571327
                  atr_breakout          1 valid      trade_filter 0.580092  0.565574   0.638554 0.697368 0.666667       NaN          NaN          NaN      NaN       NaN       NaN        NaN       NaN
                  atr_breakout          1 valid regime_classifier      NaN  0.983607        NaN      NaN      NaN  0.660000     0.975574          NaN      NaN       NaN       NaN        NaN       NaN
                  atr_breakout          1 valid           mfe_mae      NaN       NaN        NaN      NaN      NaN       NaN          NaN     7.392774 1.193650  9.952509  1.547254 -25.142867 -0.038525
                  atr_breakout          1  test      trade_filter 0.428571  0.468085   0.431034 0.595238 0.500000       NaN          NaN          NaN      NaN       NaN       NaN        NaN       NaN
                  atr_breakout          1  test regime_classifier      NaN  0.957447        NaN      NaN      NaN  0.652063     0.936636          NaN      NaN       NaN       NaN        NaN       NaN
                  atr_breakout          1  test           mfe_mae      NaN       NaN        NaN      NaN      NaN       NaN          NaN     2.043136 1.855167  2.829781  2.617099  -0.215738 -0.179110
breakout_pullback_continuation          0 train      trade_filter 1.000000  1.000000   1.000000 1.000000 1.000000       NaN          NaN          NaN      NaN       NaN       NaN        NaN       NaN
breakout_pullback_continuation          0 train regime_classifier      NaN  0.992248        NaN      NaN      NaN  0.663768     0.988406          NaN      NaN       NaN       NaN        NaN       NaN
breakout_pullback_continuation          0 train           mfe_mae      NaN       NaN        NaN      NaN      NaN       NaN          NaN     1.419560 0.310956  2.053812  0.401159   0.584970  0.705861
breakout_pullback_continuation          0 valid      trade_filter 0.895535  0.802632   0.602740 0.977778 0.745763       NaN          NaN          NaN      NaN       NaN       NaN        NaN       NaN
breakout_pullback_continuation          0 valid regime_classifier      NaN  1.000000        NaN      NaN      NaN  1.000000     1.000000          NaN      NaN       NaN       NaN        NaN       NaN
breakout_pullback_continuation          0 valid           mfe_mae      NaN       NaN        NaN      NaN      NaN       NaN          NaN     2.080883 0.815880  2.341699  1.414974  -0.757864 -0.218986
breakout_pullback_continuation          0  test      trade_filter 0.870366  0.785340   0.720930 0.784810 0.751515       NaN          NaN          NaN      NaN       NaN       NaN        NaN       NaN
breakout_pullback_continuation          0  test regime_classifier      NaN  0.994764        NaN      NaN      NaN  0.665211     0.992158          NaN      NaN       NaN       NaN        NaN       NaN
breakout_pullback_continuation          0  test           mfe_mae      NaN       NaN        NaN      NaN      NaN       NaN          NaN     1.626097 0.671197  2.069887  0.879006  -0.227270 -0.384289
breakout_pullback_continuation          1 train      trade_filter 1.000000  1.000000   1.000000 1.000000 1.000000       NaN          NaN          NaN      NaN       NaN       NaN        NaN       NaN
breakout_pullback_continuation          1 train regime_classifier      NaN  0.996441        NaN      NaN      NaN  0.664975     0.994671          NaN      NaN       NaN       NaN        NaN       NaN
breakout_pullback_continuation          1 train           mfe_mae      NaN       NaN        NaN      NaN      NaN       NaN          NaN     1.036905 0.398085  1.538500  0.758062   0.661332  0.503225
breakout_pullback_continuation          1 valid      trade_filter 0.897717  0.717277   0.837838 0.392405 0.534483       NaN          NaN          NaN      NaN       NaN       NaN        NaN       NaN
breakout_pullback_continuation          1 valid regime_classifier      NaN  0.994764        NaN      NaN      NaN  0.665211     0.992158          NaN      NaN       NaN       NaN        NaN       NaN
breakout_pullback_continuation          1 valid           mfe_mae      NaN       NaN        NaN      NaN      NaN       NaN          NaN     1.484226 0.576992  2.083357  0.744016  -0.243294  0.008239
breakout_pullback_continuation          1  test      trade_filter 0.833992  0.755556   0.730769 0.826087 0.775510       NaN          NaN          NaN      NaN       NaN       NaN        NaN       NaN
breakout_pullback_continuation          1  test regime_classifier      NaN  1.000000        NaN      NaN      NaN  1.000000     1.000000          NaN      NaN       NaN       NaN        NaN       NaN
breakout_pullback_continuation          1  test           mfe_mae      NaN       NaN        NaN      NaN      NaN       NaN          NaN     1.555934 0.688180  1.907326  0.864741  -0.272340 -0.149137
             donchian_breakout          0 train      trade_filter 1.000000  1.000000   1.000000 1.000000 1.000000       NaN          NaN          NaN      NaN       NaN       NaN        NaN       NaN
             donchian_breakout          0 train regime_classifier      NaN  0.992908        NaN      NaN      NaN  0.664000     0.989390          NaN      NaN       NaN       NaN        NaN       NaN
             donchian_breakout          0 train           mfe_mae      NaN       NaN        NaN      NaN      NaN       NaN          NaN     1.054083 1.212329  1.622221  2.222979   0.613888  0.461097
             donchian_breakout          0 valid      trade_filter 0.849475  0.767528   0.605634 0.551282 0.577181       NaN          NaN          NaN      NaN       NaN       NaN        NaN       NaN
             donchian_breakout          0 valid regime_classifier      NaN  0.992620        NaN      NaN      NaN  0.663717     0.988946          NaN      NaN       NaN       NaN        NaN       NaN
             donchian_breakout          0 valid           mfe_mae      NaN       NaN        NaN      NaN      NaN       NaN          NaN     2.494975 2.470292  9.561495  2.917685  -0.018402 -3.564211
             donchian_breakout          0  test      trade_filter 0.813333  0.706329   0.653846 0.364286 0.467890       NaN          NaN          NaN      NaN       NaN       NaN        NaN       NaN
             donchian_breakout          0  test regime_classifier      NaN  0.984810        NaN      NaN      NaN  0.661170     0.977278          NaN      NaN       NaN       NaN        NaN       NaN
             donchian_breakout          0  test           mfe_mae      NaN       NaN        NaN      NaN      NaN       NaN          NaN     1.599900 2.084732  2.014891  2.411771  -0.066274 -3.403037
             donchian_breakout          1 train      trade_filter 1.000000  1.000000   1.000000 1.000000 1.000000       NaN          NaN          NaN      NaN       NaN       NaN        NaN       NaN
             donchian_breakout          1 train regime_classifier      NaN  0.992767        NaN      NaN      NaN  0.663751     0.989171          NaN      NaN       NaN       NaN        NaN       NaN
             donchian_breakout          1 train           mfe_mae      NaN       NaN        NaN      NaN      NaN       NaN          NaN     1.583089 0.882184  6.487491  1.642049   0.162631  0.498587
             donchian_breakout          1 valid      trade_filter 0.835350  0.701266   0.661765 0.321429 0.432692       NaN          NaN          NaN      NaN       NaN       NaN        NaN       NaN
             donchian_breakout          1 valid regime_classifier      NaN  0.984810        NaN      NaN      NaN  0.661170     0.977278          NaN      NaN       NaN       NaN        NaN       NaN
             donchian_breakout          1 valid           mfe_mae      NaN       NaN        NaN      NaN      NaN       NaN          NaN     6.913228 1.127317  8.751192  1.402515 -19.114090 -0.489003
             donchian_breakout          1  test      trade_filter 0.850350  0.751678   0.521739 0.615385 0.564706       NaN          NaN          NaN      NaN       NaN       NaN        NaN       NaN
             donchian_breakout          1  test regime_classifier      NaN  1.000000        NaN      NaN      NaN  1.000000     1.000000          NaN      NaN       NaN       NaN        NaN       NaN
             donchian_breakout          1  test           mfe_mae      NaN       NaN        NaN      NaN      NaN       NaN          NaN     2.091591 1.544955  3.183344  2.007959  -0.679956 -0.134888
          tight_range_breakout          0 train      trade_filter 1.000000  1.000000   1.000000 1.000000 1.000000       NaN          NaN          NaN      NaN       NaN       NaN        NaN       NaN
          tight_range_breakout          0 train regime_classifier      NaN  0.996441        NaN      NaN      NaN  0.996627     0.996439          NaN      NaN       NaN       NaN        NaN       NaN
          tight_range_breakout          0 train           mfe_mae      NaN       NaN        NaN      NaN      NaN       NaN          NaN     1.572513 0.593925  3.589714  0.892154   0.376172  0.669566
          tight_range_breakout          0 valid      trade_filter 0.726945  0.885659   0.475410 0.252174 0.329545       NaN          NaN          NaN      NaN       NaN       NaN        NaN       NaN
          tight_range_breakout          0 valid regime_classifier      NaN  0.630814        NaN      NaN      NaN  0.667034     0.598549          NaN      NaN       NaN       NaN        NaN       NaN
          tight_range_breakout          0 valid           mfe_mae      NaN       NaN        NaN      NaN      NaN       NaN          NaN     1.791842 1.212170  2.194978  1.636836  -0.274044 -0.341230
          tight_range_breakout          0  test      trade_filter 0.747114  0.846685   0.514706 0.309735 0.386740       NaN          NaN          NaN      NaN       NaN       NaN        NaN       NaN
          tight_range_breakout          0  test regime_classifier      NaN  0.780387        NaN      NaN      NaN  0.793018     0.773318          NaN      NaN       NaN       NaN        NaN       NaN
          tight_range_breakout          0  test           mfe_mae      NaN       NaN        NaN      NaN      NaN       NaN          NaN     1.590343 1.713833  2.029036  1.987157  -0.185461 -1.132872
          tight_range_breakout          1 train      trade_filter 0.999131  0.984006   0.992647 0.870968 0.927835       NaN          NaN          NaN      NaN       NaN       NaN        NaN       NaN
          tight_range_breakout          1 train regime_classifier      NaN  0.999238        NaN      NaN      NaN  0.999233     0.999238          NaN      NaN       NaN       NaN        NaN       NaN
          tight_range_breakout          1 train           mfe_mae      NaN       NaN        NaN      NaN      NaN       NaN          NaN     0.913747 0.581958  1.908468  0.807057   0.544097  0.690264
          tight_range_breakout          1 valid      trade_filter 0.764944  0.848066   0.540541 0.176991 0.266667       NaN          NaN          NaN      NaN       NaN       NaN        NaN       NaN
          tight_range_breakout          1 valid regime_classifier      NaN  0.966851        NaN      NaN      NaN  0.964905     0.966446          NaN      NaN       NaN       NaN        NaN       NaN
          tight_range_breakout          1 valid           mfe_mae      NaN       NaN        NaN      NaN      NaN       NaN          NaN     1.549607 1.103047  2.073383  1.492319  -0.237846 -0.202883
          tight_range_breakout          1  test      trade_filter 0.874074  0.846154   0.666667 0.666667 0.666667       NaN          NaN          NaN      NaN       NaN       NaN        NaN       NaN
          tight_range_breakout          1  test regime_classifier      NaN  1.000000        NaN      NaN      NaN  1.000000     1.000000          NaN      NaN       NaN       NaN        NaN       NaN
          tight_range_breakout          1  test           mfe_mae      NaN       NaN        NaN      NaN      NaN       NaN          NaN     2.174633 2.289043  2.625591  3.161169  -0.061296 -0.494238

- candidates_csv: `/Users/wuyuliang/code/vnpy/cta/report/backtest/20260426_RB0_minute60_both_model_pipeline/20260426_RB0_minute60_both_candidates.csv`
- feature_table_csv: `/Users/wuyuliang/code/vnpy/cta/report/backtest/20260426_RB0_minute60_both_model_pipeline/20260426_RB0_minute60_both_feature_table.csv`
- predictions_csv: `/Users/wuyuliang/code/vnpy/cta/report/backtest/20260426_RB0_minute60_both_model_pipeline/20260426_RB0_minute60_both_predictions.csv`
- metrics_csv: `/Users/wuyuliang/code/vnpy/cta/report/backtest/20260426_RB0_minute60_both_model_pipeline/20260426_RB0_minute60_both_metrics.csv`
