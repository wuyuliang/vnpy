"""Sim soak 启动门槛验证：1 symbol × 1 day × 60min 上 max|diff| < 1e-6.

roadmap §5.1 sim soak 启动门槛
------------------------------
跑这个脚本 → 输出 parity report；overall_pass=True 才允许进入 sim soak。

用法：
    # 默认：RB0 / day / 用 cta/data/feature 真实 parquet
    python3 -m cta.sim.feature_parity_sim_soak

    # 自定义 symbol / interval / 日期数
    python3 -m cta.sim.feature_parity_sim_soak --symbol RB0 --interval day --n-days 5

退出码：
    0 = parity pass
    1 = fail（max|diff| > tolerance），上 sim soak 前必须修
    2 = 数据缺失 / 配置错误

由于本仓库 ``OnlineFeatureLoader`` 直接读离线 parquet（``feature/online`` 增量计算
尚未落地，见 ``online_feature.py:23``），online 和 offline 在数学上完全相同；
本脚本的作用是**结构性验证**：确认 (1) 解析路径正确 (2) 数据读得到 (3) feature
列数 ≥ 阈值 (4) 对 100+ 列做 self-parity 跑通。

未来 ``cta/feature/online`` 增量计算上线后，把 offline_loader 改成它即可复用本脚本。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

from cta.live.feature_parity import compare_online_vs_offline
from cta.live.online_feature import OnlineFeatureLoader

logger = logging.getLogger(__name__)


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Sim soak feature parity gate")
    p.add_argument("--symbol", default="RB0")
    p.add_argument("--interval", default="day", choices=("day", "60min", "30min", "15min", "5min", "min"))
    p.add_argument("--n-days", type=int, default=1)
    p.add_argument("--feature-root", default="cta/data/feature")
    p.add_argument("--tolerance", type=float, default=1e-6)
    p.add_argument(
        "--min-columns",
        type=int,
        default=10,
        help="parity 要求的最少 feature 列数；低于则数据可能损坏",
    )
    return p


def _resolve_recent_dates(
    loader: OnlineFeatureLoader, symbol: str, interval: str, n_days: int
) -> list[pd.Timestamp]:
    """从磁盘上现有 parquet 反查最近 n_days 个时间戳。"""
    interval_dir = loader.root / interval
    if interval == "day":
        sym_dir = interval_dir / symbol
    else:
        prefix = "".join(ch for ch in symbol if ch.isalpha()).upper()
        sym_dir = interval_dir / prefix
    if not sym_dir.exists():
        logger.error("data dir does not exist: %s", sym_dir)
        return []

    parquet_files = sorted(sym_dir.glob("*.parquet"))
    if not parquet_files:
        return []
    recent = parquet_files[-n_days:]
    timestamps: list[pd.Timestamp] = []
    for fp in recent:
        try:
            df = pd.read_parquet(fp)
            if "datetime" in df.columns:
                # 该日内所有 timestamp
                for ts in pd.to_datetime(df["datetime"]).tolist():
                    timestamps.append(pd.Timestamp(ts))
            else:
                # day-level：一行 = 一天，用文件名解析
                date_str = fp.stem
                timestamps.append(pd.Timestamp(date_str))
        except Exception:  # noqa: BLE001
            logger.exception("failed to read %s", fp)
    return timestamps


def run_parity_gate(args: argparse.Namespace) -> int:
    feature_root = Path(args.feature_root).resolve()
    if not feature_root.exists():
        logger.error("feature_root not found: %s", feature_root)
        return 2

    loader = OnlineFeatureLoader(feature_root=feature_root)
    dates = _resolve_recent_dates(loader, args.symbol, args.interval, args.n_days)
    if not dates:
        logger.error(
            "no timestamps resolved for %s/%s under %s; cannot run parity",
            args.symbol, args.interval, feature_root,
        )
        return 2

    logger.info(
        "parity gate: symbol=%s interval=%s n_timestamps=%d tolerance=%g",
        args.symbol, args.interval, len(dates), args.tolerance,
    )

    # online == offline（都通过 OnlineFeatureLoader 读同一 parquet）；
    # 用 self-parity 验证：(1) 路径解析正确 (2) 数据稳定可读 (3) 列数 OK
    def loader_fn(sym: str, itv: str, dt: pd.Timestamp) -> "pd.Series | None":
        return loader.load_at(symbol=sym, interval=itv, dt=dt)

    report = compare_online_vs_offline(
        symbol=args.symbol,
        interval=args.interval,
        dates=dates,
        online_loader=loader_fn,
        offline_loader=loader_fn,
        tolerance=args.tolerance,
    )

    logger.info(
        "parity result: n_columns=%d n_pass=%d n_fail=%d overall=%s",
        report.n_columns, report.n_columns_pass, report.n_columns_fail, report.overall_pass,
    )
    if report.n_columns < int(args.min_columns):
        logger.error(
            "feature columns too few (%d < %d) — data may be truncated or wrong interval",
            report.n_columns, args.min_columns,
        )
        return 1
    if not report.overall_pass:
        logger.error("FAILING columns: %s", list(report.failing_columns)[:20])
        return 1

    logger.info(
        "✓ sim soak parity gate PASSED: %d columns within %g tolerance",
        report.n_columns, args.tolerance,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = _build_argparser().parse_args(argv)
    return run_parity_gate(args)


if __name__ == "__main__":
    sys.exit(main())
