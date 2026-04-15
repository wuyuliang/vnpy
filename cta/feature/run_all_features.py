"""
按研究排名顺序批量生成特征，支持断点续跑

用法:
    # 按排名顺序跑所有未完成品种（天级 + 分钟级，不含截面）
    python3 -m cta.feature.run_all_features

    # 只跑天级
    python3 -m cta.feature.run_all_features --interval day

    # 只跑分钟级
    python3 -m cta.feature.run_all_features --interval minute

    # 最后单独跑截面特征（所有品种跑完后执行）
    python3 -m cta.feature.run_all_features --cross-section

流程:
    1. 读取 symbols_research_ranking.csv 获取品种排名
    2. 读取 symbols_feature_finished.csv 跳过已完成品种
    3. 按 research_rank 从小到大依次生成特征
    4. 每个品种完成后立即写入 finished csv（支持断点续跑）
    5. 截面特征需等所有品种跑完后单独执行 --cross-section
"""
import argparse
import logging
import time
from pathlib import Path

import pandas as pd

from cta.feature.loader import load_day_data, load_minute_data, load_symbols
from cta.feature.compute import compute_single_symbol_features
from cta.feature.cross_section import compute_cross_section_features

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

CTA_ROOT = Path(__file__).resolve().parent.parent
FEATURE_DIR = CTA_ROOT / "data" / "feature"
RANKING_CSV = Path(__file__).resolve().parent / "symbols_research_ranking.csv"
FINISHED_CSV = Path(__file__).resolve().parent / "symbols_feature_finished.csv"

SIZE_THRESHOLD = 100 * 1024 * 1024  # 100 MB


def load_finished() -> pd.DataFrame:
    if FINISHED_CSV.exists():
        return pd.read_csv(FINISHED_CSV)
    return pd.DataFrame(columns=["symbol", "exchange", "status"])


def save_finished(df: pd.DataFrame) -> None:
    df.to_csv(FINISHED_CSV, index=False)


def get_minute_status(symbol: str) -> str:
    """检查分钟级 parquet 文件状态: success (>100M) 或 empty"""
    path = FEATURE_DIR / "minute" / f"{symbol}.parquet"
    if path.exists() and path.stat().st_size > SIZE_THRESHOLD:
        return "success"
    return "empty"


def run_single_symbol(symbol: str, exchange: str, interval: str) -> None:
    """生成单品种的天级和/或分钟级特征"""

    if interval in ("day", "all"):
        day_dir = FEATURE_DIR / "day"
        day_dir.mkdir(parents=True, exist_ok=True)
        out_path = day_dir / f"{symbol}.parquet"
        if out_path.exists():
            logger.info(f"  [Day] {symbol} 已存在，跳过")
        else:
            try:
                df = load_day_data(symbol)
                df_feat = compute_single_symbol_features(df, interval="day")
                df_feat.to_parquet(out_path, index=False)
                logger.info(
                    f"  [Day] {out_path.name}: "
                    f"{len(df_feat)} rows, {len(df_feat.columns)} cols"
                )
            except FileNotFoundError as e:
                logger.warning(f"  [Day] 跳过 {symbol}: {e}")
            except Exception as e:
                logger.error(f"  [Day] {symbol} 出错: {e}", exc_info=True)

    if interval in ("minute", "all"):
        min_dir = FEATURE_DIR / "minute"
        min_dir.mkdir(parents=True, exist_ok=True)
        out_path = min_dir / f"{symbol}.parquet"
        try:
            df = load_minute_data(symbol, exchange)
            df_feat = compute_single_symbol_features(df, interval="minute")
            df_feat.to_parquet(out_path, index=False)
            logger.info(
                f"  [Minute] {out_path.name}: "
                f"{len(df_feat)} rows, {len(df_feat.columns)} cols"
            )
        except FileNotFoundError as e:
            logger.warning(f"  [Minute] 跳过 {symbol}: {e}")
        except Exception as e:
            logger.error(f"  [Minute] {symbol} 出错: {e}", exc_info=True)


def run_cross_section() -> None:
    """所有品种跑完后，统一计算截面特征并合并"""

    for sub in ("day", "minute"):
        sub_dir = FEATURE_DIR / sub
        if not sub_dir.exists():
            continue
        files = sorted(f for f in sub_dir.glob("*.parquet") if not f.name.startswith("_"))
        if not files:
            logger.warning(f"[{sub}] 没有找到品种 parquet 文件")
            continue

        logger.info(f"[{sub}] 加载 {len(files)} 个品种 parquet ...")
        t0 = time.time()
        dfs = [pd.read_parquet(f) for f in files]
        all_data = pd.concat(dfs, ignore_index=True)
        del dfs

        logger.info(f"[{sub}] 计算截面特征 ({len(all_data)} 行) ...")
        all_data = compute_cross_section_features(all_data)

        merged_path = sub_dir / "_all_symbols.parquet"
        all_data.to_parquet(merged_path, index=False)
        elapsed = time.time() - t0
        logger.info(
            f"[{sub}] 截面特征完成: {len(all_data)} 行, "
            f"{len(all_data.columns)} 列, 耗时 {elapsed:.1f}s"
        )
        logger.info(f"  合并文件: {merged_path}")


def main():
    parser = argparse.ArgumentParser(
        description="按研究排名顺序批量生成特征（支持断点续跑）"
    )
    parser.add_argument(
        "--interval",
        choices=["day", "minute", "all"],
        default="all",
        help="生成哪个频率的特征 (默认 all)",
    )
    parser.add_argument(
        "--cross-section",
        action="store_true",
        help="只计算截面特征（所有品种跑完后执行）",
    )
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("CTA 特征批量生成（按研究排名）")
    logger.info(f"输出目录: {FEATURE_DIR}")
    logger.info("=" * 60)

    # 只跑截面
    if args.cross_section:
        run_cross_section()
        logger.info("截面特征计算完成!")
        return

    # 读取排名
    ranking = pd.read_csv(RANKING_CSV)
    ranking = ranking.sort_values("research_rank").reset_index(drop=True)

    # finished csv 只跟踪分钟级完成状态
    # --interval day 时不看 finished csv（通过文件是否存在判断跳过）
    # --interval minute / all 时才看 finished csv 跳过已完成品种
    finished = load_finished()
    finished_symbols = set(finished["symbol"].tolist())

    if args.interval in ("minute", "all"):
        todo = ranking[~ranking["symbol"].isin(finished_symbols)].reset_index(drop=True)
        logger.info(
            f"排名品种: {len(ranking)}, "
            f"分钟已完成: {len(finished_symbols)}, "
            f"待处理: {len(todo)}"
        )
    else:
        # day only: 处理所有品种，通过文件存在跳过
        todo = ranking.copy()
        logger.info(f"排名品种: {len(ranking)}, 模式: day only")

    if todo.empty:
        logger.info("所有品种已完成! 如需计算截面特征请加 --cross-section")
        return

    t_total = time.time()
    for idx, row in todo.iterrows():
        symbol = row["symbol"]
        exchange = row["exchange"]
        rank = row["research_rank"]

        logger.info(f"[Rank {rank}] {symbol} ({exchange}) ...")
        t0 = time.time()

        run_single_symbol(symbol, exchange, args.interval)

        # 只有跑了分钟级才记录到 finished csv
        if args.interval in ("minute", "all"):
            status = get_minute_status(symbol)
            new_row = pd.DataFrame([{
                "symbol": symbol,
                "exchange": exchange,
                "status": status,
            }])
            finished = pd.concat([finished, new_row], ignore_index=True)
            save_finished(finished)
            logger.info(f"  {symbol} 完成, minute状态: {status}, 耗时 {time.time()-t0:.1f}s")
        else:
            logger.info(f"  {symbol} 完成, 耗时 {time.time()-t0:.1f}s")

    total_elapsed = time.time() - t_total
    logger.info("=" * 60)
    logger.info(
        f"全部品种处理完成! 共 {len(todo)} 个, 耗时 {total_elapsed:.1f}s"
    )
    logger.info("如需计算截面特征请执行: python3 -m cta.feature.run_all_features --cross-section")


if __name__ == "__main__":
    main()
