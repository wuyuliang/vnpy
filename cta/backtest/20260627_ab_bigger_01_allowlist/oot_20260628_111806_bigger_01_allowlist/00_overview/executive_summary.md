# OOT 评估摘要 — bigger_01_allowlist

期间：2024-01-02 → 2026-05-29

总收益率 0.0823，年化 0.0334，最大回撤 -0.0615，月度夏普 0.1654。
最强板块：cluster_chemical；最弱板块：cluster_other。

⚠️ 收益集中度偏高：请优先查看 09_diagnostics/concentration_diagnostics.csv。

## 复现信息
- 运行命令：`--from-root cta/backtest --pattern '20260627_GRP_CLUSTER_*_both_model_pipeline' --output-root cta/backtest/20260627_ab_bigger_01_allowlist --run-tag bigger_01_allowlist --cfg-json cta/config/ab_tests_20260627_bigger/allowlist_only.json --note 'allow only bull_pullback_continuation and cross_sectional_momentum'`
- 启动时间：2026-06-28T11:18:06.094447+08:00
- git_sha：60a04422
- run_tag：bigger_01_allowlist
- 关键 cfg：use_portfolio_logic_runtime=True，trade_filter_gate_mode=cluster_interval_percentile，use_risk_system=False，use_impact_cost=False，impact_cost_k=0.1，use_liquidity_floor_guard=True
- 主要测试方向：allow only bull_pullback_continuation and cross_sectional_momentum
