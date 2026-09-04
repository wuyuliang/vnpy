from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import pandas as pd

from .industry import SHARE_SIZE_COLUMNS, normalize_share_size

LOGGER = logging.getLogger(__name__)

STANDARD_DAILY_COLUMNS = [
    "symbol",
    "datetime",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
]
INDUSTRY_RULES: tuple[tuple[str, str], ...] = (
    ("semiconductor", r"半导体|芯片|集成电路"),
    ("electronics", r"消费电子|光学光电子|元器件|电子"),
    ("computer_ai", r"人工智能|计算机|软件|云计算|大数据|信创|数字经济"),
    ("communication", r"通信|5G"),
    ("machinery", r"工业母机|工程机械|高端装备|机床|机器人|机械"),
    ("automobile", r"新能源汽车|新能源车|智能车|汽车"),
    ("new_energy", r"新能源|光伏|电池|锂电|储能|风电"),
    ("finance", r"银行|证券|保险|金融"),
    ("healthcare", r"医药|医疗|生物科技|创新药|中药"),
    ("consumer", r"食品饮料|家电|旅游|消费|白酒|酒"),
    ("materials", r"稀有金属|有色|钢铁|化工|建材"),
    ("defense", r"航空航天|军工|国防"),
    ("utilities", r"绿色电力|公用事业|电力"),
    ("energy", r"石油|油气|煤炭|传统能源|能源"),
    ("agriculture", r"农业|畜牧|养殖|粮食"),
    ("real_estate", r"房地产|地产"),
    ("construction", r"工程建设|建筑|基建"),
    ("transportation", r"交通运输|物流|航运"),
    ("media", r"传媒|游戏|影视"),
    ("environmental", r"环保"),
)
FOREIGN_MARKET_PATTERN = (
    r"QDII|港股|香港|沪港深|沪深港|粤港澳|中概|海外|全球|跨境|境外|"
    r"纳斯达克|NASDAQ|"
    r"日经|NIKKEI|道琼斯|DOWJONES|美国|日本|德国|法国|印度|越南|新加坡|"
    r"韩国|沙特|东南亚|亚太|DAX|恒生|标普|S&P"
)
DOMESTIC_A_SHARE_PATTERN = (
    r"A股|中国A50|沪深|中证|上证|深证|国证|创业板|科创板|科创|北证|"
    r"全指|巨潮|央视"
)
REQUEST_INTERVAL_SECONDS = 0.35
RATE_LIMIT_BACKOFF_SECONDS = 61.0
RATE_LIMIT_RETRIES = 3


def _query_tushare(request: Callable[[], pd.DataFrame]) -> pd.DataFrame:
    for attempt in range(RATE_LIMIT_RETRIES):
        try:
            return request()
        except Exception as exc:
            if "频率超限" not in str(exc) or attempt == RATE_LIMIT_RETRIES - 1:
                raise
            LOGGER.warning(
                "Tushare rate limit reached; retrying after %.0fs",
                RATE_LIMIT_BACKOFF_SECONDS,
            )
            time.sleep(RATE_LIMIT_BACKOFF_SECONDS)
    raise RuntimeError("unreachable Tushare retry state")


def normalize_daily_frame(
    raw: pd.DataFrame,
    *,
    symbol: str | None = None,
    amount_in_thousands: bool = False,
) -> pd.DataFrame:
    """Normalize Tushare-like or local daily data to the strategy schema."""
    frame = raw.copy()
    frame = frame.rename(
        columns={
            "ts_code": "symbol",
            "trade_date": "datetime",
            "vol": "volume",
            "amount": "turnover",
        }
    )
    if "symbol" not in frame and symbol is not None:
        frame["symbol"] = symbol
    missing = set(STANDARD_DAILY_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"daily data missing columns: {sorted(missing)}")
    frame["datetime"] = pd.to_datetime(frame["datetime"], errors="raise")
    for column in ("open", "high", "low", "close", "volume", "turnover"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if amount_in_thousands:
        frame["turnover"] *= 1000.0
    valid = (
        (frame[["open", "high", "low", "close"]] > 0).all(axis=1)
        & (frame["high"] >= frame[["open", "close"]].max(axis=1))
        & (frame["low"] <= frame[["open", "close"]].min(axis=1))
    )
    frame = frame.loc[valid].copy()
    frame = frame.drop_duplicates(["symbol", "datetime"], keep="last")
    frame = frame.sort_values(["symbol", "datetime"]).reset_index(drop=True)
    return frame


def apply_forward_adjustment(
    daily: pd.DataFrame, factors: pd.DataFrame
) -> pd.DataFrame:
    """Adjust ETF OHLC to the latest factor and inversely adjust volume."""
    if factors.empty:
        raise ValueError("ETF adjustment factors are empty")
    if daily["symbol"].nunique() > 1:
        adjusted_groups: list[pd.DataFrame] = []
        factor_symbol_column = "ts_code" if "ts_code" in factors else "symbol"
        for symbol, bars in daily.groupby("symbol", sort=True):
            symbol_factors = factors.loc[factors[factor_symbol_column] == symbol]
            if symbol_factors.empty:
                LOGGER.warning(
                    "Skipping %s because adjustment factors are missing", symbol
                )
                continue
            adjusted_groups.append(apply_forward_adjustment(bars, symbol_factors))
        if not adjusted_groups:
            raise ValueError("no ETF has complete adjustment factors")
        return pd.concat(adjusted_groups, ignore_index=True).sort_values(
            ["symbol", "datetime"]
        )
    adjusted = daily.copy().sort_values("datetime").reset_index(drop=True)
    factor_frame = factors.rename(columns={"trade_date": "datetime"}).copy()
    factor_frame["datetime"] = pd.to_datetime(factor_frame["datetime"], errors="raise")
    factor_frame["adj_factor"] = pd.to_numeric(
        factor_frame["adj_factor"], errors="coerce"
    )
    factor_frame = factor_frame[["datetime", "adj_factor"]].drop_duplicates(
        "datetime", keep="last"
    )
    adjusted = adjusted.merge(factor_frame, on="datetime", how="left")
    adjusted["adj_factor"] = adjusted["adj_factor"].ffill().bfill()
    latest_factor = adjusted["adj_factor"].dropna().iloc[-1]
    if latest_factor <= 0 or (adjusted["adj_factor"] <= 0).any():
        raise ValueError("ETF adjustment factors must be positive and cover daily data")
    ratio = adjusted["adj_factor"] / latest_factor
    for column in ("open", "high", "low", "close"):
        adjusted[column] *= ratio
    adjusted["volume"] /= ratio
    return adjusted.drop(columns="adj_factor")


def filter_etf_universe(funds: pd.DataFrame) -> pd.DataFrame:
    """Keep listed exchange-traded non-money ETFs with explicit type labels."""
    frame = funds.copy()
    if "ts_code" in frame and "symbol" not in frame:
        frame = frame.rename(columns={"ts_code": "symbol"})
    if "status" in frame:
        frame = frame.loc[
            frame["status"].isin(
                ["L", "D", "上市", "摘牌", "listed", "delisted"],
            )
        ]
    fund_type = frame["fund_type"].fillna("").astype(str)
    is_etf = fund_type.str.contains("ETF", case=False, regex=False)
    is_money = fund_type.str.contains("货币|money", case=False, regex=True)
    is_non_etf = fund_type.str.contains("LOF|封闭", case=False, regex=True)
    return frame.loc[is_etf & ~is_money & ~is_non_etf].reset_index(drop=True)


def _metadata_text(value: object) -> str:
    return "" if value is None or pd.isna(value) else str(value).strip()


def normalize_benchmark_key(benchmark: object, symbol: object | None = None) -> str:
    """Return a stable key for exact-index entry deduplication."""
    text = re.sub(r"\s+", "", _metadata_text(benchmark))
    text = text.replace("％", "%").replace("＊", "*")
    text = re.sub(r"指数收益率(?:×|x|\*)?100%$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"指数$", "", text)
    text = re.sub(r"[，,。·（）()\-_/]", "", text)
    if text:
        return text.casefold()
    return f"symbol:{_metadata_text(symbol)}"


def classify_etf_industry(name: object, benchmark: object) -> str:
    """Classify an A-share index ETF using deterministic ordered keywords."""
    text = f"{_metadata_text(name)}|{_metadata_text(benchmark)}"
    for industry, pattern in INDUSTRY_RULES:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return industry
    return "broad_or_other"


def prepare_a_share_index_metadata(funds: pd.DataFrame) -> pd.DataFrame:
    """Keep strictly identified mainland A-share index ETFs and add industry."""
    frame = filter_etf_universe(funds)
    if frame.empty:
        return frame.assign(
            asset_scope=pd.Series(dtype=str),
            industry=pd.Series(dtype=str),
        )
    benchmark = (
        frame.get("benchmark", pd.Series("", index=frame.index)).fillna("").astype(str)
    )
    name = frame.get("name", pd.Series("", index=frame.index)).fillna("").astype(str)
    text = (name + "|" + benchmark).str.replace(r"\s+", "", regex=True)
    protected = text.str.replace("恒生A股", "A股", regex=False)
    protected = protected.str.replace("标普中国A股", "A股", regex=False)
    protected = protected.str.replace("S&P中国A股", "A股", regex=False)
    foreign = protected.str.contains(FOREIGN_MARKET_PATTERN, case=False, regex=True)
    stock = frame["fund_type"].fillna("").astype(str).eq("股票型ETF")
    index_benchmark = benchmark.str.contains(
        "指数", regex=False
    ) & ~benchmark.str.contains(
        r"\+|＋",
        regex=True,
    )
    domestic = text.str.contains(DOMESTIC_A_SHARE_PATTERN, case=False, regex=True)
    frame = frame.loc[stock & index_benchmark & domestic & ~foreign].copy()
    frame["asset_scope"] = "a_share_index"
    frame["industry"] = [
        classify_etf_industry(row.get("name"), row.get("benchmark"))
        for _, row in frame.iterrows()
    ]
    frame["benchmark_key"] = [
        normalize_benchmark_key(row.get("benchmark"), row.get("symbol"))
        for _, row in frame.iterrows()
    ]
    return frame.reset_index(drop=True)


def normalize_fund_metadata(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalize Tushare fund_basic fields and retain explicitly identified ETFs."""
    frame = raw.rename(columns={"ts_code": "symbol"}).copy()
    name = frame.get("name", pd.Series("", index=frame.index)).fillna("").astype(str)
    source_type = (
        frame.get("fund_type", pd.Series("", index=frame.index)).fillna("").astype(str)
    )
    explicit_etf = name.str.contains(
        "ETF|交易型开放式", case=False, regex=True
    ) | source_type.str.contains(
        "ETF|交易型开放式",
        case=False,
        regex=True,
    )
    explicit_non_etf = name.str.contains("LOF|封闭", case=False, regex=True)
    frame = frame.loc[explicit_etf & ~explicit_non_etf].copy()
    asset_type = (
        frame.get("type", frame.get("invest_type", source_type)).fillna("").astype(str)
    )
    frame["fund_type"] = asset_type.str.replace("ETF", "", regex=False) + "ETF"
    frame["list_date"] = pd.to_datetime(frame["list_date"], errors="coerce")
    if "delist_date" in frame:
        frame["delist_date"] = pd.to_datetime(frame["delist_date"], errors="coerce")
    return filter_etf_universe(frame)


def load_daily_csv(path: Path, *, amount_in_thousands: bool = False) -> pd.DataFrame:
    """Load and normalize one combined daily CSV file."""
    return normalize_daily_frame(
        pd.read_csv(path),
        amount_in_thousands=amount_in_thousands,
    )


def load_etf_directory(path: Path) -> pd.DataFrame:
    """Load all per-symbol ETF CSV files in deterministic order."""
    frames = [
        normalize_daily_frame(pd.read_csv(file), symbol=file.stem)
        for file in sorted(path.glob("*.csv"))
    ]
    if not frames:
        raise ValueError(f"no ETF csv files found in {path}")
    return pd.concat(frames, ignore_index=True).sort_values(["symbol", "datetime"])


class TushareEtfDownloader:
    """Small optional Tushare adapter for benchmark, ETF metadata and daily bars."""

    def __init__(self, token: str | None = None) -> None:
        try:
            import tushare as ts  # type: ignore
        except ImportError as exc:
            raise RuntimeError("tushare is required when download is enabled") from exc
        api_token = token or os.environ.get("TUSHARE_TOKEN")
        if not api_token:
            raise RuntimeError("TUSHARE_TOKEN is required when download is enabled")
        self.pro = ts.pro_api(api_token)

    def fetch_metadata(self) -> pd.DataFrame:
        """Download and normalize listed and delisted exchange ETFs."""
        raw = self.pro.fund_basic(market="E")
        return prepare_a_share_index_metadata(normalize_fund_metadata(raw))

    def fetch_benchmark(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        """Download benchmark index daily bars."""
        raw = self.pro.index_daily(
            ts_code=symbol,
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
        )
        return normalize_daily_frame(raw, amount_in_thousands=True)

    def fetch_share_size(
        self,
        metadata: pd.DataFrame,
        start: str,
        end: str,
    ) -> pd.DataFrame:
        """Download ETF share-size history for every metadata symbol."""
        symbols = sorted(metadata["symbol"].dropna().astype(str).unique())
        frames: list[pd.DataFrame] = []
        for index, symbol in enumerate(symbols):
            raw = _query_tushare(
                lambda symbol=symbol: self.pro.etf_share_size(
                    ts_code=symbol,
                    start_date=start.replace("-", ""),
                    end_date=end.replace("-", ""),
                    fields=(
                        "trade_date,ts_code,total_share,total_size,nav,close,exchange"
                    ),
                )
            )
            if not raw.empty:
                frames.append(raw)
            if index < len(symbols) - 1:
                time.sleep(REQUEST_INTERVAL_SECONDS)
        if not frames:
            return pd.DataFrame(columns=SHARE_SIZE_COLUMNS)
        return normalize_share_size(pd.concat(frames, ignore_index=True))

    def fetch_etfs(
        self,
        metadata: pd.DataFrame,
        start: str,
        end: str,
        *,
        trade_dates: Sequence[object] | None = None,
    ) -> pd.DataFrame:
        """Download and forward-adjust daily bars for every ETF in metadata."""
        if trade_dates is not None:
            return self._fetch_etfs_by_trade_date(metadata, trade_dates)
        frames: list[pd.DataFrame] = []
        for symbol in metadata["symbol"].sort_values():
            raw = self.pro.fund_daily(
                ts_code=symbol,
                start_date=start.replace("-", ""),
                end_date=end.replace("-", ""),
            )
            if not raw.empty:
                daily = normalize_daily_frame(raw, amount_in_thousands=True)
                factors = self.pro.fund_adj(
                    ts_code=symbol,
                    start_date=start.replace("-", ""),
                    end_date=end.replace("-", ""),
                )
                frames.append(apply_forward_adjustment(daily, factors))
        if not frames:
            raise RuntimeError("Tushare returned no ETF daily data")
        return pd.concat(frames, ignore_index=True)

    def _fetch_etfs_by_trade_date(
        self,
        metadata: pd.DataFrame,
        trade_dates: Sequence[object],
    ) -> pd.DataFrame:
        symbols = set(metadata["symbol"])
        daily_frames: list[pd.DataFrame] = []
        factor_frames: list[pd.DataFrame] = []
        dates = sorted({pd.Timestamp(date) for date in trade_dates})
        for index, date in enumerate(dates, start=1):
            trade_date = date.strftime("%Y%m%d")
            raw = _query_tushare(
                lambda trade_date=trade_date: self.pro.fund_daily(trade_date=trade_date)
            )
            factors = _query_tushare(
                lambda trade_date=trade_date: self.pro.fund_adj(trade_date=trade_date)
            )
            if not raw.empty:
                daily_frames.append(raw.loc[raw["ts_code"].isin(symbols)])
            if not factors.empty:
                factor_frames.append(factors.loc[factors["ts_code"].isin(symbols)])
            if index == 1 or index % 20 == 0 or index == len(dates):
                LOGGER.info(
                    "Downloaded ETF batches for %s/%s trade dates", index, len(dates)
                )
            if index < len(dates):
                time.sleep(REQUEST_INTERVAL_SECONDS)
        if not daily_frames:
            raise RuntimeError("Tushare returned no ETF daily data")
        daily = normalize_daily_frame(
            pd.concat(daily_frames, ignore_index=True), amount_in_thousands=True
        )
        factors = (
            pd.concat(factor_frames, ignore_index=True)
            if factor_frames
            else pd.DataFrame()
        )
        return apply_forward_adjustment(daily, factors).reset_index(drop=True)
