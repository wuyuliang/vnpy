"""轻量级回测引擎(不依赖 vnpy BacktestingEngine)。

原因:
- v3 需要跨周期(HTF/MTF/LTF)同步特征,vnpy 原生每次只跑单周期
- 特征已在 cta/data/feature/ 预计算,从 parquet 直读更高效
- 成交成本(手续费 + 滑点)在 close 时直接从 pnl 扣除

流程(每个 vt_symbol):
1. 从 feature_loader 加载 LTF OHLCV+特征段
2. FeatureAdapter(offline) 在 BrooksV3Core 内部按需取 HTF/MTF
3. 逐 bar 喂给 core.on_bar → 记录 trades + 逐日 equity
4. 对 trades.parquet 与 daily_equity.csv 落盘
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from cta.strategy.brooks.config.params import BrooksV3Params
from cta.strategy.brooks.config.symbols import get_symbol_meta
from cta.strategy.brooks.core.features.adapter import FeatureAdapter
from cta.strategy.brooks.core.model.score_gate import ScoreGate
from cta.strategy.brooks.core.risk import PortfolioRiskManager
from cta.strategy.brooks.core.strategy import BrooksV3Core
from cta.strategy.brooks.core.trade_log import TradeLogger

logger = logging.getLogger(__name__)


@dataclass
class SingleRunResult:
    vt_symbol: str
    trades_path: Path
    daily_equity_path: Path
    stats: dict = field(default_factory=dict)


def run_single(
    vt_symbol: str,
    params: BrooksV3Params,
    start_date: str,
    end_date: str,
    capital: float,
    score_gate: ScoreGate,
    output_dir: Path,
) -> SingleRunResult:
    meta = get_symbol_meta(vt_symbol, params.contract)
    adapter = FeatureAdapter(mode="offline")
    ltf = params.intervals.ltf

    try:
        df = adapter.get_range(vt_symbol, ltf, start_date, end_date)
    except FileNotFoundError as e:
        logger.warning("skip %s: %s", vt_symbol, e)
        return SingleRunResult(
            vt_symbol=vt_symbol,
            trades_path=Path(), daily_equity_path=Path(),
            stats={"error": str(e)},
        )
    if df.empty:
        return SingleRunResult(vt_symbol=vt_symbol, trades_path=Path(),
                               daily_equity_path=Path(),
                               stats={"error": "empty_features"})
    df = df.reset_index(drop=True)
    logger.info("run_single %s %s %s..%s: %d bars",
                vt_symbol, ltf, start_date, end_date, len(df))

    sub_dir = output_dir / vt_symbol.replace(".", "_")
    sub_dir.mkdir(parents=True, exist_ok=True)
    trades_path = sub_dir / "trades.parquet"
    daily_path = sub_dir / "daily_equity.csv"

    logger_ = TradeLogger(output_path=trades_path)
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
        trade_logger=logger_,
        contract_size=meta.size,
        price_tick=meta.pricetick,
        initial_capital=capital,
    )

    daily_rows: list[dict] = []
    last_day: str | None = None
    for i, row in df.iterrows():
        core.on_bar(int(i), row)
        day = pd.Timestamp(row["datetime"]).strftime("%Y-%m-%d")
        if day != last_day:
            daily_rows.append({"date": day, "equity": core.equity})
            last_day = day

    # 收盘强平任何残留
    if core.position_qty > 0 and core.position_state is not None:
        from cta.strategy.brooks.core.risk.stops import ExitReason
        last = df.iloc[-1]
        core._close_position(  # noqa: SLF001
            pd.Timestamp(last["datetime"]),
            float(last["close"]),
            ExitReason.MAX_HOLDING,
        )

    core.finalize()
    daily_df = pd.DataFrame(daily_rows)
    if not daily_df.empty:
        daily_df.to_csv(daily_path, index=False)

    # 扣除成本(简化:每成交一笔固定 slippage * size * 2 + rate * notional * 2)
    trades_df = pd.read_parquet(trades_path) if trades_path.exists() else pd.DataFrame()
    if not trades_df.empty:
        cost_per_trade = 2 * meta.slippage * meta.size * trades_df["qty"].astype(float)
        rate_cost = 2 * meta.rate * trades_df["entry_price"].astype(float) \
                    * trades_df["qty"].astype(float) * meta.size
        trades_df["cost"] = cost_per_trade + rate_cost
        trades_df["pnl_net"] = trades_df["pnl"] - trades_df["cost"]
        trades_df.to_parquet(trades_path, index=False)

    stats = _compute_stats(trades_df, daily_df, capital)
    stats["vt_symbol"] = vt_symbol
    stats["trade_count"] = int(len(trades_df))
    return SingleRunResult(vt_symbol=vt_symbol, trades_path=trades_path,
                           daily_equity_path=daily_path, stats=stats)


def _compute_stats(trades: pd.DataFrame, daily: pd.DataFrame, capital: float) -> dict:
    out: dict = {}
    if trades.empty:
        return {"total_return": 0.0, "n_trades": 0}

    out["n_trades"] = int(len(trades))
    total_pnl_net = float(trades["pnl_net"].sum()) if "pnl_net" in trades else float(trades["pnl"].sum())
    out["total_pnl_net"] = total_pnl_net
    out["total_return"] = total_pnl_net / capital

    wins = trades[trades["pnl_net" if "pnl_net" in trades else "pnl"] > 0]
    losses = trades[trades["pnl_net" if "pnl_net" in trades else "pnl"] <= 0]
    out["win_rate"] = float(len(wins) / len(trades)) if len(trades) else 0.0
    win_sum = float(wins["pnl_net" if "pnl_net" in wins else "pnl"].sum())
    loss_sum = float(-losses["pnl_net" if "pnl_net" in losses else "pnl"].sum())
    out["profit_factor"] = float(win_sum / loss_sum) if loss_sum > 0 else float("inf")
    out["avg_win"] = float(wins["pnl_net" if "pnl_net" in wins else "pnl"].mean()) if len(wins) else 0.0
    out["avg_loss"] = float(losses["pnl_net" if "pnl_net" in losses else "pnl"].mean()) if len(losses) else 0.0

    if not daily.empty:
        eq = daily["equity"].astype(float).to_numpy()
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / peak
        out["max_drawdown"] = float(dd.min())
        rets = np.diff(eq) / eq[:-1]
        if len(rets) > 5:
            mean_r = rets.mean()
            std_r = rets.std()
            if std_r > 0:
                out["sharpe"] = float(mean_r / std_r * np.sqrt(252))
            else:
                out["sharpe"] = 0.0
            out["annual_return"] = float((1 + mean_r) ** 252 - 1)
    return out


__all__ = ["run_single", "SingleRunResult"]
