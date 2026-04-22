from __future__ import annotations

import argparse
from pathlib import Path

from tight_range_vnpy.sqlite_feature_service import FeatureGenerationService


def main() -> None:
    parser = argparse.ArgumentParser(description="遍历 day 目录，生成天级别特征到 SQLite")
    parser.add_argument("--csv-dir", required=True, help="包含多个品种 csv 的目录，例如 /root/day")
    parser.add_argument("--db-path", required=True, help="输出 SQLite 文件路径，例如 /root/vnpy/cta/tight_range_vnpy/data/tight_range_features.sqlite")
    args = parser.parse_args()

    service = FeatureGenerationService(args.db_path)
    result = service.build_from_dir(args.csv_dir)
    print(f"[DONE] files={result.file_count} total_rows={result.row_count} db={args.db_path}")


if __name__ == "__main__":
    main()
