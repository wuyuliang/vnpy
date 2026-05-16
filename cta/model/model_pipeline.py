"""CTA model pipeline with signal-type split and walk-forward evaluation."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import platform
import re
import subprocess
import sys
from dataclasses import dataclass, replace as dc_replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Literal, Sequence

import numpy as np
import pandas as pd

from cta.config.baseline_skill_suite_config import (
    BASELINE_SIGNAL_TYPES,
    DEFAULT_REPORT_ROOT,
    LABEL_MAE_PENALTY,
    LABEL_THRESHOLD,
)
from cta.config.model_oot_eval_config import DEFAULT_OOT_EVAL_CONFIG, OotEvaluationConfig
from cta.config.symbol_cluster_config import infer_symbol_cluster
from cta.config.skill_tight_range_breakout_config import BacktestConfig
from cta.model.feature.training_feature_builder import (
    DEFAULT_GENERIC_COLUMNS,
    FEATURE_ROOT,
)
from cta.model.final_decision_model import FinalDecisionModel, evaluate_final_decision_model
from cta.model.mfe_mae_model import MfeMaeModel, evaluate_mfe_mae_model
from cta.model.pipeline_feature_enrichment import (
    _auto_enrich_candidate_features_for_models,
    _build_training_feature_table_with_auto_fallback,
)
from cta.model.pipeline_html_report import write_pipeline_oot_html_report
from cta.model.pipeline_oot_evaluation import _evaluate_oot_real_execution
from cta.model.pipeline_pooling import _build_pooled_feature_df as _build_pooled_feature_df_impl
from cta.model.regime_classifier_model import RegimeClassifierModel, evaluate_regime_model
from cta.model.trade_filter_model import TradeFilterModel, evaluate_trade_filter_model
from cta.portfolio_logic.score_calibrator import ScoreCalibrator
from cta.strategy.baseline_skill_suite import generate_candidate_opportunities, prepare_master_feature_frame
from cta.strategy.skill_tight_range_backtest import load_bars, normalize_interval, resolve_exchange
from cta.utils.random_seed import seed_all

# B5 fix: WindowMode 放到所有 import 之后，符合 PEP 8 import 顺序。
WindowMode = Literal["expanding", "sliding", "rolling"]

# B1 fix: MFE/MAE 训练若没有可用 is_executed=1 样本，返回这个 sentinel 串，
# 避免拿全 0 标签训出"恒预测 0"的回归器。
MFE_MAE_KIND_SKIPPED_NO_EXEC = "skipped_no_exec"

logger = logging.getLogger(__name__)

FEATURES_DOC_PATH = Path(__file__).resolve().parents[1] / "feature" / "FEATURES.md"
CAUSALITY_MANIFEST_PATH = Path(__file__).resolve().parents[1] / "feature" / "causality_manifest.csv"
SYMBOLS_RANKING_PATH = Path(__file__).resolve().parents[1] / "feature" / "symbols_research_ranking.csv"

_FEATURE_MEANING_FALLBACK: dict[str, str] = {
    "feature_close": "信号时点收盘价",
    "feature_open": "信号时点开盘价",
    "feature_high": "信号时点最高价",
    "feature_low": "信号时点最低价",
    "feature_volume": "信号时点成交量",
    "feature_open_interest": "信号时点持仓量",
    "feature_turnover": "信号时点成交额",
    "feature_atr14": "14周期 ATR 波动率",
    "feature_trend_score": "趋势强弱分数",
    "feature_trend_dir": "趋势方向编码",
    "feature_breakout_score": "突破质量分数",
    "feature_setup_quality": "交易 setup 质量分数",
    "feature_tr_width": "tight range 宽度",
    "feature_tr_range_atr": "tight range / ATR 比值",
    "feature_don_upper_entry": "Donchian 上轨入场价",
    "feature_don_lower_entry": "Donchian 下轨入场价",
    "feature_don_atr20": "Donchian 宽度/ATR20",
    "feature_bp_trigger": "突破回踩触发价",
    "feature_bp_stop": "突破回踩止损价",
    "feature_bp_valid": "突破回踩有效标记",
    "feature_bp_confirmed": "突破回踩确认标记",
    "feature_side_code": "方向编码（long=1, short=-1）",
    "feature_signal_code": "信号类型编码",
    "feature_trigger": "突破/触发价（决策时刻可见）",
    "feature_fallback": "兜底常数特征（无可用特征时）",
    "model_trade_setup": "候选构造的交易质量特征（trade gate）",
    "model_trade_breakout_trend": "候选构造的突破*趋势交互特征（trade gate）",
    "model_regime_state": "候选构造的状态特征（regime gate）",
    "model_regime_volatility": "候选构造的波动特征（regime gate）",
    "model_mfe_edge": "候选构造的预期边际特征（mfe/mae gate）",
    "model_mfe_side_interaction": "候选构造的边际-方向交互特征（mfe/mae gate）",
    "auto_close": "候选构造的通用收盘价特征",
    "auto_open": "候选构造的通用开盘价特征",
    "auto_high": "候选构造的通用最高价特征",
    "auto_low": "候选构造的通用最低价特征",
    "auto_volume": "候选构造的通用成交量特征",
}


@dataclass(frozen=True)
class ModelPipelineResult:
    output_dir: Path
    candidate_path: Path
    feature_table_path: Path
    prediction_path: Path
    metrics_path: Path
    oot_monthly_path: Path
    oot_summary_path: Path
    oot_trades_path: Path
    html_report_path: Path
    top_feature_importance_path: Path
    report_path: Path


@dataclass(frozen=True)
class _WalkForwardWindow:
    window_id: int
    train: pd.DataFrame
    valid: pd.DataFrame
    test: pd.DataFrame
    train_end: pd.Timestamp
    valid_end: pd.Timestamp
    test_end: pd.Timestamp


def _safe_name(value: str) -> str:
    out = re.sub(r"[^A-Za-z0-9_]+", "_", str(value).strip().lower())
    return out.strip("_") or "unknown"


@lru_cache(maxsize=8)
def _load_feature_meaning_map_cached(
    features_doc_path: str, mtime_ns: int
) -> dict[str, str]:
    """Inner cached loader keyed by (path, mtime_ns).

    D4 fix：单跟 path 缓存会让长跑进程在 FEATURES.md 改动后仍取老映射。把 mtime
    放进缓存键，文件改动后自动失效一次重读。``mtime_ns`` 是不存在文件的兜底 0。
    """
    path = Path(features_doc_path)
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except Exception:
        return {}

    mapping: dict[str, str] = {}
    pat = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*([^|]+?)\s*\|")
    for raw in text.splitlines():
        line = raw.strip()
        m = pat.match(line)
        if not m:
            continue
        key = str(m.group(1)).strip()
        meaning = str(m.group(2)).strip()
        if key and meaning and key not in mapping:
            mapping[key] = meaning
    return mapping


def _load_feature_meaning_map(features_doc_path: str) -> dict[str, str]:
    """Load ``feature_name -> meaning`` mapping from FEATURES.md markdown tables.

    Public wrapper that derives the cache key (path + mtime) so callers stay
    backward-compatible with the old single-arg signature.
    """
    p = Path(features_doc_path)
    try:
        mtime_ns = p.stat().st_mtime_ns if p.exists() else 0
    except OSError:
        mtime_ns = 0
    return _load_feature_meaning_map_cached(features_doc_path, mtime_ns)


def _feature_meaning(feature_name: str, features_doc_path: Path = FEATURES_DOC_PATH) -> str:
    key = str(feature_name).strip()
    if not key:
        return ""
    if key in _FEATURE_MEANING_FALLBACK:
        return _FEATURE_MEANING_FALLBACK[key]

    mapping = _load_feature_meaning_map(str(features_doc_path.resolve()))
    candidates = [key]
    if key.startswith("generic_"):
        candidates.append(key[len("generic_") :])
    if key.startswith("feature_"):
        candidates.append(key[len("feature_") :])
    for c in candidates:
        if c in mapping:
            return mapping[c]
    return "未在 FEATURES.md 登记（自定义/衍生特征）"


def _load_causality_manifest(manifest_path: Path = CAUSALITY_MANIFEST_PATH) -> dict[str, bool]:
    """Load feature causality manifest (feature -> causal flag)."""
    path = Path(manifest_path)
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(path, encoding="utf-8-sig")
    except Exception:
        return {}
    if "feature" not in df.columns or "causal" not in df.columns:
        return {}
    out: dict[str, bool] = {}
    for _, row in df.iterrows():
        feat = str(row.get("feature", "")).strip()
        if not feat:
            continue
        c = pd.to_numeric(pd.Series([row.get("causal", 1)]), errors="coerce").fillna(1).iloc[0]
        out[feat.lower()] = bool(int(c) != 0)
    return out


def _apply_causality_manifest_filter(
    feature_columns: Sequence[str],
    *,
    manifest_path: Path = CAUSALITY_MANIFEST_PATH,
) -> list[str]:
    """Drop manifest-marked non-causal features."""
    manifest = _load_causality_manifest(manifest_path=manifest_path)
    if not manifest:
        return [str(c) for c in feature_columns if str(c).strip()]
    out: list[str] = []
    for c in feature_columns:
        name = str(c).strip()
        if not name:
            continue
        lower = name.lower()
        allow = manifest.get(lower, True)
        if not allow:
            continue
        out.append(name)
    return out


def _list_unaudited_features(
    feature_columns: Sequence[str],
    *,
    manifest_path: Path = CAUSALITY_MANIFEST_PATH,
) -> list[str]:
    """Return features not listed in the causality manifest.

    P1.5：未列名特征**默认通过**（避免误杀大量 generic_*），但生成"待审计"清单，
    让团队增量补全 manifest，最终覆盖所有特征。
    """
    manifest = _load_causality_manifest(manifest_path=manifest_path)
    if not manifest:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for c in feature_columns:
        name = str(c).strip()
        if not name:
            continue
        lower = name.lower()
        if lower in seen:
            continue
        seen.add(lower)
        if lower not in manifest:
            out.append(name)
    return out


def _build_symbol_cluster_sample_weight(df: pd.DataFrame) -> pd.Series:
    """Build per-row weights to compensate dense symbol clusters in pooled training.

    P1.2 双层加权：
    1) cluster 维度：sqrt(n_clusters_in_use / symbols_in_this_cluster) —
       样本品种多的板块（如黑色 5 个 vs 贵金属 2 个）权重小化，使各板块边际样本权重均衡。
    2) cluster 内 symbol 维度：sqrt(median_samples_in_cluster / this_symbol_samples) —
       同板块内样本悬殊（RB 1万 vs HC 500）时给小样本品种更高权重。
    最终 weight = w_cluster × w_within_cluster。
    """
    if df.empty or "symbol" not in df.columns:
        return pd.Series(np.ones(len(df), dtype=float), index=df.index, dtype=float)
    sym = df["symbol"].astype(str).str.upper().fillna("")
    unique_symbols = sorted({s for s in sym.tolist() if s})
    if len(unique_symbols) <= 1:
        return pd.Series(np.ones(len(df), dtype=float), index=df.index, dtype=float)

    cluster_by_symbol = {s: infer_symbol_cluster(s) for s in unique_symbols}
    cluster_symbol_count: dict[str, int] = {}
    for s in unique_symbols:
        cl = cluster_by_symbol.get(s, "other")
        cluster_symbol_count[cl] = int(cluster_symbol_count.get(cl, 0) + 1)

    # 第二层所需：每个 symbol 的样本数 + 每个 cluster 内的样本数中位数
    sym_sample_count = sym.value_counts().to_dict()
    cluster_sym_samples: dict[str, list[int]] = {}
    for s, n in sym_sample_count.items():
        cl = cluster_by_symbol.get(str(s), "other")
        cluster_sym_samples.setdefault(cl, []).append(int(n))
    cluster_median_samples = {
        cl: float(np.median(arr)) if arr else 1.0 for cl, arr in cluster_sym_samples.items()
    }

    n_clusters_in_use = float(len(cluster_symbol_count))
    w = pd.Series(np.ones(len(df), dtype=float), index=df.index, dtype=float)
    for idx, s in sym.items():
        s_str = str(s)
        cl = cluster_by_symbol.get(s_str, "other")
        cnt = max(1, int(cluster_symbol_count.get(cl, 1)))
        w_cluster = float(np.sqrt(n_clusters_in_use / float(cnt)))
        median_in_cluster = max(1.0, float(cluster_median_samples.get(cl, 1.0)))
        this_sym_samples = max(1.0, float(sym_sample_count.get(s_str, 1)))
        w_within = float(np.sqrt(median_in_cluster / this_sym_samples))
        w.at[idx] = float(w_cluster * w_within)
    return w


def _resolve_run_exchange(
    exchange_from_rank: str | None,
    cli_exchange: str | None,
) -> str | None:
    """Resolve the runtime exchange given ranking-supplied + CLI inputs.

    D1 fix：旧实现 ``exchange_from_rank or str(args.exchange).upper() if args.exchange else None``
    被 Python 解析成 ``(exchange_from_rank or str(args.exchange).upper()) if args.exchange else None``，
    在 CLI 没传 ``--exchange`` 时即使 ranking 已提供 SHFE 也会被丢成 None。
    抽出来做白盒测试，避免跨模块靠操作符优先级隐式依赖。
    """
    if exchange_from_rank:
        return str(exchange_from_rank).upper()
    if cli_exchange:
        return str(cli_exchange).upper()
    return None


def _load_top_n_symbols_from_ranking(
    ranking_path: Path,
    top_n: int,
    *,
    respect_disabled_manifest: bool = True,
) -> list[tuple[str, str | None]]:
    """Load top-N symbols ordered by research_rank from ranking csv.

    M2：加载完成后会调用 ``filter_out_disabled_pairs`` 把 symbol_disable_manifest
    标记的"持续亏损 / 数据源不一致 / 流动性不足"品种剔除，再按 top-N 截断。
    顺序：先按 research_rank 排序 → 再过滤 disabled → 取前 top_n。
    这样剔除 3 个 disabled 后仍能保证最终 top-N 个有效品种参与训练。
    """
    n = int(top_n)
    if n <= 0:
        return []
    path = Path(ranking_path).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"symbols ranking csv not found: {path}")

    df = pd.read_csv(path, encoding="utf-8-sig")
    if "symbol" not in df.columns:
        raise KeyError(f"ranking csv missing symbol column: {path}")
    df = df.copy()
    if "research_rank" in df.columns:
        df["_rank"] = pd.to_numeric(df["research_rank"], errors="coerce")
    else:
        df["_rank"] = np.arange(len(df), dtype=float)
    df["_rank"] = df["_rank"].fillna(np.inf)
    df = df.sort_values(["_rank"]).reset_index(drop=True)

    # 先收集全部按 rank 排序的 pairs（不截断），再过滤 disabled，再取前 top_n。
    all_pairs: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for _, row in df.iterrows():
        sym = str(row.get("symbol", "")).strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        ex_raw = row.get("exchange", None)
        ex = str(ex_raw).strip().upper() if pd.notna(ex_raw) and str(ex_raw).strip() else None
        all_pairs.append((sym, ex))

    if respect_disabled_manifest:
        from cta.config.symbol_disable import filter_out_disabled_pairs

        filtered = filter_out_disabled_pairs(all_pairs)
    else:
        filtered = list(all_pairs)
    picked = filtered[:n]
    if not picked:
        raise ValueError(
            f"no valid symbols loaded from ranking csv: {path}; "
            f"check symbol_disable_manifest.csv if you expect more"
        )
    return picked


def _load_symbol_groups_from_ranking(
    ranking_path: Path,
    *,
    top_n: int = 0,
    group_by: str = "tier",
    min_symbols_per_group: int = 2,
    respect_disabled_manifest: bool = True,
) -> list[tuple[str, list[tuple[str, str | None]]]]:
    """Load symbols from ranking csv and split into ordered groups.

    分组来源：
    - ``group_by='cluster'``：基于 ``infer_symbol_cluster``；
    - 其它值：按 ranking csv 同名列（大小写不敏感）分组。
    """
    path = Path(ranking_path).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"symbols ranking csv not found: {path}")

    df = pd.read_csv(path, encoding="utf-8-sig")
    if "symbol" not in df.columns:
        raise KeyError(f"ranking csv missing symbol column: {path}")

    work = df.copy()
    if "research_rank" in work.columns:
        work["_rank"] = pd.to_numeric(work["research_rank"], errors="coerce")
    else:
        work["_rank"] = np.arange(len(work), dtype=float)
    work["_rank"] = work["_rank"].fillna(np.inf)
    work = work.sort_values(["_rank"]).reset_index(drop=True)

    row_by_symbol: dict[str, dict[str, Any]] = {}
    ordered_pairs: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for _, row in work.iterrows():
        sym = str(row.get("symbol", "")).strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        ex_raw = row.get("exchange", None)
        ex = str(ex_raw).strip().upper() if pd.notna(ex_raw) and str(ex_raw).strip() else None
        ordered_pairs.append((sym, ex))
        row_by_symbol[sym] = dict(row)

    if respect_disabled_manifest:
        from cta.config.symbol_disable import filter_out_disabled_pairs

        ordered_pairs = filter_out_disabled_pairs(ordered_pairs)

    n = int(top_n)
    if n > 0:
        ordered_pairs = ordered_pairs[:n]
    if not ordered_pairs:
        raise ValueError(f"no symbols left after ranking/group filter: {path}")

    group_by_raw = str(group_by).strip()
    group_by_key = _safe_name(group_by_raw or "tier")
    lower_to_col = {str(c).strip().lower(): str(c) for c in work.columns}
    group_col = lower_to_col.get(group_by_raw.lower())

    group_order: list[str] = []
    groups: dict[str, list[tuple[str, str | None]]] = {}
    for sym, ex in ordered_pairs:
        if group_by_key == "cluster":
            gval = infer_symbol_cluster(sym)
        else:
            row = row_by_symbol.get(sym, {})
            if group_col:
                raw = row.get(group_col, "")
            else:
                raw = row.get(group_by_raw, "")
            gval = str(raw).strip() if raw is not None else ""
            if not gval:
                gval = "ungrouped"
        gname = f"{group_by_key}_{_safe_name(gval)}"
        if gname not in groups:
            groups[gname] = []
            group_order.append(gname)
        groups[gname].append((sym, ex))

    min_size = max(1, int(min_symbols_per_group))
    out: list[tuple[str, list[tuple[str, str | None]]]] = []
    for gname in group_order:
        members = groups.get(gname, [])
        if len(members) < min_size:
            continue
        out.append((gname, members))
    if not out:
        raise ValueError(
            "no symbol groups satisfy min_symbols_per_group="
            f"{min_size} for group_by={group_by_key}"
        )
    return out


def _empty_top_feature_importance_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "signal_type",
            "window_id",
            "model",
            "model_kind",
            "rank",
            "feature",
            "importance",
            "feature_meaning",
        ]
    )


def _build_valid_test_gap_alerts(metrics_df: pd.DataFrame, *, max_gap: float) -> pd.DataFrame:
    """把每个 (signal_type, window_id, model) 的 valid/test AUC 差 > max_gap 的行抓出来。

    test 不参与选参，但若 valid AUC 与 test AUC 差距过大，说明 valid 与 test 分布偏离
    或存在仅在 valid 期可见的弱信号 — 是潜在过拟合 / 时间漂移的提示。
    """
    cols = ["signal_type", "window_id", "model", "split", "auc"]
    if metrics_df is None or metrics_df.empty or not set(cols).issubset(metrics_df.columns):
        return pd.DataFrame(
            columns=["signal_type", "window_id", "model", "valid_auc", "test_auc", "gap"]
        )
    sub = metrics_df.loc[metrics_df["split"].isin(["valid", "test"]), cols].copy()
    if sub.empty:
        return pd.DataFrame(
            columns=["signal_type", "window_id", "model", "valid_auc", "test_auc", "gap"]
        )
    sub["auc"] = pd.to_numeric(sub["auc"], errors="coerce")
    pivot = (
        sub.pivot_table(
            index=["signal_type", "window_id", "model"],
            columns="split",
            values="auc",
            aggfunc="last",
        )
        .reset_index()
    )
    pivot.columns.name = None
    if "valid" not in pivot.columns:
        pivot["valid"] = float("nan")
    if "test" not in pivot.columns:
        pivot["test"] = float("nan")
    pivot = pivot.rename(columns={"valid": "valid_auc", "test": "test_auc"})
    pivot["gap"] = (
        pd.to_numeric(pivot["valid_auc"], errors="coerce")
        - pd.to_numeric(pivot["test_auc"], errors="coerce")
    ).abs()
    out = pivot.loc[pivot["gap"].fillna(0.0) > float(max_gap)].copy()
    out = out.sort_values("gap", ascending=False).reset_index(drop=True)
    return out[["signal_type", "window_id", "model", "valid_auc", "test_auc", "gap"]]


def _build_top_feature_concentration_alerts(
    top_feature_df: pd.DataFrame,
    *,
    top1_thresh: float,
) -> pd.DataFrame:
    """top-1 特征在 top-10 集合内占比 > 阈值时告警。

    输入 ``top_feature_df`` 是 ``_tag_top_feature_importance`` 的产物，**每个
    (signal_type, window_id, model) 只保留 top-10 特征**。本函数计算的指标是：
    ``top1_pct_in_top10 = importance(top1) / sum(importance(top1..top10))``。

    这是一个**相对集中度**指标，不等同于"top1 在全集 ~400 个特征里的真实占比"。
    在 top-10 内占比超过 ``top1_thresh``（默认 0.5）说明 top-10 的"次重要特征贡献微弱"，
    高度依赖单一特征，常见于穿越或常数特征。要看真实全集占比请检查
    *_feature_manifest.csv（dump_feature_manifest 落盘的全集 importance）。
    """
    cols = ["signal_type", "window_id", "model", "feature", "importance"]
    out_cols = ["signal_type", "window_id", "model", "top_feature", "top1_pct_in_top10"]
    if top_feature_df is None or top_feature_df.empty or not set(cols).issubset(top_feature_df.columns):
        return pd.DataFrame(columns=out_cols)
    df = top_feature_df.loc[:, cols].copy()
    df["importance"] = pd.to_numeric(df["importance"], errors="coerce").fillna(0.0)
    rows: list[dict[str, Any]] = []
    for (sig, win, mdl), grp in df.groupby(["signal_type", "window_id", "model"], sort=False):
        total = float(grp["importance"].sum())
        if total <= 0:
            continue
        top_row = grp.sort_values("importance", ascending=False).iloc[0]
        pct = float(top_row["importance"]) / total
        if pct >= float(top1_thresh):
            rows.append(
                {
                    "signal_type": str(sig),
                    "window_id": int(win),
                    "model": str(mdl),
                    "top_feature": str(top_row["feature"]),
                    "top1_pct_in_top10": float(pct),
                }
            )
    if not rows:
        return pd.DataFrame(columns=out_cols)
    out = pd.DataFrame(rows).sort_values("top1_pct_in_top10", ascending=False).reset_index(drop=True)
    return out


def _tag_top_feature_importance(
    top_df: pd.DataFrame,
    *,
    signal_type: str,
    window_id: int,
    model: str,
    model_kind: str,
) -> pd.DataFrame:
    out = top_df.copy()
    if out.empty:
        return _empty_top_feature_importance_frame()
    if "feature" not in out.columns or "importance" not in out.columns:
        return _empty_top_feature_importance_frame()
    out = out[["feature", "importance"]].copy()
    out["importance"] = pd.to_numeric(out["importance"], errors="coerce").fillna(0.0)
    out = out.sort_values(["importance", "feature"], ascending=[False, True]).reset_index(drop=True)
    out["rank"] = np.arange(1, len(out) + 1, dtype=int)
    out["feature_meaning"] = out["feature"].astype(str).map(_feature_meaning)
    out["signal_type"] = str(signal_type)
    out["window_id"] = int(window_id)
    out["model"] = str(model)
    out["model_kind"] = str(model_kind)
    return out[
        [
            "signal_type",
            "window_id",
            "model",
            "model_kind",
            "rank",
            "feature",
            "importance",
            "feature_meaning",
        ]
    ]


def _dump_feature_manifest(
    *,
    joblib_path: Path,
    feature_columns: Sequence[str],
    importance_df: pd.DataFrame | None,
    model_kind: str,
) -> Path | None:
    """Persist a per-model feature manifest beside the joblib model.

    部署时下游需要严格按"训练用过的特征清单"做 schema 校验。每个
    ``<model>.joblib`` 旁边写一份 ``<model>_features.csv``，列：
    ``rank / feature / importance / feature_meaning``，按 importance 降序。

    防御式实现：
    1. 永远写出全部 ``feature_columns``（不只 top-k），哪怕 importance 缺失也把缺失行
       置为 0.0 后排在末尾；下游能拿到完整 schema 是关键。
    2. ``importance_df`` 为 None / 空 / 列缺失时也不抛异常，全部置 0.0。
    3. 写文件用 utf-8-sig + index=False，与 pipeline 其它产物保持一致。
    4. 返回写出的 path 便于日志追踪；joblib 不存在时返回 None（孤儿保护）。
    """
    if not joblib_path.exists():
        logger.warning(
            "skip feature manifest: joblib not found at %s (mfe_mae skipped or save failed)",
            joblib_path,
        )
        return None

    feats = [str(c) for c in feature_columns if str(c).strip()]
    if not feats:
        logger.warning("skip feature manifest for %s: empty feature_columns", joblib_path)
        return None

    # 准备 importance 映射，缺失全部置 0.0
    imp_map: dict[str, float] = {f: 0.0 for f in feats}
    try:
        if importance_df is not None and not importance_df.empty and {"feature", "importance"}.issubset(
            set(importance_df.columns)
        ):
            for _, r in importance_df.iterrows():
                fname = str(r["feature"])
                if fname not in imp_map:
                    continue
                v = pd.to_numeric(r["importance"], errors="coerce")
                imp_map[fname] = float(v) if pd.notna(v) else 0.0
    except Exception as exc:  # 任何异常都不能阻断 pipeline
        logger.warning("feature manifest importance parse failed for %s: %s", joblib_path, exc)

    df = pd.DataFrame({"feature": feats, "importance": [imp_map[f] for f in feats]})
    # 主键去重（保险），按 importance 降序 + feature 字典序
    df = df.drop_duplicates(subset=["feature"], keep="first")
    df = df.sort_values(["importance", "feature"], ascending=[False, True]).reset_index(drop=True)
    df["rank"] = np.arange(1, len(df) + 1, dtype=int)
    df["feature_meaning"] = df["feature"].astype(str).map(_feature_meaning)
    df["model_kind"] = str(model_kind)
    df = df[["rank", "feature", "importance", "feature_meaning", "model_kind"]]

    manifest_path = joblib_path.with_name(joblib_path.stem + "_features.csv")
    try:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(manifest_path, index=False, encoding="utf-8-sig")
    except Exception as exc:
        logger.warning("failed writing feature manifest %s: %s", manifest_path, exc)
        return None
    return manifest_path


def _log_top_feature_importance(top_df: pd.DataFrame) -> None:
    if top_df.empty:
        return
    sig = str(top_df["signal_type"].iloc[0])
    wid = int(top_df["window_id"].iloc[0])
    model = str(top_df["model"].iloc[0])
    model_kind = str(top_df["model_kind"].iloc[0])
    rows = [f"{r.feature}={float(r.importance):.6f}" for r in top_df.itertuples(index=False)]
    logger.info(
        "top10 feature importance | signal=%s window=%s model=%s kind=%s | %s",
        sig,
        wid,
        model,
        model_kind,
        ", ".join(rows),
    )


def _build_synthetic_candidate(
    symbol: str,
    exchange: str,
    interval: str,
    start_date: str,
    end_date: str,
    periods: int = 400,
) -> pd.DataFrame:
    rng = np.random.default_rng(20260426)
    n = max(int(periods), 120)
    dt = pd.date_range(start=pd.Timestamp(start_date), end=pd.Timestamp(end_date), periods=n)
    x1 = rng.normal(0.0, 1.0, size=n)
    x2 = rng.normal(0.0, 1.0, size=n)
    x3 = rng.normal(0.0, 1.0, size=n)

    signal_types = np.array(BASELINE_SIGNAL_TYPES, dtype=object)
    signal_code = np.arange(n, dtype=int) % len(signal_types)
    signal_type = signal_types[signal_code]

    status_draw = rng.uniform(0.0, 1.0, size=n)
    candidate_status = np.where(
        status_draw < 0.65,
        "filled",
        np.where(status_draw < 0.85, "not_triggered", "filtered"),
    )
    is_executed = (candidate_status == "filled").astype(int)
    is_filtered = (candidate_status == "filtered").astype(int)
    is_triggered = (candidate_status == "filled").astype(int)

    raw_mfe = np.maximum(0.0, 0.9 * x1 + 0.4 * x2 + rng.normal(0.0, 0.25, size=n))
    raw_mae = np.maximum(0.0, -0.5 * x1 + 0.5 * x3 + rng.normal(0.0, 0.25, size=n))
    future_mfe_atr = np.where(is_executed == 1, raw_mfe, 0.0)
    future_mae_atr = np.where(is_executed == 1, raw_mae, 0.0)
    label = np.where(
        is_executed == 1,
        ((raw_mfe - LABEL_MAE_PENALTY * raw_mae) > LABEL_THRESHOLD).astype(int),
        0,
    )

    side = np.where(x2 >= 0, "long", "short")
    regime = np.where(x1 > 0.7, "trend_up", np.where(x1 < -0.7, "trend_down", "range"))
    base_price = 3500.0 + 20.0 * x1

    return pd.DataFrame(
        {
            "symbol": str(symbol).upper(),
            "exchange": str(exchange).upper(),
            "interval": str(interval),
            "datetime": dt,
            "signal_datetime": dt,
            "signal_type": signal_type,
            "side": side,
            "entry_i": np.arange(n, dtype=int),
            "horizon_i": np.arange(n, dtype=int) + 20,
            "entry_price": base_price,
            "stop_price": base_price - np.where(side == "long", 20.0, -20.0),
            "future_mfe_atr": future_mfe_atr,
            "future_mae_atr": future_mae_atr,
            "label_class": label,
            "regime_label": regime,
            "candidate_status": candidate_status,
            "is_executed": is_executed,
            "is_filtered": is_filtered,
            "is_triggered": is_triggered,
            "filtered_reason": np.where(is_filtered == 1, "synthetic_filter", ""),
            "feature_close": base_price,
            "feature_volume": 2000.0 + 300.0 * np.abs(x2),
            "feature_atr14": 20.0 + 2.0 * np.abs(x3),
            "feature_trend_score": x1,
            "feature_breakout_score": x2,
            "feature_setup_quality": 0.6 * x1 + 0.4 * x2,
        }
    )


def _ensure_training_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "datetime" not in out.columns:
        raise KeyError("candidate frame missing datetime")
    out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
    out = out.dropna(subset=["datetime"]).copy()

    if "candidate_status" not in out.columns:
        out["candidate_status"] = "filled"
    out["candidate_status"] = out["candidate_status"].astype(str).replace({"": "filled"}).fillna("filled")

    if "is_executed" not in out.columns:
        out["is_executed"] = (out["candidate_status"] == "filled").astype(int)
    out["is_executed"] = pd.to_numeric(out["is_executed"], errors="coerce").fillna(0).astype(int)

    if "is_filtered" not in out.columns:
        out["is_filtered"] = out["candidate_status"].astype(str).str.startswith("filtered").astype(int)
    out["is_filtered"] = pd.to_numeric(out["is_filtered"], errors="coerce").fillna(0).astype(int)

    if "is_triggered" not in out.columns:
        out["is_triggered"] = out["is_executed"]
    out["is_triggered"] = pd.to_numeric(out["is_triggered"], errors="coerce").fillna(0).astype(int)

    if "filtered_reason" not in out.columns:
        out["filtered_reason"] = ""
    out["filtered_reason"] = out["filtered_reason"].astype(str).fillna("")

    if "label_class" not in out.columns:
        if "label_win" in out.columns:
            out["label_class"] = pd.to_numeric(out["label_win"], errors="coerce").fillna(0).astype(int)
        else:
            out["label_class"] = 0
    out["label_class"] = pd.to_numeric(out["label_class"], errors="coerce").fillna(0).astype(int)

    if "future_mfe_atr" not in out.columns:
        out["future_mfe_atr"] = pd.to_numeric(out.get("label_mfe_atr", 0.0), errors="coerce")
    if "future_mae_atr" not in out.columns:
        out["future_mae_atr"] = pd.to_numeric(out.get("label_mae_atr", 0.0), errors="coerce")
    # atr_warmed 标识 ATR 是否已经热身（前 ~14 根早期 bar 的 ATR 不可靠）
    # 缺失时（来自 trade_log 路径）默认置 1。
    if "atr_warmed" not in out.columns:
        out["atr_warmed"] = 1
    out["atr_warmed"] = pd.to_numeric(out["atr_warmed"], errors="coerce").fillna(1).astype(int)
    # D3 fix：旧实现对所有行 fillna(0.0)，会把 atr_warmup 期"未知"的 mfe/mae
    # 悄悄写成 0/0，候选样本特意保留的 NaN 信号被吞掉。这里仅对已热身行 fillna，
    # 让 warmup 行（line 728 会被 drop）保持 NaN 便于诊断。
    mfe_raw = pd.to_numeric(out["future_mfe_atr"], errors="coerce")
    mae_raw = pd.to_numeric(out["future_mae_atr"], errors="coerce")
    warm_mask_arr = out["atr_warmed"].astype(int).to_numpy() == 1
    out["future_mfe_atr"] = np.where(warm_mask_arr, mfe_raw.fillna(0.0), mfe_raw)
    out["future_mae_atr"] = np.where(warm_mask_arr, mae_raw.fillna(0.0), mae_raw)

    if "regime_label" not in out.columns:
        if "feature_trend_dir" in out.columns:
            trend_raw = out["feature_trend_dir"]
        else:
            trend_raw = pd.Series([0.0] * len(out), index=out.index)
        trend_dir = pd.to_numeric(trend_raw, errors="coerce").fillna(0.0)
        out["regime_label"] = np.where(
            trend_dir > 0,
            "trend_up",
            np.where(trend_dir < 0, "trend_down", "range"),
        )
    out["regime_label"] = out["regime_label"].astype(str).replace({"": "range"}).fillna("range")

    # Avoid all-0 labels after adding negative samples.
    # 重算时仅对 is_executed==1 且 atr_warmed==1 的成交样本生效，
    # 不能把 not_triggered/filtered 或 ATR warmup 期的样本误标为正样本。
    if len(np.unique(out["label_class"])) < 2:
        mfe = pd.to_numeric(out["future_mfe_atr"], errors="coerce").fillna(0.0)
        mae = pd.to_numeric(out["future_mae_atr"], errors="coerce").fillna(0.0)
        exec_mask = out["is_executed"].astype(int) == 1
        warm_mask = out["atr_warmed"].astype(int) == 1
        valid_mask = exec_mask & warm_mask
        new_label = pd.Series(0, index=out.index, dtype=int)
        if valid_mask.any():
            new_label.loc[valid_mask] = (
                (mfe.loc[valid_mask] - LABEL_MAE_PENALTY * mae.loc[valid_mask]) > LABEL_THRESHOLD
            ).astype(int)
        out["label_class"] = new_label

    if "signal_type" not in out.columns:
        out["signal_type"] = "unknown"
    out["signal_type"] = out["signal_type"].astype(str).replace({"": "unknown"}).fillna("unknown")

    return out.sort_values("datetime").reset_index(drop=True)


def _is_numeric_like_column(series: pd.Series) -> bool:
    """判断列是否能安全作为数值特征（数值/布尔/可被强转的数值字符串）。

    跳过 object dtype 中含真实字符串（如 regime_label）的列，避免被强转为 NaN。
    """
    if pd.api.types.is_bool_dtype(series):
        return True
    if pd.api.types.is_numeric_dtype(series):
        return True
    if pd.api.types.is_object_dtype(series):
        sample = series.dropna()
        if sample.empty:
            return False
        # B11 fix: 之前只采样 head(50) 容易漏判靠后混入字符串的列；
        # 提到 200 行兼顾性能和正确性。即便误判，下游 pd.to_numeric(errors="coerce")
        # 会兜底把异常值变 NaN，不会崩溃。
        return all(isinstance(v, (int, float, bool, np.integer, np.floating, np.bool_)) for v in sample.head(200))
    return False


def _select_feature_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    out = df.copy()
    raw_cols = [c for c in out.columns if c.startswith("feature_") or c.startswith("generic_")]
    feature_cols = [c for c in raw_cols if _is_numeric_like_column(out[c])]
    skipped = sorted(set(raw_cols) - set(feature_cols))
    if skipped:
        logger.debug("skip non-numeric feature columns: %s", skipped)

    if not feature_cols:
        # B2 fix: df.get(col, scalar) 在缺列时返回 scalar，scalar.astype(...) 会 AttributeError；
        # 这里改成显式取列并在缺列时构造同长度 Series 兜底。
        if "side" in out.columns:
            side_series = out["side"]
        else:
            side_series = pd.Series([""] * len(out), index=out.index)
        out["feature_side_code"] = np.where(side_series.astype(str).str.lower() == "long", 1.0, -1.0)

        if "signal_type" in out.columns:
            st_series = out["signal_type"]
        else:
            st_series = pd.Series(["unknown"] * len(out), index=out.index)
        out["feature_signal_code"] = pd.Categorical(st_series.astype(str)).codes.astype(float)

        # P1-B 加固：fallback 不再写 ``feature_entry_price = entry_price``。
        # entry_price 是 entry bar (i+1) 内 stop / limit 实际成交价，决策时刻
        # (signal bar 收盘) 不可见 → 是潜在 lookahead leak。改用 trigger
        # （决策时刻可见的突破触发价）。trigger 缺失时退到 NaN（imputer 兜底）。
        if "trigger" in out.columns:
            out["feature_trigger"] = pd.to_numeric(out["trigger"], errors="coerce")
        else:
            out["feature_trigger"] = np.nan
        feature_cols = ["feature_side_code", "feature_signal_code", "feature_trigger"]

    for c in feature_cols:
        if pd.api.types.is_bool_dtype(out[c]):
            out[c] = out[c].astype(float)
        else:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    feature_cols = [c for c in feature_cols if out[c].notna().any()]
    if not feature_cols:
        out["feature_fallback"] = 0.0
        feature_cols = ["feature_fallback"]
    return out, feature_cols


def _filter_model_leakage_features(
    feature_columns: Sequence[str],
    *,
    model_name: str,
) -> list[str]:
    """Filter known leakage-prone features by model type."""
    # 命名级 leakage 词典。除了原有 label/future/target/next/lead/forward/fwd/pred 之外，
    # 补充常见的"中心化窗口 / 前/后视 / 偷看下一根"风格命名（多见于自动生成的 generic 特征）。
    leak_token_pattern = re.compile(
        r"(^|_)(label|future|target|next|lead|forward|fwd|pred|"
        r"rollingmax|rollingmin|centered|lookahead|peek|aft|shiftneg)($|_)"
    )

    def _core_name(name: str) -> str:
        lower = str(name).strip().lower()
        for prefix in ("feature_", "generic_"):
            if lower.startswith(prefix):
                return lower[len(prefix) :]
        return lower

    seen: set[str] = set()
    out: list[str] = []
    m = str(model_name).strip().lower()
    regime_exact_block = {
        "feature_trend_score",
        "feature_trend_dir",
        "generic_auto_trend",
        "generic_model_regime_state",
    }

    for c in feature_columns:
        name = str(c).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        lower = name.lower()
        core = _core_name(lower)

        # Universal guardrails.
        if lower.startswith("label_") or lower.startswith("future_") or lower.startswith("pred_"):
            continue
        if lower in {"regime_label", "label_class"}:
            continue
        if leak_token_pattern.search(core):
            continue

        # Regime label is currently derived from trend fields; block same-source features.
        if m == "regime_classifier":
            if lower in regime_exact_block:
                continue
            if lower.startswith("feature_trend_"):
                continue
            if lower.startswith("generic_model_regime_"):
                continue

        out.append(name)
    return out


def _safe_float(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float("nan")
    if not np.isfinite(out):
        return float("nan")
    return out


def _auc_gap(train_auc: float, valid_auc: float) -> float:
    t = _safe_float(train_auc)
    v = _safe_float(valid_auc)
    if np.isnan(t) or np.isnan(v):
        return float("inf")
    return abs(t - v)


def _select_best_param_trial(
    trials: Sequence[dict[str, Any]],
    *,
    max_auc_gap: float,
) -> dict[str, Any]:
    """Pick best trial with strict no-leak policy.

    选参只用 train/valid：
    1. 先过滤 ``abs(train_auc-valid_auc) <= max_auc_gap``；
    2. 在可行集合中按 valid_auc 最大选；
    3. 若无可行集合，回退到 gap 最小，再看 valid_auc。

    注意：``oot_auc`` 仅用于最终报告，不参与排序。
    """
    if not trials:
        raise ValueError("no parameter-search trial provided")

    rows: list[dict[str, Any]] = []
    for idx, row in enumerate(trials):
        train_auc = _safe_float(row.get("train_auc"))
        valid_auc = _safe_float(row.get("valid_auc"))
        gap = _auc_gap(train_auc, valid_auc)
        out = dict(row)
        out["train_auc"] = train_auc
        out["valid_auc"] = valid_auc
        out["auc_gap"] = gap
        out["_idx"] = idx
        rows.append(out)

    feasible = [
        r
        for r in rows
        if np.isfinite(r["valid_auc"])
        and np.isfinite(r["auc_gap"])
        and float(r["auc_gap"]) <= float(max_auc_gap) + 1e-12
    ]
    if feasible:
        feasible.sort(key=lambda r: (-float(r["valid_auc"]), float(r["auc_gap"]), int(r["_idx"])))
        return feasible[0]

    finite = [r for r in rows if np.isfinite(r["valid_auc"])]
    if finite:
        finite.sort(key=lambda r: (float(r["auc_gap"]), -float(r["valid_auc"]), int(r["_idx"])))
        return finite[0]

    rows.sort(key=lambda r: int(r["_idx"]))
    return rows[0]


def _default_label_stop_loss_pct() -> float:
    import inspect

    from cta.strategy.baseline_skill_suite import generate_candidate_opportunities

    sig = inspect.signature(generate_candidate_opportunities)
    param = sig.parameters.get("label_stop_loss_pct")
    if param is None or param.default is inspect.Parameter.empty:
        raise RuntimeError("generate_candidate_opportunities missing label_stop_loss_pct default")
    return float(param.default)


def _validate_stop_loss_pct_consistency(
    oot_stop_loss_pct: float | None = None,
    label_stop_loss_pct: float | None = None,
    *,
    tolerance: float = 0.005,
) -> None:
    """Runtime guard: OOT intrabar stop loss should stay aligned with training labels."""
    if oot_stop_loss_pct is None:
        oot_stop_loss_pct = float(OotEvaluationConfig().intrabar_stop_loss_pct)
    if label_stop_loss_pct is None:
        label_stop_loss_pct = _default_label_stop_loss_pct()
    diff = abs(float(oot_stop_loss_pct) - float(label_stop_loss_pct))
    if diff > float(tolerance):
        raise RuntimeError(
            "intrabar_stop_loss_pct ({:.6f}) != label_stop_loss_pct ({:.6f}); "
            "diff={:.6f} > tolerance={:.6f}. Please sync OOT vs training stop-loss config."
            .format(float(oot_stop_loss_pct), float(label_stop_loss_pct), diff, float(tolerance))
        )


def _trade_filter_param_grid() -> list[dict[str, Any]]:
    return [
        {
            "learning_rate": 0.03,
            "max_depth": 3,
            "max_iter": 180,
            "min_samples_leaf": 20,
            "max_leaf_nodes": 31,
            "l2_regularization": 1.0,
        },
        {
            "learning_rate": 0.02,
            "max_depth": 2,
            "max_iter": 240,
            "min_samples_leaf": 30,
            "max_leaf_nodes": 31,
            "l2_regularization": 2.0,
        },
        {
            "learning_rate": 0.05,
            "max_depth": 2,
            "max_iter": 140,
            "min_samples_leaf": 40,
            "max_leaf_nodes": 15,
            "l2_regularization": 3.0,
        },
        {
            "learning_rate": 0.03,
            "max_depth": 4,
            "max_iter": 160,
            "min_samples_leaf": 25,
            "max_leaf_nodes": 31,
            "l2_regularization": 1.5,
        },
    ]


def _regime_classifier_param_grid() -> list[dict[str, Any]]:
    return [
        {
            "n_estimators": 200,
            "max_depth": 5,
            "min_samples_leaf": 20,
            "max_features": "sqrt",
            "class_weight": "balanced_subsample",
        },
        {
            "n_estimators": 180,
            "max_depth": 4,
            "min_samples_leaf": 24,
            "max_features": "sqrt",
            "class_weight": "balanced_subsample",
        },
        {
            "n_estimators": 320,
            "max_depth": 6,
            "min_samples_leaf": 30,
            "max_features": 0.6,
            "class_weight": "balanced_subsample",
        },
        {
            "n_estimators": 280,
            "max_depth": 5,
            "min_samples_leaf": 40,
            "max_features": 0.5,
            "class_weight": "balanced_subsample",
        },
    ]


def _mfe_mae_param_grid() -> list[dict[str, Any]]:
    return [
        {
            "n_estimators": 200,
            "max_depth": 5,
            "min_samples_leaf": 20,
            "min_samples_split": 80,
            "max_features": 0.35,
        },
        {
            "n_estimators": 180,
            "max_depth": 4,
            "min_samples_leaf": 40,
            "min_samples_split": 120,
            "max_features": 0.3,
        },
        {
            "n_estimators": 260,
            "max_depth": 5,
            "min_samples_leaf": 28,
            "min_samples_split": 90,
            "max_features": 0.4,
        },
        {
            "n_estimators": 300,
            "max_depth": 7,
            "min_samples_leaf": 24,
            "min_samples_split": 70,
            "max_features": 0.5,
        },
    ]


def _train_mfe_mae_or_skip(
    train_df: pd.DataFrame,
    feature_columns: list[str],
    *,
    random_state: int = 2026,
    mfe_column: str = "future_mfe_atr",
    mae_column: str = "future_mae_atr",
    model_params: dict[str, Any] | None = None,
    sample_weight: pd.Series | np.ndarray | None = None,
) -> tuple[MfeMaeModel | None, str]:
    """B1 fix: 仅当 train 集存在 is_executed=1 样本时才真正训练 MFE/MAE 模型。

    train_exec 为空时返回 ``(None, MFE_MAE_KIND_SKIPPED_NO_EXEC)``，由调用方写
    NaN 指标 + NaN 预测，避免用全 0 标签训出"恒预测 0"的回归器。
    """
    if "is_executed" in train_df.columns:
        exec_mask = pd.to_numeric(train_df["is_executed"], errors="coerce").fillna(0).astype(int) == 1
    else:
        exec_mask = pd.Series(True, index=train_df.index)
    train_exec = train_df.loc[exec_mask]
    if train_exec.empty:
        return None, MFE_MAE_KIND_SKIPPED_NO_EXEC
    if sample_weight is None:
        sw_exec = None
    else:
        if isinstance(sample_weight, pd.Series):
            sw_exec = sample_weight.reindex(train_df.index).loc[train_exec.index].to_numpy(dtype=float)
        else:
            sw_all = np.asarray(sample_weight, dtype=float).reshape(-1)
            if sw_all.shape[0] != len(train_df):
                raise ValueError(f"sample_weight length mismatch: {sw_all.shape[0]} != {len(train_df)}")
            sw_exec = sw_all[exec_mask.to_numpy()]
    model = MfeMaeModel(random_state=random_state, model_params=model_params).fit(
        train_exec,
        feature_columns=feature_columns,
        mfe_column=mfe_column,
        mae_column=mae_column,
        sample_weight=sw_exec,
    )
    return model, model.model_kind


def _tune_trade_filter_model(
    train_df: pd.DataFrame,
    valid_df: pd.DataFrame,
    feature_columns: list[str],
    *,
    random_state: int,
    max_auc_gap: float,
    sample_weight: pd.Series | np.ndarray | None = None,
) -> tuple[TradeFilterModel, dict[str, Any]]:
    trials: list[dict[str, Any]] = []
    for params in _trade_filter_param_grid():
        model = TradeFilterModel(random_state=random_state, model_params=params).fit(
            train_df,
            feature_columns=feature_columns,
            label_column="label_class",
            sample_weight=sample_weight,
        )
        train_auc = evaluate_trade_filter_model(
            model,
            train_df,
            feature_columns=feature_columns,
            label_column="label_class",
        ).get("auc", float("nan"))
        valid_auc = evaluate_trade_filter_model(
            model,
            valid_df,
            feature_columns=feature_columns,
            label_column="label_class",
        ).get("auc", float("nan"))
        trials.append(
            {
                "model": model,
                "params": dict(params),
                "train_auc": train_auc,
                "valid_auc": valid_auc,
            }
        )
    best = _select_best_param_trial(trials, max_auc_gap=max_auc_gap)
    return best["model"], best


def _tune_regime_classifier_model(
    train_df: pd.DataFrame,
    valid_df: pd.DataFrame,
    feature_columns: list[str],
    *,
    random_state: int,
    max_auc_gap: float,
    sample_weight: pd.Series | np.ndarray | None = None,
) -> tuple[RegimeClassifierModel, dict[str, Any]]:
    trials: list[dict[str, Any]] = []
    for params in _regime_classifier_param_grid():
        model = RegimeClassifierModel(random_state=random_state, model_params=params).fit(
            train_df,
            feature_columns=feature_columns,
            label_column="regime_label",
            sample_weight=sample_weight,
        )
        train_auc = evaluate_regime_model(
            model,
            train_df,
            feature_columns=feature_columns,
            label_column="regime_label",
        ).get("auc", float("nan"))
        valid_auc = evaluate_regime_model(
            model,
            valid_df,
            feature_columns=feature_columns,
            label_column="regime_label",
        ).get("auc", float("nan"))
        trials.append(
            {
                "model": model,
                "params": dict(params),
                "train_auc": train_auc,
                "valid_auc": valid_auc,
            }
        )
    best = _select_best_param_trial(trials, max_auc_gap=max_auc_gap)
    return best["model"], best


def _tune_mfe_mae_model(
    train_df: pd.DataFrame,
    valid_df: pd.DataFrame,
    feature_columns: list[str],
    *,
    random_state: int,
    max_auc_gap: float,
    sample_weight: pd.Series | np.ndarray | None = None,
) -> tuple[MfeMaeModel | None, str, dict[str, Any]]:
    trials: list[dict[str, Any]] = []
    for params in _mfe_mae_param_grid():
        model, model_kind = _train_mfe_mae_or_skip(
            train_df,
            feature_columns=feature_columns,
            random_state=random_state,
            model_params=params,
            sample_weight=sample_weight,
        )
        if model is None:
            train_auc = float("nan")
            valid_auc = float("nan")
        else:
            train_exec = train_df.loc[pd.to_numeric(train_df["is_executed"], errors="coerce").fillna(0).astype(int) == 1]
            valid_exec = valid_df.loc[pd.to_numeric(valid_df["is_executed"], errors="coerce").fillna(0).astype(int) == 1]
            if train_exec.empty:
                train_auc = float("nan")
            else:
                train_auc = evaluate_mfe_mae_model(
                    model,
                    train_exec,
                    feature_columns=feature_columns,
                    mfe_column="future_mfe_atr",
                    mae_column="future_mae_atr",
                    mae_penalty=LABEL_MAE_PENALTY,
                    direction_threshold=LABEL_THRESHOLD,
                ).get("direction_auc", float("nan"))
            if valid_exec.empty:
                valid_auc = float("nan")
            else:
                valid_auc = evaluate_mfe_mae_model(
                    model,
                    valid_exec,
                    feature_columns=feature_columns,
                    mfe_column="future_mfe_atr",
                    mae_column="future_mae_atr",
                    mae_penalty=LABEL_MAE_PENALTY,
                    direction_threshold=LABEL_THRESHOLD,
                ).get("direction_auc", float("nan"))
        trials.append(
            {
                "model": model,
                "model_kind": model_kind,
                "params": dict(params),
                "train_auc": train_auc,
                "valid_auc": valid_auc,
            }
        )
    best = _select_best_param_trial(trials, max_auc_gap=max_auc_gap)
    return best.get("model"), str(best.get("model_kind", MFE_MAE_KIND_SKIPPED_NO_EXEC)), best


def _ensure_binary_label_diversity(df: pd.DataFrame) -> pd.DataFrame:
    """Try to keep label_class with at least two classes within an executed subset.

    重算时**只**对 is_executed==1 的成交样本根据 mfe/mae 重新打 label，
    其它行强制保持 label_class=0，避免把 not_triggered/filtered 的样本
    错误识别为正样本污染 trade-filter 训练。
    """
    out = df.copy()
    if "label_class" not in out.columns:
        out["label_class"] = 0
    y = pd.to_numeric(out["label_class"], errors="coerce").fillna(0).astype(int)
    if y.nunique() >= 2:
        out["label_class"] = y
        return out

    mfe = pd.to_numeric(out.get("future_mfe_atr", 0.0), errors="coerce").fillna(0.0)
    mae = pd.to_numeric(out.get("future_mae_atr", 0.0), errors="coerce").fillna(0.0)
    if "is_executed" in out.columns:
        exec_mask = pd.to_numeric(out["is_executed"], errors="coerce").fillna(0).astype(int) == 1
    else:
        exec_mask = pd.Series(True, index=out.index)
    if "atr_warmed" in out.columns:
        warm_mask = pd.to_numeric(out["atr_warmed"], errors="coerce").fillna(1).astype(int) == 1
    else:
        warm_mask = pd.Series(True, index=out.index)
    valid_mask = exec_mask & warm_mask
    new_label = pd.Series(0, index=out.index, dtype=int)
    if valid_mask.any():
        new_label.loc[valid_mask] = (
            (mfe.loc[valid_mask] - LABEL_MAE_PENALTY * mae.loc[valid_mask]) > LABEL_THRESHOLD
        ).astype(int)
    out["label_class"] = new_label
    return out


def _format_ts(v: Any) -> str:
    ts = pd.to_datetime(v, errors="coerce")
    if pd.isna(ts):
        return ""
    return pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _build_split_span(df: pd.DataFrame) -> tuple[str, str]:
    if df.empty or "datetime" not in df.columns:
        return "", ""
    dt = pd.to_datetime(df["datetime"], errors="coerce").dropna()
    if dt.empty:
        return "", ""
    return _format_ts(dt.min()), _format_ts(dt.max())


def _compute_feature_null_stats(
    df: pd.DataFrame,
    feature_columns: Sequence[str],
) -> dict[str, Any]:
    cols = [c for c in feature_columns if c in df.columns]
    if not cols:
        return {
            "feature_count": 0,
            "feature_null_ratio_mean": float("nan"),
            "feature_null_ratio_max": float("nan"),
            "feature_null_feature_count": 0,
            "feature_all_null_count": 0,
        }
    frame = df[cols]
    null_ratio = frame.isna().mean(axis=0)
    return {
        "feature_count": int(len(cols)),
        "feature_null_ratio_mean": float(null_ratio.mean()) if len(null_ratio) else float("nan"),
        "feature_null_ratio_max": float(null_ratio.max()) if len(null_ratio) else float("nan"),
        "feature_null_feature_count": int((null_ratio > 0.0).sum()),
        "feature_all_null_count": int((null_ratio >= 1.0).sum()),
    }


def _compute_feature_ic_stats(
    df: pd.DataFrame,
    feature_columns: Sequence[str],
    target: pd.Series,
    *,
    min_pairs: int = 20,
    max_rows: int = 5000,
) -> dict[str, Any]:
    cols = [c for c in feature_columns if c in df.columns]
    if not cols or df.empty:
        return {
            "ic_abs_mean": float("nan"),
            "ic_abs_median": float("nan"),
            "ic_abs_top": float("nan"),
            "ic_top_feature": "",
        }

    y = pd.to_numeric(target, errors="coerce")
    base = pd.DataFrame({"_target": y}, index=df.index)
    if len(base) > int(max_rows):
        idx = np.linspace(0, len(base) - 1, int(max_rows), dtype=int)
        base = base.iloc[idx].copy()

    values: list[tuple[str, float]] = []
    for col in cols:
        x = pd.to_numeric(df[col], errors="coerce")
        if len(x) != len(df):
            continue
        s = pd.DataFrame({"x": x, "y": base["_target"]}, index=df.index).loc[base.index].dropna()
        if len(s) < int(min_pairs):
            continue
        if s["x"].nunique() < 2 or s["y"].nunique() < 2:
            continue
        ic = s["x"].corr(s["y"], method="spearman")
        if pd.isna(ic):
            continue
        values.append((str(col), float(ic)))

    if not values:
        return {
            "ic_abs_mean": float("nan"),
            "ic_abs_median": float("nan"),
            "ic_abs_top": float("nan"),
            "ic_top_feature": "",
        }

    values = sorted(values, key=lambda x: abs(x[1]), reverse=True)
    abs_vals = np.asarray([abs(v) for _, v in values], dtype=float)
    return {
        "ic_abs_mean": float(np.mean(abs_vals)),
        "ic_abs_median": float(np.median(abs_vals)),
        "ic_abs_top": float(abs_vals[0]),
        "ic_top_feature": str(values[0][0]),
    }


def _time_split(
    df: pd.DataFrame,
    train_end: str,
    valid_end: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    def _purge_by_exit_datetime(part: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
        if part.empty or "exit_datetime" not in part.columns:
            return part
        ex = pd.to_datetime(part["exit_datetime"], errors="coerce")
        # 缺失 exit_datetime 的行无法确认 horizon 是否穿越，保守起见丢弃。
        keep = ex.notna() & (ex <= cutoff)
        return part.loc[keep].copy()

    dt = pd.to_datetime(df["datetime"], errors="coerce")
    train_cut = pd.Timestamp(train_end)
    valid_cut = pd.Timestamp(valid_end)
    train = df.loc[dt <= train_cut].copy()
    valid = df.loc[(dt > train_cut) & (dt <= valid_cut)].copy()
    test = df.loc[dt > valid_cut].copy()
    train = _purge_by_exit_datetime(train, train_cut)
    valid = _purge_by_exit_datetime(valid, valid_cut)

    if train.empty or valid.empty or test.empty:
        n = len(df)
        if n < 6:
            return df.copy(), df.copy(), df.copy()
        i1 = max(1, int(n * 0.6))
        i2 = max(i1 + 1, int(n * 0.8))
        train = df.iloc[:i1].copy()
        valid = df.iloc[i1:i2].copy()
        test = df.iloc[i2:].copy()
        if test.empty:
            test = df.iloc[-max(1, n // 5) :].copy()
        if valid.empty:
            valid = train.copy()
        train_cut_fb = pd.to_datetime(train["datetime"], errors="coerce").max()
        valid_cut_fb = pd.to_datetime(valid["datetime"], errors="coerce").max()
        if pd.notna(train_cut_fb):
            train = _purge_by_exit_datetime(train, pd.Timestamp(train_cut_fb))
        if pd.notna(valid_cut_fb):
            valid = _purge_by_exit_datetime(valid, pd.Timestamp(valid_cut_fb))
    return train, valid, test


def _build_walk_forward_windows(
    df: pd.DataFrame,
    train_end: str,
    valid_end: str,
    max_windows: int,
    window_mode: WindowMode = "expanding",
    rolling_train_years: int = 3,
    rolling_valid_years: int = 1,
    rolling_test_years: int = 1,
    rolling_step_years: int = 1,
) -> list[_WalkForwardWindow]:
    """Build walk-forward windows.

    window_mode:
        - "expanding": train 起点固定为最早样本，每个窗口 train_end 单调右移；
        - "sliding"  : train 长度固定（首窗口 train 长度），整体滑动；
        - "rolling"  : 固定 train/valid/test 年份（默认 3y/1y/1y），按 step 年滚动。
    任意窗口若 train/valid/test 任一段为空则跳过；当数据已超过 valid_end 也提前退出。
    """
    mode = str(window_mode).lower()
    if mode not in {"expanding", "sliding", "rolling"}:
        raise ValueError(f"invalid window_mode={window_mode}, valid=expanding|sliding|rolling")

    dt = pd.to_datetime(df["datetime"], errors="coerce")
    base_train_end = pd.Timestamp(train_end)
    base_valid_end = pd.Timestamp(valid_end)
    if mode == "rolling":
        train_years = max(1, int(rolling_train_years))
        valid_years = max(1, int(rolling_valid_years))
        test_years = max(1, int(rolling_test_years))
        step_years = max(1, int(rolling_step_years))
        step = pd.DateOffset(years=step_years)
    else:
        step = base_valid_end - base_train_end
        if step <= pd.Timedelta(0):
            step = pd.Timedelta(days=180)

    dt_min = dt.min() if not dt.empty else pd.NaT
    dt_max = dt.max() if not dt.empty else pd.NaT
    if mode == "rolling":
        base_train_start = base_train_end - pd.DateOffset(years=max(1, int(rolling_train_years)))
    else:
        base_train_start = dt_min if pd.notna(dt_min) else base_train_end - step

    windows: list[_WalkForwardWindow] = []
    n_windows = max(1, int(max_windows))

    for wid in range(n_windows):
        if mode == "rolling":
            shift = pd.DateOffset(years=wid * step_years)
            w_train_end = base_train_end + shift
            w_train_start = w_train_end - pd.DateOffset(years=train_years)
            w_valid_end = w_train_end + pd.DateOffset(years=valid_years)
            w_test_end = w_valid_end + pd.DateOffset(years=test_years)
            train_mask = (dt > w_train_start) & (dt <= w_train_end)
        elif mode == "sliding":
            w_train_end = base_train_end + wid * step
            w_valid_end = base_valid_end + wid * step
            w_test_end = w_valid_end + step
            w_train_start = base_train_start + wid * step
            train_mask = (dt > w_train_start) & (dt <= w_train_end) if wid > 0 else (dt <= w_train_end)
        else:
            w_train_end = base_train_end + wid * step
            w_valid_end = base_valid_end + wid * step
            w_test_end = w_valid_end + step
            w_train_start = base_train_start
            train_mask = dt <= w_train_end
        train_df = df.loc[train_mask].copy()
        valid_df = df.loc[(dt > w_train_end) & (dt <= w_valid_end)].copy()
        test_df = df.loc[(dt > w_valid_end) & (dt <= w_test_end)].copy()
        if "exit_datetime" in df.columns:
            train_exit = pd.to_datetime(train_df["exit_datetime"], errors="coerce")
            valid_exit = pd.to_datetime(valid_df["exit_datetime"], errors="coerce")
            test_exit = pd.to_datetime(test_df["exit_datetime"], errors="coerce")
            train_df = train_df.loc[train_exit.notna() & (train_exit <= w_train_end)].copy()
            valid_df = valid_df.loc[valid_exit.notna() & (valid_exit <= w_valid_end)].copy()
            test_df = test_df.loc[test_exit.notna() & (test_exit <= w_test_end)].copy()
        if train_df.empty or valid_df.empty or test_df.empty:
            # 数据已经覆盖不到下一个 valid_end / test_end，提前退出
            if pd.notna(dt_max) and dt_max <= w_valid_end:
                break
            continue
        windows.append(
            _WalkForwardWindow(
                window_id=len(windows),
                train=train_df,
                valid=valid_df,
                test=test_df,
                train_end=w_train_end,
                valid_end=w_valid_end,
                test_end=w_test_end,
            )
        )
        if pd.notna(dt_max) and dt_max <= w_test_end:
            break

    if windows:
        return windows

    train_df, valid_df, test_df = _time_split(df, train_end=train_end, valid_end=valid_end)
    return [
        _WalkForwardWindow(
            window_id=0,
            train=train_df,
            valid=valid_df,
            test=test_df,
            train_end=pd.to_datetime(train_df["datetime"], errors="coerce").max(),
            valid_end=pd.to_datetime(valid_df["datetime"], errors="coerce").max(),
            test_end=pd.to_datetime(test_df["datetime"], errors="coerce").max(),
        )
    ]


def _build_pooled_feature_df(
    pool_symbols: Sequence[tuple[str, str | None]],
    *,
    interval: str,
    start_date: str,
    end_date: str,
    trade_side_mode: str,
    synthetic_periods: int,
    feature_root: Path,
    generic_columns: Any,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Thin wrapper for pooled table builder (moved to pipeline_pooling module)."""
    return _build_pooled_feature_df_impl(
        pool_symbols,
        interval=interval,
        start_date=start_date,
        end_date=end_date,
        trade_side_mode=trade_side_mode,
        synthetic_periods=synthetic_periods,
        feature_root=feature_root,
        generic_columns=generic_columns,
        build_candidate_table_fn=_build_candidate_table,
        ensure_training_columns_fn=_ensure_training_columns,
        build_training_feature_table_with_auto_fallback_fn=_build_training_feature_table_with_auto_fallback,
    )


def _build_candidate_table(
    symbol: str,
    exchange: str | None,
    interval: str,
    start_date: str,
    end_date: str,
    trade_side_mode: str,
    synthetic_periods: int,
    allow_synthetic_fallback: bool = True,
) -> tuple[pd.DataFrame, str]:
    sym = str(symbol).upper()
    interval_norm = normalize_interval(interval)
    bcfg = BacktestConfig(interval=interval_norm)
    ex = str(exchange).upper() if exchange else resolve_exchange(sym, bcfg.symbols_list_path)

    try:
        bars = load_bars(sym, bcfg, start_date, end_date, exchange=ex)
        frame = prepare_master_feature_frame(bars, interval=interval_norm)
        parts: list[pd.DataFrame] = []
        for st in BASELINE_SIGNAL_TYPES:
            cand = generate_candidate_opportunities(
                frame=frame,
                symbol=sym,
                exchange=ex,
                interval=interval_norm,
                signal_type=st,
                horizon_bars=20,
                trade_side_mode=trade_side_mode,
            )
            if not cand.empty:
                parts.append(cand)
        if parts:
            out = pd.concat(parts, axis=0, ignore_index=True).sort_values("datetime").reset_index(drop=True)
            return out, ex
        if not allow_synthetic_fallback:
            raise ValueError(
                f"candidate table empty from real data for {sym}.{ex} {interval_norm}, "
                "synthetic fallback disabled"
            )
        logger.warning("candidate table empty from real data, switching to synthetic fallback")
    except Exception as exc:
        if not allow_synthetic_fallback:
            raise
        logger.warning("candidate generation from local data failed, fallback to synthetic: %s", exc)

    synth = _build_synthetic_candidate(
        symbol=sym,
        exchange=ex,
        interval=interval_norm,
        start_date=start_date,
        end_date=end_date,
        periods=synthetic_periods,
    )
    return synth, ex


def _build_last_oot_decile_table(
    prediction_df: pd.DataFrame,
    bins: int = 10,
) -> pd.DataFrame:
    """Build decile return table on the final OOT set.

    OOT 定义：
    1. 优先使用 `pred_split == "test"`；
    2. 若存在 `window_id`，只取最大 window_id（最后一个 walk-forward test 窗口）。

    收益评估口径：
    - 仅对 `is_executed == 1` 的样本计算十档收益（按用户要求）。
    """
    out_cols = [
        "window_id",
        "pred_split",
        "decile",
        "sample_count",
        "score_min",
        "score_max",
        "score_mean",
        "avg_return_atr",
        "median_return_atr",
        "total_return_atr",
        "win_rate",
        "executed_rate",
        "executed_avg_return_atr",
    ]
    if prediction_df.empty or "trade_filter_prob" not in prediction_df.columns:
        return pd.DataFrame(columns=out_cols)

    oot = prediction_df.copy()
    if "pred_split" in oot.columns:
        split = oot["pred_split"].astype(str).str.lower()
        test_mask = split == "test"
        if test_mask.any():
            oot = oot.loc[test_mask].copy()
    if oot.empty:
        return pd.DataFrame(columns=out_cols)

    if "window_id" in oot.columns:
        w = pd.to_numeric(oot["window_id"], errors="coerce")
        if w.notna().any():
            last_wid = int(w.max())
            oot = oot.loc[w == last_wid].copy()
        else:
            last_wid = -1
    else:
        last_wid = -1
    if oot.empty:
        return pd.DataFrame(columns=out_cols)

    score = pd.to_numeric(oot["trade_filter_prob"], errors="coerce")
    oot = oot.loc[score.notna()].copy()
    if oot.empty:
        return pd.DataFrame(columns=out_cols)
    oot["trade_filter_prob"] = score.loc[oot.index]

    # 只看已成交样本的收益分布
    exec_mask = pd.to_numeric(oot.get("is_executed", 0), errors="coerce").fillna(0).astype(int) == 1
    oot = oot.loc[exec_mask].copy()
    if oot.empty:
        return pd.DataFrame(columns=out_cols)

    mfe = pd.to_numeric(oot.get("future_mfe_atr", 0.0), errors="coerce").fillna(0.0)
    mae = pd.to_numeric(oot.get("future_mae_atr", 0.0), errors="coerce").fillna(0.0)
    oot["future_return_atr"] = mfe - LABEL_MAE_PENALTY * mae
    oot["is_executed"] = 1

    n = len(oot)
    n_bins = min(max(1, int(bins)), n)
    rank = oot["trade_filter_prob"].rank(method="first")
    oot["decile"] = pd.qcut(rank, q=n_bins, labels=False, duplicates="drop").astype(int) + 1

    parts: list[dict[str, Any]] = []
    for decile, g in oot.groupby("decile", sort=True):
        ret = pd.to_numeric(g["future_return_atr"], errors="coerce").fillna(0.0)
        parts.append(
            {
                "window_id": last_wid if last_wid >= 0 else np.nan,
                "pred_split": "test",
                "decile": int(decile),
                "sample_count": int(len(g)),
                "score_min": float(g["trade_filter_prob"].min()),
                "score_max": float(g["trade_filter_prob"].max()),
                "score_mean": float(g["trade_filter_prob"].mean()),
                "avg_return_atr": float(ret.mean()),
                "median_return_atr": float(ret.median()),
                "total_return_atr": float(ret.sum()),
                "win_rate": float((ret > 0.0).mean()),
                "executed_rate": 1.0,
                "executed_avg_return_atr": float(ret.mean()),
            }
        )
    out = pd.DataFrame(parts).sort_values("decile").reset_index(drop=True)
    return out[out_cols]


GenericMode = Literal["auto", "whitelist"]


def _resolve_generic_columns(generic_mode: str) -> Iterable[str] | None:
    """Translate ``generic_mode`` to the ``generic_columns`` arg of merge helpers.

    - ``"auto"`` (默认): None → 由 merge_candidate_and_generic_features 自动取
      磁盘 parquet 上所有数值列（排除 OHLCV / 元数据），把 cta/data/feature 里
      预先算好的 ~400 个特征全部带进训练。
    - ``"whitelist"``: 锁定 18 列窄白名单 ``DEFAULT_GENERIC_COLUMNS``，复现
      历史行为或减少特征维度。
    """
    mode = str(generic_mode).strip().lower()
    if mode == "auto":
        return None
    if mode == "whitelist":
        return DEFAULT_GENERIC_COLUMNS
    raise ValueError(f"invalid generic_mode={generic_mode!r}, expected 'auto' or 'whitelist'")


def _regime_to_code(labels: pd.Series) -> pd.Series:
    s = labels.astype(str).str.lower().fillna("range")
    out = pd.Series(0.0, index=labels.index, dtype=float)
    out.loc[s == "trend_up"] = 1.0
    out.loc[s == "trend_down"] = -1.0
    return out


def _build_final_decision_features(
    df: pd.DataFrame,
    *,
    trade_prob: np.ndarray,
    regime_label: Sequence[str],
    pred_mfe: np.ndarray,
    pred_mae: np.ndarray,
    mae_penalty: float,
) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    out["meta_trade_filter_prob"] = pd.to_numeric(pd.Series(trade_prob, index=df.index), errors="coerce").fillna(0.0)
    out["meta_regime_code"] = _regime_to_code(pd.Series(regime_label, index=df.index))
    out["meta_pred_mfe_atr"] = pd.to_numeric(pd.Series(pred_mfe, index=df.index), errors="coerce").fillna(0.0)
    out["meta_pred_mae_atr"] = pd.to_numeric(pd.Series(pred_mae, index=df.index), errors="coerce").fillna(0.0)
    out["meta_pred_edge_atr"] = out["meta_pred_mfe_atr"] - float(mae_penalty) * out["meta_pred_mae_atr"]
    side_raw = df.get("side", pd.Series([""] * len(df), index=df.index))
    side_code = np.where(side_raw.astype(str).str.lower() == "long", 1.0, -1.0)
    out["meta_side_code"] = pd.Series(side_code, index=df.index, dtype=float)
    out["meta_edge_side"] = out["meta_pred_edge_atr"] * out["meta_side_code"]
    return out


def _sha256_of_file(path: Path) -> str:
    if not Path(path).exists():
        return ""
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while True:
            buf = f.read(1024 * 1024)
            if not buf:
                break
            h.update(buf)
    return f"sha256:{h.hexdigest()}"


def _sha256_of_text(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _git_commit_short() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        return ""
    return ""


def _write_provenance(
    *,
    out_dir: Path,
    run_tag: str,
    candidate_path: Path,
    feature_table_path: Path,
    prediction_path: Path,
    metrics_path: Path,
    top_feature_importance_path: Path,
) -> Path:
    payload = {
        "run_tag": str(run_tag),
        "generated_at": pd.Timestamp.now().isoformat(),
        "python_version": sys.version.replace("\n", " "),
        "platform": platform.platform(),
        "git_commit": _git_commit_short(),
        "candidate_path": str(candidate_path),
        "feature_table_path": str(feature_table_path),
        "prediction_path": str(prediction_path),
        "metrics_path": str(metrics_path),
        "top_feature_importance_path": str(top_feature_importance_path),
        "data_snapshot_hash": _sha256_of_file(candidate_path),
        "feature_manifest_hash": _sha256_of_file(feature_table_path),
        "prediction_hash": _sha256_of_file(prediction_path),
        "metrics_hash": _sha256_of_file(metrics_path),
    }
    # 额外记录特征文档哈希，便于比对“同数据不同特征含义文档”。
    try:
        feat_doc_text = FEATURES_DOC_PATH.read_text(encoding="utf-8-sig")
    except Exception:
        feat_doc_text = ""
    payload["features_doc_hash"] = _sha256_of_text(feat_doc_text)

    path = Path(out_dir) / "provenance.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _final_model_importance_df(
    model: FinalDecisionModel,
    feature_columns: Sequence[str],
    *,
    top_k: int,
) -> pd.DataFrame:
    feats = [str(c) for c in feature_columns]
    if not feats or int(top_k) <= 0:
        return pd.DataFrame(columns=["feature", "importance"])
    imp = np.zeros(len(feats), dtype=float)
    est = model.estimator
    if est is not None and hasattr(est, "named_steps"):
        clf = est.named_steps.get("model")
        if clf is not None and hasattr(clf, "coef_"):
            coef = np.asarray(getattr(clf, "coef_"), dtype=float)
            if coef.ndim == 2:
                coef = np.mean(np.abs(coef), axis=0)
            else:
                coef = np.abs(coef)
            imp = np.asarray(coef, dtype=float).reshape(-1)
    if imp.shape[0] != len(feats):
        imp = np.resize(imp, len(feats))
    out = pd.DataFrame({"feature": feats, "importance": np.nan_to_num(imp, nan=0.0, posinf=0.0, neginf=0.0)})
    out = out.sort_values(["importance", "feature"], ascending=[False, True]).head(int(top_k)).reset_index(drop=True)
    return out


def run_model_pipeline(
    symbol: str = "RB0",
    exchange: str | None = "SHFE",
    interval: str = "60min",
    start_date: str = "2000-01-01",
    end_date: str = "2019-12-31",
    trade_side_mode: str = "both",
    train_end: str = "2018-12-31",
    valid_end: str = "2019-06-30",
    output_root: Path | None = None,
    feature_root: Path = FEATURE_ROOT,
    synthetic_periods: int = 400,
    by_signal_type: bool = True,
    max_walk_forward_windows: int = 3,
    window_mode: WindowMode = "expanding",
    rolling_train_years: int = 3,
    rolling_valid_years: int = 1,
    rolling_test_years: int = 1,
    rolling_step_years: int = 1,
    max_auc_gap: float = 0.03,
    max_valid_test_gap: float = 0.10,
    top_feature_importance_alert_pct: float = 0.50,
    generic_mode: GenericMode = "auto",
    oot_eval_config: OotEvaluationConfig = DEFAULT_OOT_EVAL_CONFIG,
    pool_symbols: Sequence[tuple[str, str | None]] | None = None,
    pool_name: str | None = None,
    min_used_symbols: int = 2,
    seed: int = 2026,
) -> ModelPipelineResult:
    """Run full candidate->feature->model pipeline.

    G1: ``generic_mode`` 控制 generic 特征拼接策略：
      - ``"auto"`` (默认): 拼上磁盘 parquet 上**所有数值列**（~400 个特征）
      - ``"whitelist"``: 仅 18 列 ``DEFAULT_GENERIC_COLUMNS`` 历史白名单

    Pool mode：传入 ``pool_symbols=[(sym, ex), ...]`` 时，把多 symbol 的样本拼成
    一个共享训练集训出**单个跨品种模型**（输出目录用 ``POOL`` 替代单品种代码）。
    适合 day 等单品种样本不足的 interval。``symbol`` / ``exchange`` 参数在 pool
    模式下被忽略。

    过拟合控制：
    - 三类模型都做参数网格搜索；
    - 选参仅基于 train/valid AUC，并约束 ``abs(train-valid) <= max_auc_gap``；
    - OOT(test) 严格不参与选参，只用于最终效果评估。
    """
    run_date = pd.Timestamp.now().strftime("%Y%m%d")
    rng_seed = int(seed_all(seed))
    is_pool = bool(pool_symbols)
    if is_pool:
        pool_label = str(pool_name).strip().upper() if pool_name else "POOL"
        sym = pool_label or "POOL"
    else:
        sym = str(symbol).upper()
    interval_norm = normalize_interval(interval)
    root = output_root or DEFAULT_REPORT_ROOT
    out_dir = root / f"{run_date}_{sym}_{interval_norm}_{trade_side_mode}_model_pipeline"
    out_dir.mkdir(parents=True, exist_ok=True)
    process_steps: list[str] = []
    pool_meta_path: Path | None = None
    process_steps.append("1) 生成候选样本（candidate events）")

    if is_pool:
        # M2：即使调用方显式传 pool_symbols，仍要按 manifest 做最终过滤，
        # 防止"绕过 ranking 加载但忘了同步 manifest"的不一致路径。
        from cta.config.symbol_disable import filter_out_disabled_pairs

        pool_symbols_filtered = filter_out_disabled_pairs(list(pool_symbols))
        candidate_df, feature_df = _build_pooled_feature_df(
            pool_symbols=pool_symbols_filtered,
            interval=interval_norm,
            start_date=start_date,
            end_date=end_date,
            trade_side_mode=trade_side_mode,
            synthetic_periods=synthetic_periods,
            feature_root=feature_root,
            generic_columns=_resolve_generic_columns(generic_mode),
        )
        if feature_df.empty:
            raise ValueError(
                "POOL mode produced empty real-data feature table. "
                "No pool symbol has usable local bars/features under current interval."
            )
        ex = ""
        # 落盘 pool 包含哪些 symbol，便于复现；以 pool_members.csv 替代 candidates.csv
        used_symbols = set(feature_df.get("symbol", pd.Series(dtype=str)).astype(str).str.upper().tolist())
        pool_meta = pd.DataFrame(
            [
                {"symbol": s, "exchange": e or "", "used_in_training": int(str(s).upper() in used_symbols)}
                for s, e in pool_symbols_filtered
            ]
        )
        used_count = int(pool_meta["used_in_training"].sum()) if not pool_meta.empty else 0
        logger.info(
            "POOL mode real-data symbols used: %d/%d (after symbol_disable_manifest filter)",
            used_count, len(pool_symbols_filtered or []),
        )
        pool_meta_path = out_dir / f"{run_date}_{sym}_{interval_norm}_{trade_side_mode}_pool_members.csv"
        pool_meta.to_csv(pool_meta_path, index=False, encoding="utf-8-sig")
        min_required = max(1, int(min_used_symbols))
        if used_count < min_required:
            raise ValueError(
                "POOL mode used symbol coverage too low: "
                f"used={used_count}, required>={min_required}, pool_members_csv={pool_meta_path}"
            )
        candidate_path = out_dir / f"{run_date}_{sym}_{interval_norm}_{trade_side_mode}_candidates.csv"
        candidate_df.to_csv(candidate_path, index=False, encoding="utf-8-sig")
    else:
        candidate_df, ex = _build_candidate_table(
            symbol=sym,
            exchange=exchange,
            interval=interval_norm,
            start_date=start_date,
            end_date=end_date,
            trade_side_mode=trade_side_mode,
            synthetic_periods=synthetic_periods,
        )
        candidate_df = _ensure_training_columns(candidate_df)
        candidate_path = out_dir / f"{run_date}_{sym}_{interval_norm}_{trade_side_mode}_candidates.csv"
        candidate_df.to_csv(candidate_path, index=False, encoding="utf-8-sig")

    cand_start, cand_end = _build_split_span(candidate_df)
    process_steps.append(
        f"- candidate_count={len(candidate_df)}, period={cand_start or 'NA'} -> {cand_end or 'NA'}, file={candidate_path.name}"
    )
    process_steps.append("2) 拼接候选样本对应的通用特征 + 模型训练特征")

    if is_pool:
        # pool 分支的 feature_df 已在 _build_pooled_feature_df 中按每个 symbol 完成拼接。
        feature_df = _ensure_training_columns(feature_df)
    else:
        feature_df = _build_training_feature_table_with_auto_fallback(
            candidate_df=candidate_df,
            symbol=sym,
            interval=interval_norm,
            feature_root=feature_root,
            # G1: 透传 generic 拼接策略；默认 "auto" 把所有 generic 数值列带进训练。
            generic_columns=_resolve_generic_columns(generic_mode),
        )
        # B10 note: _ensure_training_columns 是幂等的；这里第二次调用是为了把
        # generic 特征 merge 后可能出现的 dtype/NaN 漂移再统一收敛一次，确保
        # 后续 walk-forward 看到的 schema 与 candidate_df 完全一致。
        feature_df = _ensure_training_columns(feature_df)

    # 丢弃 ATR warmup 期样本（atr14 不足导致 mfe/mae 归一化不可靠）。
    warmed_mask = feature_df["atr_warmed"].astype(int) == 1
    n_total = int(len(feature_df))
    n_dropped = int((~warmed_mask).sum())
    if n_dropped > 0:
        logger.warning(
            "drop ATR warmup samples: dropped=%s total=%s ratio=%.4f",
            n_dropped,
            n_total,
            (n_dropped / n_total) if n_total else 0.0,
        )
    feature_df = feature_df.loc[warmed_mask].reset_index(drop=True)

    feature_table_path = out_dir / f"{run_date}_{sym}_{interval_norm}_{trade_side_mode}_feature_table.csv"
    feature_df.to_csv(feature_table_path, index=False, encoding="utf-8-sig")
    model_feature_columns = [c for c in feature_df.columns if c.startswith("feature_") or c.startswith("generic_")]
    all_feat_null_stats = _compute_feature_null_stats(feature_df, model_feature_columns)
    feat_start, feat_end = _build_split_span(feature_df)
    process_steps.append(
        (
            f"- feature_sample_count={len(feature_df)}, period={feat_start or 'NA'} -> {feat_end or 'NA'}, "
            f"feature_cols={all_feat_null_stats['feature_count']}, "
            f"null_ratio_mean={_safe_float(all_feat_null_stats['feature_null_ratio_mean']):.6f}, "
            f"null_ratio_max={_safe_float(all_feat_null_stats['feature_null_ratio_max']):.6f}, "
            f"file={feature_table_path.name}"
        )
    )
    process_steps.append("3) 按 signal_type / walk-forward 训练三类模型并做参数搜索（仅 train/valid）")

    if by_signal_type:
        signal_frames = [(str(st), g.copy()) for st, g in feature_df.groupby("signal_type") if not g.empty]
    else:
        signal_frames = [("all_signal_types", feature_df.copy())]

    metrics_parts: list[pd.DataFrame] = []
    prediction_parts: list[pd.DataFrame] = []
    top_feature_parts: list[pd.DataFrame] = []
    model_root = out_dir / "models"

    for signal_type_key, sig_df in signal_frames:
        sig_df = sig_df.sort_values("datetime").reset_index(drop=True)
        sig_df, feature_columns = _select_feature_columns(sig_df)
        if by_signal_type and "feature_signal_code" in feature_columns:
            feature_columns = [c for c in feature_columns if c != "feature_signal_code"]
            if not feature_columns:
                sig_df["feature_fallback"] = 0.0
                feature_columns = ["feature_fallback"]
        trade_feature_columns = _filter_model_leakage_features(
            feature_columns,
            model_name="trade_filter",
        )
        regime_feature_columns = _filter_model_leakage_features(
            feature_columns,
            model_name="regime_classifier",
        )
        mfe_feature_columns = _filter_model_leakage_features(
            feature_columns,
            model_name="mfe_mae",
        )
        if not trade_feature_columns:
            sig_df["feature_fallback"] = 0.0
            trade_feature_columns = ["feature_fallback"]
        if not regime_feature_columns:
            sig_df["feature_fallback"] = 0.0
            regime_feature_columns = ["feature_fallback"]
        if not mfe_feature_columns:
            sig_df["feature_fallback"] = 0.0
            mfe_feature_columns = ["feature_fallback"]
        windows = _build_walk_forward_windows(
            sig_df,
            train_end=train_end,
            valid_end=valid_end,
            max_windows=max_walk_forward_windows,
            window_mode=window_mode,
            rolling_train_years=rolling_train_years,
            rolling_valid_years=rolling_valid_years,
            rolling_test_years=rolling_test_years,
            rolling_step_years=rolling_step_years,
        )
        if int(max_walk_forward_windows) > 1 and len(windows) <= 1:
            logger.warning(
                "walk-forward produced only %d window for signal=%s (requested=%d). "
                "Consider shrinking train/valid span to increase OOT windows.",
                len(windows),
                signal_type_key,
                int(max_walk_forward_windows),
            )

        for win in windows:
            train_df = _ensure_binary_label_diversity(win.train)
            valid_df = win.valid
            test_df = win.test
            if train_df.empty:
                continue

            train_exec_mask = pd.to_numeric(train_df.get("is_executed", 0), errors="coerce").fillna(0).astype(int) == 1
            train_executed_count = int(train_exec_mask.sum())
            train_non_executed_count = int(len(train_df) - train_executed_count)
            # 显式保留未成交样本参与 trade_filter / regime 训练（仅 MFE/MAE 训练用 executed 子集）
            logger.debug(
                "train mix signal=%s window=%s total=%s executed=%s non_executed=%s",
                signal_type_key,
                win.window_id,
                len(train_df),
                train_executed_count,
                train_non_executed_count,
            )
            cluster_sample_weight = _build_symbol_cluster_sample_weight(train_df)

            trade_model, trade_selected = _tune_trade_filter_model(
                train_df,
                valid_df,
                feature_columns=trade_feature_columns,
                random_state=rng_seed,
                max_auc_gap=max_auc_gap,
                sample_weight=cluster_sample_weight,
            )
            regime_model, regime_selected = _tune_regime_classifier_model(
                train_df,
                valid_df,
                feature_columns=regime_feature_columns,
                random_state=rng_seed,
                max_auc_gap=max_auc_gap,
                sample_weight=cluster_sample_weight,
            )
            mfe_mae_model, mfe_mae_kind, mfe_selected = _tune_mfe_mae_model(
                train_df,
                valid_df,
                feature_columns=mfe_feature_columns,
                random_state=rng_seed,
                max_auc_gap=max_auc_gap,
                sample_weight=cluster_sample_weight,
            )
            # P1.3: 三段模型输出作为 meta 特征，再训最终决策模型。
            train_trade_prob = trade_model.predict_proba(train_df, feature_columns=trade_feature_columns)
            valid_trade_prob = trade_model.predict_proba(valid_df, feature_columns=trade_feature_columns)
            test_trade_prob = trade_model.predict_proba(test_df, feature_columns=trade_feature_columns)
            train_regime_pred = regime_model.predict(train_df, feature_columns=regime_feature_columns)
            valid_regime_pred = regime_model.predict(valid_df, feature_columns=regime_feature_columns)
            test_regime_pred = regime_model.predict(test_df, feature_columns=regime_feature_columns)
            if mfe_mae_model is None:
                train_pred_mfe = np.zeros(len(train_df), dtype=float)
                train_pred_mae = np.zeros(len(train_df), dtype=float)
                valid_pred_mfe = np.zeros(len(valid_df), dtype=float)
                valid_pred_mae = np.zeros(len(valid_df), dtype=float)
                test_pred_mfe = np.zeros(len(test_df), dtype=float)
                test_pred_mae = np.zeros(len(test_df), dtype=float)
            else:
                p_train = mfe_mae_model.predict(train_df, feature_columns=mfe_feature_columns)
                p_valid = mfe_mae_model.predict(valid_df, feature_columns=mfe_feature_columns)
                p_test = mfe_mae_model.predict(test_df, feature_columns=mfe_feature_columns)
                train_pred_mfe = pd.to_numeric(p_train["pred_mfe_atr"], errors="coerce").fillna(0.0).to_numpy()
                train_pred_mae = pd.to_numeric(p_train["pred_mae_atr"], errors="coerce").fillna(0.0).to_numpy()
                valid_pred_mfe = pd.to_numeric(p_valid["pred_mfe_atr"], errors="coerce").fillna(0.0).to_numpy()
                valid_pred_mae = pd.to_numeric(p_valid["pred_mae_atr"], errors="coerce").fillna(0.0).to_numpy()
                test_pred_mfe = pd.to_numeric(p_test["pred_mfe_atr"], errors="coerce").fillna(0.0).to_numpy()
                test_pred_mae = pd.to_numeric(p_test["pred_mae_atr"], errors="coerce").fillna(0.0).to_numpy()

            meta_train = _build_final_decision_features(
                train_df,
                trade_prob=train_trade_prob,
                regime_label=train_regime_pred,
                pred_mfe=train_pred_mfe,
                pred_mae=train_pred_mae,
                mae_penalty=LABEL_MAE_PENALTY,
            )
            meta_valid = _build_final_decision_features(
                valid_df,
                trade_prob=valid_trade_prob,
                regime_label=valid_regime_pred,
                pred_mfe=valid_pred_mfe,
                pred_mae=valid_pred_mae,
                mae_penalty=LABEL_MAE_PENALTY,
            )
            meta_test = _build_final_decision_features(
                test_df,
                trade_prob=test_trade_prob,
                regime_label=test_regime_pred,
                pred_mfe=test_pred_mfe,
                pred_mae=test_pred_mae,
                mae_penalty=LABEL_MAE_PENALTY,
            )
            final_feature_columns = list(meta_train.columns)
            final_train_df = train_df.copy()
            final_valid_df = valid_df.copy()
            final_test_df = test_df.copy()
            for c in final_feature_columns:
                final_train_df[c] = meta_train[c].to_numpy()
                final_valid_df[c] = meta_valid[c].to_numpy()
                final_test_df[c] = meta_test[c].to_numpy()
            # P1.4：默认走 ensemble (ElasticNet + HistGBT)，捕捉 meta-feature 非线性交互。
            final_model = FinalDecisionModel(random_state=rng_seed).fit(
                final_train_df,
                feature_columns=final_feature_columns,
                label_column="label_class",
                sample_weight=cluster_sample_weight,
                ensemble=True,
            )
            final_train_eval = evaluate_final_decision_model(
                final_model,
                final_train_df,
                feature_columns=final_feature_columns,
                label_column="label_class",
            )
            final_valid_eval = evaluate_final_decision_model(
                final_model,
                final_valid_df,
                feature_columns=final_feature_columns,
                label_column="label_class",
            )
            final_selected = {
                "params": {"meta_features": final_feature_columns},
                "train_auc": _safe_float(final_train_eval.get("auc")),
                "valid_auc": _safe_float(final_valid_eval.get("auc")),
                "auc_gap": abs(
                    _safe_float(final_train_eval.get("auc")) - _safe_float(final_valid_eval.get("auc"))
                ),
            }
            logger.info(
                "param-search selected | signal=%s window=%s | trade(valid_auc=%.4f gap=%.4f) regime(valid_auc=%.4f gap=%.4f) mfe(valid_auc=%.4f gap=%.4f) final(valid_auc=%.4f gap=%.4f)",
                signal_type_key,
                win.window_id,
                _safe_float(trade_selected.get("valid_auc")),
                _safe_float(trade_selected.get("auc_gap")),
                _safe_float(regime_selected.get("valid_auc")),
                _safe_float(regime_selected.get("auc_gap")),
                _safe_float(mfe_selected.get("valid_auc")),
                _safe_float(mfe_selected.get("auc_gap")),
                _safe_float(final_selected.get("valid_auc")),
                _safe_float(final_selected.get("auc_gap")),
            )
            trade_params_json = json.dumps(trade_selected.get("params", {}), ensure_ascii=False, sort_keys=True)
            regime_params_json = json.dumps(regime_selected.get("params", {}), ensure_ascii=False, sort_keys=True)
            mfe_params_json = json.dumps(mfe_selected.get("params", {}), ensure_ascii=False, sort_keys=True)
            final_params_json = json.dumps(final_selected.get("params", {}), ensure_ascii=False, sort_keys=True)

            trade_top = _tag_top_feature_importance(
                trade_model.get_top_feature_importance(
                    train_df,
                    feature_columns=trade_feature_columns,
                    label_column="label_class",
                    top_k=10,
                ),
                signal_type=signal_type_key,
                window_id=win.window_id,
                model="trade_filter",
                model_kind=trade_model.model_kind,
            )
            _log_top_feature_importance(trade_top)
            if not trade_top.empty:
                top_feature_parts.append(trade_top)

            regime_top = _tag_top_feature_importance(
                regime_model.get_top_feature_importance(
                    feature_columns=regime_feature_columns,
                    top_k=10,
                ),
                signal_type=signal_type_key,
                window_id=win.window_id,
                model="regime_classifier",
                model_kind=regime_model.model_kind,
            )
            _log_top_feature_importance(regime_top)
            if not regime_top.empty:
                top_feature_parts.append(regime_top)

            if mfe_mae_model is None:
                mfe_base = pd.DataFrame(
                    {
                        "feature": mfe_feature_columns[:10],
                        "importance": [0.0] * min(10, len(mfe_feature_columns)),
                    }
                )
            else:
                mfe_base = mfe_mae_model.get_top_feature_importance(
                    feature_columns=mfe_feature_columns,
                    top_k=10,
                )
            mfe_top = _tag_top_feature_importance(
                mfe_base,
                signal_type=signal_type_key,
                window_id=win.window_id,
                model="mfe_mae",
                model_kind=mfe_mae_kind,
            )
            _log_top_feature_importance(mfe_top)
            if not mfe_top.empty:
                top_feature_parts.append(mfe_top)

            final_base = _final_model_importance_df(
                final_model,
                feature_columns=final_feature_columns,
                top_k=10,
            )
            final_top = _tag_top_feature_importance(
                final_base,
                signal_type=signal_type_key,
                window_id=win.window_id,
                model="final_decision_stack",
                model_kind=final_model.model_kind,
            )
            _log_top_feature_importance(final_top)
            if not final_top.empty:
                top_feature_parts.append(final_top)

            signal_dir = model_root / _safe_name(signal_type_key) / f"window_{win.window_id:02d}"
            trade_joblib = signal_dir / "trade_filter.joblib"
            regime_joblib = signal_dir / "regime_classifier.joblib"
            mfe_mae_joblib = signal_dir / "mfe_mae.joblib"
            final_joblib = signal_dir / "final_decision_stack.joblib"
            trade_cal_joblib = signal_dir / "trade_filter_calibration.joblib"
            regime_cal_joblib = signal_dir / "regime_classifier_calibration.joblib"
            mfe_mae_cal_joblib = signal_dir / "mfe_mae_calibration.joblib"
            final_cal_joblib = signal_dir / "final_decision_stack_calibration.joblib"

            cal_ref_df = valid_df.copy() if not valid_df.empty else train_df.copy()
            cal_cluster = (
                cal_ref_df.get("cluster_name")
                if "cluster_name" in cal_ref_df.columns
                else cal_ref_df.get("cluster")
            )
            if cal_cluster is None:
                cal_cluster = cal_ref_df.get(
                    "symbol",
                    pd.Series([""] * len(cal_ref_df), index=cal_ref_df.index),
                ).astype(str).map(infer_symbol_cluster)
            else:
                cal_cluster = cal_cluster.astype(str)
            cal_interval = cal_ref_df.get(
                "interval",
                pd.Series([interval] * len(cal_ref_df), index=cal_ref_df.index),
            ).astype(str)
            cal_datetime = pd.to_datetime(
                cal_ref_df.get("datetime", pd.Series([pd.NaT] * len(cal_ref_df), index=cal_ref_df.index)),
                errors="coerce",
            )
            trade_prob_ref = (
                np.asarray(valid_trade_prob, dtype=float).reshape(-1)
                if len(valid_trade_prob) == len(cal_ref_df)
                else np.asarray(
                    trade_model.predict_proba(cal_ref_df, feature_columns=trade_feature_columns),
                    dtype=float,
                ).reshape(-1)
            )
            cal_trade = pd.DataFrame(
                {
                    "cluster_name": np.asarray(cal_cluster, dtype=object),
                    "interval": np.asarray(cal_interval, dtype=object),
                    "datetime": np.asarray(cal_datetime, dtype="datetime64[ns]"),
                    "trade_filter_prob": trade_prob_ref,
                }
            )
            trade_calibrator = ScoreCalibrator.fit_for_holdout(
                cal_trade,
                score_column="trade_filter_prob",
                model_kind="trade_filter",
            )
            if trade_calibrator.calibrations:
                trade_calibrator.save(trade_cal_joblib)

            regime_probs = regime_model.predict_proba(cal_ref_df, feature_columns=regime_feature_columns)
            if regime_probs.ndim == 2 and regime_probs.shape[0] == len(cal_ref_df):
                regime_score = np.nanmax(np.asarray(regime_probs, dtype=float), axis=1)
                cal_regime = pd.DataFrame(
                    {
                        "cluster_name": cal_cluster,
                        "interval": cal_interval,
                        "datetime": cal_datetime,
                        "regime_confidence": regime_score,
                    }
                )
                regime_calibrator = ScoreCalibrator.fit_for_holdout(
                    cal_regime,
                    score_column="regime_confidence",
                    model_kind="regime_classifier",
                )
                if regime_calibrator.calibrations:
                    regime_calibrator.save(regime_cal_joblib)

            if mfe_mae_model is not None:
                mfe_pred_ref = mfe_mae_model.predict(cal_ref_df, feature_columns=mfe_feature_columns)
                edge_ref = pd.to_numeric(mfe_pred_ref["pred_mfe_atr"], errors="coerce").fillna(0.0) - float(
                    LABEL_MAE_PENALTY
                ) * pd.to_numeric(mfe_pred_ref["pred_mae_atr"], errors="coerce").fillna(0.0)
                cal_mfe = pd.DataFrame(
                    {
                        "cluster_name": cal_cluster,
                        "interval": cal_interval,
                        "datetime": cal_datetime,
                        "pred_edge_atr": edge_ref.to_numpy(dtype=float),
                    }
                )
                mfe_calibrator = ScoreCalibrator.fit_for_holdout(
                    cal_mfe,
                    score_column="pred_edge_atr",
                    model_kind="mfe_mae_edge",
                )
                if mfe_calibrator.calibrations:
                    mfe_calibrator.save(mfe_mae_cal_joblib)

            final_ref_df = final_valid_df.copy() if not final_valid_df.empty else final_train_df.copy()
            final_cluster = (
                final_ref_df.get("cluster_name")
                if "cluster_name" in final_ref_df.columns
                else final_ref_df.get("cluster")
            )
            if final_cluster is None:
                final_cluster = final_ref_df.get(
                    "symbol",
                    pd.Series([""] * len(final_ref_df), index=final_ref_df.index),
                ).astype(str).map(infer_symbol_cluster)
            else:
                final_cluster = final_cluster.astype(str)
            final_interval = final_ref_df.get(
                "interval",
                pd.Series([interval] * len(final_ref_df), index=final_ref_df.index),
            ).astype(str)
            final_datetime = pd.to_datetime(
                final_ref_df.get("datetime", pd.Series([pd.NaT] * len(final_ref_df), index=final_ref_df.index)),
                errors="coerce",
            )
            final_prob_ref = final_model.predict_proba(final_ref_df, feature_columns=final_feature_columns)
            cal_final = pd.DataFrame(
                {
                    "cluster_name": np.asarray(final_cluster, dtype=object),
                    "interval": np.asarray(final_interval, dtype=object),
                    "datetime": np.asarray(final_datetime, dtype="datetime64[ns]"),
                    "final_decision_score": np.asarray(final_prob_ref, dtype=float).reshape(-1),
                }
            )
            final_calibrator = ScoreCalibrator.fit_for_holdout(
                cal_final,
                score_column="final_decision_score",
                model_kind="final_decision_stack",
            )
            if final_calibrator.calibrations:
                final_calibrator.save(final_cal_joblib)

            trade_model.save(trade_joblib)
            regime_model.save(regime_joblib)
            if mfe_mae_model is not None:
                mfe_mae_model.save(mfe_mae_joblib)
            final_model.save(final_joblib)

            # F1: 模型部署需要每个 joblib 旁边配一份全量特征清单（按 importance
            # 降序），下游可据此严格做 schema 校验。get_top_feature_importance
            # 传 top_k=len(feature_columns) 即返回全集。
            n_feat_trade = len(trade_feature_columns)
            try:
                trade_full = trade_model.get_top_feature_importance(
                    train_df,
                    feature_columns=trade_feature_columns,
                    label_column="label_class",
                    top_k=n_feat_trade,
                )
            except Exception as exc:
                logger.warning("trade_filter full importance failed: %s", exc)
                trade_full = None
            _dump_feature_manifest(
                joblib_path=trade_joblib,
                feature_columns=trade_feature_columns,
                importance_df=trade_full,
                model_kind=trade_model.model_kind,
            )

            n_feat_regime = len(regime_feature_columns)
            try:
                regime_full = regime_model.get_top_feature_importance(
                    feature_columns=regime_feature_columns,
                    top_k=n_feat_regime,
                )
            except Exception as exc:
                logger.warning("regime_classifier full importance failed: %s", exc)
                regime_full = None
            _dump_feature_manifest(
                joblib_path=regime_joblib,
                feature_columns=regime_feature_columns,
                importance_df=regime_full,
                model_kind=regime_model.model_kind,
            )

            if mfe_mae_model is not None:
                n_feat_mfe = len(mfe_feature_columns)
                try:
                    mfe_full = mfe_mae_model.get_top_feature_importance(
                        feature_columns=mfe_feature_columns,
                        top_k=n_feat_mfe,
                    )
                except Exception as exc:
                    logger.warning("mfe_mae full importance failed: %s", exc)
                    mfe_full = None
                _dump_feature_manifest(
                    joblib_path=mfe_mae_joblib,
                    feature_columns=mfe_feature_columns,
                    importance_df=mfe_full,
                    model_kind=mfe_mae_kind,
                )

            n_feat_final = len(final_feature_columns)
            final_full = _final_model_importance_df(
                final_model,
                feature_columns=final_feature_columns,
                top_k=n_feat_final,
            )
            _dump_feature_manifest(
                joblib_path=final_joblib,
                feature_columns=final_feature_columns,
                importance_df=final_full,
                model_kind=final_model.model_kind,
            )

            # Calibration 也落 feature manifest，确保部署侧对所有 *.joblib
            # 都能做统一 schema 校验（含 *_calibration.joblib）。
            if trade_cal_joblib.exists():
                _dump_feature_manifest(
                    joblib_path=trade_cal_joblib,
                    feature_columns=["trade_filter_prob"],
                    importance_df=None,
                    model_kind="score_calibration",
                )
            if regime_cal_joblib.exists():
                _dump_feature_manifest(
                    joblib_path=regime_cal_joblib,
                    feature_columns=["regime_confidence"],
                    importance_df=None,
                    model_kind="score_calibration",
                )
            if mfe_mae_cal_joblib.exists():
                _dump_feature_manifest(
                    joblib_path=mfe_mae_cal_joblib,
                    feature_columns=["pred_edge_atr"],
                    importance_df=None,
                    model_kind="score_calibration",
                )
            if final_cal_joblib.exists():
                _dump_feature_manifest(
                    joblib_path=final_cal_joblib,
                    feature_columns=["final_decision_score"],
                    importance_df=None,
                    model_kind="score_calibration",
                )

            for split_name, split_df in (("train", train_df), ("valid", valid_df), ("test", test_df)):
                if split_df.empty:
                    continue
                split_start, split_end = _build_split_span(split_df)
                split_exec_mask = pd.to_numeric(split_df.get("is_executed", 0), errors="coerce").fillna(0).astype(int) == 1
                split_executed_count = int(split_exec_mask.sum())
                split_non_executed_count = int(len(split_df) - split_executed_count)
                split_null_stats = _compute_feature_null_stats(split_df, feature_columns)
                label_target = pd.to_numeric(split_df.get("label_class", 0), errors="coerce")
                label_ic = _compute_feature_ic_stats(
                    split_df,
                    feature_columns=feature_columns,
                    target=label_target,
                )
                if "regime_label" in split_df.columns:
                    regime_target = pd.Series(
                        pd.Categorical(split_df["regime_label"].astype(str)).codes.astype(float),
                        index=split_df.index,
                    )
                else:
                    regime_target = pd.Series([np.nan] * len(split_df), index=split_df.index, dtype=float)
                regime_ic = _compute_feature_ic_stats(
                    split_df,
                    feature_columns=feature_columns,
                    target=regime_target,
                )
                split_exec_df = split_df.loc[split_exec_mask].copy()
                if split_exec_df.empty:
                    return_ic = {
                        "ic_abs_mean": float("nan"),
                        "ic_abs_median": float("nan"),
                        "ic_abs_top": float("nan"),
                        "ic_top_feature": "",
                    }
                else:
                    edge = (
                        pd.to_numeric(split_exec_df.get("future_mfe_atr", 0.0), errors="coerce").fillna(0.0)
                        - LABEL_MAE_PENALTY
                        * pd.to_numeric(split_exec_df.get("future_mae_atr", 0.0), errors="coerce").fillna(0.0)
                    )
                    return_ic = _compute_feature_ic_stats(
                        split_exec_df,
                        feature_columns=feature_columns,
                        target=edge,
                    )
                trade_metrics = evaluate_trade_filter_model(
                    trade_model,
                    split_df,
                    feature_columns=trade_feature_columns,
                    label_column="label_class",
                )
                regime_metrics = evaluate_regime_model(
                    regime_model,
                    split_df,
                    feature_columns=regime_feature_columns,
                    label_column="regime_label",
                )
                nan_mfe_metrics: dict[str, float] = {
                    "direction_auc": float("nan"),
                    "mfe_mae_mae": float("nan"),
                    "mae_mae": float("nan"),
                    "mfe_rmse": float("nan"),
                    "mae_rmse": float("nan"),
                    "mfe_r2": float("nan"),
                    "mae_r2": float("nan"),
                }
                if mfe_mae_model is None:
                    # B1 fix: 训练时无 executed 样本 → 评估直接 NaN，model_kind 写 skipped_no_exec
                    mfe_metrics: dict[str, float] = nan_mfe_metrics
                else:
                    split_exec = split_df.loc[
                        pd.to_numeric(split_df["is_executed"], errors="coerce").fillna(0).astype(int) == 1
                    ]
                    if split_exec.empty:
                        mfe_metrics = nan_mfe_metrics
                    else:
                        mfe_metrics = evaluate_mfe_mae_model(
                        mfe_mae_model,
                        split_exec,
                        feature_columns=mfe_feature_columns,
                        mfe_column="future_mfe_atr",
                        mae_column="future_mae_atr",
                        mae_penalty=LABEL_MAE_PENALTY,
                        direction_threshold=LABEL_THRESHOLD,
                    )
                final_split_df = {
                    "train": final_train_df,
                    "valid": final_valid_df,
                    "test": final_test_df,
                }.get(split_name, final_test_df)
                final_metrics = evaluate_final_decision_model(
                    final_model,
                    final_split_df,
                    feature_columns=final_feature_columns,
                    label_column="label_class",
                )

                metrics_parts.append(
                    pd.DataFrame(
                        [
                            {
                                "signal_type": signal_type_key,
                                "window_id": win.window_id,
                                "split": split_name,
                                "model": "trade_filter",
                                "model_kind": trade_model.model_kind,
                                "train_executed_count": train_executed_count,
                                "train_non_executed_count": train_non_executed_count,
                                "selected_params": trade_params_json,
                                "selection_train_auc": _safe_float(trade_selected.get("train_auc")),
                                "selection_valid_auc": _safe_float(trade_selected.get("valid_auc")),
                                "selection_auc_gap": _safe_float(trade_selected.get("auc_gap")),
                                "split_start": split_start,
                                "split_end": split_end,
                                "split_sample_count": int(len(split_df)),
                                "split_executed_count": split_executed_count,
                                "split_non_executed_count": split_non_executed_count,
                                "feature_count": int(split_null_stats["feature_count"]),
                                "feature_null_ratio_mean": _safe_float(split_null_stats["feature_null_ratio_mean"]),
                                "feature_null_ratio_max": _safe_float(split_null_stats["feature_null_ratio_max"]),
                                "feature_null_feature_count": int(split_null_stats["feature_null_feature_count"]),
                                "feature_all_null_count": int(split_null_stats["feature_all_null_count"]),
                                "label_ic_abs_mean": _safe_float(label_ic["ic_abs_mean"]),
                                "label_ic_abs_top": _safe_float(label_ic["ic_abs_top"]),
                                "label_ic_top_feature": str(label_ic["ic_top_feature"]),
                                "regime_ic_abs_mean": _safe_float(regime_ic["ic_abs_mean"]),
                                "regime_ic_abs_top": _safe_float(regime_ic["ic_abs_top"]),
                                "regime_ic_top_feature": str(regime_ic["ic_top_feature"]),
                                "return_ic_abs_mean": _safe_float(return_ic["ic_abs_mean"]),
                                "return_ic_abs_top": _safe_float(return_ic["ic_abs_top"]),
                                "return_ic_top_feature": str(return_ic["ic_top_feature"]),
                                **trade_metrics,
                            },
                            {
                                "signal_type": signal_type_key,
                                "window_id": win.window_id,
                                "split": split_name,
                                "model": "regime_classifier",
                                "model_kind": regime_model.model_kind,
                                "train_executed_count": train_executed_count,
                                "train_non_executed_count": train_non_executed_count,
                                "selected_params": regime_params_json,
                                "selection_train_auc": _safe_float(regime_selected.get("train_auc")),
                                "selection_valid_auc": _safe_float(regime_selected.get("valid_auc")),
                                "selection_auc_gap": _safe_float(regime_selected.get("auc_gap")),
                                "split_start": split_start,
                                "split_end": split_end,
                                "split_sample_count": int(len(split_df)),
                                "split_executed_count": split_executed_count,
                                "split_non_executed_count": split_non_executed_count,
                                "feature_count": int(split_null_stats["feature_count"]),
                                "feature_null_ratio_mean": _safe_float(split_null_stats["feature_null_ratio_mean"]),
                                "feature_null_ratio_max": _safe_float(split_null_stats["feature_null_ratio_max"]),
                                "feature_null_feature_count": int(split_null_stats["feature_null_feature_count"]),
                                "feature_all_null_count": int(split_null_stats["feature_all_null_count"]),
                                "label_ic_abs_mean": _safe_float(label_ic["ic_abs_mean"]),
                                "label_ic_abs_top": _safe_float(label_ic["ic_abs_top"]),
                                "label_ic_top_feature": str(label_ic["ic_top_feature"]),
                                "regime_ic_abs_mean": _safe_float(regime_ic["ic_abs_mean"]),
                                "regime_ic_abs_top": _safe_float(regime_ic["ic_abs_top"]),
                                "regime_ic_top_feature": str(regime_ic["ic_top_feature"]),
                                "return_ic_abs_mean": _safe_float(return_ic["ic_abs_mean"]),
                                "return_ic_abs_top": _safe_float(return_ic["ic_abs_top"]),
                                "return_ic_top_feature": str(return_ic["ic_top_feature"]),
                                **regime_metrics,
                            },
                            {
                                "signal_type": signal_type_key,
                                "window_id": win.window_id,
                                "split": split_name,
                                "model": "mfe_mae",
                                "model_kind": mfe_mae_kind,
                                "train_executed_count": train_executed_count,
                                "train_non_executed_count": train_non_executed_count,
                                "selected_params": mfe_params_json,
                                "selection_train_auc": _safe_float(mfe_selected.get("train_auc")),
                                "selection_valid_auc": _safe_float(mfe_selected.get("valid_auc")),
                                "selection_auc_gap": _safe_float(mfe_selected.get("auc_gap")),
                                "split_start": split_start,
                                "split_end": split_end,
                                "split_sample_count": int(len(split_df)),
                                "split_executed_count": split_executed_count,
                                "split_non_executed_count": split_non_executed_count,
                                "feature_count": int(split_null_stats["feature_count"]),
                                "feature_null_ratio_mean": _safe_float(split_null_stats["feature_null_ratio_mean"]),
                                "feature_null_ratio_max": _safe_float(split_null_stats["feature_null_ratio_max"]),
                                "feature_null_feature_count": int(split_null_stats["feature_null_feature_count"]),
                                "feature_all_null_count": int(split_null_stats["feature_all_null_count"]),
                                "label_ic_abs_mean": _safe_float(label_ic["ic_abs_mean"]),
                                "label_ic_abs_top": _safe_float(label_ic["ic_abs_top"]),
                                "label_ic_top_feature": str(label_ic["ic_top_feature"]),
                                "regime_ic_abs_mean": _safe_float(regime_ic["ic_abs_mean"]),
                                "regime_ic_abs_top": _safe_float(regime_ic["ic_abs_top"]),
                                "regime_ic_top_feature": str(regime_ic["ic_top_feature"]),
                                "return_ic_abs_mean": _safe_float(return_ic["ic_abs_mean"]),
                                "return_ic_abs_top": _safe_float(return_ic["ic_abs_top"]),
                                "return_ic_top_feature": str(return_ic["ic_top_feature"]),
                                **mfe_metrics,
                            },
                            {
                                "signal_type": signal_type_key,
                                "window_id": win.window_id,
                                "split": split_name,
                                "model": "final_decision_stack",
                                "model_kind": final_model.model_kind,
                                "train_executed_count": train_executed_count,
                                "train_non_executed_count": train_non_executed_count,
                                "selected_params": final_params_json,
                                "selection_train_auc": _safe_float(final_selected.get("train_auc")),
                                "selection_valid_auc": _safe_float(final_selected.get("valid_auc")),
                                "selection_auc_gap": _safe_float(final_selected.get("auc_gap")),
                                "split_start": split_start,
                                "split_end": split_end,
                                "split_sample_count": int(len(split_df)),
                                "split_executed_count": split_executed_count,
                                "split_non_executed_count": split_non_executed_count,
                                "feature_count": int(split_null_stats["feature_count"]),
                                "feature_null_ratio_mean": _safe_float(split_null_stats["feature_null_ratio_mean"]),
                                "feature_null_ratio_max": _safe_float(split_null_stats["feature_null_ratio_max"]),
                                "feature_null_feature_count": int(split_null_stats["feature_null_feature_count"]),
                                "feature_all_null_count": int(split_null_stats["feature_all_null_count"]),
                                "label_ic_abs_mean": _safe_float(label_ic["ic_abs_mean"]),
                                "label_ic_abs_top": _safe_float(label_ic["ic_abs_top"]),
                                "label_ic_top_feature": str(label_ic["ic_top_feature"]),
                                "regime_ic_abs_mean": _safe_float(regime_ic["ic_abs_mean"]),
                                "regime_ic_abs_top": _safe_float(regime_ic["ic_abs_top"]),
                                "regime_ic_top_feature": str(regime_ic["ic_top_feature"]),
                                "return_ic_abs_mean": _safe_float(return_ic["ic_abs_mean"]),
                                "return_ic_abs_top": _safe_float(return_ic["ic_abs_top"]),
                                "return_ic_top_feature": str(return_ic["ic_top_feature"]),
                                **final_metrics,
                            },
                        ]
                    )
                )

            # B6 fix: walk-forward 已经在 _build_walk_forward_windows 内保证 test_df 非空，
            # 所以这里的 pred_split 一定是 "test"；删掉 elif/else 死代码避免误导。
            if test_df.empty:
                # 兼容 _build_walk_forward_windows 的兜底 fallback 路径（极小数据集）
                continue
            pred_base = test_df.copy()
            pred_split = "test"
            pred_df = pred_base[
                [
                    c
                    for c in (
                        "symbol",
                        "exchange",
                        "interval",
                        "datetime",
                        "signal_datetime",
                        "exit_datetime",
                        "signal_type",
                        "side",
                        "order_type",
                        "candidate_status",
                        "is_executed",
                        "entry_price",
                        "exit_price_ref",
                        "trigger",
                        "stop_price",
                        "label_class",
                        "regime_label",
                        "future_mfe_atr",
                        "future_mae_atr",
                        "future_pnl_atr",
                    )
                    if c in pred_base.columns
                ]
            ].copy()
            pred_df["model_signal_type"] = signal_type_key
            pred_df["window_id"] = win.window_id
            pred_df["pred_split"] = pred_split
            pred_df["trade_filter_prob"] = trade_model.predict_proba(
                pred_base,
                feature_columns=trade_feature_columns,
            )
            pred_df["pred_regime_label"] = regime_model.predict(
                pred_base,
                feature_columns=regime_feature_columns,
            )
            if mfe_mae_model is None:
                # B1 fix: 没有 executed 训练样本 → 预测列写 NaN
                pred_df["pred_mfe_atr"] = np.nan
                pred_df["pred_mae_atr"] = np.nan
            else:
                mfe_pred = mfe_mae_model.predict(pred_base, feature_columns=mfe_feature_columns)
                pred_df["pred_mfe_atr"] = mfe_pred["pred_mfe_atr"].to_numpy()
                pred_df["pred_mae_atr"] = mfe_pred["pred_mae_atr"].to_numpy()
            pred_meta = _build_final_decision_features(
                pred_base,
                trade_prob=np.asarray(pred_df["trade_filter_prob"], dtype=float),
                regime_label=pred_df["pred_regime_label"].astype(str).tolist(),
                pred_mfe=np.asarray(pd.to_numeric(pred_df["pred_mfe_atr"], errors="coerce").fillna(0.0), dtype=float),
                pred_mae=np.asarray(pd.to_numeric(pred_df["pred_mae_atr"], errors="coerce").fillna(0.0), dtype=float),
                mae_penalty=LABEL_MAE_PENALTY,
            )
            for c in final_feature_columns:
                pred_df[c] = pred_meta[c].to_numpy()
            pred_df["final_decision_score"] = final_model.predict_proba(
                pred_df,
                feature_columns=final_feature_columns,
            )
            pred_df["final_decision_model_kind"] = final_model.model_kind
            prediction_parts.append(pred_df)

    if metrics_parts:
        metrics_df = pd.concat(metrics_parts, axis=0, ignore_index=True)
    else:
        metrics_df = pd.DataFrame(
            columns=[
                "signal_type",
                "window_id",
                "split",
                "model",
                "model_kind",
                "train_executed_count",
                "train_non_executed_count",
                "selected_params",
                "selection_train_auc",
                "selection_valid_auc",
                "selection_auc_gap",
                "split_start",
                "split_end",
                "split_sample_count",
                "split_executed_count",
                "split_non_executed_count",
                "feature_count",
                "feature_null_ratio_mean",
                "feature_null_ratio_max",
                "feature_null_feature_count",
                "feature_all_null_count",
                "label_ic_abs_mean",
                "label_ic_abs_top",
                "label_ic_top_feature",
                "regime_ic_abs_mean",
                "regime_ic_abs_top",
                "regime_ic_top_feature",
                "return_ic_abs_mean",
                "return_ic_abs_top",
                "return_ic_top_feature",
                "auc",
                "accuracy",
                "precision",
                "recall",
                "f1",
                "macro_f1",
                "weighted_f1",
                "direction_auc",
                "mfe_mae_mae",
                "mae_mae",
                "mfe_rmse",
                "mae_rmse",
                "mfe_r2",
                "mae_r2",
            ]
        )
    if prediction_parts:
        prediction_df = pd.concat(prediction_parts, axis=0, ignore_index=True)
    else:
        prediction_df = pd.DataFrame()
    if top_feature_parts:
        top_feature_df = pd.concat(top_feature_parts, axis=0, ignore_index=True)
    else:
        top_feature_df = _empty_top_feature_importance_frame()

    metrics_path = out_dir / f"{run_date}_{sym}_{interval_norm}_{trade_side_mode}_metrics.csv"
    prediction_path = out_dir / f"{run_date}_{sym}_{interval_norm}_{trade_side_mode}_predictions.csv"
    top_feature_importance_path = out_dir / f"{run_date}_{sym}_{interval_norm}_{trade_side_mode}_top10_feature_importance.csv"
    decile_path = out_dir / f"{run_date}_{sym}_{interval_norm}_{trade_side_mode}_last_oot_decile_returns.csv"
    oot_monthly_path = out_dir / f"{run_date}_{sym}_{interval_norm}_{trade_side_mode}_oot_monthly_returns.csv"
    oot_summary_path = out_dir / f"{run_date}_{sym}_{interval_norm}_{trade_side_mode}_oot_summary.csv"
    oot_trades_path = out_dir / f"{run_date}_{sym}_{interval_norm}_{trade_side_mode}_oot_trade_details.csv"
    oot_throttle_log_path = out_dir / f"{run_date}_{sym}_{interval_norm}_{trade_side_mode}_throttle_log.csv"
    oot_position_lifetime_path = out_dir / f"{run_date}_{sym}_{interval_norm}_{trade_side_mode}_oot_position_lifetime.csv"
    metrics_df.to_csv(metrics_path, index=False, encoding="utf-8-sig")
    prediction_df.to_csv(prediction_path, index=False, encoding="utf-8-sig")
    top_feature_df.to_csv(top_feature_importance_path, index=False, encoding="utf-8-sig")
    _write_provenance(
        out_dir=out_dir,
        run_tag=run_date,
        candidate_path=candidate_path,
        feature_table_path=feature_table_path,
        prediction_path=prediction_path,
        metrics_path=metrics_path,
        top_feature_importance_path=top_feature_importance_path,
    )

    # valid/test AUC gap 守护：test 不参与选参，只做事后告警。
    # 把每个 (signal_type, window_id, model) 的 valid_auc 与 test_auc 比较，
    # 超过阈值的窗口写到 auc_gap_alerts.csv 便于人工审计 train/test 泄漏。
    auc_gap_alerts_path = out_dir / f"{run_date}_{sym}_{interval_norm}_{trade_side_mode}_auc_gap_alerts.csv"
    auc_gap_alert_df = _build_valid_test_gap_alerts(metrics_df, max_gap=float(max_valid_test_gap))
    auc_gap_alert_df.to_csv(auc_gap_alerts_path, index=False, encoding="utf-8-sig")
    if not auc_gap_alert_df.empty:
        logger.warning(
            "valid-test AUC gap alerts: %d rows (max_gap=%.4f) -> %s",
            len(auc_gap_alert_df),
            float(max_valid_test_gap),
            auc_gap_alerts_path.name,
        )

    # top-1 特征集中度告警：单一特征 importance > 阈值时大概率是穿越或特征拟合到噪声。
    suspect_features_path = out_dir / f"{run_date}_{sym}_{interval_norm}_{trade_side_mode}_suspect_features.csv"
    suspect_features_df = _build_top_feature_concentration_alerts(
        top_feature_df, top1_thresh=float(top_feature_importance_alert_pct)
    )
    suspect_features_df.to_csv(suspect_features_path, index=False, encoding="utf-8-sig")
    if not suspect_features_df.empty:
        logger.warning(
            "top-1 feature concentration alerts: %d rows (top1_pct_in_top10>=%.2f) -> %s",
            len(suspect_features_df),
            float(top_feature_importance_alert_pct),
            suspect_features_path.name,
        )

    # P1.5：列出"在 manifest 中没有声明 causal 与否"的特征，写到 unaudited_features.csv
    # 让团队增量补 manifest，最终目标是 manifest 覆盖所有 generic_/feature_ 列。
    unaudited_path = out_dir / f"{run_date}_{sym}_{interval_norm}_{trade_side_mode}_unaudited_features.csv"
    try:
        all_feats: list[str] = []
        seen_feat: set[str] = set()
        for col in feature_df.columns:
            n = str(col)
            if (n.startswith("feature_") or n.startswith("generic_")) and n.lower() not in seen_feat:
                seen_feat.add(n.lower())
                all_feats.append(n)
        unaudited = _list_unaudited_features(all_feats)
        pd.DataFrame({"feature": unaudited}).to_csv(unaudited_path, index=False, encoding="utf-8-sig")
        if unaudited:
            logger.warning(
                "unaudited features (not in causality_manifest.csv): %d -> %s",
                len(unaudited),
                unaudited_path.name,
            )
    except Exception as exc:
        logger.warning("write unaudited_features failed: %s", exc)

    process_steps.append("4) 使用 OOT(test) 进行最终评估并生成报告")
    decile_df = _build_last_oot_decile_table(prediction_df, bins=10)
    decile_df.to_csv(decile_path, index=False, encoding="utf-8-sig")
    oot_extra: dict[str, pd.DataFrame] = {}
    oot_monthly_df, oot_summary_df, oot_trade_df = _evaluate_oot_real_execution(
        prediction_df,
        cfg=oot_eval_config,
        extra_outputs=oot_extra,
    )
    oot_monthly_df.to_csv(oot_monthly_path, index=False, encoding="utf-8-sig")
    oot_summary_df.to_csv(oot_summary_path, index=False, encoding="utf-8-sig")
    oot_trade_df.to_csv(oot_trades_path, index=False, encoding="utf-8-sig")
    oot_extra.get("throttle_log", pd.DataFrame()).to_csv(oot_throttle_log_path, index=False, encoding="utf-8-sig")
    oot_extra.get("position_lifetime", pd.DataFrame()).to_csv(
        oot_position_lifetime_path,
        index=False,
        encoding="utf-8-sig",
    )
    html_report_path = write_pipeline_oot_html_report(
        oot_trade_df=oot_trade_df,
        out_dir=out_dir,
        title=f"CTA Model OOT / {sym}.{ex} / {interval_norm} / {trade_side_mode}",
        periods_per_year=int(max(1.0, float(oot_eval_config.annualization_factor))),
        initial_capital=float(oot_eval_config.initial_capital),
    )
    if html_report_path is None:
        html_report_path = out_dir / f"{run_date}_{sym}_{interval_norm}_{trade_side_mode}_oot_report.html"
        html_report_path.write_text(
            (
                "<!DOCTYPE html><html><head><meta charset='utf-8'><title>OOT Report</title></head>"
                "<body><h1>OOT Report</h1><p>No valid OOT executed trades.</p></body></html>"
            ),
            encoding="utf-8",
        )
    process_steps.append(
        f"- oot_prediction_rows={len(prediction_df)}, last_oot_decile_rows={len(decile_df)}, metrics_rows={len(metrics_df)}"
    )
    if not oot_summary_df.empty:
        row = oot_summary_df.iloc[0]
        sel_val = _safe_float(row.get("selected_rows", 0))
        sel_int = int(sel_val) if np.isfinite(sel_val) else 0
        process_steps.append(
            (
                f"- oot_real_exec selected={sel_int}, "
                f"gross_pnl={_safe_float(row.get('gross_pnl')):.6f}, "
                f"total_return_pct={_safe_float(row.get('total_return_pct')):.6f}, "
                f"monthly_sharpe={_safe_float(row.get('monthly_sharpe')):.6f}"
            )
        )

    neg_count = int((pd.to_numeric(candidate_df["is_executed"], errors="coerce").fillna(0).astype(int) == 0).sum())
    total_count = len(candidate_df)
    ratio = (neg_count / total_count) if total_count else 0.0

    # B8 fix: 优先 to_markdown（依赖 tabulate），缺包/转换失败时回退 to_string，
    # 与 baseline_skill_suite.run_baseline_suite 的报告逻辑保持一致。
    if metrics_df.empty:
        metrics_block = "no metrics"
    else:
        try:
            metrics_block = metrics_df.to_markdown(index=False, floatfmt=".6f")
        except (ImportError, ValueError) as exc:
            logger.warning("metrics_df.to_markdown failed (%s), fallback to to_string", exc)
            metrics_block = "```\n" + metrics_df.to_string(index=False) + "\n```"

    if decile_df.empty:
        decile_block = "no last-oot decile rows"
    else:
        try:
            decile_block = decile_df.to_markdown(index=False, floatfmt=".6f")
        except (ImportError, ValueError) as exc:
            logger.warning("decile_df.to_markdown failed (%s), fallback to to_string", exc)
            decile_block = "```\n" + decile_df.to_string(index=False) + "\n```"

    if oot_monthly_df.empty:
        oot_monthly_block = "no oot monthly rows"
    else:
        try:
            oot_monthly_block = oot_monthly_df.to_markdown(index=False, floatfmt=".6f")
        except (ImportError, ValueError) as exc:
            logger.warning("oot_monthly_df.to_markdown failed (%s), fallback to to_string", exc)
            oot_monthly_block = "```\n" + oot_monthly_df.to_string(index=False) + "\n```"

    if oot_summary_df.empty:
        oot_summary_block = "no oot summary rows"
    else:
        try:
            oot_summary_block = oot_summary_df.to_markdown(index=False, floatfmt=".6f")
        except (ImportError, ValueError) as exc:
            logger.warning("oot_summary_df.to_markdown failed (%s), fallback to to_string", exc)
            oot_summary_block = "```\n" + oot_summary_df.to_string(index=False) + "\n```"

    if metrics_df.empty:
        split_diag_block = "no split diagnostics"
    else:
        split_diag_cols = [
            "signal_type",
            "window_id",
            "split",
            "split_start",
            "split_end",
            "split_sample_count",
            "split_executed_count",
            "split_non_executed_count",
            "feature_count",
            "feature_null_ratio_mean",
            "feature_null_ratio_max",
            "feature_null_feature_count",
            "feature_all_null_count",
            "label_ic_abs_mean",
            "regime_ic_abs_mean",
            "return_ic_abs_mean",
        ]
        split_diag_df = metrics_df.loc[:, [c for c in split_diag_cols if c in metrics_df.columns]].drop_duplicates(
            subset=["signal_type", "window_id", "split"], keep="first"
        )
        split_diag_df = split_diag_df.sort_values(["signal_type", "window_id", "split"]).reset_index(drop=True)
        try:
            split_diag_block = split_diag_df.to_markdown(index=False, floatfmt=".6f")
        except (ImportError, ValueError) as exc:
            logger.warning("split_diag_df.to_markdown failed (%s), fallback to to_string", exc)
            split_diag_block = "```\n" + split_diag_df.to_string(index=False) + "\n```"

    process_steps_block = "\n".join(f"- {line}" for line in process_steps)

    report_path = out_dir / f"{run_date}_{sym}_{interval_norm}_{trade_side_mode}_model_report.md"
    report_lines = [
        "# CTA Model Pipeline Report",
        "",
        f"- symbol: `{sym}.{ex}`",
        f"- interval: `{interval_norm}`",
        f"- side mode: `{trade_side_mode}`",
        f"- date range: `{start_date}` -> `{end_date}`",
        f"- by_signal_type: `{by_signal_type}`",
        f"- max_walk_forward_windows: `{max_walk_forward_windows}`",
        f"- window_mode: `{window_mode}`",
        f"- rolling_train_years: `{rolling_train_years}`",
        f"- rolling_valid_years: `{rolling_valid_years}`",
        f"- rolling_test_years: `{rolling_test_years}`",
        f"- rolling_step_years: `{rolling_step_years}`",
        f"- max_auc_gap: `{max_auc_gap}`",
        f"- candidate_count: `{total_count}`",
        f"- negative_candidate_count: `{neg_count}`",
        f"- negative_ratio: `{ratio:.4f}`",
        # B12 fix: 改名 signal_frames_count，避免在 by_signal_type=False 时让读者误以为
        # signal_type 总数=1。signal_frames_count 含义是"实际进入训练循环的分组数"。
        f"- signal_frames_count: `{len(signal_frames)}`",
        "",
        "## Process Steps",
        process_steps_block,
        "",
        "## Split Diagnostics",
        split_diag_block,
        "",
        "## Metrics",
        metrics_block,
        "",
        "## Last OOT Decile Returns",
        decile_block,
        "",
        "## OOT Real Execution Evaluation",
        f"- config: `{oot_eval_config}`",
        "",
        "### OOT Monthly Returns",
        oot_monthly_block,
        "",
        "### OOT Summary",
        oot_summary_block,
        "",
        f"- candidates_csv: `{candidate_path}`",
        f"- feature_table_csv: `{feature_table_path}`",
        f"- predictions_csv: `{prediction_path}`",
        f"- metrics_csv: `{metrics_path}`",
        f"- top10_feature_importance_csv: `{top_feature_importance_path}`",
        f"- last_oot_decile_csv: `{decile_path}`",
        f"- oot_monthly_returns_csv: `{oot_monthly_path}`",
        f"- oot_summary_csv: `{oot_summary_path}`",
        f"- oot_trade_details_csv: `{oot_trades_path}`",
        f"- oot_throttle_log_csv: `{oot_throttle_log_path}`",
        f"- oot_position_lifetime_csv: `{oot_position_lifetime_path}`",
        f"- html_report: `{html_report_path}`",
    ]
    if pool_meta_path is not None:
        report_lines.append(f"- pool_members_csv: `{pool_meta_path}`")
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    logger.info("model pipeline finished: %s", out_dir)
    return ModelPipelineResult(
        output_dir=out_dir,
        candidate_path=candidate_path,
        feature_table_path=feature_table_path,
        prediction_path=prediction_path,
        metrics_path=metrics_path,
        oot_monthly_path=oot_monthly_path,
        oot_summary_path=oot_summary_path,
        oot_trades_path=oot_trades_path,
        html_report_path=html_report_path,
        top_feature_importance_path=top_feature_importance_path,
        report_path=report_path,
    )


def _normalize_intervals(raw: Iterable[str]) -> tuple[str, ...]:
    """Normalize a list of interval tokens into an ordered, deduplicated tuple.

    支持以下输入形态（CLI / 程序调用都可以混用）：
    - 空格分隔：``["day", "60min", "30min"]``
    - 逗号分隔（单 token 内）：``["day,60min", "30min"]``
    - 混合 + 重复：``[" day ", "", "60min,, 30min ", "day"]``

    返回首次出现顺序保留的去重元组。完全空时 raise ``ValueError`` 而不是
    静默返回空，避免 CLI 默认值消失后没人发现。
    """
    seen: set[str] = set()
    out: list[str] = []
    for token in raw:
        if token is None:
            continue
        for piece in str(token).split(","):
            v = piece.strip().lower()
            if not v:
                continue
            if v in seen:
                continue
            seen.add(v)
            out.append(v)
    if not out:
        raise ValueError("no valid interval provided; expected one of day/60min/30min/15min/5min/min")
    return tuple(out)


def run_model_pipeline_multi(
    symbol: str = "RB0",
    exchange: str | None = "SHFE",
    intervals: Sequence[str] = ("60min",),
    start_date: str = "2000-01-01",
    end_date: str = "2019-12-31",
    trade_side_mode: str = "both",
    train_end: str = "2018-12-31",
    valid_end: str = "2019-06-30",
    output_root: Path | None = None,
    feature_root: Path = FEATURE_ROOT,
    synthetic_periods: int = 400,
    by_signal_type: bool = True,
    max_walk_forward_windows: int = 3,
    window_mode: WindowMode = "expanding",
    rolling_train_years: int = 3,
    rolling_valid_years: int = 1,
    rolling_test_years: int = 1,
    rolling_step_years: int = 1,
    max_auc_gap: float = 0.03,
    max_valid_test_gap: float = 0.10,
    top_feature_importance_alert_pct: float = 0.50,
    generic_mode: GenericMode = "auto",
    oot_eval_config: OotEvaluationConfig = DEFAULT_OOT_EVAL_CONFIG,
    min_used_symbols: int = 2,
    seed: int = 2026,
) -> list[ModelPipelineResult]:
    """Run ``run_model_pipeline`` for each interval and return the result list.

    单一 interval 的失败不会终止整批：每个 interval 独立 run，异常会写日志后
    继续下一个；想要严格"全部成功"语义请自行检查 ``len(results) == len(intervals)``。
    """
    interval_tuple = _normalize_intervals(intervals)
    results: list[ModelPipelineResult] = []
    for idx, interval in enumerate(interval_tuple, start=1):
        logger.info(
            "[%d/%d] running model pipeline for symbol=%s interval=%s",
            idx,
            len(interval_tuple),
            symbol,
            interval,
        )
        try:
            res = run_model_pipeline(
                symbol=symbol,
                exchange=exchange,
                interval=interval,
                start_date=start_date,
                end_date=end_date,
                trade_side_mode=trade_side_mode,
                train_end=train_end,
                valid_end=valid_end,
                output_root=output_root,
                feature_root=feature_root,
                synthetic_periods=synthetic_periods,
                by_signal_type=by_signal_type,
                max_walk_forward_windows=max_walk_forward_windows,
                window_mode=window_mode,
                rolling_train_years=rolling_train_years,
                rolling_valid_years=rolling_valid_years,
                rolling_test_years=rolling_test_years,
                rolling_step_years=rolling_step_years,
                max_auc_gap=max_auc_gap,
                max_valid_test_gap=max_valid_test_gap,
                top_feature_importance_alert_pct=top_feature_importance_alert_pct,
                generic_mode=generic_mode,
                oot_eval_config=oot_eval_config,
                min_used_symbols=min_used_symbols,
                seed=seed,
            )
        except Exception:
            logger.exception("model pipeline failed for interval=%s", interval)
            continue
        results.append(res)
    return results


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run CTA model pipeline")
    parser.add_argument("--symbol", default="RB0")
    parser.add_argument("--exchange", default="SHFE")
    parser.add_argument(
        "--top-n-symbols",
        type=int,
        default=0,
        help=(
            "if > 0, ignore --symbol and load top-N symbols from --symbols-ranking-path "
            "ordered by research_rank"
        ),
    )
    parser.add_argument(
        "--symbols-ranking-path",
        default=str(SYMBOLS_RANKING_PATH),
        help="csv path of symbol research ranking (default cta/feature/symbols_research_ranking.csv)",
    )
    # M1: interval 支持数组（空格 + 逗号都行）。例如：
    #   --interval day 60min 30min 15min
    #   --interval day,60min 30min,15min
    parser.add_argument(
        "--interval",
        nargs="+",
        default=["60min"],
        help=(
            "one or more intervals (day/60min/30min/15min/5min/min). "
            "Accepts space-separated and comma-separated tokens; duplicates are deduped."
        ),
    )
    parser.add_argument("--start", default="2000-01-01")
    parser.add_argument("--end", default="2019-12-31")
    parser.add_argument("--trade-side-mode", default="both")
    parser.add_argument("--train-end", default="2018-12-31")
    parser.add_argument("--valid-end", default="2019-06-30")
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--synthetic-periods", type=int, default=400)
    parser.add_argument("--seed", type=int, default=2026, help="global random seed for reproducibility")
    parser.add_argument("--max-walk-forward-windows", type=int, default=3)
    parser.add_argument(
        "--max-auc-gap",
        type=float,
        default=0.03,
        help="max allowed abs(train_auc-valid_auc) during parameter selection (default 0.03)",
    )
    parser.add_argument(
        "--max-valid-test-gap",
        type=float,
        default=0.10,
        help=(
            "valid 与 test AUC 差异告警阈值（默认 0.10）。test 不参与选参，仅在事后"
            "把每个 (signal_type, window_id, model) 的 valid/test gap > 阈值的窗口写到"
            " *_auc_gap_alerts.csv 便于人工审计过拟合 / 时间漂移。"
        ),
    )
    parser.add_argument(
        "--top-feature-alert-pct",
        type=float,
        default=0.50,
        help=(
            "单一特征 importance 占比超过该值时写到 *_suspect_features.csv（默认 0.50）。"
            "经验上 top-1 特征占比过高通常对应命名未触发现有 leakage filter 的穿越特征。"
        ),
    )
    parser.add_argument(
        "--window-mode",
        default="expanding",
        choices=("expanding", "sliding", "rolling"),
        help="walk-forward window mode (default expanding train; sliding/rolling supported)",
    )
    parser.add_argument("--rolling-train-years", type=int, default=3, help="rolling mode train window years")
    parser.add_argument("--rolling-valid-years", type=int, default=1, help="rolling mode valid window years")
    parser.add_argument("--rolling-test-years", type=int, default=1, help="rolling mode test window years")
    parser.add_argument("--rolling-step-years", type=int, default=1, help="rolling mode step years")
    parser.add_argument("--by-signal-type", dest="by_signal_type", action="store_true")
    parser.add_argument("--no-by-signal-type", dest="by_signal_type", action="store_false")
    parser.set_defaults(by_signal_type=True)
    parser.add_argument(
        "--generic-mode",
        default="auto",
        choices=("auto", "whitelist"),
        help=(
            "generic 特征拼接策略：'auto' (默认) 自动取磁盘 parquet 上所有数值列；"
            "'whitelist' 仅 18 列 DEFAULT_GENERIC_COLUMNS。auto 模式下模型可用上 "
            "cta/data/feature 里预先算好的全部 ~400 个通用特征。"
        ),
    )
    parser.add_argument(
        "--pool",
        action="store_true",
        help=(
            "把 --top-n-symbols / --symbol 给定的多个品种**池化**成一个共享样本，"
            "训出一个跨品种的模型（输出目录 ..._POOL_..._model_pipeline）。"
            "适合 day 等单品种样本不足的 interval。"
        ),
    )
    parser.add_argument(
        "--group-pool",
        action="store_true",
        help=(
            "把 ranking 里的 symbols 先按 group 切分（例如 tier/cluster），"
            "然后每个 group 各自做一套 pool 训练与预测（组 × interval）。"
        ),
    )
    parser.add_argument(
        "--group-by",
        default="tier",
        help=(
            "group-pool 模式分组键：'cluster' 使用内置 symbol cluster；"
            "其它值按 ranking csv 的同名列分组（默认 tier）。"
        ),
    )
    parser.add_argument(
        "--group-min-size",
        type=int,
        default=2,
        help="group-pool 模式保留的最小组大小（默认 2）。",
    )
    parser.add_argument(
        "--only-clusters",
        nargs="+",
        default=None,
        help=(
            "group-pool 模式下仅训练指定 cluster（基础名，不带 'cluster_' 前缀）。"
            "例: --only-clusters index → 仅训练 cluster_index（IF0/IH0/IC0/IM0）；"
            "    --only-clusters index bond → 训练股指+国债两个 cluster。"
            "未指定（默认 None）= 训练 ranking 中全部 cluster。"
        ),
    )
    parser.add_argument(
        "--use-portfolio-logic-runtime",
        action="store_true",
        default=False,
        help=(
            "OOT 评估按线上 portfolio_logic 真实逻辑走（HTF gate + ranker + trailing + "
            "pyramid + score_calibration + risk_throttle）。默认 False = 用旧 FCFS 路径。"
            "等价于 OotEvaluationConfig(use_portfolio_logic_runtime=True)。"
            "依赖 cluster_registry.json 与 *_calibration.joblib 已生成。"
        ),
    )
    parser.add_argument(
        "--include-disabled-symbols",
        action="store_true",
        help=(
            "不应用 symbol_disable_manifest 过滤。默认会过滤掉被标记禁用的品种；"
            "若要覆盖 70+ 全量品种可打开该开关。"
        ),
    )
    parser.add_argument(
        "--min-used-symbols",
        type=int,
        default=2,
        help=(
            "POOL 模式最少实际参与训练的品种数（默认 2）。"
            "若低于该阈值则报错，避免把单品种误当池化模型。"
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    _validate_stop_loss_pct_consistency()
    # P2.2：入口处先按 CTA_GLOBAL_SEED 统一全局 RNG，run.sh 默认把它设为 RUN_TAG。
    # 这样 numpy/random/torch（若存在）三者初始状态一致，model_pipeline 的 random_state=2026
    # 不会覆盖更上游的 candidate 采样 / 特征构造里的隐式随机源。
    from cta.utils.random_seed import seed_all_from_env

    used_seed = seed_all_from_env("CTA_GLOBAL_SEED")
    if used_seed is not None:
        logger.info("model_pipeline: seeded global RNG from CTA_GLOBAL_SEED=%s", used_seed)
    args = _parse_args(argv)
    intervals = _normalize_intervals(args.interval)
    output_root = Path(args.output_root).resolve() if args.output_root else None
    top_n = int(getattr(args, "top_n_symbols", 0))
    respect_disabled_manifest = not bool(getattr(args, "include_disabled_symbols", False))

    # 根据 --use-portfolio-logic-runtime 构造 OOT 评估 config（用 line-trading 决策路径）
    if bool(getattr(args, "use_portfolio_logic_runtime", False)):
        effective_oot_cfg = dc_replace(
            DEFAULT_OOT_EVAL_CONFIG, use_portfolio_logic_runtime=True
        )
        logger.info(
            "OOT eval will use portfolio_logic runtime (htf=%s ranker=%s trail=%s "
            "pyramid=%s calib=%s throttle=%s)",
            effective_oot_cfg.portfolio_logic.enable_htf_gate,
            effective_oot_cfg.portfolio_logic.enable_ranker,
            effective_oot_cfg.portfolio_logic.enable_trailing,
            effective_oot_cfg.portfolio_logic.enable_pyramid,
            effective_oot_cfg.portfolio_logic.enable_score_calibration,
            effective_oot_cfg.portfolio_logic.enable_risk_throttle,
        )
    else:
        effective_oot_cfg = DEFAULT_OOT_EVAL_CONFIG

    if bool(getattr(args, "group_pool", False)):
        group_specs = _load_symbol_groups_from_ranking(
            Path(args.symbols_ranking_path),
            top_n=top_n,
            group_by=str(args.group_by),
            min_symbols_per_group=int(args.group_min_size),
            respect_disabled_manifest=respect_disabled_manifest,
        )
        # --only-clusters: 训练时只保留指定 cluster（如 index / bond）
        only_clusters = getattr(args, "only_clusters", None)
        if only_clusters:
            group_by_key = _safe_name(str(args.group_by) or "tier")
            wanted = {f"{group_by_key}_{_safe_name(str(c))}" for c in only_clusters}
            before = [g for g, _ in group_specs]
            group_specs = [(g, m) for g, m in group_specs if g in wanted]
            if not group_specs:
                raise ValueError(
                    f"--only-clusters {only_clusters} 过滤后无可训练组；"
                    f"原可用组={before}; 期望前缀={group_by_key}_*"
                )
        logger.info(
            "GROUP-POOL mode enabled: group_by=%s groups=%s intervals=%s",
            str(args.group_by),
            [g for g, _ in group_specs],
            list(intervals),
        )
        # 收集每个 (interval, group) 训练结果用于落 cluster_registry.json
        registry_records: list[dict[str, Any]] = []
        for interval in intervals:
            interval_records: list[dict[str, Any]] = []
            for group_name, members in group_specs:
                pool_name = f"GRP_{str(group_name).strip().upper()}"
                try:
                    res = run_model_pipeline(
                        symbol=pool_name,
                        exchange=None,
                        interval=interval,
                        start_date=args.start,
                        end_date=args.end,
                        trade_side_mode=args.trade_side_mode,
                        train_end=args.train_end,
                        valid_end=args.valid_end,
                        output_root=output_root,
                        synthetic_periods=args.synthetic_periods,
                        by_signal_type=bool(args.by_signal_type),
                        max_walk_forward_windows=int(args.max_walk_forward_windows),
                        window_mode=str(args.window_mode),
                        rolling_train_years=int(args.rolling_train_years),
                        rolling_valid_years=int(args.rolling_valid_years),
                        rolling_test_years=int(args.rolling_test_years),
                        rolling_step_years=int(args.rolling_step_years),
                        max_auc_gap=float(args.max_auc_gap),
                        max_valid_test_gap=float(args.max_valid_test_gap),
                        top_feature_importance_alert_pct=float(args.top_feature_alert_pct),
                        generic_mode=str(args.generic_mode),
                        pool_symbols=members,
                        pool_name=pool_name,
                        min_used_symbols=int(args.min_used_symbols),
                        seed=int(args.seed),
                        oot_eval_config=effective_oot_cfg,
                    )
                except Exception:
                    logger.exception(
                        "GROUP-POOL pipeline failed for group=%s interval=%s",
                        group_name,
                        interval,
                    )
                    continue
                logger.info(
                    "[%s][%s] report=%s predictions=%s metrics=%s top10=%s",
                    pool_name,
                    interval,
                    res.report_path,
                    res.prediction_path,
                    res.metrics_path,
                    res.top_feature_importance_path,
                )
                interval_records.append(
                    {
                        "interval": str(interval),
                        "group_name": str(group_name),
                        "pool_name": pool_name,
                        "model_dir": str(Path(res.output_dir).resolve()),
                        "members": [
                            {"symbol": str(s).upper(), "exchange": str(e or "")}
                            for s, e in members
                        ],
                    }
                )
            if interval_records:
                registry_records.extend(interval_records)
            else:
                logger.warning(
                    "group-pool interval=%s has no successful group training; registry interval entry is empty",
                    interval,
                )

        # 落 cluster_registry.json：线上推理由 ClusterModelRegistry 读取，
        # 按 symbol → group → model_dir 路由到对应 cluster 模型。
        if registry_records:
            run_date_tag = pd.Timestamp.now().strftime("%Y%m%d")
            registry_root = (output_root or DEFAULT_REPORT_ROOT).resolve()
            registry_root.mkdir(parents=True, exist_ok=True)
            registry_path = (
                registry_root
                / f"{run_date_tag}_cluster_registry_{str(args.group_by).strip().lower()}_{args.trade_side_mode}.json"
            )
            registry_payload = {
                "run_tag": run_date_tag,
                "group_by": str(args.group_by),
                "trade_side_mode": str(args.trade_side_mode),
                "intervals": sorted({r["interval"] for r in registry_records}),
                "entries": registry_records,
            }
            registry_path.write_text(
                json.dumps(registry_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("cluster registry written: %s (%d entries)", registry_path, len(registry_records))
        return

    if top_n > 0:
        symbols_to_run = _load_top_n_symbols_from_ranking(
            Path(args.symbols_ranking_path),
            top_n=top_n,
            respect_disabled_manifest=respect_disabled_manifest,
        )
        logger.info(
            "top-n symbol mode enabled: top_n=%s ranking_path=%s loaded=%s",
            top_n,
            args.symbols_ranking_path,
            [s for s, _ in symbols_to_run],
        )
    else:
        symbols_to_run = [(str(args.symbol).upper(), str(args.exchange).upper() if args.exchange else None)]

    if bool(getattr(args, "pool", False)):
        # 池化模式：把所有 symbol 一次性拼成共享训练集，跨 interval 各训一个 POOL 模型
        logger.info(
            "POOL mode: training a single shared model across %d symbols: %s",
            len(symbols_to_run), [s for s, _ in symbols_to_run],
        )
        for interval in intervals:
            try:
                res = run_model_pipeline(
                    symbol="POOL",
                    exchange=None,
                    interval=interval,
                    start_date=args.start,
                    end_date=args.end,
                    trade_side_mode=args.trade_side_mode,
                    train_end=args.train_end,
                    valid_end=args.valid_end,
                    output_root=output_root,
                    synthetic_periods=args.synthetic_periods,
                    by_signal_type=bool(args.by_signal_type),
                    max_walk_forward_windows=int(args.max_walk_forward_windows),
                    window_mode=str(args.window_mode),
                    rolling_train_years=int(args.rolling_train_years),
                    rolling_valid_years=int(args.rolling_valid_years),
                    rolling_test_years=int(args.rolling_test_years),
                    rolling_step_years=int(args.rolling_step_years),
                    max_auc_gap=float(args.max_auc_gap),
                    max_valid_test_gap=float(args.max_valid_test_gap),
                    top_feature_importance_alert_pct=float(args.top_feature_alert_pct),
                    generic_mode=str(args.generic_mode),
                    pool_symbols=symbols_to_run,
                    min_used_symbols=int(args.min_used_symbols),
                    seed=int(args.seed),
                    oot_eval_config=effective_oot_cfg,
                )
            except Exception:
                logger.exception("POOL pipeline failed for interval=%s", interval)
                continue
            logger.info("[POOL][%s] report: %s", interval, res.report_path)
            logger.info("[POOL][%s] predictions: %s", interval, res.prediction_path)
            logger.info("[POOL][%s] metrics: %s", interval, res.metrics_path)
            logger.info("[POOL][%s] top10 feature importance: %s",
                        interval, res.top_feature_importance_path)
        return

    for sidx, (symbol, exchange_from_rank) in enumerate(symbols_to_run, start=1):
        # D1 fix：靠 helper 把"ranking 提供 vs CLI 提供"的优先级显式化，
        # 避免回到 ``a or b if c else None`` 这种被 Python 优先级误解析的写法。
        run_exchange = _resolve_run_exchange(exchange_from_rank, args.exchange)
        logger.info(
            "[%d/%d] run symbol=%s exchange=%s intervals=%s",
            sidx,
            len(symbols_to_run),
            symbol,
            run_exchange,
            list(intervals),
        )
        results = run_model_pipeline_multi(
            symbol=symbol,
            exchange=run_exchange,
            intervals=intervals,
            start_date=args.start,
            end_date=args.end,
            trade_side_mode=args.trade_side_mode,
            train_end=args.train_end,
            valid_end=args.valid_end,
            output_root=output_root,
            synthetic_periods=args.synthetic_periods,
            by_signal_type=bool(args.by_signal_type),
            max_walk_forward_windows=int(args.max_walk_forward_windows),
            window_mode=str(args.window_mode),
            rolling_train_years=int(args.rolling_train_years),
            rolling_valid_years=int(args.rolling_valid_years),
            rolling_test_years=int(args.rolling_test_years),
            rolling_step_years=int(args.rolling_step_years),
            max_auc_gap=float(args.max_auc_gap),
            max_valid_test_gap=float(args.max_valid_test_gap),
            top_feature_importance_alert_pct=float(args.top_feature_alert_pct),
            generic_mode=str(args.generic_mode),
            min_used_symbols=int(args.min_used_symbols),
            seed=int(args.seed),
            oot_eval_config=effective_oot_cfg,
        )
        for interval, result in zip(intervals, results):
            logger.info("[%s][%s] report: %s", symbol, interval, result.report_path)
            logger.info("[%s][%s] predictions: %s", symbol, interval, result.prediction_path)
            logger.info("[%s][%s] metrics: %s", symbol, interval, result.metrics_path)
            logger.info("[%s][%s] top10 feature importance: %s", symbol, interval, result.top_feature_importance_path)
        if len(results) < len(intervals):
            logger.warning(
                "[%s] only %d/%d intervals succeeded; see logs for failures",
                symbol,
                len(results),
                len(intervals),
            )


if __name__ == "__main__":
    main()


__all__ = [
    "ModelPipelineResult",
    "run_model_pipeline",
    "run_model_pipeline_multi",
]
