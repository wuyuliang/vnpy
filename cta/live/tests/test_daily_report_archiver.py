"""P3-26: 复盘自动化 + webhook 告警 tests."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from cta.live.daily_report_archiver import (
    DailyIncidentEvent,
    DailyIncidentSummary,
    DailyReportArchiver,
)


def _ev(category: str, severity: str = "info", description: str = "x") -> DailyIncidentEvent:
    return DailyIncidentEvent(
        timestamp=datetime(2024, 3, 25, 14, 30, 0),
        category=category, severity=severity, description=description,
    )


class TestDailyIncidentSummary(unittest.TestCase):
    def test_empty_summary(self) -> None:
        s = DailyIncidentSummary(trade_date=date(2024, 3, 25))
        self.assertFalse(s.has_critical)
        self.assertFalse(s.has_warn)
        self.assertEqual(s.counts_by_category(), {})

    def test_categorize_counts(self) -> None:
        s = DailyIncidentSummary(trade_date=date(2024, 3, 25))
        s.add(_ev("kill_switch", "critical", "daily_loss > 3%"))
        s.add(_ev("kill_switch", "critical", "again"))
        s.add(_ev("reject", "warn", "partial"))
        counts = s.counts_by_category()
        self.assertEqual(counts["kill_switch"], 2)
        self.assertEqual(counts["reject"], 1)
        self.assertTrue(s.has_critical)
        self.assertTrue(s.has_warn)

    def test_to_dict_serializable(self) -> None:
        s = DailyIncidentSummary(trade_date=date(2024, 3, 25))
        s.add(_ev("drift", "warn", "parity > 50bp"))
        d = s.to_dict()
        # JSON roundtrip
        encoded = json.dumps(d, ensure_ascii=False)
        decoded = json.loads(encoded)
        self.assertEqual(decoded["n_events"], 1)
        self.assertEqual(decoded["events"][0]["category"], "drift")


class _FakeWebhookAlerter:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send(self, *, severity, title, message, labels=None):  # noqa: ANN001
        self.calls.append({
            "severity": severity, "title": title, "message": message,
            "labels": dict(labels or {}),
        })
        return True


class TestDailyReportArchiver(unittest.TestCase):
    def test_archive_creates_date_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            archiver = DailyReportArchiver(archive_root=tmpdir)
            summary = DailyIncidentSummary(trade_date=date(2024, 3, 25))
            archive_path = archiver.archive_today(
                trade_date=date(2024, 3, 25),
                incident_summary=summary,
            )
            self.assertTrue(archive_path.exists())
            self.assertEqual(archive_path.name, "20240325")
            self.assertTrue((archive_path / "incident_summary.json").exists())

    def test_archive_parity_report_when_source_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            # 模拟当日 parity_report.md
            src = tmp / "parity_report.md"
            src.write_text("# Parity report\nOK\n", encoding="utf-8")
            archiver = DailyReportArchiver(
                archive_root=tmp / "archive", source_parity_report=src,
            )
            summary = DailyIncidentSummary(trade_date=date(2024, 3, 25))
            archive_path = archiver.archive_today(
                trade_date=date(2024, 3, 25),
                incident_summary=summary,
            )
            archived = archive_path / "parity_report.md"
            self.assertTrue(archived.exists())
            self.assertIn("Parity report", archived.read_text(encoding="utf-8"))

    def test_archive_metrics_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            archiver = DailyReportArchiver(archive_root=tmpdir)
            summary = DailyIncidentSummary(trade_date=date(2024, 3, 25))
            archive_path = archiver.archive_today(
                trade_date=date(2024, 3, 25),
                incident_summary=summary,
                metrics_snapshot="orders_total 42\nequity 1000000\n",
            )
            metrics_file = archive_path / "metrics_snapshot.txt"
            self.assertTrue(metrics_file.exists())
            self.assertIn("orders_total", metrics_file.read_text())

    def test_critical_triggers_webhook(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            alerter = _FakeWebhookAlerter()
            archiver = DailyReportArchiver(archive_root=tmpdir, webhook_alerter=alerter)
            summary = DailyIncidentSummary(trade_date=date(2024, 3, 25))
            summary.add(_ev("kill_switch", "critical", "daily_loss > 3%"))
            archiver.archive_today(
                trade_date=date(2024, 3, 25), incident_summary=summary,
            )
            self.assertEqual(len(alerter.calls), 1)
            self.assertEqual(alerter.calls[0]["severity"], "critical")
            self.assertIn("CRITICAL", alerter.calls[0]["title"])

    def test_warn_triggers_webhook_at_warn_severity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            alerter = _FakeWebhookAlerter()
            archiver = DailyReportArchiver(archive_root=tmpdir, webhook_alerter=alerter)
            summary = DailyIncidentSummary(trade_date=date(2024, 3, 25))
            summary.add(_ev("reject", "warn", "partial fill"))
            archiver.archive_today(
                trade_date=date(2024, 3, 25), incident_summary=summary,
            )
            self.assertEqual(len(alerter.calls), 1)
            self.assertEqual(alerter.calls[0]["severity"], "warn")

    def test_info_only_no_webhook(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            alerter = _FakeWebhookAlerter()
            archiver = DailyReportArchiver(archive_root=tmpdir, webhook_alerter=alerter)
            summary = DailyIncidentSummary(trade_date=date(2024, 3, 25))
            summary.add(_ev("manual", "info", "ops check"))
            archiver.archive_today(
                trade_date=date(2024, 3, 25), incident_summary=summary,
            )
            self.assertEqual(len(alerter.calls), 0)


if __name__ == "__main__":
    unittest.main()
