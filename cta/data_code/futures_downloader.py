"""FuturesDownloader for day + minute data under ``cta/data/origin``."""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

from cta.data_code.tushare_client import (
    RateLimiter,
    _safe_retry,
    alpha_prefix,
    tushare_exchange_variants,
)

logger = logging.getLogger(__name__)


CTA_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = CTA_ROOT / "data"
DATA_ORIGIN_DIR = DATA_DIR / "origin"
DAY_DIR = DATA_ORIGIN_DIR / "day"

# 支持的分钟级频率（与目录名一一对应）
MINUTE_INTERVALS: Tuple[str, ...] = ("minute", "minute5", "minute15", "minute30", "minute60")
ALL_INTERVALS: Tuple[str, ...] = ("day",) + MINUTE_INTERVALS

# 目录名 -> pandas resample freq
RESAMPLE_FREQ: Dict[str, str] = {
    "minute": "1min",
    "minute5": "5min",
    "minute15": "15min",
    "minute30": "30min",
    "minute60": "60min",
}

# 商品期货交易所白名单
COMMODITY_EXCHANGES = {"DCE", "CZCE", "SHFE", "INE", "GFEX"}


@dataclass
class DownloadResult:
    symbol: str
    exchange: str
    interval: str
    status: str                     # success / empty / skip / error
    rows: int = 0
    date_start: str = ""
    date_end: str = ""
    detail: str = ""                # 失败或空数据原因


class FuturesDownloader:
    """离线/在线统一下载器。"""

    def __init__(
        self,
        token: Optional[str] = None,
        rate_limit: int = 450,
        workers: int = 4,
        sleep_after_call: float = 0.0,
    ):
        self.token = (token or os.getenv("TUSHARE_TOKEN", "")).strip()
        self.rate_limit = rate_limit
        self.workers = max(1, int(workers))
        self.sleep_after_call = sleep_after_call

        self._limiter = RateLimiter(rate_limit)
        self._pro = None  # lazy
        self._pro_lock = threading.Lock()

    # ---------- Tushare lazy init ----------
    def _get_pro(self):
        if self._pro is None:
            with self._pro_lock:
                if self._pro is None:
                    if not self.token:
                        raise RuntimeError(
                            "未设置 tushare token。请 export TUSHARE_TOKEN=xxx 或传参 token=..."
                        )
                    import tushare as ts  # type: ignore
                    ts.set_token(self.token)
                    self._pro = ts.pro_api()
        return self._pro

    # =========================================================
    # 日线（akshare）
    # =========================================================
    def download_day(
        self,
        symbol: str,
        exchange: str,
        out_dir: Optional[Path] = None,
        overwrite: bool = False,
    ) -> DownloadResult:
        """
        下载单品种全历史日线 -> cta/data/origin/day/{SYMBOL}.csv

        与 download_data.py 一致字段:
        symbol, exchange, interval, datetime, open, high, low, close,
        volume, open_interest, turnover
        """
        out_dir = out_dir or DAY_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{symbol}.csv"

        if out_path.exists() and not overwrite:
            try:
                df = pd.read_csv(out_path, encoding="utf-8-sig")
                return DownloadResult(
                    symbol=symbol, exchange=exchange, interval="day",
                    status="skip", rows=len(df),
                    date_start=str(df["datetime"].min()) if len(df) else "",
                    date_end=str(df["datetime"].max()) if len(df) else "",
                    detail="file exists",
                )
            except Exception:
                pass  # 读坏了就重下

        try:
            import akshare as ak  # type: ignore
        except ImportError as e:
            return DownloadResult(
                symbol=symbol, exchange=exchange, interval="day",
                status="error", detail=f"akshare 未安装: {e}",
            )

        try:
            raw = _safe_retry(
                ak.futures_main_sina,
                symbol=symbol,
                start_date="19900101",
                end_date="22220101",
                retries=3, wait=1.5,
            )
        except Exception as e:  # noqa: BLE001
            return DownloadResult(
                symbol=symbol, exchange=exchange, interval="day",
                status="error", detail=f"akshare fetch 失败: {e}",
            )

        df = _normalize_daily_df(raw, symbol, exchange)
        if df.empty:
            return DownloadResult(
                symbol=symbol, exchange=exchange, interval="day",
                status="empty", detail="akshare 空数据",
            )

        df.to_csv(out_path, index=False, encoding="utf-8-sig")
        return DownloadResult(
            symbol=symbol, exchange=exchange, interval="day",
            status="success", rows=len(df),
            date_start=str(df["datetime"].min()),
            date_end=str(df["datetime"].max()),
        )

    # =========================================================
    # Tushare 主力映射（带交易所兜底）
    # =========================================================
    def fetch_fut_mapping(
        self,
        symbol: str,
        exchange: str,
    ) -> Tuple[pd.DataFrame, str]:
        """
        获取 ts_code 的主力日期->合约映射。
        自动尝试 exchange 的多种后缀变体（CZCE/CZC/ZCE, SHFE/SHF, GFEX/GFE）。

        返回: (mapping_df[trade_date, mapping_ts_code], 实际有效的 tushare 后缀)
        """
        prefix = alpha_prefix(symbol)
        variants = tushare_exchange_variants(exchange)

        last_err: Optional[Exception] = None
        for suf in variants:
            ts_code = f"{prefix}.{suf}"
            try:
                self._limiter.acquire()
                df = _safe_retry(
                    self._get_pro().fut_mapping,
                    ts_code=ts_code,
                    retries=3, wait=2.0,
                )
                if df is None or df.empty:
                    logger.info(f"    fut_mapping {ts_code} 返回空")
                    continue
                out = _normalize_mapping_df(df)
                if out.empty:
                    continue
                logger.info(f"    fut_mapping OK: {ts_code} -> {len(out)} rows")
                return out, suf
            except Exception as e:  # noqa: BLE001
                last_err = e
                logger.warning(f"    fut_mapping {ts_code} 失败: {e}")
                continue

        if last_err:
            logger.error(f"    所有 exchange 变体均失败: {variants}, last err = {last_err}")
        return pd.DataFrame(columns=["trade_date", "mapping_ts_code"]), ""

    # =========================================================
    # 单日 1min 拉取
    # =========================================================
    def fetch_1min_day(
        self,
        contract_code: str,
        trade_date: str,
    ) -> pd.DataFrame:
        """
        拉取 contract_code 在 trade_date 当日的全部 1min K 线。
        trade_date: YYYY-MM-DD
        返回统一字段:
            datetime, open, high, low, close, volume, open_interest, turnover
        """
        start_dt = f"{trade_date} 00:00:00"
        end_dt = f"{trade_date} 23:59:59"

        self._limiter.acquire()
        try:
            df = _safe_retry(
                self._get_pro().ft_mins,
                ts_code=contract_code,
                freq="1min",
                start_date=start_dt,
                end_date=end_dt,
                retries=3, wait=2.0,
            )
        finally:
            if self.sleep_after_call:
                time.sleep(self.sleep_after_call)

        if df is None or df.empty:
            return pd.DataFrame()

        return _normalize_ftmins_df(df, contract_code)

    # =========================================================
    # 本地重采样
    # =========================================================
    @staticmethod
    def resample_minute(df_1min: pd.DataFrame, freq: str) -> pd.DataFrame:
        """
        把 1min DataFrame 聚合到 {freq}。
        期货跨夜盘（21:00-02:30 & 09:00-15:00），直接整天 resample 会产生大量空 bar；
        这里按自然日分组，日内做 resample 后拼接。

        freq 例: '5min' / '15min' / '30min' / '60min'
        聚合 bar 的 datetime 取该 bar 的起始时间（label='left', closed='left'）
        元信息列（ts_code 等）按日取 first 回填。
        """
        if df_1min is None or df_1min.empty:
            return pd.DataFrame()

        df = df_1min.copy()
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.sort_values("datetime").reset_index(drop=True)

        agg_full = {
            "open":          "first",
            "high":          "max",
            "low":           "min",
            "close":         "last",
            "volume":        "sum",
            "turnover":      "sum",
            "open_interest": "last",
        }
        agg = {k: v for k, v in agg_full.items() if k in df.columns}
        meta_cols = [c for c in df.columns
                     if c not in agg and c != "datetime"]

        df["__date__"] = df["datetime"].dt.normalize()
        parts: List[pd.DataFrame] = []
        for _, g in df.groupby("__date__", sort=True):
            idx = g.set_index("datetime")
            ohlc = idx[list(agg.keys())].resample(freq, label="left", closed="left").agg(agg)
            ohlc = ohlc.dropna(subset=["open"])  # 去掉空 bar（无成交的时段）
            if ohlc.empty:
                continue
            ohlc = ohlc.reset_index()
            # 元信息（单日同一合约，取 first）
            if meta_cols:
                meta = {c: g[c].iloc[0] for c in meta_cols}
                for c, v in meta.items():
                    ohlc[c] = v
            parts.append(ohlc)

        if not parts:
            return pd.DataFrame()
        out = pd.concat(parts, ignore_index=True).sort_values("datetime").reset_index(drop=True)
        return out

    # =========================================================
    # 单日多频率下载 + 落盘（在线/离线共用）
    # =========================================================
    def download_day_minute_all(
        self,
        symbol: str,
        exchange: str,
        contract_code: str,
        trade_date: str,
        intervals: Iterable[str],
        out_root: Optional[Path] = None,
        overwrite: bool = False,
    ) -> Dict[str, DownloadResult]:
        """
        某品种某交易日一次 1min 拉取 + 多频率落盘。
        intervals 可包含: minute/minute5/minute15/minute30/minute60

        返回 {interval: DownloadResult}
        """
        out_root = out_root or DATA_ORIGIN_DIR
        prefix = alpha_prefix(symbol)
        intervals = [i for i in intervals if i in MINUTE_INTERVALS]

        # 判断哪些需要下
        to_fetch = []
        results: Dict[str, DownloadResult] = {}
        for itv in intervals:
            out_path = out_root / itv / prefix / f"{trade_date}.parquet"
            if out_path.exists() and not overwrite:
                try:
                    size = out_path.stat().st_size
                    results[itv] = DownloadResult(
                        symbol=symbol, exchange=exchange, interval=itv,
                        status="skip", rows=0, date_start=trade_date, date_end=trade_date,
                        detail=f"exists {size}B",
                    )
                    continue
                except Exception:
                    pass
            to_fetch.append(itv)

        if not to_fetch:
            return results

        # 拉 1min
        try:
            df_1min = self.fetch_1min_day(contract_code, trade_date)
        except Exception as e:  # noqa: BLE001
            for itv in to_fetch:
                results[itv] = DownloadResult(
                    symbol=symbol, exchange=exchange, interval=itv,
                    status="error", detail=f"fetch_1min_day: {e}",
                )
            return results

        if df_1min.empty:
            for itv in to_fetch:
                results[itv] = DownloadResult(
                    symbol=symbol, exchange=exchange, interval=itv,
                    status="empty", detail="tushare ft_mins 空",
                )
            return results

        df_1min["symbol"] = symbol
        df_1min["exchange"] = exchange

        # 落 minute
        if "minute" in to_fetch:
            out_path = out_root / "minute" / prefix / f"{trade_date}.parquet"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            df_1min.to_parquet(out_path, index=False)
            results["minute"] = DownloadResult(
                symbol=symbol, exchange=exchange, interval="minute",
                status="success", rows=len(df_1min),
                date_start=trade_date, date_end=trade_date,
            )

        # 落高阶重采样
        for itv in to_fetch:
            if itv == "minute":
                continue
            freq = RESAMPLE_FREQ[itv]
            df_agg = self.resample_minute(df_1min, freq)
            out_path = out_root / itv / prefix / f"{trade_date}.parquet"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if df_agg.empty:
                results[itv] = DownloadResult(
                    symbol=symbol, exchange=exchange, interval=itv,
                    status="empty", detail="resample 空",
                )
                continue
            df_agg["symbol"] = symbol
            df_agg["exchange"] = exchange
            df_agg.to_parquet(out_path, index=False)
            results[itv] = DownloadResult(
                symbol=symbol, exchange=exchange, interval=itv,
                status="success", rows=len(df_agg),
                date_start=trade_date, date_end=trade_date,
            )

        return results


def _normalize_daily_df(raw_df: pd.DataFrame, symbol: str, exchange: str) -> pd.DataFrame:
    """akshare futures_main_sina 结果 -> 统一 CSV 格式"""
    if raw_df is None or raw_df.empty:
        return pd.DataFrame(columns=[
            "symbol", "exchange", "interval", "datetime",
            "open", "high", "low", "close",
            "volume", "open_interest", "turnover",
        ])

    col_map = {
        "日期": "datetime",
        "开盘价": "open",
        "最高价": "high",
        "最低价": "low",
        "收盘价": "close",
        "成交量": "volume",
        "持仓量": "open_interest",
        "动态结算价": "settlement",
    }
    df = raw_df.rename(columns=col_map).copy()

    keep = [c for c in ["datetime", "open", "high", "low", "close",
                        "volume", "open_interest"] if c in df.columns]
    df = df[keep].copy()

    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df = df[df["datetime"].notna()].copy()
    df["datetime"] = df["datetime"].dt.strftime("%Y-%m-%d 00:00:00")

    for c in ["open", "high", "low", "close", "volume", "open_interest"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.drop_duplicates(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)
    df.insert(0, "symbol", symbol)
    df.insert(1, "exchange", exchange)
    df.insert(2, "interval", "d")
    df["turnover"] = 0.0

    if "open_interest" not in df.columns:
        df["open_interest"] = 0.0
    if "volume" not in df.columns:
        df["volume"] = 0.0

    cols = ["symbol", "exchange", "interval", "datetime",
            "open", "high", "low", "close",
            "volume", "open_interest", "turnover"]
    for c in cols:
        if c not in df.columns:
            df[c] = None
    return df[cols]


def _normalize_mapping_df(df: pd.DataFrame) -> pd.DataFrame:
    """fut_mapping 规范化: -> [trade_date(YYYY-MM-DD), mapping_ts_code]"""
    if df is None or df.empty:
        return pd.DataFrame(columns=["trade_date", "mapping_ts_code"])

    out = df.copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"], format="%Y%m%d", errors="coerce")
    out = out.dropna(subset=["trade_date", "mapping_ts_code"])
    out["trade_date"] = out["trade_date"].dt.strftime("%Y-%m-%d")
    out = (
        out[["trade_date", "mapping_ts_code"]]
        .drop_duplicates()
        .sort_values("trade_date")
        .reset_index(drop=True)
    )
    return out


def _normalize_ftmins_df(df: pd.DataFrame, contract_code: str) -> pd.DataFrame:
    """
    ft_mins 返回 -> 统一字段:
        datetime, open, high, low, close, volume, open_interest, turnover, ts_code
    """
    rename = {
        "trade_time": "datetime",
        "vol": "volume",
        "oi": "open_interest",
        "amount": "turnover",
    }
    out = df.rename(columns=rename).copy()

    needed = ["datetime", "open", "high", "low", "close",
              "volume", "open_interest", "turnover"]
    for c in needed:
        if c not in out.columns:
            out[c] = pd.NA

    out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
    out = out.dropna(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)
    for c in ["open", "high", "low", "close", "volume", "open_interest", "turnover"]:
        out[c] = pd.to_numeric(out[c], errors="coerce")

    if "ts_code" not in out.columns:
        out["ts_code"] = contract_code

    keep = needed + ["ts_code"]
    keep = [c for c in keep if c in out.columns]
    return out[keep].copy()
