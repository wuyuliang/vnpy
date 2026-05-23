"""Hot-reloadable wrapper around ClusterModelRegistry for sim/live (P0-3).

为什么需要这个 wrapper
----------------------
``cta.model.training.cluster_model_registry.ClusterModelRegistry`` 一次性 load JSON
并构造静态 registry，没有热加载能力。sim/live 需要在不重启进程的前提下，**用
SIGHUP 或 reload() 调用切换模型版本**（例如 weekly retrain 后 swap），并且：

1. 启动前 schema 校验失败 → 显式 raise（不允许静默 fallback）
2. 热加载新版本失败 → 自动回滚到上一个可用版本（fail-safe）
3. 版本号 / 加载时间记入日志，便于复盘
4. **SIGHUP** 自动触发 reload（生产环境运维：``kill -HUP <pid>``）

设计原则：
- 包装现有 ``ClusterModelRegistry``，不改它的接口
- 把 "load + validate" 抽出，让 reload 复用
- 旧 registry 保留在内存里，做 cold standby（reload 失败 → 切回旧的）
- signal handler 只在主线程注册；非主线程调用 ``install_sighup_handler`` 会 raise
"""
from __future__ import annotations

import json
import logging
import signal
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cta.model.training.cluster_model_registry import ClusterModelRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RegistrySchemaIssue:
    """schema 校验失败的单个问题描述。"""
    level: str       # "error" | "warning"
    message: str
    location: str    # 例如 "entries[3].model_dir"


@dataclass(frozen=True)
class RegistrySchemaReport:
    """schema 校验报告。"""
    is_valid: bool
    errors: tuple[RegistrySchemaIssue, ...]
    warnings: tuple[RegistrySchemaIssue, ...]
    entries_count: int

    def raise_if_invalid(self) -> None:
        if not self.is_valid:
            msgs = [f"[{e.level}] {e.location}: {e.message}" for e in self.errors]
            raise ValueError(
                f"cluster_registry schema invalid ({len(self.errors)} errors):\n"
                + "\n".join(msgs)
            )


_REQUIRED_TOP_LEVEL_KEYS = ("run_tag", "group_by", "trade_side_mode", "intervals", "entries")
_REQUIRED_ENTRY_KEYS = ("interval", "group_name", "pool_name", "model_dir", "members")


def validate_registry_schema(registry_path: Path | str) -> RegistrySchemaReport:
    """校验 cluster_registry.json 的 schema 与 model_dir 是否存在。"""
    p = Path(registry_path)
    errors: list[RegistrySchemaIssue] = []
    warnings: list[RegistrySchemaIssue] = []
    entries_count = 0

    if not p.exists():
        errors.append(RegistrySchemaIssue("error", f"file not found: {p}", "<file>"))
        return RegistrySchemaReport(False, tuple(errors), (), 0)

    try:
        with open(p, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except json.JSONDecodeError as exc:
        errors.append(RegistrySchemaIssue("error", f"invalid json: {exc}", "<file>"))
        return RegistrySchemaReport(False, tuple(errors), (), 0)
    except OSError as exc:
        errors.append(RegistrySchemaIssue("error", f"read failed: {exc}", "<file>"))
        return RegistrySchemaReport(False, tuple(errors), (), 0)

    if not isinstance(payload, dict):
        errors.append(RegistrySchemaIssue("error", "top-level must be dict", "<root>"))
        return RegistrySchemaReport(False, tuple(errors), (), 0)

    for key in _REQUIRED_TOP_LEVEL_KEYS:
        if key not in payload:
            errors.append(RegistrySchemaIssue("error", f"missing key {key!r}", "<root>"))

    entries = payload.get("entries", [])
    if not isinstance(entries, list):
        errors.append(RegistrySchemaIssue("error", "entries must be a list", "entries"))
    else:
        entries_count = len(entries)
        if entries_count == 0:
            warnings.append(RegistrySchemaIssue("warning", "entries is empty", "entries"))
        for idx, entry in enumerate(entries):
            loc = f"entries[{idx}]"
            if not isinstance(entry, dict):
                errors.append(RegistrySchemaIssue("error", "must be dict", loc))
                continue
            for ek in _REQUIRED_ENTRY_KEYS:
                if ek not in entry:
                    errors.append(RegistrySchemaIssue("error", f"missing key {ek!r}", loc))
            model_dir = entry.get("model_dir")
            if model_dir:
                model_path = Path(str(model_dir))
                if not model_path.exists():
                    errors.append(
                        RegistrySchemaIssue(
                            "error",
                            f"model_dir does not exist: {model_path}",
                            f"{loc}.model_dir",
                        )
                    )

    is_valid = len(errors) == 0
    return RegistrySchemaReport(is_valid, tuple(errors), tuple(warnings), entries_count)


@dataclass
class HotReloadableRegistry:
    """线程安全的 registry 容器，支持热加载 + 回滚。"""

    registry_path: Path
    _current: ClusterModelRegistry | None = field(default=None, init=False, repr=False)
    _version: int = field(default=0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @classmethod
    def from_path(
        cls,
        registry_path: Path | str,
        *,
        strict: bool = True,
    ) -> "HotReloadableRegistry":
        """构造时立刻 load + validate；strict=True 时 schema 错误 raise。"""
        wrapper = cls(registry_path=Path(registry_path))
        report = validate_registry_schema(wrapper.registry_path)
        if strict:
            report.raise_if_invalid()
        elif not report.is_valid:
            logger.warning(
                "registry schema has %d errors, loading anyway (strict=False)", len(report.errors)
            )
        wrapper._current = ClusterModelRegistry.from_registry_json(wrapper.registry_path)
        wrapper._version = 1
        logger.info(
            "registry loaded: path=%s version=%d entries=%d",
            wrapper.registry_path, wrapper._version, report.entries_count,
        )
        return wrapper

    @property
    def current(self) -> ClusterModelRegistry:
        """当前 registry。未 load 时 raise。"""
        if self._current is None:
            raise RuntimeError("registry not loaded; call from_path() first")
        return self._current

    @property
    def version(self) -> int:
        return self._version

    def reload(self) -> tuple[bool, RegistrySchemaReport]:
        """尝试 reload；失败保留旧版本。返回 (success, report)。"""
        with self._lock:
            report = validate_registry_schema(self.registry_path)
            if not report.is_valid:
                logger.error(
                    "registry reload failed: schema invalid; keeping old version=%d errors=%d",
                    self._version, len(report.errors),
                )
                return False, report
            try:
                new_registry = ClusterModelRegistry.from_registry_json(self.registry_path)
            except Exception as exc:  # noqa: BLE001
                logger.exception("registry reload exception; keeping old version=%d: %s", self._version, exc)
                return False, report
            self._current = new_registry
            self._version += 1
            logger.info(
                "registry reloaded: path=%s version=%d entries=%d",
                self.registry_path, self._version, report.entries_count,
            )
            return True, report

    def install_sighup_handler(self) -> None:
        """绑定 SIGHUP → reload。运维可用 ``kill -HUP <pid>`` 触发。

        注意：``signal.signal`` 只能在主线程调用；非主线程会 raise ValueError。
        Windows 没有 SIGHUP，仍以 AttributeError fallback 处理（不绑定）。
        """
        if not hasattr(signal, "SIGHUP"):
            logger.warning("SIGHUP not supported on this platform; skip handler install")
            return
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError(
                "install_sighup_handler must be called from main thread"
            )

        def _handler(signum: int, frame: Any) -> None:  # noqa: ARG001
            logger.info("SIGHUP received → reloading registry")
            ok, _ = self.reload()
            logger.info("SIGHUP reload result: success=%s version=%d", ok, self._version)

        signal.signal(signal.SIGHUP, _handler)
        logger.info("SIGHUP handler installed for registry path=%s", self.registry_path)


__all__ = [
    "HotReloadableRegistry",
    "RegistrySchemaIssue",
    "RegistrySchemaReport",
    "validate_registry_schema",
]
