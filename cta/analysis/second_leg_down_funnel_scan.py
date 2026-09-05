"""Count how many 1-minute bars survive each second-leg-down entry gate.

The strategy produced 9 trades in 2026H1. Tuning by guesswork is expensive:
one full backtest per idea. This scans the raw minute partitions directly with
a vectorised replica of the entry predicates, so a whole parameter sweep costs
seconds and tells you *which* gate is binding before you change anything.

The replica is deliberately independent of ``cta.strategy.second_leg_down``:
it re-derives session segments from the bar spacing rather than the metadata
sessions, so it also cross-checks the production predicates. Calibration: at
the shipped defaults it predicts 9 candidates over 40 symbols, against 9 actual
trades in ``report/second_leg_down/sld_v1``.

Use it for *relative* comparisons between parameter sets. It has no fills,
fees, slippage, sizing or capital limits, so its absolute counts are an upper
bound on what a run will trade.

    python3 -m cta.analysis.second_leg_down_funnel_scan --mode funnel
    python3 -m cta.analysis.second_leg_down_funnel_scan --mode sweep
    python3 -m cta.analysis.second_leg_down_funnel_scan --mode presets
    python3 -m cta.analysis.second_leg_down_funnel_scan --mode edge
"""
from __future__ import annotations

import argparse
import glob
from pathlib import Path

import numpy as np
import pandas as pd

DEFAULT_MINUTE_ROOT = Path("cta/data/origin/minute")
_BAR_MINUTES = [1]
_MIN_SAMPLES = [15]
DEFAULT_SYMBOLS = (
    "RB HC I J JM CU AL ZN NI SA MA L P Y M A CF SR TA V PP EG".split()
)


def load_minutes(symbol, *, minute_root, start, end):
    """Concatenate a symbol's natural-date partitions over [start, end]."""
    paths = [
        path
        for path in sorted(glob.glob(f"{minute_root}/{symbol}/*.parquet"))
        if start <= Path(path).stem <= end
    ]
    frames = []
    for path in paths:
        try:
            frame = pd.read_parquet(path)
        except (OSError, ValueError):
            continue
        if "datetime" not in frame.columns:
            return None
        frames.append(frame[["datetime", "open", "high", "low", "close", "volume"]])
    if not frames:
        return None
    return (
        pd.concat(frames, ignore_index=True)
        .sort_values("datetime")
        .reset_index(drop=True)
    )


def resample_bars(frame, *, minutes):
    """Aggregate one-minute bars into ``minutes``-minute bars inside sessions.

    Buckets are counted from each segment's own first bar, which is what the
    exchange clock does: 09:01-09:05 is the first five-minute bar of the day
    session. Bucketing on wall-clock time instead would straddle the recess.
    """
    if minutes <= 1:
        return frame
    stamps = pd.to_datetime(frame["datetime"])
    segment = (stamps.diff() > pd.Timedelta(minutes=1)).cumsum()
    position = segment.groupby(segment).cumcount()
    bucket = pd.Series(list(zip(segment, position // minutes)), index=frame.index)
    grouped = frame.groupby(bucket, sort=False)
    out = pd.DataFrame({
        "datetime": grouped["datetime"].last(),
        "open": grouped["open"].first(),
        "high": grouped["high"].max(),
        "low": grouped["low"].min(),
        "close": grouped["close"].last(),
        "volume": grouped["volume"].sum(),
    }).reset_index(drop=True)
    return out


def features(
    frame, *, atr_period=60, atr_method="sma", baseline_bars=60, bar_minutes=1
):
    """Causal per-bar features. Every window ends at the previous bar."""
    stamps = pd.to_datetime(frame["datetime"])
    # 交易时段按分钟间隔切：>1 分钟的跳跃就是新 segment。比读元数据轻，
    # 而且对夜盘 23:00 / 01:00 / 02:30 三种收盘时间自动正确。
    # 间隔阈值必须跟着 bar 大小走：5 分钟 K 线上相邻两根本来就差 5 分钟，
    # 用 1 分钟当阈值会把每一根都判成新时段，成交量基线和时间窗直接全灭
    segment = (
        (stamps.diff() > pd.Timedelta(minutes=bar_minutes)).cumsum().to_numpy()
    )
    open_, high, low, close, volume = (
        frame[name].astype(float).to_numpy()
        for name in ("open", "high", "low", "close", "volume")
    )
    prior_close = np.r_[np.nan, close[:-1]]
    true_range = np.nanmax(
        np.c_[high - low, np.abs(high - prior_close), np.abs(low - prior_close)],
        axis=1,
    )
    series = pd.Series(true_range)
    averaged = (
        series.rolling(atr_period, min_periods=atr_period).mean()
        if atr_method == "sma"
        else series.ewm(alpha=1 / atr_period, adjust=False).mean()
    )
    atr = averaged.shift(1).to_numpy()
    closes = pd.Series(close)
    emas = [closes.ewm(span=n, adjust=False).mean().to_numpy() for n in (5, 10, 20)]
    is_first = np.r_[True, segment[1:] != segment[:-1]]
    # 剔除时段首根：开盘集合竞价的量会把基线抬到没法用
    usable = pd.Series(np.where(is_first, np.nan, volume))
    baseline = usable.shift(1).rolling(baseline_bars, min_periods=1).mean().to_numpy()
    samples = usable.shift(1).rolling(baseline_bars, min_periods=1).count().to_numpy()
    index = pd.Series(segment)
    since_open = index.groupby(index).cumcount().to_numpy()
    until_close = index.map(index.value_counts()).to_numpy() - 1 - since_open
    return {
        "segment": segment, "open": open_, "high": high, "low": low,
        "close": close, "volume": volume, "atr": atr,
        "ema_fast": emas[0], "ema_mid": emas[1], "ema_slow": emas[2],
        "baseline": baseline, "samples": samples,
        "since_open": since_open, "until_close": until_close,
    }


def _gates_impl(f, *, body_mult=3.0, volume_mult=2.0, wick_ratio=0.15,
          min_samples=15, block_after_open=10, block_before_close=20,
          bar_minutes=1):
    body = f["close"] - f["open"]
    with np.errstate(invalid="ignore"):
        return {
            "big": (body < 0) & (np.abs(body) >= body_mult * f["atr"])
                   & np.isfinite(f["atr"]),
            "volume": (f["volume"] >= volume_mult * f["baseline"])
                      & (f["samples"] >= min_samples),
            "upper_wick": (f["high"] - f["open"]) <= wick_ratio * np.abs(body),
            "lower_wick": (f["close"] - f["low"]) <= wick_ratio * np.abs(body),
            "ema": (f["ema_fast"] < f["ema_mid"]) & (f["ema_mid"] < f["ema_slow"]),
            # 时间窗是"分钟"，换算成这个周期下的根数
            "window": (f["since_open"] > block_after_open / bar_minutes)
                      & (f["until_close"] > block_before_close / bar_minutes),
        }


def gates(f, **kwargs):
    kwargs.setdefault("bar_minutes", _BAR_MINUTES[0])
    # 基线窗口比 min_samples 还短时，样本门槛永远达不到，成交量那道门会全灭
    kwargs.setdefault("min_samples", _MIN_SAMPLES[0])
    return _gates_impl(f, **kwargs)


def two_bar_hits(f, g, *, leg_mode=False, body_mult=3.0):
    """Boolean mask of bars completing a two-bar pattern."""
    segment = f["segment"]
    prior = lambda values: np.r_[False, values[:-1]]
    same_segment = np.r_[False, segment[1:] == segment[:-1]]
    if leg_mode:
        # "腿"口径：两根合起来的净跌幅 >= N×ATR，单根 >= N/2×ATR。
        # 两处都用最后一根的 ATR，整条腿一把尺子量。
        body = f["close"] - f["open"]
        leg = f["close"] - np.r_[np.nan, f["open"][:-1]]
        with np.errstate(invalid="ignore"):
            half = (body < 0) & (np.abs(body) >= (body_mult / 2) * f["atr"])
            shape = prior(half) & half & (leg <= -body_mult * f["atr"])
    else:
        shape = prior(g["big"]) & g["big"]
    return (
        same_segment & shape
        & prior(g["volume"]) & g["volume"]
        & prior(g["upper_wick"]) & g["lower_wick"]
        & g["ema"] & g["window"]
    )


def _load_all(args):
    cache = {}
    for symbol in args.symbols:
        frame = load_minutes(
            symbol, minute_root=args.minute_root, start=args.start, end=args.end
        )
        if frame is not None and len(frame) > 500:
            cache[symbol] = frame
    if not cache:
        raise SystemExit(f"no minute partitions under {args.minute_root}")
    return cache


def _count(cached_features, *, body_mult, volume_mult, wick_ratio, leg_mode=False):
    return sum(
        int(two_bar_hits(
            f,
            gates(f, body_mult=body_mult, volume_mult=volume_mult,
                  wick_ratio=wick_ratio),
            leg_mode=leg_mode, body_mult=body_mult,
        ).sum())
        for f in cached_features.values()
    )


def _simulate(entry_index, entry, stop, f, *, arm=0.5, giveback=0.5,
              no_progress=5, horizon=30):
    """Replay the shipped exit rules and return the result in ATR units.

    ATR rather than R on purpose: entering closer to the stop shrinks R, which
    inflates every R-denominated statistic for free. Comparing entry variants
    in R units makes a worse entry look better.
    """
    close, high, low = f["close"], f["high"], f["low"]
    segment, atr = f["segment"], f["atr"]
    risk = stop - entry
    unit = atr[entry_index]
    if risk <= 0 or not np.isfinite(unit) or unit <= 0:
        return None
    peak = 0.0
    floor = None
    last = min(entry_index + horizon, len(close) - 1)
    for step, j in enumerate(range(entry_index + 1, last + 1), start=1):
        if segment[j] != segment[entry_index]:
            return (entry - close[j - 1]) / unit
        if high[j] >= stop:
            return -risk / unit
        if floor is not None and high[j] >= floor:
            return (entry - floor) / unit
        peak = max(peak, (entry - low[j]) / risk)
        if peak >= arm:
            floor = entry - max(0.0, peak - giveback) * risk
        if step >= no_progress and peak < arm:
            return (entry - close[j]) / unit
    return (entry - close[last]) / unit


def _climax_report(cached, *, wait=3):
    """Test whether selling-climax entries can be filtered out ex ante.

    Two parameter sets are scanned so a finding has to survive both. A gate
    that looks decisive on the small sample and vanishes on the large one is
    bucket noise, not a climax effect.
    """
    for body_mult, volume_mult, wick_ratio in ((4.0, 2.0, 0.15), (3.0, 1.5, 0.30)):
        rows = []
        variants = {name: [] for name in ("chase", "pull_1atr", "confirm")}
        for f in cached.values():
            g = gates(f, body_mult=body_mult, volume_mult=volume_mult,
                      wick_ratio=wick_ratio)
            mask = two_bar_hits(f, g, leg_mode=True, body_mult=body_mult)
            open_, high, low = f["open"], f["high"], f["low"]
            close, atr, segment = f["close"], f["atr"], f["segment"]
            body = close - open_
            bear = body < 0
            run = np.zeros(len(close), dtype=int)
            for i in range(len(close)):
                run[i] = (
                    run[i - 1] + 1
                    if i and bear[i] and segment[i] == segment[i - 1]
                    else int(bear[i])
                )
            for t in np.flatnonzero(mask):
                unit = atr[t]
                if not np.isfinite(unit) or unit <= 0:
                    continue
                stop = max(high[t - 1], high[t])
                base = _simulate(t, close[t], stop, f)
                if base is None:
                    continue
                rows.append({
                    "below_ema20": (f["ema_slow"][t] - close[t]) / unit,
                    "drop20": (close[max(t - 20, 0)] - close[t]) / unit,
                    "leg_atr": (open_[t - 1] - close[t]) / unit,
                    "bear_run": run[t],
                    "vol_ratio": f["volume"][t] / f["baseline"][t],
                    "result": base,
                })
                variants["chase"].append(base)
                limit = close[t] + unit
                filled = None
                for j in range(t + 1, min(t + 1 + wait, len(close))):
                    if segment[j] != segment[t]:
                        break
                    if high[j] >= limit:
                        filled = j
                        break
                # 没成交记 0：机会成本必须算进去
                variants["pull_1atr"].append(
                    0.0 if filled is None
                    else (_simulate(filled, limit, stop, f) or 0.0)
                )
                nxt = t + 1
                confirmed = (
                    nxt < len(close)
                    and segment[nxt] == segment[t]
                    and close[nxt] < low[t]
                )
                variants["confirm"].append(
                    (_simulate(nxt, close[nxt], stop, f) or 0.0)
                    if confirmed else 0.0
                )
        frame = pd.DataFrame(rows)
        print(f"\n===== leg N={body_mult} M={volume_mult} w={wick_ratio}"
              f"   n={len(frame)} =====")
        if len(frame) < 20:
            print("  样本太少")
            continue
        print("  -- 是否已经跌过头（按四分位，结果以 ATR 计） --")
        for column in ("below_ema20", "drop20", "leg_atr", "bear_run", "vol_ratio"):
            try:
                buckets = pd.qcut(
                    frame[column].rank(method="first"), 4,
                    labels=("Q1低", "Q2", "Q3", "Q4高"),
                )
            except ValueError:
                continue
            grouped = frame.groupby(buckets, observed=True)["result"].mean()
            cells = "  ".join(f"{name}:{value:+.2f}" for name, value in grouped.items())
            print(f"    {column:<12} {cells}")
        print("  -- 入场方式（未成交记 0，ATR 计） --")
        for name, label in (("chase", "追空 收盘-1tick"),
                            ("pull_1atr", "回撤 +1.0ATR 挂单"),
                            ("confirm", "下一根确认后再进")):
            values = np.array(variants[name])
            print(f"    {label:<18} 期望{values.mean():+.3f}  "
                  f"胜率{(values > 0).mean():.0%}  最差{values.min():+.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minute-root", default=str(DEFAULT_MINUTE_ROOT))
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end", default="2026-07-01")
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument(
        "--bar-minutes", type=int, default=1,
        help="Aggregate the one-minute partitions to this bar size first.",
    )
    parser.add_argument(
        "--atr-period", type=int, default=60,
        help="ATR window, in bars of --bar-minutes.",
    )
    parser.add_argument(
        "--baseline-bars", type=int, default=60,
        help="Volume baseline window, in bars of --bar-minutes.",
    )
    parser.add_argument(
        "--mode", default="funnel",
        choices=("funnel", "sweep", "presets", "edge", "climax"),
    )
    args = parser.parse_args()
    cache = {
        symbol: resample_bars(frame, minutes=args.bar_minutes)
        for symbol, frame in _load_all(args).items()
    }
    cached = {
        symbol: features(
            frame,
            atr_period=args.atr_period,
            baseline_bars=args.baseline_bars,
            bar_minutes=args.bar_minutes,
        )
        for symbol, frame in cache.items()
    }
    _BAR_MINUTES[0] = args.bar_minutes
    _MIN_SAMPLES[0] = min(15, args.baseline_bars)
    print(f"bar_minutes={args.bar_minutes} atr_period={args.atr_period} "
          f"baseline_bars={args.baseline_bars}")
    print(f"symbols={len(cache)} bars={sum(len(f['close']) for f in cached.values()):,}")

    if args.mode == "funnel":
        rows = {}
        for symbol, f in cached.items():
            g = gates(f)
            rows[symbol] = {
                "bars": len(f["close"]),
                "ema": int(g["ema"].sum()),
                "window": int(g["window"].sum()),
                "big_body": int(g["big"].sum()),
                "volume": int(g["volume"].sum()),
                "big+vol": int((g["big"] & g["volume"]).sum()),
                "+wick": int((g["big"] & g["volume"] & g["lower_wick"]).sum()),
                "two_bar": int(two_bar_hits(f, g).sum()),
            }
        table = pd.DataFrame(rows).T
        print(table.to_string())
        print("\nTOTAL")
        print(table.sum().to_string())
        return

    if args.mode == "sweep":
        for label, key, values, kwargs in (
            ("body_mult N", "body_mult", (1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0), {}),
            ("volume_mult M", "volume_mult", (1.0, 1.2, 1.5, 2.0, 2.5),
             {"body_mult": 1.5}),
            ("wick_ratio", "wick_ratio", (0.15, 0.25, 0.4, 0.6, 1.0),
             {"body_mult": 1.5}),
        ):
            print(f"\n== {label} ==")
            for value in values:
                base = {"body_mult": 3.0, "volume_mult": 2.0, "wick_ratio": 0.15}
                base.update(kwargs)
                base[key] = value
                print(f"  {key}={value:<6} hits={_count(cached, **base)}")
        return

    if args.mode == "presets":
        scale = 40 / len(cache)
        presets = (
            ("旧 per_bar N=3.0 M=2.0 w=0.15", 3.0, 2.0, 0.15, False),
            ("per_bar    N=2.0 M=2.0 w=0.15", 2.0, 2.0, 0.15, False),
            ("leg        N=5.0 M=2.0 w=0.15", 5.0, 2.0, 0.15, True),
            ("leg 出厂   N=4.0 M=2.0 w=0.15", 4.0, 2.0, 0.15, True),
            ("leg        N=4.0 M=1.5 w=0.25", 4.0, 1.5, 0.25, True),
            ("leg        N=3.0 M=1.5 w=0.30", 3.0, 1.5, 0.30, True),
        )
        print(f"\n{'preset':<32}{'hits':>8}{'≈40 symbols':>14}{'≈/month':>10}")
        for name, n, m, w, leg in presets:
            hits = _count(cached, body_mult=n, volume_mult=m, wick_ratio=w,
                          leg_mode=leg)
            print(f"{name:<32}{hits:>8}{int(hits * scale):>14}{hits * scale / 6:>10.0f}")
        return

    if args.mode == "climax":
        _climax_report(cached)
        return

    # edge: 入场后的正向/反向行程，判断放宽后的样本是不是更差
    for name, n, m, w, leg in (
        ("旧 per_bar N=3.0/M=2.0/w=0.15", 3.0, 2.0, 0.15, False),
        ("leg 出厂   N=4.0/M=2.0/w=0.15", 4.0, 2.0, 0.15, True),
        ("leg        N=4.0/M=1.5/w=0.25", 4.0, 1.5, 0.25, True),
        ("leg        N=3.0/M=1.5/w=0.30", 3.0, 1.5, 0.30, True),
    ):
        rows = []
        for f in cached.values():
            mask = two_bar_hits(
                f, gates(f, body_mult=n, volume_mult=m, wick_ratio=w),
                leg_mode=leg, body_mult=n,
            )
            for t in np.flatnonzero(mask):
                entry = f["close"][t]
                risk = max(f["high"][t - 1], f["high"][t]) - entry
                if risk <= 0:
                    continue
                row = {}
                for horizon in (5, 10, 20):
                    stop = min(t + horizon, len(f["close"]) - 1)
                    while stop > t and f["segment"][stop] != f["segment"][t]:
                        stop -= 1
                    if stop <= t:
                        continue
                    row[f"mfe{horizon}"] = (
                        entry - f["low"][t + 1: stop + 1].min()
                    ) / risk
                    row[f"mae{horizon}"] = (
                        f["high"][t + 1: stop + 1].max() - entry
                    ) / risk
                rows.append(row)
        frame = pd.DataFrame(rows)
        if frame.empty:
            print(f"\n{name}: no samples")
            continue
        print(f"\n{name}  n={len(frame)}")
        print(f"  5 根内 MFE>=0.5R : {(frame['mfe5'] >= 0.5).mean():.1%}"
              f"   median MFE5={frame['mfe5'].median():.2f}R")
        print(f" 10 根内 MFE>=0.5R : {(frame['mfe10'] >= 0.5).mean():.1%}"
              f"   median MFE10={frame['mfe10'].median():.2f}R")
        print(f" 20 根内 MFE>=1.0R : {(frame['mfe20'] >= 1.0).mean():.1%}"
              f"   median MFE20={frame['mfe20'].median():.2f}R")
        print(f" 20 根内 MAE>=1.0R : {(frame['mae20'] >= 1.0).mean():.1%}"
              f"   median MAE20={frame['mae20'].median():.2f}R")


if __name__ == "__main__":
    main()
