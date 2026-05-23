"""CTA 项目统一命令行入口。

子命令
------
- ``cta backtest``      — 事件驱动回测 + HTML 报告
- ``cta validate``      — 数据校验（包装 ``cta.data_code.validate``）
- ``cta expand-minute`` — 主力品种分钟数据扩展（包装 ``cta.data_code.expand_minute``）

策略加载约定
------------
``--strategy module.path:factory`` 中 ``factory`` 是无参 callable，返回已实例化、
实现 ``on_bar(i, bar, position) -> list[order_dict]`` 接口的策略实例。

示例
----
    python3 -m cta.cli backtest \\
        --strategy cta.strategy.demos:make_double_ma \\
        --bars cta/data/origin/day/RB0.csv \\
        --out-dir cta/backtest/$(date +%Y%m%d)_double_ma_rb0 \\
        --title "DoubleMA / RB0 / day"

    python3 -m cta.cli validate --interval day --max-rank 20 --out report.csv
"""
from __future__ import annotations

import argparse
import importlib
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from cta.data_code import validate as V
from cta.data_code.futures_downloader import MINUTE_INTERVALS
from cta.run.runner import run_event_driven_backtest
from cta.skills.data_backtest.event_driven_backtest import EngineConfig
from cta.utils.random_seed import seed_all_from_env

logger = logging.getLogger("cta.cli")


def _load_obj(spec: str) -> Any:
    """解析 ``module.path:attr`` 形式，返回属性。"""
    mod_path, _, attr = spec.partition(":")
    if not mod_path or not attr:
        raise ValueError(f"expected 'module:attr', got: {spec!r}")
    mod = importlib.import_module(mod_path)
    return getattr(mod, attr)


def _cmd_backtest(args: argparse.Namespace) -> int:
    try:
        factory = _load_obj(args.strategy)
        strategy = factory()
    except Exception as e:  # noqa: BLE001
        print(f"failed to load strategy {args.strategy!r}: {e}", file=sys.stderr)
        return 2

    bars_path = Path(args.bars)
    if not bars_path.exists():
        print(f"bars file not found: {bars_path}", file=sys.stderr)
        return 2
    bars = pd.read_csv(bars_path, encoding="utf-8-sig")

    cost_fn = None
    if args.cost_fn:
        try:
            cost_fn = _load_obj(args.cost_fn)
        except Exception as e:  # noqa: BLE001
            print(f"failed to load cost_fn {args.cost_fn!r}: {e}", file=sys.stderr)
            return 2

    cfg = EngineConfig(
        fill_rule=args.fill_rule,
        stop_fill=args.stop_fill,
        slippage_ticks=args.slippage_ticks,
        limit_move_pct=args.limit_move_pct,
        liquidity_ratio=args.liquidity_ratio,
    )
    res = run_event_driven_backtest(
        strategy=strategy,
        bars=bars,
        engine_cfg=cfg,
        cost_fn=cost_fn,
        out_dir=args.out_dir,
        title=args.title,
        monte_carlo_iter=args.monte_carlo_iter,
    )
    s = res.stats
    print(f"report : {res.report_path}")
    print(f"trades : {len(res.trade_log)}")
    print(f"sharpe : {s.get('sharpe', 0.0):.4f}")
    print(f"sortino: {s.get('sortino', 0.0):.4f}")
    print(f"calmar : {s.get('calmar', 0.0):.4f}")
    print(f"mdd    : {s.get('mdd', 0.0):.4f}")
    print(f"winrate: {s.get('winrate', 0.0):.4f}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    if args.csv:
        sym = args.symbol or Path(args.csv).stem
        rep = V.validate_day_csv(sym, Path(args.csv))
        df = pd.DataFrame([rep.to_row()])
    else:
        try:
            pairs = V.load_ranking(args.max_rank, args.symbol)
        except FileNotFoundError as e:
            print(str(e), file=sys.stderr)
            return 2
        if not pairs:
            print("no symbols matched", file=sys.stderr)
            return 1
        reports = V.validate_symbols(args.interval, pairs)
        df = pd.DataFrame([r.to_row() for r in reports])
    print(df.to_string(index=False))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(args.out, index=False, encoding="utf-8-sig")
        print(f"wrote {args.out}")
    return 0


def _cmd_expand_minute(args: argparse.Namespace) -> int:
    cmd = [sys.executable, "-m", "cta.data_code.expand_minute"]
    if args.max_rank is not None:
        cmd += ["--max-rank", str(args.max_rank)]
    if args.only_symbols:
        cmd += ["--only-symbols", *args.only_symbols]
    if args.intervals:
        cmd += ["--intervals", *args.intervals]
    if args.workers:
        cmd += ["--workers", str(args.workers)]
    if args.rate_limit:
        cmd += ["--rate-limit", str(args.rate_limit)]
    if args.validate_only:
        cmd.append("--validate-only")
    if args.validate_out:
        cmd += ["--validate-out", args.validate_out]
    return subprocess.run(cmd, check=False).returncode


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cta", description="CTA backtest / data CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # backtest
    p_bt = sub.add_parser("backtest", help="run event-driven backtest with HTML report")
    p_bt.add_argument("--strategy", required=True, help="'module.path:factory_func'")
    p_bt.add_argument("--bars", required=True, help="bars CSV path")
    p_bt.add_argument("--out-dir", required=True, help="output directory for HTML/JSON")
    p_bt.add_argument("--title", default="Backtest")
    p_bt.add_argument("--cost-fn", default=None, help="optional 'module:func' cost function")
    p_bt.add_argument("--fill-rule", default="next_open", choices=["next_open", "cur_close"])
    p_bt.add_argument("--stop-fill", default="worst", choices=["worst", "exact"])
    p_bt.add_argument("--slippage-ticks", type=float, default=1.5)
    p_bt.add_argument(
        "--limit-move-pct", type=float, default=None,
        help="一字涨跌停过滤百分比，例 0.07；留空关闭",
    )
    p_bt.add_argument(
        "--liquidity-ratio", type=float, default=None,
        help="单笔最大成交量占比，例 0.1；留空关闭",
    )
    p_bt.add_argument("--monte-carlo-iter", type=int, default=1000)

    # validate
    p_val = sub.add_parser("validate", help="validate origin data integrity")
    p_val.add_argument("--csv", default=None, help="single csv path; if set takes priority")
    p_val.add_argument(
        "--interval", default="day",
        choices=["day", *MINUTE_INTERVALS],
        help="batch mode interval",
    )
    p_val.add_argument("--symbol", action="append", default=None, help="repeatable")
    p_val.add_argument("--max-rank", type=int, default=None)
    p_val.add_argument("--out", default=None, help="optional CSV output path")

    # expand-minute (passthrough)
    p_exp = sub.add_parser("expand-minute", help="expand minute data via download_all")
    p_exp.add_argument("--max-rank", type=int, default=10)
    p_exp.add_argument("--only-symbols", nargs="*", default=None)
    p_exp.add_argument(
        "--intervals", nargs="+", default=["minute60"],
        choices=list(MINUTE_INTERVALS),
    )
    p_exp.add_argument("--workers", type=int, default=4)
    p_exp.add_argument("--rate-limit", type=int, default=450)
    p_exp.add_argument("--validate-only", action="store_true")
    p_exp.add_argument("--validate-out", default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    used_seed = seed_all_from_env("CTA_GLOBAL_SEED")
    if used_seed is not None:
        logger.info("seeded global RNG from CTA_GLOBAL_SEED=%s", used_seed)
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "backtest":
        return _cmd_backtest(args)
    if args.cmd == "validate":
        return _cmd_validate(args)
    if args.cmd == "expand-minute":
        return _cmd_expand_minute(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
