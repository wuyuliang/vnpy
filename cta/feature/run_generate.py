#!/usr/bin/env python3
"""
特征生成启动脚本 —— 自动处理路径，直接运行即可

用法（在任意目录下）:
    python3 cta/feature/run_generate.py                          # 天级+分钟级全部
    python3 cta/feature/run_generate.py --interval day           # 仅天级
    python3 cta/feature/run_generate.py --interval minute        # 仅分钟级
    python3 cta/feature/run_generate.py --symbol CU0 RB0        # 指定品种
    python3 cta/feature/run_generate.py --no-cross-section       # 跳过截面特征（更快）
"""
import sys
from pathlib import Path

# 把 vnpy 项目根目录加入 sys.path，确保 cta 包可被导入
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from cta.feature.generate_features import main  # noqa: E402

if __name__ == "__main__":
    main()
