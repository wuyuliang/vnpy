"""eval_only：从已有 train run 目录复用 predictions.csv 重跑 OOT。

目标（2026-05-25）：把训练 / OOT 评估拆开。同一个 train 产物可以被多个 cfg 反复 eval，
不必每次重训 4-5 个模型（saves 5-10 min/cluster）。

公共 API：
  - `TrainRunMeta`：从 train 输出目录解析出的元数据（路径 + run_date + pool + interval）
  - `discover_train_runs(root, pattern)`：扫盘找到所有匹配的 train 目录
  - `rerun_oot_for_train_run(meta, cfg, output_dir, htf_reference_df=None)`：单个目录 OOT 重跑
  - `run_oot_eval_batch(from_root, pattern, output_root, cfg, run_tag)`：批量重跑 + 聚合 bundle

设计原则：
  1. 复用 `_evaluate_oot_real_execution(prediction_df, cfg, extra_outputs, htf_reference_df)` 不重写；
  2. 聚合产出 `oot_<timestamp>_<run_tag>` 目录结构与 model_pipeline group_pool 一致；
  3. meta/cfg_fingerprint.json 必须 dump 关键字段，防止"v_acb 命名失真"那类事故；
  4. predictions.csv 缺列时降级 fallback pass-all（与 OOT pipeline 现有兜底一致）。
"""
from __future__ import annotations

import dataclasses
import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.model.orchestration.pipeline_base import ModelPipelineResult
from cta.model.oot.pipeline_oot_evaluation import _evaluate_oot_real_execution

logger = logging.getLogger(__name__)


# 训练目录命名形如：20260523_GRP_CLUSTER_BLACK_day_both_model_pipeline
_DIR_PATTERN = re.compile(
    r"^(?P<run_date>\d{8})_(?P<pool_name>[A-Z0-9_]+?)_(?P<interval>day|60min|30min|15min|5min|min|minute60|minute30|minute15|minute5|minute)_(?P<side>both|long|short)_model_pipeline$"
)


@dataclass(frozen=True)
class TrainRunMeta:
    """从 train 输出目录解析的元数据。"""

    output_dir: Path
    run_date: str
    pool_name: str
    interval: str
    trade_side_mode: str
    prediction_path: Path
    metrics_path: Path
    candidate_path: Path
    feature_table_path: Path

    @property
    def group_name(self) -> str:
        """`GRP_CLUSTER_BLACK` → `cluster_black`（OOT 聚合期望的小写形态）。"""
        name = self.pool_name.lower()
        if name.startswith("grp_"):
            name = name[4:]
        return name

    def name_prefix(self) -> str:
        return f"{self.run_date}_{self.pool_name}_{self.interval}_{self.trade_side_mode}"


def _parse_train_dir(d: Path) -> TrainRunMeta | None:
    """解析单个目录名 → TrainRunMeta，找不到 predictions.csv 返回 None。"""
    if not d.is_dir():
        return None
    m = _DIR_PATTERN.match(d.name)
    if m is None:
        return None
    run_date = m.group("run_date")
    pool_name = m.group("pool_name")
    interval = m.group("interval")
    side = m.group("side")
    prefix = f"{run_date}_{pool_name}_{interval}_{side}"
    prediction_path = d / f"{prefix}_predictions.csv"
    if not prediction_path.exists():
        logger.debug("skip dir without predictions: %s", d)
        return None
    return TrainRunMeta(
        output_dir=d,
        run_date=run_date,
        pool_name=pool_name,
        interval=interval,
        trade_side_mode=side,
        prediction_path=prediction_path,
        metrics_path=d / f"{prefix}_metrics.csv",
        candidate_path=d / f"{prefix}_candidates.csv",
        feature_table_path=d / f"{prefix}_feature_table.csv",
    )


def discover_train_runs(root: Path, pattern: str = "*_model_pipeline") -> list[TrainRunMeta]:
    """扫描 root 下所有 train 输出目录，返回可用的 TrainRunMeta 列表。"""
    root = Path(root)
    if not root.is_dir():
        return []
    metas: list[TrainRunMeta] = []
    for d in sorted(root.glob(pattern)):
        meta = _parse_train_dir(d)
        if meta is not None:
            metas.append(meta)
    return metas


def _dump_cfg_fingerprint(
    meta_dir: Path,
    cfg: OotEvaluationConfig,
    argv: Sequence[str] | None = None,
    note: str = "",
) -> Path:
    """把 cfg 关键字段 + argv 写到 meta/cfg_fingerprint.json，防止命名失真。"""
    meta_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "argv": list(argv) if argv is not None else None,
        "note": str(note or ""),
        # Action 1：cost manifest
        "commission_pct_by_cluster_interval": dict(getattr(cfg, "commission_pct_by_cluster_interval", {})),
        "slippage_pct_by_cluster_interval": dict(getattr(cfg, "slippage_pct_by_cluster_interval", {})),
        "commission_pct_by_symbol": dict(getattr(cfg, "commission_pct_by_symbol", {})),
        "slippage_pct_by_symbol": dict(getattr(cfg, "slippage_pct_by_symbol", {})),
        "commission_pct_per_trade": float(getattr(cfg, "commission_pct_per_trade", 0.0)),
        "slippage_pct_per_trade": float(getattr(cfg, "slippage_pct_per_trade", 0.0)),
        "use_impact_cost": bool(getattr(cfg, "use_impact_cost", False)),
        "impact_cost_k": float(getattr(cfg, "impact_cost_k", 0.0)),
        "impact_adv_lots_column": str(getattr(cfg, "impact_adv_lots_column", "")),
        # Action 2：bond filter
        "trade_filter_gate_mode": str(getattr(cfg, "trade_filter_gate_mode", "")),
        "trade_filter_threshold": float(getattr(cfg, "trade_filter_threshold", 0.0)),
        "trade_filter_percentile_threshold": float(getattr(cfg, "trade_filter_percentile_threshold", 0.0)),
        "trade_filter_raw_threshold_by_cluster_interval": dict(getattr(cfg, "trade_filter_raw_threshold_by_cluster_interval", {})),
        "trade_filter_percentile_threshold_by_cluster_interval": dict(getattr(cfg, "trade_filter_percentile_threshold_by_cluster_interval", {})),
        "trade_filter_raw_threshold_delta_by_signal_type": dict(
            getattr(cfg, "trade_filter_raw_threshold_delta_by_signal_type", {})
        ),
        "trade_filter_percentile_threshold_delta_by_signal_type": dict(
            getattr(cfg, "trade_filter_percentile_threshold_delta_by_signal_type", {})
        ),
        # Action 3：htf fallback
        "portfolio_logic": {
            "enable_htf_gate": bool(getattr(getattr(cfg, "portfolio_logic", None), "enable_htf_gate", False)),
            "interval_gate": {
                "fallback_when_htf_missing": str(getattr(getattr(getattr(cfg, "portfolio_logic", None), "interval_gate", None), "fallback_when_htf_missing", "")),
                "fallback_when_htf_missing_by_cluster_interval": dict(
                    getattr(
                        getattr(getattr(cfg, "portfolio_logic", None), "interval_gate", None),
                        "fallback_when_htf_missing_by_cluster_interval",
                        {},
                    )
                ),
            },
            "caps": {
                "max_total_positions": int(getattr(getattr(cfg, "portfolio_logic", None), "caps", None).max_total_positions)
                if getattr(getattr(cfg, "portfolio_logic", None), "caps", None) is not None
                else 0,
                "max_per_symbol": int(getattr(getattr(cfg, "portfolio_logic", None), "caps", None).max_per_symbol)
                if getattr(getattr(cfg, "portfolio_logic", None), "caps", None) is not None
                else 0,
                "max_total_per_cluster": int(getattr(getattr(cfg, "portfolio_logic", None), "caps", None).max_total_per_cluster)
                if getattr(getattr(cfg, "portfolio_logic", None), "caps", None) is not None
                else 0,
                "max_symbol_notional_pct": float(getattr(getattr(cfg, "portfolio_logic", None), "caps", None).max_symbol_notional_pct)
                if getattr(getattr(cfg, "portfolio_logic", None), "caps", None) is not None
                else 0.0,
                "max_cluster_notional_pct": float(getattr(getattr(cfg, "portfolio_logic", None), "caps", None).max_cluster_notional_pct)
                if getattr(getattr(cfg, "portfolio_logic", None), "caps", None) is not None
                else 0.0,
                "max_total_notional_pct": float(getattr(getattr(cfg, "portfolio_logic", None), "caps", None).max_total_notional_pct)
                if getattr(getattr(cfg, "portfolio_logic", None), "caps", None) is not None
                else 0.0,
            },
        },
        # 其它核心字段
        "use_portfolio_logic_runtime": bool(getattr(cfg, "use_portfolio_logic_runtime", False)),
        "use_intrabar_stop_tracking": bool(getattr(cfg, "use_intrabar_stop_tracking", False)),
        "intrabar_stop_loss_pct": float(getattr(cfg, "intrabar_stop_loss_pct", 0.0)),
        "intrabar_stop_loss_pct_by_cluster_interval": dict(getattr(cfg, "intrabar_stop_loss_pct_by_cluster_interval", {})),
        "enforce_intrabar_bar_volume_cap": bool(getattr(cfg, "enforce_intrabar_bar_volume_cap", False)),
        "intrabar_max_bar_volume_participation_pct": float(
            getattr(cfg, "intrabar_max_bar_volume_participation_pct", 0.0)
        ),
        "intrabar_volume_column": str(getattr(cfg, "intrabar_volume_column", "volume")),
        "use_trade_filter_gate": bool(getattr(cfg, "use_trade_filter_gate", True)),
        "use_stacking_gate": bool(getattr(cfg, "use_stacking_gate", True)),
        "stacking_score_threshold": float(getattr(cfg, "stacking_score_threshold", 0.0)),
        "use_liquidity_floor_guard": bool(getattr(cfg, "use_liquidity_floor_guard", False)),
        "use_portfolio_constraints": bool(getattr(cfg, "use_portfolio_constraints", False)),
        "initial_capital": float(getattr(cfg, "initial_capital", 0.0)),
        "max_total_leverage": float(getattr(cfg, "max_total_leverage", 0.0)),
        "max_daily_new_notional_pct": float(getattr(cfg, "max_daily_new_notional_pct", 0.0)),
        "max_position_scale": float(getattr(cfg, "max_position_scale", 0.0)),
        "signal_type_size_multiplier": dict(getattr(cfg, "signal_type_size_multiplier", {})),
        "signal_type_blacklist": list(getattr(cfg, "signal_type_blacklist", ())),
        "use_signal_type_allowlist": bool(getattr(cfg, "use_signal_type_allowlist", False)),
        "signal_type_allowlist": list(getattr(cfg, "signal_type_allowlist", ())),
        "disabled_intervals": list(getattr(cfg, "disabled_intervals", ())),
        "disabled_cluster_signal_interval_cells": list(getattr(cfg, "disabled_cluster_signal_interval_cells", ())),
        "enabled_cluster_signal_interval_cells": list(getattr(cfg, "enabled_cluster_signal_interval_cells", ())),
        "signal_type_max_concurrent_positions": dict(
            getattr(cfg, "signal_type_max_concurrent_positions", {})
        ),
        "signal_type_min_reserved_slots": dict(getattr(cfg, "signal_type_min_reserved_slots", {})),
        "use_minute30_positive_cell_gate": bool(getattr(cfg, "use_minute30_positive_cell_gate", False)),
        "minute30_positive_cell_lookback_months": int(getattr(cfg, "minute30_positive_cell_lookback_months", 0)),
        "minute30_positive_cell_min_trades": int(getattr(cfg, "minute30_positive_cell_min_trades", 0)),
        "minute30_positive_cell_min_net_pnl": float(getattr(cfg, "minute30_positive_cell_min_net_pnl", 0.0)),
        "use_hard_stop_entry_filter": bool(getattr(cfg, "use_hard_stop_entry_filter", False)),
        "min_pred_mfe_mae_ratio_by_signal_type_interval": dict(getattr(cfg, "min_pred_mfe_mae_ratio_by_signal_type_interval", {})),
        "max_pred_mae_atr_by_signal_type_interval": dict(getattr(cfg, "max_pred_mae_atr_by_signal_type_interval", {})),
        "neutral_htf_size_multiplier_by_interval": dict(getattr(cfg, "neutral_htf_size_multiplier_by_interval", {})),
        "high_mae_size_multiplier": float(getattr(cfg, "high_mae_size_multiplier", 0.0)),
        "max_symbol_notional_pct": float(getattr(cfg, "max_symbol_notional_pct", 0.0)),
        "max_concurrent_positions_per_symbol": int(
            getattr(cfg, "max_concurrent_positions_per_symbol", 0)
        ),
        "max_concurrent_positions_total": int(
            getattr(cfg, "max_concurrent_positions_total", 0)
        ),
        # 周回撤
        "weekly_max_drawdown_pct": float(getattr(cfg, "weekly_max_drawdown_pct", 0.0)),
        # risk orchestrator 子配置（P0Δ-5 invariant 14）
        "risk_system": (
            dataclasses.asdict(cfg.risk_system)
            if getattr(cfg, "risk_system", None) is not None
            else None
        ),
    }
    fingerprint_path = meta_dir / "cfg_fingerprint.json"
    fingerprint_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return fingerprint_path


def _cfg_drift_report(*, metas: Sequence[TrainRunMeta], cfg: OotEvaluationConfig) -> dict[str, Any]:
    """Compare train-run fingerprints with the eval cfg on key deployment fields."""
    current: dict[str, Any] = {
        "use_portfolio_logic_runtime": bool(getattr(cfg, "use_portfolio_logic_runtime", False)),
        "use_impact_cost": bool(getattr(cfg, "use_impact_cost", False)),
        "impact_cost_k": float(getattr(cfg, "impact_cost_k", 0.0)),
        "use_liquidity_floor_guard": bool(getattr(cfg, "use_liquidity_floor_guard", False)),
        "trade_filter_gate_mode": str(getattr(cfg, "trade_filter_gate_mode", "")),
        "trade_filter_percentile_threshold": float(getattr(cfg, "trade_filter_percentile_threshold", 0.0)),
        "trade_filter_threshold": float(getattr(cfg, "trade_filter_threshold", 0.0)),
    }
    missing: list[str] = []
    mismatches: list[dict[str, Any]] = []
    for meta in metas:
        fp = meta.output_dir / "meta" / "cfg_fingerprint.json"
        if not fp.exists():
            missing.append(str(meta.output_dir))
            continue
        try:
            train_payload = json.loads(fp.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{meta.output_dir} (parse_failed: {exc})")
            continue
        for field, eval_value in current.items():
            if field not in train_payload:
                continue
            train_value = train_payload.get(field)
            if train_value != eval_value:
                mismatches.append(
                    {
                        "model_dir": str(meta.output_dir),
                        "field": field,
                        "train_value": train_value,
                        "eval_value": eval_value,
                    }
                )
    return {
        "checked_runs": len(list(metas)),
        "missing_fingerprint_dirs": missing,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }


def rerun_oot_for_train_run(
    meta: TrainRunMeta,
    *,
    cfg: OotEvaluationConfig,
    output_dir: Path,
    htf_reference_df: pd.DataFrame | None = None,
) -> ModelPipelineResult:
    """加载 train 目录的 predictions.csv，按 cfg 重跑 OOT，输出新的 oot_*.csv。

    输出落在 `output_dir/<pool_name>_<interval>_<side>/`，文件命名与原 train run 保持一致。
    """
    output_dir = Path(output_dir)
    sub = output_dir / f"{meta.pool_name}_{meta.interval}_{meta.trade_side_mode}"
    sub.mkdir(parents=True, exist_ok=True)
    prefix = meta.name_prefix()
    pred_df = pd.read_csv(meta.prediction_path, encoding="utf-8-sig")
    extra: dict[str, pd.DataFrame] = {}
    oot_monthly, oot_summary, oot_trades = _evaluate_oot_real_execution(
        pred_df,
        cfg=cfg,
        extra_outputs=extra,
        htf_reference_df=htf_reference_df,
    )
    monthly_path = sub / f"{prefix}_oot_monthly_returns.csv"
    summary_path = sub / f"{prefix}_oot_summary.csv"
    trades_path = sub / f"{prefix}_oot_trade_details.csv"
    throttle_path = sub / f"{prefix}_throttle_log.csv"
    pos_lifetime_path = sub / f"{prefix}_oot_position_lifetime.csv"
    oot_monthly.to_csv(monthly_path, index=False, encoding="utf-8-sig")
    oot_summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    oot_trades.to_csv(trades_path, index=False, encoding="utf-8-sig")
    extra.get("throttle_log", pd.DataFrame()).to_csv(throttle_path, index=False, encoding="utf-8-sig")
    extra.get("position_lifetime", pd.DataFrame()).to_csv(pos_lifetime_path, index=False, encoding="utf-8-sig")
    # 同时把 predictions.csv 复制一份到 sub/，方便 group_pool aggregator 找到
    shutil.copy2(meta.prediction_path, sub / meta.prediction_path.name)
    res = ModelPipelineResult(
        output_dir=sub,
        candidate_path=sub / meta.candidate_path.name,
        feature_table_path=sub / meta.feature_table_path.name,
        prediction_path=sub / meta.prediction_path.name,
        metrics_path=sub / meta.metrics_path.name,
        oot_monthly_path=monthly_path,
        oot_summary_path=summary_path,
        oot_trades_path=trades_path,
        html_report_path=sub / f"{prefix}_oot_report.html",
        top_feature_importance_path=sub / f"{prefix}_top10_feature_importance.csv",
        report_path=sub / f"{prefix}_model_report.md",
    )
    return res


def _pool_group_key(meta: TrainRunMeta) -> tuple[str, str, str]:
    """Group key for shared-constraint reruns: (run_date, pool_name, side)."""
    return (str(meta.run_date), str(meta.pool_name), str(meta.trade_side_mode))


def _group_train_runs_by_pool(metas: Sequence[TrainRunMeta]) -> list[list[TrainRunMeta]]:
    """Group train runs so same pool across intervals is evaluated in one portfolio pass."""
    buckets: dict[tuple[str, str, str], list[TrainRunMeta]] = {}
    for meta in metas:
        buckets.setdefault(_pool_group_key(meta), []).append(meta)
    out: list[list[TrainRunMeta]] = []
    for _, rows in sorted(buckets.items(), key=lambda kv: kv[0]):
        out.append(sorted(rows, key=lambda m: (str(m.interval), str(m.output_dir))))
    return out


def rerun_oot_for_train_group(
    metas: Sequence[TrainRunMeta],
    *,
    cfg: OotEvaluationConfig,
    output_dir: Path,
    htf_reference_df: pd.DataFrame | None = None,
    pool_label: str | None = None,
) -> tuple[TrainRunMeta, ModelPipelineResult]:
    """Re-run one group (possibly multi-interval / multi-cluster) in a single pass.

    关键语义：组内所有候选合并后一次回放，因此同一 symbol/cluster 的仓位限制
    跨 interval（以及统一模式下跨 cluster）共用同一份 PortfolioState。

    ``pool_label``（2026-06-01 统一组合）：显式指定输出标签时，强制走"合并→单次回放"
    路径（即便组内只有一个 meta），并用该 label 作为输出 pool_name。统一组合评估
    用 ``pool_label="unified"`` 把全部 cluster 的候选汇入同一池竞争（对齐 sim/live）。
    """
    rows = list(metas)
    if not rows:
        raise ValueError("rerun_oot_for_train_group requires at least one TrainRunMeta")
    anchor = sorted(rows, key=lambda m: (str(m.interval), str(m.output_dir)))[0]
    if len(rows) == 1 and pool_label is None:
        return (
            anchor,
            rerun_oot_for_train_run(
                anchor,
                cfg=cfg,
                output_dir=output_dir,
                htf_reference_df=htf_reference_df,
            ),
        )

    merged_frames: list[pd.DataFrame] = []
    dropped: list[str] = []
    for meta in rows:
        try:
            frame = pd.read_csv(meta.prediction_path, encoding="utf-8-sig")
        except Exception as exc:
            logger.warning("read prediction failed for grouped rerun: %s (%s)", meta.prediction_path, exc)
            dropped.append(f"{meta.pool_name}_{meta.interval} (read_failed)")
            continue
        if frame.empty:
            dropped.append(f"{meta.pool_name}_{meta.interval} (empty)")
            continue
        if "interval" not in frame.columns:
            frame["interval"] = str(meta.interval)
        else:
            interval_series = frame["interval"].astype(str)
            if bool(interval_series.str.strip().eq("").all()):
                frame["interval"] = str(meta.interval)
        merged_frames.append(frame)
    # S3（统一组合）：缺席的 pool 必须 loud warning，避免静默少算一整簇竞争对手。
    if dropped:
        logger.warning(
            "grouped rerun (label=%s): %d/%d source predictions dropped: %s",
            pool_label or anchor.pool_name,
            len(dropped),
            len(rows),
            ", ".join(dropped[:20]),
        )
    if not merged_frames:
        raise RuntimeError(
            f"all prediction files are empty/unreadable for group={pool_label or anchor.pool_name}"
        )

    pred_df = pd.concat(merged_frames, axis=0, ignore_index=True, sort=False)
    group_meta = TrainRunMeta(
        output_dir=anchor.output_dir,
        run_date=anchor.run_date,
        pool_name=str(pool_label) if pool_label else anchor.pool_name,
        interval="mixed",
        trade_side_mode=anchor.trade_side_mode,
        prediction_path=anchor.prediction_path,
        metrics_path=anchor.metrics_path,
        candidate_path=anchor.candidate_path,
        feature_table_path=anchor.feature_table_path,
    )
    output_dir = Path(output_dir)
    sub = output_dir / f"{group_meta.pool_name}_{group_meta.interval}_{group_meta.trade_side_mode}"
    sub.mkdir(parents=True, exist_ok=True)
    prefix = group_meta.name_prefix()
    extra: dict[str, pd.DataFrame] = {}
    oot_monthly, oot_summary, oot_trades = _evaluate_oot_real_execution(
        pred_df,
        cfg=cfg,
        extra_outputs=extra,
        htf_reference_df=htf_reference_df,
    )
    monthly_path = sub / f"{prefix}_oot_monthly_returns.csv"
    summary_path = sub / f"{prefix}_oot_summary.csv"
    trades_path = sub / f"{prefix}_oot_trade_details.csv"
    throttle_path = sub / f"{prefix}_throttle_log.csv"
    pos_lifetime_path = sub / f"{prefix}_oot_position_lifetime.csv"
    prediction_path = sub / f"{prefix}_predictions.csv"
    oot_monthly.to_csv(monthly_path, index=False, encoding="utf-8-sig")
    oot_summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    oot_trades.to_csv(trades_path, index=False, encoding="utf-8-sig")
    extra.get("throttle_log", pd.DataFrame()).to_csv(throttle_path, index=False, encoding="utf-8-sig")
    extra.get("position_lifetime", pd.DataFrame()).to_csv(pos_lifetime_path, index=False, encoding="utf-8-sig")
    pred_df.to_csv(prediction_path, index=False, encoding="utf-8-sig")
    res = ModelPipelineResult(
        output_dir=sub,
        candidate_path=sub / anchor.candidate_path.name,
        feature_table_path=sub / anchor.feature_table_path.name,
        prediction_path=prediction_path,
        metrics_path=sub / anchor.metrics_path.name,
        oot_monthly_path=monthly_path,
        oot_summary_path=summary_path,
        oot_trades_path=trades_path,
        html_report_path=sub / f"{prefix}_oot_report.html",
        top_feature_importance_path=sub / f"{prefix}_top10_feature_importance.csv",
        report_path=sub / f"{prefix}_model_report.md",
    )
    return (group_meta, res)


def _aggregate_bundle(
    output_root: Path,
    run_tag: str,
    results: list[tuple[TrainRunMeta, ModelPipelineResult]],
    cfg: OotEvaluationConfig,
    argv: Sequence[str] | None = None,
    note: str = "",
    unified: bool = False,
) -> Path:
    """聚合多个 per-cluster OOT 输出，调 oot_report_writer 写到 oot_<ts>_<run_tag>。

    流程：
      1) 在 output_root 下建一个"source bundle"目录，用 oot_report_writer 期望的命名
         （`*_all_symbol_group_oot_trade_details.csv`）放聚合 trades 表 +
         `symbol_group_details/` 子目录；
      2) 调 `write_oot_evaluation_report(bundle_dir=source)`，它会在
         `source.parent` 下创建 `oot_<新ts>_<run_tag>/`（最终给用户的报告目录）；
      3) 把 cfg_fingerprint.json 写到**最终 report_dir 的 meta/**（不是 source）。
      4) 出错时降级：直接在 output_root 下建 raw/all_trade_details.csv（向后兼容）。

    历史背景（2026-05-27）：之前的实现自己创建 oot_<ts>_<run_tag> 目录写
    raw/all_trade_details.csv，但 oot_report_writer 内部又用 datetime.now() 算新
    的 report_dir，结果产出两个目录：用户看到的 oot_<ts2>_<run_tag> 只有空 schema、
    raw/ 里的 csv 落在 oot_<ts1>_<run_tag>。本修复用单一来源、单一 report_dir 解决。
    """
    from cta.model.reporting.oot_report_writer import write_oot_evaluation_report
    from cta.model.reporting.group_pool_aggregate import AggregateConfig

    # 1) source bundle dir：放聚合 trades + symbol_group_details/
    src_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    src_bundle = output_root / f"_eval_source_{src_ts}_{run_tag}"
    src_bundle.mkdir(parents=True, exist_ok=True)
    # 拼聚合 trades
    trade_frames: list[pd.DataFrame] = []
    for meta, res in results:
        if not res.oot_trades_path.exists():
            continue
        try:
            df = pd.read_csv(res.oot_trades_path, encoding="utf-8-sig")
        except Exception as exc:
            logger.warning("read oot trades failed: %s (%s)", res.oot_trades_path, exc)
            continue
        df["group_name"] = meta.group_name
        df["pool_name"] = meta.pool_name
        if "group_interval" not in df.columns:
            if "interval" in df.columns:
                df["group_interval"] = df["interval"].astype(str)
            else:
                df["group_interval"] = str(meta.interval)
        else:
            gi = df["group_interval"].astype(str)
            if bool(gi.str.strip().eq("").all()):
                if "interval" in df.columns:
                    df["group_interval"] = df["interval"].astype(str)
                else:
                    df["group_interval"] = str(meta.interval)
        df["model_dir"] = str(meta.output_dir.resolve())
        trade_frames.append(df)
    agg_df = (
        pd.concat(trade_frames, axis=0, ignore_index=True)
        if trade_frames
        else pd.DataFrame()
    )
    # 统一组合模式（2026-06-01）：只有一份合并回放的 trades，meta.group_name 是占位
    # "unified"。02_by_cluster 等 breakdown 由 oot_report_views 按 group_name 列分组得到，
    # 所以这里必须把 group_name 按每行 symbol 的真实 cluster 重写，否则全簇并到一个
    # "unified" 桶。命名对齐 per-cluster 模式的 "cluster_<x>"（= TrainRunMeta.group_name 形态）。
    if unified and not agg_df.empty and "symbol" in agg_df.columns:
        from cta.config.symbol_cluster_config import infer_symbol_cluster

        agg_df["group_name"] = (
            agg_df["symbol"].astype(str).map(lambda s: f"cluster_{infer_symbol_cluster(s)}")
        )
    # 重要：文件名必须匹配 oot_report_writer._find_trade_path 的 glob
    #   `*_all_symbol_group_oot_trade_details.csv`
    agg_filename = f"eval_{src_ts}_{run_tag}_all_symbol_group_oot_trade_details.csv"
    agg_df.to_csv(src_bundle / agg_filename, index=False, encoding="utf-8-sig")
    # symbol_group_details/ 子目录：oot_report_writer 会把里面的 detail 拷到 02_by_cluster/
    # 统一组合模式下只有一份 "unified_mixed" 结果——若拷过去会在 02_by_cluster 留一个杂项
    # "unified_mixed" 目录，遮盖真正按 cluster 切分的视图。故 unified 时跳过这步：
    # 02_by_cluster 完全由 oot_report_views 按 group_name 列重新切分（见上面的 group_name 重写）。
    if not unified:
        sgd_root = src_bundle / "symbol_group_details"
        sgd_root.mkdir(exist_ok=True)
        for meta, res in results:
            detail_dir = sgd_root / f"{meta.pool_name}_{meta.interval}"
            detail_dir.mkdir(parents=True, exist_ok=True)
            # 把 per-cluster oot 输出复制过去
            for src_path in (
                res.oot_monthly_path,
                res.oot_summary_path,
                res.oot_trades_path,
                res.prediction_path,
            ):
                try:
                    if src_path.exists():
                        shutil.copy2(src_path, detail_dir / src_path.name)
                except Exception as exc:
                    logger.warning("copy detail artifact failed: %s (%s)", src_path, exc)
    # 2) 调 oot_report_writer 产出最终 report_dir
    try:
        run_records = [
            {
                "result": res,
                "group_name": meta.group_name,
                "pool_name": meta.pool_name,
                "interval": meta.interval,
                "members": [],
            }
            for meta, res in results
        ]
        report_dir = write_oot_evaluation_report(
            bundle_dir=src_bundle,
            run_records=run_records,
            run_tag=run_tag,
            cfg=AggregateConfig.from_oot_config(cfg),
            reproducibility_info={
                "argv": list(argv) if argv is not None else None,
                "generated_at": datetime.now().astimezone().isoformat(),
                "note": str(note or ""),
                "run_tag": str(run_tag),
                "key_cfg": {
                    "use_portfolio_logic_runtime": bool(getattr(cfg, "use_portfolio_logic_runtime", False)),
                    "trade_filter_gate_mode": str(getattr(cfg, "trade_filter_gate_mode", "")),
                    "use_risk_system": bool(getattr(cfg, "risk_system", None) is not None),
                    "use_impact_cost": bool(getattr(cfg, "use_impact_cost", False)),
                    "impact_cost_k": float(getattr(cfg, "impact_cost_k", 0.0)),
                    "use_liquidity_floor_guard": bool(getattr(cfg, "use_liquidity_floor_guard", False)),
                },
            },
        )
    except Exception as exc:
        # 降级：oot_report_writer 失败 → 直接把 raw/all_trade_details.csv 摆在
        # output_root 下，至少保留诊断能用的核心数据。
        logger.exception("oot_report_writer failed, falling back to minimal bundle: %s", exc)
        fallback_dir = output_root / f"oot_{src_ts}_{run_tag}"
        (fallback_dir / "raw").mkdir(parents=True, exist_ok=True)
        (fallback_dir / "meta").mkdir(parents=True, exist_ok=True)
        agg_df.to_csv(fallback_dir / "raw" / "all_trade_details.csv", index=False, encoding="utf-8-sig")
        _dump_cfg_fingerprint(fallback_dir / "meta", cfg, argv=argv, note=note)
        (fallback_dir / "meta" / "run_tag.txt").write_text(run_tag, encoding="utf-8")
        return fallback_dir
    # 3) cfg fingerprint 写到最终 report_dir 的 meta/
    report_dir = Path(report_dir).resolve()
    (report_dir / "meta").mkdir(parents=True, exist_ok=True)
    _dump_cfg_fingerprint(report_dir / "meta", cfg, argv=argv, note=note)
    drift_report = _cfg_drift_report(metas=[meta for meta, _ in results], cfg=cfg)
    (report_dir / "meta" / "cfg_drift_report.json").write_text(
        json.dumps(drift_report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    # 4) clean up source bundle（保留聚合 csv 作为 audit trail，但可选清理）
    # 暂保留，便于排查；如要清理：shutil.rmtree(src_bundle, ignore_errors=True)
    return report_dir


def run_oot_eval_batch(
    *,
    from_root: Path,
    pattern: str = "*_model_pipeline",
    output_root: Path,
    cfg: OotEvaluationConfig,
    run_tag: str = "cluster_both",
    enable_shared_htf_reference: bool = True,
    argv: Sequence[str] | None = None,
    note: str = "",
    unified_portfolio: bool = True,
) -> Path:
    """批量重跑 OOT + 聚合。返回 bundle 目录。

    ``unified_portfolio``（2026-06-01，默认 True）：把**全部** cluster/interval 的候选合并成
    一次 ``_evaluate_oot_real_execution`` 回放，共享单一 PortfolioState + 单一 1000 万资金池
    + 单一 150% 总额，所有 symbol/cluster 的机会在每根 bar 全局竞争——与 sim/live 同引擎同账本。

    ``unified_portfolio=False`` 回退旧行为：每个 pool（cluster）各自独立评估、各占 1000 万 +
    各自 150%，cluster 间不竞争（仅用于对比研究，已知与 sim/live 不一致）。
    """
    from_root = Path(from_root)
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    metas = discover_train_runs(from_root, pattern=pattern)
    if not metas:
        raise ValueError(f"no train runs found under {from_root} matching {pattern!r}")
    logger.info("eval batch: discovered %d train runs under %s", len(metas), from_root)
    # 可选：构建跨组 HTF 参考
    shared_ref: pd.DataFrame | None = None
    if enable_shared_htf_reference and len(metas) >= 2:
        try:
            frames = []
            for m in metas:
                df = pd.read_csv(m.prediction_path, encoding="utf-8-sig")
                if not df.empty:
                    frames.append(df)
            if frames:
                shared_ref = pd.concat(frames, axis=0, ignore_index=True)
        except Exception as exc:
            logger.warning("build shared_ref failed: %s", exc)
            shared_ref = None
    results: list[tuple[TrainRunMeta, ModelPipelineResult]] = []
    if unified_portfolio:
        # 统一组合：全部 cluster/interval 候选合并成一次回放，全局竞争同一 1000 万 + 150%。
        per_run_output = output_root / f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}_unified"
        logger.info(
            "eval batch: UNIFIED portfolio over %d train runs (single PortfolioState, shared 150%% cap)",
            len(metas),
        )
        meta_out, res = rerun_oot_for_train_group(
            list(metas),
            cfg=cfg,
            output_dir=per_run_output,
            htf_reference_df=shared_ref,
            pool_label="unified",
        )
        results.append((meta_out, res))
    else:
        # 旧行为：每 pool（cluster）跨 interval 合并、各自独立评估（cluster 间不竞争）。
        per_run_output = output_root / f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}_per_cluster"
        for group in _group_train_runs_by_pool(metas):
            group_names = ", ".join(str(m.interval) for m in group)
            group_label = f"{group[0].pool_name}[{group_names}]"
            try:
                meta_out, res = rerun_oot_for_train_group(
                    group,
                    cfg=cfg,
                    output_dir=per_run_output,
                    htf_reference_df=shared_ref,
                )
                results.append((meta_out, res))
            except Exception:
                logger.exception("rerun OOT failed for grouped pool %s", group_label)
        if not results:
            raise RuntimeError("all per-cluster OOT reruns failed; see logs")
    # 聚合到 bundle
    bundle_dir = _aggregate_bundle(
        output_root, run_tag, results, cfg, argv=argv, note=note, unified=unified_portfolio
    )
    logger.info("eval batch: aggregated bundle at %s", bundle_dir)
    return bundle_dir


__all__ = [
    "TrainRunMeta",
    "discover_train_runs",
    "rerun_oot_for_train_group",
    "rerun_oot_for_train_run",
    "run_oot_eval_batch",
]
