"""Symbol disable manifest loader / filter.

M2: 统一管理"应该从训练 / OOT 评估里剔除的品种"，例如：
  - persistent_loss: 多次 OOT 持续负 net_pnl 的品种（人工 / 自动标注）
  - cross_source_mismatch: cross_source_audit 标出的源不一致品种
  - liquidity_too_low: 流动性不足无法实盘交易的品种
  - manual: 人工 ad-hoc 禁用

每种原因都用一个 reason tag，便于：
  - 灰度恢复（"先把 cross_source_mismatch 类恢复，看效果"）
  - 审计（"为什么 AL0 没在训练集里？"）

manifest schema：``cta/feature/symbol_disable_manifest.csv``
    symbol, reason, source, disabled_at, notes
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

import pandas as pd

from cta.config.skill_tight_range_breakout_config import CTA_ROOT

logger = logging.getLogger(__name__)

SYMBOL_DISABLE_MANIFEST_PATH: Path = CTA_ROOT / "feature" / "symbol_disable_manifest.csv"


def load_disabled_symbols(
    *,
    manifest_path: Path = SYMBOL_DISABLE_MANIFEST_PATH,
    reasons: Iterable[str] | None = None,
) -> set[str]:
    """Load uppercased symbol set from disable manifest.

    Args:
        manifest_path: manifest csv path. 不存在或为空时返回空集合（fail-open，
            避免因为运维忘了创建 manifest 而把所有 symbol 误杀）。
        reasons: 仅过滤这些 reason 的行；None 表示用全部 reason。

    Returns:
        uppercased symbol set; 若 manifest 缺失返回 ``set()``。
    """
    path = Path(manifest_path)
    if not path.exists():
        logger.info("symbol_disable_manifest not found at %s — no symbols filtered", path)
        return set()
    try:
        df = pd.read_csv(path, encoding="utf-8-sig")
    except FileNotFoundError:
        logger.info("symbol_disable_manifest not found at %s — no symbols filtered", path)
        return set()
    except pd.errors.EmptyDataError:
        logger.warning("symbol_disable_manifest is empty at %s", path)
        return set()
    except pd.errors.ParserError as exc:
        logger.error("symbol_disable_manifest parse failed at %s: %s", path, exc)
        raise
    if df.empty or "symbol" not in df.columns:
        return set()
    if reasons is not None:
        wanted = {str(r).strip().lower() for r in reasons if str(r).strip()}
        if "reason" in df.columns:
            df = df.loc[df["reason"].astype(str).str.strip().str.lower().isin(wanted)]
        else:
            logger.warning("symbol_disable_manifest %s missing reason column; reasons filter ignored", path)
    out: set[str] = set()
    for v in df["symbol"].astype(str).tolist():
        s = v.strip().upper()
        if s:
            out.add(s)
    return out


def filter_out_disabled_symbols(
    symbols: Iterable[str],
    *,
    manifest_path: Path = SYMBOL_DISABLE_MANIFEST_PATH,
    reasons: Iterable[str] | None = None,
) -> list[str]:
    """Return uppercased symbol list with disabled ones removed (order preserved)."""
    disabled = load_disabled_symbols(manifest_path=manifest_path, reasons=reasons)
    if not disabled:
        return [str(s).upper() for s in symbols if str(s).strip()]
    out: list[str] = []
    dropped: list[str] = []
    for s in symbols:
        u = str(s).upper().strip()
        if not u:
            continue
        if u in disabled:
            dropped.append(u)
            continue
        out.append(u)
    if dropped:
        logger.warning(
            "symbol_disable_manifest dropped %d/%d symbols: %s",
            len(dropped),
            len(dropped) + len(out),
            dropped,
        )
    return out


def filter_out_disabled_pairs(
    pairs: Iterable[tuple[str, str | None]],
    *,
    manifest_path: Path = SYMBOL_DISABLE_MANIFEST_PATH,
    reasons: Iterable[str] | None = None,
) -> list[tuple[str, str | None]]:
    """Same as ``filter_out_disabled_symbols`` but preserves exchange pairs."""
    disabled = load_disabled_symbols(manifest_path=manifest_path, reasons=reasons)
    if not disabled:
        return [(str(s).upper(), ex) for s, ex in pairs if str(s).strip()]
    out: list[tuple[str, str | None]] = []
    dropped: list[str] = []
    for s, ex in pairs:
        u = str(s).upper().strip()
        if not u:
            continue
        if u in disabled:
            dropped.append(u)
            continue
        out.append((u, ex))
    if dropped:
        logger.warning(
            "symbol_disable_manifest dropped %d/%d pool pairs: %s",
            len(dropped),
            len(dropped) + len(out),
            dropped,
        )
    return out


def mask_disabled_rows(
    df: pd.DataFrame,
    *,
    symbol_column: str = "symbol",
    manifest_path: Path = SYMBOL_DISABLE_MANIFEST_PATH,
    reasons: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Return df with disabled-symbol rows removed.

    Used at OOT evaluation entry so any leftover row from disabled symbols in
    the prediction table is dropped before resource simulation.
    """
    if df is None or df.empty or symbol_column not in df.columns:
        return df
    disabled = load_disabled_symbols(manifest_path=manifest_path, reasons=reasons)
    if not disabled:
        return df
    sym_norm = df[symbol_column].astype(str).str.upper().str.strip()
    keep_mask = ~sym_norm.isin(disabled)
    dropped_count = int((~keep_mask).sum())
    if dropped_count > 0:
        logger.info(
            "symbol_disable_manifest masked %d/%d rows (symbols=%s)",
            dropped_count,
            len(df),
            sorted({s for s in sym_norm[~keep_mask].unique() if s}),
        )
    return df.loc[keep_mask].copy().reset_index(drop=True)


__all__ = [
    "SYMBOL_DISABLE_MANIFEST_PATH",
    "load_disabled_symbols",
    "filter_out_disabled_symbols",
    "filter_out_disabled_pairs",
    "mask_disabled_rows",
]
