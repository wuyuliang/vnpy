import unittest
from unittest.mock import call, patch

import pandas as pd

from stock.etf.data import (
    TushareEtfDownloader,
    apply_forward_adjustment,
    classify_etf_industry,
    filter_etf_universe,
    normalize_daily_frame,
    normalize_benchmark_key,
    normalize_fund_metadata,
    prepare_a_share_index_metadata,
)


class DataTests(unittest.TestCase):
    def test_normalize_tushare_amount_from_thousand_yuan(self) -> None:
        raw = pd.DataFrame(
            {
                "ts_code": ["510300.SH"],
                "trade_date": ["20260701"],
                "open": [4.0],
                "high": [4.2],
                "low": [3.9],
                "close": [4.1],
                "vol": [1000.0],
                "amount": [250000.0],
            }
        )

        result = normalize_daily_frame(raw, amount_in_thousands=True)

        self.assertEqual(result.loc[0, "symbol"], "510300.SH")
        self.assertEqual(result.loc[0, "turnover"], 250_000_000.0)
        self.assertEqual(result.loc[0, "datetime"], pd.Timestamp("2026-07-01"))

    def test_universe_excludes_money_and_non_etf(self) -> None:
        funds = pd.DataFrame(
            {
                "symbol": ["A.SH", "B.SH", "C.SH"],
                "fund_type": ["股票型ETF", "货币ETF", "LOF"],
                "status": ["L", "L", "L"],
            }
        )

        result = filter_etf_universe(funds)

        self.assertEqual(result["symbol"].tolist(), ["A.SH"])

    def test_universe_keeps_listed_and_delisted_funds(self) -> None:
        funds = pd.DataFrame(
            {
                "symbol": ["LISTED.SH", "DELISTED.SH", "ISSUING.SH"],
                "fund_type": ["股票型ETF"] * 3,
                "status": ["L", "D", "I"],
            }
        )

        result = filter_etf_universe(funds)

        self.assertEqual(result["symbol"].tolist(), ["LISTED.SH", "DELISTED.SH"])

    def test_tushare_metadata_uses_explicit_etf_name_and_asset_type(self) -> None:
        raw = pd.DataFrame(
            {
                "ts_code": ["511010.SH", "160000.SZ"],
                "name": ["国债ETF", "普通LOF"],
                "fund_type": ["契约型开放式", "契约型开放式"],
                "type": ["债券型", "股票型"],
                "list_date": ["20200101", "20200101"],
                "status": ["L", "L"],
            }
        )

        result = normalize_fund_metadata(raw)

        self.assertEqual(result["symbol"].tolist(), ["511010.SH"])
        self.assertEqual(result.loc[0, "fund_type"], "债券型ETF")

    def test_a_share_index_metadata_is_strict_and_keeps_domestic_exceptions(
        self,
    ) -> None:
        funds = pd.DataFrame(
            {
                "symbol": [
                    "A.SH",
                    "HS_A.SH",
                    "SP_A.SH",
                    "MSCI_A.SH",
                    "NASDAQ.SH",
                    "NIKKEI.SH",
                    "HK.SH",
                    "BOND.SH",
                    "ACTIVE.SH",
                    "UNKNOWN.SH",
                ],
                "name": [
                    "中证半导体ETF",
                    "恒生A股行业龙头ETF",
                    "标普中国A股ETF",
                    "MSCI中国A50互联互通ETF",
                    "纳斯达克100ETF(QDII)",
                    "日经225ETF(QDII)",
                    "恒生科技ETF",
                    "国债ETF",
                    "主动成长ETF",
                    "未知ETF",
                ],
                "fund_type": ["股票型ETF"] * 7
                + ["债券型ETF", "股票型ETF", "股票型ETF"],
                "benchmark": [
                    "中证全指半导体指数收益率×100%",
                    "恒生A股行业龙头指数收益率×100%",
                    "标普中国A股指数收益率×100%",
                    "MSCI中国A50互联互通指数收益率×100%",
                    "纳斯达克100指数收益率×100%",
                    "日经225指数收益率×100%",
                    "恒生科技指数收益率×100%",
                    "中证国债指数收益率×100%",
                    "沪深300指数收益率×80%+中债指数收益率×20%",
                    "",
                ],
                "status": ["L"] * 10,
            }
        )

        result = prepare_a_share_index_metadata(funds).set_index("symbol")

        self.assertEqual(
            set(result.index),
            {"A.SH", "HS_A.SH", "SP_A.SH", "MSCI_A.SH"},
        )
        self.assertTrue((result["asset_scope"] == "a_share_index").all())
        self.assertEqual(result.loc["A.SH", "industry"], "semiconductor")

    def test_industry_rules_prioritize_specific_themes(self) -> None:
        self.assertEqual(
            classify_etf_industry("芯片ETF", "中证芯片指数收益率×100%"),
            "semiconductor",
        )
        self.assertEqual(
            classify_etf_industry("集成电路ETF", "国证集成电路指数"),
            "semiconductor",
        )
        self.assertEqual(
            classify_etf_industry("沪深300ETF", "沪深300指数收益率×100%"),
            "broad_or_other",
        )
        self.assertEqual(
            classify_etf_industry("新能源车ETF", "中证新能源汽车指数"),
            "automobile",
        )

    def test_benchmark_key_normalizes_suffixes_and_falls_back_to_symbol(self) -> None:
        self.assertEqual(
            normalize_benchmark_key(" 中证电池主题 指数收益率 × 100% ", "A.SH"),
            "中证电池主题",
        )
        self.assertEqual(
            normalize_benchmark_key("中证电池主题指数", "B.SH"),
            "中证电池主题",
        )
        self.assertEqual(normalize_benchmark_key(None, "C.SH"), "symbol:C.SH")

    def test_a_share_scope_excludes_mixed_hushengang_and_greater_bay_indexes(
        self,
    ) -> None:
        funds = pd.DataFrame(
            {
                "symbol": ["MIXED.SH", "GBA.SH"],
                "name": ["中证沪深港科技ETF", "粤港澳大湾区ETF"],
                "fund_type": ["股票型ETF", "股票型ETF"],
                "benchmark": [
                    "中证沪深港科技50指数收益率×100%",
                    "中证粤港澳大湾区发展主题指数收益率×100%",
                ],
                "status": ["L", "L"],
            }
        )

        result = prepare_a_share_index_metadata(funds)

        self.assertTrue(result.empty)

    def test_forward_adjustment_scales_ohlc_to_latest_factor(self) -> None:
        daily = pd.DataFrame(
            {
                "symbol": ["A.SH", "A.SH"],
                "datetime": pd.to_datetime(["2026-01-01", "2026-01-02"]),
                "open": [10.0, 5.0],
                "high": [12.0, 6.0],
                "low": [8.0, 4.0],
                "close": [10.0, 5.0],
                "volume": [100.0, 200.0],
                "turnover": [1000.0, 1000.0],
            }
        )
        factors = pd.DataFrame(
            {
                "trade_date": ["20260101", "20260102"],
                "adj_factor": [1.0, 2.0],
            }
        )

        result = apply_forward_adjustment(daily, factors)

        self.assertEqual(result["close"].tolist(), [5.0, 5.0])
        self.assertEqual(result["volume"].tolist(), [200.0, 200.0])

    def test_downloader_batches_etfs_by_trade_date(self) -> None:
        class FakePro:
            def __init__(self) -> None:
                self.daily_calls: list[str] = []

            def fund_daily(self, *, trade_date: str) -> pd.DataFrame:
                self.daily_calls.append(trade_date)
                close = 10.0 if trade_date == "20260101" else 5.0
                return pd.DataFrame(
                    {
                        "ts_code": ["A.SH", "IGNORED.SH"],
                        "trade_date": [trade_date, trade_date],
                        "open": [close, close],
                        "high": [close, close],
                        "low": [close, close],
                        "close": [close, close],
                        "vol": [100.0, 100.0],
                        "amount": [1000.0, 1000.0],
                    }
                )

            def fund_adj(self, *, trade_date: str) -> pd.DataFrame:
                factor = 1.0 if trade_date == "20260101" else 2.0
                return pd.DataFrame(
                    {
                        "ts_code": ["A.SH"],
                        "trade_date": [trade_date],
                        "adj_factor": [factor],
                    }
                )

        downloader = object.__new__(TushareEtfDownloader)
        downloader.pro = FakePro()
        metadata = pd.DataFrame({"symbol": ["A.SH"]})

        with patch("time.sleep") as sleep:
            result = downloader.fetch_etfs(
                metadata,
                "2026-01-01",
                "2026-01-02",
                trade_dates=pd.to_datetime(["2026-01-01", "2026-01-02"]),
            )

        self.assertEqual(downloader.pro.daily_calls, ["20260101", "20260102"])
        self.assertEqual(result["symbol"].unique().tolist(), ["A.SH"])
        self.assertEqual(result["close"].tolist(), [5.0, 5.0])
        self.assertEqual(sleep.call_args_list, [call(0.35)])

    def test_downloader_fetches_listed_and_delisted_metadata(self) -> None:
        class FakePro:
            def __init__(self) -> None:
                self.calls: list[dict[str, str]] = []

            def fund_basic(self, **kwargs: str) -> pd.DataFrame:
                self.calls.append(kwargs)
                return pd.DataFrame(
                    {
                        "ts_code": ["LISTED.SH", "DELISTED.SH"],
                        "name": ["中证A股ETF", "中证A股历史ETF"],
                        "fund_type": ["契约型开放式", "契约型开放式"],
                        "type": ["股票型", "股票型"],
                        "benchmark": ["中证A股指数收益率×100%"] * 2,
                        "list_date": ["20200101", "20190101"],
                        "delist_date": [None, "20251231"],
                        "status": ["L", "D"],
                    }
                )

        downloader = object.__new__(TushareEtfDownloader)
        downloader.pro = FakePro()

        result = downloader.fetch_metadata()

        self.assertEqual(downloader.pro.calls, [{"market": "E"}])
        self.assertEqual(result["symbol"].tolist(), ["LISTED.SH", "DELISTED.SH"])
        self.assertEqual(
            result.set_index("symbol").loc["DELISTED.SH", "delist_date"],
            pd.Timestamp("2025-12-31"),
        )

    def test_downloader_retries_only_tushare_rate_limit_errors(self) -> None:
        class RateLimitedPro:
            def __init__(self) -> None:
                self.daily_calls = 0

            def fund_daily(self, *, trade_date: str) -> pd.DataFrame:
                self.daily_calls += 1
                if self.daily_calls == 1:
                    raise Exception("访问接口(fund_daily)频率超限(200次/分钟)")
                return pd.DataFrame(
                    {
                        "ts_code": ["A.SH"],
                        "trade_date": [trade_date],
                        "open": [10.0],
                        "high": [10.0],
                        "low": [10.0],
                        "close": [10.0],
                        "vol": [100.0],
                        "amount": [1000.0],
                    }
                )

            def fund_adj(self, *, trade_date: str) -> pd.DataFrame:
                return pd.DataFrame(
                    {
                        "ts_code": ["A.SH"],
                        "trade_date": [trade_date],
                        "adj_factor": [1.0],
                    }
                )

        downloader = object.__new__(TushareEtfDownloader)
        downloader.pro = RateLimitedPro()
        metadata = pd.DataFrame({"symbol": ["A.SH"]})

        with patch("time.sleep") as sleep:
            result = downloader.fetch_etfs(
                metadata,
                "2026-01-01",
                "2026-01-01",
                trade_dates=pd.to_datetime(["2026-01-01"]),
            )

        self.assertEqual(downloader.pro.daily_calls, 2)
        self.assertEqual(result["symbol"].tolist(), ["A.SH"])
        self.assertEqual(sleep.call_args_list, [call(61.0)])

    def test_downloader_fetches_share_size_by_sorted_symbol(self) -> None:
        class FakePro:
            def __init__(self) -> None:
                self.calls: list[dict[str, str]] = []

            def etf_share_size(self, **kwargs: str) -> pd.DataFrame:
                self.calls.append(kwargs)
                if kwargs["ts_code"] == "B.SH":
                    return pd.DataFrame()
                return pd.DataFrame(
                    {
                        "ts_code": ["A.SH"],
                        "trade_date": ["20260102"],
                        "total_share": [12.0],
                        "total_size": [121.2],
                        "close": [1.01],
                    }
                )

        downloader = object.__new__(TushareEtfDownloader)
        downloader.pro = FakePro()
        metadata = pd.DataFrame({"symbol": ["B.SH", "A.SH", "A.SH"]})

        with patch("time.sleep") as sleep:
            result = downloader.fetch_share_size(
                metadata,
                "2026-01-01",
                "2026-01-31",
            )

        self.assertEqual(
            downloader.pro.calls,
            [
                {
                    "ts_code": "A.SH",
                    "start_date": "20260101",
                    "end_date": "20260131",
                    "fields": (
                        "trade_date,ts_code,total_share,total_size,nav,close,exchange"
                    ),
                },
                {
                    "ts_code": "B.SH",
                    "start_date": "20260101",
                    "end_date": "20260131",
                    "fields": (
                        "trade_date,ts_code,total_share,total_size,nav,close,exchange"
                    ),
                },
            ],
        )
        self.assertEqual(result["fund_units"].tolist(), [120_000.0])
        self.assertEqual(sleep.call_args_list, [call(0.35)])

    def test_downloader_returns_normalized_empty_share_size(self) -> None:
        class FakePro:
            def etf_share_size(self, **kwargs: str) -> pd.DataFrame:
                return pd.DataFrame()

        downloader = object.__new__(TushareEtfDownloader)
        downloader.pro = FakePro()

        result = downloader.fetch_share_size(
            pd.DataFrame({"symbol": ["A.SH"]}),
            "2026-01-01",
            "2026-01-31",
        )

        self.assertTrue(result.empty)
        self.assertEqual(
            result.columns.tolist(),
            [
                "symbol",
                "datetime",
                "fund_units",
                "market_close",
                "reported_total_size",
            ],
        )


if __name__ == "__main__":
    unittest.main()
