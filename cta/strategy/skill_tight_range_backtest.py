"""Backtest entry for skill-based tight range breakout strategy."""
from __future__ import annotations

import argparse
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from cta.config.futures_meta import FUTURES_META
from cta.config.skill_tight_range_breakout_config import (
    BacktestConfig,
    StrategyConfig,
    VALID_SIDE_MODES,
)
from cta.skills.data_backtest.event_driven_backtest import EngineConfig, run_backtest
from cta.skills.data_backtest.trade_evaluation import summarize_trades
from cta.skills.data_backtest.transaction_cost import estimate_cost
from cta.strategy.skill_tight_range_breakout import (
    ContractSpec,
    SkillTightRangeBreakoutStrategy,
    prepare_strategy_frame,
)

logger = logging.getLogger(__name__)


INTERVAL_ALIAS: dict[str, str] = {
    "d": "day",
    "day": "day",
    "daily": "day",
    "min": "minute",
    "1min": "minute",
    "minute": "minute",
    "5min": "minute5",
    "minute5": "minute5",
    "15min": "minute15",
    "minute15": "minute15",
    "30min": "minute30",
    "minute30": "minute30",
    "60min": "minute60",
    "minute60": "minute60",
}
INTERVALS_PERIODS_PER_YEAR: dict[str, int] = {
    "day": 252,
    "minute60": 252 * 4,
    "minute30": 252 * 8,
    "minute15": 252 * 16,
    "minute5": 252 * 48,
    "minute": 252 * 240,
}


@dataclass(frozen=True)
class BacktestRunResult:
    symbol: str
    exchange: str
    trade_log_path: Path
    equity_path: Path
    summary_path: Path
    metrics: dict[str, float]


def normalize_interval(interval: str) -> str:
    """Normalize user interval alias to canonical key."""
    key = str(interval).strip().lower()
    if key not in INTERVAL_ALIAS:
        raise ValueError(
            f"unsupported interval={interval!r}, valid aliases: {sorted(INTERVAL_ALIAS)}"
        )
    return INTERVAL_ALIAS[key]


def suggest_periods_per_year(interval: str) -> int:
    canon = normalize_interval(interval)
    return int(INTERVALS_PERIODS_PER_YEAR.get(canon, 252))


def resolve_exchange(symbol: str, symbols_list_path: Path | None = None) -> str:
    """Resolve exchange from symbols list CSV."""
    cfg = BacktestConfig()
    path = symbols_list_path or cfg.symbols_list_path
    if not path.exists():
        raise FileNotFoundError(f"symbols list not found: {path}")
    df = pd.read_csv(path, encoding="utf-8-sig")
    sym = str(symbol).upper()
    hit = df.loc[df["symbol"].astype(str).str.upper() == sym, "exchange"]
    if hit.empty:
        raise ValueError(f"symbol={sym} not found in symbols list: {path}")
    return str(hit.iloc[0]).upper()


def _legacy_contract_meta(symbol: str, exchange: str) -> tuple[float, float, float, float] | None:
    root = re.sub(r"[^A-Za-z]", "", symbol).lower()
    if not root:
        return None
    key = f"{root}888.{exchange.upper()}"
    meta = FUTURES_META.get(key)
    if meta is None:
        return None
    size = float(meta.get("size", 10.0))
    rate = float(meta.get("rate", 0.0001))
    pricetick = float(meta.get("pricetick", 1.0))
    slippage_price = float(meta.get("slippage", 1.0))
    return size, rate, pricetick, slippage_price


def build_contract_spec(symbol: str, exchange: str) -> ContractSpec:
    """Build contract meta with v3 meta first and fallback defaults."""
    sym = str(symbol).upper()
    ex = str(exchange).upper()
    vt_symbol = f"{sym}.{ex}"

    try:
        from cta.strategy.brooks.config.params import ContractDefaultsCfg
        from cta.strategy.brooks.config.symbols import get_symbol_meta

        meta = get_symbol_meta(vt_symbol, ContractDefaultsCfg())
        tick_size = max(float(meta.pricetick), 1e-9)
        slippage_ticks = max(float(meta.slippage) / tick_size, 0.0)
        return ContractSpec(
            symbol=sym,
            exchange=ex,
            multiplier=float(meta.size),
            tick_size=tick_size,
            commission_rate=float(meta.rate),
            slippage_ticks=slippage_ticks,
        )
    except Exception as exc:  # pragma: no cover - fallback path validated by tests
        logger.warning("v3 contract meta lookup failed for %s: %s", vt_symbol, exc)

    legacy = _legacy_contract_meta(sym, ex)
    if legacy is not None:
        size, rate, tick_size, slippage_price = legacy
        slippage_ticks = max(slippage_price / max(tick_size, 1e-9), 0.0)
        return ContractSpec(
            symbol=sym,
            exchange=ex,
            multiplier=size,
            tick_size=tick_size,
            commission_rate=rate,
            slippage_ticks=slippage_ticks,
        )

    return ContractSpec(
        symbol=sym,
        exchange=ex,
        multiplier=10.0,
        tick_size=1.0,
        commission_rate=0.0001,
        slippage_ticks=1.0,
    )


def _load_day_bars(
    symbol: str,
    backtest_cfg: BacktestConfig,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """Load one symbol day bars from local CSV."""
    csv_path = backtest_cfg.data_day_dir / f"{symbol.upper()}.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"day csv not found: {csv_path}")
    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    if "datetime" not in df.columns:
        raise KeyError(f"missing datetime column in {csv_path}")

    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df = df.dropna(subset=["datetime"]).copy()
    df = df[(df["datetime"] >= pd.Timestamp(start_date)) & (df["datetime"] <= pd.Timestamp(end_date))]
    df = df.sort_values("datetime").reset_index(drop=True)
    need = {"open", "high", "low", "close", "volume"}
    miss = need - set(df.columns)
    if miss:
        raise KeyError(f"missing required columns {miss} in {csv_path}")
    return df


def _symbol_root(symbol: str) -> str:
    letters = re.sub(r"[^A-Za-z]", "", str(symbol).upper())
    return letters if letters else str(symbol).upper()


def _resolve_minute_symbol_dir(
    data_interval_dir: Path,
    symbol: str,
    exchange: str | None = None,
) -> Path:
    sym = str(symbol).upper()
    root = _symbol_root(sym)
    ex = str(exchange).upper() if exchange else ""

    candidates: list[str] = [root, root[:2], sym]
    if ex:
        candidates.extend([f"{sym}.{ex}", f"{root}.{ex}"])

    seen: set[str] = set()
    for c in candidates:
        if not c or c in seen:
            continue
        seen.add(c)
        p = data_interval_dir / c
        if p.exists() and p.is_dir():
            return p

    # fallback: find first directory prefix-matching root
    prefixed = sorted(
        [p for p in data_interval_dir.iterdir() if p.is_dir() and p.name.upper().startswith(root)]
    )
    if prefixed:
        return prefixed[0]
    raise FileNotFoundError(
        f"minute symbol dir not found under {data_interval_dir} for symbol={symbol}, exchange={exchange}"
    )


def _load_minute_bars(
    symbol: str,
    exchange: str | None,
    backtest_cfg: BacktestConfig,
    start_date: str,
    end_date: str,
    interval: str,
) -> pd.DataFrame:
    data_interval_dir = backtest_cfg.data_root / interval
    if not data_interval_dir.exists():
        raise FileNotFoundError(f"interval data directory not found: {data_interval_dir}")

    symbol_dir = _resolve_minute_symbol_dir(data_interval_dir, symbol=symbol, exchange=exchange)
    files = sorted(symbol_dir.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"no parquet files found in {symbol_dir}")

    start_day = pd.Timestamp(start_date).normalize()
    end_day = pd.Timestamp(end_date).normalize()

    selected: list[Path] = []
    for file in files:
        try:
            file_day = pd.Timestamp(file.stem).normalize()
            if start_day <= file_day <= end_day:
                selected.append(file)
        except Exception:
            selected.append(file)
    if not selected:
        selected = files

    parts: list[pd.DataFrame] = []
    for file in selected:
        df = pd.read_parquet(file)
        parts.append(df)
    out = pd.concat(parts, axis=0, ignore_index=True)
    if "datetime" not in out.columns:
        raise KeyError(f"missing datetime column in parquet data: {symbol_dir}")

    out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
    out = out.dropna(subset=["datetime"]).copy()
    out = out[(out["datetime"] >= pd.Timestamp(start_date)) & (out["datetime"] <= pd.Timestamp(end_date))]
    out = out.sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)

    # Keep a stable schema for strategy/backtest.
    for c in ("open", "high", "low", "close", "volume"):
        if c not in out.columns:
            raise KeyError(f"missing required columns {c} in parquet data: {symbol_dir}")
        out[c] = out[c].astype(float)
    if "open_interest" not in out.columns:
        out["open_interest"] = 0.0
    if "turnover" not in out.columns:
        out["turnover"] = 0.0
    out["symbol"] = str(symbol).upper()
    out["exchange"] = str(exchange or "").upper()
    out["interval"] = interval
    return out


def load_bars(
    symbol: str,
    backtest_cfg: BacktestConfig,
    start_date: str,
    end_date: str,
    exchange: str | None = None,
) -> pd.DataFrame:
    """Load bars for day/minute intervals with unified output schema."""
    interval = normalize_interval(backtest_cfg.interval)
    if interval == "day":
        return _load_day_bars(
            symbol=symbol,
            backtest_cfg=backtest_cfg,
            start_date=start_date,
            end_date=end_date,
        )
    return _load_minute_bars(
        symbol=symbol,
        exchange=exchange,
        backtest_cfg=backtest_cfg,
        start_date=start_date,
        end_date=end_date,
        interval=interval,
    )


def _build_metrics(
    trade_log: pd.DataFrame,
    equity_curve: pd.Series,
    initial_capital: float,
    periods_per_year: int,
) -> dict[str, float]:
    summary = summarize_trades(
        trade_log,
        equity_curve,
        periods_per_year=periods_per_year,
    )
    total_pnl = float(summary["total_pnl"])
    total_return = total_pnl / float(initial_capital) if initial_capital != 0 else 0.0
    return {
        "total_pnl": total_pnl,
        "total_return": total_return,
        "annualized": float(summary["annualized"]),
        "mdd": float(summary["mdd"]),
        "sharpe": float(summary["sharpe"]),
        "calmar": float(summary["calmar"]),
        "winrate": float(summary["winrate"]),
        "pf": float(summary["pf"]),
        "trade_count": int(summary["trade_count"]),
    }


def run_symbol_backtest(
    symbol: str = "RB0",
    exchange: str | None = None,
    start_date: str = "2018-01-01",
    end_date: str = "2024-12-31",
    strategy_cfg: StrategyConfig | None = None,
    backtest_cfg: BacktestConfig | None = None,
) -> BacktestRunResult:
    """Run one symbol backtest and write outputs."""
    scfg = strategy_cfg or StrategyConfig()
    bcfg = backtest_cfg or BacktestConfig()
    sym = str(symbol).upper()
    ex = str(exchange).upper() if exchange else resolve_exchange(sym, bcfg.symbols_list_path)
    interval = normalize_interval(bcfg.interval)

    bars = load_bars(sym, bcfg, start_date, end_date, exchange=ex)
    frame = prepare_strategy_frame(bars, scfg, interval=interval)
    contract = build_contract_spec(sym, ex)

    strategy = SkillTightRangeBreakoutStrategy(
        frame=frame,
        cfg=scfg,
        contract=contract,
        capital_base=bcfg.initial_capital,
    )
    engine_cfg = EngineConfig(
        fill_rule="next_open",
        stop_fill="worst",
        cost_fn=estimate_cost,
        slippage_ticks=contract.slippage_ticks,
    )
    out = run_backtest(frame, strategy, engine_cfg)
    trade_log = out["trade_log"].copy()
    equity_pnl = out["equity_curve"].astype(float)
    equity_curve = (equity_pnl + float(bcfg.initial_capital)).rename("equity")

    metrics = _build_metrics(
        trade_log=trade_log,
        equity_curve=equity_curve,
        initial_capital=bcfg.initial_capital,
        periods_per_year=bcfg.periods_per_year,
    )

    run_date = pd.Timestamp.now().strftime("%Y%m%d")
    mode = str(scfg.trade_side_mode).strip().lower()
    out_dir = bcfg.output_root / f"{run_date}_skill_tight_range_breakout_{sym}_{interval}_{mode}"
    out_dir.mkdir(parents=True, exist_ok=True)

    trade_log_path = out_dir / f"{run_date}_{sym}_{mode}_trades.csv"
    equity_path = out_dir / f"{run_date}_{sym}_{mode}_equity.csv"
    summary_path = out_dir / f"{run_date}_{sym}_{mode}_summary.csv"

    trade_log.to_csv(trade_log_path, index=False, encoding="utf-8-sig")
    pd.DataFrame({"equity": equity_curve}).to_csv(equity_path, index=False, encoding="utf-8-sig")
    pd.DataFrame(
        [{**metrics, "symbol": sym, "exchange": ex, "interval": interval, "trade_side_mode": mode}]
    ).to_csv(
        summary_path,
        index=False,
        encoding="utf-8-sig",
    )

    logger.info("backtest done: %s.%s -> %s", sym, ex, out_dir)
    return BacktestRunResult(
        symbol=sym,
        exchange=ex,
        trade_log_path=trade_log_path,
        equity_path=equity_path,
        summary_path=summary_path,
        metrics=metrics,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Skill tight-range breakout backtest")
    parser.add_argument("--symbol", default="RB0", help="e.g. RB0")
    parser.add_argument("--exchange", default=None, help="e.g. SHFE, optional")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--interval", default="day", help="day/60min/30min/15min/5min/min")
    parser.add_argument(
        "--trade-side-mode",
        default="both",
        choices=sorted(VALID_SIDE_MODES),
        help="both/long/short",
    )
    parser.add_argument("--periods-per-year", type=int, default=None)
    parser.add_argument("--output-root", default=None, help="override output root directory")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args = _parse_args()
    interval = normalize_interval(args.interval)
    periods_per_year = (
        int(args.periods_per_year)
        if args.periods_per_year is not None
        else suggest_periods_per_year(interval)
    )
    backtest_cfg = BacktestConfig(
        output_root=Path(args.output_root).resolve() if args.output_root else BacktestConfig().output_root,
        interval=interval,
        periods_per_year=periods_per_year,
    )
    strategy_cfg = StrategyConfig(
        trade_side_mode=str(args.trade_side_mode).strip().lower(),
    )
    result = run_symbol_backtest(
        symbol=args.symbol,
        exchange=args.exchange,
        start_date=args.start,
        end_date=args.end,
        strategy_cfg=strategy_cfg,
        backtest_cfg=backtest_cfg,
    )
    logger.info("summary: %s", result.summary_path)
    logger.info("metrics: %s", result.metrics)


if __name__ == "__main__":
    main()
