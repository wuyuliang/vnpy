"""Cluster-aware model registry for online inference.

线上推理路由：
1. 训练侧 (``model_pipeline.py`` 的 ``--group-pool --group-by cluster`` 模式)
   按 cluster 分组训练，每个 cluster 落一份独立产物目录 + 写入 cluster_registry.json；
2. 推理侧（本模块）读取 cluster_registry.json，按 symbol → cluster → model_dir 路由
   到对应 cluster 的 joblib 加载和 predict_proba。

目录约定
--------
每个 cluster 的 model_dir 结构：
    <model_dir>/
      models/
        <signal_type>/
          window_NN/
            trade_filter.joblib
            regime_classifier.joblib
            mfe_mae.joblib
            final_decision_stack.joblib
            *_features.csv
      provenance.json
      ...

推理时默认取 ``window_NN`` 最新的（最大 window_id）模型。

线上使用示例
------------

    from cta.model.training.cluster_model_registry import ClusterModelRegistry

    reg = ClusterModelRegistry.from_registry_json(
        "cta/backtest/20260515_cluster_registry_cluster_both.json"
    )
    # df 含 columns: symbol, signal_type, feature_*, generic_*
    out = reg.predict_proba(df, model_kind="trade_filter")
    # out 新增列：trade_filter_prob, cluster_used, model_dir_used

registry schema（cluster_registry.json）
---------------------------------------
{
  "run_tag": "20260516",
  "group_by": "cluster",
  "trade_side_mode": "both",
  "intervals": ["day", "60min"],
  "entries": [
    {
      "interval": "60min",
      "group_name": "cluster_black",
      "pool_name": "GRP_CLUSTER_BLACK",
      "model_dir": "cta/backtest/20260516_GRP_CLUSTER_BLACK_minute60_both_model_pipeline",
      "members": [{"symbol": "RB0", "exchange": "SHFE"}]
    }
  ]
}
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from cta.config.symbol_cluster_config import infer_symbol_cluster
from cta.config.skill_tight_range_breakout_config import CTA_ROOT
from cta.portfolio_logic.score_calibrator import ScoreCalibrator

logger = logging.getLogger(__name__)


SUPPORTED_MODEL_KINDS: tuple[str, ...] = (
    "trade_filter",
    "regime_classifier",
    "mfe_mae",
    "final_decision_stack",
)


def _load_model_obj(joblib_path: Path, model_kind: str) -> Any:
    """Load a saved model using its class-level ``.load()`` to restore the wrapper object.

    Raw ``joblib.load()`` returns the persisted dict, not the wrapper class instance
    (which has ``.predict_proba(df, feature_columns=...)``). Dispatch by model_kind.
    """
    if model_kind == "trade_filter":
        from cta.model.training.trade_filter_model import TradeFilterModel

        return TradeFilterModel.load(joblib_path)
    if model_kind == "regime_classifier":
        from cta.model.training.regime_classifier_model import RegimeClassifierModel

        return RegimeClassifierModel.load(joblib_path)
    if model_kind == "mfe_mae":
        from cta.model.training.mfe_mae_model import MfeMaeModel

        return MfeMaeModel.load(joblib_path)
    if model_kind == "final_decision_stack":
        from cta.model.training.final_decision_model import FinalDecisionModel

        return FinalDecisionModel.load(joblib_path)
    raise ValueError(f"unsupported model_kind={model_kind}")


def _safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", str(s)).strip("_").lower()


def _cluster_group_key(symbol: str, group_by: str = "cluster") -> str:
    """Map symbol to group_name (matching training-time _load_symbol_groups_from_ranking)."""
    if str(group_by).strip().lower() != "cluster":
        # 非 cluster 分组（如 tier）无法在线推断，只能依赖 registry 的 symbol→group 反查
        return ""
    cluster = infer_symbol_cluster(symbol)
    return f"cluster_{_safe_name(cluster)}"


@dataclass
class ClusterEntry:
    """One row of cluster_registry.json — 一个 (interval, cluster) 的训练产物。"""

    interval: str
    group_name: str        # e.g. cluster_black
    pool_name: str         # e.g. GRP_CLUSTER_BLACK
    model_dir: Path
    members: list[str] = field(default_factory=list)


@dataclass
class ClusterModelRegistry:
    """In-memory routing table: (interval, group_name) → model_dir; symbol → group_name."""

    group_by: str
    trade_side_mode: str
    entries: dict[tuple[str, str], ClusterEntry]
    symbol_to_group: dict[str, str]
    _model_cache: dict[tuple[str, str, str, int], Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.entries)

    def __bool__(self) -> bool:
        return bool(self.entries)

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------
    @classmethod
    def from_registry_json(cls, path: Path | str) -> "ClusterModelRegistry":
        p = Path(path).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"cluster registry json not found: {p}")
        payload = json.loads(p.read_text(encoding="utf-8"))
        group_by = str(payload.get("group_by", "cluster"))
        trade_side_mode = str(payload.get("trade_side_mode", "both"))
        raw_entries = payload.get("entries")
        if raw_entries is None:
            logger.error("cluster_registry.json at %s missing entries field", p)
            raw_entries = []
        if not isinstance(raw_entries, list):
            logger.error(
                "cluster_registry.json at %s has non-list entries=%s; treating as empty",
                p,
                type(raw_entries).__name__,
            )
            raw_entries = []
        entries: dict[tuple[str, str], ClusterEntry] = {}
        symbol_to_group: dict[str, str] = {}
        for rec in raw_entries:
            itv = str(rec.get("interval", "")).strip()
            grp = str(rec.get("group_name", "")).strip()
            pool = str(rec.get("pool_name", "")).strip()
            mdir = Path(str(rec.get("model_dir", ""))).expanduser()
            if not itv or not grp or not pool or not str(mdir):
                continue
            members = [str(m.get("symbol", "")).upper() for m in rec.get("members", []) if m.get("symbol")]
            entries[(itv, grp)] = ClusterEntry(
                interval=itv, group_name=grp, pool_name=pool, model_dir=mdir, members=members
            )
            for s in members:
                # 同 symbol 在多个 interval 下都映射到同一 group 名，因为分组规则只看 symbol
                symbol_to_group[s] = grp
        if not entries:
            logger.error(
                "cluster_registry.json at %s has empty entries — registry is unusable",
                p,
            )
        if str(group_by).strip().lower() != "cluster":
            logger.warning(
                "cluster_registry.json group_by=%s (non-cluster). "
                "symbol not in members cannot be inferred to a group online.",
                group_by,
            )
        return cls(
            group_by=group_by,
            trade_side_mode=trade_side_mode,
            entries=entries,
            symbol_to_group=symbol_to_group,
        )

    @classmethod
    def from_run_tag(
        cls,
        run_tag: str,
        *,
        report_root: Path | str = CTA_ROOT / "report" / "backtest",
        group_by: str = "cluster",
        trade_side_mode: str = "both",
    ) -> "ClusterModelRegistry":
        """Discover by scanning ``<report_root>/<run_tag>_cluster_registry_*.json``."""
        root = Path(report_root).expanduser().resolve()
        candidates = sorted(
            root.glob(f"{run_tag}_cluster_registry_{group_by}_{trade_side_mode}.json")
        )
        if not candidates:
            raise FileNotFoundError(
                f"no cluster registry under {root} matching "
                f"{run_tag}_cluster_registry_{group_by}_{trade_side_mode}.json"
            )
        # 取最新的一份（按文件 mtime）
        latest = max(candidates, key=lambda p: p.stat().st_mtime)
        logger.info("loading cluster registry from %s", latest)
        return cls.from_registry_json(latest)

    # ------------------------------------------------------------------
    # routing
    # ------------------------------------------------------------------
    def resolve_group(self, symbol: str) -> str:
        """Return group_name for the given symbol (cluster_black, cluster_metal, ...)."""
        sym = str(symbol).upper().strip()
        if sym in self.symbol_to_group:
            return self.symbol_to_group[sym]
        # fallback：动态按 cluster 推（symbol 不在训练集时也能用同 cluster 的模型）
        if str(self.group_by).lower() == "cluster":
            return _cluster_group_key(sym, "cluster")
        logger.warning(
            "ClusterModelRegistry initialized with group_by=%s, "
            "resolve_group for symbol=%s will return empty",
            self.group_by,
            sym,
        )
        return ""

    def resolve_model_dir(self, symbol: str, interval: str) -> Path | None:
        grp = self.resolve_group(symbol)
        if not grp:
            return None
        entry = self.entries.get((str(interval).strip(), grp))
        return entry.model_dir if entry else None

    # ------------------------------------------------------------------
    # model loading & prediction
    # ------------------------------------------------------------------
    def _latest_window_dir(self, model_dir: Path, signal_type: str) -> Path | None:
        sig_dir = Path(model_dir) / "models" / _safe_name(signal_type)
        if not sig_dir.exists():
            return None
        windows = sorted(
            [p for p in sig_dir.glob("window_*") if p.is_dir()],
            key=lambda p: int(re.findall(r"\d+", p.name)[-1] or "0"),
        )
        return windows[-1] if windows else None

    def _load_model(
        self, model_dir: Path, signal_type: str, model_kind: str
    ) -> tuple[Any, list[str], Path, ScoreCalibrator | None]:
        """Return (model_obj, feature_columns, window_dir, calibrator) with cache."""
        if model_kind not in SUPPORTED_MODEL_KINDS:
            raise ValueError(f"unsupported model_kind={model_kind}; valid={SUPPORTED_MODEL_KINDS}")
        window_dir = self._latest_window_dir(model_dir, signal_type)
        if window_dir is None:
            raise FileNotFoundError(
                f"no window_NN dir under {model_dir}/models/{signal_type}"
            )
        wid = int(re.findall(r"\d+", window_dir.name)[-1] or "0")
        key = (str(model_dir), signal_type, model_kind, wid)
        if key in self._model_cache:
            cached = self._model_cache[key]
            return cached["model"], cached["features"], window_dir, cached.get("calibrator")
        joblib_path = window_dir / f"{model_kind}.joblib"
        if not joblib_path.exists():
            raise FileNotFoundError(f"{joblib_path} not found")
        # 用对应 model 类的 .load() 还原 wrapper 对象（带 predict_proba 接口），
        # 不能用 raw joblib.load —— 后者返回持久化字典。
        model_obj = _load_model_obj(joblib_path, model_kind)
        feat_csv = window_dir / f"{model_kind}_features.csv"
        if feat_csv.exists():
            feat_df = pd.read_csv(feat_csv, encoding="utf-8-sig")
            features = feat_df["feature"].astype(str).tolist() if "feature" in feat_df.columns else []
        else:
            features = []
        cal = None
        cal_path = window_dir / f"{model_kind}_calibration.joblib"
        if cal_path.exists():
            try:
                cal = ScoreCalibrator.load(cal_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("failed to load calibration %s: %s", cal_path, exc)
                cal = None
        self._model_cache[key] = {"model": model_obj, "features": features, "calibrator": cal}
        return model_obj, features, window_dir, cal

    def predict_proba(
        self,
        df: pd.DataFrame,
        *,
        model_kind: str = "trade_filter",
        signal_type_column: str = "signal_type",
        interval_column: str = "interval",
        symbol_column: str = "symbol",
        default_interval: str | None = None,
    ) -> pd.DataFrame:
        """Predict probability per row using cluster-routed models.

        返回原 df + 3 列：``<model_kind>_prob`` / ``cluster_used`` / ``model_dir_used``。
        symbol 找不到对应 cluster 模型时该行返回 NaN，便于上游 fallback 处理。
        """
        out = df.copy()
        out["cluster_used"] = ""
        out["model_dir_used"] = ""
        prob_col = f"{model_kind}_prob"
        out[prob_col] = np.nan

        if out.empty:
            return out

        sym_series = out.get(symbol_column, pd.Series([""] * len(out))).astype(str).str.upper().str.strip()
        if interval_column in out.columns:
            itv_series = out[interval_column].astype(str).str.strip()
        elif default_interval is not None:
            itv_series = pd.Series([str(default_interval)] * len(out), index=out.index)
        else:
            raise ValueError(
                f"df missing {interval_column} column and no default_interval provided"
            )
        sig_series = out.get(signal_type_column, pd.Series(["unknown"] * len(out))).astype(str)

        # 按 (interval, group, signal_type) 分组批量 predict 提升性能
        out["__group"] = [self.resolve_group(s) for s in sym_series]
        out["__interval"] = itv_series
        out["__signal_type"] = sig_series

        for (itv, grp, sig), sub_idx in out.groupby(
            ["__interval", "__group", "__signal_type"], dropna=False
        ).groups.items():
            if not grp:
                continue
            entry = self.entries.get((str(itv), str(grp)))
            if entry is None:
                logger.warning(
                    "no model entry for interval=%s group=%s; skipping %d rows",
                    itv, grp, len(sub_idx),
                )
                continue
            try:
                model_obj, feats, win_dir, calibrator = self._load_model(
                    entry.model_dir, str(sig), model_kind
                )
            except FileNotFoundError as exc:
                logger.warning(
                    "model not loadable for %s/%s/%s: %s", entry.model_dir, sig, model_kind, exc
                )
                continue
            sub_df = out.loc[sub_idx]
            if feats:
                feats_present = [c for c in feats if c in sub_df.columns]
                if not feats_present:
                    logger.warning(
                        "no overlapping features for group=%s signal=%s; got %d feats from manifest",
                        grp, sig, len(feats),
                    )
                    continue
                x = sub_df[feats_present]
            else:
                # 没有 manifest 时只能尽力用 model 自带 feature_names
                feature_names = getattr(model_obj, "feature_names_in_", None)
                if feature_names is None:
                    logger.warning(
                        "no feature manifest and model has no feature_names_in_; skip group=%s signal=%s",
                        grp, sig,
                    )
                    continue
                feats_present = [c for c in feature_names if c in sub_df.columns]
                x = sub_df[feats_present]
            try:
                # 训练侧 wrapper（TradeFilterModel / RegimeClassifierModel / FinalDecisionModel）
                # 统一接口：predict_proba(df, feature_columns) → ndarray[:, 1]
                # MfeMaeModel 是回归器，无 predict_proba，单独处理。
                if model_kind == "mfe_mae":
                    pred_df = model_obj.predict(sub_df, feature_columns=feats_present)
                    # MfeMaeModel.predict 返回 DataFrame {pred_mfe_atr, pred_mae_atr}
                    # 这里把"预测边际"作为 prob 写出（与训练侧 mfe_mae_gate 口径一致）
                    pred_mfe = (
                        pd.to_numeric(pred_df["pred_mfe_atr"], errors="coerce")
                        if "pred_mfe_atr" in pred_df.columns
                        else pd.Series(np.nan, index=pred_df.index)
                    )
                    pred_mae = (
                        pd.to_numeric(pred_df["pred_mae_atr"], errors="coerce")
                        if "pred_mae_atr" in pred_df.columns
                        else pd.Series(np.nan, index=pred_df.index)
                    )
                    edge = pred_mfe - 0.7 * pred_mae
                    if bool(edge.isna().all()):
                        logger.warning(
                            "mfe_mae returns all-NaN for %d rows in interval=%s cluster=%s signal=%s",
                            len(sub_df),
                            itv,
                            grp,
                            sig,
                        )
                    p1 = np.asarray(edge.to_numpy(), dtype=float)
                else:
                    proba = model_obj.predict_proba(sub_df, feature_columns=feats_present)
                    if hasattr(proba, "ndim") and proba.ndim == 2 and proba.shape[1] >= 2:
                        p1 = np.asarray(proba[:, 1], dtype=float)
                    else:
                        p1 = np.asarray(proba, dtype=float).reshape(-1)
            except Exception as exc:  # noqa: BLE001
                logger.exception("predict failed for group=%s signal=%s: %s", grp, sig, exc)
                continue
            out.loc[sub_idx, prob_col] = np.asarray(p1, dtype=float)
            out.loc[sub_idx, "cluster_used"] = grp
            out.loc[sub_idx, "model_dir_used"] = str(entry.model_dir)
            if calibrator is not None:
                interval_ser = out.loc[sub_idx, "__interval"].astype(str)
                cluster_ser = out.loc[sub_idx, "__group"].astype(str)
                pctl_vals: list[float] = []
                # mfe_mae 输出是 edge 值，校准 key 使用 mfe_mae_edge
                cal_model_kind = "mfe_mae_edge" if model_kind == "mfe_mae" else model_kind
                for raw_v, cluster_v, itv_v in zip(np.asarray(p1, dtype=float), cluster_ser, interval_ser):
                    pctl_vals.append(
                        calibrator.to_percentile(
                            str(cluster_v),
                            str(itv_v),
                            str(cal_model_kind),
                            float(raw_v),
                        )
                    )
                out.loc[sub_idx, f"{prob_col}_pctl"] = pctl_vals

        return out.drop(columns=["__group", "__interval", "__signal_type"])


__all__ = [
    "ClusterEntry",
    "ClusterModelRegistry",
    "SUPPORTED_MODEL_KINDS",
]
