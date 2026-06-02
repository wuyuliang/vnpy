"""OOT-style structured trade logger for sim/live runtime."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


OOT_TRADE_LOG_COLUMNS: tuple[str, ...] = (
    "datetime",
    "symbol",
    "exchange",
    "interval",
    "signal_type",
    "side",
    "execution_status",
    "block_reason",
    "risk_block_reason",
    "entry_fill_price",
    "entry_lots",
    "trade_filter_prob",
    "trade_filter_prob_pctl",
    "final_decision_score",
)


@dataclass(frozen=True)
class FlushResult:
    csv_path: Path
    jsonl_path: Path


class OotStyleTradeLogger:
    """Append-only decision/fill logger with OOT-compatible columns."""

    def __init__(self, *, out_dir: Path | str, run_tag: str = "live") -> None:
        self.out_dir = Path(out_dir)
        self.run_tag = str(run_tag).strip() or "live"
        self._rows: list[dict[str, Any]] = []
        self._events: list[dict[str, Any]] = []

    def _base_row(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = {k: payload.get(k) for k in OOT_TRADE_LOG_COLUMNS}
        row["datetime"] = str(payload.get("datetime", ""))
        row["symbol"] = str(payload.get("symbol", "")).upper()
        row["side"] = str(payload.get("side", "")).lower()
        return row

    def record_decision(
        self,
        payload: dict[str, Any],
        *,
        passed: bool,
        block_reason: str = "",
        risk_block_reason: str = "",
        adjusted_lots: int = 0,
    ) -> None:
        row = self._base_row(payload)
        row["execution_status"] = "candidate" if passed else "blocked"
        row["block_reason"] = "" if passed else str(block_reason)
        row["risk_block_reason"] = "" if passed else str(risk_block_reason or block_reason)
        row["entry_lots"] = int(adjusted_lots if passed else 0)
        self._rows.append(row)
        self._events.append(
            {
                "event_type": "decision",
                "event_time": datetime.now().isoformat(timespec="seconds"),
                "passed": bool(passed),
                "block_reason": row["block_reason"],
                "risk_block_reason": row["risk_block_reason"],
                "payload": dict(payload),
            }
        )

    def record_fill(self, payload: dict[str, Any]) -> None:
        row = self._base_row(payload)
        row["execution_status"] = "filled"
        if row.get("entry_lots") in (None, ""):
            row["entry_lots"] = int(payload.get("entry_lots", 0) or 0)
        self._rows.append(row)
        self._events.append(
            {
                "event_type": "fill",
                "event_time": datetime.now().isoformat(timespec="seconds"),
                "payload": dict(payload),
            }
        )

    def to_dataframe(self) -> pd.DataFrame:
        if not self._rows:
            return pd.DataFrame(columns=list(OOT_TRADE_LOG_COLUMNS))
        df = pd.DataFrame(self._rows)
        for col in OOT_TRADE_LOG_COLUMNS:
            if col not in df.columns:
                df[col] = pd.NA
        return df.loc[:, list(OOT_TRADE_LOG_COLUMNS)]

    def flush(self) -> tuple[Path, Path]:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = self.out_dir / f"{ts}_{self.run_tag}_live_trades.csv"
        jsonl_path = self.out_dir / f"{ts}_{self.run_tag}_decision_events.jsonl"
        df = self.to_dataframe()
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        with jsonl_path.open("w", encoding="utf-8") as f:
            for event in self._events:
                f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        return csv_path, jsonl_path


__all__ = ["FlushResult", "OOT_TRADE_LOG_COLUMNS", "OotStyleTradeLogger"]
