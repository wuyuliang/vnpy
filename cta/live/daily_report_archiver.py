"""Daily report 自动归档 + 异常摘要 (P3-26).

每天收盘后：
1. 把 ``cta/sim/daily_parity_report.py`` 的 markdown report 移到 archive
2. 汇总当日异常事件（kill_switch / reject / drift），生成 incident summary
3. 异常 ≥ 1 时通过 ``WebhookAlerter`` 推送告警

archive 结构：
    archive_root/
        20260523/
            parity_report.md
            incident_summary.json
            metrics_snapshot.txt
"""
from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DailyIncidentEvent:
    timestamp: datetime
    category: str    # "kill_switch" / "reject" / "drift" / "disconnect" / "manual"
    severity: str    # info / warn / critical
    description: str
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class DailyIncidentSummary:
    trade_date: date
    events: list[DailyIncidentEvent] = field(default_factory=list)

    def add(self, event: DailyIncidentEvent) -> None:
        self.events.append(event)

    @property
    def has_critical(self) -> bool:
        return any(str(e.severity).lower() == "critical" for e in self.events)

    @property
    def has_warn(self) -> bool:
        return any(str(e.severity).lower() == "warn" for e in self.events)

    def counts_by_category(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.events:
            out[e.category] = out.get(e.category, 0) + 1
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date.isoformat(),
            "n_events": len(self.events),
            "counts_by_category": self.counts_by_category(),
            "has_critical": self.has_critical,
            "events": [
                {
                    "timestamp": e.timestamp.isoformat(),
                    "category": e.category,
                    "severity": e.severity,
                    "description": e.description,
                    "extra": dict(e.extra),
                }
                for e in self.events
            ],
        }


class DailyReportArchiver:
    """每日收盘后调用 archive_today()。"""

    def __init__(
        self,
        *,
        archive_root: Path | str,
        source_parity_report: Path | str | None = None,
        webhook_alerter: Any = None,  # cta.live.monitoring.WebhookAlerter（duck-typed）
    ) -> None:
        self.archive_root = Path(archive_root)
        self.source_parity_report = Path(source_parity_report) if source_parity_report else None
        self.webhook_alerter = webhook_alerter

    def archive_today(
        self,
        *,
        trade_date: date,
        incident_summary: DailyIncidentSummary,
        metrics_snapshot: str | None = None,
    ) -> Path:
        """归档当日所有产物到 ``archive_root/YYYYMMDD/``。返回归档目录路径。"""
        date_dir = self.archive_root / trade_date.strftime("%Y%m%d")
        date_dir.mkdir(parents=True, exist_ok=True)

        # 1. parity report
        if self.source_parity_report and self.source_parity_report.exists():
            target = date_dir / "parity_report.md"
            shutil.copy2(self.source_parity_report, target)
            logger.info("archived parity report: %s", target)

        # 2. incident summary（不论是否有事件都写入，便于审计）
        (date_dir / "incident_summary.json").write_text(
            json.dumps(incident_summary.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 3. metrics snapshot
        if metrics_snapshot is not None:
            (date_dir / "metrics_snapshot.txt").write_text(metrics_snapshot, encoding="utf-8")

        # 4. webhook 告警（仅 critical / warn）
        if self.webhook_alerter is not None:
            if incident_summary.has_critical:
                self._send_webhook(
                    incident_summary, trade_date, severity="critical",
                )
            elif incident_summary.has_warn:
                self._send_webhook(
                    incident_summary, trade_date, severity="warn",
                )

        return date_dir

    def _send_webhook(
        self,
        summary: DailyIncidentSummary,
        trade_date: date,
        *,
        severity: str,
    ) -> None:
        title = f"[{severity.upper()}] CTA daily summary {trade_date.isoformat()}"
        counts = summary.counts_by_category()
        critical_evs = [e for e in summary.events if str(e.severity).lower() == "critical"]
        msg_lines = [f"events: {len(summary.events)}", f"counts: {counts}"]
        if critical_evs:
            msg_lines.append("critical:")
            for e in critical_evs[:5]:
                msg_lines.append(f"  - {e.category}: {e.description}")
        try:
            self.webhook_alerter.send(
                severity=severity,
                title=title,
                message="\n".join(msg_lines),
                labels={"trade_date": trade_date.isoformat()},
            )
        except Exception:  # noqa: BLE001
            logger.exception("webhook send during archive failed")


__all__ = [
    "DailyIncidentEvent",
    "DailyIncidentSummary",
    "DailyReportArchiver",
]
