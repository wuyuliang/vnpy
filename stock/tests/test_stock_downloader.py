from pathlib import Path
import sys
import types

import pandas as pd

from stock.data_code.stock_downloader import (
    STOCK_DAILY_COLUMNS,
    StockDownloader,
    is_st_stock_name,
    stock_price_limit_threshold_pct,
    normalize_tushare_daily_batch_df,
)


class _DummyRateLimiter:
    def acquire(self) -> None:
        return None


class _DummyPro:
    def daily(self, **kwargs) -> pd.DataFrame:
        ts_code = str(kwargs.get("ts_code", "600519.SH"))
        return pd.DataFrame(
            {
                "ts_code": [ts_code, ts_code],
                "trade_date": ["20240103", "20240102"],
                "open": [100.0, 98.0],
                "high": [102.0, 101.0],
                "low": [99.0, 97.0],
                "close": [101.0, 100.0],
                "pct_chg": [1.0, 2.0],
                "vol": [500000.0, 600000.0],
                "amount": [5.2e8, 6.1e8],
            }
        )

    def daily_basic(self, **kwargs) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "ts_code": ["600519.SH", "000001.SZ"],
                "trade_date": [str(kwargs.get("trade_date", "20240103"))] * 2,
                "close": [101.0, 10.2],
                "total_mv": [1_234_567.0, 456_789.0],
                "circ_mv": [1_000_000.0, 300_000.0],
            }
        )

    def stock_basic(self, **kwargs) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "ts_code": ["600519.SH", "000001.SZ", "000002.SZ", "600001.SH", "900001.SH"],
                "symbol": ["600519", "000001", "000002", "600001", "900001"],
                "name": ["贵州茅台", "平安银行", "*ST样例", "ST测试", "B股样例"],
                "exchange": ["SSE", "SZSE", "SZSE", "SSE", "SSE"],
                "market": ["主板", "主板", "主板", "主板", "B股"],
                "list_status": ["L", "L", "L", "L", "L"],
            }
        )


def test_fetch_stock_day_normalizes_and_writes_csv(tmp_path: Path) -> None:
    downloader = StockDownloader(
        rate_limiter=_DummyRateLimiter(),
        data_root=tmp_path,
        pro=_DummyPro(),
    )

    result = downloader.fetch_stock_day("600519.SH", "SSE", "2024-01-01", "2024-01-31")

    assert list(result.columns) == STOCK_DAILY_COLUMNS
    assert len(result) == 2
    assert result.iloc[0]["datetime"] == "2024-01-02 00:00:00"
    assert result.iloc[0]["symbol"] == "600519.SH"
    assert result.iloc[0]["exchange"] == "SSE"

    csv_path = tmp_path / "day" / "600519_SH.csv"
    assert csv_path.exists()
    reloaded = pd.read_csv(csv_path)
    assert list(reloaded.columns) == STOCK_DAILY_COLUMNS
    assert len(reloaded) == 2


def test_list_all_a_share_symbols_keeps_sse_and_szse_a_shares(tmp_path: Path) -> None:
    downloader = StockDownloader(
        rate_limiter=_DummyRateLimiter(),
        data_root=tmp_path,
        pro=_DummyPro(),
    )

    symbols = downloader.list_all_a_share_symbols()

    assert symbols == [
        {"ts_code": "600519.SH", "exchange": "SSE", "name": "贵州茅台"},
        {"ts_code": "000001.SZ", "exchange": "SZSE", "name": "平安银行"},
    ]


def test_is_st_stock_name_detects_common_st_prefixes() -> None:
    assert is_st_stock_name("ST测试")
    assert is_st_stock_name("*ST样例")
    assert is_st_stock_name("S*ST老三板")
    assert is_st_stock_name("＊ST全角")
    assert not is_st_stock_name("贵州茅台")


def test_stock_price_limit_threshold_uses_board_specific_limits() -> None:
    assert stock_price_limit_threshold_pct("600519.SH") == 9.8
    assert stock_price_limit_threshold_pct("002001.SZ") == 9.8
    assert stock_price_limit_threshold_pct("300750.SZ") == 19.8
    assert stock_price_limit_threshold_pct("301001.SZ") == 19.8
    assert stock_price_limit_threshold_pct("688981.SH") == 19.8


def test_normalize_tushare_daily_batch_df_maps_each_symbol_exchange() -> None:
    raw = pd.DataFrame(
        {
            "ts_code": ["600519.SH", "000001.SZ"],
            "trade_date": ["20240102", "20240102"],
            "open": [100.0, 10.0],
            "high": [101.0, 10.5],
                "low": [99.0, 9.8],
                "close": [100.5, 10.2],
                "pct_chg": [0.5, 2.0],
                "vol": [500000.0, 600000.0],
                "amount": [5.2e8, 6.1e7],
            }
    )

    result = normalize_tushare_daily_batch_df(
        raw,
        {"600519.SH": "SSE", "000001.SZ": "SZSE"},
    )

    assert list(result.columns) == STOCK_DAILY_COLUMNS
    assert set(result["symbol"]) == {"600519.SH", "000001.SZ"}
    assert set(result["exchange"]) == {"SSE", "SZSE"}


def test_write_symbol_day_files_splits_batch_by_symbol(tmp_path: Path) -> None:
    downloader = StockDownloader(
        rate_limiter=_DummyRateLimiter(),
        data_root=tmp_path,
        pro=_DummyPro(),
    )
    batch = pd.DataFrame(
        {
            "symbol": ["600519.SH", "000001.SZ"],
            "exchange": ["SSE", "SZSE"],
            "interval": ["d", "d"],
            "datetime": ["2024-01-02 00:00:00", "2024-01-02 00:00:00"],
            "open": [100.0, 10.0],
            "high": [101.0, 10.5],
            "low": [99.0, 9.8],
            "close": [100.5, 10.2],
            "pct_chg": [0.5, 2.0],
            "volume": [500000.0, 600000.0],
            "open_interest": [0.0, 0.0],
            "turnover": [5.2e8, 6.1e7],
        }
    )

    paths = downloader.write_symbol_day_files(batch)

    assert len(paths) == 2
    assert (tmp_path / "day" / "600519_SH.csv").exists()
    assert (tmp_path / "day" / "000001_SZ.csv").exists()


def test_write_symbol_day_files_can_merge_recent_rows_with_existing_history(tmp_path: Path) -> None:
    downloader = StockDownloader(
        rate_limiter=_DummyRateLimiter(),
        data_root=tmp_path,
        pro=_DummyPro(),
    )
    day_dir = tmp_path / "day"
    day_dir.mkdir(parents=True)
    existing = pd.DataFrame(
        {
            "symbol": ["600519.SH", "600519.SH"],
            "exchange": ["SSE", "SSE"],
            "interval": ["d", "d"],
            "datetime": ["2024-01-02 00:00:00", "2024-01-03 00:00:00"],
            "open": [98.0, 100.0],
            "high": [101.0, 102.0],
            "low": [97.0, 99.0],
            "close": [100.0, 101.0],
            "pct_chg": [2.0, 1.0],
            "volume": [600000.0, 500000.0],
            "open_interest": [0.0, 0.0],
            "turnover": [6.1e8, 5.2e8],
        }
    )
    existing.to_csv(day_dir / "600519_SH.csv", index=False)
    recent = existing.iloc[[1]].copy()
    recent.loc[:, "close"] = [103.0]
    recent.loc[:, "datetime"] = ["2024-01-04 00:00:00"]

    paths = downloader.write_symbol_day_files(recent, merge_existing=True)

    assert len(paths) == 1
    reloaded = pd.read_csv(day_dir / "600519_SH.csv")
    assert list(reloaded["datetime"]) == ["2024-01-02 00:00:00", "2024-01-03 00:00:00", "2024-01-04 00:00:00"]
    assert reloaded.iloc[-1]["close"] == 103.0


def test_fetch_daily_basic_returns_market_cap_fields(tmp_path: Path) -> None:
    downloader = StockDownloader(
        rate_limiter=_DummyRateLimiter(),
        data_root=tmp_path,
        pro=_DummyPro(),
    )

    result = downloader.fetch_daily_basic("2024-01-03")

    assert list(result.columns) == ["ts_code", "trade_date", "close", "total_mv", "circ_mv"]
    assert set(result["ts_code"]) == {"600519.SH", "000001.SZ"}
    assert result.iloc[0]["total_mv"] == 1_234_567.0


def test_get_pro_uses_token_argument_without_set_token(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    fake_module = types.SimpleNamespace()

    def _set_token(_token: str) -> None:
        raise AssertionError("set_token should not be called")

    def _pro_api(token: str) -> object:
        calls.append(("pro_api", token))
        return object()

    fake_module.set_token = _set_token
    fake_module.pro_api = _pro_api

    monkeypatch.setenv("TUSHARE_TOKEN", "fake-token")
    monkeypatch.setitem(sys.modules, "tushare", fake_module)

    downloader = StockDownloader()

    pro = downloader._get_pro()

    assert pro is downloader._pro
    assert calls == [("pro_api", "fake-token")]
