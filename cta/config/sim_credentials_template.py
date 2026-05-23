"""SimNow / CTP credentials template — copy to ``sim_credentials.py`` and fill in.

**这是模板文件，永不入库的应该是同目录下的 ``sim_credentials.py``**（在 .gitignore 中）。

使用：
    cp cta/config/sim_credentials_template.py cta/config/sim_credentials.py
    # 编辑 sim_credentials.py 填入真实账号

加载：
    from cta.config.sim_credentials import load_credentials
    creds = load_credentials("simnow_7x24")  # 或 "simnow_realtime" / "prod_xxx"
    setting = creds.to_simnow_setting()

设计原则：
- 同一仓库支持多套环境（SimNow 7x24 / SimNow 交易日 / 实盘券商 A / 实盘 B）
- 凭据**不入库**：``sim_credentials.py`` 在 ``.gitignore``
- 调用方必须显式指定 profile name，避免误用
- CI 应 grep 防止 ``sim_credentials.py`` 被误 commit
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CtpCredentials:
    """单个 CTP 账户凭据（SimNow 或实盘）。

    所有 broker_id / app_id / auth_code 等都由真实账户管理方提供，
    SimNow 公开测试默认值见 ``cta/sim/sim_runner.py: SIMNOW_DEFAULT``。
    """
    userid: str = ""
    password: str = ""
    broker_id: str = "9999"
    td_address: str = "tcp://180.168.146.187:10130"
    md_address: str = "tcp://180.168.146.187:10131"
    app_id: str = "simnow_client_test"
    auth_code: str = "0000000000000000"
    product_info: str = ""
    profile: str = "simnow_7x24"  # 标识用，便于日志区分账户

    def is_valid(self) -> bool:
        """最小校验：userid + password 非空 + 不含 REPLACE_ME 占位标记。"""
        uid = str(self.userid).strip()
        pwd = str(self.password).strip()
        if not uid or not pwd:
            return False
        if "REPLACE_ME" in uid or "REPLACE_ME" in pwd:
            return False
        return True

    def to_simnow_setting(self) -> "object":
        """转 ``cta.sim.sim_runner.SimnowSetting``（延迟 import 避免循环）。"""
        from cta.sim.sim_runner import SimnowSetting
        return SimnowSetting(
            userid=self.userid,
            password=self.password,
            brokerid=self.broker_id,
            auth_code=self.auth_code,
            appid=self.app_id,
            product_info=self.product_info,
            td_address=self.td_address,
            md_address=self.md_address,
        )


# ---------------------------------------------------------------------------
# 模板：填入真实凭据后 rename 为 sim_credentials.py（**在 .gitignore**）
# ---------------------------------------------------------------------------

CREDENTIALS_BY_PROFILE: dict[str, CtpCredentials] = {
    # SimNow 7x24 公共仿真（不需要真实账号也能跑 smoke）
    "simnow_7x24_anonymous": CtpCredentials(
        userid="REPLACE_ME_SIMNOW_USERID",
        password="REPLACE_ME_SIMNOW_PASSWORD",
        broker_id="9999",
        td_address="tcp://180.168.146.187:10130",
        md_address="tcp://180.168.146.187:10131",
        profile="simnow_7x24_anonymous",
    ),
    # SimNow 实盘交易日仿真（需要 SimNow 真账号）
    "simnow_realtime": CtpCredentials(
        userid="REPLACE_ME",
        password="REPLACE_ME",
        broker_id="9999",
        td_address="tcp://180.168.146.187:10101",
        md_address="tcp://180.168.146.187:10111",
        profile="simnow_realtime",
    ),
    # 真实券商示例（占位，需自填）
    "prod_broker_a": CtpCredentials(
        userid="REPLACE_ME",
        password="REPLACE_ME",
        broker_id="REPLACE_ME",
        td_address="tcp://REPLACE_ME:10000",
        md_address="tcp://REPLACE_ME:10010",
        app_id="REPLACE_ME",
        auth_code="REPLACE_ME",
        profile="prod_broker_a",
    ),
}


def load_credentials(profile: str = "simnow_7x24_anonymous") -> CtpCredentials:
    """加载指定 profile 的凭据；profile 未注册时 raise。"""
    if profile not in CREDENTIALS_BY_PROFILE:
        raise KeyError(
            f"unknown credential profile {profile!r}; "
            f"available={sorted(CREDENTIALS_BY_PROFILE.keys())}"
        )
    creds = CREDENTIALS_BY_PROFILE[profile]
    if not creds.is_valid():
        raise ValueError(
            f"credential profile {profile!r} has placeholder values; "
            "please copy this template to sim_credentials.py and fill real values"
        )
    return creds


__all__ = ["CtpCredentials", "CREDENTIALS_BY_PROFILE", "load_credentials"]
