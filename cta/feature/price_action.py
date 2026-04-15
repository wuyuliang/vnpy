"""
价格行为 (Al Brooks Price Action) 特征

Al Brooks 将价格行为分析系统化为以下核心概念，本模块逐一量化：

一、K线分类 (Bar Classification)
    - 趋势K线 / 十字星 / 反转K线
    - K线相对大小
    - 收盘位置

二、信号K线 (Signal Bars)
    - 买入/卖出信号K线
    - 反转K线强度评分

三、K线关系 (Bar Context)
    - 内包线 / 外包线 / ii模式 / ioi模式
    - K线重叠度
    - 缺口K线

四、趋势结构 (Trend Structure)
    - Higher High / Higher Low 序列
    - Swing Point 识别
    - 趋势腿 (Leg) 计数与长度
    - 回撤深度 (Pullback Depth)

五、通道与交易区间 (Channel & Trading Range)
    - 微通道 (Micro Channel)
    - 紧密通道 (Tight Channel)
    - 宽通道 / 交易区间检测
    - Barb Wire (密集震荡)

六、突破 (Breakout)
    - 突破K线
    - 突破后跟随 (Follow Through)
    - 突破失败
    - 突破回测

七、测量移动与对称 (Measured Move)
    - 等距移动
    - 腿长比较

八、双顶双底与楔形 (Double Top/Bottom & Wedge)
    - 双顶 / 双底
    - 三推楔形
"""
import numpy as np
import pandas as pd


# ====================================================================
# 一、K线分类 (Bar Classification)
# ====================================================================

def bar_range(high: pd.Series, low: pd.Series) -> pd.Series:
    """K线全幅 (High - Low)"""
    return high - low


def bar_body(open_: pd.Series, close: pd.Series) -> pd.Series:
    """K线实体（有符号：正=阳线, 负=阴线）"""
    return close - open_


def bar_body_abs(open_: pd.Series, close: pd.Series) -> pd.Series:
    """K线实体绝对值"""
    return (close - open_).abs()


def bar_body_mid(open_: pd.Series, close: pd.Series) -> pd.Series:
    """K线实体中点"""
    return (open_ + close) / 2


def bar_mid(high: pd.Series, low: pd.Series) -> pd.Series:
    """K线全幅中点"""
    return (high + low) / 2


def bar_range_avg(high: pd.Series, low: pd.Series, window: int = 20
                  ) -> pd.Series:
    """近 N 根K线的平均全幅"""
    return bar_range(high, low).rolling(window).mean()


def bar_range_relative(high: pd.Series, low: pd.Series, window: int = 20
                       ) -> pd.Series:
    """当前K线全幅 / 近 N 根平均全幅（>1 为大K线, <1 为小K线）"""
    rng = bar_range(high, low)
    avg = rng.rolling(window).mean().replace(0, np.nan)
    return rng / avg


def bar_body_relative(open_: pd.Series, close: pd.Series, high: pd.Series,
                      low: pd.Series) -> pd.Series:
    """实体占全幅比例（Brooks: >0.5 为趋势K线, <0.25 为十字星）"""
    body = bar_body_abs(open_, close)
    rng = bar_range(high, low).replace(0, np.nan)
    return (body / rng).fillna(0.0)


def close_position(open_: pd.Series, high: pd.Series, low: pd.Series,
                   close: pd.Series) -> pd.Series:
    """
    收盘价在K线中的位置 [0, 1]
    0=收在最低, 1=收在最高
    Brooks: 趋势K线收盘靠近极端, 十字星收盘靠近中间
    """
    rng = (high - low).replace(0, np.nan)
    return ((close - low) / rng).fillna(0.5)


def is_trend_bar(open_: pd.Series, close: pd.Series, high: pd.Series,
                 low: pd.Series, body_threshold: float = 0.5) -> pd.Series:
    """
    趋势K线: 实体占全幅 > body_threshold
    返回: 1=阳线趋势K, -1=阴线趋势K, 0=非趋势K
    """
    ratio = bar_body_relative(open_, close, high, low)
    direction = np.sign(close - open_).fillna(0)
    return pd.Series(
        np.where(ratio > body_threshold, direction, 0),
        index=open_.index,
    )


def is_doji(open_: pd.Series, close: pd.Series, high: pd.Series,
            low: pd.Series, threshold: float = 0.25) -> pd.Series:
    """十字星: 实体占全幅 < threshold (0/1)"""
    ratio = bar_body_relative(open_, close, high, low)
    return (ratio < threshold).astype(int)


def is_shaved_top(high: pd.Series, close: pd.Series, open_: pd.Series,
                  low: pd.Series = None, tolerance: float = 0.05) -> pd.Series:
    """光头K线 (无上影线或极短): 上影占全幅 < tolerance"""
    upper_body = pd.concat([open_, close], axis=1).max(axis=1)
    total_rng = (high - low).replace(0, np.nan) if low is not None else \
                (high - pd.concat([open_, close], axis=1).min(axis=1)).replace(0, np.nan)
    upper_shadow_pct = (high - upper_body) / total_rng
    return (upper_shadow_pct < tolerance).astype(int)


def is_shaved_bottom(low: pd.Series, close: pd.Series, open_: pd.Series,
                     high: pd.Series = None, tolerance: float = 0.05) -> pd.Series:
    """光脚K线 (无下影线或极短)"""
    lower_body = pd.concat([open_, close], axis=1).min(axis=1)
    total_rng = (high - low).replace(0, np.nan) if high is not None else \
                (pd.concat([open_, close], axis=1).max(axis=1) - low).replace(0, np.nan)
    lower_shadow_pct = (lower_body - low) / total_rng
    return (lower_shadow_pct < tolerance).astype(int)


# ====================================================================
# 二、信号K线 (Signal Bars)
# ====================================================================

def bull_reversal_bar(open_: pd.Series, high: pd.Series, low: pd.Series,
                      close: pd.Series) -> pd.Series:
    """
    看涨反转K线:
    - 阳线 (close > open)
    - 收盘在K线上半部 (close_position > 0.5)
    - 下影线明显 (lower_shadow > body)
    返回: 0/1
    """
    is_bull = close > open_
    pos = close_position(open_, high, low, close)
    lower_body = pd.concat([open_, close], axis=1).min(axis=1)
    lower_shadow = lower_body - low
    body = bar_body_abs(open_, close)
    return (is_bull & (pos > 0.5) & (lower_shadow > body)).astype(int)


def bear_reversal_bar(open_: pd.Series, high: pd.Series, low: pd.Series,
                      close: pd.Series) -> pd.Series:
    """
    看跌反转K线:
    - 阴线 (close < open)
    - 收盘在K线下半部 (close_position < 0.5)
    - 上影线明显 (upper_shadow > body)
    返回: 0/1
    """
    is_bear = close < open_
    pos = close_position(open_, high, low, close)
    upper_body = pd.concat([open_, close], axis=1).max(axis=1)
    upper_shadow = high - upper_body
    body = bar_body_abs(open_, close)
    return (is_bear & (pos < 0.5) & (upper_shadow > body)).astype(int)


def signal_bar_strength(open_: pd.Series, high: pd.Series, low: pd.Series,
                        close: pd.Series, window: int = 20) -> pd.Series:
    """
    信号K线强度评分 [-5, +5]
    正分=看涨信号, 负分=看跌信号
    评分维度:
        +1/-1 阳线/阴线
        +1/-1 收盘位置 > 0.6 / < 0.4
        +1/-1 K线全幅 > 平均 (大K线)
        +1/-1 影线方向匹配 (下影长=看涨, 上影长=看跌)
        +1/-1 趋势K线 (实体 > 50%)
    """
    direction = np.sign(close - open_)
    pos = close_position(open_, high, low, close)
    rng_rel = bar_range_relative(high, low, window)
    body_rel = bar_body_relative(open_, close, high, low)

    upper_body = pd.concat([open_, close], axis=1).max(axis=1)
    lower_body = pd.concat([open_, close], axis=1).min(axis=1)
    upper_shadow = high - upper_body
    lower_shadow = lower_body - low
    rng = bar_range(high, low).replace(0, np.nan)
    shadow_bias = ((lower_shadow - upper_shadow) / rng).fillna(0)  # 正=下影长

    score = pd.Series(0.0, index=open_.index)
    score += direction  # 方向分
    score += np.where(pos > 0.6, 1, np.where(pos < 0.4, -1, 0))  # 收盘位置
    score += np.where(rng_rel > 1, direction, 0)  # 大K线
    score += np.where(shadow_bias > 0.2, 1, np.where(shadow_bias < -0.2, -1, 0))
    score += np.where(body_rel > 0.5, direction, 0)  # 趋势K线

    return score


def consecutive_bull_bars(open_: pd.Series, close: pd.Series) -> pd.Series:
    """连续阳线根数"""
    is_bull = (close > open_).astype(int)
    groups = (is_bull != is_bull.shift()).cumsum()
    counts = is_bull.groupby(groups).cumsum()
    return counts


def consecutive_bear_bars(open_: pd.Series, close: pd.Series) -> pd.Series:
    """连续阴线根数"""
    is_bear = (close < open_).astype(int)
    groups = (is_bear != is_bear.shift()).cumsum()
    counts = is_bear.groupby(groups).cumsum()
    return counts


# ====================================================================
# 三、K线关系 (Bar Context)
# ====================================================================

def inside_bar(high: pd.Series, low: pd.Series) -> pd.Series:
    """内包线 (0/1)"""
    return ((high <= high.shift(1)) & (low >= low.shift(1))).astype(int)


def outside_bar(high: pd.Series, low: pd.Series) -> pd.Series:
    """外包线 (0/1)"""
    return ((high >= high.shift(1)) & (low <= low.shift(1))).astype(int)


def ii_pattern(high: pd.Series, low: pd.Series) -> pd.Series:
    """ii模式: 连续两根内包线 (0/1)"""
    ib = inside_bar(high, low).astype(bool)
    return (ib & ib.shift(1).fillna(False)).astype(int)


def ioi_pattern(high: pd.Series, low: pd.Series) -> pd.Series:
    """ioi模式: 内包-外包-内包 (0/1)"""
    ib = inside_bar(high, low).astype(bool)
    ob = outside_bar(high, low).astype(bool)
    return (ib & ob.shift(1).fillna(False) & ib.shift(2).fillna(False)).astype(int)


def bar_overlap(high: pd.Series, low: pd.Series) -> pd.Series:
    """
    相邻K线重叠度 [0, 1]
    1=完全重叠, 0=无重叠（有缺口）
    Brooks: 高重叠 = 交易区间, 低重叠 = 趋势
    """
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    overlap_high = pd.concat([high, prev_high], axis=1).min(axis=1)
    overlap_low = pd.concat([low, prev_low], axis=1).max(axis=1)
    overlap = (overlap_high - overlap_low).clip(lower=0)
    total_range = pd.concat([high, prev_high], axis=1).max(axis=1) - \
                  pd.concat([low, prev_low], axis=1).min(axis=1)
    return overlap / total_range.replace(0, np.nan)


def bar_overlap_avg(high: pd.Series, low: pd.Series, window: int = 5
                    ) -> pd.Series:
    """滚动平均K线重叠度"""
    return bar_overlap(high, low).rolling(window).mean()


def gap_bar(open_: pd.Series, high: pd.Series, low: pd.Series,
            close: pd.Series) -> pd.Series:
    """
    缺口K线方向
    +1=向上缺口（今low > 昨high）
    -1=向下缺口（今high < 昨low）
    0=无缺口
    """
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    gap_up = (low > prev_high).astype(int)
    gap_down = -(high < prev_low).astype(int)
    return gap_up + gap_down


def gap_bar_size(open_: pd.Series, high: pd.Series, low: pd.Series,
                 close: pd.Series) -> pd.Series:
    """缺口大小（百分比），正=向上，负=向下，0=无缺口"""
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    gap_up_size = np.where(low > prev_high, (low - prev_high) / prev_close * 100, 0)
    gap_down_size = np.where(high < prev_low, (high - prev_low) / prev_close * 100, 0)
    return pd.Series(gap_up_size + gap_down_size, index=open_.index)


# ====================================================================
# 四、趋势结构 (Trend Structure)
# ====================================================================

def higher_high(high: pd.Series) -> pd.Series:
    """Higher High: 当前高点 > 前一高点 (0/1)"""
    return (high > high.shift(1)).astype(int)


def lower_low(low: pd.Series) -> pd.Series:
    """Lower Low: 当前低点 < 前一低点 (0/1)"""
    return (low < low.shift(1)).astype(int)


def higher_low(low: pd.Series) -> pd.Series:
    """Higher Low: 当前低点 > 前一低点 (0/1)"""
    return (low > low.shift(1)).astype(int)


def lower_high(high: pd.Series) -> pd.Series:
    """Lower High: 当前高点 < 前一高点 (0/1)"""
    return (high < high.shift(1)).astype(int)


def hh_hl_count(high: pd.Series, low: pd.Series, window: int = 10
                ) -> pd.DataFrame:
    """
    N 根K线内 HH+HL 的计数（多头强度）和 LH+LL 的计数（空头强度）
    Brooks: 趋势由连续的 HH+HL 或 LH+LL 定义
    """
    hh = higher_high(high)
    hl = higher_low(low)
    lh = lower_high(high)
    ll = lower_low(low)
    return pd.DataFrame({
        "hh_hl_count": (hh + hl).rolling(window).sum(),
        "lh_ll_count": (lh + ll).rolling(window).sum(),
    })


def swing_high(high: pd.Series, left: int = 2, right: int = 2) -> pd.Series:
    """
    Swing High 识别
    当前K线高点 >= 左右各 N 根K线的高点
    返回: 高点价格，非 Swing 点为 NaN
    """
    result = pd.Series(np.nan, index=high.index)
    for i in range(left, len(high) - right):
        is_swing = True
        for j in range(1, left + 1):
            if high.iloc[i] < high.iloc[i - j]:
                is_swing = False
                break
        if is_swing:
            for j in range(1, right + 1):
                if high.iloc[i] < high.iloc[i + j]:
                    is_swing = False
                    break
        if is_swing:
            result.iloc[i] = high.iloc[i]
    return result


def swing_low(low: pd.Series, left: int = 2, right: int = 2) -> pd.Series:
    """
    Swing Low 识别
    当前K线低点 <= 左右各 N 根K线的低点
    返回: 低点价格，非 Swing 点为 NaN
    """
    result = pd.Series(np.nan, index=low.index)
    for i in range(left, len(low) - right):
        is_swing = True
        for j in range(1, left + 1):
            if low.iloc[i] > low.iloc[i - j]:
                is_swing = False
                break
        if is_swing:
            for j in range(1, right + 1):
                if low.iloc[i] > low.iloc[i + j]:
                    is_swing = False
                    break
        if is_swing:
            result.iloc[i] = low.iloc[i]
    return result


def dist_to_last_swing_high(high: pd.Series, close: pd.Series,
                            left: int = 2, right: int = 2) -> pd.Series:
    """当前收盘价距离最近 Swing High 的百分比距离"""
    sh = swing_high(high, left, right)
    sh_ffill = sh.ffill()
    return (close - sh_ffill) / sh_ffill * 100


def dist_to_last_swing_low(low: pd.Series, close: pd.Series,
                           left: int = 2, right: int = 2) -> pd.Series:
    """当前收盘价距离最近 Swing Low 的百分比距离"""
    sl = swing_low(low, left, right)
    sl_ffill = sl.ffill()
    return (close - sl_ffill) / sl_ffill * 100


def pullback_depth(close: pd.Series, window: int = 20) -> pd.Series:
    """
    回撤深度: 当前价距离 N 日最高点的回撤百分比
    Brooks: 回撤到 50% 以上是趋势转弱的信号
    """
    rolling_high = close.rolling(window).max()
    rolling_low = close.rolling(window).min()
    total_range = (rolling_high - rolling_low).replace(0, np.nan)
    return (rolling_high - close) / total_range


def trend_leg_count(close: pd.Series, open_: pd.Series, window: int = 20
                    ) -> pd.DataFrame:
    """
    趋势腿计数
    连续同方向K线为一条腿，统计 N 根内上涨腿和下跌腿的数量
    """
    direction = np.sign(close - open_).fillna(0)
    leg_change = (direction != direction.shift(1)).astype(int)

    bull_legs = pd.Series(0, index=close.index)
    bear_legs = pd.Series(0, index=close.index)

    for i in range(1, len(close)):
        if leg_change.iloc[i] == 1:
            if direction.iloc[i] > 0:
                bull_legs.iloc[i] = 1
            elif direction.iloc[i] < 0:
                bear_legs.iloc[i] = 1

    return pd.DataFrame({
        "bull_legs": bull_legs.rolling(window).sum(),
        "bear_legs": bear_legs.rolling(window).sum(),
    })


def current_leg_length(close: pd.Series, open_: pd.Series) -> pd.Series:
    """当前趋势腿的长度（连续同方向K线根数）"""
    direction = np.sign(close - open_).fillna(0)
    result = pd.Series(1, index=close.index)
    for i in range(1, len(direction)):
        if direction.iloc[i] != 0 and direction.iloc[i] == direction.iloc[i - 1]:
            result.iloc[i] = result.iloc[i - 1] + 1
        else:
            result.iloc[i] = 1
    return result


def current_leg_range(close: pd.Series, open_: pd.Series, high: pd.Series,
                      low: pd.Series) -> pd.Series:
    """当前趋势腿的累计幅度（从腿起点到当前）"""
    direction = np.sign(close - open_).fillna(0)
    result = pd.Series(0.0, index=close.index)
    leg_start_high = high.iloc[0]
    leg_start_low = low.iloc[0]

    for i in range(1, len(direction)):
        if direction.iloc[i] != 0 and direction.iloc[i] == direction.iloc[i - 1]:
            if direction.iloc[i] > 0:
                result.iloc[i] = high.iloc[i] - leg_start_low
            else:
                result.iloc[i] = leg_start_high - low.iloc[i]
        else:
            leg_start_high = high.iloc[i]
            leg_start_low = low.iloc[i]
            result.iloc[i] = high.iloc[i] - low.iloc[i]
    return result


# ====================================================================
# 五、通道与交易区间 (Channel & Trading Range)
# ====================================================================

def micro_channel(close: pd.Series, open_: pd.Series,
                  min_bars: int = 3) -> pd.Series:
    """
    微通道检测: 连续 N 根同方向趋势K线且无反向K线
    Brooks: 微通道是强趋势信号，十字星(direction=0)不中断通道
    返回: 1=多头微通道, -1=空头微通道, 0=无
    """
    direction = np.sign(close - open_).fillna(0)
    result = pd.Series(0, index=close.index)
    count = 1
    last_dir = 0
    for i in range(1, len(direction)):
        d = int(direction.iloc[i])
        if d == 0:
            # 十字星不中断也不增加计数
            pass
        elif d == last_dir:
            count += 1
        elif last_dir == 0:
            # 从无方向开始
            last_dir = d
            count = 1
        else:
            # 方向反转，重置
            last_dir = d
            count = 1
        if count >= min_bars and last_dir != 0:
            result.iloc[i] = last_dir
    return result


def tight_channel_score(high: pd.Series, low: pd.Series, close: pd.Series,
                        window: int = 10) -> pd.Series:
    """
    紧密通道评分 [0, 1]
    基于:
        - K线重叠度高
        - 振幅相对较小
        - 方向一致性
    Brooks: 紧密通道中不应逆势交易
    """
    overlap = bar_overlap_avg(high, low, window)
    rng = bar_range(high, low)
    rng_std = rng.rolling(window).std() / rng.rolling(window).mean()
    # 标准差越小=越紧密
    tightness = 1 - rng_std.clip(0, 1)
    return (overlap * 0.5 + tightness * 0.5).clip(0, 1)


def trading_range_score(high: pd.Series, low: pd.Series, close: pd.Series,
                        window: int = 20) -> pd.Series:
    """
    交易区间评分 [0, 1]
    基于:
        - 价格在窄幅内波动（高低点差/均价 较小）
        - K线方向交替频繁
    Brooks: 交易区间中 80% 的突破会失败
    """
    hh = high.rolling(window).max()
    ll = low.rolling(window).min()
    mid = (hh + ll) / 2
    range_pct = (hh - ll) / mid

    # 方向交替频率
    direction = np.sign(close.diff())
    dir_change = (direction != direction.shift(1)).astype(int)
    alternation = dir_change.rolling(window).mean()

    # 区间越窄 + 交替越频繁 = 越像交易区间
    narrowness = 1 - range_pct.rank(pct=True)
    return (narrowness * 0.5 + alternation * 0.5).clip(0, 1)


def barb_wire(high: pd.Series, low: pd.Series, open_: pd.Series,
              close: pd.Series, window: int = 5) -> pd.Series:
    """
    Barb Wire 检测 (0/1)
    Brooks: 3根或以上连续的小K线(十字星)且有大量重叠
    """
    is_small = bar_body_relative(open_, close, high, low) < 0.35
    overlap = bar_overlap(high, low) > 0.5
    barb = (is_small & overlap).astype(int)
    return (barb.rolling(window).sum() >= 3).astype(int)


# ====================================================================
# 六、突破 (Breakout)
# ====================================================================

def breakout_up(high: pd.Series, window: int = 20) -> pd.Series:
    """向上突破: 当前高点突破 N 日最高 (0/1)"""
    prev_max = high.shift(1).rolling(window).max()
    return (high > prev_max).astype(int)


def breakout_down(low: pd.Series, window: int = 20) -> pd.Series:
    """向下突破: 当前低点突破 N 日最低 (0/1)"""
    prev_min = low.shift(1).rolling(window).min()
    return (low < prev_min).astype(int)


def breakout_strength(open_: pd.Series, high: pd.Series, low: pd.Series,
                      close: pd.Series, window: int = 20) -> pd.Series:
    """
    突破K线强度
    Brooks: 好的突破K线是大实体趋势K线，收盘在极端
    返回: [-1, 1]，正=强多突破，负=强空突破
    """
    bu = breakout_up(high, window)
    bd = breakout_down(low, window)
    body_rel = bar_body_relative(open_, close, high, low)
    pos = close_position(open_, high, low, close)
    direction = np.sign(close - open_).fillna(0)

    strength = direction * body_rel
    # 只在突破K线上有值
    return pd.Series(
        np.where(bu | bd, strength, 0),
        index=open_.index,
    )


def follow_through(close: pd.Series, open_: pd.Series, high: pd.Series,
                   low: pd.Series, window: int = 20) -> pd.Series:
    """
    突破后跟随: 突破后下一根K线是否同方向
    Brooks: Follow-through 确认突破有效性
    返回: 1=多头跟随, -1=空头跟随, 0=无
    """
    bu = breakout_up(high, window)
    bd = breakout_down(low, window)
    next_dir = np.sign(close - open_).fillna(0)

    ft = pd.Series(0, index=close.index)
    bu_prev = bu.shift(1).fillna(0)
    bd_prev = bd.shift(1).fillna(0)
    ft = np.where(bu_prev == 1, np.where(next_dir > 0, 1, -1), ft)
    ft = np.where(bd_prev == 1, np.where(next_dir < 0, -1, 1), ft)
    return pd.Series(ft, index=close.index)


def breakout_failure(high: pd.Series, low: pd.Series, close: pd.Series,
                     window: int = 20) -> pd.Series:
    """
    突破失败: 突破后回落到突破前区间内
    Brooks: 突破失败是强反转信号
    返回: 1=向上突破失败, -1=向下突破失败, 0=无
    """
    prev_max = high.shift(1).rolling(window).max()
    prev_min = low.shift(1).rolling(window).min()

    bu = breakout_up(high, window)
    bd = breakout_down(low, window)

    # 向上突破后收盘回到区间内
    bu_fail = bu.shift(1).fillna(0).astype(bool) & (close < prev_max.shift(1))
    # 向下突破后收盘回到区间内
    bd_fail = bd.shift(1).fillna(0).astype(bool) & (close > prev_min.shift(1))

    return bu_fail.astype(int) - bd_fail.astype(int)


def breakout_pullback(high: pd.Series, low: pd.Series, close: pd.Series,
                      window: int = 20, lookback: int = 5) -> pd.Series:
    """
    突破回测: 突破后 N 根K线内是否回到突破点附近
    返回: 1=向上突破后回测, -1=向下突破后回测, 0=无
    """
    prev_max = high.shift(1).rolling(window).max()
    prev_min = low.shift(1).rolling(window).min()
    bu = breakout_up(high, window)
    bd = breakout_down(low, window)

    result = pd.Series(0, index=close.index)
    for lag in range(2, lookback + 1):
        # 向上突破后回撤到突破点
        pullback_to_high = bu.shift(lag).fillna(0).astype(bool) & (low <= prev_max.shift(lag))
        pullback_to_low = bd.shift(lag).fillna(0).astype(bool) & (high >= prev_min.shift(lag))
        result = result + pullback_to_high.astype(int) - pullback_to_low.astype(int)

    return result.clip(-1, 1)


# ====================================================================
# 七、测量移动 (Measured Move)
# ====================================================================

def leg_ratio(close: pd.Series, open_: pd.Series, high: pd.Series,
              low: pd.Series, window: int = 20) -> pd.Series:
    """
    当前腿与前一腿的幅度比
    Brooks: 等距移动 (ratio ≈ 1) 是常见的价格目标
    """
    direction = np.sign(close - open_).fillna(0)
    leg_rng = current_leg_range(close, open_, high, low)

    # 找前一条腿的幅度
    leg_change = (direction != direction.shift(1)).astype(int)
    prev_leg_end = leg_rng.shift(1)

    # 简化: 用当前腿幅度 / 滚动平均腿幅度
    avg_leg = leg_rng.rolling(window).mean().replace(0, np.nan)
    return leg_rng / avg_leg


def close_to_measured_move(high: pd.Series, low: pd.Series,
                           close: pd.Series, window: int = 40) -> pd.Series:
    """
    收盘价距离等距移动目标的远近
    以 N 日内最高-最低作为区间，目标 = 区间等距延伸
    返回百分比距离
    """
    hh = high.rolling(window).max()
    ll = low.rolling(window).min()
    rng = hh - ll
    # 向上目标
    up_target = hh + rng
    # 向下目标
    down_target = ll - rng
    dist_up = (up_target - close) / close * 100
    dist_down = (close - down_target) / close * 100
    # 返回较近的那个
    return pd.concat([dist_up.abs(), dist_down.abs()], axis=1).min(axis=1)


# ====================================================================
# 八、双顶双底与楔形 (Double Top/Bottom & Wedge)
# ====================================================================

def double_top_proximity(high: pd.Series, window: int = 20,
                         tolerance: float = 0.005) -> pd.Series:
    """
    双顶接近度: 当前高点距离过去 N 日最高点的接近程度（排除当前K线）
    返回: [0, 1], 越接近1越像双顶（当前高点接近但不超过前高）
    Brooks: 双顶是常见反转形态
    """
    prev_max = high.shift(1).rolling(window - 1).max()
    diff_pct = (prev_max - high).abs() / prev_max.replace(0, np.nan)
    # 只有当前高点没有大幅超越前高时才有意义
    return (1 - diff_pct / tolerance).clip(0, 1)


def double_bottom_proximity(low: pd.Series, window: int = 20,
                            tolerance: float = 0.005) -> pd.Series:
    """
    双底接近度: 当前低点距离过去 N 日最低点的接近程度（排除当前K线）
    返回: [0, 1], 越接近1越像双底（当前低点接近但不低于前低）
    """
    prev_min = low.shift(1).rolling(window - 1).min()
    diff_pct = (low - prev_min).abs() / prev_min.replace(0, np.nan)
    return (1 - diff_pct / tolerance).clip(0, 1)


def wedge_pattern(high: pd.Series, low: pd.Series, window: int = 20
                  ) -> pd.Series:
    """
    楔形模式检测
    Brooks: 三推楔形是最可靠的反转形态之一
    通过检测高低点是否呈收敛/发散趋势
    返回: 1=上升楔形（看跌）, -1=下降楔形（看涨）, 0=无
    """
    # 高点斜率
    x = np.arange(window, dtype=float)
    x_mean = x.mean()
    x_var = ((x - x_mean) ** 2).sum()

    def _slope(y: np.ndarray) -> float:
        y_mean = y.mean()
        return np.sum((x - x_mean) * (y - y_mean)) / x_var

    high_slope = high.rolling(window).apply(_slope, raw=True)
    low_slope = low.rolling(window).apply(_slope, raw=True)

    # 上升楔形: 高点低点都上升，但高点斜率 < 低点斜率（收敛）
    rising_wedge = (high_slope > 0) & (low_slope > 0) & (high_slope < low_slope)
    # 下降楔形: 高点低点都下降，但低点斜率 > 高点斜率（收敛）
    falling_wedge = (high_slope < 0) & (low_slope < 0) & (low_slope > high_slope)

    result = pd.Series(0, index=high.index)
    result[rising_wedge] = 1
    result[falling_wedge] = -1
    return result


def channel_slope(high: pd.Series, low: pd.Series, window: int = 20
                  ) -> pd.DataFrame:
    """
    通道上下轨斜率
    Brooks: 斜率变化反映趋势强度变化
    """
    x = np.arange(window, dtype=float)
    x_mean = x.mean()
    x_var = ((x - x_mean) ** 2).sum()

    def _slope(y: np.ndarray) -> float:
        y_mean = y.mean()
        return np.sum((x - x_mean) * (y - y_mean)) / x_var

    return pd.DataFrame({
        "high_slope": high.rolling(window).apply(_slope, raw=True),
        "low_slope": low.rolling(window).apply(_slope, raw=True),
    })


def channel_width_change(high: pd.Series, low: pd.Series,
                         window: int = 20) -> pd.Series:
    """
    通道宽度变化率
    正=通道扩张 (趋势加速或波动放大)
    负=通道收缩 (趋势减速或将突破)
    """
    width = high.rolling(window).max() - low.rolling(window).min()
    return width.pct_change(5)


# ====================================================================
# 九、综合 Brooks 市场状态评分
# ====================================================================

def trend_strength_score(open_: pd.Series, high: pd.Series, low: pd.Series,
                         close: pd.Series, window: int = 20) -> pd.Series:
    """
    Brooks 趋势强度综合评分 [-100, +100]
    正=多头趋势, 负=空头趋势, 接近0=交易区间
    维度:
        - HH/HL vs LH/LL 比例
        - 趋势K线占比
        - 收盘位置偏向
        - K线重叠度（低=趋势强）
    """
    hh_hl = hh_hl_count(high, low, window)
    bull_score = hh_hl["hh_hl_count"]
    bear_score = hh_hl["lh_ll_count"]
    structure = (bull_score - bear_score) / window * 50

    trend_bars = is_trend_bar(open_, close, high, low)
    bull_trend_pct = (trend_bars == 1).astype(int).rolling(window).mean()
    bear_trend_pct = (trend_bars == -1).astype(int).rolling(window).mean()
    trend_score = (bull_trend_pct - bear_trend_pct) * 25

    pos = close_position(open_, high, low, close)
    pos_bias = (pos.rolling(window).mean() - 0.5) * 50

    overlap = bar_overlap_avg(high, low, min(window, 10))
    trend_clarity = (1 - overlap) * 25 * np.sign(structure)

    return (structure + trend_score + pos_bias + trend_clarity).clip(-100, 100)


# ====================================================================
# 批量生成 Al Brooks 价格行为特征
# ====================================================================

def compute_price_action_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    为单品种数据批量计算 Al Brooks 价格行为特征
    输入 df 需包含: open, high, low, close
    """
    o = df["open"]
    h = df["high"]
    l = df["low"]   # noqa: E741
    c = df["close"]
    result = pd.DataFrame(index=df.index)

    # ---- 一、K线分类 ----
    result["pa_bar_range"] = bar_range(h, l)
    result["pa_bar_body"] = bar_body(o, c)
    result["pa_bar_body_abs"] = bar_body_abs(o, c)
    result["pa_bar_body_mid"] = bar_body_mid(o, c)
    result["pa_bar_mid"] = bar_mid(h, l)
    for w in [3, 10, 20]:
        result[f"pa_bar_range_avg_{w}"] = bar_range_avg(h, l, w)
        result[f"pa_bar_range_rel_{w}"] = bar_range_relative(h, l, w)
    result["pa_body_ratio"] = bar_body_relative(o, c, h, l)
    result["pa_close_position"] = close_position(o, h, l, c)
    result["pa_is_trend_bar"] = is_trend_bar(o, c, h, l)
    result["pa_is_doji"] = is_doji(o, c, h, l)
    result["pa_is_shaved_top"] = is_shaved_top(h, c, o, l)
    result["pa_is_shaved_bottom"] = is_shaved_bottom(l, c, o, h)

    # ---- 二、信号K线 ----
    result["pa_bull_reversal"] = bull_reversal_bar(o, h, l, c)
    result["pa_bear_reversal"] = bear_reversal_bar(o, h, l, c)
    for w in [3, 10, 20]:
        result[f"pa_signal_strength_{w}"] = signal_bar_strength(o, h, l, c, w)
    result["pa_consec_bull"] = consecutive_bull_bars(o, c)
    result["pa_consec_bear"] = consecutive_bear_bars(o, c)

    # ---- 三、K线关系 ----
    result["pa_inside_bar"] = inside_bar(h, l)
    result["pa_outside_bar"] = outside_bar(h, l)
    result["pa_ii_pattern"] = ii_pattern(h, l)
    result["pa_ioi_pattern"] = ioi_pattern(h, l)
    result["pa_bar_overlap"] = bar_overlap(h, l)
    for w in [5, 10]:
        result[f"pa_bar_overlap_avg_{w}"] = bar_overlap_avg(h, l, w)
    result["pa_gap_bar"] = gap_bar(o, h, l, c)
    result["pa_gap_bar_size"] = gap_bar_size(o, h, l, c)

    # ---- 四、趋势结构 ----
    result["pa_higher_high"] = higher_high(h)
    result["pa_lower_low"] = lower_low(l)
    result["pa_higher_low"] = higher_low(l)
    result["pa_lower_high"] = lower_high(h)
    for w in [3, 10, 20]:
        hh_hl_df = hh_hl_count(h, l, w)
        result[f"pa_hh_hl_count_{w}"] = hh_hl_df["hh_hl_count"]
        result[f"pa_lh_ll_count_{w}"] = hh_hl_df["lh_ll_count"]
    result["pa_dist_swing_high"] = dist_to_last_swing_high(h, c)
    result["pa_dist_swing_low"] = dist_to_last_swing_low(l, c)
    for w in [3, 10, 20, 60]:
        result[f"pa_pullback_depth_{w}"] = pullback_depth(c, w)
    leg_df = trend_leg_count(c, o, 20)
    result["pa_bull_legs_20"] = leg_df["bull_legs"]
    result["pa_bear_legs_20"] = leg_df["bear_legs"]
    result["pa_leg_length"] = current_leg_length(c, o)
    result["pa_leg_range"] = current_leg_range(c, o, h, l)

    # ---- 五、通道与交易区间 ----
    for n in [3, 5]:
        result[f"pa_micro_channel_{n}"] = micro_channel(c, o, n)
    for w in [10, 20]:
        result[f"pa_tight_channel_{w}"] = tight_channel_score(h, l, c, w)
    for w in [10, 20]:
        result[f"pa_trading_range_{w}"] = trading_range_score(h, l, c, w)
    result["pa_barb_wire"] = barb_wire(h, l, o, c)

    # ---- 六、突破 ----
    for w in [3, 10, 20]:
        result[f"pa_breakout_up_{w}"] = breakout_up(h, w)
        result[f"pa_breakout_down_{w}"] = breakout_down(l, w)
        result[f"pa_breakout_strength_{w}"] = breakout_strength(o, h, l, c, w)
        result[f"pa_follow_through_{w}"] = follow_through(c, o, h, l, w)
        result[f"pa_breakout_fail_{w}"] = breakout_failure(h, l, c, w)
        result[f"pa_breakout_pullback_{w}"] = breakout_pullback(h, l, c, w)

    # ---- 七、测量移动 ----
    result["pa_leg_ratio"] = leg_ratio(c, o, h, l, 20)
    result["pa_dist_measured_move"] = close_to_measured_move(h, l, c, 40)

    # ---- 八、双顶双底与楔形 ----
    for w in [20, 60]:
        result[f"pa_double_top_{w}"] = double_top_proximity(h, w)
        result[f"pa_double_bottom_{w}"] = double_bottom_proximity(l, w)
    result["pa_wedge_20"] = wedge_pattern(h, l, 20)
    slopes = channel_slope(h, l, 20)
    result["pa_high_slope_20"] = slopes["high_slope"]
    result["pa_low_slope_20"] = slopes["low_slope"]
    result["pa_channel_width_chg"] = channel_width_change(h, l, 20)

    # ---- 九、综合评分 ----
    for w in [3, 10, 20]:
        result[f"pa_trend_strength_{w}"] = trend_strength_score(o, h, l, c, w)

    return result
