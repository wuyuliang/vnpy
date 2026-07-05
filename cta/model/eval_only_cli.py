"""CLI for `python -m cta.model.eval`：复用既有 train 产物，按当前 cfg 重跑 OOT。

历史背景（2026-05-25）：
  - `cta.model.model_pipeline` 把训练 + OOT 混在一起，调 cfg 也要重训 ~10 分钟/cluster；
  - 本 CLI 拆分出"只评估"路径：扫描 `--from-root` 下所有 train 产物（按 `--pattern` 匹配），
    用当前 cfg 重跑 OOT 并聚合到 `output_root/oot_<timestamp>_<run_tag>` bundle。

最小用法：

  python -m cta.model.eval \\
      --from-root cta/report/backtest \\
      --pattern "20260523_GRP_CLUSTER_*_day_both_model_pipeline" \\
      --output-root cta/backtest/20260525_eval_default

  → 输出 `cta/backtest/20260525_eval_default/oot_<ts>_cluster_both/`

只支持 `--from-root` 批量（与 plan 决策对齐）。`--pattern` 可缩窄到单个 cluster|interval。

cfg 字段切换：每个 Action 1/2/3 都有 `--<field>` 显式开关；本 CLI 仅暴露最常用的 cfg
切换，复杂场景请改 model_oot_eval_config.py 默认或写 Python 脚本直接调
`run_oot_eval_batch()`。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.model.eval_only_run import run_oot_eval_batch
from cta.risk.config import RiskSystemConfig

logger = logging.getLogger(__name__)

def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Re-run OOT evaluation on existing train predictions (no retraining).",
    )
    parser.add_argument(
        "--from-root",
        required=True,
        help="train 产物根目录（如 cta/report/backtest）；扫描所有匹配 --pattern 的子目录。",
    )
    parser.add_argument(
        "--pattern",
        default="*_model_pipeline",
        help="train 目录 glob（默认 *_model_pipeline）。",
    )
    parser.add_argument(
        "--output-root",
        required=True,
        help="输出根目录；bundle 写到 <output-root>/oot_<timestamp>_<run-tag>。",
    )
    parser.add_argument(
        "--run-tag",
        default="cluster_both",
        help="bundle 后缀（默认 cluster_both）。",
    )
    parser.add_argument(
        "--note",
        default="",
        help="可选：本轮测试方向说明，会写入 executive_summary.md 与 cfg_fingerprint.json。",
    )
    # cfg fingerprint：JSON 文件路径（可选），把整份 cfg 用 dataclasses.asdict 序列化
    parser.add_argument(
        "--cfg-json",
        default=None,
        help="可选 JSON：把字段 patch 进 OotEvaluationConfig（如 {\"stacking_score_threshold\": 0.6}）。",
    )
    parser.add_argument(
        "--no-shared-htf-reference",
        action="store_true",
        default=False,
        help="禁用跨组 HTF reference（默认启用，跟 model_pipeline group_pool 一致）。",
    )
    # 2026-06-01：统一组合评估默认开启（对齐 sim/live）。全部 cluster/interval 候选合并成
    # 一次回放，共享单一 1000 万 + 单一 150% 总额，机会全局竞争。
    parser.add_argument(
        "--per-cluster-portfolio",
        action="store_true",
        default=False,
        help="回退到旧的逐 cluster 独立评估（每簇各占 1000 万 + 各自 150%%，cluster 间不竞争）。"
        "默认统一组合（全局共享 1000 万 + 150%%），与 sim/live 一致。仅用于对比研究。",
    )
    # 2026-05-27：eval 默认 use_portfolio_logic_runtime=True 与 train CLI 对齐
    # （OotEvaluationConfig dataclass 默认 False，但 train 通常 --use-portfolio-logic-runtime
    # 显式开启；eval 重跑必须沿用同款行为，否则 HTF / ranker / trailing / horizon / pyramid
    # 全部不跑，造成 cfg 漂移与笔数虚高）。
    parser.add_argument(
        "--no-portfolio-logic-runtime",
        action="store_true",
        default=False,
        help="禁用 portfolio_logic_runtime（默认启用）。禁用后 HTF gate / ranker / trailing / horizon / pyramid 全部不跑，等于裸 FCFS 路径，仅用于对比研究。",
    )
    # Action 1 / 2 / 3 退路开关：传 'empty' 表示用空 dict，等价于回退到 default-on 之前的行为
    parser.add_argument(
        "--commission-mode",
        choices=("manifest", "global"),
        default="manifest",
        help="`manifest`：用 cost_manifest 默认（按 cluster\\|interval 分层，default-on）；`global`：用全局 commission_pct_per_trade 标量。",
    )
    parser.add_argument(
        "--bond-filter",
        choices=("strict", "default"),
        default="strict",
        help="`strict`：保留 cluster_bond_filter_manifest 默认严格阈值；`default`：清空 dict 走全局 70 pctl。",
    )
    parser.add_argument(
        "--htf-fallback",
        choices=("per_cell", "global_skip", "global_both"),
        default="per_cell",
        help="`per_cell`：保留默认 per-cell 字典；`global_skip`/`global_both`：清空字典 + 设全局 fallback。",
    )
    # risk orchestrator（W2/W4/W5）
    parser.add_argument(
        "--enable-risk-system",
        action="store_true",
        default=False,
        help="启用 risk orchestrator（默认关闭）。",
    )
    parser.add_argument(
        "--risk-quantile-field",
        choices=("p50", "p60", "p70", "p80", "p90", "p95"),
        default="p70",
        help="risk quantile threshold 档位（默认 p70）。",
    )
    parser.add_argument(
        "--risk-manifest-path",
        default="",
        help="可选：显式指定 score_quantile_manifest.json 路径。",
    )
    parser.add_argument(
        "--risk-enable-bucket-scaling",
        action="store_true",
        default=False,
        help="启用 bucket scaling（默认关闭）。",
    )
    parser.add_argument(
        "--risk-disable-linear-dd-scaler",
        action="store_true",
        default=False,
        help="关闭 linear DD scaler（默认开启）。",
    )
    parser.add_argument(
        "--risk-disable-dynamic-bump",
        action="store_true",
        default=False,
        help="关闭 dynamic bump threshold（默认开启）。",
    )
    parser.add_argument(
        "--risk-disable-quantile-threshold",
        action="store_true",
        default=False,
        help="关闭 quantile threshold adjuster（默认开启）。",
    )
    parser.add_argument(
        "--enable-impact-cost",
        action="store_true",
        default=False,
        help="启用 ADV 参与率冲击成本：impact_cost_k * sqrt(order_lots / adv_lots)。默认关闭。",
    )
    parser.add_argument(
        "--impact-cost-k",
        type=float,
        default=0.10,
        help="冲击成本系数 k（默认 0.10），仅 --enable-impact-cost 时生效。",
    )
    parser.add_argument(
        "--disable-liquidity-floor",
        action="store_true",
        default=False,
        help="关闭 OOT 流动性下限 guard。默认开启且缺指标 fail-open。",
    )
    parser.add_argument(
        "--enable-oot-guard-chain",
        action="store_true",
        default=False,
        help="启用 OOT guard 链回放（consecutive_loss/signal_concentration/score_drift）。",
    )
    parser.add_argument(
        "--enable-oot-consecutive-loss-guard",
        action="store_true",
        default=False,
        help="启用 OOT consecutive-loss cooldown guard。",
    )
    parser.add_argument(
        "--enable-oot-signal-concentration-guard",
        action="store_true",
        default=False,
        help="启用 OOT signal concentration guard。",
    )
    parser.add_argument(
        "--enable-oot-score-drift-guard",
        action="store_true",
        default=False,
        help="启用 OOT score distribution drift guard。",
    )
    parser.add_argument(
        "--score-drift-train-path",
        default="",
        help="可选：score_distribution_train json 路径（空=默认 latest）。",
    )
    return parser.parse_args(argv)


def _build_cfg_from_args(args: argparse.Namespace) -> OotEvaluationConfig:
    """从 CLI flags 装配 OotEvaluationConfig；不传 = 用 default-on 默认。"""
    kwargs: dict = {}
    # 2026-05-27：默认 use_portfolio_logic_runtime=True 与 train CLI 默认行为对齐
    # （否则 HTF / ranker / trailing / horizon / pyramid 全不跑 → cfg 漂移）。
    kwargs["use_portfolio_logic_runtime"] = not bool(
        getattr(args, "no_portfolio_logic_runtime", False)
    )
    # Action 1
    if args.commission_mode == "global":
        kwargs["commission_pct_by_cluster_interval"] = {}
        kwargs["slippage_pct_by_cluster_interval"] = {}
    # Action 2
    if args.bond_filter == "default":
        kwargs["trade_filter_percentile_threshold_by_cluster_interval"] = {}
        kwargs["trade_filter_raw_threshold_by_cluster_interval"] = {}
    # Action 3 需通过 portfolio_logic.interval_gate 改，先取默认再替换
    cfg = OotEvaluationConfig(**kwargs)
    if args.htf_fallback in ("global_skip", "global_both"):
        from cta.portfolio_logic.config import IntervalGateConfig
        new_global = "skip" if args.htf_fallback == "global_skip" else "both"
        new_ig = IntervalGateConfig(
            htf_intervals=cfg.portfolio_logic.interval_gate.htf_intervals,
            require_consensus=cfg.portfolio_logic.interval_gate.require_consensus,
            fallback_when_htf_missing=new_global,
            fallback_when_htf_missing_by_cluster_interval={},
            state_ttl_seconds=dict(cfg.portfolio_logic.interval_gate.state_ttl_seconds),
            interval_rank=dict(cfg.portfolio_logic.interval_gate.interval_rank),
        )
        new_pl = replace(cfg.portfolio_logic, interval_gate=new_ig)
        cfg = replace(cfg, portfolio_logic=new_pl)
    if bool(getattr(args, "enable_risk_system", False)):
        cfg = replace(
            cfg,
            risk_system=RiskSystemConfig(
                enable_quantile_threshold=not bool(
                    getattr(args, "risk_disable_quantile_threshold", False)
                ),
                enable_bucket_scaling=bool(
                    getattr(args, "risk_enable_bucket_scaling", False)
                ),
                enable_linear_dd_scaler=not bool(
                    getattr(args, "risk_disable_linear_dd_scaler", False)
                ),
                enable_dynamic_bump=not bool(
                    getattr(args, "risk_disable_dynamic_bump", False)
                ),
                quantile_field=str(getattr(args, "risk_quantile_field", "p70")),
                quantile_manifest_path=(
                    str(getattr(args, "risk_manifest_path", "")).strip() or None
                ),
            ),
        )
    if bool(getattr(args, "enable_impact_cost", False)):
        cfg = replace(
            cfg,
            use_impact_cost=True,
            impact_cost_k=float(getattr(args, "impact_cost_k", 0.10)),
        )
    if bool(getattr(args, "disable_liquidity_floor", False)):
        cfg = replace(cfg, use_liquidity_floor_guard=False)
    if bool(getattr(args, "enable_oot_guard_chain", False)):
        score_drift_cfg = cfg.oot_score_distribution_guard
        score_drift_path = str(getattr(args, "score_drift_train_path", "")).strip()
        if score_drift_path:
            score_drift_cfg = replace(
                score_drift_cfg,
                train_distribution_path=score_drift_path,
            )
        cfg = replace(
            cfg,
            use_oot_guard_chain=True,
            use_oot_consecutive_loss_guard=bool(getattr(args, "enable_oot_consecutive_loss_guard", False)),
            use_oot_signal_concentration_guard=bool(getattr(args, "enable_oot_signal_concentration_guard", False)),
            use_oot_score_distribution_guard=bool(getattr(args, "enable_oot_score_drift_guard", False)),
            oot_score_distribution_guard=score_drift_cfg,
        )
    # 可选 --cfg-json 覆盖（最高优先）
    if args.cfg_json is not None:
        patch_path = Path(args.cfg_json)
        patch = json.loads(patch_path.read_text())
        if not isinstance(patch, dict):
            raise ValueError("--cfg-json 必须是 JSON 对象")
        # 支持嵌套 patch：portfolio_logic.*（常用：caps/interval_gate）。
        pl_patch = patch.get("portfolio_logic")
        if isinstance(pl_patch, dict):
            patch = dict(patch)
            patch.pop("portfolio_logic", None)
            nested = dict(pl_patch)
            pl_cfg = cfg.portfolio_logic
            caps_patch = nested.get("caps")
            if isinstance(caps_patch, dict):
                nested["caps"] = replace(pl_cfg.caps, **caps_patch)
            interval_gate_patch = nested.get("interval_gate")
            if isinstance(interval_gate_patch, dict):
                nested["interval_gate"] = replace(pl_cfg.interval_gate, **interval_gate_patch)
            ranker_patch = nested.get("ranker")
            if isinstance(ranker_patch, dict):
                nested["ranker"] = replace(pl_cfg.ranker, **ranker_patch)
            risk_throttle_patch = nested.get("risk_throttle")
            if isinstance(risk_throttle_patch, dict):
                nested["risk_throttle"] = replace(pl_cfg.risk_throttle, **risk_throttle_patch)
            pyramid_patch = nested.get("pyramid")
            if isinstance(pyramid_patch, dict):
                nested["pyramid"] = replace(pl_cfg.pyramid, **pyramid_patch)
            trailing_patch = nested.get("trailing")
            if trailing_patch is None:
                trailing_patch = nested.get("trailing_exit")
            if isinstance(trailing_patch, dict):
                nested.pop("trailing_exit", None)
                nested["trailing"] = replace(pl_cfg.trailing, **trailing_patch)
            horizon_patch = nested.get("horizon_extend")
            if isinstance(horizon_patch, dict):
                nested["horizon_extend"] = replace(pl_cfg.horizon_extend, **horizon_patch)
            cfg = replace(cfg, portfolio_logic=replace(pl_cfg, **nested))
        cfg = replace(cfg, **patch)
    return cfg


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args = _parse_args(argv)
    cfg = _build_cfg_from_args(args)
    bundle_dir = run_oot_eval_batch(
        from_root=Path(args.from_root),
        pattern=args.pattern,
        output_root=Path(args.output_root),
        cfg=cfg,
        run_tag=args.run_tag,
        enable_shared_htf_reference=not args.no_shared_htf_reference,
        argv=list(sys.argv[1:]) if argv is None else list(argv),
        note=args.note,
        unified_portfolio=not bool(getattr(args, "per_cluster_portfolio", False)),
    )
    print(f"OOT eval bundle: {bundle_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
