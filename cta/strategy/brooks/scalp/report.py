"""Atomic, failure-first report publication for scalp research runs."""
from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont


CHART_BACKGROUND = "#f7f4ed"
CHART_PANEL = "#fffdf7"
CHART_INK = "#1f2933"
CHART_MUTED = "#64748b"
CHART_GRID = "#d8d2c4"

_CSV_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "contract_mapping.csv": ("root_symbol", "effective_session"),
    "daily_trading_specs.csv": ("contract_code", "exchange_trade_date"),
    "candidates.csv": ("candidate_id",),
    "rejections.csv": ("candidate_id",),
    "orders.csv": ("order_id",),
    "order_events.csv": ("event_id",),
    "fills.csv": ("fill_id",),
    "trades.csv": ("trade_id",),
    "daily_equity.csv": ("date",),
    "daily_metrics.csv": ("date",),
    "monthly_metrics.csv": ("month",),
    "stress_results.csv": ("scenario",),
}

_CSV_TIME_COLUMNS: dict[str, str] = {
    "contract_mapping.csv": "session_open",
    "candidates.csv": "setup_bar_end",
    "fills.csv": "datetime",
    "order_events.csv": "datetime",
    "trades.csv": "entry_time",
    "daily_equity.csv": "date",
    "daily_metrics.csv": "date",
    "monthly_metrics.csv": "month",
}


class ReportValidationError(RuntimeError):
    pass


class AtomicReportPublisher:
    """Publish a fully validated staging directory without damaging an old run."""

    def __init__(self, target: str | Path, *, required_files: set[str]) -> None:
        self.target = Path(target)
        self.required_files = set(required_files)
        if not self.required_files:
            raise ValueError("required_files must not be empty")

    @contextmanager
    def staging_directory(self) -> Iterator[Path]:
        self.target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{self.target.name}.", dir=self.target.parent))
        try:
            yield staging
            self.validate(staging)
            self._replace(staging)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise

    def validate(self, staging: Path) -> None:
        present = {
            str(path.relative_to(staging))
            for path in staging.rglob("*")
            if path.is_file()
        }
        missing = sorted(self.required_files - present)
        if missing:
            raise ReportValidationError(f"missing required report files: {missing}")
        extras = sorted(present - self.required_files)
        if extras:
            raise ReportValidationError(f"unknown report files: {extras}")
        csv_frames: dict[str, pd.DataFrame] = {}
        for relative in self.required_files:
            path = staging / relative
            if path.stat().st_size == 0:
                raise ReportValidationError(f"empty report file: {relative}")
            if path.suffix == ".json":
                try:
                    json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise ReportValidationError(f"invalid JSON: {relative}") from exc
            elif path.suffix == ".csv":
                try:
                    frame = pd.read_csv(path)
                except Exception as exc:
                    raise ReportValidationError(f"invalid CSV: {relative}") from exc
                csv_frames[relative] = frame
                self._validate_csv(relative, frame)
                numeric = frame.select_dtypes(include=[np.number])
                values = numeric.to_numpy()
                if values.size and np.isinf(values[~pd.isna(values)]).any():
                    raise ReportValidationError(f"non-finite CSV amount: {relative}")
            elif path.suffix == ".yaml":
                try:
                    from .config import load_config

                    load_config(path)
                except Exception as exc:
                    raise ReportValidationError(f"invalid config YAML: {relative}") from exc
            elif path.suffix == ".parquet":
                try:
                    frame = pd.read_parquet(path)
                except Exception as exc:
                    raise ReportValidationError(f"invalid Parquet: {relative}") from exc
                if relative == "minute_equity.parquet":
                    required = {"datetime", "marked_equity"}
                    if not required.issubset(frame):
                        raise ReportValidationError(
                            "minute equity reconciliation columns are missing"
                        )
                    timestamps = pd.to_datetime(frame["datetime"], errors="coerce", utc=True)
                    if (
                        frame.empty
                        or timestamps.isna().any()
                        or timestamps.duplicated().any()
                        or not timestamps.is_monotonic_increasing
                    ):
                        raise ReportValidationError("invalid minute equity ordering")
                    marked = pd.to_numeric(frame["marked_equity"], errors="coerce")
                    if marked.isna().any() or not np.isfinite(marked).all():
                        raise ReportValidationError("invalid minute marked equity")
            elif path.suffix == ".png":
                try:
                    with Image.open(path) as image:
                        if image.format != "PNG":
                            raise ValueError("image format is not PNG")
                        image.verify()
                except Exception as exc:
                    raise ReportValidationError(f"invalid PNG: {relative}") from exc
        self._validate_cross_file_reconciliation(staging, csv_frames)

    @staticmethod
    def _validate_csv(relative: str, frame: pd.DataFrame) -> None:
        key = _CSV_PRIMARY_KEYS.get(relative)
        if key is not None and set(key).issubset(frame.columns):
            if frame.loc[:, key].isna().any().any():
                raise ReportValidationError(f"empty primary key: {relative}")
            if frame.duplicated(list(key)).any():
                raise ReportValidationError(f"duplicate primary key: {relative}")
        time_column = _CSV_TIME_COLUMNS.get(relative)
        if time_column is not None and time_column in frame and not frame.empty:
            values = pd.to_datetime(frame[time_column], errors="coerce", utc=True)
            if values.isna().any() or not values.is_monotonic_increasing:
                raise ReportValidationError(f"invalid time ordering: {relative}")

    @staticmethod
    def _validate_cross_file_reconciliation(
        staging: Path,
        frames: dict[str, pd.DataFrame],
    ) -> None:
        summary_path = staging / "summary.json"
        if summary_path.is_file():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            expected_counts = {
                "candidate_count": "candidates.csv",
                "trade_count": "trades.csv",
            }
            for field, filename in expected_counts.items():
                if field in summary and filename in frames:
                    if int(summary[field]) != len(frames[filename]):
                        raise ReportValidationError(
                            f"summary count mismatch: {field}"
                        )
            config_path = staging / "config_snapshot.yaml"
            declared_hash = summary.get("config_sha256")
            if config_path.is_file() and declared_hash is not None:
                from .config import config_sha256, load_config

                actual_hash = config_sha256(load_config(config_path))
                if str(declared_hash) != actual_hash:
                    raise ReportValidationError("config hash mismatch")
                version = summary.get("strategy_version")
                if version is not None and str(version) != f"scalp-{actual_hash[:12]}":
                    raise ReportValidationError("strategy version/config hash mismatch")
        fills = frames.get("fills.csv")
        trades = frames.get("trades.csv")
        if fills is None or trades is None or trades.empty:
            return
        required_fill = {"candidate_id", "offset", "quantity", "total_fee"}
        required_trade = {"candidate_id", "quantity", "total_fee"}
        if not required_fill.issubset(fills) or not required_trade.issubset(trades):
            raise ReportValidationError("fills/trades reconciliation columns are missing")
        for trade in trades.itertuples(index=False):
            matched = fills.loc[fills["candidate_id"].astype(str).eq(str(trade.candidate_id))]
            opened = matched.loc[matched["offset"].astype(str).str.upper().eq("OPEN")]
            closed = matched.loc[~matched.index.isin(opened.index)]
            if int(opened["quantity"].sum()) != int(trade.quantity):
                raise ReportValidationError("fills/trades open quantity mismatch")
            if int(closed["quantity"].sum()) != int(trade.quantity):
                raise ReportValidationError("fills/trades close quantity mismatch")
            if not np.isclose(
                float(matched["total_fee"].sum()),
                float(trade.total_fee),
                rtol=1e-9,
                atol=1e-8,
            ):
                raise ReportValidationError("fills/trades fee mismatch")

    def _replace(self, staging: Path) -> None:
        backup = self.target.with_name(f".{self.target.name}.backup")
        if backup.exists():
            shutil.rmtree(backup)
        if self.target.exists():
            self.target.rename(backup)
        try:
            staging.rename(self.target)
        except Exception:
            if backup.exists() and not self.target.exists():
                backup.rename(self.target)
            raise
        if backup.exists():
            shutil.rmtree(backup)


def write_json(path: str | Path, payload: Any) -> None:
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def render_report_markdown(
    *,
    status: str,
    failures: list[str],
    source_audit: dict[str, Any],
    symbol_summaries: dict[str, dict[str, Any]],
    portfolio_summary: dict[str, Any],
    risk_score: dict[str, Any],
    stress_results: list[dict[str, Any]],
    limitations: list[str],
) -> str:
    """Render the mandatory failure-first report section order."""
    lines = [
        "# Brooks 纯价格行为日内剥头皮回测报告",
        "",
        "## 1. 最终状态和未通过项",
        f"- 状态：`{status}`",
    ]
    lines.extend(f"- {failure}" for failure in failures or ["无"])
    lines.extend(
        [
            "",
            "## 2. 数据、实际日期和因果审计",
            f"```json\n{json.dumps(source_audit, ensure_ascii=False, sort_keys=True, indent=2)}\n```",
        ]
    )
    for number, symbol in ((3, "RB"), (4, "CU")):
        lines.extend(
            [
                "",
                f"## {number}. {symbol} 单品种结果",
                f"```json\n{json.dumps(symbol_summaries.get(symbol, {}), ensure_ascii=False, sort_keys=True, indent=2)}\n```",
            ]
        )
    lines.extend(
        [
            "",
            "## 5. RB+CU 共享账户结果",
            f"```json\n{json.dumps(portfolio_summary, ensure_ascii=False, sort_keys=True, indent=2)}\n```",
            "",
            "## 6. Setup、方向、Session 和年度分组",
            "详见 `group_metrics.csv`。",
            "",
            "## 7. 风险和一票否决项",
            f"```json\n{json.dumps(risk_score, ensure_ascii=False, sort_keys=True, indent=2)}\n```",
            "",
            "## 8. 压力测试",
            f"```json\n{json.dumps(stress_results, ensure_ascii=False, sort_keys=True, indent=2)}\n```",
            "",
            "## 9. 日均 1% 和月均 20% 目标检查",
            "目标仅作为研究门槛，不构成收益承诺。详见 `summary.json`。",
            "",
            "## 10. 局限和下一步",
        ]
    )
    lines.extend(f"- {item}" for item in limitations or ["无"])
    return "\n".join(lines) + "\n"


def plot_portfolio_equity(daily_equity: pd.DataFrame, output_path: str | Path) -> None:
    frame = daily_equity.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame["equity"] = pd.to_numeric(frame["equity"], errors="coerce")
    frame = frame.dropna(subset=["date", "equity"]).sort_values("date")
    if frame.empty:
        frame = pd.DataFrame({"date": [pd.Timestamp("1970-01-01")], "equity": [0.0]})
    frame["drawdown"] = frame["equity"] / frame["equity"].cummax() - 1.0

    image = Image.new("RGB", (1440, 840), CHART_BACKGROUND)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    draw.text((48, 24), "Portfolio marked equity and drawdown", fill=CHART_INK, font=font)
    _draw_line_panel(
        draw,
        (48, 64, 1392, 570),
        frame["equity"].tolist(),
        label="Marked equity",
        color="#174a3b",
    )
    _draw_line_panel(
        draw,
        (48, 600, 1392, 792),
        frame["drawdown"].tolist(),
        label="Drawdown",
        color="#b6402c",
        baseline=0.0,
    )
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")


def plot_symbol_timeframes(
    minute_bars: pd.DataFrame,
    fills: pd.DataFrame,
    output_path: str | Path,
    *,
    title: str,
) -> None:
    """Render 30m/5m/1m OHLC candles with entry/exit markers and volume."""
    frame = minute_bars.copy()
    if frame.empty:
        frame = pd.DataFrame(
            {
                "bar_end": [pd.Timestamp("1970-01-01", tz="UTC")],
                "open": [0.0],
                "high": [0.0],
                "low": [0.0],
                "close": [0.0],
                "volume": [0.0],
            }
        )
    frame["bar_end"] = pd.to_datetime(frame["bar_end"], utc=True, errors="coerce")
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce").fillna(0.0)
    frame = (
        frame.dropna(subset=["bar_end", "open", "high", "low", "close"])
        .sort_values("bar_end")
        .set_index("bar_end")
    )
    image = Image.new("RGB", (1680, 1120), CHART_BACKGROUND)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    draw.text((42, 22), title, fill=CHART_INK, font=font)
    panels = ((42, 62, 1638, 330), (42, 350, 1638, 618), (42, 638, 1638, 906))
    displayed_1m = pd.DataFrame()
    for rect, interval in zip(panels, ("30min", "5min", "1min"), strict=True):
        sampled = _resample_ohlcv(frame, interval)
        displayed = _compress_candles(sampled, max_candles=1_100)
        if interval == "1min":
            displayed_1m = displayed
        _draw_candlestick_panel(
            draw,
            rect,
            displayed,
            label=f"{interval} OHLC ({len(sampled):,} bars)",
        )
        if not fills.empty and {"datetime", "fill_price", "offset"}.issubset(fills):
            marked = fills.copy()
            marked["datetime"] = pd.to_datetime(marked["datetime"], utc=True, errors="coerce")
            marked["fill_price"] = pd.to_numeric(marked["fill_price"], errors="coerce")
            marked = marked.dropna(subset=["datetime", "fill_price"])
            entries = marked.loc[marked["offset"].astype(str).str.upper().eq("OPEN")]
            exits = marked.loc[~marked.index.isin(entries.index)]
            _draw_trade_markers(draw, rect, displayed, entries, color="#14866d", upward=True)
            _draw_trade_markers(draw, rect, displayed, exits, color="#b6402c", upward=False)
    _draw_volume_panel(
        draw,
        (42, 926, 1638, 1078),
        displayed_1m.get("volume", pd.Series(dtype=float)).tolist(),
    )
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")


def plot_trade_timeframes(
    minute_bars: pd.DataFrame,
    trade: pd.Series,
    output_path: str | Path,
    *,
    sequence: int,
) -> None:
    """Render one completed trade with local day/5m/1m context and volume."""
    frame = minute_bars.copy()
    required = {
        "bar_end",
        "exchange_trade_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"trade chart bars are missing columns: {missing}")
    frame["bar_end"] = _shanghai_datetimes(frame["bar_end"])
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = (
        frame.dropna(subset=["bar_end", "open", "high", "low", "close", "volume"])
        .sort_values("bar_end")
        .drop_duplicates("bar_end", keep="last")
        .set_index("bar_end")
    )
    if frame.empty:
        raise ValueError("trade chart has no valid minute bars")

    entry_time = _shanghai_timestamp(trade["entry_time"])
    exit_time = _shanghai_timestamp(trade["exit_time"])
    if exit_time < entry_time:
        raise ValueError("trade exit precedes entry")
    if entry_time < frame.index.min() or exit_time > frame.index.max():
        raise ValueError("trade entry/exit falls outside chart bars")
    daily_frame = _trade_date_source_window(
        frame,
        trade_date=pd.Timestamp(trade["exchange_trade_date"]),
        before=24,
        after=8,
    )
    intraday_frame = _trade_source_window(
        frame,
        entry_time=entry_time,
        exit_time=exit_time,
        before=600,
        after=400,
    )

    direction = str(trade["direction"]).upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError(f"unsupported trade direction: {direction}")
    entry_action, exit_action = (
        ("BUY", "SELL") if direction == "LONG" else ("SELL", "BUY")
    )
    markers = (
        (entry_time, float(trade["entry_price"]), entry_action),
        (exit_time, float(trade["exit_price"]), exit_action),
    )

    image = Image.new("RGB", (1680, 1120), CHART_BACKGROUND)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    contract = str(trade["contract_code"])
    trade_day = entry_time.strftime("%Y-%m-%d")
    draw.text(
        (42, 18),
        f"RB trade {sequence:03d} | {contract} | {direction} | {trade_day}",
        fill=CHART_INK,
        font=font,
    )
    draw.text(
        (42, 36),
        (
            f"{entry_action} {entry_time:%H:%M} @ {float(trade['entry_price']):,.2f}  |  "
            f"{exit_action} {exit_time:%H:%M} @ {float(trade['exit_price']):,.2f}  |  "
            f"net {float(trade['net_pnl']):+,.2f} ({float(trade['net_r']):+.3f}R)  |  "
            f"{trade['exit_reason']}"
        ),
        fill=CHART_MUTED,
        font=font,
    )

    panels = (
        ((42, 62, 1638, 330), "day", 20, 5, False),
        ((42, 350, 1638, 618), "5min", 24, 12, True),
        ((42, 638, 1638, 906), "1min", 45, 30, True),
    )
    displayed_1m = pd.DataFrame()
    for rect, interval, before, after, show_time in panels:
        source = daily_frame if interval == "day" else intraday_frame
        sampled = _resample_ohlcv(source, interval)
        displayed = _trade_window(
            sampled,
            entry_time=entry_time,
            exit_time=exit_time,
            before=before,
            after=after,
        )
        if interval == "1min":
            displayed_1m = displayed
        _draw_candlestick_panel(
            draw,
            rect,
            displayed,
            label=f"{interval} OHLC ({len(displayed):,} nearby bars)",
            show_time=show_time,
        )
        for timestamp, price, action in markers:
            marker = pd.DataFrame(
                {"datetime": [timestamp], "fill_price": [price]}
            )
            _draw_trade_markers(
                draw,
                rect,
                displayed,
                marker,
                color="#1d4ed8" if action == "BUY" else "#d97706",
                upward=action == "BUY",
                label=action,
            )
    _draw_volume_panel(
        draw,
        (42, 926, 1638, 1078),
        displayed_1m.get("volume", pd.Series(dtype=float)).tolist(),
    )
    draw.text((1390, 36), "BUY", fill="#1d4ed8", font=font)
    draw.text((1450, 36), "SELL", fill="#d97706", font=font)

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")


def _shanghai_datetimes(values: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(values, errors="coerce")
    if parsed.dt.tz is None:
        return parsed.dt.tz_localize("Asia/Shanghai")
    return parsed.dt.tz_convert("Asia/Shanghai")


def _shanghai_timestamp(value: Any) -> pd.Timestamp:
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is None:
        return parsed.tz_localize("Asia/Shanghai")
    return parsed.tz_convert("Asia/Shanghai")


def _trade_window(
    frame: pd.DataFrame,
    *,
    entry_time: pd.Timestamp,
    exit_time: pd.Timestamp,
    before: int,
    after: int,
) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    entry_index = int(frame.index.searchsorted(entry_time, side="left"))
    exit_index = int(frame.index.searchsorted(exit_time, side="right"))
    start = max(0, entry_index - before)
    stop = min(len(frame), exit_index + after)
    return frame.iloc[start:stop].copy()


def _trade_source_window(
    frame: pd.DataFrame,
    *,
    entry_time: pd.Timestamp,
    exit_time: pd.Timestamp,
    before: int,
    after: int,
) -> pd.DataFrame:
    """Bound intraday resampling work around the completed trade."""
    if frame.empty:
        return frame.copy()
    entry_index = int(frame.index.searchsorted(entry_time, side="left"))
    exit_index = int(frame.index.searchsorted(exit_time, side="right"))
    start = max(0, entry_index - before)
    stop = min(len(frame), exit_index + after)
    return frame.iloc[start:stop].copy()


def _trade_date_source_window(
    frame: pd.DataFrame,
    *,
    trade_date: pd.Timestamp,
    before: int,
    after: int,
) -> pd.DataFrame:
    """Return complete exchange trade dates around one trade."""
    dates = pd.to_datetime(frame["exchange_trade_date"], errors="coerce").dt.normalize()
    if dates.isna().any():
        raise ValueError("trade chart contains invalid exchange_trade_date values")
    available = pd.DatetimeIndex(dates.drop_duplicates().sort_values())
    normalized = pd.Timestamp(trade_date).normalize().tz_localize(None)
    position = int(available.searchsorted(normalized, side="left"))
    if position >= len(available) or available[position] != normalized:
        raise ValueError(f"trade date is absent from chart bars: {normalized.date()}")
    selected = available[
        max(0, position - before) : min(len(available), position + after + 1)
    ]
    return frame.loc[dates.isin(selected)].copy()


def _resample_ohlcv(frame: pd.DataFrame, interval: str) -> pd.DataFrame:
    required = {"open", "high", "low", "close", "volume"}
    if not isinstance(frame.index, pd.DatetimeIndex) or not required.issubset(frame):
        raise ValueError("OHLCV chart frame requires a DatetimeIndex and OHLCV columns")
    source = frame.sort_index().copy()
    if interval == "1min":
        return source.loc[:, sorted(required)].copy()
    if interval == "day":
        if "exchange_trade_date" not in source:
            raise ValueError("daily OHLCV chart requires exchange_trade_date")
        source["_trade_date"] = pd.to_datetime(
            source["exchange_trade_date"], errors="coerce"
        ).dt.normalize()
        if source["_trade_date"].isna().any():
            raise ValueError("daily OHLCV chart contains an invalid trade date")
        aggregations: dict[str, tuple[str, str]] = {
            "open": ("open", "first"),
            "high": ("high", "max"),
            "low": ("low", "min"),
            "close": ("close", "last"),
            "volume": ("volume", "sum"),
            "bar_end": ("_bar_end", "max"),
        }
        source["_bar_end"] = source.index
        if "contract_code" in source:
            aggregations["_contract_count"] = ("contract_code", "nunique")
        result = source.groupby("_trade_date", sort=True).agg(**aggregations)
        if "_contract_count" in result:
            result = result.loc[result["_contract_count"].eq(1)].drop(
                columns="_contract_count"
            )
        return result.set_index("bar_end").loc[:, sorted(required)]
    period = pd.Timedelta(interval)
    expected = int(period.total_seconds() // 60)
    if expected <= 0 or period != pd.Timedelta(minutes=expected):
        raise ValueError(f"unsupported chart interval: {interval}")

    def aggregate(group: pd.DataFrame, origin: pd.Timestamp | str) -> pd.DataFrame:
        result = group.resample(
            interval,
            closed="right",
            label="right",
            origin=origin,
        ).agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
            _count=("close", "count"),
        )
        return result.loc[result["_count"].eq(expected)].drop(columns="_count")

    segment_columns = {"session_id", "segment_id", "segment_start"}
    if segment_columns.issubset(source):
        pieces = []
        for _, group in source.groupby(["session_id", "segment_id"], sort=False):
            origin = pd.Timestamp(group["segment_start"].iloc[0])
            if origin.tzinfo is None:
                origin = origin.tz_localize(source.index.tz)
            else:
                origin = origin.tz_convert(source.index.tz)
            pieces.append(aggregate(group, origin))
        if not pieces:
            return pd.DataFrame(columns=sorted(required), index=source.index[:0])
        return pd.concat(pieces).sort_index()
    return aggregate(source, source.index[0].floor(interval))


def _compress_candles(frame: pd.DataFrame, *, max_candles: int) -> pd.DataFrame:
    if max_candles <= 0:
        raise ValueError("max_candles must be positive")
    if len(frame) <= max_candles:
        return frame.copy()
    work = frame.copy()
    work["_bar_end"] = work.index
    work["_bucket"] = np.arange(len(work), dtype=np.int64) * max_candles // len(work)
    compressed = work.groupby("_bucket", sort=True).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        bar_end=("_bar_end", "last"),
    )
    return compressed.set_index("bar_end")


def _draw_candlestick_panel(
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    candles: pd.DataFrame,
    *,
    label: str,
    show_time: bool = False,
    plot_slots: Sequence[float] | None = None,
    plot_slot_count: int | None = None,
    price_levels: Sequence[float] = (),
) -> None:
    left, top, right, bottom = rect
    draw.rounded_rectangle(rect, radius=10, fill=CHART_PANEL, outline=CHART_GRID)
    draw.text((left + 12, top + 8), label, fill=CHART_INK, font=ImageFont.load_default())
    chart = (left + 68, top + 30, right - 18, bottom - 28)
    for step in range(5):
        y = chart[1] + (chart[3] - chart[1]) * step / 4
        draw.line((chart[0], y, chart[2], y), fill=CHART_GRID, width=1)
    if candles.empty:
        draw.text((chart[0] + 12, chart[1] + 12), "No data", fill=CHART_MUTED)
        return
    finite_levels = [float(value) for value in price_levels if np.isfinite(value)]
    low = min([float(candles["low"].min()), *finite_levels])
    high = max([float(candles["high"].max()), *finite_levels])
    if high == low:
        padding = max(abs(high) * 0.01, 1.0)
        low -= padding
        high += padding
    if plot_slots is None:
        slots = np.arange(len(candles), dtype=float)
    else:
        slots = np.asarray(plot_slots, dtype=float)
        if len(slots) != len(candles) or not np.isfinite(slots).all():
            raise ValueError("plot_slots must contain one finite slot per candle")
    slot_count = int(plot_slot_count or len(candles))
    if slot_count <= 0 or np.any(slots < 0) or np.any(slots >= slot_count):
        raise ValueError("plot slots must fit inside plot_slot_count")
    span = chart[2] - chart[0]
    width = max(1.0, min(8.0, span / slot_count * 0.70))

    def y_at(value: float) -> float:
        return chart[3] - (value - low) / (high - low) * (chart[3] - chart[1])

    for slot, candle in zip(slots, candles.itertuples(index=False), strict=True):
        x = chart[0] + (slot + 0.5) / slot_count * span
        open_y = y_at(float(candle.open))
        close_y = y_at(float(candle.close))
        high_y = y_at(float(candle.high))
        low_y = y_at(float(candle.low))
        color = "#c53d2f" if candle.close >= candle.open else "#16876f"
        draw.line((x, high_y, x, low_y), fill=color, width=1)
        body_top, body_bottom = sorted((open_y, close_y))
        if body_bottom - body_top < 1.0:
            draw.line((x - width / 2, body_top, x + width / 2, body_top), fill=color)
        else:
            draw.rectangle(
                (x - width / 2, body_top, x + width / 2, body_bottom),
                fill=color,
            )
    draw.text((left + 8, chart[1]), f"{high:,.2f}", fill=CHART_MUTED)
    draw.text((left + 8, chart[3] - 10), f"{low:,.2f}", fill=CHART_MUTED)
    time_format = "%m-%d %H:%M" if show_time else "%Y-%m-%d"
    start_label = candles.index.min().strftime(time_format)
    end_label = candles.index.max().strftime(time_format)
    draw.text((chart[0], chart[3] + 7), start_label, fill=CHART_MUTED)
    end_box = draw.textbbox((0, 0), end_label)
    end_width = end_box[2] - end_box[0]
    draw.text((chart[2] - end_width, chart[3] + 7), end_label, fill=CHART_MUTED)


def _draw_line_panel(
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    values: list[float],
    *,
    label: str,
    color: str,
    baseline: float | None = None,
) -> None:
    left, top, right, bottom = rect
    draw.rounded_rectangle(rect, radius=10, fill=CHART_PANEL, outline=CHART_GRID)
    draw.text((left + 12, top + 8), label, fill=CHART_INK, font=ImageFont.load_default())
    chart = (left + 68, top + 30, right - 18, bottom - 20)
    for step in range(5):
        y = chart[1] + (chart[3] - chart[1]) * step / 4
        draw.line((chart[0], y, chart[2], y), fill=CHART_GRID, width=1)
    finite = [float(value) for value in values if np.isfinite(value)]
    if not finite:
        draw.text((chart[0] + 12, chart[1] + 12), "No data", fill=CHART_MUTED)
        return
    low, high = min(finite), max(finite)
    if baseline is not None:
        low, high = min(low, baseline), max(high, baseline)
    if high == low:
        padding = max(abs(high) * 0.01, 1.0)
        low -= padding
        high += padding
    points = _scaled_points(values, chart, low=low, high=high)
    if len(points) == 1:
        x, y = points[0]
        draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=color)
    elif points:
        draw.line(points, fill=color, width=3, joint="curve")
    draw.text((left + 8, chart[1]), f"{high:,.2f}", fill=CHART_MUTED)
    draw.text((left + 8, chart[3] - 10), f"{low:,.2f}", fill=CHART_MUTED)


def _draw_volume_panel(
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    values: list[float],
) -> None:
    left, top, right, bottom = rect
    draw.rounded_rectangle(rect, radius=10, fill=CHART_PANEL, outline=CHART_GRID)
    draw.text((left + 12, top + 8), "1min volume", fill=CHART_INK)
    chart_left, chart_top, chart_right, chart_bottom = left + 68, top + 28, right - 18, bottom - 15
    maximum = max((float(value) for value in values if np.isfinite(value)), default=0.0)
    if maximum <= 0 or not values:
        return
    width = max(1, int((chart_right - chart_left) / max(len(values), 1)))
    for index, value in enumerate(values):
        x = chart_left + (chart_right - chart_left) * index / max(len(values) - 1, 1)
        height = max(0.0, float(value)) / maximum * (chart_bottom - chart_top)
        draw.rectangle((x, chart_bottom - height, x + width, chart_bottom), fill="#718096")


def _draw_trade_markers(
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    sampled: pd.DataFrame,
    fills: pd.DataFrame,
    *,
    color: str,
    upward: bool,
    label: str | None = None,
) -> None:
    if sampled.empty or fills.empty:
        return
    left, top, right, bottom = rect
    chart = (left + 68, top + 30, right - 18, bottom - 20)
    low, high = float(sampled["low"].min()), float(sampled["high"].max())
    if high == low:
        low, high = low - 1.0, high + 1.0
    index = sampled.index
    span = chart[2] - chart[0]
    for row in fills.itertuples(index=False):
        dt = pd.Timestamp(row.datetime)
        if dt < index.min() or dt > index.max():
            continue
        candle_index = _candle_position(index, dt)
        x = chart[0] + (candle_index + 0.5) / len(index) * span
        y = chart[3] - (float(row.fill_price) - low) / (high - low) * (chart[3] - chart[1])
        y = min(max(y, chart[1] + 8), chart[3] - 8)
        points = (
            [(x, y - 7), (x - 6, y + 5), (x + 6, y + 5)]
            if upward
            else [(x, y + 7), (x - 6, y - 5), (x + 6, y - 5)]
        )
        draw.polygon(points, fill=color)
        if label:
            label_y = y - 18 if upward else y + 8
            label_y = min(max(label_y, chart[1]), chart[3] - 10)
            draw.text((min(x + 8, chart[2] - 30), label_y), label, fill=color)


def _candle_position(index: pd.DatetimeIndex, timestamp: pd.Timestamp) -> int:
    """Map a fill to the first right-labeled candle ending at or after it."""
    position = int(index.searchsorted(timestamp, side="left"))
    return min(max(position, 0), len(index) - 1)


def _scaled_points(
    values: list[float],
    rect: tuple[int, int, int, int],
    *,
    low: float,
    high: float,
) -> list[tuple[float, float]]:
    left, top, right, bottom = rect
    denominator = max(len(values) - 1, 1)
    return [
        (
            left + (right - left) * index / denominator,
            bottom - (float(value) - low) / (high - low) * (bottom - top),
        )
        for index, value in enumerate(values)
        if np.isfinite(value)
    ]


__all__ = [
    "AtomicReportPublisher",
    "ReportValidationError",
    "plot_portfolio_equity",
    "plot_symbol_timeframes",
    "plot_trade_timeframes",
    "render_report_markdown",
    "write_json",
]
