"""Structured JSON logging + size-based file rotation (P3-22).

为什么需要结构化日志
-------------------
- 生产 sim/live 长跑日志日积月累 GB 级；plain text 难以聚合 / 按字段查询
- JSON 行格式（``{ts, level, name, msg, extra}``）可直接喂 ELK / Loki / Datadog
- 按大小 rotation 防止单文件爆盘

用法：
    from cta.utils.logging_config import setup_structured_logging
    setup_structured_logging(
        log_dir="/var/log/cta_live",
        max_bytes=100 * 1024 * 1024,
        backup_count=10,
        structured=True,
    )
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import time
from pathlib import Path
from typing import Any


class JsonFormatter(logging.Formatter):
    """JSON 行格式：每条 log 一个 JSON。"""

    def __init__(self, *, include_extras: bool = True) -> None:
        super().__init__()
        self._include_extras = bool(include_extras)

    # 标准 LogRecord 字段（不带入 extras）
    _STANDARD_KEYS = frozenset({
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "asctime",
    })

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created))
                  + f".{int(record.msecs):03d}",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if self._include_extras:
            extras = {
                k: v for k, v in record.__dict__.items()
                if k not in self._STANDARD_KEYS and not k.startswith("_")
            }
            if extras:
                payload["extra"] = extras
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_structured_logging(
    *,
    log_dir: str | Path | None = None,
    level: int = logging.INFO,
    max_bytes: int = 100 * 1024 * 1024,
    backup_count: int = 10,
    structured: bool = True,
    stream_to_stdout: bool = True,
    log_file_name: str = "cta_live.log",
) -> None:
    """配置 root logger：JSON 文件 + 控制台.

    Args:
        log_dir: 日志目录；None → 仅 console 输出，不 rotation
        level: 日志级别
        max_bytes: 单文件最大字节数（默认 100 MB）
        backup_count: rotation 保留份数
        structured: True → JSON；False → 普通 text
        stream_to_stdout: 是否同时输出到 stdout
        log_file_name: 日志文件名（log_dir 下）
    """
    root = logging.getLogger()
    # 清理可能的旧 handler（避免重复 setup 加倍）
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(level)

    fmt = JsonFormatter() if structured else logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    if log_dir is not None:
        d = Path(log_dir)
        d.mkdir(parents=True, exist_ok=True)
        rotating = logging.handlers.RotatingFileHandler(
            d / log_file_name,
            maxBytes=int(max_bytes),
            backupCount=int(backup_count),
            encoding="utf-8",
        )
        rotating.setFormatter(fmt)
        rotating.setLevel(level)
        root.addHandler(rotating)

    if stream_to_stdout:
        console = logging.StreamHandler(stream=sys.stdout)
        console.setFormatter(fmt)
        console.setLevel(level)
        root.addHandler(console)


__all__ = ["JsonFormatter", "setup_structured_logging"]
