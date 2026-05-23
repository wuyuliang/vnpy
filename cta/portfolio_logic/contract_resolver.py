"""Resolve continuous-symbol -> active contract code for sim/live execution.

研究阶段策略下发的合约是连续 symbol（``RB0`` / ``AU0``），但实盘 / 仿真必须下单到
真实合约（``RB2501.SHFE``）。本模块给 sim/live 主循环提供：

- ``resolve_active_contract(continuous_symbol, as_of_date)`` → 当前主力合约代码
- ``is_rollover_day(symbol, date, window_days)`` → 是否处于主力换月窗口内

设计原则：
- **数据源优先级**：(1) 离线主力日历 [main_secondary_resolver](../data_code/main_secondary_resolver.py)
  → (2) vnpy ``MainEngine.get_all_contracts()`` 实时查询（fallback）。
- **rollover 窗口**：换月前后 ``window_days`` 天内 ``is_rollover_day`` 返回 True，
  调用方可据此**不发新单**或**强平后开新主力**。
- **fail-fast**：找不到主力合约时 raise，不静默 fallback 到任意合约。
- **OOT / sim / live 一致**：本模块在三层共用同一份日历。

P0-5 实施：本文件提供 stub 接口 + 主力日历优先解析；vnpy 实时查询的 fallback
留 hook，后续由 sim_runner 在装机时注入。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Mapping

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActiveContractInfo:
    """主力合约解析结果。"""
    continuous_symbol: str       # 例如 "RB0"
    active_contract: str         # 例如 "RB2501.SHFE"
    exchange: str                # 例如 "SHFE"
    expiry_yyyymm: int           # 例如 202501
    as_of_date: pd.Timestamp


def _normalize_continuous_symbol(symbol: str) -> str:
    return str(symbol).strip().upper()


def _extract_prefix(continuous_symbol: str) -> str:
    """从连续 symbol 提取字母前缀，例如 ``RB0`` → ``RB``、``AU0`` → ``AU``。"""
    s = _normalize_continuous_symbol(continuous_symbol)
    alpha = "".join(ch for ch in s if ch.isalpha())
    if not alpha:
        raise ValueError(f"continuous symbol has no alpha prefix: {symbol!r}")
    return alpha


def _parse_main_contract_from_calendar_row(
    row: Mapping[str, object],
) -> tuple[str, str, int]:
    """从主力日历一行返回 (active_contract, exchange, expiry_yyyymm)。"""
    code = str(row.get("main_contract_code", "")).strip().upper()
    if not code:
        raise ValueError("calendar row missing main_contract_code")
    # 期望 "RB2501.SHFE" 或 "RB2501"
    if "." in code:
        contract, exchange = code.split(".", 1)
    else:
        contract = code
        exchange = ""
    # 解析 yyyymm（兼容 RB2501 / RB251 / 不规则）
    alpha = "".join(ch for ch in contract if ch.isalpha())
    digits = contract[len(alpha):]
    if len(digits) >= 4:
        yyyymm = 200000 + int(digits[:4])
        # heuristic：2 位 year + 2 位 month
        yy = int(digits[:2])
        mm = int(digits[2:4])
        yyyymm = 2000 + yy
        yyyymm = yyyymm * 100 + mm
    else:
        yyyymm = 0
    return contract, exchange.upper(), int(yyyymm)


class ContractResolver:
    """主力合约解析器，给 sim/live 主循环用。"""

    def __init__(
        self,
        *,
        calendar_df: pd.DataFrame | None = None,
        vnpy_query_fn: Callable[[str], list[str]] | None = None,
    ) -> None:
        """构造时注入数据源。

        Args:
            calendar_df: 主力日历，schema = [trade_date, main_contract_code,
                (secondary_contract_code)]；空 / None 时只用 vnpy fallback。
            vnpy_query_fn: 备用解析，给定 continuous_symbol 返回候选合约代码列表
                （例如 ``["RB2412", "RB2501", "RB2505"]``）；未注入时 fallback 失败。
        """
        self._calendar = self._index_calendar(calendar_df) if calendar_df is not None else None
        self._vnpy_query_fn = vnpy_query_fn

    @staticmethod
    def _index_calendar(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        out = df.copy()
        if "trade_date" not in out.columns:
            raise KeyError("calendar_df missing trade_date column")
        out["trade_date"] = pd.to_datetime(out["trade_date"], errors="coerce")
        out = out.dropna(subset=["trade_date"]).sort_values("trade_date").reset_index(drop=True)
        return out

    def resolve_active_contract(
        self,
        continuous_symbol: str,
        as_of_date: pd.Timestamp | str | date,
    ) -> ActiveContractInfo:
        """解析给定日期的主力合约。"""
        symbol = _normalize_continuous_symbol(continuous_symbol)
        prefix = _extract_prefix(symbol)
        as_of = pd.Timestamp(as_of_date).normalize()

        # 优先离线日历
        if self._calendar is not None and not self._calendar.empty:
            sub = self._calendar.loc[
                (self._calendar["trade_date"] <= as_of)
                & self._calendar["main_contract_code"].astype(str).str.upper().str.startswith(prefix)
            ]
            if not sub.empty:
                row = sub.iloc[-1]
                contract, exch, yyyymm = _parse_main_contract_from_calendar_row(row)
                return ActiveContractInfo(
                    continuous_symbol=symbol,
                    active_contract=contract if not exch else f"{contract}.{exch}",
                    exchange=exch,
                    expiry_yyyymm=yyyymm,
                    as_of_date=as_of,
                )

        # fallback：vnpy 实时查询
        if self._vnpy_query_fn is not None:
            candidates = list(self._vnpy_query_fn(prefix) or [])
            if candidates:
                # 取最近未到期的（按 yyyymm 升序、>= as_of yyyymm 的第一个）
                as_of_yyyymm = int(as_of.year * 100 + as_of.month)
                parsed = []
                for c in candidates:
                    code = str(c).strip().upper()
                    # 只取前导字母作 prefix（不能用 isalpha 全扫，否则会把 ".SHFE" 也算进去）
                    head_alpha = ""
                    for ch in code:
                        if ch.isalpha():
                            head_alpha += ch
                        else:
                            break
                    if not head_alpha.startswith(prefix):
                        continue
                    tail = code[len(head_alpha):].split(".")[0]
                    if len(tail) >= 4:
                        yy = int(tail[:2])
                        mm = int(tail[2:4])
                        yyyymm = 2000 + yy
                        yyyymm = yyyymm * 100 + mm
                        parsed.append((yyyymm, code))
                # 同月份合约通常已接近到期、流动性差；严格大于当前 yyyymm
                future = sorted(p for p in parsed if p[0] > as_of_yyyymm)
                if future:
                    yyyymm, code = future[0]
                    if "." in code:
                        contract, exch = code.split(".", 1)
                    else:
                        contract, exch = code, ""
                    return ActiveContractInfo(
                        continuous_symbol=symbol,
                        active_contract=contract if not exch else f"{contract}.{exch}",
                        exchange=exch.upper(),
                        expiry_yyyymm=yyyymm,
                        as_of_date=as_of,
                    )

        raise LookupError(
            f"cannot resolve active contract for {continuous_symbol!r} on {as_of_date}; "
            "no calendar match and no vnpy fallback returned a usable contract"
        )

    def is_rollover_day(
        self,
        continuous_symbol: str,
        as_of_date: pd.Timestamp | str | date,
        *,
        window_days: int = 3,
    ) -> bool:
        """是否处于主力切换 ±window_days 天窗口内。"""
        symbol = _normalize_continuous_symbol(continuous_symbol)
        prefix = _extract_prefix(symbol)
        as_of = pd.Timestamp(as_of_date).normalize()

        if self._calendar is None or self._calendar.empty:
            return False

        sub = self._calendar.loc[
            self._calendar["main_contract_code"].astype(str).str.upper().str.startswith(prefix)
        ].copy()
        if sub.empty:
            return False

        # 找出主力代码切换的日期点
        sub["main_contract_code"] = sub["main_contract_code"].astype(str).str.upper()
        sub["_prev"] = sub["main_contract_code"].shift(1)
        switches = sub.loc[sub["main_contract_code"] != sub["_prev"]].copy()
        switches = switches.iloc[1:]  # 第一行不算切换
        if switches.empty:
            return False

        window = pd.Timedelta(days=int(window_days))
        in_window = (
            (switches["trade_date"] - window <= as_of)
            & (as_of <= switches["trade_date"] + window)
        )
        return bool(in_window.any())


def from_fut_mapping(
    mapping_df: pd.DataFrame,
    *,
    months_ahead: int = 2,
    vnpy_query_fn: Callable[[str], list[str]] | None = None,
) -> ContractResolver:
    """Build resolver directly from a tushare ``fut_mapping`` style DataFrame.

    Bridges to [data_code/main_secondary_resolver.py](../data_code/main_secondary_resolver.py)
    ``resolve_main_secondary_by_mapping`` — let sim_runner pass the same calendar
    used by training pipelines, ensuring OOT / sim / live see identical contract
    selection.
    """
    from cta.data_code.main_secondary_resolver import resolve_main_secondary_by_mapping

    calendar = resolve_main_secondary_by_mapping(mapping_df, months_ahead=int(months_ahead))
    return ContractResolver(calendar_df=calendar, vnpy_query_fn=vnpy_query_fn)


_DEFAULT_CALENDAR_PATHS: tuple[Path, ...] = (
    Path("cta/data/contract_calendar.csv"),
    Path("cta/data/main_secondary_calendar.csv"),
    Path("cta/data/origin/contract_calendar.csv"),
)


def load_default_resolver(
    calendar_path: Path | str | None = None,
    *,
    vnpy_query_fn: Callable[[str], list[str]] | None = None,
) -> ContractResolver:
    """从默认主力日历构造 resolver。

    解析顺序：
    1. 调用方显式 ``calendar_path`` → 读它
    2. 否则按 ``_DEFAULT_CALENDAR_PATHS`` 顺序查找第一个存在的文件
    3. 都缺失 → 返回 vnpy-only resolver（必须注入 vnpy_query_fn 才能工作）

    如要直接传 fut_mapping DataFrame 见 ``from_fut_mapping``。
    """
    df: pd.DataFrame | None = None
    if calendar_path is not None:
        p = Path(calendar_path)
        if p.exists():
            df = pd.read_csv(p, parse_dates=["trade_date"])
            logger.info("contract calendar loaded from explicit path: %s", p)
        else:
            logger.warning("contract calendar not found at explicit path: %s", p)
    else:
        for candidate in _DEFAULT_CALENDAR_PATHS:
            if candidate.exists():
                df = pd.read_csv(candidate, parse_dates=["trade_date"])
                logger.info("contract calendar auto-loaded from %s", candidate)
                break
        if df is None:
            logger.warning(
                "no contract calendar found at default paths %s; resolver requires vnpy_query_fn",
                [str(p) for p in _DEFAULT_CALENDAR_PATHS],
            )

    return ContractResolver(calendar_df=df, vnpy_query_fn=vnpy_query_fn)


__all__ = [
    "ActiveContractInfo",
    "ContractResolver",
    "from_fut_mapping",
    "load_default_resolver",
]
