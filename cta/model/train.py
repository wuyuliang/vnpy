"""`python -m cta.model.train` entry shim — train-only 入口。

实质上还是调 `cta.model.model_pipeline`（保留向后兼容）。本文件存在的目的：
  1) 给用户一个**对称的**调用方式：`train` ↔ `eval`，便于记忆；
  2) 未来若要从 model_pipeline 抽出"纯训练"路径，这里是入口卡口。

现在 stages：
  - `python -m cta.model.train ...args`  → 走完整 model_pipeline（train + 一次性 OOT eval）
  - `python -m cta.model.eval --from-root <dir> ...` → 复用 train 产物重跑 OOT

详见 [cta/report/change_log.md](../report/change_log.md) 2026-05-25 条目。
"""
from __future__ import annotations

import sys

from cta.model.model_pipeline import main


if __name__ == "__main__":
    main()
