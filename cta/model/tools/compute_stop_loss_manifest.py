"""按 (cluster, interval) 从历史 OHLC 计算 `intrabar_stop_loss_pct` 推荐值。

设计动机
--------
全局单一止损率（cta/config/model_oot_eval_config.py:intrabar_stop_loss_pct=0.01）
对 day 级别 A 股 INDEX/BLACK 等高波动 cluster 过紧——day 内 (high-low)/close 中位数
就在 1.5%-2.0%，1% 必被日内噪音打穿。2026-05-19 OOT 诊断显示 INDEX day 13 笔
全在 1% 被 9.24 暴涨打穿 hard_stop。

本脚本扫描 `cta/data/origin/{interval}/<symbol>.csv`，按 cluster 聚合
**截止指定 cutoff 日期之前**（默认 2024-01-01）的 daily true_range/close 分布，
取 P90 作为推荐止损率（即覆盖 90% 正常日波动，10% 极端日才触发止损）。

输出可直接粘贴进 OotEvaluationConfig.intrabar_stop_loss_pct_by_cluster_interval。

CLI 用法
--------
``python -m cta.model.tools.compute_stop_loss_manifest \\
    --interval day --cutoff 2024-01-01 --quantile 0.90``

只读 `cta/data/origin/`，不修改任何 config。
"""
from __future__ import annotations

import argparse
import glob
import logging
import os

import numpy as np
import pandas as pd

from cta.config.symbol_cluster_config import infer_symbol_cluster

logger = logging.getLogger(__name__)

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _list_symbol_csvs(interval: str) -> list[str]:
    """枚举 cta/data/origin/{interval}/*.csv（day 是 flat；minute60 等可能在子目录）。"""
    flat_dir = os.path.join(REPO_ROOT, "cta", "data", "origin", interval)
    flat = sorted(glob.glob(os.path.join(flat_dir, "*.csv")))
    if flat:
        return flat
    # 兼容按 symbol 切目录的存储：cta/data/origin/minute60/RB/RB0_2024-01-01.csv
    return sorted(glob.glob(os.path.join(flat_dir, "*", "*.csv")))


def _symbol_from_path(p: str, interval: str) -> str:
    """从路径推断 symbol。flat：文件名去 .csv；nested：上一级目录 + 文件名前缀。"""
    rel = os.path.relpath(p, os.path.join(REPO_ROOT, "cta", "data", "origin", interval))
    parts = rel.split(os.sep)
    if len(parts) == 1:
        return parts[0].replace(".csv", "")
    return parts[-2] if parts[-2] else parts[-1].split("_")[0]


def compute_per_cluster_stop_pct(
    interval: str,
    cutoff: pd.Timestamp,
    quantile: float = 0.90,
    min_obs_per_cluster: int = 100,
    range_clip: tuple[float, float] = (0.0, 0.5),
) -> dict[str, dict[str, float]]:
    """返回 {cluster: {"n": int, "median": float, "p75": float, "p90": float, ...}}。"""
    pooled: dict[str, list[float]] = {}
    for fp in _list_symbol_csvs(interval):
        sym = _symbol_from_path(fp, interval)
        cluster = (infer_symbol_cluster(sym) or "").lower()
        if not cluster:
            continue
        try:
            df = pd.read_csv(fp, low_memory=False)
        except Exception as exc:
            logger.debug("skip %s: %s", fp, exc)
            continue
        if "datetime" not in df.columns:
            continue
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
        df = df.dropna(subset=["datetime"])
        df = df.loc[df["datetime"] < cutoff].copy()
        if len(df) < 100:
            continue
        if not {"high", "low", "close"}.issubset(df.columns):
            continue
        for c in ("high", "low", "close"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        rng = (df["high"] - df["low"]) / df["close"]
        rng = rng.replace([np.inf, -np.inf], np.nan).dropna()
        rng = rng.loc[(rng > range_clip[0]) & (rng < range_clip[1])]
        pooled.setdefault(cluster, []).extend(rng.tolist())

    out: dict[str, dict[str, float]] = {}
    for cluster, samples in pooled.items():
        if len(samples) < min_obs_per_cluster:
            continue
        arr = np.asarray(samples, dtype=float)
        out[cluster] = {
            "n": float(len(arr)),
            "median": float(np.median(arr)),
            "p75": float(np.quantile(arr, 0.75)),
            "p90": float(np.quantile(arr, 0.90)),
            "p95": float(np.quantile(arr, 0.95)),
            "p99": float(np.quantile(arr, 0.99)),
            "recommended": float(np.clip(np.quantile(arr, quantile), 0.005, 0.05)),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--interval", default="day", help="day / 60min / 30min / 15min / 5min / min")
    ap.add_argument("--cutoff", default="2024-01-01", help="只统计 datetime < cutoff 的样本")
    ap.add_argument("--quantile", type=float, default=0.90, help="选择的分位数（默认 P90）")
    ap.add_argument(
        "--min-obs-per-cluster",
        type=int,
        default=100,
        help="每个 cluster 至少需要的样本数才纳入推荐",
    )
    args = ap.parse_args()

    cutoff = pd.Timestamp(args.cutoff)
    stats = compute_per_cluster_stop_pct(
        interval=args.interval,
        cutoff=cutoff,
        quantile=float(args.quantile),
        min_obs_per_cluster=int(args.min_obs_per_cluster),
    )

    if not stats:
        print(f"no clusters with ≥{args.min_obs_per_cluster} samples for interval={args.interval}")
        return 1

    print(f"=== stop_loss_pct stats for interval={args.interval}, cutoff={args.cutoff} ===")
    print(f"{'cluster':<10s} {'n':>8s} {'median':>9s} {'P75':>9s} {'P90':>9s} {'P95':>9s} {'P99':>9s}")
    for cluster in sorted(stats):
        s = stats[cluster]
        print(
            f"{cluster:<10s} {int(s['n']):8d} "
            f"{s['median']:9.4f} {s['p75']:9.4f} {s['p90']:9.4f} {s['p95']:9.4f} {s['p99']:9.4f}"
        )

    print(f"\n=== recommended intrabar_stop_loss_pct_by_cluster_interval (quantile={args.quantile}) ===")
    print(f"intrabar_stop_loss_pct_by_cluster_interval = {{")
    for cluster in sorted(stats):
        print(f'    "{cluster}|{args.interval}": {stats[cluster]["recommended"]:.4f},')
    print("}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
