"""Runtime portfolio state for allocation, audit and snapshot recovery."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


def _norm_direction(direction: object) -> str:
    return str(direction).strip().lower()


def _sym_key(symbol: object, exchange: object) -> tuple[str, str]:
    return (str(symbol).strip().upper(), str(exchange).strip().upper())


def _tuple_key_to_str(sym_key: tuple[str, str]) -> str:
    return f"{sym_key[0]}|{sym_key[1]}"


def _str_to_tuple_key(key: object) -> tuple[str, str]:
    raw = str(key)
    if "|" in raw:
        symbol, exchange = raw.split("|", 1)
        return _sym_key(symbol, exchange)
    return _sym_key(raw, "")


@dataclass
class PortfolioState:
    """Runtime state used by ranker/allocation and live snapshot recovery."""

    equity: float
    symbol_counts: dict[tuple[str, str], int] = field(default_factory=dict)
    cluster_counts: dict[str, int] = field(default_factory=dict)
    symbol_notional: dict[tuple[str, str], float] = field(default_factory=dict)
    cluster_notional: dict[str, float] = field(default_factory=dict)
    total_positions: int = 0
    total_open_notional: float = 0.0
    open_symbol_direction: set[tuple[tuple[str, str], str]] = field(default_factory=set)
    positions: dict[str, dict[str, Any]] = field(default_factory=dict)
    trade_log: list[dict[str, Any]] = field(default_factory=list)

    _tentative_symbol_counts: dict[tuple[str, str], int] = field(default_factory=dict, init=False)
    _tentative_cluster_counts: dict[str, int] = field(default_factory=dict, init=False)
    _tentative_symbol_notional: dict[tuple[str, str], float] = field(default_factory=dict, init=False)
    _tentative_cluster_notional: dict[str, float] = field(default_factory=dict, init=False)
    _tentative_total_positions: int = field(default=0, init=False)
    _tentative_total_notional: float = field(default=0.0, init=False)
    _tentative_symbol_direction: set[tuple[tuple[str, str], str]] = field(default_factory=set, init=False)
    _committed_symbol_counts: dict[tuple[str, str], int] = field(default_factory=dict, init=False)
    _committed_cluster_counts: dict[str, int] = field(default_factory=dict, init=False)
    _committed_symbol_notional: dict[tuple[str, str], float] = field(default_factory=dict, init=False)
    _committed_cluster_notional: dict[str, float] = field(default_factory=dict, init=False)
    _committed_total_positions: int = field(default=0, init=False)
    _committed_total_notional: float = field(default=0.0, init=False)
    _committed_symbol_direction: set[tuple[tuple[str, str], str]] = field(default_factory=set, init=False)

    def begin_allocation(self) -> None:
        """Start allocation transaction from committed state."""
        self._committed_symbol_counts = dict(self.symbol_counts)
        self._committed_cluster_counts = dict(self.cluster_counts)
        self._committed_symbol_notional = dict(self.symbol_notional)
        self._committed_cluster_notional = dict(self.cluster_notional)
        self._committed_total_positions = int(self.total_positions)
        self._committed_total_notional = float(self.total_open_notional)
        self._committed_symbol_direction = set(self.open_symbol_direction)
        self._tentative_symbol_counts = dict(self._committed_symbol_counts)
        self._tentative_cluster_counts = dict(self._committed_cluster_counts)
        self._tentative_symbol_notional = dict(self._committed_symbol_notional)
        self._tentative_cluster_notional = dict(self._committed_cluster_notional)
        self._tentative_total_positions = int(self._committed_total_positions)
        self._tentative_total_notional = float(self._committed_total_notional)
        self._tentative_symbol_direction = set(self._committed_symbol_direction)

    def snapshot_for_allocation(self) -> None:
        """Take a copy used for tentative allocation updates."""
        self._tentative_symbol_counts = dict(self.symbol_counts)
        self._tentative_cluster_counts = dict(self.cluster_counts)
        self._tentative_symbol_notional = dict(self.symbol_notional)
        self._tentative_cluster_notional = dict(self.cluster_notional)
        self._tentative_total_positions = int(self.total_positions)
        self._tentative_total_notional = float(self.total_open_notional)
        self._tentative_symbol_direction = set(self.open_symbol_direction)

    def commit_allocation(self) -> None:
        """Commit tentative state into primary counters."""
        self.symbol_counts = dict(self._tentative_symbol_counts)
        self.cluster_counts = dict(self._tentative_cluster_counts)
        self.symbol_notional = dict(self._tentative_symbol_notional)
        self.cluster_notional = dict(self._tentative_cluster_notional)
        self.total_positions = int(self._tentative_total_positions)
        self.total_open_notional = float(self._tentative_total_notional)
        self.open_symbol_direction = set(self._tentative_symbol_direction)
        self._committed_symbol_counts = dict(self.symbol_counts)
        self._committed_cluster_counts = dict(self.cluster_counts)
        self._committed_symbol_notional = dict(self.symbol_notional)
        self._committed_cluster_notional = dict(self.cluster_notional)
        self._committed_total_positions = int(self.total_positions)
        self._committed_total_notional = float(self.total_open_notional)
        self._committed_symbol_direction = set(self.open_symbol_direction)

    def rollback_allocation(self) -> None:
        """Discard tentative edits and reload from committed state."""
        self._tentative_symbol_counts = dict(self._committed_symbol_counts)
        self._tentative_cluster_counts = dict(self._committed_cluster_counts)
        self._tentative_symbol_notional = dict(self._committed_symbol_notional)
        self._tentative_cluster_notional = dict(self._committed_cluster_notional)
        self._tentative_total_positions = int(self._committed_total_positions)
        self._tentative_total_notional = float(self._committed_total_notional)
        self._tentative_symbol_direction = set(self._committed_symbol_direction)

    def has_open_or_picked(self, sym_key: tuple[str, str], direction: str) -> bool:
        key = (_sym_key(sym_key[0], sym_key[1]), _norm_direction(direction))
        return key in self._tentative_symbol_direction

    def tentative_total_positions(self) -> int:
        return int(self._tentative_total_positions)

    def tentative_cluster_count(self, cluster: str) -> int:
        return int(self._tentative_cluster_counts.get(str(cluster), 0))

    def tentative_symbol_count(self, sym_key: tuple[str, str]) -> int:
        return int(self._tentative_symbol_counts.get(_sym_key(sym_key[0], sym_key[1]), 0))

    def tentative_symbol_notional(self, sym_key: tuple[str, str]) -> float:
        return float(self._tentative_symbol_notional.get(_sym_key(sym_key[0], sym_key[1]), 0.0))

    def tentative_cluster_notional(self, cluster: str) -> float:
        return float(self._tentative_cluster_notional.get(str(cluster), 0.0))

    def tentative_total_notional(self) -> float:
        return float(self._tentative_total_notional)

    def tentative_apply(
        self,
        sym_key: tuple[str, str],
        cluster: str,
        notional: float,
        direction: str,
    ) -> None:
        """Apply one candidate allocation into tentative counters."""
        n = float(notional)
        if n <= 0.0:
            return
        key = _sym_key(sym_key[0], sym_key[1])
        cluster_key = str(cluster)
        dir_key = _norm_direction(direction)
        self._tentative_total_positions += 1
        self._tentative_total_notional += n
        self._tentative_symbol_counts[key] = self._tentative_symbol_counts.get(key, 0) + 1
        self._tentative_cluster_counts[cluster_key] = self._tentative_cluster_counts.get(cluster_key, 0) + 1
        self._tentative_symbol_notional[key] = self._tentative_symbol_notional.get(key, 0.0) + n
        self._tentative_cluster_notional[cluster_key] = self._tentative_cluster_notional.get(cluster_key, 0.0) + n
        self._tentative_symbol_direction.add((key, dir_key))

    def apply_exits(self, exits: Iterable[dict[str, Any]]) -> None:
        """Apply exit events onto committed counters."""
        for exit_row in exits:
            symbol = exit_row.get("symbol", "")
            exchange = exit_row.get("exchange", "")
            sym_key = _sym_key(symbol, exchange)
            cluster = str(exit_row.get("cluster", ""))
            direction = _norm_direction(exit_row.get("direction", exit_row.get("side", "")))
            notional = max(0.0, float(exit_row.get("notional", 0.0) or 0.0))
            count = max(0, int(exit_row.get("count", 1) or 1))
            if count > 0:
                self.total_positions = max(0, int(self.total_positions) - count)
                self.symbol_counts[sym_key] = max(0, int(self.symbol_counts.get(sym_key, 0) - count))
                if cluster:
                    self.cluster_counts[str(cluster)] = max(
                        0, int(self.cluster_counts.get(str(cluster), 0) - count)
                    )
            if notional > 0.0:
                self.total_open_notional = max(0.0, float(self.total_open_notional - notional))
                self.symbol_notional[sym_key] = max(0.0, float(self.symbol_notional.get(sym_key, 0.0) - notional))
                if cluster:
                    self.cluster_notional[str(cluster)] = max(
                        0.0, float(self.cluster_notional.get(str(cluster), 0.0) - notional)
                    )
            if direction:
                self.open_symbol_direction.discard((sym_key, direction))

        # Clean zeros to keep state compact/readable in snapshots.
        self.symbol_counts = {k: int(v) for k, v in self.symbol_counts.items() if int(v) > 0}
        self.cluster_counts = {k: int(v) for k, v in self.cluster_counts.items() if int(v) > 0}
        self.symbol_notional = {k: float(v) for k, v in self.symbol_notional.items() if float(v) > 0.0}
        self.cluster_notional = {k: float(v) for k, v in self.cluster_notional.items() if float(v) > 0.0}
        self.snapshot_for_allocation()

    def add_position(self, pos: dict[str, Any]) -> None:
        """Register one newly-opened position in committed state."""
        pos_id = str(pos.get("pos_id", "")).strip()
        if not pos_id:
            return
        symbol = pos.get("symbol", "")
        exchange = pos.get("exchange", "")
        sym_key = _sym_key(symbol, exchange)
        cluster = str(pos.get("cluster", ""))
        direction = _norm_direction(pos.get("direction", pos.get("side", "long")))
        notional = max(0.0, float(pos.get("notional", 0.0) or 0.0))
        self.positions[pos_id] = dict(pos)
        self.total_positions += 1
        self.total_open_notional += notional
        self.symbol_counts[sym_key] = self.symbol_counts.get(sym_key, 0) + 1
        self.symbol_notional[sym_key] = self.symbol_notional.get(sym_key, 0.0) + notional
        if cluster:
            self.cluster_counts[cluster] = self.cluster_counts.get(cluster, 0) + 1
            self.cluster_notional[cluster] = self.cluster_notional.get(cluster, 0.0) + notional
        self.open_symbol_direction.add((sym_key, direction))
        self.snapshot_for_allocation()

    def remove_position(self, pos_id: str) -> None:
        """Remove one position and apply its exit delta."""
        key = str(pos_id).strip()
        if not key or key not in self.positions:
            return
        pos = self.positions.pop(key)
        self.apply_exits(
            [
                {
                    "symbol": pos.get("symbol", ""),
                    "exchange": pos.get("exchange", ""),
                    "cluster": pos.get("cluster", ""),
                    "direction": pos.get("direction", pos.get("side", "")),
                    "notional": pos.get("notional", 0.0),
                    "count": 1,
                }
            ]
        )

    def record_trades(self, trades: Iterable[dict[str, Any]]) -> None:
        """Append trade audit rows."""
        for row in trades:
            self.trade_log.append(dict(row))

    def to_dict(self) -> dict[str, Any]:
        """Serialize state for snapshot persistence."""
        return {
            "equity": float(self.equity),
            "symbol_counts": {_tuple_key_to_str(k): int(v) for k, v in self.symbol_counts.items()},
            "cluster_counts": {str(k): int(v) for k, v in self.cluster_counts.items()},
            "symbol_notional": {_tuple_key_to_str(k): float(v) for k, v in self.symbol_notional.items()},
            "cluster_notional": {str(k): float(v) for k, v in self.cluster_notional.items()},
            "total_positions": int(self.total_positions),
            "total_open_notional": float(self.total_open_notional),
            "open_symbol_direction": [
                {"symbol": sk[0], "exchange": sk[1], "direction": direction}
                for sk, direction in sorted(self.open_symbol_direction)
            ],
            "positions": {str(k): dict(v) for k, v in self.positions.items()},
            "trade_log": [dict(x) for x in self.trade_log],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PortfolioState":
        """Deserialize from snapshot payload."""
        out = cls(equity=float(payload.get("equity", 0.0) or 0.0))
        out.symbol_counts = {
            _str_to_tuple_key(k): int(v)
            for k, v in dict(payload.get("symbol_counts", {})).items()
            if int(v) > 0
        }
        out.cluster_counts = {
            str(k): int(v)
            for k, v in dict(payload.get("cluster_counts", {})).items()
            if int(v) > 0
        }
        out.symbol_notional = {
            _str_to_tuple_key(k): float(v)
            for k, v in dict(payload.get("symbol_notional", {})).items()
            if float(v) > 0.0
        }
        out.cluster_notional = {
            str(k): float(v)
            for k, v in dict(payload.get("cluster_notional", {})).items()
            if float(v) > 0.0
        }
        out.total_positions = int(payload.get("total_positions", 0) or 0)
        out.total_open_notional = float(payload.get("total_open_notional", 0.0) or 0.0)
        out.positions = {
            str(k): dict(v) for k, v in dict(payload.get("positions", {})).items()
        }
        out.trade_log = [dict(x) for x in list(payload.get("trade_log", []))]
        out.open_symbol_direction = set()
        for row in list(payload.get("open_symbol_direction", [])):
            sym_key = _sym_key(row.get("symbol", ""), row.get("exchange", ""))
            direction = _norm_direction(row.get("direction", ""))
            if direction:
                out.open_symbol_direction.add((sym_key, direction))
        out.snapshot_for_allocation()
        return out
