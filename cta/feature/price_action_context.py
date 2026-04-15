"""
Al Brooks 价格行为 —— 交易决策上下文特征

与 price_action.py 的区别：
    price_action.py 描述"当前K线是什么"
    price_action_context.py 描述"当前处于什么交易环境、该不该交易、风险回报如何"

Brooks 反复强调的核心原则：
    1. 永远先判断 context（趋势 vs 交易区间 vs 突破）
    2. 只在有利概率 + 合理风险回报的位置交易
    3. 信号K线本身不重要，重要的是它出现在哪里

本模块覆盖以下高级概念：

十、市场结构识别 (Market Structure)
    - 区间宽度归一化 (Range / ATR)
    - inside bar 比例 / overlap 比例（判断拥堵）
    - 趋势K线占比（多/空分别统计）
    - 十字星密度
    - 两条腿回撤识别 (Two-Legged Pullback)
    - 当前 EMA 斜率 (Trend Slope)

十一、回撤质量 (Pullback Quality)
    - 前上涨/下跌腿的斜率
    - 回调深度相对前腿
    - 回调中趋势K线占比（弱回调 vs 强回调）
    - 回调 bar 数 / 前腿 bar 数
    - 回调是否到 EMA 附近

十二、突破质量 (Breakout Quality)
    - 突破K线实体占比 (breakout body ratio)
    - 突破K线收盘位置 (breakout close position)
    - 突破时的 volume ratio
    - 突破后几根K线的方向一致性
    - 突破前区间的成熟度（bar 数量）
    - 突破前区间的紧密度

十三、关键价位与空间 (Key Levels & Space)
    - 距前高/前低的空间 (%)
    - 距 EMA 的距离 (%)
    - 当前在区间的位置（上1/3, 中, 下1/3）
    - 上方/下方空间比
    - 阻力密度 / 支撑密度

十四、风险回报 (Risk-Reward)
    - 预期风险 (止损距离 = 信号K线极端 or ATR)
    - 预期回报 (前腿等距 or 区间宽度)
    - Expected RR ratio
    - Brooks Scalp / Swing / Trend trade 可行性评分

十五、多头/空头力量平衡 (Bull vs Bear Pressure)
    - N日内多头 vs 空头趋势K线占比差
    - N日内收盘位置平均偏向
    - N日内上影 vs 下影总量比较
    - 买方/卖方 climax 检测
    - 连续方向的 momentum 衰减
"""
import numpy as np
import pandas as pd

from cta.feature.price_action import (
    bar_range,
    bar_body_abs,
    bar_body_relative,
    bar_range_avg,
    close_position,
    is_trend_bar,
    is_doji,
    inside_bar,
    bar_overlap,
    current_leg_length,
    breakout_up,
    breakout_down,
)


# ====================================================================
# 十、市场结构识别 (Market Structure)
# ====================================================================

def range_over_atr(high: pd.Series, low: pd.Series, close: pd.Series,
                   range_window: int = 20, atr_window: int = 14) -> pd.Series:
    """
    区间宽度 / ATR
    Brooks: >3 说明区间已经很宽（大区间），突破后的空间大
            <1.5 说明区间很紧（紧凑区间），即将突破
    """
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / atr_window, adjust=False).mean().replace(0, np.nan)
    range_width = high.rolling(range_window).max() - low.rolling(range_window).min()
    return range_width / atr


def inside_bar_ratio(high: pd.Series, low: pd.Series,
                     window: int = 20) -> pd.Series:
    """
    N 根内 inside bar 占比 [0, 1]
    Brooks: 高比例 = 市场犹豫不决 = 即将大幅突破
    """
    ib = inside_bar(high, low)
    return ib.rolling(window).mean()


def overlap_ratio(high: pd.Series, low: pd.Series,
                  window: int = 20) -> pd.Series:
    """
    N 根内高重叠K线(overlap>0.5)的占比
    Brooks: 高比例 = 交易区间行为，低比例 = 趋势行为
    """
    ov = bar_overlap(high, low)
    return (ov > 0.5).astype(int).rolling(window).mean()


def trend_bar_ratio(open_: pd.Series, close: pd.Series, high: pd.Series,
                    low: pd.Series, window: int = 20) -> pd.DataFrame:
    """
    N 根内趋势K线占比，分多头和空头
    Brooks: 多头趋势K线远多于空头 => 多头仍强
    """
    tb = is_trend_bar(open_, close, high, low)
    bull_pct = (tb == 1).astype(int).rolling(window).mean()
    bear_pct = (tb == -1).astype(int).rolling(window).mean()
    return pd.DataFrame({
        "trend_bar_bull_pct": bull_pct,
        "trend_bar_bear_pct": bear_pct,
        "trend_bar_net": bull_pct - bear_pct,
    })


def doji_density(open_: pd.Series, close: pd.Series, high: pd.Series,
                 low: pd.Series, window: int = 10) -> pd.Series:
    """
    十字星密度：N 根内十字星占比
    Brooks: 高密度 = barb wire = 不适合交易
    """
    dj = is_doji(open_, close, high, low)
    return dj.rolling(window).mean()


def two_legged_pullback(close: pd.Series, open_: pd.Series,
                        high: pd.Series, low: pd.Series) -> pd.Series:
    """
    两条腿回撤检测
    Brooks: 大多数回撤由两条腿组成，第二条腿结束是进场点
    返回: 1=两条腿多头回撤完成, -1=两条腿空头回撤完成, 0=无

    算法：追踪腿的方向变化，当主趋势方向的腿中出现两次反向腿后恢复
    """
    direction = np.sign(close - open_).fillna(0)
    result = pd.Series(0, index=close.index)
    leg_dir = 0
    pullback_legs = 0
    initialized = False

    for i in range(1, len(direction)):
        d = int(direction.iloc[i])
        prev_d = int(direction.iloc[i - 1])

        if d == 0:
            continue

        # 先确立主方向
        if not initialized:
            leg_dir = d
            initialized = True
            continue

        if d != prev_d and prev_d != 0:
            # 方向变了 = 新腿开始
            if d != leg_dir:
                # 进入回调腿
                pullback_legs += 1
            else:
                # 回到主方向
                if pullback_legs >= 2:
                    result.iloc[i] = leg_dir
                pullback_legs = 0

        if pullback_legs == 0:
            leg_dir = d

    return result


def ema_slope(close: pd.Series, span: int = 20,
              slope_window: int = 5) -> pd.Series:
    """
    EMA 斜率（标准化为 ATR 单位）
    Brooks: EMA 斜率是判断趋势强度和方向的核心参考
    正=上升趋势, 负=下降趋势, 接近0=横盘
    """
    ema_val = close.ewm(span=span, adjust=False).mean()
    slope = ema_val.diff(slope_window)
    # 标准化: 除以近期平均波幅
    avg_range = close.diff().abs().rolling(20).mean().replace(0, np.nan)
    return slope / avg_range


def ema_slope_acceleration(close: pd.Series, span: int = 20) -> pd.Series:
    """
    EMA 斜率的变化率（加速度）
    正=趋势加速, 负=趋势减速
    """
    slope = ema_slope(close, span, 5)
    return slope.diff(3)


# ====================================================================
# 十一、回撤质量 (Pullback Quality)
# ====================================================================

def prior_leg_slope(close: pd.Series, open_: pd.Series,
                    high: pd.Series, low: pd.Series) -> pd.Series:
    """
    前一条趋势腿的斜率（幅度/长度）
    Brooks: 陡峭的腿后面的回调更可能是好入场
    """
    direction = np.sign(close - open_).fillna(0)
    result = pd.Series(np.nan, index=close.index)

    leg_start_price = close.iloc[0]
    leg_start_idx = 0
    prev_leg_slope = np.nan
    prev_dir = 0

    for i in range(1, len(direction)):
        d = int(direction.iloc[i])
        if d == 0:
            d = prev_dir
        if d == prev_dir:
            pass  # 延续当前腿
        else:
            if prev_dir != 0:
                leg_bars = i - leg_start_idx
                if leg_bars > 0:
                    prev_leg_slope = (close.iloc[i - 1] - leg_start_price) / leg_bars
            leg_start_price = close.iloc[i]
            leg_start_idx = i
            prev_dir = d
        if prev_dir == 0 and d != 0:
            prev_dir = d
        result.iloc[i] = prev_leg_slope

    # 标准化
    avg_range = close.diff().abs().rolling(20).mean().replace(0, np.nan)
    return result / avg_range


def pullback_retrace_ratio(close: pd.Series, open_: pd.Series,
                           high: pd.Series, low: pd.Series) -> pd.Series:
    """
    回调深度相对前腿的比例 [0, 2+]
    Brooks: <0.5 = 浅回调（强趋势）, 0.5-0.618 = 健康回调, >1 = 趋势可能反转
    """
    direction = np.sign(close - open_).fillna(0)
    result = pd.Series(np.nan, index=close.index)

    leg_high = high.iloc[0]
    leg_low = low.iloc[0]
    prev_leg_range = np.nan
    prev_dir = 0
    in_pullback = False

    for i in range(1, len(direction)):
        d = int(direction.iloc[i])
        if d == 0:
            d = prev_dir

        if d == prev_dir:
            leg_high = max(leg_high, high.iloc[i])
            leg_low = min(leg_low, low.iloc[i])
        else:
            if prev_dir != 0:
                prev_leg_range = leg_high - leg_low
                in_pullback = True
            leg_high = high.iloc[i]
            leg_low = low.iloc[i]
            prev_dir = d

        if in_pullback and prev_leg_range and prev_leg_range > 0:
            current_pullback = leg_high - leg_low
            result.iloc[i] = current_pullback / prev_leg_range

    return result


def pullback_trend_bar_quality(open_: pd.Series, close: pd.Series,
                               high: pd.Series, low: pd.Series,
                               window: int = 5) -> pd.Series:
    """
    回调中趋势K线的方向质量
    Brooks: 好的（弱）回调 = 回调中的K线大多是小K线或十字星
            坏的（强）回调 = 回调中有大量反方向趋势K线
    返回: [-1, 1], 正=回调较弱（有利做多），负=回调较强
    """
    tb = is_trend_bar(open_, close, high, low)
    # 看最近N根的趋势K线净方向
    return tb.rolling(window).mean()


def pullback_bar_count_ratio(close: pd.Series, open_: pd.Series) -> pd.Series:
    """
    回调K线数 / 前腿K线数
    Brooks: <0.5 = 非常浅的回调（V型），0.5-1 = 正常，>1 = 可能不是回调
    """
    direction = np.sign(close - open_).fillna(0)
    result = pd.Series(np.nan, index=close.index)
    prev_leg_len = 0
    curr_leg_len = 0
    prev_dir = 0

    for i in range(1, len(direction)):
        d = int(direction.iloc[i])
        if d == 0:
            continue  # 十字星不计入任何腿
        if prev_dir == 0:
            # 初始化主方向
            prev_dir = d
            curr_leg_len = 1
            continue
        if d == prev_dir:
            curr_leg_len += 1
        else:
            if prev_leg_len > 0:
                result.iloc[i] = curr_leg_len / prev_leg_len
            prev_leg_len = curr_leg_len
            curr_leg_len = 1
            prev_dir = d

    return result


def dist_to_ema(close: pd.Series, span: int = 20) -> pd.Series:
    """
    收盘价距 EMA 的距离（%）
    Brooks: 回调到 EMA 附近是高概率入场点
            距 EMA 过远 = 追单风险高
    """
    ema_val = close.ewm(span=span, adjust=False).mean().replace(0, np.nan)
    return (close - ema_val) / ema_val * 100


def pullback_to_ema(close: pd.Series, span: int = 20,
                    threshold: float = 0.3) -> pd.Series:
    """
    是否回调到 EMA 附近 (0/1)
    threshold: EMA 距离 < threshold% 视为"到达"
    Brooks: 这是最常见的高概率入场位置之一
    """
    dist = dist_to_ema(close, span).abs()
    return (dist < threshold).astype(int)


# ====================================================================
# 十二、突破质量 (Breakout Quality)
# ====================================================================

def breakout_body_ratio(open_: pd.Series, close: pd.Series,
                        high: pd.Series, low: pd.Series,
                        window: int = 20) -> pd.Series:
    """
    突破K线的实体占比
    Brooks: 好突破 = 大实体(>0.7)，差突破 = 十字星或小实体
    仅在突破K线上有值，非突破K线为 0
    """
    bu = breakout_up(high, window)
    bd = breakout_down(low, window)
    is_bo = (bu | bd).astype(bool)
    body_rel = bar_body_relative(open_, close, high, low)
    return pd.Series(np.where(is_bo, body_rel, 0), index=close.index)


def breakout_close_pos(open_: pd.Series, close: pd.Series,
                       high: pd.Series, low: pd.Series,
                       window: int = 20) -> pd.Series:
    """
    突破K线的收盘位置
    Brooks: 向上突破收在高位 (>0.8) = 强突破
            向上突破收在低位 (<0.4) = 可能是假突破
    仅在突破K线上有值，非突破K线为 0.5（中性）
    """
    bu = breakout_up(high, window)
    bd = breakout_down(low, window)
    is_bo = (bu | bd).astype(bool)
    pos = close_position(open_, high, low, close)
    return pd.Series(np.where(is_bo, pos, 0.5), index=close.index)


def breakout_volume_ratio(volume: pd.Series, high: pd.Series,
                          low: pd.Series, window: int = 20,
                          vol_window: int = 20) -> pd.Series:
    """
    突破K线的量比（当日成交量 / 均量）
    Brooks: 放量突破更可靠（但不是必要条件）
    仅在突破K线上有值，非突破K线为 1（中性）
    """
    bu = breakout_up(high, window)
    bd = breakout_down(low, window)
    is_bo = (bu | bd).astype(bool)
    vol_ma = volume.rolling(vol_window).mean().replace(0, np.nan)
    vol_ratio = volume / vol_ma
    return pd.Series(np.where(is_bo, vol_ratio, 1.0), index=volume.index)


def breakout_follow_consistency(close: pd.Series, open_: pd.Series,
                                high: pd.Series, low: pd.Series,
                                window: int = 20,
                                follow_bars: int = 3) -> pd.Series:
    """
    突破后 N 根K线的方向一致性 [0, 1]
    Brooks: 好突破后连续同方向K线确认有效性
    1=全部同方向, 0=完全混乱
    """
    bu = breakout_up(high, window)
    bd = breakout_down(low, window)
    direction = np.sign(close - open_).fillna(0)

    result = pd.Series(np.nan, index=close.index)
    for i in range(follow_bars, len(close)):
        if bu.iloc[i - follow_bars] == 1:
            follow_dirs = direction.iloc[i - follow_bars + 1:i + 1]
            result.iloc[i] = (follow_dirs > 0).sum() / follow_bars
        elif bd.iloc[i - follow_bars] == 1:
            follow_dirs = direction.iloc[i - follow_bars + 1:i + 1]
            result.iloc[i] = (follow_dirs < 0).sum() / follow_bars

    return result


def pre_breakout_range_bars(high: pd.Series, low: pd.Series,
                            window: int = 20) -> pd.Series:
    """
    突破前区间已持续的K线数量（区间成熟度）
    Brooks: 区间持续越久，突破后运动越大（越多止损单堆积）
    算法: 统计价格在 N 日 high/low 范围内持续的 bar 数
    """
    hh = high.rolling(window).max()
    ll = low.rolling(window).min()
    mid_range = (hh + ll) / 2
    range_width = (hh - ll).replace(0, np.nan)

    # 价格在区间中部60%的比例
    in_range = ((high < hh - 0.2 * range_width) &
                (low > ll + 0.2 * range_width)).astype(int)
    # 连续在区间内的K线数
    groups = (in_range != in_range.shift(1)).cumsum()
    return in_range.groupby(groups).cumsum()


def pre_breakout_tightness(high: pd.Series, low: pd.Series,
                           open_: pd.Series, close: pd.Series,
                           window: int = 10) -> pd.Series:
    """
    突破前区间的紧密度 [0, 1]
    综合: inside bar 比例 + 小K线比例 + 高重叠比例
    Brooks: 紧密区间(tight trading range)后的突破更有力
    """
    ib_pct = inside_bar_ratio(high, low, window)
    small_bar = (bar_body_relative(open_, close, high, low) < 0.4).astype(int)
    small_pct = small_bar.rolling(window).mean()
    overlap_pct = overlap_ratio(high, low, window)
    return ((ib_pct + small_pct + overlap_pct) / 3).clip(0, 1)


# ====================================================================
# 十三、关键价位与空间 (Key Levels & Space)
# ====================================================================

def space_to_prev_high(close: pd.Series, high: pd.Series,
                       window: int = 20) -> pd.Series:
    """
    距前高的空间 (%)
    正=在前高之下(有上方阻力), 负=已突破前高
    Brooks: 空间决定了 RR，空间不够不值得交易
    """
    prev_max = high.shift(1).rolling(window - 1).max()
    return (prev_max - close) / close.replace(0, np.nan) * 100


def space_to_prev_low(close: pd.Series, low: pd.Series,
                      window: int = 20) -> pd.Series:
    """
    距前低的空间 (%)
    正=在前低之上(有下方支撑), 负=已跌破前低
    """
    prev_min = low.shift(1).rolling(window - 1).min()
    return (close - prev_min) / close.replace(0, np.nan) * 100


def range_position_zone(close: pd.Series, high: pd.Series, low: pd.Series,
                        window: int = 20) -> pd.Series:
    """
    在区间中的位置分区
    Brooks: 在区间下1/3做多，上1/3做空，中间不交易
    返回: -1=下1/3, 0=中间1/3, 1=上1/3
    """
    hh = high.rolling(window).max()
    ll = low.rolling(window).min()
    pos = ((close - ll) / (hh - ll).replace(0, np.nan)).clip(0, 1)
    return pd.Series(
        np.where(pos < 1 / 3, -1, np.where(pos > 2 / 3, 1, 0)),
        index=close.index,
    )


def space_ratio_up_down(close: pd.Series, high: pd.Series, low: pd.Series,
                        window: int = 20) -> pd.Series:
    """
    上方空间 / 下方空间
    Brooks: >1 = 上方空间更大 = 做多更有利
            <1 = 下方空间更大 = 做空更有利
    """
    hh = high.rolling(window).max()
    ll = low.rolling(window).min()
    up_space = (hh - close).clip(lower=0)
    down_space = (close - ll).clip(lower=0)
    return up_space / down_space.replace(0, np.nan)


def resistance_density(high: pd.Series, close: pd.Series,
                       window: int = 40, band_pct: float = 0.5) -> pd.Series:
    """
    上方阻力密度: 过去 N 根K线中有多少根的高点在当前价上方 band_pct% 范围内
    Brooks: 密集阻力区域 = 突破困难
    """
    result = pd.Series(0.0, index=close.index)
    upper_band = close * (1 + band_pct / 100)
    for lag in range(1, window + 1):
        shifted_high = high.shift(lag)
        in_band = ((shifted_high >= close) & (shifted_high <= upper_band)).astype(int)
        result += in_band
    return result / window


def support_density(low: pd.Series, close: pd.Series,
                    window: int = 40, band_pct: float = 0.5) -> pd.Series:
    """
    下方支撑密度: 过去 N 根K线中有多少根的低点在当前价下方 band_pct% 范围内
    Brooks: 密集支撑 = 不易跌破
    """
    result = pd.Series(0.0, index=close.index)
    lower_band = close * (1 - band_pct / 100)
    for lag in range(1, window + 1):
        shifted_low = low.shift(lag)
        in_band = ((shifted_low <= close) & (shifted_low >= lower_band)).astype(int)
        result += in_band
    return result / window


# ====================================================================
# 十四、风险回报 (Risk-Reward)
# ====================================================================

def signal_bar_risk(high: pd.Series, low: pd.Series, close: pd.Series,
                    atr_window: int = 14) -> pd.Series:
    """
    信号K线止损距离 / ATR
    Brooks: 用信号K线的极端作为止损，归一化为 ATR 单位
    <1 = 止损距离小于 ATR（风险可控）
    >1.5 = 止损距离偏大（需要更大的回报才值得）
    """
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / atr_window, adjust=False).mean().replace(0, np.nan)

    # 止损距离 = 当前K线的全幅（信号K线的极端到入场）
    risk = high - low
    return risk / atr


def expected_reward(close: pd.Series, high: pd.Series, low: pd.Series,
                    window: int = 20) -> pd.Series:
    """
    预期回报 (ATR 单位)
    用前一段的平均腿长作为预期回报
    """
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean().replace(0, np.nan)

    # 预期回报 = 最近区间宽度（可达的合理目标）
    range_width = high.rolling(window).max() - low.rolling(window).min()
    return range_width / atr


def expected_rr_ratio(close: pd.Series, high: pd.Series, low: pd.Series,
                      atr_window: int = 14, range_window: int = 20
                      ) -> pd.Series:
    """
    预期风险回报比 (RR)
    = 预期回报 / 当前K线风险
    Brooks: >2 才值得 scalp，>3 才值得 swing
    """
    risk = signal_bar_risk(high, low, close, atr_window)
    reward = expected_reward(close, high, low, range_window)
    return reward / risk.replace(0, np.nan)


def scalp_feasibility(close: pd.Series, high: pd.Series, low: pd.Series,
                      open_: pd.Series, atr_window: int = 14) -> pd.Series:
    """
    Brooks Scalp 可行性评分 [0, 100]
    Scalp = 最小利润目标(约1 ATR)，要求高概率
    评分: ATR空间 + 趋势方向明确 + 回调浅 + 止损小
    """
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / atr_window, adjust=False).mean().replace(0, np.nan)

    # 止损合理 (K线幅度 < 1.2 ATR)
    risk_ok = ((high - low) / atr < 1.2).astype(float) * 25

    # 有趋势方向
    ema_val = close.ewm(span=20, adjust=False).mean()
    ema_dir = np.sign(ema_val.diff(5))
    trend_clear = (ema_dir != 0).astype(float) * 25

    # 不在区间中间 (close_position 不在 0.35-0.65)
    pos = close_position(open_, high, low, close)
    not_middle = ((pos < 0.35) | (pos > 0.65)).astype(float) * 25

    # 有空间 (距前高/前低 > 1 ATR)
    space_up = high.rolling(20).max() - close
    space_down = close - low.rolling(20).min()
    has_space = (pd.concat([space_up, space_down], axis=1).max(axis=1) / atr > 1
                 ).astype(float) * 25

    return (risk_ok + trend_clear + not_middle + has_space).clip(0, 100)


def swing_feasibility(close: pd.Series, high: pd.Series, low: pd.Series,
                      open_: pd.Series, atr_window: int = 14) -> pd.Series:
    """
    Brooks Swing Trade 可行性评分 [0, 100]
    Swing = 更大利润(2-4 ATR)，可以接受稍低概率
    评分: 大区间 + 明确趋势 + 有足够空间 + 合理RR
    """
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / atr_window, adjust=False).mean().replace(0, np.nan)

    # 区间足够大 (> 3 ATR)
    range_width = high.rolling(40).max() - low.rolling(40).min()
    big_range = (range_width / atr > 3).astype(float) * 25

    # 明确趋势
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema60 = close.ewm(span=60, adjust=False).mean()
    trend = (np.sign(ema20 - ema60) != 0).astype(float) * 25

    # RR > 2
    rr = expected_rr_ratio(close, high, low, atr_window, 40)
    good_rr = (rr > 2).astype(float) * 25

    # 在区间边缘
    hh = high.rolling(40).max()
    ll = low.rolling(40).min()
    pos_in_range = (close - ll) / (hh - ll).replace(0, np.nan)
    at_edge = ((pos_in_range < 0.3) | (pos_in_range > 0.7)).astype(float) * 25

    return (big_range + trend + good_rr + at_edge).clip(0, 100)


# ====================================================================
# 十五、多头/空头力量平衡 (Bull vs Bear Pressure)
# ====================================================================

def close_position_bias(open_: pd.Series, high: pd.Series, low: pd.Series,
                        close: pd.Series, window: int = 10) -> pd.Series:
    """
    N 根K线收盘位置的平均偏向
    Brooks: 持续 >0.6 = 多头控盘，持续 <0.4 = 空头控盘
    """
    pos = close_position(open_, high, low, close)
    return pos.rolling(window).mean()


def shadow_pressure(open_: pd.Series, high: pd.Series, low: pd.Series,
                    close: pd.Series, window: int = 10) -> pd.Series:
    """
    上下影线压力比: N 根K线的下影总长 / 上影总长
    >1 = 下方买盘强（看涨压力）, <1 = 上方卖盘强（看跌压力）
    Brooks: 影线反映被拒绝的价格，影线方向相反的力量更强
    """
    upper_body = pd.concat([open_, close], axis=1).max(axis=1)
    lower_body = pd.concat([open_, close], axis=1).min(axis=1)
    upper_shadow = high - upper_body
    lower_shadow = lower_body - low

    upper_sum = upper_shadow.rolling(window).sum().replace(0, np.nan)
    lower_sum = lower_shadow.rolling(window).sum()
    return lower_sum / upper_sum


def buy_climax(open_: pd.Series, high: pd.Series, low: pd.Series,
               close: pd.Series, window: int = 20) -> pd.Series:
    """
    买方 Climax 检测 (0/1)
    Brooks: 在上升趋势末端出现异常大的阳线趋势K线 + 收盘靠近最高
            = 可能是最后的疯狂买入 = 趋势即将反转
    条件: 大阳线(>1.5x均幅) + 实体>60% + 收盘>80% + 处于上升趋势
    """
    rng_rel = bar_range(high, low) / bar_range_avg(high, low, window).replace(0, np.nan)
    body_rel = bar_body_relative(open_, close, high, low)
    pos = close_position(open_, high, low, close)
    is_bull = close > open_

    return (is_bull & (rng_rel > 1.5) & (body_rel > 0.6) & (pos > 0.8)).astype(int)


def sell_climax(open_: pd.Series, high: pd.Series, low: pd.Series,
                close: pd.Series, window: int = 20) -> pd.Series:
    """
    卖方 Climax 检测 (0/1)
    Brooks: 在下降趋势末端出现异常大的阴线趋势K线
            = 恐慌抛售 = 趋势即将反转
    """
    rng_rel = bar_range(high, low) / bar_range_avg(high, low, window).replace(0, np.nan)
    body_rel = bar_body_relative(open_, close, high, low)
    pos = close_position(open_, high, low, close)
    is_bear = close < open_

    return (is_bear & (rng_rel > 1.5) & (body_rel > 0.6) & (pos < 0.2)).astype(int)


def momentum_decay(close: pd.Series, open_: pd.Series,
                   window: int = 10) -> pd.Series:
    """
    连续方向动量衰减
    比较最近腿内每根K线的实体是否越来越小
    Brooks: 实体递减 = 趋势即将结束
    返回: 正=实体递减(动量衰减), 负=实体递增(动量增强)
    """
    body = bar_body_abs(open_, close)
    # 用实体的线性回归斜率来判断
    x = np.arange(window, dtype=float)
    x_mean = x.mean()
    x_var = ((x - x_mean) ** 2).sum()

    def _slope(y: np.ndarray) -> float:
        if x_var == 0:
            return 0.0
        y_mean = y.mean()
        return np.sum((x - x_mean) * (y - y_mean)) / x_var

    body_slope = body.rolling(window).apply(_slope, raw=True)
    # 标准化
    body_avg = body.rolling(window).mean().replace(0, np.nan)
    return -body_slope / body_avg  # 正=衰减, 负=增强


def exhaustion_gap(open_: pd.Series, high: pd.Series, low: pd.Series,
                   close: pd.Series, window: int = 20) -> pd.Series:
    """
    衰竭缺口检测
    Brooks: 趋势末端的缺口 + 当天反转收盘 = 衰竭缺口 = 强反转信号
    返回: 1=向上衰竭缺口(看跌), -1=向下衰竭缺口(看涨), 0=无
    """
    prev_high = high.shift(1)
    prev_low = low.shift(1)

    # 向上缺口 + 收阴线(收盘在下半部)
    gap_up = low > prev_high
    bear_close = (close < open_) & (close_position(open_, high, low, close) < 0.4)
    # 要在上升趋势中
    ema = close.ewm(span=window, adjust=False).mean()
    uptrend = close.shift(1) > ema.shift(1)

    # 向下缺口 + 收阳线(收盘在上半部)
    gap_down = high < prev_low
    bull_close = (close > open_) & (close_position(open_, high, low, close) > 0.6)
    downtrend = close.shift(1) < ema.shift(1)

    up_exhaustion = (gap_up & bear_close & uptrend).astype(int)
    down_exhaustion = (gap_down & bull_close & downtrend).astype(int)
    return up_exhaustion - down_exhaustion


def trend_bar_streak_strength(open_: pd.Series, close: pd.Series,
                              high: pd.Series, low: pd.Series) -> pd.Series:
    """
    连续趋势K线的累计强度
    Brooks: 连续出现趋势K线且实体不衰减 = 趋势非常强 = Always In 状态
    """
    tb = is_trend_bar(open_, close, high, low)
    body = bar_body_abs(open_, close).fillna(0)
    direction = np.sign(close - open_).fillna(0)

    result = pd.Series(0.0, index=close.index)
    for i in range(1, len(tb)):
        body_val = float(body.iloc[i])
        if int(tb.iloc[i]) != 0 and direction.iloc[i] == direction.iloc[i - 1]:
            result.iloc[i] = result.iloc[i - 1] + body_val
        else:
            result.iloc[i] = body_val if int(tb.iloc[i]) != 0 else 0.0

    # 标准化为 ATR 单位
    avg_rng = bar_range_avg(high, low, 20).replace(0, np.nan)
    return result / avg_rng


def always_in_direction(open_: pd.Series, close: pd.Series,
                        high: pd.Series, low: pd.Series,
                        window: int = 20) -> pd.Series:
    """
    Brooks "Always In" 方向判断
    Brooks: 如果必须持仓，你会持有哪个方向？
    综合: EMA方向 + 趋势K线净占比 + 收盘位置偏向 + HH/HL vs LH/LL

    返回: [-1, 1] 连续值
        >0.5 = Always In Long, <-0.5 = Always In Short
        -0.5 ~ 0.5 = 不明确（交易区间）
    """
    # EMA 方向
    ema = close.ewm(span=window, adjust=False).mean()
    ema_dir = np.sign(ema.diff(3))

    # 趋势K线净占比
    tb = is_trend_bar(open_, close, high, low)
    bull_pct = (tb == 1).astype(int).rolling(window).mean()
    bear_pct = (tb == -1).astype(int).rolling(window).mean()
    tb_net = bull_pct - bear_pct

    # 收盘位置偏向
    pos = close_position(open_, high, low, close)
    pos_bias = pos.rolling(window).mean() - 0.5

    # 综合
    score = (ema_dir * 0.3 + tb_net * 0.3 + pos_bias * 0.4)
    return score.clip(-1, 1)


# ====================================================================
# 批量计算
# ====================================================================

def compute_price_action_context_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    为单品种批量计算 Al Brooks 交易上下文特征
    输入 df 需包含: open, high, low, close, volume(可选)
    """
    o = df["open"]
    h = df["high"]
    l = df["low"]   # noqa: E741
    c = df["close"]
    v = df["volume"] if "volume" in df.columns else pd.Series(0, index=df.index)
    result = pd.DataFrame(index=df.index)

    # ---- 十、市场结构 ----
    for w in [10, 20]:
        result[f"pa_range_over_atr_{w}"] = range_over_atr(h, l, c, w)
    for w in [10, 20]:
        result[f"pa_inside_bar_ratio_{w}"] = inside_bar_ratio(h, l, w)
        result[f"pa_overlap_ratio_{w}"] = overlap_ratio(h, l, w)
    tb_df = trend_bar_ratio(o, c, h, l, 20)
    result["pa_trend_bar_bull_pct"] = tb_df["trend_bar_bull_pct"]
    result["pa_trend_bar_bear_pct"] = tb_df["trend_bar_bear_pct"]
    result["pa_trend_bar_net"] = tb_df["trend_bar_net"]
    for w in [5, 10]:
        result[f"pa_doji_density_{w}"] = doji_density(o, c, h, l, w)
    result["pa_two_leg_pullback"] = two_legged_pullback(c, o, h, l)
    for span in [20, 60]:
        result[f"pa_ema_slope_{span}"] = ema_slope(c, span)
    result["pa_ema_slope_accel"] = ema_slope_acceleration(c, 20)

    # ---- 十一、回撤质量 ----
    result["pa_prior_leg_slope"] = prior_leg_slope(c, o, h, l)
    result["pa_pullback_retrace"] = pullback_retrace_ratio(c, o, h, l)
    for w in [3, 5]:
        result[f"pa_pullback_tb_quality_{w}"] = pullback_trend_bar_quality(o, c, h, l, w)
    result["pa_pullback_bar_ratio"] = pullback_bar_count_ratio(c, o)
    for span in [20, 60]:
        result[f"pa_dist_to_ema_{span}"] = dist_to_ema(c, span)
    result["pa_pullback_to_ema_20"] = pullback_to_ema(c, 20)

    # ---- 十二、突破质量 ----
    for w in [10, 20]:
        result[f"pa_bo_body_ratio_{w}"] = breakout_body_ratio(o, c, h, l, w)
        result[f"pa_bo_close_pos_{w}"] = breakout_close_pos(o, c, h, l, w)
    result["pa_bo_vol_ratio_20"] = breakout_volume_ratio(v, h, l, 20)
    result["pa_bo_follow_consistency"] = breakout_follow_consistency(c, o, h, l, 20, 3)
    result["pa_pre_bo_bars"] = pre_breakout_range_bars(h, l, 20)
    for w in [5, 10]:
        result[f"pa_pre_bo_tightness_{w}"] = pre_breakout_tightness(h, l, o, c, w)

    # ---- 十三、关键价位与空间 ----
    for w in [20, 60]:
        result[f"pa_space_to_high_{w}"] = space_to_prev_high(c, h, w)
        result[f"pa_space_to_low_{w}"] = space_to_prev_low(c, l, w)
    result["pa_range_zone_20"] = range_position_zone(c, h, l, 20)
    result["pa_space_ratio_ud"] = space_ratio_up_down(c, h, l, 20)
    result["pa_resistance_density"] = resistance_density(h, c, 40)
    result["pa_support_density"] = support_density(l, c, 40)

    # ---- 十四、风险回报 ----
    result["pa_signal_risk"] = signal_bar_risk(h, l, c)
    result["pa_expected_reward"] = expected_reward(c, h, l, 20)
    result["pa_expected_rr"] = expected_rr_ratio(c, h, l, 14, 20)
    result["pa_scalp_score"] = scalp_feasibility(c, h, l, o)
    result["pa_swing_score"] = swing_feasibility(c, h, l, o)

    # ---- 十五、力量平衡 ----
    for w in [5, 10, 20]:
        result[f"pa_close_pos_bias_{w}"] = close_position_bias(o, h, l, c, w)
    for w in [5, 10]:
        result[f"pa_shadow_pressure_{w}"] = shadow_pressure(o, h, l, c, w)
    result["pa_buy_climax"] = buy_climax(o, h, l, c)
    result["pa_sell_climax"] = sell_climax(o, h, l, c)
    result["pa_momentum_decay"] = momentum_decay(c, o)
    result["pa_exhaustion_gap"] = exhaustion_gap(o, h, l, c)
    result["pa_tb_streak_strength"] = trend_bar_streak_strength(o, c, h, l)
    result["pa_always_in_dir"] = always_in_direction(o, c, h, l)

    return result
