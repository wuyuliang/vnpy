from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, List

import pandas as pd


FEATURE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS daily_features (
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    atr REAL,
    range_high REAL,
    range_low REAL,
    range_width_atr REAL,
    range_width_vs_hist_mean REAL,
    inside_bar_ratio REAL,
    overlap_ratio REAL,
    avg_body_ratio REAL,
    avg_upper_wick_ratio REAL,
    avg_lower_wick_ratio REAL,
    false_break_up_count INTEGER,
    false_break_down_count INTEGER,
    pre_leg_return REAL,
    pre_leg_return_atr REAL,
    pre_leg_slope REAL,
    pre_leg_pullback_depth_atr REAL,
    retrace_ratio_of_pre_leg REAL,
    trend_align_flag INTEGER,
    hh_hl_count INTEGER,
    ema_fast_slope REAL,
    ema_slow_slope REAL,
    distance_to_ema_fast_atr REAL,
    distance_to_ema_slow_atr REAL,
    breakout_flag INTEGER,
    breakout_excess_atr REAL,
    breakout_body_ratio REAL,
    breakout_close_pos REAL,
    breakout_volume_ratio REAL,
    breakout_gap_from_prev_close_atr REAL,
    breakout_through_prior_20_high INTEGER,
    breakout_through_prior_60_high INTEGER,
    distance_to_prior_20_high_atr REAL,
    distance_to_prior_60_high_atr REAL,
    measured_move_space_atr REAL,
    stop_distance_atr REAL,
    target_distance_atr REAL,
    expected_rr_rule REAL,
    breakout_near_major_resistance INTEGER,
    candidate_flag INTEGER,
    rule_structure_score REAL,
    rule_breakout_score REAL,
    rule_context_score REAL,
    rule_risk_score REAL,
    opportunity_score REAL,
    opportunity_level TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(symbol, exchange, trade_date)
);
"""

SIGNAL_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS signal_events (
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    action TEXT NOT NULL,
    score REAL,
    level TEXT,
    candidate_flag INTEGER,
    close_price REAL,
    atr REAL,
    comment TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(symbol, exchange, trade_date, strategy_name, action)
);
"""

INDEX_DDLS = [
    "CREATE INDEX IF NOT EXISTS idx_daily_features_symbol_date ON daily_features(symbol, exchange, trade_date);",
    "CREATE INDEX IF NOT EXISTS idx_signal_events_symbol_date ON signal_events(symbol, exchange, trade_date);",
]


class SQLiteStore:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.execute(FEATURE_TABLE_DDL)
            conn.execute(SIGNAL_TABLE_DDL)
            for ddl in INDEX_DDLS:
                conn.execute(ddl)
            conn.commit()

    def upsert_dataframe(self, table_name: str, df: pd.DataFrame) -> None:
        if df.empty:
            return
        self.initialize()
        records = df.to_dict(orient="records")
        columns = list(df.columns)
        placeholders = ",".join(["?"] * len(columns))
        col_csv = ",".join(columns)
        updates = ",".join([f"{c}=excluded.{c}" for c in columns if c not in {"symbol", "exchange", "trade_date"}])
        sql = f"INSERT INTO {table_name} ({col_csv}) VALUES ({placeholders}) ON CONFLICT(symbol, exchange, trade_date) DO UPDATE SET {updates};"
        with self.connect() as conn:
            conn.executemany(sql, [[rec.get(c) for c in columns] for rec in records])
            conn.commit()

    def insert_signal(self, row: dict) -> None:
        self.initialize()
        columns = list(row.keys())
        placeholders = ",".join(["?"] * len(columns))
        col_csv = ",".join(columns)
        sql = f"INSERT OR REPLACE INTO signal_events ({col_csv}) VALUES ({placeholders})"
        with self.connect() as conn:
            conn.execute(sql, [row.get(c) for c in columns])
            conn.commit()

    def load_symbol_features(self, symbol: str, exchange: str) -> pd.DataFrame:
        self.initialize()
        sql = "SELECT * FROM daily_features WHERE symbol=? AND exchange=? ORDER BY trade_date"
        with self.connect() as conn:
            df = pd.read_sql_query(sql, conn, params=[symbol, exchange])
        return df

    def query(self, sql: str, params: Iterable | None = None) -> pd.DataFrame:
        self.initialize()
        with self.connect() as conn:
            return pd.read_sql_query(sql, conn, params=list(params or []))
