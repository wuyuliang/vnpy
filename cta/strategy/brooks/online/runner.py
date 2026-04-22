"""v3 在线驱动(支持 parquet 回放 dry-run)。

用法:
    # 从 parquet 回放(默认 LTF,预热 200 根,后续逐 bar 喂):
    python3 -m cta.strategy.brooks.online.runner \
        --symbol RB0.SHFE --interval minute5 \
        --warmup-start 2024-01-01 --warmup-end 2024-03-31 \
        --live-start 2024-04-01 --live-end 2024-06-30 \
        --dry-run

思路:
- `--dry-run` 模式下不走真实 vnpy 网关;读 parquet 把 bar 序列化成 online 流
- warmup 段一次性塞给 FeatureAdapter.warmup_online
- live 段逐 bar 调用 adapter.update_online + core.on_bar
- 所有 OPEN/CLOSE 只记日志(不下单)

线上接入 vnpy 网关在 `online/live_strategy.py`。
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from cta.feature.feature_loader import load_symbol_features
from cta.strategy.brooks.config.params import load_params
from cta.strategy.brooks.config.symbols import get_symbol_meta, vt_to_symbol_exchange
from cta.strategy.brooks.core.features.adapter import FeatureAdapter
from cta.strategy.brooks.core.model.score_gate import ScoreGate
from cta.strategy.brooks.core.risk import PortfolioRiskManager
from cta.strategy.brooks.core.strategy import BrooksV3Core
from cta.strategy.brooks.core.trade_log import TradeLogger

logger = logging.getLogger(__name__)


def _load_features(vt_symbol: str, interval: str,
                   start: str, end: str) -> pd.DataFrame:
    symbol, _ = vt_to_symbol_exchange(vt_symbol)
    df = load_symbol_features(symbol, interval=interval,
                              start_date=start, end_date=end)
    return df.reset_index(drop=True)


def run_dry(
    vt_symbol: str,
    params,
    warmup_start: str,
    warmup_end: str,
    live_start: str,
    live_end: str,
    score_gate: ScoreGate,
    capital: float,
    output_dir: Path,
) -> None:
    meta = get_symbol_meta(vt_symbol, params.contract)

    adapter = FeatureAdapter(mode="online")
    ltf = params.intervals.ltf

    warmup_df = _load_features(vt_symbol, ltf, warmup_start, warmup_end)
    if warmup_df.empty:
        raise SystemExit(f"warmup 段无特征数据: {vt_symbol} {ltf} "
                         f"{warmup_start}..{warmup_end}")
    logger.info("warmup bars=%d", len(warmup_df))
    adapter.warmup_online(vt_symbol, ltf, warmup_df)

    # HTF/MTF 也需要 warmup(切到 offline 读一下,保证 _try_open 拿得到)
    # 简化:online 模式下 HTF/MTF 仍走 offline 缓存(核心为 LTF 的流)
    htf_warmup = _load_features(vt_symbol, params.intervals.htf,
                                warmup_start, live_end)
    mtf_warmup = _load_features(vt_symbol, params.intervals.mtf,
                                warmup_start, live_end)
    adapter.warmup_online(vt_symbol, params.intervals.htf, htf_warmup)
    adapter.warmup_online(vt_symbol, params.intervals.mtf, mtf_warmup)

    live_df = _load_features(vt_symbol, ltf, live_start, live_end)
    if live_df.empty:
        raise SystemExit(f"live 段无特征数据: {vt_symbol} {ltf} "
                         f"{live_start}..{live_end}")
    logger.info("live bars=%d", len(live_df))

    sub = output_dir / vt_symbol.replace(".", "_")
    sub.mkdir(parents=True, exist_ok=True)
    trade_logger = TradeLogger(output_path=sub / "trades_dryrun.parquet")
    portfolio = PortfolioRiskManager(
        dd_threshold_half=params.risk.portfolio.dd_threshold_half,
        dd_threshold_quarter=params.risk.portfolio.dd_threshold_quarter,
        recover_to_full_at_new_high=params.risk.portfolio.recover_to_full_at_new_high,
    )
    core = BrooksV3Core(
        vt_symbol=vt_symbol,
        params=params,
        adapter=adapter,
        score_gate=score_gate,
        portfolio=portfolio,
        trade_logger=trade_logger,
        contract_size=meta.size,
        price_tick=meta.pricetick,
        initial_capital=capital,
    )

    for i, bar in live_df.iterrows():
        adapter.update_online(vt_symbol, ltf, bar)
        decision = core.on_bar(int(i), bar)
        if decision.action != "HOLD":
            logger.info("[dry-run] %s: %s qty=%d px=%.2f reason=%s",
                        bar["datetime"], decision.action,
                        decision.qty, decision.price, decision.reason)

    core.finalize()
    logger.info("dry-run 结束,交易日志: %s", trade_logger.output_path)


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="v3 在线 dry-run 驱动")
    parser.add_argument("--config", default=None)
    parser.add_argument("--symbol", required=True, help="vt_symbol, e.g. RB0.SHFE")
    parser.add_argument("--interval", default=None, help="覆盖 params.intervals.ltf")
    parser.add_argument("--warmup-start", required=True)
    parser.add_argument("--warmup-end", required=True)
    parser.add_argument("--live-start", required=True)
    parser.add_argument("--live-end", required=True)
    parser.add_argument("--capital", type=float, default=1_000_000.0)
    parser.add_argument("--model", default=None,
                        help="--model none / 路径 / 默认自动")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="v3 目前只实现 dry-run,保留参数占位")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    params = load_params(args.config)
    if args.interval:
        params.intervals.ltf = args.interval

    if args.model and args.model.lower() == "none":
        gate = ScoreGate.passthrough()
    elif args.model:
        gate = ScoreGate.load(args.model, threshold=params.model.threshold)
    elif params.model.enabled and params.model.path:
        gate = ScoreGate.load(params.model.path, threshold=params.model.threshold)
    else:
        try:
            gate = ScoreGate.load_latest(params.output.models_root,
                                         threshold=params.model.threshold)
        except Exception:  # noqa: BLE001
            logger.warning("未找到模型,使用 passthrough")
            gate = ScoreGate.passthrough()

    out_dir = Path(args.output_dir) if args.output_dir else \
        Path(params.output.report_root) / "online_dryrun"
    out_dir.mkdir(parents=True, exist_ok=True)

    run_dry(
        vt_symbol=args.symbol,
        params=params,
        warmup_start=args.warmup_start,
        warmup_end=args.warmup_end,
        live_start=args.live_start,
        live_end=args.live_end,
        score_gate=gate,
        capital=args.capital,
        output_dir=out_dir,
    )


if __name__ == "__main__":
    main()
