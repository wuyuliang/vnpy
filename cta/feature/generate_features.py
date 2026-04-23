"""
特征生成脚本 —— 批量为天级和分钟级数据生成特征并保存为 parquet

输出目录: cta/feature/feature/
文件命名规则:
    天级(单品种):   cta/feature/feature/day/{symbol}.parquet        例: day/CU0.parquet
    天级(截面合并):  cta/feature/feature/day/_all_symbols.parquet
    分钟级(单品种): cta/feature/feature/minute/{symbol}.parquet     例: minute/CU0.parquet
    分钟级(截面):   cta/feature/feature/minute/_all_symbols.parquet

用法:
    # 生成全部（天级 + 分钟级）
    python3 -m cta.feature.generate_features

    # 只生成天级
    python3 -m cta.feature.generate_features --interval day

    # 只生成分钟级
    python3 -m cta.feature.generate_features --interval minute

    # 指定品种
    python3 -m cta.feature.generate_features --symbol CU0 RB0

    # 跳过截面特征（速度更快）
    python3 -m cta.feature.generate_features --no-cross-section

为什么选 parquet:
    1. vnpy 生态中 parquet 是常用的数据交换格式，可直接被 pandas 读取
    2. 列式存储，读取部分列时 IO 极小，适合特征选择场景
    3. 内置压缩，磁盘占用远小于 CSV
    4. 保留列类型（int/float/datetime），加载后无需二次转换
    5. 后续可直接对接 vnpy 回测引擎、DuckDB 查询、或转入数据库
"""
import argparse
import logging
import time
from pathlib import Path

import pandas as pd

from cta.feature.loader import (
    load_symbols_ranked,
    load_day_data,
    load_minute_data,
)
from cta.feature.compute import compute_single_symbol_features
from cta.feature.cross_section import compute_cross_section_features

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

CTA_ROOT = Path(__file__).resolve().parent.parent
FEATURE_DIR = CTA_ROOT / "data" / "feature"


def generate_day_features(
    symbols: list[str] | None = None,
    with_cross_section: bool = True,
) -> None:
    """生成天级特征"""
    out_dir = FEATURE_DIR / "day"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 按 research_rank 升序遍历
    symbols_df = load_symbols_ranked(symbols_filter=symbols)

    total = len(symbols_df)
    all_dfs: list[pd.DataFrame] = []
    t0 = time.time()

    for idx, (_, row) in enumerate(symbols_df.iterrows(), 1):
        symbol = row["symbol"]
        out_path = out_dir / f"{symbol}.parquet"
        try:
            logger.info(f"[Day {idx}/{total}] {symbol} ...")
            df = load_day_data(symbol)
            df_feat = compute_single_symbol_features(df, interval="day")
            df_feat.to_parquet(out_path, index=False)
            all_dfs.append(df_feat)
            logger.info(
                f"  -> {out_path.name}  "
                f"({len(df_feat)} rows, {len(df_feat.columns)} cols)"
            )
        except FileNotFoundError as e:
            logger.warning(f"  跳过 {symbol}: {e}")
        except Exception as e:
            logger.error(f"  {symbol} 出错: {e}", exc_info=True)

    if not all_dfs:
        logger.warning("天级: 没有成功计算任何品种")
        return

    # 截面特征 + 合并保存
    all_data = pd.concat(all_dfs, ignore_index=True)
    if with_cross_section:
        logger.info("计算天级截面特征 ...")
        all_data = compute_cross_section_features(all_data)

    merged_path = out_dir / "_all_symbols.parquet"
    all_data.to_parquet(merged_path, index=False)

    elapsed = time.time() - t0
    logger.info(
        f"天级特征完成: {len(all_dfs)} 品种, "
        f"{len(all_data)} 行, {len(all_data.columns)} 列, "
        f"耗时 {elapsed:.1f}s"
    )
    logger.info(f"合并文件: {merged_path}")


def generate_minute_features(
    symbols: list[str] | None = None,
    with_cross_section: bool = True,
) -> None:
    """生成分钟级特征"""
    out_dir = FEATURE_DIR / "minute"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 按 research_rank 升序遍历；无分钟数据的品种在加载阶段 FileNotFoundError 后跳过
    ranked = load_symbols_ranked(symbols_filter=symbols)
    if ranked.empty:
        logger.warning("没有匹配到任何品种")
        return

    total = len(ranked)
    all_dfs: list[pd.DataFrame] = []
    t0 = time.time()

    for idx, (_, row) in enumerate(ranked.iterrows(), 1):
        symbol = row["symbol"]
        exchange = row["exchange"]
        out_path = out_dir / f"{symbol}.parquet"
        try:
            logger.info(f"[Minute {idx}/{total}] {symbol}.{exchange} ...")
            df = load_minute_data(symbol, exchange)
            df_feat = compute_single_symbol_features(df, interval="minute")
            df_feat.to_parquet(out_path, index=False)
            all_dfs.append(df_feat)
            logger.info(
                f"  -> {out_path.name}  "
                f"({len(df_feat)} rows, {len(df_feat.columns)} cols)"
            )
        except FileNotFoundError as e:
            logger.warning(f"  跳过 {symbol}: {e}")
        except Exception as e:
            logger.error(f"  {symbol} 出错: {e}", exc_info=True)

    if not all_dfs:
        logger.warning("分钟级: 没有成功计算任何品种")
        return

    all_data = pd.concat(all_dfs, ignore_index=True)
    if with_cross_section:
        logger.info("计算分钟级截面特征 ...")
        all_data = compute_cross_section_features(all_data)

    merged_path = out_dir / "_all_symbols.parquet"
    all_data.to_parquet(merged_path, index=False)

    elapsed = time.time() - t0
    logger.info(
        f"分钟级特征完成: {len(all_dfs)} 品种, "
        f"{len(all_data)} 行, {len(all_data.columns)} 列, "
        f"耗时 {elapsed:.1f}s"
    )
    logger.info(f"合并文件: {merged_path}")


def main():
    parser = argparse.ArgumentParser(
        description="CTA 特征批量生成（天级 + 分钟级）"
    )
    parser.add_argument(
        "--interval",
        choices=["day", "minute", "all"],
        default="all",
        help="生成哪个频率的特征 (默认 all)",
    )
    parser.add_argument(
        "--symbol",
        nargs="*",
        default=None,
        help="指定品种（可多个），不指定则全部",
    )
    parser.add_argument(
        "--no-cross-section",
        action="store_true",
        help="跳过截面特征",
    )
    args = parser.parse_args()

    cs = not args.no_cross_section

    logger.info("=" * 60)
    logger.info("CTA 特征生成开始")
    logger.info(f"输出目录: {FEATURE_DIR}")
    logger.info(f"频率: {args.interval}  截面特征: {cs}")
    logger.info("=" * 60)

    if args.interval in ("day", "all"):
        generate_day_features(symbols=args.symbol, with_cross_section=cs)

    if args.interval in ("minute", "all"):
        generate_minute_features(symbols=args.symbol, with_cross_section=cs)

    logger.info("全部完成!")


if __name__ == "__main__":
    main()
