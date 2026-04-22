#!/usr/bin/env python3
"""
特征生成启动脚本（兼容旧 CLI）

** 已迁移提示 **
-----------------
新入口: `python3 -m cta.feature.run_all_features`
  - 输出布局: `cta/data/feature/{interval}/{SYMBOL}/{YYYY-MM-DD}.parquet`
  - 兼容 `cta.feature.feature_loader`（按日分片读取）
  - 支持全频率 day/minute/minute5/minute15/minute30/minute60 + 多进程 + 断点续跑

本脚本保留是为了不打断老的调用方，功能等价于：
    python3 cta/feature/run_generate.py --interval day            → --interval day
    python3 cta/feature/run_generate.py --interval minute         → --interval minute
    python3 cta/feature/run_generate.py --interval all            → --interval all
    python3 cta/feature/run_generate.py --symbol CU0 RB0          → --symbols CU0 RB0
    python3 cta/feature/run_generate.py --no-cross-section        → 省略 --cross-section

本脚本会在启动时打印 DeprecationWarning，随后将 CLI 透传给
`cta.feature.run_all_features.main()`，产物一律落到新布局，不再生成
旧版的 `cta/data/feature/day/{symbol}.parquet` 平铺文件。
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

# 把 vnpy 项目根目录加入 sys.path，确保 cta 包可被导入
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))


def _translate_argv() -> list[str]:
    """把旧 CLI 转成新 CLI；返回给 run_all_features 的 sys.argv[1:] 列表。"""
    parser = argparse.ArgumentParser(
        description="Legacy CTA feature generator (shim over run_all_features)"
    )
    parser.add_argument(
        "--interval", choices=["day", "minute", "all"], default="all",
    )
    parser.add_argument("--symbol", nargs="*", default=None)
    parser.add_argument("--no-cross-section", action="store_true")
    args = parser.parse_args()

    new_argv: list[str] = []
    # 频率
    if args.interval == "all":
        new_argv += ["--interval", "all"]
    else:
        new_argv += ["--interval", args.interval]
    # 品种
    if args.symbol:
        new_argv += ["--symbols", *args.symbol]
    # 旧 CLI 的 --no-cross-section = 默认，不追加 --cross-section
    # 旧 CLI 里没有对应"只跑截面"，保持一致
    return new_argv


def main() -> None:
    warnings.warn(
        "run_generate.py 已迁移。请改用 `python3 -m cta.feature.run_all_features`，"
        "它支持全频率 (day/minute/minute5/minute15/minute30/minute60) + 多进程 + 断点续跑。"
        " 本脚本将透传至新入口。",
        DeprecationWarning,
        stacklevel=2,
    )

    from cta.feature.run_all_features import main as new_main

    new_argv = _translate_argv()
    sys.argv = [sys.argv[0]] + new_argv
    new_main()


if __name__ == "__main__":
    main()
