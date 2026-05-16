"""Generate a baseline causality_manifest.csv from existing generic feature parquets.

P1.5：当前手写的 manifest 只有 ~13 行，但磁盘 generic 特征 ~400 列。本脚本扫描
``cta/data/feature/<interval>/<symbol>/*.parquet`` 的列名，为每个 generic_/feature_
列写入一个 baseline 行（``causal=1, reason="auto_seed_needs_review"``），让人后续在
原 manifest 基础上**人工把可疑列标 0**，而不是从零起步。

用法
----
    python -m cta.model.tools.generate_causality_manifest_seed \
        --interval day --symbol A0 \
        --out cta/feature/causality_manifest_seed.csv

不会覆盖已有 manifest 行；只追加未在 manifest 中的列。
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Iterable

import pandas as pd

from cta.config.skill_tight_range_breakout_config import CTA_ROOT
from cta.strategy.skill_tight_range_backtest import normalize_interval

logger = logging.getLogger(__name__)

FEATURE_ROOT: Path = CTA_ROOT / "data" / "feature"
CAUSALITY_MANIFEST_PATH: Path = CTA_ROOT / "feature" / "causality_manifest.csv"


def _load_existing_manifest_lines(path: Path) -> tuple[set[str], list[dict[str, str]]]:
    if not path.exists():
        return set(), []
    try:
        df = pd.read_csv(path, encoding="utf-8-sig")
    except Exception:
        return set(), []
    seen: set[str] = set()
    rows: list[dict[str, str]] = []
    for _, r in df.iterrows():
        name = str(r.get("feature", "")).strip()
        if not name:
            continue
        seen.add(name.lower())
        rows.append(
            {
                "feature": name,
                "causal": str(r.get("causal", 1)),
                "reason": str(r.get("reason", "")),
            }
        )
    return seen, rows


def _scan_generic_columns(symbol_dir: Path) -> set[str]:
    cols: set[str] = set()
    for fp in sorted(symbol_dir.glob("*.parquet"))[:1]:  # 单文件足以拿到列 schema
        try:
            df = pd.read_parquet(fp)
        except Exception:
            continue
        for c in df.columns:
            n = str(c)
            if n in {"datetime", "open", "high", "low", "close", "volume", "open_interest", "turnover"}:
                continue
            cols.add(n)
    return cols


def generate(
    interval: str,
    symbols: Iterable[str],
    out_path: Path,
    *,
    feature_root: Path = FEATURE_ROOT,
    existing_manifest_path: Path = CAUSALITY_MANIFEST_PATH,
) -> Path:
    seen, existing_rows = _load_existing_manifest_lines(existing_manifest_path)
    interval_norm = normalize_interval(interval)
    all_cols: set[str] = set()
    for sym in symbols:
        sym_dir = feature_root / interval_norm / str(sym).upper()
        if not sym_dir.exists():
            logger.warning("symbol dir missing: %s", sym_dir)
            continue
        all_cols |= _scan_generic_columns(sym_dir)

    new_rows: list[dict[str, str]] = list(existing_rows)
    added = 0
    for c in sorted(all_cols):
        # generic feature 在 training_feature_builder 里加 generic_ 前缀；
        # 这里同时落 raw + 带前缀两版，下游 manifest 匹配统一 lower
        for variant in {c, f"generic_{c}", f"feature_{c}"}:
            v_lower = variant.lower()
            if v_lower in seen:
                continue
            seen.add(v_lower)
            new_rows.append(
                {"feature": variant, "causal": "1", "reason": "auto_seed_needs_review"}
            )
            added += 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(new_rows, columns=["feature", "causal", "reason"]).to_csv(
        out_path, index=False, encoding="utf-8-sig"
    )
    logger.info("manifest seed written: %s (added %d rows, total %d)", out_path, added, len(new_rows))
    return out_path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed baseline causality_manifest.csv")
    p.add_argument("--interval", default="day")
    p.add_argument("--symbol", nargs="+", required=True, help="symbols to scan")
    p.add_argument(
        "--feature-root", default=str(FEATURE_ROOT), help=f"feature parquet root (default {FEATURE_ROOT})"
    )
    p.add_argument(
        "--existing-manifest",
        default=str(CAUSALITY_MANIFEST_PATH),
        help="existing manifest path to extend (default cta/feature/causality_manifest.csv)",
    )
    p.add_argument(
        "--out",
        default=str(CTA_ROOT / "feature" / "causality_manifest_seed.csv"),
        help="output csv path",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    args = _parse_args(argv)
    generate(
        interval=str(args.interval),
        symbols=[str(s).upper() for s in args.symbol],
        out_path=Path(args.out),
        feature_root=Path(args.feature_root),
        existing_manifest_path=Path(args.existing_manifest),
    )


__all__ = ["generate", "main"]


if __name__ == "__main__":
    main()
