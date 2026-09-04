# Brooks 纯价格行为日内剥头皮回测报告

## 1. 最终状态和未通过项
- 状态：`INCONCLUSIVE`
- 日均 1% 目标未通过。
- 月均 20% 目标未通过。
- 至少一个必需分组 FAILED 或 INCONCLUSIVE。
- RiskScore 未通过。

## 2. 数据、实际日期和因果审计
```json
{
  "actual_end": "2020-05-15T15:00:00+08:00",
  "actual_start": "2020-04-27T09:01:00+08:00",
  "actual_symbol_ranges": {
    "CU0.SHFE": {
      "first_bar_end": "2020-04-27T09:01:00+08:00",
      "last_bar_end": "2020-05-15T15:00:00+08:00"
    },
    "RB0.SHFE": {
      "first_bar_end": "2020-04-27T09:01:00+08:00",
      "last_bar_end": "2020-05-15T15:00:00+08:00"
    }
  },
  "bar_timestamp_semantics": "source timestamp is bar_end; exact segment-open snapshots are excluded",
  "base_slippage_ticks": {
    "CU2006.SHF": 1.0,
    "RB2010.SHF": 1.0
  },
  "boundary_opening_snapshots_dropped": {
    "CU0.SHFE": 16,
    "RB0.SHFE": 16
  },
  "causal_audit_passed": true,
  "chart_source_interval_minutes": 1,
  "excluded_intrasession_roll_sessions": {
    "CU0.SHFE": [],
    "RB0.SHFE": []
  },
  "ledger_reconciliation_passed": true,
  "metadata_manifest": {
    "created_at": "2026-08-09T21:53:04.849361+08:00",
    "files": {
      "contract_daily.csv": {
        "downloaded_at": "2026-08-09T21:53:04.849361+08:00",
        "max_date": "2026-04-16",
        "min_date": "2018-01-03",
        "rows": 4134,
        "sha256": "8993040b0a9d2c744b2f9c11d1cb3a53cb69b70fb0976631deccf92e56693560",
        "source_sha256": "8993040b0a9d2c744b2f9c11d1cb3a53cb69b70fb0976631deccf92e56693560",
        "source_url_or_file": "SHFE official kx/js + Tushare SHFE calendar/basic mirror"
      },
      "contract_specs.csv": {
        "downloaded_at": "2026-08-09T21:53:04.849361+08:00",
        "max_date": "2026-10-15",
        "min_date": "2018-02-09",
        "rows": 126,
        "sha256": "0491115784faa7b73c92d92745c0ab6d35a84e70370f246ae2f9c514171568df",
        "source_sha256": "0491115784faa7b73c92d92745c0ab6d35a84e70370f246ae2f9c514171568df",
        "source_url_or_file": "SHFE official kx/js + Tushare SHFE calendar/basic mirror"
      },
      "exchange_calendar.csv": {
        "downloaded_at": "2026-08-09T21:53:04.849361+08:00",
        "max_date": "2026-05-28",
        "min_date": "2017-11-27",
        "rows": 2061,
        "sha256": "2c047739c6230ffd57b315ec98e6f4d0d9a3b3b80a02f351f9f15669e4a4df50",
        "source_sha256": "2c047739c6230ffd57b315ec98e6f4d0d9a3b3b80a02f351f9f15669e4a4df50",
        "source_url_or_file": "SHFE official kx/js + Tushare SHFE calendar/basic mirror"
      },
      "fee_margin_schedule.csv": {
        "downloaded_at": "2026-08-09T21:53:04.849361+08:00",
        "max_date": "2026-04-15",
        "min_date": "2018-01-03",
        "rows": 4134,
        "sha256": "51d3aa81c12b0b12a2f1c3f526a2e6b4bbdfcff3988a529dabb11eadd02dbf83",
        "source_sha256": "51d3aa81c12b0b12a2f1c3f526a2e6b4bbdfcff3988a529dabb11eadd02dbf83",
        "source_url_or_file": "SHFE official kx/js + Tushare SHFE calendar/basic mirror"
      }
    },
    "schema_version": 1,
    "source_audit": {
      "limit_derivation": "spec_margin_minus_2pp_then_floor_both_prices_to_tick",
      "path": "shfe_source_audit.jsonl.gz",
      "sha256": "1fdc1a7824f1b33a7a48d0b7ce92941ced0e6f8d7649e1d455e9f0cfaaa4b961",
      "source_files": 4070
    }
  },
  "portfolio_excluded_roll_sessions": [],
  "requested_end": "2020-05-15",
  "requested_start": "2020-04-27",
  "rollover_audit_passed": true,
  "source_files": {
    "CU0.SHFE": {
      "cta/data/origin/minute/CU0.SHF/2020-04-20.parquet": "c6b457ea97bccc0f9dc9892b1af1210425217dc385fef1243b6df0dc5b67639d",
      "cta/data/origin/minute/CU0.SHF/2020-04-21.parquet": "144f422a12dbca150968f9e389e646e27850cf7cabc6d8291956a8f003cc3118",
      "cta/data/origin/minute/CU0.SHF/2020-04-22.parquet": "468870fbb59fcd1300a952b789264661b94913bfb688966a33cb324dc318611e",
      "cta/data/origin/minute/CU0.SHF/2020-04-23.parquet": "43fa0293c414cc9df1a4e3f90dd1dc3d3e8e0857fb8a3a7b1c611ac32557f075",
      "cta/data/origin/minute/CU0.SHF/2020-04-24.parquet": "0fcbd257597292030341c7a88b518b8de7bbd700da2b7d067aca832bb35c235a",
      "cta/data/origin/minute/CU0.SHF/2020-04-27.parquet": "7540f05c0df54dd9fcf4b6a716b42d4f691188c09d3894578a8915418c5e70dc",
      "cta/data/origin/minute/CU0.SHF/2020-04-28.parquet": "ae2fc90f8b54b85ac333ce2a5f409c5163da71e803f978256a79c2a0a29354a3",
      "cta/data/origin/minute/CU0.SHF/2020-04-29.parquet": "6d16eaf8b69f0843dec0a05ecfd5cb328a22f6a134a7db0d6f66f8c123b74738",
      "cta/data/origin/minute/CU0.SHF/2020-04-30.parquet": "54ead118765b6d6f7d9353c50817c90845a50f7401b898226c03737cf2ab53d5",
      "cta/data/origin/minute/CU0.SHF/2020-05-06.parquet": "836da704b87f03bac57c656099fe700a327b7e58ff2e7db38fb5ebe741feab28",
      "cta/data/origin/minute/CU0.SHF/2020-05-07.parquet": "4d79b58b1c5a8ae778ba6ab304db658cd9b5e1748f7135596e817c907019ac41",
      "cta/data/origin/minute/CU0.SHF/2020-05-08.parquet": "bc783dd7e5e05270f7279ec581def1823f88105252011455421f362377a72823",
      "cta/data/origin/minute/CU0.SHF/2020-05-11.parquet": "2406c7e9e4555bdbbf00b7b53b49fb27c768fdb3b0b35329dd2f040725494385",
      "cta/data/origin/minute/CU0.SHF/2020-05-12.parquet": "a91b43dc46f0afa9529237a3091571e0835dc79b2d0adcc0f538f90af4e93eeb",
      "cta/data/origin/minute/CU0.SHF/2020-05-13.parquet": "1f9fad96b23774ad0bdb4b10d3c8c2bce43942d92f6179ac8cdfeec951b7a19b",
      "cta/data/origin/minute/CU0.SHF/2020-05-14.parquet": "b44f41ab1d9a656c8c1fc246e86dbca8115f9c189dac730b80d0eb69dc91ad10",
      "cta/data/origin/minute/CU0.SHF/2020-05-15.parquet": "198eded79160ef07b424e9c58ac3f8da6af90203d0dd6ba2cf2e49d7d440dc53"
    },
    "RB0.SHFE": {
      "cta/data/origin/minute/RB/2020-04-20.parquet": "dc4847dd786860cb51975dfa0f504cfcc62d2497abf42614e528f28e47dad0cd",
      "cta/data/origin/minute/RB/2020-04-21.parquet": "39eb25ffc49ee0f2cb74c2f4e7878a3a6594648196bc8fb6d0365a82f9749ca1",
      "cta/data/origin/minute/RB/2020-04-22.parquet": "15f1606faf62293810b704fbe96d8b8e679517b20ae663b6d1ae7d314596aa7d",
      "cta/data/origin/minute/RB/2020-04-23.parquet": "96a8857ac36e58c61567cf3016a353788b98fecc63d8d21cb2c247967acdd9d4",
      "cta/data/origin/minute/RB/2020-04-24.parquet": "ff40f8927638ce843c374e75589f1c9f45f97aa5498c6c0b3dac856e4a15b007",
      "cta/data/origin/minute/RB/2020-04-27.parquet": "482a52408c1059b9bb013091689456578a457a7aa93b8d9cb20ee5187b0119f7",
      "cta/data/origin/minute/RB/2020-04-28.parquet": "22d23d3dcf459f6aac10a5dc7b9e3eaeeca15c38a33fe1a53f855b6aeebc528a",
      "cta/data/origin/minute/RB/2020-04-29.parquet": "5134bba230fc11d1ae21973f68c36ecd0d1fd52df17392c9609af7eabcf7e3ed",
      "cta/data/origin/minute/RB/2020-04-30.parquet": "b4da66275a3d6c09ec83f5b0870d2af2245a668d73ff6d0a6258b5f67959d7bb",
      "cta/data/origin/minute/RB/2020-05-06.parquet": "4b52716e78b4e511b15e4f104137bb674cba0bbfc72c11ac3c0d570ae389b9e0",
      "cta/data/origin/minute/RB/2020-05-07.parquet": "2898636c68b29620da931f0b239b8081524a2aa87225d82edceafb16387c3a2e",
      "cta/data/origin/minute/RB/2020-05-08.parquet": "f63632105c2c14b62cd1160a72d39d8b8a7f1daea6ff37af5881af22c04eba9a",
      "cta/data/origin/minute/RB/2020-05-11.parquet": "0879a9c776e882f0c1578fb5e33cef0ebbd54bba0c1cedc7d7ae3b0134700299",
      "cta/data/origin/minute/RB/2020-05-12.parquet": "8d13f75f5263baae1631df277882c47e967bdc8a22525922941729adf65cbcc9",
      "cta/data/origin/minute/RB/2020-05-13.parquet": "ddcb7d399170457f19adcfcac8012bae7b79cd115e24c3af5ded6d4d37564caf",
      "cta/data/origin/minute/RB/2020-05-14.parquet": "e09fd66a906a27bbdb5721ef1bb7f2c0b0cf8dc2ef57284b18c75779aba5420e",
      "cta/data/origin/minute/RB/2020-05-15.parquet": "6dc03b214df47063af9e0f217a0613b425528e148ca11ad475b2d8e158a0fdb7"
    }
  },
  "source_hash_stability_passed": true,
  "symbols": [
    "RB0.SHFE",
    "CU0.SHFE"
  ],
  "vendor_midnight_timestamps_shifted": {
    "CU0.SHFE": 0,
    "RB0.SHFE": 0
  },
  "vendor_no_night_rows_dropped": {
    "CU0.SHFE": 360,
    "RB0.SHFE": 242
  },
  "vendor_zero_placeholders_dropped": {
    "CU0.SHFE": 0,
    "RB0.SHFE": 0
  }
}
```

## 3. RB 单品种结果
```json
{
  "equity_metrics": {
    "average_daily_return": 0.0,
    "average_monthly_return": 0.0,
    "daily_count": 12,
    "es99": 0.0,
    "max_drawdown": 0.0,
    "var99": 0.0,
    "worst_daily_return": 0.0
  },
  "final_equity": 200000.0,
  "risk_score": {
    "bootstrap_loss_probability": null,
    "items": [
      {
        "maximum": 10,
        "name": "max_drawdown",
        "passed": true,
        "points": 10
      },
      {
        "maximum": 8,
        "name": "margin_survival",
        "passed": true,
        "points": 8
      },
      {
        "maximum": 7,
        "name": "bootstrap_survival",
        "passed": false,
        "points": 0
      },
      {
        "maximum": 8,
        "name": "worst_day",
        "passed": true,
        "points": 8
      },
      {
        "maximum": 6,
        "name": "var99",
        "passed": true,
        "points": 6
      },
      {
        "maximum": 6,
        "name": "es99",
        "passed": true,
        "points": 6
      },
      {
        "maximum": 8,
        "name": "fee_stress",
        "passed": false,
        "points": 0
      },
      {
        "maximum": 8,
        "name": "slippage_stress",
        "passed": false,
        "points": 0
      },
      {
        "maximum": 4,
        "name": "gap_limit_stress",
        "passed": true,
        "points": 4
      },
      {
        "maximum": 5,
        "name": "risk_budgets",
        "passed": true,
        "points": 5
      },
      {
        "maximum": 5,
        "name": "circuit_breakers",
        "passed": true,
        "points": 5
      },
      {
        "maximum": 5,
        "name": "session_flat",
        "passed": true,
        "points": 5
      },
      {
        "maximum": 4,
        "name": "positive_years",
        "passed": false,
        "points": 0
      },
      {
        "maximum": 3,
        "name": "positive_quarters",
        "passed": false,
        "points": 0
      },
      {
        "maximum": 3,
        "name": "rb_cu_survival",
        "passed": true,
        "points": 3
      },
      {
        "maximum": 4,
        "name": "causal_audit",
        "passed": true,
        "points": 4
      },
      {
        "maximum": 3,
        "name": "rollover_audit",
        "passed": true,
        "points": 3
      },
      {
        "maximum": 3,
        "name": "ledger_audit",
        "passed": true,
        "points": 3
      }
    ],
    "score": 70,
    "status": "FAILED",
    "vetoes": []
  },
  "status": "INCONCLUSIVE",
  "trade_metrics": {
    "average_holding_1m_bars": 0.0,
    "average_mae_r": 0.0,
    "average_mfe_r": 0.0,
    "expected_pnl": 0.0,
    "expected_r": 0.0,
    "loss_count": 0,
    "net_average_payoff": 0.0,
    "net_pnl": 0.0,
    "net_win_rate": 0.0,
    "profit_factor": 0.0,
    "sample_count": 0,
    "scratch_count": 0,
    "total_fee": 0.0,
    "total_slippage": 0.0,
    "wilson_high": 0.0,
    "wilson_low": 0.0,
    "win_count": 0
  }
}
```

## 4. CU 单品种结果
```json
{
  "equity_metrics": {
    "average_daily_return": 0.0,
    "average_monthly_return": 0.0,
    "daily_count": 12,
    "es99": 0.0,
    "max_drawdown": 0.0,
    "var99": 0.0,
    "worst_daily_return": 0.0
  },
  "final_equity": 200000.0,
  "risk_score": {
    "bootstrap_loss_probability": null,
    "items": [
      {
        "maximum": 10,
        "name": "max_drawdown",
        "passed": true,
        "points": 10
      },
      {
        "maximum": 8,
        "name": "margin_survival",
        "passed": true,
        "points": 8
      },
      {
        "maximum": 7,
        "name": "bootstrap_survival",
        "passed": false,
        "points": 0
      },
      {
        "maximum": 8,
        "name": "worst_day",
        "passed": true,
        "points": 8
      },
      {
        "maximum": 6,
        "name": "var99",
        "passed": true,
        "points": 6
      },
      {
        "maximum": 6,
        "name": "es99",
        "passed": true,
        "points": 6
      },
      {
        "maximum": 8,
        "name": "fee_stress",
        "passed": false,
        "points": 0
      },
      {
        "maximum": 8,
        "name": "slippage_stress",
        "passed": false,
        "points": 0
      },
      {
        "maximum": 4,
        "name": "gap_limit_stress",
        "passed": true,
        "points": 4
      },
      {
        "maximum": 5,
        "name": "risk_budgets",
        "passed": true,
        "points": 5
      },
      {
        "maximum": 5,
        "name": "circuit_breakers",
        "passed": true,
        "points": 5
      },
      {
        "maximum": 5,
        "name": "session_flat",
        "passed": true,
        "points": 5
      },
      {
        "maximum": 4,
        "name": "positive_years",
        "passed": false,
        "points": 0
      },
      {
        "maximum": 3,
        "name": "positive_quarters",
        "passed": false,
        "points": 0
      },
      {
        "maximum": 3,
        "name": "rb_cu_survival",
        "passed": true,
        "points": 3
      },
      {
        "maximum": 4,
        "name": "causal_audit",
        "passed": true,
        "points": 4
      },
      {
        "maximum": 3,
        "name": "rollover_audit",
        "passed": true,
        "points": 3
      },
      {
        "maximum": 3,
        "name": "ledger_audit",
        "passed": true,
        "points": 3
      }
    ],
    "score": 70,
    "status": "FAILED",
    "vetoes": []
  },
  "status": "INCONCLUSIVE",
  "trade_metrics": {
    "average_holding_1m_bars": 0.0,
    "average_mae_r": 0.0,
    "average_mfe_r": 0.0,
    "expected_pnl": 0.0,
    "expected_r": 0.0,
    "loss_count": 0,
    "net_average_payoff": 0.0,
    "net_pnl": 0.0,
    "net_win_rate": 0.0,
    "profit_factor": 0.0,
    "sample_count": 0,
    "scratch_count": 0,
    "total_fee": 0.0,
    "total_slippage": 0.0,
    "wilson_high": 0.0,
    "wilson_low": 0.0,
    "win_count": 0
  }
}
```

## 5. RB+CU 共享账户结果
```json
{
  "accepted_candidate_count": 0,
  "baselines": {
    "always_in_only": {
      "status": "NOT_ENABLED_IN_FROZEN_RULE_RUN"
    },
    "brooks_v3_existing": {
      "status": "NOT_COMPARABLE_OLD_ACCOUNTING"
    },
    "no_trade": {
      "net_pnl": 0.0,
      "status": "REFERENCE"
    },
    "scalp_rule_full": {
      "net_pnl": 0.0
    }
  },
  "candidate_count": 0,
  "config_sha256": "12105f911baf4d1e19f73fc26221dfffa4d0691b4e20b606412e05b4d8e7b4c5",
  "daily_target_passed": false,
  "independent_symbol_results": {
    "CU0.SHFE": {
      "equity_metrics": {
        "average_daily_return": 0.0,
        "average_monthly_return": 0.0,
        "daily_count": 12,
        "es99": 0.0,
        "max_drawdown": 0.0,
        "var99": 0.0,
        "worst_daily_return": 0.0
      },
      "final_equity": 200000.0,
      "risk_score": {
        "bootstrap_loss_probability": null,
        "items": [
          {
            "maximum": 10,
            "name": "max_drawdown",
            "passed": true,
            "points": 10
          },
          {
            "maximum": 8,
            "name": "margin_survival",
            "passed": true,
            "points": 8
          },
          {
            "maximum": 7,
            "name": "bootstrap_survival",
            "passed": false,
            "points": 0
          },
          {
            "maximum": 8,
            "name": "worst_day",
            "passed": true,
            "points": 8
          },
          {
            "maximum": 6,
            "name": "var99",
            "passed": true,
            "points": 6
          },
          {
            "maximum": 6,
            "name": "es99",
            "passed": true,
            "points": 6
          },
          {
            "maximum": 8,
            "name": "fee_stress",
            "passed": false,
            "points": 0
          },
          {
            "maximum": 8,
            "name": "slippage_stress",
            "passed": false,
            "points": 0
          },
          {
            "maximum": 4,
            "name": "gap_limit_stress",
            "passed": true,
            "points": 4
          },
          {
            "maximum": 5,
            "name": "risk_budgets",
            "passed": true,
            "points": 5
          },
          {
            "maximum": 5,
            "name": "circuit_breakers",
            "passed": true,
            "points": 5
          },
          {
            "maximum": 5,
            "name": "session_flat",
            "passed": true,
            "points": 5
          },
          {
            "maximum": 4,
            "name": "positive_years",
            "passed": false,
            "points": 0
          },
          {
            "maximum": 3,
            "name": "positive_quarters",
            "passed": false,
            "points": 0
          },
          {
            "maximum": 3,
            "name": "rb_cu_survival",
            "passed": true,
            "points": 3
          },
          {
            "maximum": 4,
            "name": "causal_audit",
            "passed": true,
            "points": 4
          },
          {
            "maximum": 3,
            "name": "rollover_audit",
            "passed": true,
            "points": 3
          },
          {
            "maximum": 3,
            "name": "ledger_audit",
            "passed": true,
            "points": 3
          }
        ],
        "score": 70,
        "status": "FAILED",
        "vetoes": []
      },
      "status": "INCONCLUSIVE",
      "trade_metrics": {
        "average_holding_1m_bars": 0.0,
        "average_mae_r": 0.0,
        "average_mfe_r": 0.0,
        "expected_pnl": 0.0,
        "expected_r": 0.0,
        "loss_count": 0,
        "net_average_payoff": 0.0,
        "net_pnl": 0.0,
        "net_win_rate": 0.0,
        "profit_factor": 0.0,
        "sample_count": 0,
        "scratch_count": 0,
        "total_fee": 0.0,
        "total_slippage": 0.0,
        "wilson_high": 0.0,
        "wilson_low": 0.0,
        "win_count": 0
      }
    },
    "RB0.SHFE": {
      "equity_metrics": {
        "average_daily_return": 0.0,
        "average_monthly_return": 0.0,
        "daily_count": 12,
        "es99": 0.0,
        "max_drawdown": 0.0,
        "var99": 0.0,
        "worst_daily_return": 0.0
      },
      "final_equity": 200000.0,
      "risk_score": {
        "bootstrap_loss_probability": null,
        "items": [
          {
            "maximum": 10,
            "name": "max_drawdown",
            "passed": true,
            "points": 10
          },
          {
            "maximum": 8,
            "name": "margin_survival",
            "passed": true,
            "points": 8
          },
          {
            "maximum": 7,
            "name": "bootstrap_survival",
            "passed": false,
            "points": 0
          },
          {
            "maximum": 8,
            "name": "worst_day",
            "passed": true,
            "points": 8
          },
          {
            "maximum": 6,
            "name": "var99",
            "passed": true,
            "points": 6
          },
          {
            "maximum": 6,
            "name": "es99",
            "passed": true,
            "points": 6
          },
          {
            "maximum": 8,
            "name": "fee_stress",
            "passed": false,
            "points": 0
          },
          {
            "maximum": 8,
            "name": "slippage_stress",
            "passed": false,
            "points": 0
          },
          {
            "maximum": 4,
            "name": "gap_limit_stress",
            "passed": true,
            "points": 4
          },
          {
            "maximum": 5,
            "name": "risk_budgets",
            "passed": true,
            "points": 5
          },
          {
            "maximum": 5,
            "name": "circuit_breakers",
            "passed": true,
            "points": 5
          },
          {
            "maximum": 5,
            "name": "session_flat",
            "passed": true,
            "points": 5
          },
          {
            "maximum": 4,
            "name": "positive_years",
            "passed": false,
            "points": 0
          },
          {
            "maximum": 3,
            "name": "positive_quarters",
            "passed": false,
            "points": 0
          },
          {
            "maximum": 3,
            "name": "rb_cu_survival",
            "passed": true,
            "points": 3
          },
          {
            "maximum": 4,
            "name": "causal_audit",
            "passed": true,
            "points": 4
          },
          {
            "maximum": 3,
            "name": "rollover_audit",
            "passed": true,
            "points": 3
          },
          {
            "maximum": 3,
            "name": "ledger_audit",
            "passed": true,
            "points": 3
          }
        ],
        "score": 70,
        "status": "FAILED",
        "vetoes": []
      },
      "status": "INCONCLUSIVE",
      "trade_metrics": {
        "average_holding_1m_bars": 0.0,
        "average_mae_r": 0.0,
        "average_mfe_r": 0.0,
        "expected_pnl": 0.0,
        "expected_r": 0.0,
        "loss_count": 0,
        "net_average_payoff": 0.0,
        "net_pnl": 0.0,
        "net_win_rate": 0.0,
        "profit_factor": 0.0,
        "sample_count": 0,
        "scratch_count": 0,
        "total_fee": 0.0,
        "total_slippage": 0.0,
        "wilson_high": 0.0,
        "wilson_low": 0.0,
        "win_count": 0
      }
    }
  },
  "monthly_target_passed": false,
  "portfolio_metrics": {
    "average_daily_return": 0.0,
    "average_monthly_return": 0.0,
    "daily_count": 12,
    "es99": 0.0,
    "max_drawdown": 0.0,
    "var99": 0.0,
    "worst_daily_return": 0.0
  },
  "portfolio_status": "INCONCLUSIVE",
  "requested_symbols": [
    "RB0.SHFE",
    "CU0.SHFE"
  ],
  "research_only": true,
  "status": "INCONCLUSIVE",
  "strategy_version": "scalp-12105f911baf",
  "trade_count": 0,
  "trade_metrics": {
    "average_holding_1m_bars": 0.0,
    "average_mae_r": 0.0,
    "average_mfe_r": 0.0,
    "expected_pnl": 0.0,
    "expected_r": 0.0,
    "loss_count": 0,
    "net_average_payoff": 0.0,
    "net_pnl": 0.0,
    "net_win_rate": 0.0,
    "profit_factor": 0.0,
    "sample_count": 0,
    "scratch_count": 0,
    "total_fee": 0.0,
    "total_slippage": 0.0,
    "wilson_high": 0.0,
    "wilson_low": 0.0,
    "win_count": 0
  }
}
```

## 6. Setup、方向、Session 和年度分组
详见 `group_metrics.csv`。

## 7. 风险和一票否决项
```json
{
  "bootstrap_loss_probability": null,
  "items": [
    {
      "maximum": 10,
      "name": "max_drawdown",
      "passed": true,
      "points": 10
    },
    {
      "maximum": 8,
      "name": "margin_survival",
      "passed": true,
      "points": 8
    },
    {
      "maximum": 7,
      "name": "bootstrap_survival",
      "passed": false,
      "points": 0
    },
    {
      "maximum": 8,
      "name": "worst_day",
      "passed": true,
      "points": 8
    },
    {
      "maximum": 6,
      "name": "var99",
      "passed": true,
      "points": 6
    },
    {
      "maximum": 6,
      "name": "es99",
      "passed": true,
      "points": 6
    },
    {
      "maximum": 8,
      "name": "fee_stress",
      "passed": false,
      "points": 0
    },
    {
      "maximum": 8,
      "name": "slippage_stress",
      "passed": false,
      "points": 0
    },
    {
      "maximum": 4,
      "name": "gap_limit_stress",
      "passed": true,
      "points": 4
    },
    {
      "maximum": 5,
      "name": "risk_budgets",
      "passed": true,
      "points": 5
    },
    {
      "maximum": 5,
      "name": "circuit_breakers",
      "passed": true,
      "points": 5
    },
    {
      "maximum": 5,
      "name": "session_flat",
      "passed": true,
      "points": 5
    },
    {
      "maximum": 4,
      "name": "positive_years",
      "passed": false,
      "points": 0
    },
    {
      "maximum": 3,
      "name": "positive_quarters",
      "passed": false,
      "points": 0
    },
    {
      "maximum": 3,
      "name": "rb_cu_survival",
      "passed": true,
      "points": 3
    },
    {
      "maximum": 4,
      "name": "causal_audit",
      "passed": true,
      "points": 4
    },
    {
      "maximum": 3,
      "name": "rollover_audit",
      "passed": true,
      "points": 3
    },
    {
      "maximum": 3,
      "name": "ledger_audit",
      "passed": true,
      "points": 3
    }
  ],
  "score": 70,
  "status": "FAILED",
  "symbols": {
    "CU0.SHFE": {
      "bootstrap_loss_probability": null,
      "items": [
        {
          "maximum": 10,
          "name": "max_drawdown",
          "passed": true,
          "points": 10
        },
        {
          "maximum": 8,
          "name": "margin_survival",
          "passed": true,
          "points": 8
        },
        {
          "maximum": 7,
          "name": "bootstrap_survival",
          "passed": false,
          "points": 0
        },
        {
          "maximum": 8,
          "name": "worst_day",
          "passed": true,
          "points": 8
        },
        {
          "maximum": 6,
          "name": "var99",
          "passed": true,
          "points": 6
        },
        {
          "maximum": 6,
          "name": "es99",
          "passed": true,
          "points": 6
        },
        {
          "maximum": 8,
          "name": "fee_stress",
          "passed": false,
          "points": 0
        },
        {
          "maximum": 8,
          "name": "slippage_stress",
          "passed": false,
          "points": 0
        },
        {
          "maximum": 4,
          "name": "gap_limit_stress",
          "passed": true,
          "points": 4
        },
        {
          "maximum": 5,
          "name": "risk_budgets",
          "passed": true,
          "points": 5
        },
        {
          "maximum": 5,
          "name": "circuit_breakers",
          "passed": true,
          "points": 5
        },
        {
          "maximum": 5,
          "name": "session_flat",
          "passed": true,
          "points": 5
        },
        {
          "maximum": 4,
          "name": "positive_years",
          "passed": false,
          "points": 0
        },
        {
          "maximum": 3,
          "name": "positive_quarters",
          "passed": false,
          "points": 0
        },
        {
          "maximum": 3,
          "name": "rb_cu_survival",
          "passed": true,
          "points": 3
        },
        {
          "maximum": 4,
          "name": "causal_audit",
          "passed": true,
          "points": 4
        },
        {
          "maximum": 3,
          "name": "rollover_audit",
          "passed": true,
          "points": 3
        },
        {
          "maximum": 3,
          "name": "ledger_audit",
          "passed": true,
          "points": 3
        }
      ],
      "score": 70,
      "status": "FAILED",
      "vetoes": []
    },
    "RB0.SHFE": {
      "bootstrap_loss_probability": null,
      "items": [
        {
          "maximum": 10,
          "name": "max_drawdown",
          "passed": true,
          "points": 10
        },
        {
          "maximum": 8,
          "name": "margin_survival",
          "passed": true,
          "points": 8
        },
        {
          "maximum": 7,
          "name": "bootstrap_survival",
          "passed": false,
          "points": 0
        },
        {
          "maximum": 8,
          "name": "worst_day",
          "passed": true,
          "points": 8
        },
        {
          "maximum": 6,
          "name": "var99",
          "passed": true,
          "points": 6
        },
        {
          "maximum": 6,
          "name": "es99",
          "passed": true,
          "points": 6
        },
        {
          "maximum": 8,
          "name": "fee_stress",
          "passed": false,
          "points": 0
        },
        {
          "maximum": 8,
          "name": "slippage_stress",
          "passed": false,
          "points": 0
        },
        {
          "maximum": 4,
          "name": "gap_limit_stress",
          "passed": true,
          "points": 4
        },
        {
          "maximum": 5,
          "name": "risk_budgets",
          "passed": true,
          "points": 5
        },
        {
          "maximum": 5,
          "name": "circuit_breakers",
          "passed": true,
          "points": 5
        },
        {
          "maximum": 5,
          "name": "session_flat",
          "passed": true,
          "points": 5
        },
        {
          "maximum": 4,
          "name": "positive_years",
          "passed": false,
          "points": 0
        },
        {
          "maximum": 3,
          "name": "positive_quarters",
          "passed": false,
          "points": 0
        },
        {
          "maximum": 3,
          "name": "rb_cu_survival",
          "passed": true,
          "points": 3
        },
        {
          "maximum": 4,
          "name": "causal_audit",
          "passed": true,
          "points": 4
        },
        {
          "maximum": 3,
          "name": "rollover_audit",
          "passed": true,
          "points": 3
        },
        {
          "maximum": 3,
          "name": "ledger_audit",
          "passed": true,
          "points": 3
        }
      ],
      "score": 70,
      "status": "FAILED",
      "vetoes": []
    }
  },
  "vetoes": []
}
```

## 8. 压力测试
```json
[
  {
    "forced_flat_failures": 0,
    "margin_breaches": 0,
    "max_margin_usage": 0.0,
    "net_pnl": 0.0,
    "profit_factor": 0.0,
    "scenario": "2x_fee",
    "trade_count": 0
  },
  {
    "forced_flat_failures": 0,
    "margin_breaches": 0,
    "max_margin_usage": 0.0,
    "net_pnl": 0.0,
    "profit_factor": 0.0,
    "scenario": "3x_slippage",
    "trade_count": 0
  },
  {
    "forced_flat_failures": 0,
    "margin_breaches": 0,
    "max_margin_usage": 0.0,
    "net_pnl": 0.0,
    "profit_factor": 0.0,
    "scenario": "gap_limit",
    "trade_count": 0
  }
]
```

## 9. 日均 1% 和月均 20% 目标检查
目标仅作为研究门槛，不构成收益承诺。详见 `summary.json`。

## 10. 局限和下一步
- 日均 1% 与月均 20% 是研究目标，不是收益承诺。
- 任一冻结分组样本少于 100 笔时结论保持 INCONCLUSIVE。
