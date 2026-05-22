# CTA 特征说明文档

> 共 **18 大类全部落地 + 截面特征**，全部基于 OHLCV + 持仓量原始字段计算。
> §13–§18 已通过 `volatility_regime.py / price_action_supplement.py / multi_timeframe.py / regime.py / composite.py / entry_stop.py` 实现，对应 py 文件可直接 grep。
> §11 的 75 个同比特征**仅在 1 分钟 bar 上生成**；minute5/15/30/60 的 bar 时间跨度本身已超过同比所定义的 1/3/5/10/20 分钟粒度，显式不生成以避免假精度。
> 所有特征以 parquet 格式输出，按日分片落盘：`cta/data/feature/{interval}/{SYMBOL}/{YYYY-MM-DD}.parquet`。
> **生成入口**：`python3 -m cta.feature.run_all_features`（支持全频率覆盖 day/minute/minute5/minute15/minute30/minute60 + 多进程 + 断点续跑）。
> `cta/feature/run_generate.py` 为旧入口，仅限单机 day+minute 联调使用，产物为平铺布局，**不推荐**，已通过 FEATURES.md 头部提示迁移。
---

## 目录

**已落地（基于 day/minute/minute30 原始 bar 计算，已有/待算均属 bar-derivable 范畴）：**

1. [趋势类 (trend.py)](#1-趋势类-trendpy)
2. [动量类 (momentum.py)](#2-动量类-momentumpy)
3. [波动率类 (volatility.py)](#3-波动率类-volatilitypy)
4. [成交量与持仓量类 (volume.py)](#4-成交量与持仓量类-volumepy)
5. [价格形态类 (pattern.py)](#5-价格形态类-patternpy)
6. [Al Brooks 价格行为类 (price_action.py)](#6-al-brooks-价格行为类-price_actionpy)
7. [截面类 (cross_section.py)](#7-截面类-cross_sectionpy)
8. [日历类 (calendar_feat.py)](#8-日历类-calendar_featpy)
9. [Al Brooks 交易上下文类 (price_action_context.py)](#9-al-brooks-交易上下文类-price_action_contextpy)
10. [高级 Al Brooks 特征 (price_action_advanced.py)](#10-高级-al-brooks-特征-price_action_advancedpy)
11. [分钟级同比特征 (minute_tod.py)](#11-分钟级同比特征-minute_todpy)
12. [统计/分形类特征 (stats_feat.py)](#12-统计分形类特征-stats_featpy)

**已落地（源自 `cta/cta_skills/` 设计，已实现 py 文件）：**

13. [波动率体制补充](#13-波动率体制补充--volatility-regime-supplement) — `volatility_regime.py`
14. [Al Brooks 形态补充](#14-al-brooks-形态补充--price-action-supplement) — `price_action_supplement.py`
15. [多周期对齐](#15-多周期对齐--multi-timeframe-alignment) — `multi_timeframe.py`
16. [市场状态机](#16-市场状态机--regime-labels) — `regime.py`
17. [综合评分](#17-综合评分--composite-scores) — `composite.py`
18. [入场/止损建议价](#18-入场止损建议价--entry--stop-hints) — `entry_stop.py`

**未来特征（非 bar 原始数据派生，依赖交易 / 组合 / 元数据 / ML 模型，后续接入）：**

19. [未来特征](#19-未来特征--future-features)

---

## 1. 趋势类 (trend.py)

衡量价格运动的方向和持续性。

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `sma_{N}` | N 日简单移动平均 | `close.rolling(N).mean()` |
| `ema_{N}` | N 日指数移动平均 | `close.ewm(span=N).mean()`，权重按指数衰减 |
| `macd_dif` | MACD 快慢线差 | `EMA(close,12) - EMA(close,26)` |
| `macd_dea` | MACD 信号线 | `EMA(DIF, 9)` |
| `macd_hist` | MACD 柱状图 | `2 * (DIF - DEA)`，红柱正/绿柱负 |
| `plus_di` | +DI 正方向指标 | 正向运动 DM 的 EMA / ATR * 100 |
| `minus_di` | -DI 负方向指标 | 负向运动 DM 的 EMA / ATR * 100 |
| `adx` | 平均方向指数 | DX 的 EMA，DX = \|+DI - -DI\| / (+DI + -DI) * 100 |
| `aroon_up` | Aroon 上行 | N 日内最高点距今天数 / N * 100 |
| `aroon_down` | Aroon 下行 | N 日内最低点距今天数 / N * 100 |
| `aroon_osc` | Aroon 震荡 | Aroon Up - Aroon Down |
| `ma_alignment` | 均线排列 | 1=多头排列(5>10>20>60), -1=空头排列, 0=混合 |
| `bias_{N}` | 乖离率 | `(close - SMA_N) / SMA_N * 100`，偏离均线程度 |
| `slope_{N}` | 线性回归斜率 | N 日滚动窗口内的最小二乘回归斜率 |
| `supertrend` | SuperTrend 值 | 基于 ATR 的自适应趋势跟踪线 |
| `supertrend_dir` | SuperTrend 方向 | 1=上升趋势, -1=下降趋势 |

> **周期参数**: SMA/EMA = {3, 5, 10, 20, 60, 120}; BIAS = {3, 5, 10, 20, 60}; Slope = {3, 10, 20, 60}

---

## 2. 动量类 (momentum.py)

衡量价格变化的速度和力度，用于判断超买超卖。

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `rsi_{N}` | 相对强弱指数 | `100 - 100/(1 + avg_gain/avg_loss)`，EMA 平滑，范围 [0,100] |
| `roc_{N}` | N 日变化率 | `(close - close_N前) / close_N前 * 100` |
| `roc_ma_12_6` | ROC 移动平均 | ROC(12) 的 6 日移动平均，平滑动量信号 |
| `williams_r_14` | 威廉 %R | `-100 * (HH - close) / (HH - LL)`，范围 [-100, 0] |
| `cci_{N}` | 商品通道指数 | `(TP - MA_TP) / (0.015 * MD)`，TP=(H+L+C)/3 |
| `stoch_k` | KDJ-K 值 | RSV 的 EMA 平滑，RSV = (C-LL)/(HH-LL)*100 |
| `stoch_d` | KDJ-D 值 | K 值的 EMA 平滑 |
| `stoch_j` | KDJ-J 值 | `3K - 2D`，超买超卖更敏感 |
| `momentum_{N}` | 动量 | `close - close_N日前`，绝对价差 |
| `tsi` | 真实强度指数 | 价差的双重 EMA 平滑 / 价差绝对值的双重 EMA 平滑 * 100 |
| `ultimate_osc` | 终极震荡指标 | 三个周期(7/14/28)的买入压力加权平均 |
| `log_ret_{N}` | N 日对数收益率 | `ln(close / close_N日前)` |
| `pct_ret_{N}` | N 日百分比收益率 | `(close - close_N日前) / close_N日前` |

> **周期参数**: RSI = {3, 6, 14, 24}; ROC = {3, 5, 10, 20}; CCI = {3, 14, 20}; Momentum/Returns = {1, 3, 5, 10, 20}

---

## 3. 波动率类 (volatility.py)

衡量价格的波动幅度和分布特征。

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `atr_{N}` | N 日平均真实波幅 | TR = max(H-L, \|H-prevC\|, \|L-prevC\|) 的 EMA |
| `natr_14` | 标准化 ATR | `ATR / close * 100`，可跨品种比较 |
| `bb_mid` | 布林带中轨 | 20 日 SMA |
| `bb_upper` | 布林带上轨 | `SMA + 2 * STD` |
| `bb_lower` | 布林带下轨 | `SMA - 2 * STD` |
| `bb_width` | 布林带宽度 | `(上轨 - 下轨) / 中轨 * 100` |
| `bb_pctb` | 布林带 %B | `(close - 下轨) / (上轨 - 下轨)`，0=下轨,1=上轨 |
| `hist_vol_{N}` | N 日历史波动率 | `log_ret.rolling(N).std() * sqrt(252)`，年化 |
| `realized_var_20` | 已实现方差 | `sum(log_ret^2)` 的 20 日滚动 |
| `kc_mid` | Keltner 通道中轨 | 20 日 EMA |
| `kc_upper` / `kc_lower` | Keltner 上/下轨 | `EMA +/- 1.5 * ATR` |
| `dc_upper_{N}` / `dc_lower_{N}` | 唐奇安通道 | N 日最高价 / 最低价 |
| `dc_mid_{N}` | 唐奇安中轨 | `(上轨 + 下轨) / 2` |
| `vol_ratio_5_20` | 波动率比率 | 短期(5日)波动率 / 长期(20日)波动率 |
| `vol_ratio_10_60` | 波动率比率 | 10日 / 60日 |
| `intraday_range` | 日内振幅 | `(H - L) / close * 100` |
| `avg_range_20` | 平均振幅 | 20 日 intraday_range 均值 |
| `gk_vol_20` | Garman-Klass 波动率 | 利用 OHLC 四价的高效波动率估计，年化 |
| `parkinson_vol_20` | Parkinson 波动率 | 仅用 High/Low 的波动率估计，年化 |

> **周期参数**: ATR = {3, 5, 14, 20}; 历史波动率 = {3, 10, 20, 60}; Donchian = {3, 10, 20}

---

## 4. 成交量与持仓量类 (volume.py)

分析资金流向和市场参与度。

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `vol_ma_{N}` | N 日成交量均线 | `volume.rolling(N).mean()` |
| `vol_ratio_{N}` | 量比 | 当日成交量 / N 日成交量均值 |
| `vol_zscore_20` | 成交量 Z-Score | `(volume - MA) / STD`，标准化异常放量 |
| `obv` | 能量潮 | 阳线成交量累加、阴线累减 |
| `obv_ma_20` | OBV 移动平均 | OBV 的 20 日 SMA |
| `vwap_{N}` | 滚动成交量加权均价 | `sum(close*volume, N) / sum(volume, N)` |
| `mfi_14` | 资金流量指数 | 类似 RSI 但加入成交量权重，范围 [0,100] |
| `ad_line` | A/D 累积分布线 | CLV * volume 的累积和，CLV=(C-L-(H-C))/(H-L) |
| `chaikin_osc` | 佳庆震荡指标 | AD 的 EMA(3) - EMA(10) |
| `cmf_20` | 佳庆资金流 | 20 日窗口内 CLV*volume 之和 / volume 之和 |
| `force_idx_13` | 力量指数 | `(close变化 * volume)` 的 13 日 EMA |
| `oi_change` | 持仓量变化 | 持仓量日差 |
| `oi_change_pct` | 持仓量变化率 | 持仓量日变化百分比 |
| `oi_vol_ratio` | 持仓量/成交量 | 反映持仓结构 |
| `oi_ma_{N}` | 持仓量均线 | N 日持仓量 SMA |
| `pv_corr_20` | 价量相关系数 | close 与 volume 的 20 日滚动 Pearson 相关 |
| `pv_divergence_10` | 量价背离 | 价格方向与成交量方向的差异 |

---

## 5. 价格形态类 (pattern.py)

识别 K 线形态和关键价格位置。

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `body_ratio` | 实体占全幅比例 | `\|close - open\| / (high - low)` |
| `upper_shadow_ratio` | 上影线比例 | `(high - max(O,C)) / (high - low)` |
| `lower_shadow_ratio` | 下影线比例 | `(min(O,C) - low) / (high - low)` |
| `candle_dir` | K线方向 | 1=阳线, -1=阴线, 0=十字 |
| `gap` | 缺口 | 今日开盘 - 昨日收盘 |
| `gap_pct` | 缺口百分比 | `gap / 昨收 * 100` |
| `is_{N}d_high` | 是否创 N 日新高 | 当前高点 > 过去 N 日最高（排除今日）|
| `is_{N}d_low` | 是否创 N 日新低 | 当前低点 < 过去 N 日最低（排除今日）|
| `days_since_{N}d_high` | 距 N 日新高天数 | N 日窗口内最高点到当前的天数 |
| `days_since_{N}d_low` | 距 N 日新低天数 | N 日窗口内最低点到当前的天数 |
| `price_pos_{N}` | N 日价格位置 | `(close - LL) / (HH - LL)`，0=最低,1=最高 |
| `consecutive_days` | 连涨/连跌天数 | 正=连涨天数, 负=连跌天数 |
| `inside_bar` | 内包线 | 今日高低在昨日范围内 (0/1) |
| `outside_bar` | 外包线 | 今日高低完全覆盖昨日 (0/1) |

> **周期参数**: 新高新低 = {3, 5, 10, 20, 60}; 价格位置 = {3, 10, 20, 60}

---

## 6. Al Brooks 价格行为类 (price_action.py)

基于 Al Brooks 《Price Action》系列著作的系统化量化特征，覆盖其核心交易概念。

### 6.1 K线分类 (Bar Classification)

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `pa_bar_range` | K线全幅 | `high - low` |
| `pa_bar_body` | K线实体（有符号） | `close - open`，正=阳线，负=阴线 |
| `pa_bar_body_abs` | K线实体绝对值 | `\|close - open\|` |
| `pa_bar_body_mid` | 实体中点 | `(open + close) / 2` |
| `pa_bar_mid` | K线中点 | `(high + low) / 2` |
| `pa_bar_range_avg_{N}` | N 日平均全幅 | `bar_range.rolling(N).mean()` |
| `pa_bar_range_rel_{N}` | 相对全幅 | 当前全幅 / N日平均全幅，>1=大K线 |
| `pa_body_ratio` | 实体占比 | `\|body\| / range`。Brooks: >0.5=趋势K线, <0.25=十字星 |
| `pa_close_position` | 收盘位置 | `(close-low)/(high-low)` [0,1]。趋势K收在极端 |
| `pa_is_trend_bar` | 趋势K线 | 实体>50%全幅：1=阳线趋势K, -1=阴线趋势K |
| `pa_is_doji` | 十字星 | 实体<25%全幅 (0/1) |
| `pa_is_shaved_top` | 光头K线 | 上影线极短(<5%全幅) (0/1) |
| `pa_is_shaved_bottom` | 光脚K线 | 下影线极短(<5%全幅) (0/1) |

### 6.2 信号K线 (Signal Bars)

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `pa_bull_reversal` | 看涨反转K线 | 阳线 + 收盘在上半部 + 下影线>实体 (0/1) |
| `pa_bear_reversal` | 看跌反转K线 | 阴线 + 收盘在下半部 + 上影线>实体 (0/1) |
| `pa_signal_strength_{N}` | 信号强度 | [-5,+5] 综合评分: 方向+收盘位置+K线大小+影线+实体比例 |
| `pa_consec_bull` | 连续阳线根数 | 连续 close > open 的计数 |
| `pa_consec_bear` | 连续阴线根数 | 连续 close < open 的计数 |

### 6.3 K线关系 (Bar Context)

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `pa_inside_bar` | 内包线 | 今日 H/L 在昨日 H/L 范围内 (0/1) |
| `pa_outside_bar` | 外包线 | 今日 H/L 完全覆盖昨日 (0/1) |
| `pa_ii_pattern` | ii 模式 | 连续两根内包线 (0/1)。Brooks: 突破方向的关键信号 |
| `pa_ioi_pattern` | ioi 模式 | 内包-外包-内包序列 (0/1) |
| `pa_bar_overlap` | 相邻K线重叠度 | [0,1] 重叠区间/总区间。高=震荡,低=趋势 |
| `pa_bar_overlap_avg_{N}` | N根平均重叠度 | 滚动均值，判断市场结构 |
| `pa_gap_bar` | 缺口K线方向 | +1=向上缺口(今low>昨high), -1=向下缺口 |
| `pa_gap_bar_size` | 缺口大小% | 缺口幅度占昨收的百分比 |

### 6.4 趋势结构 (Trend Structure)

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `pa_higher_high` | Higher High | 今日高点 > 昨日高点 (0/1) |
| `pa_lower_low` | Lower Low | 今日低点 < 昨日低点 (0/1) |
| `pa_higher_low` | Higher Low | 今日低点 > 昨日低点 (0/1) |
| `pa_lower_high` | Lower High | 今日高点 < 昨日高点 (0/1) |
| `pa_hh_hl_count_{N}` | N日内 HH+HL 计数 | 多头结构强度，Brooks 趋势定义 |
| `pa_lh_ll_count_{N}` | N日内 LH+LL 计数 | 空头结构强度 |
| `pa_dist_swing_high` | 距最近 Swing High | `(close - last_swing_high) / last_swing_high * 100` |
| `pa_dist_swing_low` | 距最近 Swing Low | `(close - last_swing_low) / last_swing_low * 100` |
| `pa_pullback_depth_{N}` | N日回撤深度 | `(N日最高 - close) / (N日最高 - N日最低)` [0,1] |
| `pa_bull_legs_20` | 20日多头腿数 | 连续阳线段的个数 |
| `pa_bear_legs_20` | 20日空头腿数 | 连续阴线段的个数 |
| `pa_leg_length` | 当前腿长度 | 当前连续同方向K线根数 |
| `pa_leg_range` | 当前腿幅度 | 腿起点到当前的价格变动 |

### 6.5 通道与交易区间 (Channel & Trading Range)

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `pa_micro_channel_{N}` | N根微通道 | 连续N根同方向趋势K线: 1=多头, -1=空头。十字星不中断 |
| `pa_tight_channel_{N}` | 紧密通道评分 | [0,1]，重叠度高+振幅稳定=紧密。Brooks: 不逆势交易 |
| `pa_trading_range_{N}` | 交易区间评分 | [0,1]，区间窄+方向交替=震荡。Brooks: 80%突破失败 |
| `pa_barb_wire` | Barb Wire | 5根内有3+根小K线+高重叠 (0/1)。避免交易信号 |

### 6.6 突破 (Breakout)

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `pa_breakout_up_{N}` | 向上突破 | 今日 high > 过去 N 日最高 (0/1) |
| `pa_breakout_down_{N}` | 向下突破 | 今日 low < 过去 N 日最低 (0/1) |
| `pa_breakout_strength_{N}` | 突破K线强度 | [-1,1] = 方向 * 实体占比。好突破=大实体趋势K |
| `pa_follow_through_{N}` | 突破跟随 | 突破后下一根是否同方向: 1=确认, -1=背离 |
| `pa_breakout_fail_{N}` | 突破失败 | 突破后收回区间: 1=向上失败, -1=向下失败。强反转信号 |
| `pa_breakout_pullback_{N}` | 突破回测 | 突破后5根内是否回到突破点 |

### 6.7 测量移动 (Measured Move)

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `pa_leg_ratio` | 腿幅比 | 当前腿/平均腿幅度。约1 为等距移动(价格目标) |
| `pa_dist_measured_move` | 距等距目标距离% | 到上方或下方等距延伸目标的较近距离 |

### 6.8 双顶双底与楔形 (Double Top/Bottom & Wedge)

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `pa_double_top_{N}` | 双顶接近度 | [0,1] 当前高点接近过去 N 日前高的程度(排除当日) |
| `pa_double_bottom_{N}` | 双底接近度 | [0,1] 当前低点接近过去 N 日前低的程度 |
| `pa_wedge_20` | 楔形模式 | 1=上升楔形(看跌), -1=下降楔形(看涨) |
| `pa_high_slope_20` | 高点斜率 | 20日内高点的线性回归斜率 |
| `pa_low_slope_20` | 低点斜率 | 20日内低点的线性回归斜率 |
| `pa_channel_width_chg` | 通道宽度变化率 | 正=扩张(加速), 负=收缩(将突破) |

### 6.9 综合评分

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `pa_trend_strength_{N}` | 趋势强度综合分 | [-100,+100] 综合: HH/HL结构+趋势K占比+收盘位置偏向+重叠度 |

---

## 7. 截面类 (cross_section.py)

跨品种横截面比较，用于截面动量、均值回归等策略。

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `cs_ret_rank_{N}` | N日收益率截面排名 | 当日该品种 N 日收益率在所有品种中的百分位 [0,1] |
| `cs_ret_zscore_{N}` | N日收益率截面Z分 | `(品种ret - 均值) / 标准差`，标准化后的相对强弱 |
| `cs_mom_rank_{N}` | N日动量截面排名 | 动量(N日收益率)在截面中的百分位排名 |
| `cs_vol_rank_{N}` | N日波动率截面排名 | 波动率在截面中的百分位排名 |
| `cs_volume_rank` | 成交量截面排名 | 当日成交量在所有品种中的百分位 |
| `cs_oi_rank` | 持仓量截面排名 | 当日持仓量在所有品种中的百分位 |
| `cs_rel_strength_{N}` | 相对强弱 | 品种N日收益率 / 全品种等权平均收益率 |

> **周期参数**: 收益率排名 = {1, 3, 5, 20}; 动量排名 = {3, 10, 20, 60}; 波动率排名 = {3, 20, 60}; 相对强弱 = {3, 10, 20, 60}

---

## 8. 日历类 (calendar_feat.py)

时间周期性和日历效应特征。

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `dow` | 星期几 | 0=周一, 4=周五 |
| `month` | 月份 | 1-12 |
| `quarter` | 季度 | 1-4 |
| `dom` | 日期 | 一月中第几天 |
| `doy` | 年内天序 | 一年中第几天 |
| `woy` | 年内周序 | 一年中第几周 |
| `is_week_start` | 交易周首日 | (0/1) |
| `is_week_end` | 交易周末日 | (0/1) |
| `is_month_start` | 月度首个交易日 | (0/1) |
| `is_month_end` | 月度最后交易日 | (0/1) |
| `days_to_month_end` | 距月末天数 | 自然日天数 |
| `dow_sin` / `dow_cos` | 星期周期编码 | sin/cos 编码，让周一周五在数值上相近 |
| `month_sin` / `month_cos` | 月份周期编码 | sin/cos 编码，让12月和1月在数值上相近 |

---

## 9. Al Brooks 交易上下文类 (price_action_context.py)

与 price_action.py 的区别：price_action.py 描述"当前K线是什么"，本模块描述"当前处于什么交易环境、该不该交易、风险回报如何"。

Brooks 核心原则：
1. 永远先判断 context（趋势 vs 交易区间 vs 突破）
2. 只在有利概率 + 合理风险回报的位置交易
3. 信号K线本身不重要，重要的是它出现在哪里

### 9.1 市场结构识别 (Market Structure)

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `pa_range_over_atr_{N}` | 区间宽度/ATR | N日区间(HH-LL)/ATR。>3=大区间突破空间大，<1.5=紧凑即将突破 |
| `pa_inside_bar_ratio_{N}` | Inside Bar 占比 | N根内IB数量占比 [0,1]。高=犹豫不决，即将大幅突破 |
| `pa_overlap_ratio_{N}` | 重叠K线占比 | N根内overlap>0.5的K线占比。高=交易区间，低=趋势 |
| `pa_trend_bar_bull_pct` | 多头趋势K线占比 | 20根内阳线趋势K线比例 |
| `pa_trend_bar_bear_pct` | 空头趋势K线占比 | 20根内阴线趋势K线比例 |
| `pa_trend_bar_net` | 趋势K线净占比 | 多头占比 - 空头占比。正=多头优势，负=空头优势 |
| `pa_doji_density_{N}` | 十字星密度 | N根内十字星占比。高=barb wire，不适合交易 |
| `pa_two_leg_pullback` | 两条腿回撤 | 1=多头两腿回撤完成(做多点), -1=空头两腿回撤完成(做空点), 0=无 |
| `pa_ema_slope_{N}` | EMA 斜率 | EMA(N)的5日差分/平均波幅。正=上升趋势，负=下降趋势，0=横盘 |
| `pa_ema_slope_accel` | EMA 斜率加速度 | EMA斜率的3日变化率。正=趋势加速，负=趋势减速 |

> **周期参数**: range_over_atr = {10, 20}; IB/overlap ratio = {10, 20}; doji = {5, 10}; EMA slope = {20, 60}

### 9.2 回撤质量 (Pullback Quality)

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `pa_prior_leg_slope` | 前腿斜率 | 前一趋势腿的(收盘变化/K线数)/平均波幅。陡=好入场 |
| `pa_pullback_retrace` | 回调深度比 | 回调幅度/前腿幅度。<0.5=浅(强趋势)，0.5-0.618=健康，>1=可能反转 |
| `pa_pullback_tb_quality_{N}` | 回调趋势K线质量 | 最近N根趋势K线均值 [-1,1]。正=回调较弱(有利做多) |
| `pa_pullback_bar_ratio` | 回调K线数比 | 回调K线数/前腿K线数。<0.5=V型，0.5-1=正常，>1=可能不是回调 |
| `pa_dist_to_ema_{N}` | 距EMA距离(%) | `(close-EMA)/EMA*100`。正=价格在EMA上方，负=下方 |
| `pa_pullback_to_ema_20` | 是否回调到EMA | 距EMA<0.3%时=1，Brooks最常见高概率入场位 |

> **周期参数**: pullback_tb_quality = {3, 5}; dist_to_ema = {20, 60}

### 9.3 突破质量 (Breakout Quality)

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `pa_bo_body_ratio_{N}` | 突破K线实体占比 | 突破K线的body/range。>0.7=好突破，十字星=差突破。非突破K=0 |
| `pa_bo_close_pos_{N}` | 突破K线收盘位置 | 突破K线的close_position。向上突破>0.8=强。非突破K=0.5 |
| `pa_bo_vol_ratio_20` | 突破量比 | 突破K线成交量/20日均量。放量突破更可靠。非突破K=1 |
| `pa_bo_follow_consistency` | 突破跟随一致性 | 突破后3根K线同方向比例 [0,1]。1=全同向确认 |
| `pa_pre_bo_bars` | 突破前区间K线数 | 区间成熟度，越久突破后运动越大（止损单堆积） |
| `pa_pre_bo_tightness_{N}` | 突破前紧密度 | [0,1] = (IB占比+小K线占比+高重叠占比)/3。紧密区间突破更有力 |

> **周期参数**: bo_body_ratio/close_pos = {10, 20}; pre_bo_tightness = {5, 10}

### 9.4 关键价位与空间 (Key Levels & Space)

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `pa_space_to_high_{N}` | 距前高空间(%) | `(N日前高-close)/close*100`。正=有上方阻力，负=已突破 |
| `pa_space_to_low_{N}` | 距前低空间(%) | `(close-N日前低)/close*100`。正=有下方支撑，负=已跌破 |
| `pa_range_zone_20` | 区间位置分区 | -1=下1/3(做多区), 0=中间1/3(不交易), 1=上1/3(做空区) |
| `pa_space_ratio_ud` | 上下空间比 | 上方空间/下方空间。>1=做多更有利，<1=做空更有利 |
| `pa_resistance_density` | 上方阻力密度 | 40根内高点在当前价上方0.5%范围内的比例。高=突破困难 |
| `pa_support_density` | 下方支撑密度 | 40根内低点在当前价下方0.5%范围内的比例。高=不易跌破 |

> **周期参数**: space_to_high/low = {20, 60}

### 9.5 风险回报 (Risk-Reward)

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `pa_signal_risk` | 信号K线风险 | K线全幅/ATR。<1=风险可控，>1.5=需要更大回报 |
| `pa_expected_reward` | 预期回报(ATR单位) | 20日区间宽度/ATR，合理的利润目标估计 |
| `pa_expected_rr` | 预期风险回报比 | 预期回报/信号风险。>2=值得scalp，>3=值得swing |
| `pa_scalp_score` | Scalp可行性 | [0,100] = 止损合理(25)+趋势明确(25)+不在中间(25)+有空间(25) |
| `pa_swing_score` | Swing可行性 | [0,100] = 区间够大(25)+明确趋势(25)+RR>2(25)+在边缘(25) |

### 9.6 多头/空头力量平衡 (Bull vs Bear Pressure)

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `pa_close_pos_bias_{N}` | 收盘位置偏向 | N根K线收盘位置均值。持续>0.6=多头控盘，<0.4=空头控盘 |
| `pa_shadow_pressure_{N}` | 影线压力比 | N根下影总长/上影总长。>1=下方买盘强(看涨)，<1=上方卖盘强(看跌) |
| `pa_buy_climax` | 买方Climax | 大阳线(>1.5x均幅)+实体>60%+收盘>80%=最后疯狂=趋势将反转 (0/1) |
| `pa_sell_climax` | 卖方Climax | 大阴线(>1.5x均幅)+实体>60%+收盘<20%=恐慌抛售=趋势将反转 (0/1) |
| `pa_momentum_decay` | 动量衰减 | 实体的线性回归斜率（反向）。正=实体递减=趋势将结束 |
| `pa_exhaustion_gap` | 衰竭缺口 | 1=向上缺口+收阴(看跌反转), -1=向下缺口+收阳(看涨反转) |
| `pa_tb_streak_strength` | 趋势K线连续强度 | 连续同方向趋势K线实体累计/平均全幅。Always In 状态指标 |
| `pa_always_in_dir` | Always In 方向 | [-1,1] 综合(EMA方向30%+趋势K净占比30%+收盘偏向40%)。>0.5=做多，<-0.5=做空 |

> **周期参数**: close_pos_bias = {5, 10, 20}; shadow_pressure = {5, 10}

---

## 10. 高级 Al Brooks 特征 (price_action_advanced.py)

进一步深化 Al Brooks Price Action 量化体系，覆盖腿微观结构、回调分类、通道细节等高级概念。

### 10.1 趋势腿微观结构 (Leg Microstructure)

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `pa_leg_body_consistency_{N}` | 腿内实体一致性 | 最近N根同方向K线的body方差/均值。低=强趋势腿 |
| `pa_leg_close_consistency_{N}` | 腿内收盘位置一致性 | 同方向腿内close_position均值。持续>0.7=极强多头腿 |
| `pa_leg_gap_count_{N}` | 腿内缺口数 | N根内出现的同方向缺口数量。多=趋势非常强 |
| `pa_trend_bar_cluster_{N}` | 趋势K线聚集度 | N根内最密集的连续趋势K线段长度。反映 spike 强度 |
| `pa_leg_ema_separation_{N}` | 腿与EMA分离度 | 腿内K线与EMA的平均距离/ATR。大=趋势过度延伸 |
| `pa_leg_acceleration` | 腿加速度 | 腿内后半段平均实体/前半段平均实体。>1=加速，<1=减速 |

### 10.2 回调形态分类 (Pullback Pattern Classification)

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `pa_pullback_type` | 回调类型 | 1=单K线回调，2=两腿回调，3=楔形回调，4=复杂回调 |
| `pa_pullback_overlap_with_leg` | 回调与前腿重叠 | 回调K线高低点与前腿K线重叠程度。低=强趋势回调 |
| `pa_pullback_close_vs_entry` | 回调收盘位置 | 回调末根K线的收盘位置。>0.7(多头回调末)=好入场 |
| `pa_first_pullback` | 是否首次回调 | 突破后的第一次回调=最高概率入场 (0/1) |
| `pa_high_1_2_3` | H1/H2/H3 标记 | Brooks 风格的多头回调计数。H1=首次回调，H2=第二次 |
| `pa_low_1_2_3` | L1/L2/L3 标记 | 空头回调计数。L1=首次，L2=第二次(最佳做空) |

### 10.3 通道细分 (Channel Details)

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `pa_channel_overshoot` | 通道超越 | 价格突破通道线后又回到通道内 (0/1)。反转信号 |
| `pa_channel_touch_count_{N}` | 通道触碰次数 | N根内触碰上/下通道边界的次数。多次触碰后突破概率高 |
| `pa_channel_age_{N}` | 通道年龄 | 当前通道已存续的K线数。越老=突破概率越高 |
| `pa_embedded_channel` | 嵌套通道 | 大通道中出现的小通道 (0/1)。小通道突破方向=大通道延续 |
| `pa_spike_and_channel` | Spike & Channel | 急涨/急跌后进入缓慢通道 (0/1)。Brooks 经典形态 |

### 10.4 交易区间进阶 (Trading Range Advanced)

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `pa_range_maturity` | 区间成熟度 | 区间已持续K线数/20。越大=突破后运动幅度越大 |
| `pa_range_shrinking` | 区间收缩 | 后半段区间宽度/前半段。<1=区间在收缩=即将突破 |
| `pa_range_false_bo_count` | 区间假突破次数 | 区间内累计假突破次数。越多=下次真突破概率越高 |
| `pa_range_polarity` | 区间极性 | 区间上沿曾是支撑or阻力的次数差。多=上沿更可能被突破 |
| `pa_mid_range_bounce` | 中线反弹 | 价格到达区间中线后反弹 (0/1)。区间交易的重要参考 |

### 10.5 进场/出场时机 (Entry/Exit Timing)

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `pa_second_entry` | 第二次进场信号 | 第一次入场失败后的第二次同方向信号=最可靠 (0/1) |
| `pa_failed_signal_reversal` | 信号失败反转 | 信号K线失败后反向=极强反转 (0/1) |
| `pa_profit_target_hit` | 利润目标到达 | 预期MM目标是否被触及 (0/1)。用于回测验证 |
| `pa_trail_stop_level` | 移动止损位 | 建议的移动止损水平(基于swing low/high) |
| `pa_bar_since_signal` | 距信号K线根数 | 最近一个信号K线到当前的距离。5根内入场最佳 |

### 10.6 多时间框架关系 (Multi-Timeframe)

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `pa_htf_trend_alignment` | 高级别趋势对齐 | 用60日视角的趋势方向与20日视角是否一致。1=一致(高概率) |
| `pa_ltf_setup_quality` | 低级别Setup质量 | 用3日超短视角评估当前K线的信号质量 |
| `pa_timeframe_conflict` | 时间框架冲突 | 3/20/60日趋势方向的分歧度。高=震荡市，避免交易 |

---

## 11. 分钟级同比特征 (minute_tod.py)

分钟级别的时间对比（同比）特征，比较当前分钟K线与历史同时段的表现差异，捕捉日内时间结构的变化。仅在分钟级数据中生成。

### 11.1 设计思路

- **对比基准**: 1天前同时刻、3天均值同时刻、5天均值同时刻
- **时间粒度**: 1分钟、3分钟、5分钟、10分钟、20分钟（分别聚合后对比）
- **核心思想**: 同一品种每天的日内结构（开盘波动、午盘沉寂、尾盘异动）有规律性，偏离历史同期的行为往往是交易信号

### 11.2 价格同比特征

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `tod_ret_vs_1d_{M}min` | M分钟收益率 vs 1天前同时刻 | 当前M分钟收益率 - 1天前相同时段的M分钟收益率 |
| `tod_ret_vs_3d_{M}min` | M分钟收益率 vs 3天均值 | 当前M分钟收益率 - 过去3天相同时段M分钟收益率均值 |
| `tod_ret_vs_5d_{M}min` | M分钟收益率 vs 5天均值 | 当前M分钟收益率 - 过去5天相同时段M分钟收益率均值 |
| `tod_ret_ratio_1d_{M}min` | 收益率比值(1天) | 当前M分钟收益率 / 1天前同时段收益率（方向+幅度） |
| `tod_ret_zscore_{M}min` | 收益率Z-Score | (当前收益率 - 5天同时段均值) / 5天同时段标准差 |

> **M 取值**: {1, 3, 5, 10, 20}，产生 5 * 5 = 25 个价格同比特征

### 11.3 波动率同比特征

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `tod_range_vs_1d_{M}min` | M分钟振幅 vs 1天前 | 当前M分钟(H-L)/C - 1天前同时段振幅 |
| `tod_range_vs_3d_{M}min` | M分钟振幅 vs 3天均值 | 当前振幅 - 3天同时段振幅均值 |
| `tod_range_vs_5d_{M}min` | M分钟振幅 vs 5天均值 | 当前振幅 - 5天同时段振幅均值 |
| `tod_range_ratio_1d_{M}min` | 振幅比值(1天) | 当前振幅 / 1天前同时段振幅 |

> **M 取值**: {1, 3, 5, 10, 20}，产生 5 * 4 = 20 个波动率同比特征

### 11.4 成交量同比特征

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `tod_vol_vs_1d_{M}min` | M分钟成交量 vs 1天前 | 当前M分钟成交量 / 1天前同时段成交量 |
| `tod_vol_vs_3d_{M}min` | M分钟成交量 vs 3天均值 | 当前成交量 / 3天同时段成交量均值 |
| `tod_vol_vs_5d_{M}min` | M分钟成交量 vs 5天均值 | 当前成交量 / 5天同时段成交量均值 |
| `tod_vol_zscore_{M}min` | 成交量Z-Score | (当前量 - 5天同时段均值) / 5天同时段标准差 |

> **M 取值**: {1, 3, 5, 10, 20}，产生 5 * 4 = 20 个成交量同比特征

### 11.5 K线形态同比特征

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `tod_body_ratio_vs_3d_{M}min` | 实体占比 vs 3天均值 | 当前实体占比 - 3天同时段实体占比均值 |
| `tod_close_pos_vs_3d_{M}min` | 收盘位置 vs 3天均值 | 当前收盘位置 - 3天同时段收盘位置均值 |
| `tod_dir_consistency_{M}min` | 方向一致性 | 当前方向与过去5天同时段方向一致的天数/5 |

> **M 取值**: {1, 3, 5, 10, 20}，产生 5 * 3 = 15 个形态同比特征

### 11.6 聚合逻辑说明

对于 M > 1 分钟的粒度：
1. 将 1 分钟K线按 M 根一组聚合：open=first, high=max, low=min, close=last, volume=sum
2. 每根聚合K线对应一个固定的日内时段（如 09:01-09:03、09:04-09:06 ...）
3. 用日内时段作为 key，匹配历史同时段数据
4. 日期偏移取交易日历（非自然日），避免周末/节假日错位

> **同比特征总计**: 约 80 个（25 + 20 + 20 + 15），仅在分钟级数据中生成

---

## 12. 统计/分形类特征 (stats_feat.py)

量化价格序列的统计特性，辅助判断市场状态。

| 特征名 | 含义 | 计算方式 |
|--------|------|----------|
| `hurst_{N}` | Hurst 指数 | R/S 分析。>0.5=趋势性，<0.5=均值回归，≈0.5=随机游走 |
| `entropy_{N}` | Shannon 信息熵 | 收益率分箱后的概率分布熵。低=有序(趋势)，高=无序(震荡) |

> **周期参数**: {20, 60}

> **N=3 短周期已全面覆盖**: 所有模块的周期参数均已增加 N=3，包括 SMA/EMA/BIAS/Slope、RSI/ROC/CCI/Momentum/Returns、ATR/HistVol/Donchian、新高新低/价格位置、PA特征、截面特征等。

---

## 13. 波动率体制补充 / Volatility Regime Supplement

在 §3 已有 `atr_N / natr_14 / bb_width / hist_vol_N / gk_vol_20` 的基础上补充**分位 / 分类 / 二阶**视角，用于 `05_regime_switch_strategies/` 的压缩-扩张识别。**全部 bar-derivable。**

| 特征名 | 含义 | 计算方式 | 来源章节 |
|--------|------|----------|----------|
| `atr_pct_zscore_252` | natr_14 的 252 日分位 zscore | `(natr_14 - mean_252) / std_252` | `01/04`, `05/01` |
| `bb_width_zscore_252` | bb_width 的 252 日 zscore | 同上思路 | `01/02`, `05/01` |
| `vol_regime_3` | 波动率体制分类 | 按 natr_14 分位 {<30: low, 30-70: normal, >70: high} | `01/04` |
| `vol_of_vol_20` | 波动率的波动率 | `rolling(20).std(natr_14)` | `01/04` |
| `range_ratio_N` | 窗口内振幅占比 | `(rolling_high_N - rolling_low_N) / close` | `01/02`, `04/01` |
| `range_height_atr_N` | 窗口振幅 / ATR | `(rolling_high_N - rolling_low_N) / atr_14` | `02/01`, `05/01` |
| `consecutive_hh_N` | N 日内 HH 连续计数 | 连续 `pa_higher_high=1` 的 bar 数 | `01/01` |
| `consecutive_ll_N` | N 日内 LL 连续计数 | 连续 `pa_lower_low=1` 的 bar 数 | `01/01` |
| `zscore_close_N` | 收盘价 zscore | `(close - SMA_N) / STD_N` | `04/02` |
| `breakout_dist_atr_N` | 偏离区间上/下沿 ATR 数 | `(close - dc_upper_N) / atr_14` (向下类似) | `01/03`, `05/02` |

> **周期参数**: zscore = {60, 252}; range_ratio = {10, 20, 60}; range_height_atr = {5, 10, 20}; consecutive = {5, 10, 20}

---

## 14. Al Brooks 形态补充 / Price Action Supplement

§6 已覆盖 180+ 原语，本节追加 `cta_skills/02_price_action/` 中强调但未直接实现的形态。**全部 bar-derivable。**

| 特征名 | 含义 | 计算方式 | 来源章节 |
|--------|------|----------|----------|
| `pa_tight_range_flag_K` | 近 K bar 是否 tight range | K bar 内 `range_height_atr` < 1.5 且 `close_position_bias < 0.2` | `02/01` |
| `pa_bull_flag_flag` | 多头旗形识别 | 前导 spike (N 根趋势 K) + 紧缩回调 (≥3 bar 重叠 + 斜率弱) | `02/02` |
| `pa_bear_flag_flag` | 空头旗形识别 | 空头版同上 | `02/02` |
| `pa_bpb_flag` | breakout-pullback-continuation | 突破 + 回测不破 + 第二腿同向 | `02/03` |
| `pa_swing_high_idx` | 最近 swing high 索引 | swing 窗口 N=3, 返回相对 bar 索引 | `02/06` |
| `pa_swing_low_idx` | 最近 swing low 索引 | 同上 | `02/06` |
| `pa_trend_channel_top` | 由近 3 swing high 拟合的上沿 | 线性回归 | `02/06` |
| `pa_trend_channel_bot` | 由近 3 swing low 拟合的下沿 | 同上 | `02/06` |
| `pa_trend_channel_slope` | 通道斜率 | 上/下沿平均斜率 | `02/06` |
| `pa_channel_width_atr` | 通道宽度 / ATR | `(top - bot) / atr_14` | `02/06` |

> **参数建议**: tight_range K = {5, 8, 10}; bull/bear_flag legs ≥ 5 + pullback ≥ 3。

---

## 15. 多周期对齐 / Multi-Timeframe Alignment

§9/§10 的 `pa_htf_trend_alignment / pa_timeframe_conflict / pa_ltf_setup_quality` 给了粗粒度视角；本节补充**数值化的 HTF / MTF / LTF 分数**，供 Brooks v3 风格的三级共振使用。**bar-derivable，需要多频率对齐。**

| 特征名 | 含义 | 计算方式 | 来源章节 |
|--------|------|----------|----------|
| `htf_trend_score_day` | 日线视角趋势得分 | day bar 上的 `pa_trend_strength_20` / 100 | `01/05` |
| `htf_bias` | 日线方向偏置 | sign(htf_trend_score) with dead zone \|x\| < 0.2 → 0 | `01/05`, `03/*` |
| `mtf_trend_score_60m` | 60m 视角趋势得分 | 60m bar 上的 `pa_trend_strength_20` / 100 | `01/05` |
| `mtf_trend_score_30m` | 30m 视角 | 同上 | `01/05` |
| `mtf_align_flag` | HTF / MTF 方向一致 | `sign(htf) == sign(mtf) and abs(both) >= 0.3` | `01/05` |
| `ltf_signal_ready_5m` | 5m 出现信号 bar | 5m bar 的 `pa_bull_reversal / pa_bear_reversal` | `01/05` |
| `mtf_conflict_score` | 多周期分歧度 | std(htf, mtf_60, mtf_30) | `01/05` |

> **实现提示**: 用 `cta/strategy/brooks/core/features/adapter.py` 的 offline-as-of 接口做时间戳对齐（以 LTF 为时钟 left-merge）。

---

## 16. 市场状态机 / Regime Labels

把 §3 / §6 / §13 的分数聚合为**离散 regime 标签**，供 `05_regime_switch_strategies/` 和门控规则使用。**bar-derivable。**

| 特征名 | 含义 | 计算方式 | 来源章节 |
|--------|------|----------|----------|
| `regime_label` | 分类：trend_up / trend_down / range / compression / expansion / transition | 阈值组合：`pa_trend_strength` & `vol_regime` & `pa_trading_range_20` | `05/03` |
| `regime_conf` | 首位 - 次位得分差 | softmax 后最大 - 次大 | `05/03` |
| `regime_age` | 自上次切换以来的 bar 数 | cumcount 到本 regime | `05/03`, `05/04` |
| `transition_flag` | 当前处于过渡 | `regime_label == 'transition' or regime_age < 5` | `05/04` |
| `transition_risk` | 过渡风险分 | `(1 - regime_conf) * 0.5 + vol_of_vol_zscore * 0.5` | `05/03`, `05/04` |

> **阈值建议**: trend_up 条件 `pa_trend_strength_20 > 30 且 pa_higher_high 最近 5 根 ≥ 3`；range 条件 `pa_trading_range_20 > 0.6 且 pa_doji_density_10 > 0.3`；compression 条件 `bb_width_zscore_252 < -0.8`；expansion 条件 `atr_pct_zscore_252 > 0.8`。

---

## 17. 综合评分 / Composite Scores

从原始/中间特征聚合到 0-1 / -1-1 的评分，用作策略门控或加权。**bar-derivable。**

| 特征名 | 含义 | 计算方式 | 来源章节 |
|--------|------|----------|----------|
| `trend_score` | 综合趋势得分 [-1, 1] | `tanh((slope_20_norm + adx_scaled + ma_alignment + hh_hl_bias) / 4)` | `01/01`, `05/03` |
| `compression_score` | 压缩体制分 [0, 1] | `pa_tight_range_flag_8 × 0.5 + (bb_width < 30%分位) × 0.3 + pa_inside_bar_ratio_10 × 0.2` | `05/01` |
| `expansion_score` | 扩张体制分 [0, 1] | `(atr_pct_zscore_252 > 1) × 0.5 + (range_height_atr > 2.5) × 0.5` | `05/01` |
| `breakout_mode_score` | 突破模式整体分 [0, 1] | compression 后出现突破的打分合成 | `05/02` |
| `setup_quality_score` | 形态质量分 [0, 1] | signal bar 质量 + 回调深度 + 与 EMA 距离 | `06/01` |
| `breakout_quality_score` | 突破质量分 [0, 1] | `pa_bo_body_ratio_20 × 0.4 + pa_bo_close_pos_20 × 0.3 + pa_bo_vol_ratio_20_norm × 0.3` | `06/02` |
| `context_score` | 大环境配合分 [0, 1] | 多周期一致性 + regime 匹配 + 空间充足 | `06/03` |
| `rr_score` | 潜在盈亏比分 | 与 `pa_expected_rr` 同族，标准化到 [0, 1] | `06/04` |

> **使用建议**: 规则层先用单变量阈值筛，门控层再用 score ≥ 0.6。

---

## 18. 入场/止损建议价 / Entry & Stop Hints

对每根 bar 给出"**假设此刻入场**"的建议止损 / 止盈价位，不依赖真实持仓。**bar-derivable。**

| 特征名 | 含义 | 计算方式 | 来源章节 |
|--------|------|----------|----------|
| `atr_based_stop_long` | 做多止损建议价 | `close - k × atr_14` (k=1..1.5) | `03/*`, `06/04`, `07/01` |
| `atr_based_stop_short` | 做空止损建议价 | `close + k × atr_14` | 同上 |
| `atr_based_target_long` | 做多止盈建议价 | `close + k × atr_14` (k=2..3) | `06/04` |
| `atr_based_target_short` | 做空止盈建议价 | `close - k × atr_14` | 同上 |
| `chandelier_stop_long` | 吊灯止损（多） | `rolling_high_22 - 3 × atr_14` | `03/05` |
| `chandelier_stop_short` | 吊灯止损（空） | `rolling_low_22 + 3 × atr_14` | `03/05` |
| `micro_channel_stop_long` | 微通道下沿止损 | 最近 N 根低点最低 | `03/05` |
| `micro_channel_stop_short` | 微通道上沿止损 | 最近 N 根高点最高 | `03/05` |
| `liquidity_filter_pass` | 流动性过滤通过 | `vol_ma_20 × close × multiplier ≥ adv_threshold` (bool) | `03/04` |

> **注意**：这些特征是"**候选止损/止盈价**"，非实际持仓状态；真正持仓的 `holding_bars / mfe_live / mae_live / break_even_trigger / trailing_stop_price` 见 §19。

---

## 19. 未来特征 / Future Features

**下列特征依赖 bar 之外的数据**（成交日志 / 组合状态 / 合约元数据 / 成本表 / ML 模型 / 实盘系统），不属于当前 `run_generate.py` 的 bar-derivable 范畴，**将来按需接入**。分组整理：

### 19.1 合约与数据工程元数据（来自 `cta/config/futures_meta.py` + rollover 逻辑）

| 特征名 | 含义 | 来源章节 |
|--------|------|----------|
| `active_contract` | 当前主力合约代码 | `08/01` |
| `active_by_oi` / `active_by_vol` | OI / 成交量主力判定 | `08/02` |
| `days_to_expiry` | 距到期日天数 | `08/02` |
| `roll_window` | 展期时间窗标志 | `08/02` |
| `gap_size` | 换月跳空 | `08/01` |
| `adj_factor` | 连续合约复权因子 | `08/01` |
| `continuity_score` | 拼接无断点检查 | `08/01` |

### 19.2 成本与撮合模型（来自 `cta/config/futures_meta.py` + 成本模型）

| 特征名 | 含义 | 来源章节 |
|--------|------|----------|
| `commission_rate` | 手续费率 | `08/03` |
| `tick_size` | 最小跳动 | `08/03` |
| `slippage_ticks` | 回测滑点 ticks | `08/03` |
| `impact_bps` | 冲击成本 bps | `08/03` |
| `roll_cost` | 展期总成本 | `08/02`, `08/03` |
| `fill_rule` | 成交价规则 | `08/04` |

### 19.3 仓位与风控运行时（来自持仓 + equity 曲线）

| 特征名 | 含义 | 来源章节 |
|--------|------|----------|
| `risk_pct` | 单笔风险 / 权益 | `07/01` |
| `lots_atr` | 按 ATR 反推手数 | `07/01` |
| `vol_target_ann` / `realized_vol_ann` / `vol_scaling` | 波动率目标 | `07/02` |
| `sector_exposure` | 板块净敞口 | `07/03` |
| `correlation_cluster` | 相关簇标签 | `07/03` |
| `current_drawdown` / `peak_equity` | 回撤状态 | `07/04` |
| `consecutive_losing_days` | 连亏天数 | `07/04` |
| `trailing_sharpe_30d` | 滚动 Sharpe | `07/04` |
| `max_allowed_risk_pct` | DD 档位风险上限 | `07/04` |

### 19.4 组合层（来自多策略 panel）

| 特征名 | 含义 | 来源章节 |
|--------|------|----------|
| `strategy_weight` | 策略组合权重 | `07/05` |
| `portfolio_risk_pct` | 组合总风险 | `07/05` |
| `rebalance_frequency` | 再平衡频率 | `07/05` |
| `strategy_correlation_60d` | 策略间 PnL 相关 | `07/05` |

### 19.5 持仓运行时（依赖真实 entry）

| 特征名 | 含义 | 来源章节 |
|--------|------|----------|
| `holding_bars` | 已持仓 bar 数 | `03/05` |
| `mfe_live` / `mae_live` | 实时浮盈 / 浮亏最大 | `03/05`, `09/03` |
| `break_even_trigger` | 盈亏平衡移动触发 | `03/05` |
| `trailing_stop_price` | 实时移动止盈 | `03/05`, `10/01` |
| `take_profit_price` | 静态止盈目标 | `03/*`, `04/*` |

### 19.6 交易日志与标签（来自 trade_log）

| 特征名 | 含义 | 来源章节 |
|--------|------|----------|
| `mfe` / `mae` | 交易结束 MFE / MAE | `08/05`, `09/03` |
| `net_pnl` | 扣成本 PnL | `08/05` |
| `label_trade_pos` | ML 二分类标签 | `09/01` |
| `mfe_target_atr` / `mae_target_atr` | ML 回归标签 | `09/03` |

### 19.7 ML 模型产出

| 特征名 | 含义 | 来源章节 |
|--------|------|----------|
| `ml_opportunity_prob` | 机会评分模型输出 | `06/05`, `09/01` |
| `ml_gate_prob` | filter 门控输出 | `09/01` |
| `regime_prob_*` | 状态分类器概率 | `09/02`, `05/03` |
| `stability_score` | regime 稳定度 | `09/02` |

### 19.8 特征基础设施（feature store）

| 特征名 | 含义 | 来源章节 |
|--------|------|----------|
| `feature_version` | git + cfg hash | `09/04` |
| `schema_hash` | 列集合 md5 | `09/04` |
| `freshness_lag` | 最新特征落后实盘 | `09/04` |

### 19.9 实盘运维监控

| 特征名 | 含义 | 来源章节 |
|--------|------|----------|
| `fill_rate` / `reject_rate` | 订单成交 / 被拒比例 | `10/02` |
| `avg_latency_ms` | 下单到成交延迟 | `10/02` |
| `position_delta` | 本地 vs 券商差异 | `10/02`, `10/03` |
| `data_freshness_sec` | 最新 bar 延迟 | `10/03` |
| `daily_pnl` / `live_vs_bt_diff` / `slippage_realized` | 复盘对照 | `10/04` |
| `strategy_heartbeat` | 进程活性 | `10/03` |
| `strategy_version` | 策略 SemVer | `10/05` |

> **规划**：§13-§18 按 `cta/cta_skills/` skill 顺序在 `run_generate.py` 中增量接入；§19 按模块独立接入（`cta/strategy/common/cost/`、`.../risk/`、`.../ml/`、`.../live/` 等）。

---

## 输出文件结构

```
cta/feature/feature/
├── day/
│   ├── {symbol}.parquet           # 每品种一个文件（含全部时序特征）
│   └── _all_symbols.parquet       # 全品种合并（额外含截面特征）
└── minute/
    ├── {symbol}.parquet
    └── _all_symbols.parquet
```

## 使用示例

```python
import pandas as pd

# 读取单品种全部特征
df = pd.read_parquet("cta/feature/feature/day/CU0.parquet")

# 只读部分列（parquet 列式存储，IO 极小）
df = pd.read_parquet(
    "cta/feature/feature/day/_all_symbols.parquet",
    columns=["symbol", "datetime", "close", "rsi_14", "pa_trend_strength_20"]
)

# 筛选品种 + 时间
df = df[(df["symbol"] == "CU0") & (df["datetime"] >= "2020-01-01")]

# 读取 Al Brooks 交易上下文特征
cols = ["symbol", "datetime", "close",
        "pa_always_in_dir", "pa_expected_rr", "pa_scalp_score",
        "pa_range_zone_20", "pa_bo_body_ratio_20"]
df = pd.read_parquet("cta/feature/feature/day/_all_symbols.parquet", columns=cols)
```
