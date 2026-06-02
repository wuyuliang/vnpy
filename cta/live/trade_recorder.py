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
from typing import Any, Callable

import pandas as pd

from cta.sim.timezone_helpers import ensure_naive_shanghai


def _safe_attr(obj: Any, name: str, default: Any = "") -> Any:
    val = getattr(obj, name, default)
    if hasattr(val, "value"):
        return val.value
    return val


class TradeRecorder:
    """append-only 成交记录，可 flush 到 parquet。"""

    def __init__(
        self,
        out_dir: str,
        vt_symbol: str,
        *,
        file_prefix: str = "trades",
        commission_resolver: Callable[[str, float, float], float] | None = None,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.vt_symbol = str(vt_symbol)
        self.file_prefix = str(file_prefix)
        self._commission_of = commission_resolver
        self._rows: list[dict] = []

    def record(self, trade: Any) -> None:
        px = float(getattr(trade, "price", 0.0) or 0.0)
        vol = float(getattr(trade, "volume", 0.0) or 0.0)
        ts = ensure_naive_shanghai(getattr(trade, "datetime", None) or pd.Timestamp.now())
        comm = float(getattr(trade, "commission", 0.0) or 0.0)
        if comm <= 0.0 and self._commission_of is not None and vol > 0.0:
            try:
                comm = float(self._commission_of(self.vt_symbol, px, vol))
            except Exception:  # noqa: BLE001
                comm = 0.0
        self._rows.append(
            {
                "datetime": ts,
                "vt_symbol": self.vt_symbol,
                "gateway_name": _safe_attr(trade, "gateway_name", ""),
                "symbol": _safe_attr(trade, "symbol", ""),
                "exchange": _safe_attr(trade, "exchange", ""),
                "orderid": _safe_attr(trade, "orderid", ""),
                "tradeid": _safe_attr(trade, "tradeid", ""),
                "direction": _safe_attr(trade, "direction", ""),
                "offset": _safe_attr(trade, "offset", ""),
                "price": px,
                "volume": vol,
                "commission": comm,
            }
        )

    def to_dataframe(self) -> pd.DataFrame:
        if not self._rows:
            return pd.DataFrame(
                columns=[
                    "datetime",
                    "vt_symbol",
                    "direction",
                    "offset",
                    "price",
                    "volume",
                    "commission",
                    "tradeid",
                ]
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
            row_comm = float(row.get("commission", 0.0) or 0.0)
            if "open" in offs:
                # long open / short open
                side = "long" if "long" in direc else "short"
                open_pos[side].append(
                    {
                        "i": i,
                        "price": px,
                        "vol": vol,
                        "comm_per_vol": row_comm / max(vol, 1e-12),
                    }
                )
                continue
            # close: 对手方向匹配
            counter_side = "long" if "short" in direc else "short"
            queue = open_pos[counter_side]
            remaining = vol
            close_comm_per_vol = row_comm / max(vol, 1e-12)
            while remaining > 1e-9 and queue:
                head = queue[0]
                used = min(head["vol"], remaining)
                gross = (px - head["price"]) * used * float(multiplier)
                if counter_side == "short":
                    gross = -gross
                entry_cost = float(head.get("comm_per_vol", 0.0)) * used
                exit_cost = close_comm_per_vol * used
                if entry_cost > 0.0 or exit_cost > 0.0:
                    cost = entry_cost + exit_cost
                else:
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
