"""CTA model pipeline with signal-type split and walk-forward evaluation."""
from __future__ import annotations

import argparse
import logging
import re
from dataclasses import dataclass
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
from cta.config.skill_tight_range_breakout_config import BacktestConfig
from cta.model.feature.training_feature_builder import FEATURE_ROOT, build_training_feature_table
from cta.model.mfe_mae_model import MfeMaeModel, evaluate_mfe_mae_model
from cta.model.regime_classifier_model import RegimeClassifierModel, evaluate_regime_model
from cta.model.trade_filter_model import TradeFilterModel, evaluate_trade_filter_model
from cta.strategy.baseline_skill_suite import generate_candidate_opportunities, prepare_master_feature_frame
from cta.strategy.skill_tight_range_backtest import load_bars, normalize_interval, resolve_exchange

# B5 fix: WindowMode 放到所有 import 之后，符合 PEP 8 import 顺序。
WindowMode = Literal["expanding", "sliding"]

# B1 fix: MFE/MAE 训练若没有可用 is_executed=1 样本，返回这个 sentinel 串，
# 避免拿全 0 标签训出"恒预测 0"的回归器。
MFE_MAE_KIND_SKIPPED_NO_EXEC = "skipped_no_exec"

logger = logging.getLogger(__name__)

FEATURES_DOC_PATH = Path(__file__).resolve().parents[1] / "feature" / "FEATURES.md"
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
    "feature_entry_price": "入场价格",
    "feature_fallback": "兜底常数特征（无可用特征时）",
}


@dataclass(frozen=True)
class ModelPipelineResult:
    output_dir: Path
    candidate_path: Path
    feature_table_path: Path
    prediction_path: Path
    metrics_path: Path
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
) -> list[tuple[str, str | None]]:
    """Load top-N symbols ordered by research_rank from ranking csv."""
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

    picked: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for _, row in df.iterrows():
        sym = str(row.get("symbol", "")).strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        ex_raw = row.get("exchange", None)
        ex = str(ex_raw).strip().upper() if pd.notna(ex_raw) and str(ex_raw).strip() else None
        picked.append((sym, ex))
        if len(picked) >= n:
            break
    if not picked:
        raise ValueError(f"no valid symbols loaded from ranking csv: {path}")
    return picked


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

        if "entry_price" in out.columns:
            out["feature_entry_price"] = pd.to_numeric(out["entry_price"], errors="coerce")
        else:
            out["feature_entry_price"] = np.nan
        feature_cols = ["feature_side_code", "feature_signal_code", "feature_entry_price"]

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


def _train_mfe_mae_or_skip(
    train_df: pd.DataFrame,
    feature_columns: list[str],
    *,
    random_state: int = 2026,
    mfe_column: str = "future_mfe_atr",
    mae_column: str = "future_mae_atr",
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
    model = MfeMaeModel(random_state=random_state).fit(
        train_exec,
        feature_columns=feature_columns,
        mfe_column=mfe_column,
        mae_column=mae_column,
    )
    return model, model.model_kind


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


def _time_split(
    df: pd.DataFrame,
    train_end: str,
    valid_end: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    dt = pd.to_datetime(df["datetime"], errors="coerce")
    train_cut = pd.Timestamp(train_end)
    valid_cut = pd.Timestamp(valid_end)
    train = df.loc[dt <= train_cut].copy()
    valid = df.loc[(dt > train_cut) & (dt <= valid_cut)].copy()
    test = df.loc[dt > valid_cut].copy()

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
    return train, valid, test


def _build_walk_forward_windows(
    df: pd.DataFrame,
    train_end: str,
    valid_end: str,
    max_windows: int,
    window_mode: WindowMode = "expanding",
) -> list[_WalkForwardWindow]:
    """Build walk-forward windows.

    window_mode:
        - "expanding": train 起点固定为最早样本，每个窗口 train_end 单调右移；
        - "sliding"  : train 长度固定（首窗口 train 长度），整体滑动。
    任意窗口若 train/valid/test 任一段为空则跳过；当数据已超过 valid_end 也提前退出。
    """
    mode = str(window_mode).lower()
    if mode not in {"expanding", "sliding"}:
        raise ValueError(f"invalid window_mode={window_mode}, valid=expanding|sliding")

    dt = pd.to_datetime(df["datetime"], errors="coerce")
    base_train_end = pd.Timestamp(train_end)
    base_valid_end = pd.Timestamp(valid_end)
    step = base_valid_end - base_train_end
    if step <= pd.Timedelta(0):
        step = pd.Timedelta(days=180)

    dt_min = dt.min() if not dt.empty else pd.NaT
    dt_max = dt.max() if not dt.empty else pd.NaT
    base_train_start = dt_min if pd.notna(dt_min) else base_train_end - step

    windows: list[_WalkForwardWindow] = []
    n_windows = max(1, int(max_windows))

    for wid in range(n_windows):
        w_train_end = base_train_end + wid * step
        w_valid_end = base_valid_end + wid * step
        w_test_end = w_valid_end + step
        if mode == "sliding":
            w_train_start = base_train_start + wid * step
            train_mask = (dt > w_train_start) & (dt <= w_train_end) if wid > 0 else (dt <= w_train_end)
        else:
            w_train_start = base_train_start
            train_mask = dt <= w_train_end
        train_df = df.loc[train_mask].copy()
        valid_df = df.loc[(dt > w_train_end) & (dt <= w_valid_end)].copy()
        test_df = df.loc[(dt > w_valid_end) & (dt <= w_test_end)].copy()
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


def _build_candidate_table(
    symbol: str,
    exchange: str | None,
    interval: str,
    start_date: str,
    end_date: str,
    trade_side_mode: str,
    synthetic_periods: int,
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
        logger.warning("candidate table empty from real data, switching to synthetic fallback")
    except Exception as exc:
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
) -> ModelPipelineResult:
    """Run full candidate->feature->model pipeline."""
    run_date = pd.Timestamp.now().strftime("%Y%m%d")
    sym = str(symbol).upper()
    interval_norm = normalize_interval(interval)
    root = output_root or DEFAULT_REPORT_ROOT
    out_dir = root / f"{run_date}_{sym}_{interval_norm}_{trade_side_mode}_model_pipeline"
    out_dir.mkdir(parents=True, exist_ok=True)

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

    feature_df = build_training_feature_table(
        candidate_df=candidate_df,
        symbol=sym,
        interval=interval_norm,
        feature_root=feature_root,
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
        windows = _build_walk_forward_windows(
            sig_df,
            train_end=train_end,
            valid_end=valid_end,
            max_windows=max_walk_forward_windows,
            window_mode=window_mode,
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

            trade_model = TradeFilterModel(random_state=2026).fit(
                train_df,
                feature_columns=feature_columns,
                label_column="label_class",
            )
            regime_model = RegimeClassifierModel(random_state=2026).fit(
                train_df,
                feature_columns=feature_columns,
                label_column="regime_label",
            )
            mfe_mae_model, mfe_mae_kind = _train_mfe_mae_or_skip(
                train_df,
                feature_columns=feature_columns,
                random_state=2026,
            )

            trade_top = _tag_top_feature_importance(
                trade_model.get_top_feature_importance(
                    train_df,
                    feature_columns=feature_columns,
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
                    feature_columns=feature_columns,
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
                        "feature": feature_columns[:10],
                        "importance": [0.0] * min(10, len(feature_columns)),
                    }
                )
            else:
                mfe_base = mfe_mae_model.get_top_feature_importance(
                    feature_columns=feature_columns,
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

            signal_dir = model_root / _safe_name(signal_type_key) / f"window_{win.window_id:02d}"
            trade_model.save(signal_dir / "trade_filter.joblib")
            regime_model.save(signal_dir / "regime_classifier.joblib")
            if mfe_mae_model is not None:
                mfe_mae_model.save(signal_dir / "mfe_mae.joblib")

            for split_name, split_df in (("train", train_df), ("valid", valid_df), ("test", test_df)):
                if split_df.empty:
                    continue
                trade_metrics = evaluate_trade_filter_model(
                    trade_model,
                    split_df,
                    feature_columns=feature_columns,
                    label_column="label_class",
                )
                regime_metrics = evaluate_regime_model(
                    regime_model,
                    split_df,
                    feature_columns=feature_columns,
                    label_column="regime_label",
                )
                nan_mfe_metrics: dict[str, float] = {
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
                            feature_columns=feature_columns,
                            mfe_column="future_mfe_atr",
                            mae_column="future_mae_atr",
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
                                **mfe_metrics,
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
                        "signal_type",
                        "side",
                        "candidate_status",
                        "is_executed",
                        "label_class",
                        "regime_label",
                        "future_mfe_atr",
                        "future_mae_atr",
                    )
                    if c in pred_base.columns
                ]
            ].copy()
            pred_df["model_signal_type"] = signal_type_key
            pred_df["window_id"] = win.window_id
            pred_df["pred_split"] = pred_split
            pred_df["trade_filter_prob"] = trade_model.predict_proba(pred_base, feature_columns=feature_columns)
            pred_df["pred_regime_label"] = regime_model.predict(pred_base, feature_columns=feature_columns)
            if mfe_mae_model is None:
                # B1 fix: 没有 executed 训练样本 → 预测列写 NaN
                pred_df["pred_mfe_atr"] = np.nan
                pred_df["pred_mae_atr"] = np.nan
            else:
                mfe_pred = mfe_mae_model.predict(pred_base, feature_columns=feature_columns)
                pred_df["pred_mfe_atr"] = mfe_pred["pred_mfe_atr"].to_numpy()
                pred_df["pred_mae_atr"] = mfe_pred["pred_mae_atr"].to_numpy()
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
                "auc",
                "accuracy",
                "precision",
                "recall",
                "f1",
                "macro_f1",
                "weighted_f1",
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
    metrics_df.to_csv(metrics_path, index=False, encoding="utf-8-sig")
    prediction_df.to_csv(prediction_path, index=False, encoding="utf-8-sig")
    top_feature_df.to_csv(top_feature_importance_path, index=False, encoding="utf-8-sig")
    decile_df = _build_last_oot_decile_table(prediction_df, bins=10)
    decile_df.to_csv(decile_path, index=False, encoding="utf-8-sig")

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
        f"- candidate_count: `{total_count}`",
        f"- negative_candidate_count: `{neg_count}`",
        f"- negative_ratio: `{ratio:.4f}`",
        # B12 fix: 改名 signal_frames_count，避免在 by_signal_type=False 时让读者误以为
        # signal_type 总数=1。signal_frames_count 含义是"实际进入训练循环的分组数"。
        f"- signal_frames_count: `{len(signal_frames)}`",
        "",
        "## Metrics",
        metrics_block,
        "",
        "## Last OOT Decile Returns",
        decile_block,
        "",
        f"- candidates_csv: `{candidate_path}`",
        f"- feature_table_csv: `{feature_table_path}`",
        f"- predictions_csv: `{prediction_path}`",
        f"- metrics_csv: `{metrics_path}`",
        f"- top10_feature_importance_csv: `{top_feature_importance_path}`",
        f"- last_oot_decile_csv: `{decile_path}`",
    ]
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    logger.info("model pipeline finished: %s", out_dir)
    return ModelPipelineResult(
        output_dir=out_dir,
        candidate_path=candidate_path,
        feature_table_path=feature_table_path,
        prediction_path=prediction_path,
        metrics_path=metrics_path,
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
    parser.add_argument("--max-walk-forward-windows", type=int, default=3)
    parser.add_argument(
        "--window-mode",
        default="expanding",
        choices=("expanding", "sliding"),
        help="walk-forward window mode (default expanding train, optionally sliding train)",
    )
    parser.add_argument("--by-signal-type", dest="by_signal_type", action="store_true")
    parser.add_argument("--no-by-signal-type", dest="by_signal_type", action="store_false")
    parser.set_defaults(by_signal_type=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    args = _parse_args(argv)
    intervals = _normalize_intervals(args.interval)
    output_root = Path(args.output_root).resolve() if args.output_root else None
    top_n = int(getattr(args, "top_n_symbols", 0))
    if top_n > 0:
        symbols_to_run = _load_top_n_symbols_from_ranking(
            Path(args.symbols_ranking_path),
            top_n=top_n,
        )
        logger.info(
            "top-n symbol mode enabled: top_n=%s ranking_path=%s loaded=%s",
            top_n,
            args.symbols_ranking_path,
            [s for s, _ in symbols_to_run],
        )
    else:
        symbols_to_run = [(str(args.symbol).upper(), str(args.exchange).upper() if args.exchange else None)]

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
