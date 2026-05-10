"""在线读取 ``cta/data/feature`` 离线特征 parquet，作为 ``model_filter`` 的特征源。

为什么需要这个模块
------------------
``cta.live.model_filter.make_trade_filter`` 默认从 ``adapter._frame.iloc[-1:]`` 取特征。
但 ``_frame`` 只含 baseline 内嵌的 ~30 列（``prepare_master_feature_frame`` 算出的），
而 ``model_pipeline --generic-mode auto`` 训出的模型期待 ``cta/data/feature/`` 中
通用特征（~400 列）。直接用 ``_frame`` 会触发"missing columns"全量降级放行。

本模块从 ``cta/data/feature/{interval}/{prefix}/{YYYY-MM-DD}.parquet``
（日线则是 ``cta/data/feature/day/{symbol}.parquet``）按时间戳定位特征行，
作为 ``feature_provider`` 接入 ``make_trade_filter``：

    loader = OnlineFeatureLoader(feature_root="cta/data/feature")
    adapter.order_filter = make_trade_filter(
        model_path, feature_provider=loader,
    )

注意事项
--------
- ``cta/data/feature/`` 中的特征是**离线**预先生成的；当日特征文件可能滞后 T+1 才落盘。
  对依赖当日特征的策略，应在收盘后跑 ``cta.feature.run_all_features`` 更新。
- 真正的实时增量特征引擎需要 ``cta.feature.online`` 增量计算，是 P2 大改的范畴。
"""
from __future__ import annotations

import re
from collections import OrderedDict
from pathlib import Path
from typing import Any

import pandas as pd

_PREFIX_RE = re.compile(r"^([A-Za-z]+)")


def _alpha_prefix(symbol: str) -> str:
    m = _PREFIX_RE.match(str(symbol).strip())
    return m.group(1).upper() if m else str(symbol).strip().upper()


class OnlineFeatureLoader:
    """从离线 parquet 读特征行，LRU 缓存最近访问过的文件。"""

    def __init__(
        self,
        *,
        feature_root: str | Path = "cta/data/feature",
        cache_size: int = 8,
    ) -> None:
        self.root = Path(feature_root)
        self.cache_size = max(int(cache_size), 1)
        self._cache: OrderedDict[Path, pd.DataFrame] = OrderedDict()

    def _load_file(self, path: Path) -> pd.DataFrame | None:
        if not path.exists():
            return None
        if path in self._cache:
            self._cache.move_to_end(path)
            return self._cache[path]
        try:
            df = pd.read_parquet(path)
        except Exception:  # noqa: BLE001
            return None
        self._cache[path] = df
        self._cache.move_to_end(path)
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return df

    def _resolve_path(self, *, symbol: str, interval: str,
                     dt: pd.Timestamp) -> Path | None:
        if interval == "day":
            # day-level 是按 symbol 的单文件
            return self.root / "day" / f"{symbol}.parquet"
        prefix = _alpha_prefix(symbol)
        date_str = pd.Timestamp(dt).strftime("%Y-%m-%d")
        return self.root / interval / prefix / f"{date_str}.parquet"

    def load_at(
        self,
        *,
        symbol: str,
        interval: str,
        dt: Any,
    ) -> pd.Series | None:
        """返回 ``symbol/interval`` 在 ``dt`` 时刻（取 ≤dt 的最近一行）的特征 Series。

        没有对应 parquet / 没有 ≤dt 的行时返回 None。
        """
        path = self._resolve_path(symbol=symbol, interval=interval, dt=dt)
        if path is None:
            return None
        df = self._load_file(path)
        if df is None or df.empty:
            return None
        if "datetime" not in df.columns:
            return df.iloc[-1]
        ts = pd.to_datetime(df["datetime"])
        target = pd.Timestamp(dt)
        mask = ts <= target
        if not mask.any():
            return None
        return df.iloc[int(mask.values.nonzero()[0].max())]

    def __call__(
        self,
        adapter: Any,
        columns: list[str] | None = None,
    ) -> pd.DataFrame | None:
        """``feature_provider`` 协议：返回 1×N DataFrame 或 None。

        从 adapter 推断 ``vt_symbol`` 与最新 bar 的 ``datetime``；
        ``interval`` 优先取 ``adapter.interval`` 属性，缺失时默认 "day"。
        """
        vt = str(getattr(adapter, "vt_symbol", "") or "")
        symbol = vt.split(".")[0] if vt else ""
        interval = str(getattr(adapter, "interval", "day") or "day")
        buffer = getattr(adapter, "_buffer", None) or []
        if not symbol or not buffer:
            return None
        latest = buffer[-1]
        dt = getattr(latest, "datetime", None)
        if dt is None:
            return None
        row = self.load_at(symbol=symbol, interval=interval, dt=dt)
        if row is None:
            return None
        # 转成 1 行 DataFrame，按 columns 重排（缺列保留 NaN）
        df = pd.DataFrame([row])
        if columns is not None:
            df = df.reindex(columns=columns)
        return df


__all__ = ["OnlineFeatureLoader"]
