"""仿真 / 实盘运行守护：监控 gateway 连接状态并自动重连。

使用
----
    sup = Supervisor(main_engine, gateway_name="CTP", connect_setting=cfg.to_vnpy(),
                     check_interval=10.0, max_reconnects=100)
    stop = threading.Event()
    sup.loop(stop)             # 阻塞直到 stop.set()

测试时注入 ``main_engine`` 是带 ``get_gateway/connect`` 接口的 fake 即可。

注意
----
本模块不处理订单恢复、持仓对账：那是策略 / 风控的职责。守护只关心连接层。
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Supervisor:
    main_engine: Any
    gateway_name: str
    connect_setting: dict
    check_interval: float = 10.0
    max_reconnects: int = 100
    _reconnect_count: int = field(default=0, init=False, repr=False)

    def _is_connected(self) -> bool:
        gw = self.main_engine.get_gateway(self.gateway_name)
        if gw is None:
            return False
        # gateway 可能没有显式 connected 字段：用 query_account 探活
        flag = getattr(gw, "connected", None)
        if isinstance(flag, bool):
            return flag
        try:
            gw.query_account()
            return True
        except Exception:  # noqa: BLE001
            return False

    def _gateway_present(self) -> bool:
        return self.main_engine.get_gateway(self.gateway_name) is not None

    def step(self) -> dict:
        if not self._gateway_present():
            logger.warning("gateway %s not registered on main_engine", self.gateway_name)
            return {"event": "gateway_missing", "gateway": self.gateway_name}
        if self._is_connected():
            return {"event": "ok", "gateway": self.gateway_name}
        if self._reconnect_count >= self.max_reconnects:
            logger.error(
                "gateway %s reconnect cap reached (%d), giving up",
                self.gateway_name, self.max_reconnects,
            )
            return {
                "event": "give_up",
                "gateway": self.gateway_name,
                "count": self._reconnect_count,
            }
        self._reconnect_count += 1
        logger.warning(
            "gateway %s disconnected, reconnect attempt #%d",
            self.gateway_name, self._reconnect_count,
        )
        try:
            self.main_engine.connect(self.connect_setting, self.gateway_name)
        except Exception as e:  # noqa: BLE001
            logger.exception("connect failed: %s", e)
            return {"event": "reconnect_failed", "error": str(e)}
        return {
            "event": "reconnect",
            "gateway": self.gateway_name,
            "count": self._reconnect_count,
        }

    def loop(self, stop_event: threading.Event) -> int:
        """持续运行直到 ``stop_event.is_set()``，返回累计执行的 step 次数。"""
        n = 0
        while not stop_event.is_set():
            try:
                self.step()
            except Exception:  # noqa: BLE001
                logger.exception("supervisor step error")
            n += 1
            stop_event.wait(self.check_interval)
        return n


__all__ = ["Supervisor"]
