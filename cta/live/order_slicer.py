"""Order slicing utilities for sim/live execution."""
from __future__ import annotations

from dataclasses import dataclass
from math import floor


@dataclass(frozen=True)
class OrderSliceConfig:
    """Static slicing config."""

    max_order_volume: int = 5
    adv_participation_rate: float = 0.10
    method: str = "iceberg"  # iceberg | twap
    twap_child_count: int = 4
    min_child_lots: int = 1


def _sanitize_lots(value: int | float) -> int:
    v = int(value)
    return max(v, 0)


def _child_cap(*, adv_lots: float | None, cfg: OrderSliceConfig) -> int:
    cap = max(int(cfg.max_order_volume), int(cfg.min_child_lots))
    if adv_lots is None:
        return cap
    adv = float(adv_lots)
    if adv <= 0:
        return cap
    adv_cap = int(floor(adv * float(cfg.adv_participation_rate)))
    adv_cap = max(adv_cap, int(cfg.min_child_lots))
    return max(int(cfg.min_child_lots), min(cap, adv_cap))


def _slice_iceberg(total_lots: int, cap: int) -> list[int]:
    remain = _sanitize_lots(total_lots)
    out: list[int] = []
    while remain > 0:
        x = min(cap, remain)
        out.append(int(x))
        remain -= int(x)
    return out


def _slice_twap(total_lots: int, cap: int, child_count: int) -> list[int]:
    n = max(1, int(child_count))
    total = _sanitize_lots(total_lots)
    if total == 0:
        return []
    # 先均分，再对超 cap 的子单二次切分
    base = total // n
    rem = total % n
    rough = [base + (1 if i < rem else 0) for i in range(n)]
    out: list[int] = []
    for lots in rough:
        if lots <= 0:
            continue
        if lots <= cap:
            out.append(int(lots))
            continue
        out.extend(_slice_iceberg(lots, cap))
    return out


def slice_order(*, total_lots: int | float, adv_lots: float | None, cfg: OrderSliceConfig) -> list[int]:
    """Slice one parent order to child lots.

    约束：
    - 每个 child <= ``max_order_volume``
    - 若提供 ``adv_lots``，每个 child 还需 <= ``adv_lots * adv_participation_rate``
    """
    total = _sanitize_lots(total_lots)
    if total <= 0:
        return []
    cap = _child_cap(adv_lots=adv_lots, cfg=cfg)
    method = str(cfg.method).strip().lower()
    if method == "twap":
        out = _slice_twap(total, cap, int(cfg.twap_child_count))
    else:
        out = _slice_iceberg(total, cap)
    if sum(out) != total:
        # 理论上不会触发；兜底修正
        diff = total - sum(out)
        if diff > 0:
            out.extend(_slice_iceberg(diff, cap))
    return [int(x) for x in out if int(x) > 0]


__all__ = ["OrderSliceConfig", "slice_order"]
