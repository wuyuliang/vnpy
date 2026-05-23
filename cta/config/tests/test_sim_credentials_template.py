"""Tests for sim_credentials template structure (P0-4 验收)."""
from __future__ import annotations

import unittest

from cta.config.sim_credentials_template import (
    CREDENTIALS_BY_PROFILE,
    CtpCredentials,
    load_credentials,
)


class TestSimCredentialsTemplate(unittest.TestCase):
    def test_credentials_dataclass_frozen(self) -> None:
        c = CtpCredentials(userid="x", password="y")
        with self.assertRaises(Exception):
            c.userid = "z"  # type: ignore[misc]

    def test_default_simnow_template_keys_present(self) -> None:
        # 3 个示例 profile 必须存在
        for key in ("simnow_7x24_anonymous", "simnow_realtime", "prod_broker_a"):
            self.assertIn(key, CREDENTIALS_BY_PROFILE)

    def test_template_values_are_placeholders(self) -> None:
        """模板内默认值应该是 REPLACE_ME 占位，避免误把模板当真凭据使用。"""
        for profile, creds in CREDENTIALS_BY_PROFILE.items():
            # 至少 userid 或 password 是占位
            self.assertTrue(
                "REPLACE_ME" in creds.userid or "REPLACE_ME" in creds.password,
                f"profile {profile} should have REPLACE_ME placeholder",
            )

    def test_is_valid_rejects_empty(self) -> None:
        self.assertFalse(CtpCredentials().is_valid())
        self.assertFalse(CtpCredentials(userid="x").is_valid())
        self.assertFalse(CtpCredentials(password="y").is_valid())
        self.assertTrue(CtpCredentials(userid="x", password="y").is_valid())

    def test_load_credentials_raises_on_placeholder(self) -> None:
        """模板内 placeholder profile load 时应 raise。"""
        with self.assertRaises(ValueError):
            load_credentials("simnow_7x24_anonymous")

    def test_load_credentials_raises_on_unknown_profile(self) -> None:
        with self.assertRaises(KeyError):
            load_credentials("does_not_exist_profile")

    def test_to_simnow_setting_returns_expected_fields(self) -> None:
        c = CtpCredentials(
            userid="u", password="p", broker_id="9999",
            td_address="td", md_address="md",
            app_id="app", auth_code="auth", product_info="pi",
            profile="test",
        )
        s = c.to_simnow_setting()
        # SimnowSetting 内部以英文 attr name 存储
        self.assertEqual(s.userid, "u")
        self.assertEqual(s.password, "p")
        self.assertEqual(s.brokerid, "9999")
        self.assertEqual(s.td_address, "td")
        self.assertEqual(s.md_address, "md")
        self.assertEqual(s.appid, "app")
        self.assertEqual(s.auth_code, "auth")
        # 中文 key 字典也正确转换
        d = s.to_vnpy()
        self.assertEqual(d["用户名"], "u")
        self.assertEqual(d["密码"], "p")
        self.assertEqual(d["经纪商代码"], "9999")


if __name__ == "__main__":
    unittest.main()
