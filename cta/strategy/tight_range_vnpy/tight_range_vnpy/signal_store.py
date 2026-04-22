from __future__ import annotations

from pathlib import Path
from typing import Optional

from .db import SQLiteStore


class SignalStore:
    def __init__(self, db_path: Path | str) -> None:
        self.store = SQLiteStore(db_path)

    def save_signal(
        self,
        symbol: str,
        exchange: str,
        trade_date: str,
        strategy_name: str,
        action: str,
        score: float,
        level: str,
        candidate_flag: int,
        close_price: float,
        atr: float,
        comment: str = "",
    ) -> None:
        row = {
            "symbol": symbol.upper(),
            "exchange": exchange.upper(),
            "trade_date": trade_date,
            "strategy_name": strategy_name,
            "action": action,
            "score": float(score),
            "level": level,
            "candidate_flag": int(candidate_flag),
            "close_price": float(close_price),
            "atr": float(atr),
            "comment": comment,
        }
        self.store.insert_signal(row)
