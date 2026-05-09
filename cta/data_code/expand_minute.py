"""主力品种分钟级数据批量扩展（薄包装 download_all + validate）。

设计目标
--------
当前 ``cta/data/origin/minute*/`` 仅 RB0 等少数品种较完整。本脚本一键扩展
至 ranking top-N 主力品种的全部分钟级，并在下载完成后自动跑 ``validate``
输出每品种的完整性概览。

用法
----
# 默认：top-10 主力 + minute60 + minute30
python3 -m cta.data_code.expand_minute --max-rank 10 --intervals minute60 minute30

# 指定品种：
python3 -m cta.data_code.expand_minute --only-symbols RB0 HC0 I0 \\
        --intervals minute minute5 minute60

# 仅校验，不下载：
python3 -m cta.data_code.expand_minute --validate-only --max-rank 20

依赖
----
TUSHARE_TOKEN 环境变量；详见 cta/data_code/futures_downloader.py。
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

import pandas as pd

from cta.data_code.futures_downloader import MINUTE_INTERVALS
from cta.data_code.validate import load_ranking, validate_symbols

logger = logging.getLogger("cta.data_code.expand_minute")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="批量扩展主力品种分钟级数据 + 校验")
    parser.add_argument("--max-rank", type=int, default=10)
    parser.add_argument("--only-symbols", nargs="*", default=None)
    parser.add_argument(
        "--intervals", nargs="+",
        default=["minute60"],
        choices=list(MINUTE_INTERVALS),
        help=f"频率列表（{list(MINUTE_INTERVALS)}）",
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--rate-limit", type=int, default=450)
    parser.add_argument("--validate-only", action="store_true", help="只跑校验不下载")
    parser.add_argument("--validate-out", type=str, default=None, help="校验结果 CSV 输出")
    args = parser.parse_args()

    pairs = load_ranking(args.max_rank, args.only_symbols)
    if not pairs:
        logger.warning("no symbols matched")
        return
    logger.info(f"target symbols: {[s for s, _ in pairs]}")
    logger.info(f"intervals     : {args.intervals}")

    if not args.validate_only:
        cmd = [
            sys.executable, "-m", "cta.data_code.download_all",
            "--intervals", *args.intervals,
            "--workers", str(args.workers),
            "--rate-limit", str(args.rate_limit),
        ]
        if args.max_rank:
            cmd += ["--max-rank", str(args.max_rank)]
        if args.only_symbols:
            cmd += ["--only-symbols", *args.only_symbols]
        logger.info(f"run: {' '.join(cmd)}")
        rc = subprocess.run(cmd, check=False).returncode
        if rc != 0:
            logger.error(f"download_all exit {rc}")
            sys.exit(rc)

    rows = []
    for itv in args.intervals:
        reports = validate_symbols(itv, pairs)
        rows.extend(r.to_row() for r in reports)
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    if args.validate_out:
        Path(args.validate_out).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(args.validate_out, index=False, encoding="utf-8-sig")
        logger.info(f"validate report -> {args.validate_out}")


if __name__ == "__main__":
    main()
