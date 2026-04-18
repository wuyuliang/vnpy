"""
在线特征生成接口 —— 与离线 100% 一致的纯函数 API

适用场景:
    - 实时行情服务: 收到一根新 bar 后立即计算特征向量
    - 回测引擎: 逐 bar 调用获取最新特征
    - 模型推理服务: HTTP/gRPC 接口接收 OHLCV 历史窗口返回特征

设计原则:
    1. 纯函数: 无文件 IO，无全局状态，可重复调用
    2. 与离线一致: 内部直接复用 compute_single_symbol_features
    3. 极简接口: 输入 OHLCV DataFrame，输出 (原始 + 特征) DataFrame
    4. 跨频率: day / minute / 5min / 15min / 30min / 60min 一份代码

输入数据格式（DataFrame 必需列）:
    datetime, open, high, low, close, volume
    可选列: open_interest, turnover, symbol, exchange

最小回看 bar 数 (low-water mark)：
    达到下表数量后所有特征都可用（更短窗口部分特征会是 NaN）

用法示例:
    >>> from cta.feature.online import compute_features, compute_latest_features
    >>> import pandas as pd
    >>>
    >>> # 1. 全量计算（与离线等价）
    >>> df = pd.DataFrame({...})  # 至少 250 行 OHLCV
    >>> feat_df = compute_features(df, interval="day")
    >>>
    >>> # 2. 只取最新一行特征（实时推理）
    >>> latest = compute_latest_features(df, interval="day")
    >>> feature_vec = latest.iloc[0].to_dict()
    >>>
    >>> # 3. 滚动窗口（流式调用）
    >>> generator = FeatureGenerator(interval="day", window=500)
    >>> for bar in stream:
    ...     feat = generator.update(bar)  # 返回最新一行特征
"""
from __future__ import annotations

from collections import deque
from typing import Optional

import pandas as pd

from cta.feature.compute import compute_single_symbol_features
from cta.feature.loader import normalize_interval

# 不同频率推荐的最小历史 bar 数（保证大多数特征非 NaN）
# 同时兼容规范名（minute5/...）与旧名（5min/...）
MIN_LOOKBACK = {
    "day":      250,    # 约一年
    "minute":   1000,   # 约 3-4 个交易日（225 分钟/日）
    "minute5":  500,    # 约 10 个交易日
    "minute15": 300,    # 约 17 个交易日
    "minute30": 250,    # 约 28 个交易日
    "minute60": 250,    # 约 50 个交易日
    # 旧名别名
    "5min":  500,
    "15min": 300,
    "30min": 250,
    "60min": 250,
}

# 必需列
_REQUIRED_COLS = ["datetime", "open", "high", "low", "close", "volume"]


def _validate(df: pd.DataFrame) -> pd.DataFrame:
    """校验并补全必要列"""
    missing = [c for c in _REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"缺少必需列: {missing}")
    if "open_interest" not in df.columns:
        df = df.copy()
        df["open_interest"] = 0
    return df


def get_recommended_lookback(interval: str = "day") -> int:
    """获取该频率推荐的最小历史 bar 数（兼容规范名与旧名）"""
    canon = normalize_interval(interval)
    return MIN_LOOKBACK.get(canon, MIN_LOOKBACK.get(interval, 250))


def compute_features(
    df: pd.DataFrame,
    interval: str = "day",
) -> pd.DataFrame:
    """
    全量计算特征 —— 在线/离线统一接口

    Parameters
    ----------
    df : OHLCV DataFrame
    interval : 频率 (day / minute / 5min / 15min / 30min / 60min)

    Returns
    -------
    DataFrame: 原始列 + 特征列
    """
    df = _validate(df)
    canon = normalize_interval(interval)
    # 只有 1min 频率需要时间同比（minute_tod）特征；
    # 其它盘中频率（minute5/15/30/60）走通用流程（与 day 相同特征集）
    compute_interval = "minute" if canon == "minute" else "day"
    return compute_single_symbol_features(df, interval=compute_interval)


def compute_latest_features(
    df: pd.DataFrame,
    interval: str = "day",
    n: int = 1,
) -> pd.DataFrame:
    """
    只返回最新 n 行的特征（适合实时推理场景）

    Parameters
    ----------
    df : OHLCV DataFrame，应包含足够历史（>= get_recommended_lookback(interval)）
    interval : 频率
    n : 取最后 n 行（默认 1，即最新一根 bar）

    Returns
    -------
    DataFrame: 最新 n 行的（原始 + 特征）
    """
    feat = compute_features(df, interval=interval)
    return feat.tail(n).reset_index(drop=True)


class FeatureGenerator:
    """
    流式特征生成器 —— 维护滚动窗口，每次喂入新 bar 返回最新特征

    适合 vnpy/回测引擎/实时服务的 on_bar 回调场景。
    内部维护 deque 保证窗口长度不超过 window，避免内存增长。

    Parameters
    ----------
    interval : 频率
    window : 滚动窗口大小（bar 数），默认取该频率推荐的最小回看
    symbol : 可选，写入返回 DataFrame 的 symbol 列
    exchange : 可选，写入返回 DataFrame 的 exchange 列

    Example
    -------
    >>> gen = FeatureGenerator(interval="minute", window=2000)
    >>> for bar in stream:
    ...     feat_row = gen.update(bar)  # bar 是 dict 或 Series
    ...     if feat_row is not None:
    ...         model.predict(feat_row)
    """

    def __init__(
        self,
        interval: str = "day",
        window: Optional[int] = None,
        symbol: str = "",
        exchange: str = "",
    ):
        self.interval = interval
        self.window = window or get_recommended_lookback(interval)
        self.symbol = symbol
        self.exchange = exchange
        self._buffer: deque[dict] = deque(maxlen=self.window)

    def warmup(self, df: pd.DataFrame) -> None:
        """用历史 DataFrame 预热缓冲区"""
        df = _validate(df)
        for _, row in df.tail(self.window).iterrows():
            self._buffer.append(row.to_dict())

    def update(self, bar) -> Optional[pd.Series]:
        """
        喂入一根新 bar，返回最新一行特征（Series）
        若缓冲区数据不足以计算特征，返回 None

        bar : dict / Series，必须包含 datetime, open, high, low, close, volume
        """
        if isinstance(bar, pd.Series):
            bar = bar.to_dict()
        self._buffer.append(bar)

        if len(self._buffer) < 30:  # 极少数特征也至少需要 30 根
            return None

        df = pd.DataFrame(list(self._buffer))
        if self.symbol and "symbol" not in df.columns:
            df["symbol"] = self.symbol
        if self.exchange and "exchange" not in df.columns:
            df["exchange"] = self.exchange

        feat = compute_features(df, interval=self.interval)
        return feat.iloc[-1]

    def reset(self) -> None:
        self._buffer.clear()

    def __len__(self) -> int:
        return len(self._buffer)
