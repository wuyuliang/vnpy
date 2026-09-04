"""Independent causal EMA-universe and market-cycle scanner for cycle_v1."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
import json
import math
from pathlib import Path
import re
import shlex
from typing import Any

import pandas as pd

from cta.strategy.brooks.scalp.config import load_config as load_scalp_config
from cta.strategy.brooks.scalp.metadata import (
    DEFAULT_META_ROOT,
    BlockedMetadataError,
    MetadataBundle,
)

from ..config import DEFAULT_CONFIG_PATH, load_config
from ..instruments.metadata import BlockedMetadataError as CycleBlockedMetadataError
from ..legacy_adapters.scalp import load_normalized_symbol
from .data_loader import DiscoveredSymbol, LoadedSymbol, discover_symbols
from .candidate_charts import generate_candidate_charts
from .execution_metadata import build_execution_metadata_store
from .market_data_update import (
    DEFAULT_DAY_ROOT,
    DEFAULT_RANKING_CSV,
    prepare_minute_data,
)
from .replay import (
    build_metadata_coverage_requests,
    empty_replay_artifacts,
    replay_cycle_strategy,
)
from .reporter import (
    build_funnel,
    build_group_report,
    build_official_summary,
    compute_performance_metrics,
    write_report_bundle,
)
from .scanner import empty_scan_artifacts, scan_loaded_symbols
from .timeframes import TimeframeSet
from .vendor_metadata_cache import CachingMetadataSourceClient
from .vendor_metadata_builder import (
    AuditedVendorMetadataClient,
    MetadataBuildError,
    prepare_execution_metadata,
)


DEFAULT_DATA_ROOT = Path("cta/data/origin/minute")
DEFAULT_VENDOR_CACHE_ROOT = Path("cta/data/origin/vendor_metadata_cache")


def _vendor_source_client(args: argparse.Namespace) -> Any:
    """Return a disk-cached vendor client, or None to use the builder default.

    The bundle cache is keyed by the exact run window, so a longer or shifted
    backtest rebuilds it from scratch. Caching each vendor fetch on disk makes
    that rebuild reuse everything already downloaded and only pull the new days.
    """
    root = str(getattr(args, "vendor_cache_root", "") or "")
    if not root:
        return None
    return CachingMetadataSourceClient(
        AuditedVendorMetadataClient(rate_limit=args.download_rate_limit),
        root,
    )
DEFAULT_OUTPUT_ROOT = Path("cta/strategy/brooks/report/cycle_v1")
DEFAULT_METADATA_CACHE_ROOT = Path(
    "cta/strategy/brooks/cycle_v1/meta_cache"
)
REPO_ROOT = Path(__file__).resolve().parents[5]
_REQUESTED_SYMBOL = re.compile(r"^(?P<root>[A-Z]+)(?:0)?(?:\.[A-Z]+)?$")


class _TrackDownloadOption(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        del parser, option_string
        setattr(namespace, self.dest, values)
        provided = set(getattr(namespace, "_download_options_provided", ()))
        provided.add(self.dest)
        namespace._download_options_provided = provided


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=["all"])
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--long-tf", "--large-tf", dest="long_tf", default="1hour")
    parser.add_argument("--medium-tf", default="30min")
    parser.add_argument("--short-tf", "--small-tf", dest="short_tf", default="5min")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--meta-root", default=str(DEFAULT_META_ROOT))
    parser.add_argument(
        "--metadata-cache-root",
        default=str(DEFAULT_METADATA_CACHE_ROOT),
        action=_TrackDownloadOption,
    )
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    parser.add_argument(
        "--vendor-cache-root",
        default=str(DEFAULT_VENDOR_CACHE_ROOT),
        help=(
            "Directory holding the durable per-fetch vendor metadata cache. "
            "Pass an empty string to disable caching and always hit the vendor."
        ),
    )
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--initial-equity", type=float, default=200_000.0)
    parser.add_argument("--download-minute-data", action="store_true")
    parser.add_argument("--top-n", type=int, default=0, action=_TrackDownloadOption)
    parser.add_argument("--include-ema-eligible", action="store_true")
    parser.add_argument(
        "--ranking-csv",
        default=str(DEFAULT_RANKING_CSV),
        action=_TrackDownloadOption,
    )
    parser.add_argument(
        "--day-root",
        default=str(DEFAULT_DAY_ROOT),
        action=_TrackDownloadOption,
    )
    parser.add_argument(
        "--download-rate-limit",
        type=int,
        default=450,
        action=_TrackDownloadOption,
    )
    parser.add_argument(
        "--download-audit-output",
        default="",
        action=_TrackDownloadOption,
    )
    return parser


def validate_download_arguments(
    args: argparse.Namespace,
) -> tuple[str, ...] | None:
    download_options_used = bool(
        getattr(args, "_download_options_provided", ())
    ) or any(
        (
            args.top_n != 0,
            args.include_ema_eligible,
            str(args.ranking_csv) != str(DEFAULT_RANKING_CSV),
            str(args.day_root) != str(DEFAULT_DAY_ROOT),
            args.download_rate_limit != 450,
            bool(args.download_audit_output),
        )
    )
    if not args.download_minute_data:
        if download_options_used:
            raise ValueError(
                "download selection options require --download-minute-data"
            )
        return None
    if args.top_n < 0:
        raise ValueError("top-n must be nonnegative")
    if args.download_rate_limit <= 0:
        raise ValueError("download-rate-limit must be positive")

    tokens = tuple(
        token.strip()
        for value in args.symbols
        for token in str(value).split(",")
        if token.strip()
    )
    has_all = any(token.lower() == "all" for token in tokens)
    if has_all and not (len(tokens) == 1 and tokens[0].lower() == "all"):
        raise ValueError("symbols=all cannot be combined with explicit symbols")
    explicit = () if has_all else tokens
    if not explicit and args.top_n == 0 and not args.include_ema_eligible:
        raise ValueError(
            "minute download requires an explicit symbol, positive --top-n, "
            "or --include-ema-eligible EMA source"
        )
    return explicit


def resolve_requested_symbols(
    requested: Sequence[str],
    discovered: tuple[DiscoveredSymbol, ...],
) -> tuple[DiscoveredSymbol, ...]:
    tokens = [
        token.strip()
        for value in requested
        for token in str(value).split(",")
        if token.strip()
    ]
    if not tokens:
        raise ValueError("symbols must not be empty")
    if any(token.lower() == "all" for token in tokens):
        if len(tokens) != 1:
            raise ValueError("symbols=all cannot be combined with explicit symbols")
        return discovered
    by_root = {item.root_symbol: item for item in discovered}
    selected: list[DiscoveredSymbol] = []
    seen: set[str] = set()
    for token in tokens:
        match = _REQUESTED_SYMBOL.fullmatch(token.upper())
        if match is None:
            raise ValueError(f"invalid futures symbol: {token}")
        root = match.group("root")
        item = by_root.get(root)
        if item is None:
            raise ValueError(f"requested symbol has no discovered minute data: {token}")
        if root in seen:
            raise ValueError(f"duplicate requested root symbol: {root}")
        seen.add(root)
        selected.append(item)
    return tuple(selected)


def prepare_backtest_symbols(
    args: argparse.Namespace,
    *,
    start: date,
    end: date,
) -> tuple[
    tuple[DiscoveredSymbol, ...],
    tuple[DiscoveredSymbol, ...],
    dict[str, object],
]:
    explicit = validate_download_arguments(args)
    if explicit is None:
        discovered = discover_symbols(args.data_root)
        return (
            discovered,
            resolve_requested_symbols(args.symbols, discovered),
            {"enabled": False},
        )

    download_summary = prepare_minute_data(
        explicit=explicit,
        top_n=args.top_n,
        include_ema_eligible=args.include_ema_eligible,
        ranking_csv=args.ranking_csv,
        day_root=args.day_root,
        start=start,
        end=end,
        data_root=args.data_root,
        audit_output=args.download_audit_output,
        rate_limit=args.download_rate_limit,
    )
    selected_rows = download_summary["selection"]["selected"]
    selected_roots = {str(row["root_symbol"]) for row in selected_rows}
    # --include-top-turnover 补进来的品种走的是 explicit 通道，但那是本程序
    # 自己加的，不是用户点名要的：补池里有一个解析不了就整跑失败没有道理。
    auto_added = {
        str(root).upper().split(".")[0]
        for root in getattr(args, "_turnover_topup_added", ()) or ()
    }
    rejected_explicit: list[str] = []
    rejected_auto: list[str] = []
    for root in download_summary["selection"].get("explicit", ()):
        token = str(root)
        if token in selected_roots:
            continue
        if token.upper().split(".")[0] in auto_added:
            rejected_auto.append(token)
        else:
            rejected_explicit.append(token)
    if rejected_auto:
        download_summary["turnover_topup_rejected"] = rejected_auto
    if rejected_explicit:
        raise ValueError(
            "explicit minute symbols were rejected: "
            + ",".join(rejected_explicit)
        )
    if not selected_rows:
        raise ValueError("minute download selection produced no symbols")

    discovered = discover_symbols(args.data_root)
    by_identity = {
        (item.root_symbol, item.exchange): item for item in discovered
    }
    selected: list[DiscoveredSymbol] = []
    missing: list[str] = []
    for row in selected_rows:
        identity = (str(row["root_symbol"]), str(row["exchange"]))
        item = by_identity.get(identity)
        if item is None:
            missing.append(f"{identity[0]}.{identity[1]}")
            continue
        selected.append(item)
    if missing:
        diagnosis = _missing_symbol_diagnosis(missing, download_summary)
        if not bool(getattr(args, "allow_missing_symbols", False)):
            raise ValueError(
                "missing selected minute data after download: "
                + ",".join(missing)
                + "\n"
                + diagnosis
                + "\n重跑时加 --allow-missing-symbols 可以跳过这些品种继续，"
                  "缺口会写进 metadata_gaps.csv。"
            )
        if not selected:
            raise ValueError(
                "every selected symbol is missing minute data:\n" + diagnosis
            )
        download_summary["missing_symbols"] = list(missing)
        download_summary["missing_symbol_diagnosis"] = diagnosis
    return discovered, tuple(selected), {"enabled": True, **download_summary}


def _missing_symbol_diagnosis(
    missing: Sequence[str],
    download_summary: Mapping[str, Any],
) -> str:
    """Explain why each selected symbol has no usable minute data.

    ``update_minute_data`` already records per-symbol counters and every
    download error, but the caller used to raise a bare "missing ..." that threw
    all of it away — leaving no way to tell "the vendor has no main-contract
    mapping for this root" apart from "the network dropped".
    """
    errors = download_summary.get("download_errors") or []
    lines: list[str] = []
    for token in missing:
        root = str(token).split(".")[0]
        counters = " ".join(
            f"{name}={(download_summary.get(name) or {}).get(root, 0)}"
            for name in ("requested_dates", "downloaded", "skipped", "empty")
        )
        lines.append(f"  {token}: {counters}")
        reasons: dict[str, int] = {}
        for item in errors:
            if str(item.get("root_symbol", "")) != root:
                continue
            key = f"{item.get('stage', '?')}: {item.get('reason', '')}"
            reasons[key] = reasons.get(key, 0) + 1
        if not reasons:
            lines.append(
                "    没有记录到下载错误——供应商在该区间内对这个品种就没有数据，"
                "或者主力合约映射为空。"
            )
        for key, count in sorted(reasons.items(), key=lambda kv: -kv[1])[:4]:
            lines.append(f"    ×{count} {key[:160]}")
    return "\n".join(lines)


def prepare_backtest_metadata(
    args: argparse.Namespace,
    selected: tuple[DiscoveredSymbol, ...],
    *,
    start: date,
    end: date,
    allow_runtime_defaults: bool = False,
) -> tuple[MetadataBundle, dict[str, object], dict[str, object] | None]:
    """Load the base bundle or prepare one exact run-scoped merged cache."""
    if not args.download_minute_data and not bool(
        getattr(args, "auto_metadata", False)
    ):
        return (
            MetadataBundle.load(args.meta_root),
            {"enabled": False, "metadata_root": str(args.meta_root)},
            None,
        )
    try:
        prepared = prepare_execution_metadata(
            symbols=selected,
            start=start,
            end=end,
            base_root=args.meta_root,
            cache_root=args.metadata_cache_root,
            rate_limit=args.download_rate_limit,
            allow_runtime_defaults=allow_runtime_defaults,
            source_client=_vendor_source_client(args),
        )
    except (
        BlockedMetadataError,
        MetadataBuildError,
        FileNotFoundError,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        reason_code = getattr(exc, "code", "BLOCKED_METADATA")
        audit = {
            "enabled": True,
            "status": "BLOCKED_METADATA",
            "base_root": str(args.meta_root),
            "cache_root": str(args.metadata_cache_root),
            "reason_code": reason_code,
            "reason": str(exc),
        }
        gap = {
            "root_symbol": "ALL",
            "field": "execution_metadata_preparation",
            "reason_code": reason_code,
            "reason": str(exc),
        }
        try:
            return MetadataBundle.load(args.meta_root), audit, gap
        except (BlockedMetadataError, FileNotFoundError, OSError, ValueError) as base_exc:
            # 这条路本来是"记一笔缺口然后继续"，但退回来加载基准包又炸了，
            # 于是降级被伪装成了一个看不懂的报错。基准包本身缺失时没有任何
            # 可继续的东西，唯一有用的动作是说清楚是它缺了。
            raise BlockedMetadataError(
                f"execution metadata bundle at {str(args.meta_root)!r} cannot be "
                f"loaded, so the earlier failure ({reason_code}: {exc}) cannot be "
                f"degraded: {base_exc}"
            ) from base_exc
    return (
        MetadataBundle.load(prepared.metadata_root),
        {"enabled": True, **prepared.to_audit_dict()},
        None,
    )


def build_scan_summary(
    *,
    discovered_count: int,
    requested_count: int,
    loaded_count: int,
    universe_daily: pd.DataFrame,
    cycle_snapshots: pd.DataFrame,
    metadata_gaps: list[dict[str, object]],
    performance: dict[str, object] | None = None,
    primary_curve: list[dict[str, object]] | None = None,
    execution_funnel: dict[str, int] | None = None,
    minute_data_update: dict[str, object] | None = None,
    execution_metadata_update: dict[str, object] | None = None,
) -> dict[str, object]:
    eligible_days = (
        int(universe_daily["eligible"].fillna(False).sum())
        if "eligible" in universe_daily
        else 0
    )
    funnel = {
        "discovered_symbols": discovered_count,
        "requested_symbols": requested_count,
        "loaded_symbols": loaded_count,
        "eligible_symbol_days": eligible_days,
        "cycle_snapshots": len(cycle_snapshots),
        **(execution_funnel or {}),
    }
    summary = build_official_summary(
        funnel=funnel,
        metadata_gaps=metadata_gaps,
        performance=performance,
        primary_curve=primary_curve,
    )
    summary["minute_data_update"] = minute_data_update or {"enabled": False}
    summary["execution_metadata_update"] = execution_metadata_update or {
        "enabled": False
    }
    return summary


def build_signal_series_provenance(
    loaded: tuple[LoadedSymbol, ...],
) -> dict[str, object]:
    versions: dict[str, list[str]] = {}
    adjustment_count = 0
    for item in loaded:
        required = {"adjustment_version", "adjustment_offset", "bar_end"}
        missing = sorted(required.difference(item.minute_bars.columns))
        if missing:
            raise ValueError(
                "loaded signal series is missing provenance: " + ",".join(missing)
            )
        ordered = item.minute_bars.sort_values("bar_end", kind="stable")
        version = ordered["adjustment_version"].astype(str)
        changed = version.ne(version.shift())
        roll_versions = sorted(
            set(version.loc[changed])
            - {f"{item.root_symbol}:UNADJUSTED"}
        )
        versions[item.root_symbol] = roll_versions
        adjustment_count += len(roll_versions)
    return {
        "signal_price_series": "PIT_PRE_SETTLEMENT_ADDITIVE_V1",
        "signal_adjustment_count": adjustment_count,
        "signal_adjustment_versions": versions,
    }


def build_reproduction_command(args: argparse.Namespace) -> dict[str, object]:
    argv = [
        "python3",
        "-m",
        "cta.strategy.brooks.cycle_v1.backtest.runner",
        "--symbols",
        *[str(symbol) for symbol in args.symbols],
        "--start",
        str(args.start),
        "--end",
        str(args.end),
        "--long-tf",
        str(args.long_tf),
        "--medium-tf",
        str(args.medium_tf),
        "--short-tf",
        str(args.short_tf),
        "--initial-equity",
        _format_cli_value(args.initial_equity),
    ]
    if str(args.config) != str(DEFAULT_CONFIG_PATH):
        argv.extend(["--config", str(args.config)])
    if str(args.meta_root) != str(DEFAULT_META_ROOT):
        argv.extend(["--meta-root", str(args.meta_root)])
    if str(args.metadata_cache_root) != str(DEFAULT_METADATA_CACHE_ROOT):
        argv.extend(["--metadata-cache-root", str(args.metadata_cache_root)])
    if str(args.data_root) != str(DEFAULT_DATA_ROOT):
        argv.extend(["--data-root", str(args.data_root)])
    if str(args.output_root) != str(DEFAULT_OUTPUT_ROOT):
        argv.extend(["--output-root", str(args.output_root)])
    if args.download_minute_data:
        argv.append("--download-minute-data")
    if args.top_n != 0:
        argv.extend(["--top-n", str(args.top_n)])
    if args.include_ema_eligible:
        argv.append("--include-ema-eligible")
    if str(args.ranking_csv) != str(DEFAULT_RANKING_CSV):
        argv.extend(["--ranking-csv", str(args.ranking_csv)])
    if str(args.day_root) != str(DEFAULT_DAY_ROOT):
        argv.extend(["--day-root", str(args.day_root)])
    if args.download_rate_limit != 450:
        argv.extend(["--download-rate-limit", str(args.download_rate_limit)])
    if args.download_audit_output:
        argv.extend(["--download-audit-output", str(args.download_audit_output)])
    shell_command = (
        f"cd {shlex.quote(str(REPO_ROOT))} && {shlex.join(argv)}"
    )
    return {
        "working_directory": str(REPO_ROOT),
        "argv": argv,
        "shell_command": shell_command,
    }


def run_from_args(args: argparse.Namespace) -> tuple[dict[str, object], Path]:
    start = _parse_date(args.start, "start")
    end = _parse_date(args.end, "end")
    if end < start:
        raise ValueError("end precedes start")
    if float(getattr(args, "initial_equity", 200_000.0)) <= 0:
        raise ValueError("initial-equity must be positive")
    timeframes = TimeframeSet.from_values(
        args.long_tf,
        args.medium_tf,
        args.short_tf,
    )
    run_id = args.run_id or _default_run_id(start, end, timeframes)
    if Path(run_id).name != run_id or run_id in {"", ".", ".."}:
        raise ValueError("run-id must be one plain directory name")
    config = load_config(args.config)
    warmup_start = start - timedelta(
        days=_warmup_calendar_days(config.features.percentile_lookback, timeframes)
    )
    discovered, selected, minute_data_update = prepare_backtest_symbols(
        args,
        start=start,
        end=end,
    )
    effective_warmup_starts = {
        item.root_symbol: _effective_warmup_start(item, warmup_start, start)
        for item in selected
    }
    metadata_start = min(effective_warmup_starts.values(), default=start)
    metadata, execution_metadata_update, preparation_gap = (
        prepare_backtest_metadata(
            args,
            selected,
            start=metadata_start,
            end=end,
        )
    )
    legacy_config = load_scalp_config()

    loaded: list[LoadedSymbol] = []
    gap_rows: list[dict[str, object]] = (
        [preparation_gap] if preparation_gap is not None else []
    )
    normalized_selection = () if preparation_gap is not None else selected
    for item in normalized_selection:
        effective_warmup_start = effective_warmup_starts[item.root_symbol]
        try:
            loaded.append(
                load_normalized_symbol(
                    symbol=item.vt_symbol,
                    start=effective_warmup_start,
                    end=end,
                    data_root=Path(args.data_root),
                    metadata=metadata,
                    config=legacy_config,
                )
            )
        except (BlockedMetadataError, FileNotFoundError, OSError, ValueError) as exc:
            gap_rows.append(
                {
                    "root_symbol": item.root_symbol,
                    "field": "normalized_input",
                    "reason_code": getattr(exc, "code", "BLOCKED_METADATA"),
                    "reason": str(exc),
                }
            )
    if loaded:
        artifacts = scan_loaded_symbols(
            tuple(loaded),
            config=config,
            timeframes=timeframes,
            start=start,
            end=end,
        )
    else:
        artifacts = empty_scan_artifacts()

    report_universe = artifacts.universe_daily.loc[
        artifacts.universe_daily["exchange_trade_date"].map(
            lambda value: start <= value <= end
        )
    ].reset_index(drop=True)
    coverage = pd.DataFrame(
        columns=[
            "request_index",
            "contract_code",
            "exchange_trade_date",
            "decision_asof",
            "order_event",
            "field",
            "covered",
            "reason_code",
            "detail",
        ]
    )
    replay = empty_replay_artifacts()
    if loaded and not gap_rows:
        execution_store = None
        try:
            execution_store = build_execution_metadata_store(
                metadata,
                tuple(loaded),
                cost_stress_mult=config.risk.cost_stress_mult,
            )
        except ValueError as exc:
            gap_rows.append(
                {
                    "root_symbol": "ALL",
                    "field": "execution_metadata",
                    "reason_code": "BLOCKED_METADATA",
                    "reason": str(exc),
                }
            )
        if execution_store is not None:
            coverage_requests = build_metadata_coverage_requests(
                tuple(loaded),
                start=start,
                end=end,
            )
            coverage = execution_store.coverage_report(coverage_requests)
            for row in coverage.loc[coverage["covered"].eq(0)].itertuples(index=False):
                request = coverage_requests[int(row.request_index)]
                gap_rows.append(
                    {
                        "root_symbol": request["root_symbol"],
                        "field": row.field,
                        "reason_code": row.reason_code,
                        "reason": row.detail,
                    }
                )
            if not gap_rows:
                try:
                    replay = replay_cycle_strategy(
                        tuple(loaded),
                        metadata_store=execution_store,
                        universe_daily=artifacts.universe_daily,
                        config=config,
                        timeframes=timeframes,
                        start=start,
                        end=end,
                        initial_equity=float(
                            getattr(args, "initial_equity", 200_000.0)
                        ),
                        replay_frames=artifacts.replay_frames,
                    )
                except CycleBlockedMetadataError as exc:
                    reason = str(exc)
                    gap_rows.append(
                        {
                            "root_symbol": "ALL",
                            "field": "roll_execution",
                            "reason_code": reason.split(":", maxsplit=1)[0],
                            "reason": reason,
                        }
                    )
                    replay = empty_replay_artifacts()

    performance: dict[str, object] | None = None
    primary_curve: list[dict[str, object]] | None = None
    performance_by_group = pd.DataFrame()
    if not gap_rows and not replay.daily_equity.empty:
        initial_equity = float(getattr(args, "initial_equity", 200_000.0))
        if initial_equity <= 0:
            raise ValueError("initial-equity must be positive")
        performance = compute_performance_metrics(
            replay.trades,
            replay.daily_equity,
            initial_equity=initial_equity,
        )
        primary_curve = replay.daily_equity.to_dict(orient="records")
        performance_by_group = (
            build_group_report(replay.trades)
            if not replay.trades.empty
            else pd.DataFrame(
                columns=[
                    "symbol",
                    "sector",
                    "setup",
                    "cycle",
                    "direction",
                    "trade_count",
                    "win_rate",
                    "net_pnl",
                    "profit_factor",
                ]
            )
        )
    execution_funnel = build_funnel(
        candidates=replay.candidates,
        plans=replay.plans,
        orders=replay.orders,
        fills=replay.fills,
        trades=replay.trades,
    )
    summary = build_scan_summary(
        discovered_count=len(discovered),
        requested_count=len(selected),
        loaded_count=len(loaded),
        universe_daily=report_universe,
        cycle_snapshots=artifacts.cycle_snapshots,
        metadata_gaps=gap_rows,
        performance=performance,
        primary_curve=primary_curve,
        execution_funnel=execution_funnel,
        minute_data_update=minute_data_update,
        execution_metadata_update=execution_metadata_update,
    )
    summary.update(
        {
            "schema": config.schema,
            "strategy_version": config.version,
            "config_hash": config.config_hash,
            "requested_start": start.isoformat(),
            "requested_end": end.isoformat(),
            "warmup_start": warmup_start.isoformat(),
            "effective_warmup_starts": {
                root: value.isoformat()
                for root, value in effective_warmup_starts.items()
            },
            "timeframes": timeframes.to_dict(),
            "requested_symbols": [item.vt_symbol for item in selected],
            "loaded_symbols": [item.vt_symbol for item in loaded],
            "partition_overlap_rows_removed": {
                item.root_symbol: item.overlapping_rows_removed for item in loaded
            },
            "ema_filter": "prior completed daily EMA(1) > EMA(3) > EMA(5)",
            "initial_equity": float(getattr(args, "initial_equity", 200_000.0)),
            "execution_gateway": "CYCLE_V1_BAR_BACKTEST",
            "cost_stress_multiplier": config.risk.cost_stress_mult,
            "reproduction_command": build_reproduction_command(args),
            **build_signal_series_provenance(tuple(loaded)),
        }
    )
    metadata_gaps = pd.DataFrame(
        gap_rows,
        columns=["root_symbol", "field", "reason_code", "reason"],
    )
    source_files = pd.DataFrame(
        [
            {
                "root_symbol": item.root_symbol,
                "vt_symbol": item.vt_symbol,
                "source_file": str(path),
            }
            for item in loaded
            for path in item.source_files
        ],
        columns=["root_symbol", "vt_symbol", "source_file"],
    )
    output = write_report_bundle(
        Path(args.output_root) / run_id,
        tables={
            "universe_daily.csv": report_universe,
            "cycle_snapshots.csv": artifacts.cycle_snapshots,
            "metadata_gaps.csv": metadata_gaps,
            "metadata_coverage.csv": coverage,
            "source_files.csv": source_files,
            "candidates.csv": replay.candidates,
            "plans.csv": replay.plans,
            "orders.csv": replay.orders,
            "fills.csv": replay.fills,
            "trades.csv": replay.trades,
            "daily_equity.csv": replay.daily_equity,
            "performance_by_group.csv": performance_by_group,
            "rejections.csv": replay.rejections,
        },
        summary=summary,
    )
    generate_candidate_charts(output, data_root=Path(args.data_root))
    return summary, output


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary, output = run_from_args(args)
    except BlockedMetadataError as exc:
        print(
            json.dumps(
                {"status": "BLOCKED_METADATA", "reason": str(exc)},
                ensure_ascii=False,
            )
        )
        return 2
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "INVALID_ARGUMENT", "reason": str(exc)},
                ensure_ascii=False,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": summary["requested_interval_status"],
                "output": str(output),
            },
            ensure_ascii=False,
        )
    )
    return 0


def _parse_date(value: str, label: str) -> date:
    try:
        return pd.Timestamp(value).date()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a valid date") from exc


def _warmup_calendar_days(percentile_lookback: int, timeframes: TimeframeSet) -> int:
    required_bars = percentile_lookback * 2 + 30
    trading_days = math.ceil(required_bars * timeframes.long.minutes / 240)
    return max(30, math.ceil(trading_days * 7 / 5) + 30)


def _effective_warmup_start(
    symbol: DiscoveredSymbol,
    warmup_start: date,
    requested_start: date,
) -> date:
    partition_dates: list[date] = []
    for path in symbol.source_directory.glob("*.parquet"):
        try:
            partition_dates.append(pd.Timestamp(path.stem).date())
        except ValueError:
            continue
    available_start = max(
        warmup_start,
        min(partition_dates, default=warmup_start),
    )
    return min(available_start, requested_start)


def _format_cli_value(value: object) -> str:
    if isinstance(value, float):
        return format(value, "g")
    return str(value)


def _default_run_id(start: date, end: date, timeframes: TimeframeSet) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    labels = "_".join(
        value.replace("hour", "h").replace("min", "m")
        for value in timeframes.to_dict().values()
    )
    return f"{timestamp}_{start:%Y%m%d}_{end:%Y%m%d}_{labels}"


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "build_parser",
    "build_reproduction_command",
    "build_scan_summary",
    "build_signal_series_provenance",
    "main",
    "prepare_backtest_symbols",
    "prepare_backtest_metadata",
    "resolve_requested_symbols",
    "run_from_args",
    "validate_download_arguments",
]
