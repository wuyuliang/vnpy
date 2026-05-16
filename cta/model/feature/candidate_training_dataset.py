"""Candidate-event training dataset builder.

核心职责：
1. 把 baseline 生成的候选样本统一映射为 candidate_events 口径（含
   executed/filtered/blocked/not_triggered_market）。
2. 拼接候选特征（feature_*）与 vn.py 通用特征（generic_*）。
3. 把训练样本持久化到 ``cta/data/model_feature`` 目录，作为后续模型训练资产。

设计要点（与 candidate_vs_executed_samples.md 对齐）：
- ``candidate_id`` 一旦在 standardize 阶段生成就保持稳定，merge_asof 后再次
  归一化时不会重生成，保证 candidate_events.parquet 与 training_samples.parquet
  的主键集合一致；
- 机会质量标签（``is_good_opportunity`` / ``opportunity_class`` / ``opportunity_score``）
  在 ATR warmup（atr_warmed=0）或 mfe/mae 缺失时显式标记为 unknown（U），不再用
  ``fillna(0)`` 把"未知"伪装成"差"；
- ``future_return_atr`` 承载 baseline 的 ``future_pnl_atr`` 真实 horizon 收益，
  不再与 ``opportunity_score = mfe - 0.7*mae`` 混淆；
- 候选事件的虚拟入场价 ``entry_price_virtual`` 优先用 trigger（突破/触发价）
  而不是 stop_price（止损价）兜底；
- baseline ``not_triggered``（市场未触发）单独归类到 ``not_triggered_market``，
  与 ``blocked_by_execution``（执行规则阻断）严格区分，便于后续做归因分析。
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

from cta.config.baseline_skill_suite_config import (
    BASELINE_SIGNAL_TYPES,
    LABEL_MAE_PENALTY,
    LABEL_THRESHOLD,
    OPPORTUNITY_CLASS_A_BREAK,
    OPPORTUNITY_CLASS_B_BREAK,
)
from cta.config.skill_tight_range_breakout_config import CTA_ROOT, BacktestConfig
from cta.model.feature.training_feature_builder import (
    FEATURE_ROOT,
    build_training_feature_table,
)
from cta.strategy.baseline_skill_suite import (
    generate_candidate_opportunities,
    prepare_master_feature_frame,
)
from cta.strategy.skill_tight_range_backtest import (
    load_bars,
    normalize_interval,
    resolve_exchange,
)

logger = logging.getLogger(__name__)

MODEL_FEATURE_ROOT: Path = CTA_ROOT / "data" / "model_feature"
SYMBOLS_RANKING_PATH: Path = CTA_ROOT / "feature" / "symbols_research_ranking.csv"
DEFAULT_MACRO_FEATURE_PATH: Path = CTA_ROOT / "data" / "feature" / "macro" / "macro_daily.parquet"

# baseline candidate_status -> sample_status 映射。
# 注意：``not_triggered``（市场未触发）与 ``blocked_by_execution``（执行规则
# 阻断，例如夜盘 / 流动性 / 滑点）业务语义不同，必须分桶；前者归 ``not_triggered_market``。
_SAMPLE_STATUS_MAP: dict[str, str] = {
    "filled": "executed",
    "filtered": "filtered_by_rule",
    "not_triggered": "not_triggered_market",
}

_VALID_SAMPLE_STATUS: frozenset[str] = frozenset(
    {
        "executed",
        "filtered_by_rule",
        "blocked_by_risk",
        "blocked_by_capacity",
        "blocked_by_execution",
        "not_triggered_market",
    }
)

# (status -> 默认 block_reason)，仅在上游没给具体原因时兜底。
_STATUS_DEFAULT_BLOCK_REASON: dict[str, str] = {
    "filtered_by_rule": "filtered_by_rule",
    "blocked_by_risk": "risk_rule_blocked",
    "blocked_by_capacity": "capacity_blocked",
    "blocked_by_execution": "execution_rule_blocked",
    "not_triggered_market": "next_bar_not_triggered",
}

# candidate_events 表的稳定 schema；下游模型 schema 校验依赖此顺序。
_CORE_COLS: tuple[str, ...] = (
    "candidate_id",
    "symbol",
    "exchange",
    "interval",
    "timeframe",
    "datetime",
    "candidate_trade_date",
    "signal_datetime",
    "setup_type",
    "signal_type",
    "direction",
    "side",
    "candidate_flag",
    "sample_status",
    "block_reason",
    "filtered_reason",
    "trigger",
    "entry_price_virtual",
    "stop_price_virtual",
    "target_price_virtual",
    "entry_price",
    "stop_price",
    "atr_warmed",
    "atr_pct_at_entry",
    "label_class",
    "future_mfe_atr",
    "future_mae_atr",
    "future_return_atr",
    "opportunity_score",
    "is_good_opportunity",
    "opportunity_class",
    "executed_flag",
    "linked_trade_id",
    "risk_block_flag",
    "capacity_block_flag",
    "execution_block_flag",
    "candidate_status",
    "is_executed",
    "is_filtered",
    "is_triggered",
)

_PRIMARY_KEY_COLS: tuple[str, ...] = (
    "symbol",
    "interval",
    "datetime",
    "setup_type",
    "direction",
)


def _load_macro_feature_table(path: Path) -> pd.DataFrame:
    """Load macro feature table keyed by trade_date."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"macro feature file not found: {p}")
    df = pd.read_parquet(p)
    if "trade_date" in df.columns:
        out = df.copy()
        out["trade_date"] = pd.to_datetime(out["trade_date"], errors="coerce").dt.normalize()
    else:
        out = df.reset_index().rename(columns={"index": "trade_date"})
        if "trade_date" not in out.columns:
            raise KeyError(f"macro feature table missing trade_date: {p}")
        out["trade_date"] = pd.to_datetime(out["trade_date"], errors="coerce").dt.normalize()
    out = out.dropna(subset=["trade_date"]).copy()
    keep_cols = ["trade_date"] + [c for c in out.columns if str(c).startswith("macro_")]
    if len(keep_cols) <= 1:
        raise ValueError(f"macro feature table has no macro_* columns: {p}")
    out = out[keep_cols].drop_duplicates(subset=["trade_date"]).sort_values("trade_date")
    return out.reset_index(drop=True)


def _merge_macro_features(samples: pd.DataFrame, macro_df: pd.DataFrame) -> pd.DataFrame:
    """Join macro features by candidate trade date."""
    if samples.empty:
        return samples.copy()
    out = samples.copy()
    if "candidate_trade_date" in out.columns:
        out["_candidate_trade_date"] = pd.to_datetime(
            out["candidate_trade_date"], errors="coerce"
        ).dt.normalize()
    elif "datetime" in out.columns:
        out["_candidate_trade_date"] = pd.to_datetime(out["datetime"], errors="coerce").dt.normalize()
    else:
        return out
    merged = out.merge(
        macro_df,
        left_on="_candidate_trade_date",
        right_on="trade_date",
        how="left",
    )
    merged = merged.drop(columns=["_candidate_trade_date", "trade_date"], errors="ignore")
    return merged


@dataclass(frozen=True)
class CandidateTrainingDatasetResult:
    """Persisted dataset artifacts."""

    dataset_dir: Path
    candidate_events_parquet: Path
    training_samples_parquet: Path
    summary_parquet: Path


def _normalize_intervals(raw: Iterable[str]) -> tuple[str, ...]:
    """Normalize interval tokens into ordered, deduplicated tuple."""
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


def _load_top_n_symbols_from_ranking(
    ranking_path: Path,
    top_n: int,
) -> list[tuple[str, str | None]]:
    """Load top-N symbols ordered by research_rank from ranking csv.

    M2：与 model_pipeline._load_top_n_symbols_from_ranking 一致地接入
    symbol_disable_manifest 过滤，避免持续亏损品种污染训练集。
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

    from cta.config.symbol_disable import filter_out_disabled_pairs

    filtered = filter_out_disabled_pairs(all_pairs)
    picked = filtered[:n]
    if not picked:
        raise ValueError(
            f"no valid symbols loaded from ranking csv: {path}; "
            f"check symbol_disable_manifest.csv if you expect more"
        )
    return picked


def _resolve_run_exchange(
    exchange_from_rank: str | None,
    cli_exchange: str | None,
) -> str | None:
    if exchange_from_rank:
        return str(exchange_from_rank).upper()
    if cli_exchange:
        return str(cli_exchange).upper()
    return None


def _to_float_series(df: pd.DataFrame, column: str, fill_value: float = np.nan) -> pd.Series:
    if column not in df.columns:
        return pd.Series([fill_value] * len(df), index=df.index, dtype=float)
    return pd.to_numeric(df[column], errors="coerce")


def _build_candidate_id(df: pd.DataFrame) -> pd.Series:
    """Build natural primary key based on (symbol, interval, datetime, setup, direction).

    业务上 (symbol, interval, datetime, setup_type, direction) 应当唯一；如有重复
    说明上游 ETL 出问题，应该立刻报错而不是用 seq 兜底掩盖（参见 C7）。
    """
    dt = pd.to_datetime(df["datetime"], errors="coerce")
    if dt.isna().any():
        raise ValueError("candidate datetime contains NaT, cannot build primary key")
    dt_text = dt.dt.strftime("%Y%m%d%H%M%S")
    setup = (
        df["setup_type"]
        .astype(str)
        .str.lower()
        .str.replace(r"[^a-z0-9]+", "_", regex=True)
    )
    direction = (
        df["direction"]
        .astype(str)
        .str.lower()
        .str.replace(r"[^a-z0-9]+", "_", regex=True)
    )
    symbol = df["symbol"].astype(str).str.upper()
    interval = df["interval"].astype(str).str.lower()
    return symbol + "_" + interval + "_" + dt_text + "_" + setup + "_" + direction


def _assert_unique_primary_key(df: pd.DataFrame) -> None:
    dup_mask = df.duplicated(subset=list(_PRIMARY_KEY_COLS), keep=False)
    if dup_mask.any():
        sample = df.loc[dup_mask, list(_PRIMARY_KEY_COLS)].head(5).to_dict("records")
        raise ValueError(
            f"duplicate candidate primary key found: {int(dup_mask.sum())} rows "
            f"share the same (symbol, interval, datetime, setup_type, direction); "
            f"sample={sample}"
        )


def _normalize_sample_status(df: pd.DataFrame) -> pd.Series:
    if "sample_status" in df.columns:
        raw = df["sample_status"].fillna("").astype(str).str.strip().str.lower()
        raw = raw.replace({"nan": ""})
    elif "candidate_status" in df.columns:
        raw = (
            df["candidate_status"]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.lower()
            .replace({"nan": ""})
            .map(_SAMPLE_STATUS_MAP)
            .fillna("not_triggered_market")
        )
    else:
        raw = pd.Series(["executed"] * len(df), index=df.index)
    raw = raw.replace({"": "not_triggered_market"})
    return raw.where(raw.isin(_VALID_SAMPLE_STATUS), "not_triggered_market")


# D5 fix：parquet round-trip / pd.NA / Python None 都可能让 reason 字段
# 出现伪缺失字符串。统一在这里识别为""，让下游兜底分支生效。
_MISSING_REASON_TOKENS: dict[str, str] = {
    "nan": "",
    "<na>": "",
    "none": "",
    "null": "",
}


def _coerce_missing_reason(s: pd.Series) -> pd.Series:
    """Normalize a reason column so伪缺失 (NaN/pd.NA/None/'<NA>'/'None') → ''."""
    out = s.fillna("").astype(str).str.strip()
    return out.where(~out.str.lower().isin(_MISSING_REASON_TOKENS), "")


def _normalize_block_reason(df: pd.DataFrame, sample_status: pd.Series) -> pd.Series:
    """Resolve block_reason while treating NaN/string-'nan'/'<NA>'/'None' as missing
    (C6 + D5)。"""
    if "block_reason" in df.columns:
        reason = _coerce_missing_reason(df["block_reason"])
    else:
        reason = pd.Series([""] * len(df), index=df.index, dtype=str)

    if "filtered_reason" in df.columns:
        fr = _coerce_missing_reason(df["filtered_reason"])
        reason = reason.where(reason != "", fr)

    for status, default_reason in _STATUS_DEFAULT_BLOCK_REASON.items():
        mask = (sample_status == status) & (reason == "")
        reason.loc[mask] = default_reason
    reason.loc[sample_status == "executed"] = ""
    return reason


def _opportunity_class(score: pd.Series) -> pd.Series:
    """Bucket opportunity_score into A/B/C/D; U (unknown) is set by caller for NaN."""
    return pd.Series(
        np.select(
            [
                score >= OPPORTUNITY_CLASS_A_BREAK,
                score >= OPPORTUNITY_CLASS_B_BREAK,
                score >= LABEL_THRESHOLD,
            ],
            ["A", "B", "C"],
            default="D",  # D = below_threshold
        ),
        index=score.index,
    )


def _empty_candidate_events_frame() -> pd.DataFrame:
    """Return zero-row DataFrame with full candidate_events schema (C10)."""
    schema: dict[str, pd.Series] = {}
    for col in _CORE_COLS:
        if col in {"datetime", "signal_datetime"}:
            schema[col] = pd.Series([], dtype="datetime64[ns]")
        elif col in {
            "candidate_flag",
            "executed_flag",
            "risk_block_flag",
            "capacity_block_flag",
            "execution_block_flag",
            "is_executed",
            "is_filtered",
            "is_triggered",
            "atr_warmed",
            "label_class",
        }:
            schema[col] = pd.Series([], dtype="int64")
        elif col in {
            "trigger",
            "entry_price_virtual",
            "stop_price_virtual",
            "target_price_virtual",
            "entry_price",
            "stop_price",
            "future_mfe_atr",
            "future_mae_atr",
            "future_return_atr",
            "opportunity_score",
            "is_good_opportunity",
        }:
            schema[col] = pd.Series([], dtype="float64")
        else:
            schema[col] = pd.Series([], dtype="object")
    return pd.DataFrame(schema)


def standardize_candidate_events(candidate_df: pd.DataFrame) -> pd.DataFrame:
    """Normalize candidate table to candidate_events schema.

    说明：
    1. 保留输入中的 ``feature_*`` / ``generic_*`` 列，不破坏已有特征。
    2. 增加 candidate_vs_executed_samples.md 约定的核心字段。
    3. ``is_good_opportunity`` / ``opportunity_class`` / ``opportunity_score``
       仅看市场机会质量（与 ``executed_flag`` 解耦）；当 ATR warmup（atr_warmed=0）
       或 mfe/mae 缺失时显式置为 ``U`` / NaN，避免把"未知"伪装成"差"。
    4. ``future_return_atr`` 保留 baseline 的真实 horizon 收益（``future_pnl_atr``），
       与 opportunity_score 严格区分。
    5. ``direction`` / ``side`` 会被强制 ``str.lower()``，调用方应保证上游传入小写。
    6. 若 ``candidate_id`` 已经存在，会原样保留（保证 merge_asof 后再次归一化时
       主键稳定，参见 C1/C12）。
    """
    if candidate_df is None or len(candidate_df) == 0:
        return _empty_candidate_events_frame()
    if "datetime" not in candidate_df.columns:
        raise KeyError("candidate_df missing datetime")

    out = candidate_df.copy()
    out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
    out = out.dropna(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)
    if out.empty:
        return _empty_candidate_events_frame()
    out["candidate_trade_date"] = out["datetime"].dt.strftime("%Y-%m-%d")

    if "signal_datetime" in out.columns:
        out["signal_datetime"] = pd.to_datetime(out["signal_datetime"], errors="coerce")
    else:
        out["signal_datetime"] = out["datetime"]

    if "symbol" not in out.columns:
        out["symbol"] = "UNKNOWN"
    out["symbol"] = out["symbol"].astype(str).str.upper()

    if "exchange" not in out.columns:
        out["exchange"] = ""
    out["exchange"] = out["exchange"].astype(str).str.upper()

    if "interval" not in out.columns:
        out["interval"] = "day"
    out["interval"] = out["interval"].astype(str).str.lower()
    out["timeframe"] = out["interval"]

    # setup_type / signal_type 兜底（避免嵌套三元 + DataFrame.get 误读默认值）
    if "setup_type" in out.columns:
        out["setup_type"] = out["setup_type"].astype(str)
    elif "signal_type" in out.columns:
        out["setup_type"] = out["signal_type"].astype(str)
    else:
        out["setup_type"] = "unknown"
    if "signal_type" not in out.columns:
        out["signal_type"] = out["setup_type"]
    out["signal_type"] = out["signal_type"].astype(str)

    # direction / side 兜底
    if "direction" in out.columns:
        out["direction"] = out["direction"].astype(str).str.lower()
    elif "side" in out.columns:
        out["direction"] = out["side"].astype(str).str.lower()
    else:
        out["direction"] = "long"
    out["side"] = out["direction"]
    out["candidate_flag"] = 1

    sample_status = _normalize_sample_status(out)
    out["sample_status"] = sample_status
    # 反向回填 candidate_status，对外保持与 baseline 同步的两元 / 三元字符串。
    out["candidate_status"] = np.select(
        [
            sample_status == "executed",
            sample_status == "filtered_by_rule",
        ],
        ["filled", "filtered"],
        default="not_triggered",
    )
    out["block_reason"] = _normalize_block_reason(out, sample_status=sample_status)
    out["filtered_reason"] = out["block_reason"].where(
        sample_status == "filtered_by_rule", ""
    )

    trigger = _to_float_series(out, "trigger")
    entry_price = _to_float_series(out, "entry_price")
    stop_price = _to_float_series(out, "stop_price")
    feature_close = _to_float_series(out, "feature_close")
    close_px = _to_float_series(out, "close")

    # C2: entry_price_virtual 兜底优先级 entry_price → trigger → feature_close → close。
    # 不再用 stop_price（在 limit-order/ATR breakout 场景与 trigger 不同，会污染标签）。
    entry_virtual = entry_price.copy()
    entry_virtual = entry_virtual.where(entry_virtual.notna(), trigger)
    entry_virtual = entry_virtual.where(entry_virtual.notna(), feature_close)
    entry_virtual = entry_virtual.where(entry_virtual.notna(), close_px)
    out["entry_price_virtual"] = entry_virtual
    out["stop_price_virtual"] = stop_price
    if "target_price_virtual" not in out.columns:
        out["target_price_virtual"] = np.nan

    out["entry_price"] = entry_price
    out["stop_price"] = stop_price
    out["trigger"] = trigger
    atr_entry = _to_float_series(out, "feature_atr14")
    if atr_entry.isna().all():
        atr_entry = _to_float_series(out, "atr14")
    if atr_entry.isna().all():
        atr_entry = _to_float_series(out, "atr_14")
    out["atr_pct_at_entry"] = (atr_entry / entry_virtual.replace(0, np.nan)).astype(float)

    mfe = _to_float_series(out, "future_mfe_atr")
    mae = _to_float_series(out, "future_mae_atr")

    # atr_warmed=0（warmup 期）或 mfe/mae 缺失时，机会标签设为 unknown。
    # atr_warmed 缺失时按 1 处理（向后兼容旧数据）。
    if "atr_warmed" in out.columns:
        atr_warmed = pd.to_numeric(out["atr_warmed"], errors="coerce").fillna(1).astype(int)
    else:
        atr_warmed = pd.Series([1] * len(out), index=out.index, dtype=int)
    out["atr_warmed"] = atr_warmed

    label_known_mask = (atr_warmed == 1) & mfe.notna() & mae.notna()
    score = mfe - LABEL_MAE_PENALTY * mae
    score_masked = score.where(label_known_mask)

    out["future_mfe_atr"] = mfe.where(atr_warmed == 1)
    out["future_mae_atr"] = mae.where(atr_warmed == 1)
    out["opportunity_score"] = score_masked
    out["is_good_opportunity"] = np.where(
        label_known_mask & (score > LABEL_THRESHOLD), 1, 0
    ).astype(int)
    bucket = _opportunity_class(score_masked.fillna(LABEL_THRESHOLD - 1))
    out["opportunity_class"] = np.where(label_known_mask, bucket, "U")

    # C5: future_return_atr 承载真实 horizon 收益（baseline future_pnl_atr）。
    # D2 fix：二次调用兼容分支必须从 ``out`` 取（已 sort+reset_index），不能从
    # ``candidate_df`` 取——后者是原始索引，pandas 按 index alignment 赋值会把
    # row identity 对错（datetime 倒序传入时尤其明显）。
    if "future_pnl_atr" in out.columns:
        out["future_return_atr"] = pd.to_numeric(
            out["future_pnl_atr"], errors="coerce"
        ).where(label_known_mask)
    elif "future_return_atr" in out.columns:
        out["future_return_atr"] = pd.to_numeric(
            out["future_return_atr"], errors="coerce"
        )
    else:
        out["future_return_atr"] = np.nan

    if "label_class" not in out.columns:
        out["label_class"] = 0
    out["label_class"] = pd.to_numeric(out["label_class"], errors="coerce").fillna(0).astype(int)

    if "executed_flag" not in out.columns:
        if "is_executed" in out.columns:
            out["executed_flag"] = (
                pd.to_numeric(out["is_executed"], errors="coerce").fillna(0).astype(int)
            )
        else:
            out["executed_flag"] = (sample_status == "executed").astype(int)
    out["is_executed"] = (
        pd.to_numeric(out["executed_flag"], errors="coerce").fillna(0).astype(int)
    )

    out["risk_block_flag"] = (sample_status == "blocked_by_risk").astype(int)
    out["capacity_block_flag"] = (sample_status == "blocked_by_capacity").astype(int)
    out["execution_block_flag"] = (sample_status == "blocked_by_execution").astype(int)
    out["is_filtered"] = (sample_status == "filtered_by_rule").astype(int)
    out["is_triggered"] = (sample_status == "executed").astype(int)
    if "linked_trade_id" not in out.columns:
        out["linked_trade_id"] = ""

    # 主键唯一性必须在生成 candidate_id 之前校验；重复时直接 raise（C7）。
    _assert_unique_primary_key(out)

    # C1/C12: 如果输入已经带 candidate_id（merge_asof 后的二次归一化），不要重生成。
    if "candidate_id" in out.columns:
        existing_id = out["candidate_id"].astype(str).str.strip()
        needs_regen = existing_id.replace({"nan": ""}).eq("").any()
        if needs_regen:
            out["candidate_id"] = _build_candidate_id(out)
    else:
        out["candidate_id"] = _build_candidate_id(out)

    trailing = [c for c in out.columns if c not in _CORE_COLS]
    return out[list(_CORE_COLS) + trailing].copy()


def generate_candidate_events_from_baselines(
    symbol: str,
    exchange: str | None,
    interval: str,
    start_date: str,
    end_date: str,
    trade_side_mode: str = "both",
    signal_types: tuple[str, ...] = BASELINE_SIGNAL_TYPES,
    horizon_bars: int = 20,
) -> pd.DataFrame:
    """Generate candidate samples from baseline rule strategies."""
    sym = str(symbol).upper()
    interval_norm = normalize_interval(interval)
    bcfg = BacktestConfig(interval=interval_norm)
    ex = str(exchange).upper() if exchange else resolve_exchange(sym, bcfg.symbols_list_path)
    bars = load_bars(sym, bcfg, start_date, end_date, exchange=ex)
    frame = prepare_master_feature_frame(bars, interval=interval_norm)

    parts: list[pd.DataFrame] = []
    for signal_type in signal_types:
        cand = generate_candidate_opportunities(
            frame=frame,
            symbol=sym,
            exchange=ex,
            interval=interval_norm,
            signal_type=str(signal_type),
            horizon_bars=horizon_bars,
            trade_side_mode=trade_side_mode,
        )
        if not cand.empty:
            parts.append(cand)
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts, axis=0, ignore_index=True)
    return out.sort_values("datetime").reset_index(drop=True)


def build_and_save_candidate_training_dataset(
    candidate_df: pd.DataFrame,
    symbol: str,
    interval: str,
    output_root: Path = MODEL_FEATURE_ROOT,
    feature_root: Path = FEATURE_ROOT,
    run_tag: str | None = None,
    generic_columns: Iterable[str] | None = None,
    enable_macro_features: bool = True,
    macro_feature_path: Path = DEFAULT_MACRO_FEATURE_PATH,
) -> CandidateTrainingDatasetResult:
    """Build and persist standardized candidate-events + merged training samples."""
    sym = str(symbol).upper()
    interval_norm = normalize_interval(interval)
    tag = str(run_tag).strip() if run_tag else pd.Timestamp.now().strftime("%Y%m%d")
    dataset_dir = output_root / interval_norm / sym / tag
    dataset_dir.mkdir(parents=True, exist_ok=True)

    standardized = standardize_candidate_events(candidate_df)
    if standardized.empty:
        # C10: 即便空也写出 schema 完整的 parquet，下游读取不会缺列。
        merged = standardized.copy()
    else:
        merged_raw = build_training_feature_table(
            candidate_df=standardized,
            symbol=sym,
            interval=interval_norm,
            feature_root=feature_root,
            generic_columns=generic_columns,
        )
        # C12: merge_asof 重排了行顺序，再走一次 standardize 仅用于 schema 整理；
        # 由于 candidate_id 已经存在，_build_candidate_id 不会被再次触发 → 主键稳定。
        merged = standardize_candidate_events(merged_raw)
        if enable_macro_features:
            try:
                macro_df = _load_macro_feature_table(macro_feature_path)
                merged = _merge_macro_features(merged, macro_df)
            except Exception as exc:  # noqa: BLE001
                logger.warning("skip macro feature join: %s", exc)

    candidate_events_parquet = dataset_dir / f"{tag}_{sym}_{interval_norm}_candidate_events.parquet"
    training_samples_parquet = dataset_dir / f"{tag}_{sym}_{interval_norm}_training_samples.parquet"
    summary_parquet = dataset_dir / f"{tag}_{sym}_{interval_norm}_dataset_summary.parquet"

    standardized.to_parquet(candidate_events_parquet, index=False)
    merged.to_parquet(training_samples_parquet, index=False)

    if standardized.empty:
        summary_rows: list[dict[str, object]] = [
            {
                "symbol": sym,
                "interval": interval_norm,
                "total_candidates": 0,
                "executed_count": 0,
                "filtered_count": 0,
                "blocked_risk_count": 0,
                "blocked_capacity_count": 0,
                "blocked_execution_count": 0,
                "not_triggered_market_count": 0,
                "good_opportunity_count": 0,
                "unknown_opportunity_count": 0,
                "generic_feature_columns": 0,
                "candidate_events_parquet": str(candidate_events_parquet),
                "training_samples_parquet": str(training_samples_parquet),
            }
        ]
    else:
        summary_rows = [
            {
                "symbol": sym,
                "interval": interval_norm,
                "total_candidates": int(len(standardized)),
                "executed_count": int((standardized["sample_status"] == "executed").sum()),
                "filtered_count": int(
                    (standardized["sample_status"] == "filtered_by_rule").sum()
                ),
                "blocked_risk_count": int(
                    (standardized["sample_status"] == "blocked_by_risk").sum()
                ),
                "blocked_capacity_count": int(
                    (standardized["sample_status"] == "blocked_by_capacity").sum()
                ),
                "blocked_execution_count": int(
                    (standardized["sample_status"] == "blocked_by_execution").sum()
                ),
                "not_triggered_market_count": int(
                    (standardized["sample_status"] == "not_triggered_market").sum()
                ),
                "good_opportunity_count": int(
                    (standardized["is_good_opportunity"] == 1).sum()
                ),
                "unknown_opportunity_count": int(
                    (standardized["opportunity_class"] == "U").sum()
                ),
                "generic_feature_columns": int(
                    len([c for c in merged.columns if c.startswith("generic_")])
                ),
                "candidate_events_parquet": str(candidate_events_parquet),
                "training_samples_parquet": str(training_samples_parquet),
            }
        ]
    pd.DataFrame(summary_rows).to_parquet(summary_parquet, index=False)

    logger.info("candidate dataset saved: %s", dataset_dir)
    return CandidateTrainingDatasetResult(
        dataset_dir=dataset_dir,
        candidate_events_parquet=candidate_events_parquet,
        training_samples_parquet=training_samples_parquet,
        summary_parquet=summary_parquet,
    )


def generate_and_save_candidate_training_dataset(
    symbol: str,
    exchange: str | None,
    interval: str,
    start_date: str,
    end_date: str,
    trade_side_mode: str = "both",
    signal_types: tuple[str, ...] = BASELINE_SIGNAL_TYPES,
    horizon_bars: int = 20,
    output_root: Path = MODEL_FEATURE_ROOT,
    feature_root: Path = FEATURE_ROOT,
    run_tag: str | None = None,
    generic_columns: Iterable[str] | None = None,
    enable_macro_features: bool = True,
    macro_feature_path: Path = DEFAULT_MACRO_FEATURE_PATH,
) -> CandidateTrainingDatasetResult:
    """One-stop API: generate candidate events from baselines and persist dataset."""
    candidate_df = generate_candidate_events_from_baselines(
        symbol=symbol,
        exchange=exchange,
        interval=interval,
        start_date=start_date,
        end_date=end_date,
        trade_side_mode=trade_side_mode,
        signal_types=signal_types,
        horizon_bars=horizon_bars,
    )
    return build_and_save_candidate_training_dataset(
        candidate_df=candidate_df,
        symbol=symbol,
        interval=interval,
        output_root=output_root,
        feature_root=feature_root,
        run_tag=run_tag,
        generic_columns=generic_columns,
        enable_macro_features=enable_macro_features,
        macro_feature_path=macro_feature_path,
    )


def generate_and_save_candidate_training_dataset_multi(
    symbol: str,
    exchange: str | None,
    intervals: Sequence[str],
    start_date: str,
    end_date: str,
    trade_side_mode: str = "both",
    signal_types: tuple[str, ...] = BASELINE_SIGNAL_TYPES,
    horizon_bars: int = 20,
    output_root: Path = MODEL_FEATURE_ROOT,
    feature_root: Path = FEATURE_ROOT,
    run_tag: str | None = None,
    generic_columns: Iterable[str] | None = None,
    enable_macro_features: bool = True,
    macro_feature_path: Path = DEFAULT_MACRO_FEATURE_PATH,
) -> list[CandidateTrainingDatasetResult]:
    """Run candidate dataset generation for multiple intervals."""
    interval_tuple = _normalize_intervals(intervals)
    results: list[CandidateTrainingDatasetResult] = []
    for idx, interval in enumerate(interval_tuple, start=1):
        logger.info(
            "[%d/%d] running candidate dataset generation for symbol=%s interval=%s",
            idx,
            len(interval_tuple),
            symbol,
            interval,
        )
        try:
            result = generate_and_save_candidate_training_dataset(
                symbol=symbol,
                exchange=exchange,
                interval=interval,
                start_date=start_date,
                end_date=end_date,
                trade_side_mode=trade_side_mode,
                signal_types=signal_types,
                horizon_bars=horizon_bars,
                output_root=output_root,
                feature_root=feature_root,
                run_tag=run_tag,
                generic_columns=generic_columns,
                enable_macro_features=enable_macro_features,
                macro_feature_path=macro_feature_path,
            )
        except Exception:
            logger.exception("candidate dataset generation failed for interval=%s", interval)
            continue
        results.append(result)
    return results


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build candidate-event training dataset")
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
    parser.add_argument("--horizon-bars", type=int, default=20)
    parser.add_argument("--run-tag", default=None)
    parser.add_argument("--output-root", default=str(MODEL_FEATURE_ROOT))
    parser.add_argument("--feature-root", default=str(FEATURE_ROOT))
    parser.add_argument("--signal-types", default=",".join(BASELINE_SIGNAL_TYPES))
    parser.add_argument(
        "--macro-feature-path",
        default=str(DEFAULT_MACRO_FEATURE_PATH),
        help="macro parquet path (default cta/data/feature/macro/macro_daily.parquet)",
    )
    parser.add_argument(
        "--enable-macro-features",
        dest="enable_macro_features",
        action="store_true",
        default=True,
        help="join macro_* features into training samples (default: enabled)",
    )
    parser.add_argument(
        "--disable-macro-features",
        dest="enable_macro_features",
        action="store_false",
        help="disable macro feature join",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    # P2.2：候选样本生成是整个 model pipeline 的最上游，也需要可复现的 RNG。
    from cta.utils.random_seed import seed_all_from_env

    used_seed = seed_all_from_env("CTA_GLOBAL_SEED")
    if used_seed is not None:
        logger.info("candidate_training_dataset: seeded global RNG from CTA_GLOBAL_SEED=%s", used_seed)
    args = _parse_args(argv)
    intervals = _normalize_intervals(args.interval)
    signal_types = tuple(s.strip() for s in str(args.signal_types).split(",") if s.strip())
    output_root = Path(args.output_root).resolve()
    feature_root = Path(args.feature_root).resolve()
    macro_feature_path = Path(args.macro_feature_path).resolve()

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
        run_exchange = _resolve_run_exchange(exchange_from_rank, args.exchange)
        logger.info(
            "[%d/%d] run symbol=%s exchange=%s intervals=%s",
            sidx,
            len(symbols_to_run),
            symbol,
            run_exchange,
            list(intervals),
        )
        results = generate_and_save_candidate_training_dataset_multi(
            symbol=symbol,
            exchange=run_exchange,
            intervals=intervals,
            start_date=args.start,
            end_date=args.end,
            trade_side_mode=args.trade_side_mode,
            signal_types=signal_types,
            horizon_bars=int(args.horizon_bars),
            run_tag=args.run_tag,
            output_root=output_root,
            feature_root=feature_root,
            enable_macro_features=bool(args.enable_macro_features),
            macro_feature_path=macro_feature_path,
        )
        for interval, result in zip(intervals, results):
            logger.info("[%s][%s] candidate_events: %s", symbol, interval, result.candidate_events_parquet)
            logger.info("[%s][%s] training_samples: %s", symbol, interval, result.training_samples_parquet)
            logger.info("[%s][%s] summary: %s", symbol, interval, result.summary_parquet)


if __name__ == "__main__":
    main()


__all__ = [
    "MODEL_FEATURE_ROOT",
    "CandidateTrainingDatasetResult",
    "standardize_candidate_events",
    "generate_candidate_events_from_baselines",
    "build_and_save_candidate_training_dataset",
    "generate_and_save_candidate_training_dataset",
    "generate_and_save_candidate_training_dataset_multi",
]
