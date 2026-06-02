"""Three-layer cfg fingerprint consistency check (OOT / sim / live)."""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

DEFAULT_KEY_FIELDS: tuple[str, ...] = (
    "use_portfolio_logic_runtime",
    "commission_pct_per_trade",
    "slippage_pct_per_trade",
    "trade_filter_gate_mode",
    "trade_filter_threshold",
    "trade_filter_percentile_threshold",
    "initial_capital",
    "risk_system",
    "portfolio_logic.interval_gate.fallback_when_htf_missing",
)


@dataclass(frozen=True)
class ConfigMismatch:
    field: str
    oot_value: Any
    sim_value: Any
    live_value: Any
    detail: str = ""


@dataclass
class ConsistencyReport:
    mismatches: list[ConfigMismatch]

    @property
    def passed(self) -> bool:
        return len(self.mismatches) == 0


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.error("failed to parse json: %s (%s)", path, exc)
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _get_nested(payload: dict[str, Any], dotted: str) -> Any:
    cur: Any = payload
    for part in str(dotted).split("."):
        if not isinstance(cur, dict) or part not in cur:
            return "__missing__"
        cur = cur.get(part)
    return cur


def compare_cfg_fingerprints(
    oot_fingerprint: Path,
    sim_fingerprint: Path,
    live_fingerprint: Path,
    *,
    key_fields: Iterable[str] = DEFAULT_KEY_FIELDS,
) -> ConsistencyReport:
    oot = _load_json(oot_fingerprint)
    sim = _load_json(sim_fingerprint)
    live = _load_json(live_fingerprint)
    mismatches: list[ConfigMismatch] = []
    if oot is None or sim is None or live is None:
        mismatches.append(
            ConfigMismatch(
                field="__file__",
                oot_value=str(oot_fingerprint),
                sim_value=str(sim_fingerprint),
                live_value=str(live_fingerprint),
                detail="one or more fingerprint files missing or invalid json",
            )
        )
        return ConsistencyReport(mismatches=mismatches)

    for key in key_fields:
        ov = _get_nested(oot, key)
        sv = _get_nested(sim, key)
        lv = _get_nested(live, key)
        if ov == sv == lv:
            continue
        mismatches.append(
            ConfigMismatch(
                field=str(key),
                oot_value=ov,
                sim_value=sv,
                live_value=lv,
                detail="value mismatch across layers",
            )
        )
    return ConsistencyReport(mismatches=mismatches)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Compare OOT/sim/live cfg_fingerprint consistency")
    p.add_argument("--oot-fingerprint", required=True)
    p.add_argument("--sim-fingerprint", required=True)
    p.add_argument("--live-fingerprint", required=True)
    return p


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    args = _build_parser().parse_args()
    rep = compare_cfg_fingerprints(
        Path(args.oot_fingerprint),
        Path(args.sim_fingerprint),
        Path(args.live_fingerprint),
    )
    logger.info("cfg_consistency_check: passed=%s mismatch_count=%s", rep.passed, len(rep.mismatches))
    for mismatch in rep.mismatches:
        logger.error(
            "field=%s oot=%r sim=%r live=%r detail=%s",
            mismatch.field,
            mismatch.oot_value,
            mismatch.sim_value,
            mismatch.live_value,
            mismatch.detail,
        )
    raise SystemExit(0 if rep.passed else 2)


if __name__ == "__main__":
    main()


__all__ = [
    "ConfigMismatch",
    "ConsistencyReport",
    "DEFAULT_KEY_FIELDS",
    "compare_cfg_fingerprints",
]
