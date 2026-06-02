"""Prometheus + webhook 监控告警 (P3-21).

提供两个分层：

1. **MetricsRegistry**：lightweight 计数器 / gauge / histogram 抽象，可输出
   Prometheus exposition format（``GET /metrics`` 接口）。无需真实 prometheus-client
   依赖（避免给项目添麻烦），后续可在 sim_runner 启动时再切到 prom 官方库。

2. **WebhookAlerter**：钉钉 / 企微 / Slack webhook 推送，按 severity 分级。
   不要在主循环里同步发，建议异步线程或队列。

设计原则：
- 默认 off / no-op：未配置 endpoint 时所有方法返回 None，不阻塞业务
- 失败不传染：webhook 请求异常仅记日志，不抛
- 安全：webhook URL 含 secret，必须从 .gitignore 里的凭据文件读取
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ─── Metrics Registry ────────────────────────────────────────────────────


@dataclass
class _MetricSample:
    value: float
    timestamp: float = field(default_factory=lambda: time.time())
    labels: dict[str, str] = field(default_factory=dict)


class MetricsRegistry:
    """thread-safe counter / gauge / histogram registry; Prometheus 输出。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, dict[tuple, float]] = defaultdict(dict)
        self._gauges: dict[str, dict[tuple, float]] = defaultdict(dict)
        self._helps: dict[str, str] = {}

    def _key(self, labels: dict[str, str] | None) -> tuple:
        if not labels:
            return ()
        return tuple(sorted(labels.items()))

    def register_help(self, name: str, help_text: str) -> None:
        with self._lock:
            self._helps[name] = str(help_text)

    def inc(self, name: str, *, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        """累加 counter（单调递增）。"""
        with self._lock:
            key = self._key(labels)
            self._counters[name][key] = self._counters[name].get(key, 0.0) + float(value)

    def set_gauge(self, name: str, value: float, *, labels: dict[str, str] | None = None) -> None:
        """设置 gauge（任意可上可下）。"""
        with self._lock:
            self._gauges[name][self._key(labels)] = float(value)

    def get_counter(self, name: str, *, labels: dict[str, str] | None = None) -> float:
        with self._lock:
            return self._counters.get(name, {}).get(self._key(labels), 0.0)

    def get_gauge(self, name: str, *, labels: dict[str, str] | None = None) -> float:
        with self._lock:
            return self._gauges.get(name, {}).get(self._key(labels), 0.0)

    def render_prometheus(self) -> str:
        """输出 Prometheus exposition format（``# HELP`` + ``# TYPE`` + 数据行）。"""
        lines: list[str] = []
        with self._lock:
            for name, kv in sorted(self._counters.items()):
                lines.append(f"# HELP {name} {self._helps.get(name, name)}")
                lines.append(f"# TYPE {name} counter")
                for label_tuple, v in sorted(kv.items()):
                    lines.append(self._format_line(name, label_tuple, v))
            for name, kv in sorted(self._gauges.items()):
                lines.append(f"# HELP {name} {self._helps.get(name, name)}")
                lines.append(f"# TYPE {name} gauge")
                for label_tuple, v in sorted(kv.items()):
                    lines.append(self._format_line(name, label_tuple, v))
        return "\n".join(lines) + "\n"

    @staticmethod
    def _format_line(name: str, label_tuple: tuple, value: float) -> str:
        if not label_tuple:
            return f"{name} {value}"
        labels = ",".join(f'{k}="{v}"' for k, v in label_tuple)
        return f"{name}{{{labels}}} {value}"


# ─── Webhook Alerter ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class AlertSeverity:
    INFO: str = "info"
    WARN: str = "warn"
    CRITICAL: str = "critical"


SEVERITY = AlertSeverity()


@dataclass
class WebhookAlerter:
    """钉钉 / 企微 / Slack webhook 推送（按 severity 路由）。"""
    webhook_urls: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 3.0
    suppress_duplicates_window_seconds: float = 60.0
    queue_maxsize: int = 1024
    _last_sent: dict[str, float] = field(default_factory=dict, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _queue: "queue.Queue[tuple[str, bytes, Callable[[str, dict, float], int] | None, str, str]]" = field(
        init=False, repr=False
    )
    _stop_event: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _worker: threading.Thread = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._queue = queue.Queue(maxsize=max(int(self.queue_maxsize), 1))
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="webhook-alerter",
            daemon=True,
        )
        self._worker.start()

    def _worker_loop(self) -> None:
        while True:
            if self._stop_event.is_set() and self._queue.empty():
                return
            try:
                url, data, http_get, sev, title = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                if http_get is not None:
                    http_get(url, {"Content-Type": "application/json"}, float(self.timeout_seconds))
                else:
                    req = urllib.request.Request(
                        url,
                        data=data,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    urllib.request.urlopen(req, timeout=float(self.timeout_seconds)).close()
            except (urllib.error.URLError, OSError, ValueError) as exc:
                logger.warning("webhook send failed (severity=%s title=%s): %s", sev, title, exc)
            finally:
                self._queue.task_done()

    def shutdown(self, *, flush: bool = True, timeout: float = 3.0) -> None:
        """Stop worker thread gracefully.

        flush=True 会先等待队列发送完再退出。
        """
        if flush:
            deadline = time.time() + float(timeout)
            while time.time() < deadline:
                if self._queue.unfinished_tasks == 0:
                    break
                time.sleep(0.01)
        self._stop_event.set()
        self._worker.join(timeout=float(timeout))

    def send(
        self,
        *,
        severity: str,
        title: str,
        message: str,
        labels: dict[str, str] | None = None,
        http_get: Callable[[str, dict, float], int] | None = None,
    ) -> bool:
        """推送 alert（异步入队）；返回是否成功入队。

        ``http_get`` 是依赖注入点：默认用 urllib.request.urlopen；测试时注入 fake。
        """
        sev = str(severity).strip().lower()
        url = self.webhook_urls.get(sev) or self.webhook_urls.get("default")
        if not url:
            logger.debug("no webhook url for severity=%s; skip", sev)
            return False

        # 抑制 60s 内相同 title 的重复推送
        dedup_key = f"{sev}|{title}"
        now = time.time()
        with self._lock:
            last = self._last_sent.get(dedup_key, 0.0)
            if now - last < float(self.suppress_duplicates_window_seconds):
                return False
            self._last_sent[dedup_key] = now

        payload = {
            "severity": sev,
            "title": title,
            "message": message,
            "labels": dict(labels or {}),
            "ts": now,
        }
        try:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._queue.put_nowait((url, data, http_get, sev, title))
            return True
        except queue.Full:
            logger.warning("webhook queue full; drop alert severity=%s title=%s", sev, title)
            return False
        except ValueError as exc:
            logger.warning("webhook payload encode failed (severity=%s title=%s): %s", sev, title, exc)
            return False


__all__ = [
    "AlertSeverity",
    "MetricsRegistry",
    "SEVERITY",
    "WebhookAlerter",
]
