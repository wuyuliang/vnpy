"""P3-21: Prometheus + webhook monitoring tests."""
from __future__ import annotations

import unittest

from cta.live.monitoring import MetricsRegistry, SEVERITY, WebhookAlerter


class TestMetricsRegistry(unittest.TestCase):
    def test_counter_inc(self) -> None:
        reg = MetricsRegistry()
        reg.inc("orders_total")
        reg.inc("orders_total", value=2.0)
        self.assertEqual(reg.get_counter("orders_total"), 3.0)

    def test_counter_with_labels(self) -> None:
        reg = MetricsRegistry()
        reg.inc("orders_total", labels={"side": "long"})
        reg.inc("orders_total", labels={"side": "long"})
        reg.inc("orders_total", labels={"side": "short"})
        self.assertEqual(reg.get_counter("orders_total", labels={"side": "long"}), 2.0)
        self.assertEqual(reg.get_counter("orders_total", labels={"side": "short"}), 1.0)

    def test_gauge_set(self) -> None:
        reg = MetricsRegistry()
        reg.set_gauge("equity", 1_000_000.0)
        reg.set_gauge("equity", 1_010_000.0)
        self.assertEqual(reg.get_gauge("equity"), 1_010_000.0)

    def test_prometheus_render(self) -> None:
        reg = MetricsRegistry()
        reg.register_help("orders_total", "total orders submitted")
        reg.inc("orders_total", labels={"side": "long"})
        reg.set_gauge("equity", 1_000_000.0)
        text = reg.render_prometheus()
        self.assertIn("# HELP orders_total total orders submitted", text)
        self.assertIn("# TYPE orders_total counter", text)
        self.assertIn('orders_total{side="long"} 1.0', text)
        self.assertIn("equity 1000000.0", text)

    def test_unknown_counter_returns_zero(self) -> None:
        reg = MetricsRegistry()
        self.assertEqual(reg.get_counter("nope"), 0.0)


class TestWebhookAlerter(unittest.TestCase):
    def test_send_calls_http_when_url_present(self) -> None:
        calls: list[str] = []

        def fake_http(url, headers, timeout):
            calls.append(url)
            return 200

        alerter = WebhookAlerter(webhook_urls={"critical": "https://hook.example/x"})
        ok = alerter.send(
            severity="critical", title="loss", message="-5%",
            http_get=fake_http,
        )
        self.assertTrue(ok)
        self.assertEqual(calls, ["https://hook.example/x"])

    def test_send_no_url_returns_false(self) -> None:
        alerter = WebhookAlerter()
        ok = alerter.send(severity="warn", title="x", message="y", http_get=lambda *a: None)
        self.assertFalse(ok)

    def test_send_dedup_within_window(self) -> None:
        calls: list[str] = []
        def fake_http(url, headers, timeout):
            calls.append(url)
            return 200
        alerter = WebhookAlerter(
            webhook_urls={"warn": "https://hook"},
            suppress_duplicates_window_seconds=60.0,
        )
        alerter.send(severity="warn", title="same", message="m1", http_get=fake_http)
        alerter.send(severity="warn", title="same", message="m2", http_get=fake_http)
        self.assertEqual(len(calls), 1)  # 第 2 次被去重

    def test_send_handles_http_exception_gracefully(self) -> None:
        def fake_http(url, headers, timeout):
            raise OSError("network down")
        alerter = WebhookAlerter(webhook_urls={"info": "https://hook"})
        ok = alerter.send(severity="info", title="t", message="m", http_get=fake_http)
        self.assertFalse(ok)

    def test_default_severity_fallback(self) -> None:
        calls: list[str] = []
        def fake_http(url, headers, timeout):
            calls.append(url)
        alerter = WebhookAlerter(webhook_urls={"default": "https://default"})
        ok = alerter.send(severity="unknown_sev", title="t", message="m", http_get=fake_http)
        self.assertTrue(ok)
        self.assertEqual(calls, ["https://default"])


if __name__ == "__main__":
    unittest.main()
