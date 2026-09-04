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
REFERENCE_EXCHANGES: tuple[str, ...] = (
    "SHFE",
    "DCE",
    "CZCE",
    "INE",
    "GFEX",
    "CFFEX",
)
_CANONICAL_EXCHANGE = {
    "CFX": "CFFEX",
    "CZC": "CZCE",
    "GFE": "GFEX",
    "SHF": "SHFE",
    "ZCE": "CZCE",
}


def _format_reference_dates(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series([None] * len(frame), index=frame.index, dtype=object)
    parsed = pd.to_datetime(frame[column], errors="coerce")
    return parsed.dt.strftime("%Y-%m-%d").where(parsed.notna(), None)


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
        successful_response = False
        for suf in variants:
            ts_code = f"{prefix}.{suf}"
            try:
                self._limiter.acquire()
                df = _safe_retry(
                    self._get_pro().fut_mapping,
                    ts_code=ts_code,
                    retries=3, wait=2.0,
                )
                successful_response = True
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

        if last_err and not successful_response:
            raise RuntimeError(
                f"fut_mapping failed for {prefix} variants={variants}: {last_err}"
            ) from last_err
        return pd.DataFrame(columns=["trade_date", "mapping_ts_code"]), ""

    def fetch_contract_reference(
        self,
        exchanges: Iterable[str] = REFERENCE_EXCHANGES,
    ) -> pd.DataFrame:
        """Fetch listed-contract identities without supplying trading mechanics."""
        fields = "ts_code,symbol,exchange,fut_code,list_date,delist_date"
        parts: list[pd.DataFrame] = []
        for requested in exchanges:
            canonical = _CANONICAL_EXCHANGE.get(
                str(requested).strip().upper(),
                str(requested).strip().upper(),
            )
            if canonical not in REFERENCE_EXCHANGES:
                raise ValueError(f"unsupported futures exchange: {requested}")
            variants = (canonical,) + tuple(
                variant
                for variant in tushare_exchange_variants(canonical)
                if variant != canonical
            )
            raw = pd.DataFrame()
            last_error: Exception | None = None
            successful_response = False
            for variant in variants:
                try:
                    self._limiter.acquire()
                    candidate = _safe_retry(
                        self._get_pro().fut_basic,
                        exchange=variant,
                        fut_type="1",
                        fields=fields,
                        retries=3,
                        wait=2.0,
                    )
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    logger.warning("fut_basic %s failed: %s", variant, exc)
                    continue
                successful_response = True
                if candidate is not None and not candidate.empty:
                    raw = candidate.copy()
                    break
            if last_error and not successful_response:
                raise RuntimeError(
                    f"fut_basic failed for {canonical} variants={variants}: {last_error}"
                ) from last_error
            if raw.empty:
                continue
            required = {"ts_code", "symbol", "exchange", "fut_code"}
            missing = sorted(required.difference(raw.columns))
            if missing:
                raise ValueError(
                    f"fut_basic response is missing columns: {','.join(missing)}"
                )
            response_exchanges = {
                _CANONICAL_EXCHANGE.get(value, value)
                for value in raw["exchange"].astype(str).str.upper().str.strip()
            }
            if response_exchanges != {canonical}:
                observed = ",".join(sorted(response_exchanges)) or "EMPTY"
                raise ValueError(
                    f"fut_basic response exchange {observed} does not match {canonical}"
                )
            part = pd.DataFrame(
                {
                    "contract_code": raw["ts_code"].astype(str).str.upper().str.strip(),
                    "root_symbol": raw["fut_code"].astype(str).str.upper().str.strip(),
                    "exchange": canonical,
                    "list_date": _format_reference_dates(raw, "list_date"),
                    "delist_date": _format_reference_dates(raw, "delist_date"),
                }
            )
            blank_root = part["root_symbol"].isin({"", "NAN", "NONE"})
            if blank_root.any():
                part.loc[blank_root, "root_symbol"] = raw.loc[
                    blank_root, "symbol"
                ].map(alpha_prefix)
            parts.append(part)
        if not parts:
            return pd.DataFrame(
                columns=(
                    "contract_code",
                    "root_symbol",
                    "exchange",
                    "list_date",
                    "delist_date",
                )
            )
        return (
            pd.concat(parts, ignore_index=True)
            .drop_duplicates(["contract_code", "exchange"], keep="first")
            .sort_values(["root_symbol", "contract_code"], kind="stable")
            .reset_index(drop=True)
        )

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
            if out_path.exists() and not overwrite:
                try:
                    existing = pd.read_parquet(out_path)
                except Exception:
                    existing = pd.DataFrame()
                if not existing.empty:
                    existing_times = pd.to_datetime(
                        existing["datetime"], errors="raise"
                    )
                    incoming_times = pd.to_datetime(
                        df_out["datetime"], errors="raise"
                    )
                    existing_contracts = existing.get(
                        "contract_code",
                        existing.get("ts_code", pd.Series(contract_code, index=existing.index)),
                    ).astype(str)
                    incoming_contracts = df_out.get(
                        "contract_code",
                        df_out.get("ts_code", pd.Series(contract_code, index=df_out.index)),
                    ).astype(str)
                    existing_keys = set(zip(existing_times, existing_contracts, strict=True))
                    new_rows = [
                        key not in existing_keys
                        for key in zip(incoming_times, incoming_contracts, strict=True)
                    ]
                    if not any(new_rows):
                        results[itv] = DownloadResult(
                            symbol=symbol_u,
                            exchange=exchange_u,
                            interval=itv,
                            status="skip",
                            rows=int(len(existing)),
                            date_start=str(existing_times.min())[:10],
                            date_end=str(existing_times.max())[:10],
                            detail="requested rows already cached",
                        )
                        continue
                    df_out = (
                        pd.concat([existing, df_out.loc[new_rows]], ignore_index=True)
                        .sort_values("datetime", kind="stable")
                        .reset_index(drop=True)
                    )
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
