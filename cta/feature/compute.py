"""
特征计算主入口

用法：
    # 计算所有品种日线全部特征
    python -m cta.feature.compute --interval day --output cta/feature/output

    # 计算单品种
    python -m cta.feature.compute --interval day --symbol CU0 --output cta/feature/output

    # 只计算时序特征（不含截面）
    python -m cta.feature.compute --interval day --no-cross-section
"""
import argparse
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from cta.feature.loader import load_symbols_ranked, load_day_data
from cta.feature.trend import compute_trend_features
from cta.feature.momentum import compute_momentum_features
from cta.feature.volatility import compute_volatility_features
from cta.feature.volume import compute_volume_features
from cta.feature.pattern import compute_pattern_features
from cta.feature.calendar_feat import compute_calendar_features
from cta.feature.price_action import compute_price_action_features
from cta.feature.price_action_context import compute_price_action_context_features
from cta.feature.price_action_advanced import compute_price_action_advanced_features
from cta.feature.stats_feat import compute_stats_features
from cta.feature.cross_section import compute_cross_section_features
from cta.feature.volatility_regime import compute_volatility_regime_features
from cta.feature.price_action_supplement import compute_price_action_supplement_features
from cta.feature.multi_timeframe import compute_multi_timeframe_features
from cta.feature.regime import compute_regime_features
from cta.feature.composite import compute_composite_features
from cta.feature.entry_stop import compute_entry_stop_features

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

CTA_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = CTA_ROOT / "feature" / "output"


def compute_single_symbol_features(df: pd.DataFrame,
                                    interval: str = "day") -> pd.DataFrame:
    """
    计算单品种的所有时序特征（不含截面特征）

    Parameters
    ----------
    df : 原始 OHLCV 数据
    interval : canonical 频率名 day / minute / minute5 / minute15 / minute30 / minute60
               - minute 才追加 §11 同比特征
               - §15 多周期特征用该值决定 LTF 检测是否启用
    """
    # 确保必需列存在，缺失则用默认值填充
    if "open_interest" not in df.columns:
        df = df.copy()
        df["open_interest"] = 0

    parts = [
        compute_trend_features(df),
        compute_momentum_features(df),
        compute_volatility_features(df),
        compute_volume_features(df),
        compute_pattern_features(df),
        compute_calendar_features(df),
        compute_price_action_features(df),
        compute_price_action_context_features(df),
        compute_price_action_advanced_features(df),
        compute_stats_features(df),
        # §13 波动率体制补充
        compute_volatility_regime_features(df),
        # §14 Al Brooks 形态补充
        compute_price_action_supplement_features(df),
        # §15 多周期对齐（需要 datetime 列，day/minute 都兼容）
        compute_multi_timeframe_features(df, interval=interval),
        # §18 入场/止损建议价（只依赖 OHLCV，可单独算）
        compute_entry_stop_features(df),
    ]

    # 分钟级（1-min）才生成 tod 同比特征；
    # minute5/minute15/minute30/minute60 的 bar 已跨多分钟，内部再按 1/3/5/10/20
    # 分钟粒度聚合没有物理意义，显式不加，避免 "静默缺失"。
    if interval == "minute":
        from cta.feature.minute_tod import compute_minute_tod_features
        tod_feat = compute_minute_tod_features(df)
        if not tod_feat.empty and len(tod_feat.columns) > 0:
            parts.append(tod_feat)

    features = pd.concat(parts, axis=1)

    # §16 / §17 依赖上游其它类特征，分两步：
    #   先把 parts 拼好后再喂给 regime / composite
    with_upstream = pd.concat([df, features], axis=1)
    regime_feat = compute_regime_features(with_upstream)
    composite_feat = compute_composite_features(
        pd.concat([with_upstream, regime_feat], axis=1)
    )
    features = pd.concat([features, regime_feat, composite_feat], axis=1)

    # 全局清理 inf → NaN，避免下游模型异常
    features.replace([np.inf, -np.inf], np.nan, inplace=True)
    # 保留原始列
    return pd.concat([df, features], axis=1)


def compute_all_features(
    interval: str = "day",
    symbols: list[str] | None = None,
    with_cross_section: bool = True,
    output_dir: Path | None = None,
) -> pd.DataFrame:
    """
    计算所有品种特征

    Parameters
    ----------
    interval : 数据频率 ("day" 或 "minute")
    symbols : 指定品种列表，None 表示全部
    with_cross_section : 是否计算截面特征
    output_dir : 输出目录，None 表示不保存文件

    Returns
    -------
    包含全部特征的 DataFrame
    """
    # 按 research_rank 升序遍历，确保所有批量入口顺序一致
    symbols_df = load_symbols_ranked(symbols_filter=symbols)

    all_dfs = []
    total = len(symbols_df)

    from cta.feature.loader import normalize_interval, load_intraday_data
    canon = normalize_interval(interval)

    for idx, (_, row) in enumerate(symbols_df.iterrows(), 1):
        symbol = row["symbol"]
        logger.info(f"[{idx}/{total}] 计算 {symbol} 的时序特征...")
        try:
            if canon == "day":
                df = load_day_data(symbol)
            else:
                df = load_intraday_data(symbol, row["exchange"], interval=canon)

            df_with_feat = compute_single_symbol_features(df, interval=canon)
            all_dfs.append(df_with_feat)

            # 按品种保存
            if output_dir:
                output_dir.mkdir(parents=True, exist_ok=True)
                out_path = output_dir / f"{symbol}.parquet"
                df_with_feat.to_parquet(out_path, index=False)
                logger.info(f"  -> 已保存 {out_path}")

        except FileNotFoundError as e:
            logger.warning(f"  跳过 {symbol}: {e}")
        except Exception as e:
            logger.error(f"  计算 {symbol} 出错: {e}")

    if not all_dfs:
        raise RuntimeError("没有成功计算任何品种的特征")

    all_data = pd.concat(all_dfs, ignore_index=True)
    logger.info(f"时序特征计算完成，共 {len(all_data)} 行, {len(all_data.columns)} 列")

    # 截面特征
    if with_cross_section:
        logger.info("计算截面特征...")
        all_data = compute_cross_section_features(all_data)
        logger.info(f"截面特征计算完成，共 {len(all_data.columns)} 列")

    # 保存合并结果
    if output_dir:
        merged_path = output_dir / "all_features.parquet"
        all_data.to_parquet(merged_path, index=False)
        logger.info(f"合并结果已保存: {merged_path}")

    return all_data


def main():
    parser = argparse.ArgumentParser(description="CTA 特征批量计算")
    parser.add_argument(
        "--interval", default="day",
        choices=["day", "minute", "minute5", "minute15", "minute30", "minute60",
                 "5min", "15min", "30min", "60min"],
        help="数据频率（接受规范名 minute5/... 与旧名 5min/...）",
    )
    parser.add_argument("--symbol", nargs="*", default=None,
                        help="指定品种（可多个），不指定则全部")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT),
                        help="输出目录")
    parser.add_argument("--no-cross-section", action="store_true",
                        help="跳过截面特征计算")
    args = parser.parse_args()

    compute_all_features(
        interval=args.interval,
        symbols=args.symbol,
        with_cross_section=not args.no_cross_section,
        output_dir=Path(args.output),
    )


if __name__ == "__main__":
    main()
