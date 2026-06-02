"""紧急停单（Kill Switch）。

两种激活路径：
1. 内存：``KillSwitch.activate(reason)``（程序内自动触发，如风控降级）。
2. 信号文件：``signal_file`` 存在即视为激活，文件内容作为 reason。
   优势是运维可以从外部（cron / 监控告警 / SSH）一键禁单，无需进程交互。

集成：``KillSwitchRule(switch)`` 实现 ``cta.live.risk._BaseRule`` 协议，
可以挂入 ``RiskGuard.rules`` 让 RiskGuard 短路拒绝。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from cta.live.risk import RiskContext, RiskDecision, _BaseRule


@dataclass
class KillSwitch:
    """全局禁单开关。"""

    signal_file: Path | None = None
    _active: bool = field(default=False, init=False)
    _reason: str = field(default="", init=False)
    _cached_file_key: tuple[float, int] | None = field(default=None, init=False, repr=False)
    _cached_file_reason: str = field(default="", init=False, repr=False)

    def activate(self, reason: str = "manual") -> None:
        self._active = True
        self._reason = str(reason)

    def deactivate(self) -> None:
        self._active = False
        self._reason = ""

    def is_active(self) -> tuple[bool, str]:
        if self._active:
            return True, self._reason
        if self.signal_file:
            p = Path(self.signal_file)
            try:
                st = p.stat()
            except FileNotFoundError:
                self._cached_file_key = None
                self._cached_file_reason = ""
                return False, ""
            except Exception:  # noqa: BLE001
                return False, ""
            cache_key = (float(st.st_mtime), int(st.st_size))
            if cache_key == self._cached_file_key:
                return True, self._cached_file_reason or "signal_file"
            try:
                content = p.read_text(encoding="utf-8").strip()
            except Exception:  # noqa: BLE001
                content = ""
            self._cached_file_key = cache_key
            self._cached_file_reason = content or "signal_file"
            return True, self._cached_file_reason
        return False, ""


class KillSwitchRule(_BaseRule):
    """挂入 RiskGuard.rules 的薄包装：开关激活则拒绝任何下单。"""

    name: str = "kill_switch"

    def __init__(self, switch: KillSwitch) -> None:
        self.switch = switch

    def check(self, order: dict, ctx: RiskContext) -> RiskDecision:
        active, reason = self.switch.is_active()
        if active:
            return RiskDecision(False, f"{self.name}:{reason}")
        return RiskDecision(True, "")


__all__ = ["KillSwitch", "KillSwitchRule"]
