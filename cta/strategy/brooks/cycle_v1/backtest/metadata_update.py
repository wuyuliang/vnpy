"""Merge one audited SHFE metadata extension into the canonical history."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import date, datetime
import gzip
import json
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

import pandas as pd

from cta.strategy.brooks.scalp.metadata import MetadataBundle
from cta.strategy.brooks.scalp.metadata_importer import (
    CANONICAL_FILENAMES,
    StagingSource,
    import_metadata_bundle,
    sha256_file,
)

from .execution_metadata import merge_canonical_frames


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
_COVERAGE_COLUMNS = {
    "exchange_calendar.csv": "exchange_trade_date",
    "contract_specs.csv": "last_trade_date",
    "contract_daily.csv": "exchange_trade_date",
    "fee_margin_schedule.csv": "effective_from",
}


def merge_metadata_roots(
    *,
    base_root: str | Path,
    extension_root: str | Path,
    start: date,
    end: date,
) -> dict[str, object]:
    base_path = Path(base_root)
    extension_path = Path(extension_root)
    base = MetadataBundle.load(base_path)
    extension = MetadataBundle.load(extension_path)
    merged = merge_canonical_frames(
        base.frames,
        extension.frames,
        start=start,
        end=end,
    )
    downloaded_at = datetime.now(tz=SHANGHAI_TZ)
    with tempfile.TemporaryDirectory(
        prefix="cycle_v1_metadata_merge_",
        dir=base_path.parent,
    ) as temporary_name:
        staging = Path(temporary_name)
        sources: dict[str, StagingSource] = {}
        for filename in CANONICAL_FILENAMES:
            path = staging / filename
            merged[filename].to_csv(path, index=False)
            coverage = pd.to_datetime(
                merged[filename][_COVERAGE_COLUMNS[filename]], errors="raise"
            )
            sources[filename] = StagingSource(
                path=path,
                source_url_or_file=(
                    f"{base_path.resolve()}|{extension_path.resolve()}"
                ),
                downloaded_at=downloaded_at,
                coverage_start=coverage.min().date().isoformat(),
                coverage_end=coverage.max().date().isoformat(),
                sha256=sha256_file(path),
            )
        audit_path = staging / "shfe_source_audit.jsonl.gz"
        source_count = _merge_source_audits(
            base_path / "shfe_source_audit.jsonl.gz",
            extension_path / "shfe_source_audit.jsonl.gz",
            audit_path,
        )
        manifest = import_metadata_bundle(sources, base_path)
        target_audit = base_path / audit_path.name
        audit_path.replace(target_audit)
        manifest["source_audit"] = {
            "path": target_audit.name,
            "sha256": sha256_file(target_audit),
            "source_files": source_count,
            "limit_derivation": (
                "spec_margin_minus_2pp_then_floor_both_prices_to_tick"
            ),
        }
        manifest_path = base_path / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        prior_summary = json.loads(
            (base_path / "build_summary.json").read_text(encoding="utf-8")
        )
        summary: dict[str, object] = {
            "status": "READY",
            "start": str(prior_summary["start"]),
            "end": end.isoformat(),
            "trade_dates": int(
                merged["contract_daily.csv"]["exchange_trade_date"].nunique()
            ),
            "contracts": int(
                merged["contract_specs.csv"]["contract_code"].nunique()
            ),
            "daily_rows": len(merged["contract_daily.csv"]),
            "fee_rows": len(merged["fee_margin_schedule.csv"]),
            "source_files": source_count,
            "manifest_sha256": sha256_file(manifest_path),
        }
        (base_path / "build_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-root", required=True)
    parser.add_argument("--extension-root", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = merge_metadata_roots(
            base_root=args.base_root,
            extension_root=args.extension_root,
            start=date.fromisoformat(args.start),
            end=date.fromisoformat(args.end),
        )
    except (KeyError, OSError, TypeError, ValueError) as exc:
        print(json.dumps({"status": "ERROR", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


def _merge_source_audits(left: Path, right: Path, output: Path) -> int:
    records: dict[tuple[str, str], dict[str, object]] = {}
    for path in (left, right):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                record = json.loads(line)
                key = (str(record["kind"]), str(record["source_date"]))
                existing = records.get(key)
                if (
                    existing is not None
                    and existing.get("response_sha256")
                    != record.get("response_sha256")
                ):
                    raise ValueError(f"conflicting SHFE source audit: {key}")
                records[key] = record
    with gzip.open(output, "wt", encoding="utf-8") as handle:
        for key in sorted(records):
            handle.write(
                json.dumps(records[key], ensure_ascii=True, sort_keys=True) + "\n"
            )
    return len(records)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "merge_metadata_roots"]
