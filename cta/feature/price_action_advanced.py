"""
Al Brooks 价格行为 —— 高级特征

在 price_action.py (基础) 和 price_action_context.py (上下文) 之上，
进一步细化腿结构、回调分类、通道细节、交易区间进阶、进场时机和多时间框架关系。

十六、腿微观结构 (Leg Microstructure)
十七、回调形态分类 (Pullback Pattern Classification)
十八、通道细分 (Channel Details)
十九、交易区间进阶 (Trading Range Advanced)
二十、进场/出场时机 (Entry/Exit Timing)
二十一、多时间框架关系 (Multi-Timeframe)
"""
import numpy as np
import pandas as pd

from cta.feature.price_action import (
    bar_body_abs,
    bar_body_relative,
    bar_range,
    bar_range_avg,
    close_position,
    is_trend_bar,
    is_doji,
    inside_bar,
    breakout_up,
    breakout_down,
    current_leg_length,
    current_leg_range,
    higher_high,
    lower_low,
    bull_reversal_bar,
    bear_reversal_bar,
)


# ====================================================================
# 辅助函数
# ====================================================================

def _compute_atr(high: pd.Series, low: pd.Series, close: pd.Series,
                 window: int = 14) -> pd.Series:
    """计算 ATR，避免循环导入 volatility.py"""
    prev_c = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_c).abs(),
        (low - prev_c).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / window, adjust=False).mean()


def _direction(close: pd.Series, open_: pd.Series) -> pd.Series:
    """统一方向计算，NaN 填 0"""
    return np.sign(close - open_).fillna(0)


def _leg_id(direction: pd.Series) -> pd.Series:
    """
    给每根K线分配腿编号（同方向连续K线属于同一条腿）。
    十字星(d=0)延续前一条腿。
    """
    d = direction.copy()
    d = d.replace(0, np.nan).ffill().fillna(0)
    return (d != d.shift(1)).cumsum()


# ====================================================================
# 十六、腿微观结构 (Leg Microstructure)
# ====================================================================

def leg_body_consistency(open_: pd.Series, close: pd.Series,
                         high: pd.Series, low: pd.Series,
                         window: int = 10) -> pd.Series:
    """
    最近 N 根同方向K线的实体大小一致性（CV = std/mean）
    低 = 一致 = 强趋势腿，高 = 不一致 = 弱腿
    """
    body = bar_body_abs(open_, close)
    d = _direction(close, open_)
    leg = _leg_id(d)
    # 只在腿内滚动统计
    mean = body.rolling(window, min_periods=2).mean()
    std = body.rolling(window, min_periods=2).std()
    cv = std / mean.replace(0, np.nan)
    return cv.fillna(1.0)


def leg_close_consistency(open_: pd.Series, high: pd.Series,
                           low: pd.Series, close: pd.Series,
                           window: int = 10) -> pd.Series:
    """
    最近 N 根K线的收盘位置一致性
    持续 >0.7 = 极强多头腿，持续 <0.3 = 极强空头腿
    """
    pos = close_position(open_, high, low, close)
    return pos.rolling(window, min_periods=1).mean()


def leg_gap_count(open_: pd.Series, close: pd.Series,
                  window: int = 10) -> pd.Series:
    """
    最近 N 根K线中同方向缺口的数量
    Brooks: 趋势中的缺口 = 测量缺口 = 趋势非常强
    """
    prev_close = close.shift(1)
    gap_up = (open_ > prev_close).astype(int)
    gap_down = (open_ < prev_close).astype(int)
    d = _direction(close, open_)
    # 多头方向的向上缺口 + 空头方向的向下缺口
    aligned_gap = np.where(d > 0, gap_up, np.where(d < 0, gap_down, 0))
    return pd.Series(aligned_gap, index=close.index).rolling(window).sum()


def trend_bar_cluster(open_: pd.Series, close: pd.Series,
                      high: pd.Series, low: pd.Series,
                      window: int = 20) -> pd.Series:
    """
    N 根内最长连续趋势K线段长度
    Brooks: 连续趋势K线 = Spike = 强趋势的标志
    """
    tb = is_trend_bar(open_, close, high, low)
    is_tb = (tb != 0).astype(int)

    # 滚动窗口内最大连续1的长度
    def _max_consecutive(arr):
        max_run = 0
        run = 0
        for v in arr:
            if v == 1:
                run += 1
                if run > max_run:
                    max_run = run
            else:
                run = 0
        return max_run

    return is_tb.rolling(window, min_periods=1).apply(_max_consecutive, raw=True)


def leg_ema_separation(close: pd.Series, high: pd.Series, low: pd.Series,
                       ema_span: int = 20, window: int = 10) -> pd.Series:
    """
    最近 N 根K线与 EMA 的平均距离 / ATR
    大 = 趋势过度延伸，可能回调
    """
    ema_val = close.ewm(span=ema_span, adjust=False).mean()
    atr_val = _compute_atr(high, low, close, 14).replace(0, np.nan)
    dist = (close - ema_val).abs() / atr_val
    return dist.rolling(window, min_periods=1).mean()


def leg_acceleration(open_: pd.Series, close: pd.Series,
                     window: int = 10) -> pd.Series:
    """
    腿加速度：后半段平均实体 / 前半段平均实体
    >1 = 加速（趋势增强），<1 = 减速（趋势即将结束）
    """
    body = bar_body_abs(open_, close)
    half = window // 2
    if half < 1:
        half = 1
    first_half = body.shift(half).rolling(half, min_periods=1).mean()
    second_half = body.rolling(half, min_periods=1).mean()
    return second_half / first_half.replace(0, np.nan)


# ====================================================================
# 十七、回调形态分类 (Pullback Pattern Classification)
# ====================================================================

def pullback_type(close: pd.Series, open_: pd.Series,
                  high: pd.Series, low: pd.Series) -> pd.Series:
    """
    回调类型分类
    1 = 单K线回调 (V型)
    2 = 两腿回调 (最常见)
    3 = 三腿回调 (楔形)
    0 = 不在回调中或无法分类
    """
    d = _direction(close, open_)
    leg = _leg_id(d)
    result = pd.Series(0, index=close.index)

    # 追踪腿的方向序列
    prev_main_dir = 0
    pullback_leg_count = 0
    in_pullback = False

    for i in range(1, len(d)):
        cur_d = int(d.iloc[i])
        prev_d_val = int(d.iloc[i - 1])

        if cur_d == 0:
            continue

        if prev_main_dir == 0:
            prev_main_dir = cur_d
            continue

        if cur_d != prev_d_val and prev_d_val != 0:
            if cur_d == -prev_main_dir:
                # 进入回调
                if not in_pullback:
                    in_pullback = True
                    pullback_leg_count = 1
                else:
                    pullback_leg_count += 1
            elif cur_d == prev_main_dir:
                # 回到主方向
                if in_pullback:
                    result.iloc[i] = min(pullback_leg_count, 3)
                    in_pullback = False
                    pullback_leg_count = 0
                prev_main_dir = cur_d

        if not in_pullback and cur_d != 0:
            prev_main_dir = cur_d

    return result


def pullback_overlap_with_leg(close: pd.Series, open_: pd.Series,
                               high: pd.Series, low: pd.Series,
                               window: int = 20) -> pd.Series:
    """
    回调K线的范围与前腿范围的重叠程度 [0, 1]
    低 = 回调K线远离前腿 = 强趋势，高 = 回调深入前腿
    """
    d = _direction(close, open_)
    pb_depth = (high.rolling(window).max() - close) / \
               (high.rolling(window).max() - low.rolling(window).min()).replace(0, np.nan)
    return pb_depth.clip(0, 1)


def pullback_close_vs_entry(open_: pd.Series, high: pd.Series,
                             low: pd.Series, close: pd.Series) -> pd.Series:
    """
    回调末K线的收盘位置
    >0.7 (多头回调末) = 好入场信号
    <0.3 (空头回调末) = 好做空信号
    """
    return close_position(open_, high, low, close)


def first_pullback(close: pd.Series, open_: pd.Series,
                   high: pd.Series, low: pd.Series,
                   window: int = 20) -> pd.Series:
    """
    是否是突破后的第一次回调 (0/1)
    Brooks: 第一次回调 = 最高概率入场
    """
    bu = breakout_up(high, window)
    bd = breakout_down(low, window)

    d = _direction(close, open_)
    result = pd.Series(0, index=close.index)

    # 记录最近突破位置和方向
    last_bo_idx = -window - 1
    last_bo_dir = 0
    had_pullback = False

    for i in range(len(close)):
        if bu.iloc[i] == 1:
            last_bo_idx = i
            last_bo_dir = 1
            had_pullback = False
        elif bd.iloc[i] == 1:
            last_bo_idx = i
            last_bo_dir = -1
            had_pullback = False

        # 在突破后的合理范围内检测回调
        if 0 < (i - last_bo_idx) <= window and not had_pullback:
            cur_d = int(d.iloc[i])
            if cur_d != 0 and cur_d == -last_bo_dir:
                result.iloc[i] = 1
                had_pullback = True

    return result


def high_1_2_3(close: pd.Series, open_: pd.Series,
               high: pd.Series, low: pd.Series) -> pd.Series:
    """
    Brooks H1/H2/H3 标记：多头回调计数
    H1 = 第一次多头回调完成，H2 = 第二次，H3 = 第三次
    返回: 0/1/2/3
    """
    d = _direction(close, open_)
    result = pd.Series(0, index=close.index)
    hcount = 0
    prev_d = 0
    in_pullback = False

    for i in range(1, len(d)):
        cur_d = int(d.iloc[i])
        if cur_d == 0:
            continue

        if cur_d < 0 and prev_d >= 0:
            # 开始空头方向 = 多头趋势中的回调
            in_pullback = True
        elif cur_d > 0 and prev_d <= 0 and in_pullback:
            # 回调结束，回到多头
            hcount += 1
            result.iloc[i] = min(hcount, 3)
            in_pullback = False
        elif cur_d < 0 and prev_d < 0:
            pass  # 继续回调
        elif cur_d > 0 and prev_d > 0:
            # 连续多头 = 趋势中
            if hcount > 0 and not in_pullback:
                pass  # 保持计数

        if cur_d != 0:
            prev_d = cur_d

        # 如果出现新的 LL，重置计数
        if i >= 2 and low.iloc[i] < low.iloc[i - 1] and low.iloc[i - 1] < low.iloc[i - 2]:
            hcount = 0

    return result


def low_1_2_3(close: pd.Series, open_: pd.Series,
              high: pd.Series, low: pd.Series) -> pd.Series:
    """
    Brooks L1/L2/L3 标记：空头回调计数
    L1 = 第一次空头回调完成，L2 = 第二次，L3 = 第三次
    返回: 0/1/2/3
    """
    d = _direction(close, open_)
    result = pd.Series(0, index=close.index)
    lcount = 0
    prev_d = 0
    in_pullback = False

    for i in range(1, len(d)):
        cur_d = int(d.iloc[i])
        if cur_d == 0:
            continue

        if cur_d > 0 and prev_d <= 0:
            in_pullback = True
        elif cur_d < 0 and prev_d >= 0 and in_pullback:
            lcount += 1
            result.iloc[i] = min(lcount, 3)
            in_pullback = False
        elif cur_d > 0 and prev_d > 0:
            pass
        elif cur_d < 0 and prev_d < 0:
            if lcount > 0 and not in_pullback:
                pass

        if cur_d != 0:
            prev_d = cur_d

        if i >= 2 and high.iloc[i] > high.iloc[i - 1] and high.iloc[i - 1] > high.iloc[i - 2]:
            lcount = 0

    return result


# ====================================================================
# 十八、通道细分 (Channel Details)
# ====================================================================

def _linear_regression_channel(series: pd.Series, window: int):
    """滚动线性回归通道上下轨"""
    x = np.arange(window, dtype=float)
    x_mean = x.mean()
    x_var = ((x - x_mean) ** 2).sum()
    if x_var == 0:
        return series, series, series

    def _upper_lower(y):
        y_mean = y.mean()
        slope = np.sum((x - x_mean) * (y - y_mean)) / x_var
        intercept = y_mean - slope * x_mean
        fitted = intercept + slope * x[-1]
        residuals = y - (intercept + slope * x)
        std_r = residuals.std()
        return fitted, fitted + 2 * std_r, fitted - 2 * std_r

    mid = series.rolling(window, min_periods=window).apply(
        lambda y: _upper_lower(y)[0], raw=True)
    upper = series.rolling(window, min_periods=window).apply(
        lambda y: _upper_lower(y)[1], raw=True)
    lower = series.rolling(window, min_periods=window).apply(
        lambda y: _upper_lower(y)[2], raw=True)
    return mid, upper, lower


def channel_overshoot(high: pd.Series, low: pd.Series, close: pd.Series,
                      window: int = 20) -> pd.Series:
    """
    通道超越 (0/1)
    价格突破通道线后又回到通道内 = 反转信号
    """
    _, upper, lower = _linear_regression_channel(close, window)
    # 上超越: 前一根 high > upper 且当前收回
    prev_above = (high.shift(1) > upper.shift(1)).fillna(False)
    back_in = (close <= upper).fillna(False)
    up_overshoot = (prev_above & back_in).astype(int)

    prev_below = (low.shift(1) < lower.shift(1)).fillna(False)
    back_in_low = (close >= lower).fillna(False)
    down_overshoot = (prev_below & back_in_low).astype(int)

    return up_overshoot + down_overshoot


def channel_touch_count(high: pd.Series, low: pd.Series, close: pd.Series,
                        window: int = 20) -> pd.Series:
    """
    N 根内触碰通道上下轨的次数
    多次触碰后突破概率高
    """
    _, upper, lower = _linear_regression_channel(close, window)
    range_band = (upper - lower).replace(0, np.nan)
    # 触碰 = 价格在上轨/下轨附近 10% 范围内
    tolerance = range_band * 0.1
    touch_up = (high >= upper - tolerance.fillna(0)).astype(int)
    touch_down = (low <= lower + tolerance.fillna(0)).astype(int)
    total = touch_up + touch_down
    return total.rolling(window, min_periods=1).sum()


def channel_age(high: pd.Series, low: pd.Series, close: pd.Series,
                window: int = 20) -> pd.Series:
    """
    当前通道已存续的K线数
    Brooks: 通道越老 = 突破概率越高
    算法: 连续在通道内的K线计数
    """
    _, upper, lower = _linear_regression_channel(close, window)
    in_channel = ((close <= upper) & (close >= lower)).astype(int)
    in_channel = in_channel.fillna(0)
    # 连续在通道内的计数
    groups = (in_channel != in_channel.shift(1)).cumsum()
    return in_channel.groupby(groups).cumsum()


def spike_and_channel(open_: pd.Series, close: pd.Series,
                      high: pd.Series, low: pd.Series,
                      spike_window: int = 3,
                      channel_window: int = 10) -> pd.Series:
    """
    Spike & Channel 检测
    Brooks: 急涨/急跌(spike)后进入缓慢通道 = 经典趋势形态
    返回: 1=多头 spike+channel, -1=空头, 0=无
    """
    body = bar_body_abs(open_, close)
    avg_body = body.rolling(20, min_periods=1).mean().replace(0, np.nan)
    d = _direction(close, open_)

    # Spike: 连续 spike_window 根大趋势K线（实体>1.5倍均值）
    big_bar = (body > 1.5 * avg_body).astype(int)
    spike_bull = (big_bar * (d > 0).astype(int)).rolling(spike_window).sum()
    spike_bear = (big_bar * (d < 0).astype(int)).rolling(spike_window).sum()

    # Channel: spike 后的 channel_window 根K线中重叠度高
    from cta.feature.price_action import bar_overlap
    overlap = bar_overlap(high, low)
    overlap_avg = overlap.rolling(channel_window, min_periods=1).mean()

    result = pd.Series(0, index=close.index)
    for i in range(spike_window + channel_window, len(close)):
        # 检查 spike_window 之前是否有 spike
        spike_start = i - channel_window
        if spike_bull.iloc[spike_start] >= spike_window and overlap_avg.iloc[i] > 0.4:
            result.iloc[i] = 1
        elif spike_bear.iloc[spike_start] >= spike_window and overlap_avg.iloc[i] > 0.4:
            result.iloc[i] = -1

    return result


# ====================================================================
# 十九、交易区间进阶 (Trading Range Advanced)
# ====================================================================

def range_maturity(high: pd.Series, low: pd.Series, close: pd.Series,
                   window: int = 20) -> pd.Series:
    """
    区间成熟度：区间已持续K线数 / window
    Brooks: 区间越成熟，突破后运动幅度越大
    """
    hh = high.rolling(window).max()
    ll = low.rolling(window).min()
    range_w = (hh - ll).replace(0, np.nan)
    # 价格在区间中部60%内
    in_range = ((close < hh - 0.2 * range_w) &
                (close > ll + 0.2 * range_w)).astype(int)
    in_range = in_range.fillna(0)
    groups = (in_range != in_range.shift(1)).cumsum()
    bars_in_range = in_range.groupby(groups).cumsum()
    return bars_in_range / window


def range_shrinking(high: pd.Series, low: pd.Series,
                    window: int = 20) -> pd.Series:
    """
    区间收缩：后半段区间宽度 / 前半段区间宽度
    <1 = 区间在收缩 = 即将突破
    """
    half = window // 2
    if half < 2:
        half = 2
    first_range = (high.shift(half).rolling(half).max() -
                   low.shift(half).rolling(half).min())
    second_range = high.rolling(half).max() - low.rolling(half).min()
    return second_range / first_range.replace(0, np.nan)


def range_false_bo_count(high: pd.Series, low: pd.Series,
                         close: pd.Series, window: int = 20) -> pd.Series:
    """
    区间内累计假突破次数
    Brooks: 假突破越多 = 下次真突破概率越高
    """
    bu = breakout_up(high, window)
    bd = breakout_down(low, window)
    # 假突破 = 突破后下一根收回区间内
    prev_hh = high.shift(1).rolling(window - 1, min_periods=1).max()
    prev_ll = low.shift(1).rolling(window - 1, min_periods=1).min()

    fail_up = (bu.shift(1).fillna(0).astype(bool) &
               (close < prev_hh)).astype(int)
    fail_down = (bd.shift(1).fillna(0).astype(bool) &
                 (close > prev_ll)).astype(int)
    total_fail = fail_up + fail_down
    return total_fail.rolling(window * 2, min_periods=1).sum()


def mid_range_bounce(high: pd.Series, low: pd.Series,
                     close: pd.Series, window: int = 20) -> pd.Series:
    """
    中线反弹信号
    Brooks: 交易区间中价格到达中线后反弹 = 重要参考
    返回: 1=从下方到达中线附近且反弹, -1=从上方到达中线附近且反弹, 0=无
    """
    hh = high.rolling(window).max()
    ll = low.rolling(window).min()
    mid = (hh + ll) / 2
    range_w = (hh - ll).replace(0, np.nan)
    dist_to_mid = (close - mid).abs() / range_w

    # 在中线附近 (距中线 < 10% 区间宽度)
    near_mid = dist_to_mid < 0.1
    # 方向：从下方来还是上方来
    from_below = close.shift(1) < mid.shift(1)
    from_above = close.shift(1) > mid.shift(1)
    # 反弹方向
    bouncing_up = close > close.shift(1)
    bouncing_down = close < close.shift(1)

    result = pd.Series(0, index=close.index)
    result = result.where(~(near_mid & from_below & bouncing_up), 1)
    result = result.where(~(near_mid & from_above & bouncing_down), -1)
    return result


# ====================================================================
# 二十、进场/出场时机 (Entry/Exit Timing)
# ====================================================================

def second_entry(open_: pd.Series, high: pd.Series, low: pd.Series,
                 close: pd.Series) -> pd.Series:
    """
    第二次进场信号 (0/1)
    Brooks: 第一次入场失败后的第二次同方向信号 = 最可靠
    """
    bull_sig = bull_reversal_bar(open_, high, low, close)
    bear_sig = bear_reversal_bar(open_, high, low, close)

    result = pd.Series(0, index=close.index)
    last_bull_sig_idx = -100
    last_bear_sig_idx = -100

    for i in range(1, len(close)):
        if bull_sig.iloc[i] == 1:
            if 0 < (i - last_bull_sig_idx) <= 10:
                result.iloc[i] = 1
            last_bull_sig_idx = i
        if bear_sig.iloc[i] == 1:
            if 0 < (i - last_bear_sig_idx) <= 10:
                result.iloc[i] = 1
            last_bear_sig_idx = i

    return result


def failed_signal_reversal(open_: pd.Series, high: pd.Series,
                            low: pd.Series, close: pd.Series) -> pd.Series:
    """
    信号失败反转 (0/1)
    Brooks: 看涨信号K线后下跌 or 看跌信号K线后上涨 = 极强反转
    """
    bull_sig = bull_reversal_bar(open_, high, low, close)
    bear_sig = bear_reversal_bar(open_, high, low, close)
    d = _direction(close, open_)

    # 看涨信号后下一根收阴 = 失败反转
    bull_fail = (bull_sig.shift(1).fillna(0).astype(bool) &
                 (d < 0)).astype(int)
    # 看跌信号后下一根收阳 = 失败反转
    bear_fail = (bear_sig.shift(1).fillna(0).astype(bool) &
                 (d > 0)).astype(int)
    return bull_fail + bear_fail


def trail_stop_level(high: pd.Series, low: pd.Series,
                     close: pd.Series, window: int = 10) -> pd.Series:
    """
    建议移动止损位（基于 swing low/high）
    返回止损距离占 close 的百分比
    正 = 多头止损距离，负 = 空头止损距离
    """
    # 多头止损 = 近期最低点
    recent_low = low.rolling(window, min_periods=1).min()
    # 空头止损 = 近期最高点
    recent_high = high.rolling(window, min_periods=1).max()

    # 根据趋势方向选择
    ema = close.ewm(span=20, adjust=False).mean()
    uptrend = close > ema
    stop_dist = pd.Series(0.0, index=close.index)
    stop_dist = np.where(uptrend,
                         (close - recent_low) / close.replace(0, np.nan) * 100,
                         (recent_high - close) / close.replace(0, np.nan) * 100)
    return pd.Series(stop_dist, index=close.index)


def bar_since_signal(open_: pd.Series, high: pd.Series,
                     low: pd.Series, close: pd.Series) -> pd.Series:
    """
    距最近信号K线的根数
    Brooks: 5根内入场最佳
    """
    bull_sig = bull_reversal_bar(open_, high, low, close)
    bear_sig = bear_reversal_bar(open_, high, low, close)
    any_sig = ((bull_sig == 1) | (bear_sig == 1)).astype(int)

    result = pd.Series(np.nan, index=close.index)
    count = np.nan
    for i in range(len(close)):
        if any_sig.iloc[i] == 1:
            count = 0
        elif not np.isnan(count):
            count += 1
        result.iloc[i] = count

    return result


# ====================================================================
# 二十一、多时间框架关系 (Multi-Timeframe)
# ====================================================================

def htf_trend_alignment(close: pd.Series, open_: pd.Series,
                         high: pd.Series, low: pd.Series) -> pd.Series:
    """
    高级别趋势与低级别趋势是否对齐
    用 60 日视角 vs 20 日视角
    1 = 对齐（高概率），0 = 不对齐
    """
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema60 = close.ewm(span=60, adjust=False).mean()
    dir20 = np.sign(ema20.diff(5)).fillna(0)
    dir60 = np.sign(ema60.diff(10)).fillna(0)
    return (dir20 == dir60).astype(int)


def ltf_setup_quality(open_: pd.Series, high: pd.Series,
                       low: pd.Series, close: pd.Series) -> pd.Series:
    """
    低级别(3根)信号质量评估 [-5, +5]
    综合 3 根K线的方向、实体大小、收盘位置
    """
    d = _direction(close, open_)
    pos = close_position(open_, high, low, close)
    body_rel = bar_body_relative(open_, close, high, low)

    # 3 根K线的均值
    score = (d * 2 + (pos - 0.5) * 2 + body_rel).rolling(3, min_periods=1).mean()
    return score.clip(-5, 5)


def timeframe_conflict(close: pd.Series) -> pd.Series:
    """
    时间框架冲突度
    3/20/60 日趋势方向的分歧度
    0 = 三个时间框架一致，1 = 部分冲突，2 = 完全冲突
    """
    dir3 = np.sign(close.ewm(span=3, adjust=False).mean().diff(2)).fillna(0)
    dir20 = np.sign(close.ewm(span=20, adjust=False).mean().diff(5)).fillna(0)
    dir60 = np.sign(close.ewm(span=60, adjust=False).mean().diff(10)).fillna(0)

    # 两两比较，不同 +1
    conflict = ((dir3 != dir20).astype(int) +
                (dir20 != dir60).astype(int) +
                (dir3 != dir60).astype(int))
    # 归一化到 0-2
    return (conflict / 3 * 2).clip(0, 2)


# ====================================================================
# 批量计算
# ====================================================================

def compute_price_action_advanced_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    为单品种批量计算高级 Al Brooks 特征
    输入 df 需包含: open, high, low, close
    """
    o = df["open"]
    h = df["high"]
    l = df["low"]   # noqa: E741
    c = df["close"]
    result = pd.DataFrame(index=df.index)

    # ---- 十六、腿微观结构 ----
    for w in [5, 10, 20]:
        result[f"pa_leg_body_cv_{w}"] = leg_body_consistency(o, c, h, l, w)
        result[f"pa_leg_close_cons_{w}"] = leg_close_consistency(o, h, l, c, w)
    for w in [10, 20]:
        result[f"pa_leg_gap_count_{w}"] = leg_gap_count(o, c, w)
    result["pa_trend_bar_cluster_20"] = trend_bar_cluster(o, c, h, l, 20)
    for w in [10, 20]:
        result[f"pa_leg_ema_sep_{w}"] = leg_ema_separation(c, h, l, 20, w)
    result["pa_leg_accel_10"] = leg_acceleration(o, c, 10)

    # ---- 十七、回调形态分类 ----
    result["pa_pullback_type"] = pullback_type(c, o, h, l)
    result["pa_pb_overlap_20"] = pullback_overlap_with_leg(c, o, h, l, 20)
    result["pa_pb_close_entry"] = pullback_close_vs_entry(o, h, l, c)
    result["pa_first_pullback_20"] = first_pullback(c, o, h, l, 20)
    result["pa_h123"] = high_1_2_3(c, o, h, l)
    result["pa_l123"] = low_1_2_3(c, o, h, l)

    # ---- 十八、通道细分 ----
    result["pa_channel_overshoot"] = channel_overshoot(h, l, c, 20)
    result["pa_channel_touch_20"] = channel_touch_count(h, l, c, 20)
    result["pa_channel_age_20"] = channel_age(h, l, c, 20)
    result["pa_spike_channel"] = spike_and_channel(o, c, h, l)

    # ---- 十九、交易区间进阶 ----
    result["pa_range_maturity_20"] = range_maturity(h, l, c, 20)
    result["pa_range_shrinking_20"] = range_shrinking(h, l, 20)
    result["pa_range_false_bo"] = range_false_bo_count(h, l, c, 20)
    result["pa_mid_range_bounce"] = mid_range_bounce(h, l, c, 20)

    # ---- 二十、进场/出场时机 ----
    result["pa_second_entry"] = second_entry(o, h, l, c)
    result["pa_failed_sig_rev"] = failed_signal_reversal(o, h, l, c)
    result["pa_trail_stop"] = trail_stop_level(h, l, c, 10)
    result["pa_bar_since_sig"] = bar_since_signal(o, h, l, c)

    # ---- 二十一、多时间框架 ----
    result["pa_htf_align"] = htf_trend_alignment(c, o, h, l)
    result["pa_ltf_quality"] = ltf_setup_quality(o, h, l, c)
    result["pa_tf_conflict"] = timeframe_conflict(c)

    return result
