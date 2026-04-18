"""
按研究排名顺序批量生成特征，支持断点续跑、多频率并存

用法:
    # 跑所有频率（自动发现 data/ 下所有可用频率）
    python3 -m cta.feature.run_all_features

    # 只跑天级
    python3 -m cta.feature.run_all_features --interval day

    # 只跑分钟级
    python3 -m cta.feature.run_all_features --interval minute

    # 多个频率
    python3 -m cta.feature.run_all_features --interval minute 5min 15min

    # 只跑盘中所有频率（minute + 5min/15min/30min/60min 中已有的）
    python3 -m cta.feature.run_all_features --interval intraday

    # 所有品种跑完后单独跑截面特征
    python3 -m cta.feature.run_all_features --cross-section

支持的频率: day, minute, 5min, 15min, 30min, 60min
对应数据目录: cta/data/{interval}/{symbol}.{exchange}/YYYY-MM-DD.parquet

流程:
    1. 读取 symbols_research_ranking.csv 获取品种排名
    2. 读取 symbols_feature_finished.csv 跳过已完成 (symbol, interval) 组合
    3. 按 research_rank 从小到大依次生成特征
    4. 每个品种完成后立即写入 finished csv（支持断点续跑）
    5. 截面特征需等所有品种跑完后单独执行 --cross-section
"""
import argparse
import logging
import time
from pathlib import Path

import pandas as pd

from cta.feature.loader import (
    INTRADAY_INTERVALS,
    load_day_data,
    load_intraday_data,
    list_available_intervals,
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
RANKING_CSV = Path(__file__).resolve().parent / "symbols_research_ranking.csv"
FINISHED_CSV = Path(__file__).resolve().parent / "symbols_feature_finished.csv"

# 不同频率的"成功"文件大小阈值 (字节)
# 大于阈值视为 success，否则 empty（用于区分有数据 vs 无数据/空数据）
SIZE_THRESHOLDS = {
    "day": 1 * 1024 * 1024,         # 1 MB
    "minute": 100 * 1024 * 1024,    # 100 MB
    "5min": 20 * 1024 * 1024,       # 20 MB
    "15min": 10 * 1024 * 1024,      # 10 MB
    "30min": 5 * 1024 * 1024,       # 5 MB
    "60min": 3 * 1024 * 1024,       # 3 MB
}
DEFAULT_THRESHOLD = 1 * 1024 * 1024


# ---------------- finished csv 读写 ----------------

def load_finished() -> pd.DataFrame:
    """读取 finished csv，自动兼容旧格式（无 interval 列时默认为 minute）"""
    if not FINISHED_CSV.exists():
        return pd.DataFrame(columns=["symbol", "exchange", "interval", "status"])
    df = pd.read_csv(FINISHED_CSV)
    if "interval" not in df.columns:
        df["interval"] = "minute"
        df = df[["symbol", "exchange", "interval", "status"]]
    return df


def save_finished(df: pd.DataFrame) -> None:
    df.to_csv(FINISHED_CSV, index=False)


def get_status(symbol: str, interval: str) -> str:
    """检查输出 parquet 文件状态: success (>阈值) 或 empty"""
    path = FEATURE_DIR / interval / f"{symbol}.parquet"
    threshold = SIZE_THRESHOLDS.get(interval, DEFAULT_THRESHOLD)
    if path.exists() and path.stat().st_size > threshold:
        return "success"
    return "empty"


# ---------------- 单品种单频率处理 ----------------

def process_symbol_interval(symbol: str, exchange: str, interval: str) -> None:
    """生成单品种单频率特征"""
    out_dir = FEATURE_DIR / interval
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{symbol}.parquet"

    if interval == "day":
        if out_path.exists():
            logger.info(f"  [{interval}] {symbol} 已存在，跳过")
            return
        try:
            df = load_day_data(symbol)
            df_feat = compute_single_symbol_features(df, interval="day")
            df_feat.to_parquet(out_path, index=False)
            logger.info(
                f"  [{interval}] {out_path.name}: "
                f"{len(df_feat)} rows, {len(df_feat.columns)} cols"
            )
        except FileNotFoundError as e:
            logger.warning(f"  [{interval}] 跳过 {symbol}: {e}")
        except Exception as e:
            logger.error(f"  [{interval}] {symbol} 出错: {e}", exc_info=True)
    else:
        # 任意盘中频率
        try:
            df = load_intraday_data(symbol, exchange, interval=interval)
            # compute 函数对 minute 启用同比特征，其它盘中频率走通用流程
            compute_interval = "minute" if interval == "minute" else "day"
            df_feat = compute_single_symbol_features(df, interval=compute_interval)
            df_feat.to_parquet(out_path, index=False)
            logger.info(
                f"  [{interval}] {out_path.name}: "
                f"{len(df_feat)} rows, {len(df_feat.columns)} cols"
            )
        except FileNotFoundError as e:
            logger.warning(f"  [{interval}] 跳过 {symbol}: {e}")
        except Exception as e:
            logger.error(f"  [{interval}] {symbol} 出错: {e}", exc_info=True)


# ---------------- 截面特征 ----------------

def run_cross_section(intervals: list[str]) -> None:
    """所有品种跑完后，统一计算截面特征并合并"""
    for sub in intervals:
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


# ---------------- 频率参数解析 ----------------

def resolve_intervals(arg_intervals: list[str]) -> list[str]:
    """
    解析 --interval 参数:
      "all"      -> ["day"] + 所有可用盘中频率
      "intraday" -> 所有可用盘中频率（不含 day）
      其它       -> 直接使用，按已知顺序排序
    """
    if not arg_intervals or "all" in arg_intervals:
        return ["day"] + list_available_intervals()
    if "intraday" in arg_intervals:
        return list_available_intervals()
    # 按 [day, minute, 5min, 15min, 30min, 60min] 顺序去重
    order = ["day"] + INTRADAY_INTERVALS
    seen = set()
    result = []
    for it in order:
        if it in arg_intervals and it not in seen:
            seen.add(it)
            result.append(it)
    # 用户指定但不在 known 列表里的也保留
    for it in arg_intervals:
        if it not in seen:
            seen.add(it)
            result.append(it)
    return result


# ---------------- 主流程 ----------------

def main():
    parser = argparse.ArgumentParser(
        description="按研究排名顺序批量生成多频率特征（支持断点续跑）"
    )
    parser.add_argument(
        "--interval",
        nargs="+",
        default=["all"],
        help="频率列表: day / minute / 5min / 15min / 30min / 60min / intraday / all (默认 all)",
    )
    parser.add_argument(
        "--cross-section",
        action="store_true",
        help="只计算截面特征（所有品种跑完后执行）",
    )
    args = parser.parse_args()

    intervals = resolve_intervals(args.interval)

    logger.info("=" * 60)
    logger.info("CTA 特征批量生成（按研究排名）")
    logger.info(f"输出目录: {FEATURE_DIR}")
    logger.info(f"处理频率: {intervals}")
    logger.info("=" * 60)

    # 只跑截面
    if args.cross_section:
        run_cross_section(intervals)
        logger.info("截面特征计算完成!")
        return

    # 读取排名
    ranking = pd.read_csv(RANKING_CSV)
    ranking = ranking.sort_values("research_rank").reset_index(drop=True)

    # 读取已完成列表（按 (symbol, interval) 跟踪）
    finished = load_finished()
    finished_set = set(zip(finished["symbol"], finished["interval"]))

    # 各频率独立统计/处理
    t_total = time.time()
    for interval in intervals:
        if interval == "day":
            # day 不依赖 finished csv，通过文件存在跳过
            todo = ranking.copy()
            logger.info(f"[{interval}] 模式: day only（按文件存在跳过）")
        else:
            todo = ranking[
                ~ranking["symbol"].apply(lambda s: (s, interval) in finished_set)
            ].reset_index(drop=True)
            logger.info(
                f"[{interval}] 排名: {len(ranking)}, "
                f"已完成: {len(ranking) - len(todo)}, 待处理: {len(todo)}"
            )

        if todo.empty:
            logger.info(f"[{interval}] 全部品种已完成")
            continue

        for _, row in todo.iterrows():
            symbol = row["symbol"]
            exchange = row["exchange"]
            rank = row["research_rank"]

            logger.info(f"[{interval}][Rank {rank}] {symbol} ({exchange}) ...")
            t0 = time.time()
            process_symbol_interval(symbol, exchange, interval)

            if interval != "day":
                status = get_status(symbol, interval)
                new_row = pd.DataFrame([{
                    "symbol": symbol,
                    "exchange": exchange,
                    "interval": interval,
                    "status": status,
                }])
                finished = pd.concat([finished, new_row], ignore_index=True)
                save_finished(finished)
                finished_set.add((symbol, interval))
                logger.info(
                    f"  {symbol} 完成, 状态: {status}, 耗时 {time.time()-t0:.1f}s"
                )
            else:
                logger.info(f"  {symbol} 完成, 耗时 {time.time()-t0:.1f}s")

    total_elapsed = time.time() - t_total
    logger.info("=" * 60)
    logger.info(f"全部处理完成! 耗时 {total_elapsed:.1f}s")
    logger.info("如需计算截面特征请执行: python3 -m cta.feature.run_all_features --cross-section")


if __name__ == "__main__":
    main()
