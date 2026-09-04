# OOT 评估摘要 — cluster_both_unified

期间：2024-01-02 → 2026-05-29

总收益率 0.0808，年化 0.0328，最大回撤 -0.0605，月度夏普 0.1598。
最强板块：cluster_chemical；最弱板块：cluster_other。

⚠️ 收益集中度偏高：请优先查看 09_diagnostics/concentration_diagnostics.csv。

## 复现信息
- 运行命令：`--from-root cta/backtest --pattern '20260627_GRP_CLUSTER_*_both_model_pipeline' --output-root cta/backtest/20260627_eval_unified_portfolio --run-tag cluster_both_unified --note 'unified portfolio replay after 20260627 group-pool train'`
- 启动时间：2026-06-27T21:33:27.445508+08:00
- git_sha：60a04422
- run_tag：cluster_both_unified
- 关键 cfg：use_portfolio_logic_runtime=True，trade_filter_gate_mode=cluster_interval_percentile，use_risk_system=False，use_impact_cost=False，impact_cost_k=0.1，use_liquidity_floor_guard=True
- 主要测试方向：unified portfolio replay after 20260627 group-pool train
