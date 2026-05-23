"""Daily parity report between live(sim) and replay(backtest) signals.

P2.4 enhancements:
- column aliases for live/replay csv schemas
- time_tolerance auto-derived from interval if "auto"
- consecutive alert days streak state in out_dir
- optional DingTalk / WeCom webhook on alert
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence

import pandas as pd

from cta.sim.parity_check import SignalRecord, compare_signals

logger = logging.getLogger(__name__)


# P2.4 column aliases — sim 与 backtest 引擎落盘的列名通常不一致；这里集中处理。
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "datetime": ("datetime", "timestamp", "ts", "time"),
    "side": ("side", "direction", "action"),
    "lots": ("lots", "position_qty", "qty", "volume"),
    "order_type": ("order_type", "order_kind", "type"),
}


# P2.4 time tolerance auto-derived from interval ("auto" string).
TIME_TOLERANCE_BY_INTERVAL: dict[str, pd.Timedelta] = {
    "day": pd.Timedelta("1D"),
    "60min": pd.Timedelta("30min"),
    "minute60": pd.Timedelta("30min"),
    "30min": pd.Timedelta("15min"),
    "minute30": pd.Timedelta("15min"),
    "15min": pd.Timedelta("7min"),
    "minute15": pd.Timedelta("7min"),
    "5min": pd.Timedelta("1min"),
    "minute5": pd.Timedelta("1min"),
    "min": pd.Timedelta("5s"),
    "minute": pd.Timedelta("5s"),
}


@dataclass(frozen=True)
class DailyParityReport:
    title: str
    matched: int
    mismatched: int
    only_in_live: int
    only_in_replay: int
    mismatch_rate: float
    alert_triggered: bool
    consecutive_alert_days: int
    report_path: str
    diff_csv_path: str


def _resolve_time_tolerance(value: str | pd.Timedelta, interval: str | None) -> pd.Timedelta:
    if isinstance(value, pd.Timedelta):
        return value
    s = str(value).strip().lower()
    if s == "auto":
        if interval is None:
            return pd.Timedelta("1min")
        return TIME_TOLERANCE_BY_INTERVAL.get(interval.lower(), pd.Timedelta("1min"))
    return pd.Timedelta(s)


def _update_streak_state(out_dir: Path, alert: bool) -> int:
    """Maintain consecutive_alert_days counter under out_dir/_streak_state.json."""
    state_path = out_dir / "_streak_state.json"
    streak = 0
    if state_path.exists():
        try:
            streak = int(json.loads(state_path.read_text(encoding="utf-8")).get("streak", 0))
        except Exception:
            streak = 0
    streak = (streak + 1) if alert else 0
    try:
        state_path.write_text(
            json.dumps({"streak": streak, "updated_at": datetime.now().isoformat(timespec="seconds")}),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("write streak state failed: %s", exc)
    return streak


def _post_webhook(url: str, payload: dict, timeout: float = 5.0) -> bool:
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return 200 <= int(resp.status) < 300
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        logger.warning("webhook post failed: %s", exc)
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("webhook post failed: %s", exc)
        return False


def _send_alert_webhooks(*, title: str, body: str, env: Mapping[str, str] | None = None) -> None:
    env = dict(env or os.environ)
    dingtalk_url = env.get("CTA_DINGTALK_WEBHOOK", "").strip()
    wecom_url = env.get("CTA_WECOM_WEBHOOK", "").strip()
    if dingtalk_url:
        _post_webhook(
            dingtalk_url,
            {"msgtype": "markdown", "markdown": {"title": title, "text": f"## {title}\n\n{body}"}},
        )
    if wecom_url:
        _post_webhook(
            wecom_url,
            {"msgtype": "markdown", "markdown": {"content": f"## {title}\n\n{body}"}},
        )


def build_daily_parity_report(
    *,
    live_signals: Sequence[SignalRecord],
    replay_signals: Sequence[SignalRecord],
    out_dir: Path,
    title: str,
    mismatch_alert_threshold: float = 0.05,
    time_tolerance: pd.Timedelta | str = "auto",
    interval: str | None = None,
    consecutive_alert_threshold: int = 3,
    enable_webhook: bool = True,
) -> DailyParityReport:
    """Build markdown+csv parity report for daily operations."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    tolerance = _resolve_time_tolerance(time_tolerance, interval)
    res = compare_signals(list(live_signals), list(replay_signals), time_tolerance=tolerance)
    mismatch_rate = float(res.mismatch_rate)
    alert = bool(mismatch_rate > float(mismatch_alert_threshold))

    streak = _update_streak_state(out, alert)
    p0_alert = bool(alert and streak >= int(consecutive_alert_threshold))

    diff_csv = out / f"parity_diff_{ts}.csv"
    res.details.to_csv(diff_csv, index=False, encoding="utf-8-sig")

    report_path = out / f"parity_report_{ts}.md"
    rows_preview = "no diff rows"
    if not res.details.empty:
        rows_preview = res.details.head(50).to_markdown(index=False)
    body = (
        f"# {title}\n\n"
        f"- generated_at: {datetime.now().isoformat(timespec='seconds')}\n"
        f"- matched: {int(res.matched)}\n"
        f"- mismatched: {int(res.mismatched)}\n"
        f"- only_in_live: {int(res.only_in_a)}\n"
        f"- only_in_replay: {int(res.only_in_b)}\n"
        f"- mismatch_rate: {mismatch_rate:.6f}\n"
        f"- alert_threshold: {float(mismatch_alert_threshold):.6f}\n"
        f"- alert_triggered: {int(alert)}\n"
        f"- consecutive_alert_days: {int(streak)} (P0_alert={int(p0_alert)})\n"
        f"- time_tolerance: {tolerance}\n"
        f"- interval: {interval or 'n/a'}\n\n"
        f"## Diff (top 50)\n\n{rows_preview}\n"
    )
    report_path.write_text(body, encoding="utf-8")

    if enable_webhook and p0_alert:
        webhook_title = f"[P0] CTA parity alert × {streak} days"
        webhook_body = (
            f"- title: {title}\n- mismatch_rate: {mismatch_rate:.4f} "
            f"(threshold {mismatch_alert_threshold:.4f})\n"
            f"- consecutive_alert_days: {streak}\n- report: {report_path.name}"
        )
        _send_alert_webhooks(title=webhook_title, body=webhook_body)

    return DailyParityReport(
        title=title,
        matched=int(res.matched),
        mismatched=int(res.mismatched),
        only_in_live=int(res.only_in_a),
        only_in_replay=int(res.only_in_b),
        mismatch_rate=mismatch_rate,
        alert_triggered=alert,
        consecutive_alert_days=int(streak),
        report_path=str(report_path),
        diff_csv_path=str(diff_csv),
    )


def _resolve_alias(cols_lower: dict[str, str], key: str) -> str | None:
    for alias in COLUMN_ALIASES.get(key, (key,)):
        c = cols_lower.get(alias.lower())
        if c:
            return c
    return None


def load_signals_from_csv(path: Path) -> list[SignalRecord]:
    df = pd.read_csv(path, encoding="utf-8-sig")
    cols_lower = {str(c).lower(): str(c) for c in df.columns}
    dt_col = _resolve_alias(cols_lower, "datetime")
    side_col = _resolve_alias(cols_lower, "side")
    if not dt_col or not side_col:
        raise ValueError(
            f"signal csv missing required columns (any of {COLUMN_ALIASES['datetime']} and "
            f"{COLUMN_ALIASES['side']}): {path}"
        )
    lots_col = _resolve_alias(cols_lower, "lots")
    order_col = _resolve_alias(cols_lower, "order_type")
    out: list[SignalRecord] = []
    for _, r in df.iterrows():
        ts = pd.to_datetime(r[dt_col], errors="coerce")
        if pd.isna(ts):
            continue
        side = str(r[side_col]).strip().lower()
        lots = int(pd.to_numeric(r[lots_col], errors="coerce")) if lots_col else 1
        order_type = str(r[order_col]).strip().lower() if order_col else "market"
        out.append(SignalRecord(timestamp=pd.Timestamp(ts), side=side, lots=lots, order_type=order_type))
    return out


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build daily parity report from live/replay signal CSVs")
    p.add_argument("--live-signals-csv", required=True, help="live/sim signal csv path")
    p.add_argument("--replay-signals-csv", required=True, help="replay/backtest signal csv path")
    p.add_argument("--out-dir", required=True, help="output directory")
    p.add_argument("--title", default="daily parity report")
    p.add_argument("--mismatch-alert-threshold", type=float, default=0.05)
    p.add_argument(
        "--time-tolerance",
        default="auto",
        help='pandas timedelta string, or "auto" to derive from --interval',
    )
    p.add_argument(
        "--interval",
        default=None,
        help="strategy interval (day/60min/30min/15min/5min/min); used for --time-tolerance=auto",
    )
    p.add_argument(
        "--consecutive-alert-threshold",
        type=int,
        default=3,
        help="trigger P0 webhook only after this many consecutive alert days (default 3)",
    )
    p.add_argument("--no-webhook", action="store_true", help="disable webhook even when env vars set")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    live = load_signals_from_csv(Path(args.live_signals_csv))
    replay = load_signals_from_csv(Path(args.replay_signals_csv))
    out = build_daily_parity_report(
        live_signals=live,
        replay_signals=replay,
        out_dir=Path(args.out_dir),
        title=str(args.title),
        mismatch_alert_threshold=float(args.mismatch_alert_threshold),
        time_tolerance=str(args.time_tolerance),
        interval=str(args.interval) if args.interval else None,
        consecutive_alert_threshold=int(args.consecutive_alert_threshold),
        enable_webhook=(not bool(args.no_webhook)),
    )
    print(f"report: {out.report_path}")
    print(f"diff: {out.diff_csv_path}")
    print(f"mismatch_rate: {out.mismatch_rate:.6f}")
    print(f"alert_triggered: {int(out.alert_triggered)}")
    print(f"consecutive_alert_days: {int(out.consecutive_alert_days)}")


if __name__ == "__main__":
    main()


__all__ = [
    "DailyParityReport",
    "build_daily_parity_report",
    "load_signals_from_csv",
    "main",
    "COLUMN_ALIASES",
    "TIME_TOLERANCE_BY_INTERVAL",
]
