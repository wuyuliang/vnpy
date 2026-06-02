"""`python -m cta.model.eval` entry shim — 复用 train 产物重跑 OOT。

详见 [`cta/model/eval_only_cli.py`](./eval_only_cli.py)。

历史背景（2026-05-25）：把 `cta.model.model_pipeline` 拆成 train + eval 两路，
让用户改 cfg 时不必重训。`train` 走原 `cta.model.model_pipeline`，`eval` 走本入口。
"""
from __future__ import annotations

import sys

from cta.model.eval_only_cli import main


if __name__ == "__main__":
    sys.exit(main())
