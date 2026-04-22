"""v3 批量回测 CLI。

用法:
    python3 -m cta.strategy.brooks.backtest.runner \
        --top-n 3 --start 2022-01-01 --end 2024-12-31 \
        --capital 1000000 --model none

    python3 -m cta.strategy.brooks.backtest.runner \
        --top-n 8 --start 2023-01-01 --end 2024-12-31 \
        --model cta/strategy/brooks/models/xgb_20260419_120000.ubj
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path

from cta.strategy.brooks.backtest.engine import run_single
from cta.strategy.brooks.backtest.reporter import write_summary
from cta.strategy.brooks.config.params import load_params
from cta.strategy.brooks.config.symbols import resolve_symbols
from cta.strategy.brooks.core.model.score_gate import ScoreGate

logger = logging.getLogger(__name__)


def _make_score_gate(params, model_arg: str | None) -> ScoreGate:
    if not params.model.enabled or (model_arg and model_arg.lower() == "none"):
        logger.info("score gate disabled (--model none 或 model.enabled=false)")
        return ScoreGate.passthrough()
    if model_arg:
        return ScoreGate.load(model_arg, threshold=params.model.threshold)
    if params.model.path:
        return ScoreGate.load(params.model.path, threshold=params.model.threshold)
    # 自动选 models_root 里的最新
    return ScoreGate.load_latest(params.output.models_root,
                                 threshold=params.model.threshold)


def run_batch(
    vt_symbols: list[str],
    params,
    start: str,
    end: str,
    capital: float,
    model_arg: str | None,
    report_root: Path | None = None,
) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = report_root or Path(params.output.report_root)
    if not root.is_absolute():
        root = Path.cwd() / root
    report_dir = root / ts
    report_dir.mkdir(parents=True, exist_ok=True)

    score_gate = _make_score_gate(params, model_arg)

    results = []
    for vt in vt_symbols:
        try:
            r = run_single(
                vt_symbol=vt, params=params,
                start_date=start, end_date=end,
                capital=capital, score_gate=score_gate,
                output_dir=report_dir / "per_run",
            )
            results.append(r)
        except Exception as e:
            logger.exception("run_single %s failed: %s", vt, e)

    write_summary(results, report_dir)
    logger.info("report dir: %s", report_dir)
    return report_dir


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None)
    parser.add_argument("--top-n", type=int, default=None)
    parser.add_argument("--max-tier", default=None)
    parser.add_argument("--symbols", default=None,
                        help="逗号分隔,覆盖 top-n 解析结果")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--capital", type=float, default=1_000_000.0)
    parser.add_argument("--model", default=None,
                        help="--model none 关闭模型;路径传 .ubj;否则自动取 models 目录下最新")
    args = parser.parse_args()

    params = load_params(args.config)
    if args.top_n is not None:
        params.symbols.top_n = args.top_n
    if args.max_tier is not None:
        params.symbols.max_tier = args.max_tier

    if args.symbols:
        vt_symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        vt_symbols = resolve_symbols(params.symbols)
    if not vt_symbols:
        raise SystemExit("未解析出任何可用品种")
    logger.info("universe: %s", vt_symbols)

    run_batch(
        vt_symbols=vt_symbols, params=params,
        start=args.start, end=args.end, capital=args.capital,
        model_arg=args.model,
    )


if __name__ == "__main__":
    main()
