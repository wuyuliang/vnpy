"""特征适配层:统一离线(feature_loader)与在线(FeatureGenerator)访问。

核心 API:
- `FeatureAdapter(mode).get(vt_symbol, interval, ts) -> pd.Series`
- `FeatureAdapter(mode).get_range(vt_symbol, interval, start, end) -> pd.DataFrame`

离线模式(mode='offline')适合批量回测/训练;内部用 feature_loader 从 parquet 读。
在线模式(mode='online')适合实时行情;内部维护 per-(symbol,interval) 的 FeatureGenerator,
`get` 返回最近一根已收盘 bar 的特征(与离线语义一致,带 200ms 回撤防未来)。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import pandas as pd

from cta.feature.feature_loader import (
    load_symbol_feature_at,
    load_symbol_features,
)
from cta.feature.online import FeatureGenerator, get_recommended_lookback
from cta.strategy.brooks.config.symbols import vt_to_symbol_exchange

logger = logging.getLogger(__name__)

Mode = Literal["offline", "online"]


@dataclass
class _OnlineState:
    """在线模式下每个 (symbol, interval) 的状态。"""
    generator: FeatureGenerator
    last_row: pd.Series | None = None


def _safe_lookback(interval: str) -> str:
    """日线用大一点的 lookback,分钟级用 200ms(feature_loader 默认)。"""
    if interval == "day":
        return "1D"
    return "200ms"


@dataclass
class FeatureAdapter:
    mode: Mode = "offline"
    # 在线状态:key = (symbol, interval)
    _online_state: dict[tuple[str, str], _OnlineState] = field(default_factory=dict)
    # 离线缓存:key = (symbol, interval),value = 整段 DataFrame(按需)
    _offline_cache: dict[tuple[str, str], pd.DataFrame] = field(default_factory=dict)
    # 离线单点缓存:key = (symbol, interval, ts),仅对相同查询命中(可选)
    _at_cache: dict[tuple[str, str, pd.Timestamp], pd.Series] = field(default_factory=dict)

    # ---- offline ----
    def get_range(
        self,
        vt_symbol: str,
        interval: str,
        start: str | datetime | None = None,
        end: str | datetime | None = None,
    ) -> pd.DataFrame:
        """整段特征(离线使用)。

        vt_symbol 形如 'RB0.SHFE',内部会拆出 symbol='RB0'。
        """
        symbol, _ = vt_to_symbol_exchange(vt_symbol)
        sd = pd.Timestamp(start).strftime("%Y-%m-%d") if start is not None else None
        ed = pd.Timestamp(end).strftime("%Y-%m-%d") if end is not None else None
        return load_symbol_features(symbol, interval=interval,
                                    start_date=sd, end_date=ed)

    # ---- get at ts ----
    def get(
        self,
        vt_symbol: str,
        interval: str,
        ts: pd.Timestamp | datetime | str,
    ) -> pd.Series | None:
        """返回 `ts` 时刻可用的最新已收盘 bar 的特征(防未来)。"""
        if self.mode == "offline":
            return self._get_offline(vt_symbol, interval, ts)
        return self._get_online(vt_symbol, interval, ts)

    def _get_offline(
        self,
        vt_symbol: str,
        interval: str,
        ts: pd.Timestamp | datetime | str,
    ) -> pd.Series | None:
        """offline 路径:优先走整段缓存(searchsorted 找最近≤ts 的已收盘 bar)。"""
        symbol, _ = vt_to_symbol_exchange(vt_symbol)
        cache_key = (symbol, interval)
        ts_p = pd.Timestamp(ts)
        df = self._offline_cache.get(cache_key)
        if df is None:
            # 懒加载:整段特征一次性读入(HTF/MTF 数据量相对小)
            try:
                df = load_symbol_features(symbol, interval=interval)
            except Exception:  # noqa: BLE001
                df = pd.DataFrame()
            if not df.empty and "datetime" in df.columns:
                df = df.sort_values("datetime").reset_index(drop=True)
                # 统一转到 ns 精度,避免与 pd.Timestamp.value (ns) 错位
                df["_ts"] = pd.to_datetime(df["datetime"]).astype("datetime64[ns]").astype("int64")
            self._offline_cache[cache_key] = df
        if df.empty:
            # 降级:单点查询
            return load_symbol_feature_at(
                symbol, ts, interval=interval,
                lookback=_safe_lookback(interval),
            )
        t_ns = ts_p.value
        # 找最后一根 _ts <= t_ns 的 bar,避免用未来函数
        idx = int(df["_ts"].searchsorted(t_ns, side="right")) - 1
        if idx < 0:
            return None
        return df.iloc[idx]

    # ---- online ----
    def warmup_online(
        self,
        vt_symbol: str,
        interval: str,
        history: pd.DataFrame,
    ) -> None:
        """在线模式需用历史 bar 预热 FeatureGenerator。"""
        if self.mode != "online":
            raise RuntimeError("warmup_online 只能在 online 模式下调用")
        symbol, exchange = vt_to_symbol_exchange(vt_symbol)
        key = (symbol, interval)
        window = max(get_recommended_lookback(interval), len(history))
        gen = FeatureGenerator(
            interval=interval, window=window, symbol=symbol, exchange=exchange,
        )
        gen.warmup(history)
        self._online_state[key] = _OnlineState(generator=gen)
        logger.info("warmup_online: %s/%s window=%d history=%d",
                    symbol, interval, window, len(history))

    def update_online(
        self,
        vt_symbol: str,
        interval: str,
        bar: dict | pd.Series,
    ) -> pd.Series | None:
        """在线喂入一根新 bar,返回该 bar 对应的特征行。"""
        if self.mode != "online":
            raise RuntimeError("update_online 只能在 online 模式下调用")
        symbol, _ = vt_to_symbol_exchange(vt_symbol)
        key = (symbol, interval)
        state = self._online_state.get(key)
        if state is None:
            # 懒初始化:无历史预热,直接从空状态开始累积(特征前 N 根会是 NaN)
            _, exchange = vt_to_symbol_exchange(vt_symbol)
            gen = FeatureGenerator(
                interval=interval,
                window=get_recommended_lookback(interval),
                symbol=symbol, exchange=exchange,
            )
            state = _OnlineState(generator=gen)
            self._online_state[key] = state
        row = state.generator.update(bar)
        if row is not None:
            state.last_row = row
        return row

    def _get_online(
        self,
        vt_symbol: str,
        interval: str,
        ts: pd.Timestamp | datetime | str,  # noqa: ARG002 (ts 仅用于日志)
    ) -> pd.Series | None:
        symbol, _ = vt_to_symbol_exchange(vt_symbol)
        state = self._online_state.get((symbol, interval))
        if state is None:
            return None
        return state.last_row


__all__ = ["FeatureAdapter", "Mode"]
