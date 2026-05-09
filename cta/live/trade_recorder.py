"""实盘 / 仿真成交记录与回测格式转换。

挂在 ``LegacyCtaAdapter.trade_recorder``，每收一条 ``TradeData`` 就 append 到内存表，
``flush()`` 写 parquet 到磁盘；``to_trade_log(multiplier)`` 把流水按 open→close 配对成
回测格式的 ``trade_log`` DataFrame，可直接喂给 ``cta.live.daily_report.write_daily_report``
或 ``cta.sim.parity_check.compare_signals``。

字段约定（与 vnpy ``TradeData`` 兼容）：
    datetime, gateway_name, symbol, exchange.value, vt_symbol,
    orderid, tradeid, direction.value, offset.value, price, volume
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


def _safe_attr(obj: Any, name: str, default: Any = "") -> Any:
    val = getattr(obj, name, default)
    if hasattr(val, "value"):
        return val.value
    return val


class TradeRecorder:
    """append-only 成交记录，可 flush 到 parquet。"""

    def __init__(self, out_dir: str, vt_symbol: str, *, file_prefix: str = "trades") -> None:
        self.out_dir = Path(out_dir)
        self.vt_symbol = str(vt_symbol)
        self.file_prefix = str(file_prefix)
        self._rows: list[dict] = []

    def record(self, trade: Any) -> None:
        self._rows.append(
            {
                "datetime": getattr(trade, "datetime", None) or datetime.utcnow(),
                "vt_symbol": self.vt_symbol,
                "gateway_name": _safe_attr(trade, "gateway_name", ""),
                "symbol": _safe_attr(trade, "symbol", ""),
                "exchange": _safe_attr(trade, "exchange", ""),
                "orderid": _safe_attr(trade, "orderid", ""),
                "tradeid": _safe_attr(trade, "tradeid", ""),
                "direction": _safe_attr(trade, "direction", ""),
                "offset": _safe_attr(trade, "offset", ""),
                "price": float(getattr(trade, "price", 0.0) or 0.0),
                "volume": float(getattr(trade, "volume", 0.0) or 0.0),
            }
        )

    def to_dataframe(self) -> pd.DataFrame:
        if not self._rows:
            return pd.DataFrame(
                columns=["datetime", "vt_symbol", "direction", "offset", "price", "volume", "tradeid"]
            )
        return pd.DataFrame(self._rows)

    def flush(self) -> str:
        if not self._rows:
            return ""
        self.out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.out_dir / f"{self.file_prefix}_{self.vt_symbol.replace('.', '_')}_{ts}.parquet"
        self.to_dataframe().to_parquet(path, index=False)
        return str(path)

    def to_trade_log(self, *, multiplier: float = 1.0, commission: float = 0.0) -> pd.DataFrame:
        """把成交流水按 open→close 配对，输出回测 ``trade_log`` 格式。

        约定：FIFO 配对，单一 vt_symbol 单方向连续 open 视为加仓（按总量平均价）。
        commission 为单边成本，输出 cost = commission * 2（双边），net_pnl = gross_pnl - cost。
        """
        df = self.to_dataframe()
        if df.empty:
            return pd.DataFrame(
                columns=["entry_i", "exit_i", "side", "lots",
                         "entry_price", "exit_price", "gross_pnl", "cost", "net_pnl"]
            )
        df = df.sort_values("datetime").reset_index(drop=True)
        out_rows: list[dict] = []
        open_pos: dict[str, list[dict]] = {"long": [], "short": []}  # 队列
        for i, row in df.iterrows():
            offs = str(row["offset"]).lower()
            direc = str(row["direction"]).lower()
            px = float(row["price"])
            vol = float(row["volume"])
            if "open" in offs:
                # long open / short open
                side = "long" if "long" in direc else "short"
                open_pos[side].append({"i": i, "price": px, "vol": vol})
                continue
            # close: 对手方向匹配
            counter_side = "long" if "short" in direc else "short"
            queue = open_pos[counter_side]
            remaining = vol
            while remaining > 1e-9 and queue:
                head = queue[0]
                used = min(head["vol"], remaining)
                gross = (px - head["price"]) * used * float(multiplier)
                if counter_side == "short":
                    gross = -gross
                cost = float(commission) * 2.0 * used
                out_rows.append(
                    {
                        "entry_i": int(head["i"]),
                        "exit_i": int(i),
                        "side": counter_side,
                        "lots": int(used),
                        "entry_price": float(head["price"]),
                        "exit_price": float(px),
                        "gross_pnl": float(gross),
                        "cost": float(cost),
                        "net_pnl": float(gross - cost),
                    }
                )
                head["vol"] -= used
                remaining -= used
                if head["vol"] <= 1e-9:
                    queue.pop(0)
        return pd.DataFrame(out_rows)


__all__ = ["TradeRecorder"]
