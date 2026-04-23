"""CTA Skills —— cta/cta_skills/*.md 的代码实现包。

目录约定
--------
每一章 md（00_overview_methodology / 01_market_regime / ...）对应本包下一个
子包：

    cta_skills/00_overview_methodology/  <-> cta/skills/overview/
    cta_skills/01_market_regime/         <-> cta/skills/market_regime/
    cta_skills/02_price_action/          <-> cta/skills/price_action/
    ...

每个子包下：
- 一个 py 文件对应 md 里第 6 节的代码模块设计
- tests/ 下放单元测试
- 共享配置文件统一放 cta/skills/configs/
- 运行产物（回测 summary、筛选结果、临时快照）统一落 cta/skills/output/

多周期兼容
----------
所有工具对 day / minute / minute5 / minute15 / minute30 / minute60 一视同仁，
不在代码里 hardcode 周期名。
"""
from __future__ import annotations

from pathlib import Path

SKILLS_ROOT: Path = Path(__file__).resolve().parent
CONFIG_DIR: Path = SKILLS_ROOT / "configs"
OUTPUT_DIR: Path = SKILLS_ROOT / "output"

# 项目根 = cta/.. ，用于反查 cta/feature、cta/config、cta/report 等
PROJECT_ROOT: Path = SKILLS_ROOT.parent.parent

# 规范频率列表（与 cta/feature/loader.py 对齐）
CANON_INTERVALS: tuple[str, ...] = (
    "day", "minute60", "minute30", "minute15", "minute5", "minute",
)

__all__ = [
    "SKILLS_ROOT",
    "CONFIG_DIR",
    "OUTPUT_DIR",
    "PROJECT_ROOT",
    "CANON_INTERVALS",
]
