"""FuturesDownloader for day + minute data under ``cta/data/origin``."""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

import pandas as pd

from cta.data_code.tushare_client import (
    RateLimiter,
    _safe_retry,
    alpha_prefix,
    tushare_exchange_variants,
)
from cta.data_code.futures_downloader_utils import (
    normalize_contract_filename,
    normalize_daily_df,
    normalize_ftmins_df,
    normalize_mapping_df,
    resample_minute_bars,
)

logger = logging.getLogger(__name__)


CTA_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = CTA_ROOT / "data"
DATA_ORIGIN_DIR = DATA_DIR / "origin"
DAY_DIR = DATA_ORIGIN_DIR / "day"
CONTRACT_DIR = DATA_ORIGIN_DIR / "contract"

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

        df = normalize_daily_df(raw, symbol, exchange)
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
                out = normalize_mapping_df(df)
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

        return normalize_ftmins_df(df, contract_code)

    # =========================================================
    # 本地重采样
    # =========================================================
    @staticmethod
    def resample_minute(df_1min: pd.DataFrame, freq: str) -> pd.DataFrame:
        """把 1min DataFrame 聚合到更高频率。"""
        return resample_minute_bars(df_1min, freq)

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

    # =========================================================
    # 按显式合约下载（calendar spread 基建）
    # =========================================================
    def fetch_contract_minute_range(
        self,
        *,
        contract_code: str,
        start_date: str,
        end_date: str,
        freq: str = "1min",
    ) -> pd.DataFrame:
        """Fetch explicit contract minute bars in a date range."""
        start_ts = pd.Timestamp(start_date)
        end_ts = pd.Timestamp(end_date)
        if start_ts > end_ts:
            raise ValueError(f"start_date must be <= end_date: {start_date} > {end_date}")
        self._limiter.acquire()
        try:
            raw = _safe_retry(
                self._get_pro().ft_mins,
                ts_code=str(contract_code),
                freq=str(freq),
                start_date=f"{start_ts.strftime('%Y-%m-%d')} 00:00:00",
                end_date=f"{end_ts.strftime('%Y-%m-%d')} 23:59:59",
                retries=3,
                wait=2.0,
            )
        finally:
            if self.sleep_after_call:
                time.sleep(self.sleep_after_call)
        if raw is None or raw.empty:
            return pd.DataFrame()
        return normalize_ftmins_df(raw, str(contract_code))

    def download_explicit_contract(
        self,
        *,
        symbol: str,
        exchange: str,
        contract_code: str,
        start_date: str,
        end_date: str,
        intervals: Iterable[str] = ("minute",),
        out_root: Optional[Path] = None,
        overwrite: bool = False,
    ) -> Dict[str, DownloadResult]:
        """Download explicit contract minute data and persist parquet by interval.

        Output layout:
        ``{out_root}/contract/{SYMBOL}/{interval}/{CONTRACT}.parquet``
        """
        out_root = out_root or DATA_ORIGIN_DIR
        intervals_norm = [str(itv).strip().lower() for itv in intervals]
        for itv in intervals_norm:
            if itv not in MINUTE_INTERVALS:
                raise ValueError(f"unsupported interval for explicit contract: {itv}")
        if not intervals_norm:
            return {}

        contract_name = normalize_contract_filename(contract_code)
        symbol_u = str(symbol).strip().upper()
        exchange_u = str(exchange).strip().upper()
        date_start = pd.Timestamp(start_date).strftime("%Y-%m-%d")
        date_end = pd.Timestamp(end_date).strftime("%Y-%m-%d")

        # fetch once at 1min
        df_1min = self.fetch_contract_minute_range(
            contract_code=str(contract_code).strip().upper(),
            start_date=date_start,
            end_date=date_end,
            freq="1min",
        )
        results: Dict[str, DownloadResult] = {}
        if df_1min.empty:
            for itv in intervals_norm:
                results[itv] = DownloadResult(
                    symbol=symbol_u,
                    exchange=exchange_u,
                    interval=itv,
                    status="empty",
                    detail="explicit contract minute empty",
                )
            return results

        df_1min = df_1min.copy()
        df_1min["symbol"] = symbol_u
        df_1min["exchange"] = exchange_u
        df_1min["contract_code"] = str(contract_code).strip().upper()

        for itv in intervals_norm:
            out_path = out_root / "contract" / symbol_u / itv / f"{contract_name}.parquet"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if out_path.exists() and not overwrite:
                try:
                    existing = pd.read_parquet(out_path)
                    results[itv] = DownloadResult(
                        symbol=symbol_u,
                        exchange=exchange_u,
                        interval=itv,
                        status="skip",
                        rows=int(len(existing)),
                        date_start=str(existing["datetime"].min())[:10] if len(existing) else "",
                        date_end=str(existing["datetime"].max())[:10] if len(existing) else "",
                        detail="file exists",
                    )
                    continue
                except Exception:
                    pass

            if itv == "minute":
                df_out = df_1min.copy()
            else:
                df_out = self.resample_minute(df_1min, RESAMPLE_FREQ[itv])
                if not df_out.empty:
                    df_out["symbol"] = symbol_u
                    df_out["exchange"] = exchange_u
                    df_out["contract_code"] = str(contract_code).strip().upper()
                    if "ts_code" not in df_out.columns:
                        df_out["ts_code"] = str(contract_code).strip().upper()
            if df_out.empty:
                results[itv] = DownloadResult(
                    symbol=symbol_u,
                    exchange=exchange_u,
                    interval=itv,
                    status="empty",
                    detail="resample empty",
                )
                continue
            df_out.to_parquet(out_path, index=False)
            dt_col = pd.to_datetime(df_out["datetime"], errors="coerce")
            results[itv] = DownloadResult(
                symbol=symbol_u,
                exchange=exchange_u,
                interval=itv,
                status="success",
                rows=int(len(df_out)),
                date_start=str(dt_col.min())[:10] if len(df_out) else "",
                date_end=str(dt_col.max())[:10] if len(df_out) else "",
                detail=f"path={out_path}",
            )
        return results
