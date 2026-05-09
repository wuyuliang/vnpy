"""SimNow / 实盘 CTP 仿真运行器。

启动流程：
1. 实例化 ``EventEngine`` + ``MainEngine``
2. 注册 ``CtpGateway`` + ``CtaStrategyApp``
3. 用 ``SimnowSetting`` 翻译为 vnpy_ctp 的中文 key 字典并 ``connect``
4. 通过 CtaStrategy app 的 cta_engine 加策略 → init → start
5. 返回 main_engine（调用方负责 ``main_engine.close()`` 收尾）

测试通过 ``main_engine_factory`` 注入 fake，不真实连接 SimNow；
真实运行需要 ``vnpy_ctp`` 已安装、SimNow 账号有效、网络可达。

SimNow 公开仿真服务器（2026-05 数据，可能变化）：
- 7x24 仿真：
    交易: tcp://180.168.146.187:10130   行情: tcp://180.168.146.187:10131
- 实盘交易日仿真：
    交易: tcp://180.168.146.187:10101   行情: tcp://180.168.146.187:10111
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


SIMNOW_DEFAULT: dict[str, str] = {
    "brokerid": "9999",
    "auth_code": "0000000000000000",
    "appid": "simnow_client_test",
    "product_info": "",
    "td_address": "tcp://180.168.146.187:10130",
    "md_address": "tcp://180.168.146.187:10131",
}


@dataclass
class SimnowSetting:
    """SimNow / CTP 仿真账号配置。字段命名遵循 vnpy_ctp 中文 key 的语义对应。"""

    userid: str
    password: str
    brokerid: str = SIMNOW_DEFAULT["brokerid"]
    auth_code: str = SIMNOW_DEFAULT["auth_code"]
    appid: str = SIMNOW_DEFAULT["appid"]
    product_info: str = SIMNOW_DEFAULT["product_info"]
    td_address: str = SIMNOW_DEFAULT["td_address"]
    md_address: str = SIMNOW_DEFAULT["md_address"]

    def to_vnpy(self) -> dict[str, str]:
        """vnpy_ctp 接受的中文 key 配置字典。"""
        return {
            "用户名": self.userid,
            "密码": self.password,
            "经纪商代码": self.brokerid,
            "交易服务器": self.td_address,
            "行情服务器": self.md_address,
            "产品名称": self.appid,
            "授权编码": self.auth_code,
            "产品信息": self.product_info,
        }


@dataclass
class SimRunConfig:
    """单策略仿真启动配置。"""

    strategy_class: type
    strategy_name: str
    vt_symbol: str
    setting: dict


def _default_main_engine_factory():  # pragma: no cover - 实际运行才走到
    """生产环境：装好 vnpy_ctp 时构造 MainEngine + CtpGateway + CtaStrategyApp。"""
    from vnpy.event import EventEngine
    from vnpy.trader.engine import MainEngine

    try:
        from vnpy_ctp import CtpGateway  # type: ignore
    except ImportError as e:
        raise ImportError(
            "vnpy_ctp 未安装；pip install vnpy_ctp 后重试，或为测试场景注入 main_engine_factory。"
        ) from e

    from vnpy_ctastrategy import CtaStrategyApp  # type: ignore

    ee = EventEngine()
    me = MainEngine(ee)
    me.add_gateway(CtpGateway)
    me.add_app(CtaStrategyApp)
    return me


def run_sim(
    cfg: SimRunConfig,
    simnow: SimnowSetting,
    *,
    gateway_name: str = "CTP",
    cta_engine_name: str = "CtaStrategy",
    main_engine_factory: Callable[[], Any] | None = None,
):
    """启动一个 CtaStrategy 仿真实例。

    Parameters
    ----------
    cfg
        策略类、策略名、合约 vt_symbol、setting 字典。
    simnow
        SimNow / CTP 仿真账号 + 服务器配置。
    main_engine_factory
        测试可注入 fake；生产留 ``None`` 使用默认实现（需要 vnpy_ctp）。

    Returns
    -------
    main_engine
        已启动的 MainEngine。调用方负责 ``main_engine.close()``。
    """
    factory = main_engine_factory or _default_main_engine_factory
    me = factory()
    setting = simnow.to_vnpy()
    logger.info(
        "connecting %s gateway: td=%s md=%s",
        gateway_name, simnow.td_address, simnow.md_address,
    )
    me.connect(setting, gateway_name)

    cta_engine = me.get_engine(cta_engine_name)
    if cta_engine is None:
        raise RuntimeError(f"main_engine.get_engine({cta_engine_name!r}) returned None")

    if hasattr(cta_engine, "init_engine"):
        cta_engine.init_engine()  # 加载本地策略 / 持仓 / 数据；fake 中可空实现

    cta_engine.add_strategy(
        cfg.strategy_class, cfg.strategy_name, cfg.vt_symbol, dict(cfg.setting or {})
    )
    cta_engine.init_strategy(cfg.strategy_name)
    cta_engine.start_strategy(cfg.strategy_name)
    logger.info("strategy %s started on %s", cfg.strategy_name, cfg.vt_symbol)
    return me


__all__ = ["SIMNOW_DEFAULT", "SimnowSetting", "SimRunConfig", "run_sim"]
