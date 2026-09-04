"""Shared entry/stop/target geometry for every enabled setup."""
from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

from ...config import BrooksCycleConfig
from ..types import PlanGeometry


class BlockedPlanError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def round_for_order(
    raw: float,
    order_side: str,
    order_kind: str,
    order_role: str,
    tick: float,
) -> float:
    if order_side not in {"BUY", "SELL"}:
        raise ValueError("invalid order side")
    if order_kind not in {"stop", "limit"}:
        raise ValueError("invalid order kind")
    if (
        order_role not in {"entry", "protective_stop", "profit_target"}
        or not math.isfinite(tick)
        or tick <= 0
    ):
        raise ValueError("invalid order rounding input")
    if order_role == "protective_stop" and order_kind != "stop":
        raise ValueError("protective stop requires stop order semantics")
    if not math.isfinite(raw):
        raise ValueError("raw order price must be finite")
    units = raw / tick
    round_up = (order_kind == "stop" and order_side == "BUY") or (
        order_kind == "limit" and order_side == "SELL"
    )
    rounded_units = math.ceil(units - 1e-12) if round_up else math.floor(units + 1e-12)
    return float(rounded_units * tick)


def build_plan_geometry(
    features: Mapping[str, Any],
    direction: int,
    entry: float,
    structural_stop_candidate: float,
    planned_target: float | None,
    metadata: Any,
    config: BrooksCycleConfig,
) -> PlanGeometry:
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    atr = float(features["atr"])
    tick = float(metadata.price_tick)
    mechanics = (atr, tick, float(metadata.contract_size), entry, structural_stop_candidate)
    if not all(math.isfinite(value) and value > 0 for value in mechanics):
        raise BlockedPlanError("INVALID_PLAN_MECHANICS")
    structural_distance = direction * (entry - structural_stop_candidate)
    stop_distance = max(
        structural_distance,
        config.execution.stop_atr_floor * atr,
        config.execution.stop_min_ticks * tick,
    )
    raw_stop = entry - direction * stop_distance
    exit_side = "SELL" if direction == 1 else "BUY"
    rounded_stop = round_for_order(raw_stop, exit_side, "stop", "protective_stop", tick)
    actual_stop_distance = abs(entry - rounded_stop)
    if actual_stop_distance <= 0:
        raise BlockedPlanError("NON_POSITIVE_STOP_DISTANCE")

    obstacle_candidates = _obstacles(features, direction, metadata)
    ahead = [
        item for item in obstacle_candidates
        if math.isfinite(item[0]) and direction * (item[0] - entry) > 0
    ]
    if not ahead:
        raise BlockedPlanError("NO_CAUSAL_DIRECTIONAL_OBSTACLE")
    priority = {"PRICE_LIMIT": 0, "CONFIRMED_SWING": 1, "RANGE_BOUNDARY": 2, "HIGHER_TF": 3}
    nearest_price, obstacle_kind = min(
        ahead,
        key=lambda item: (direction * (item[0] - entry), priority[item[1]]),
    )
    target_candidates = [
        value for value in (planned_target, nearest_price)
        if value is not None and math.isfinite(float(value)) and direction * (float(value) - entry) > 0
    ]
    if not target_candidates:
        raise BlockedPlanError("NO_CAUSAL_POSITIVE_TARGET")
    effective_target = min(target_candidates, key=lambda value: direction * (float(value) - entry))
    rounded_target = round_for_order(
        float(effective_target), exit_side, "limit", "profit_target", tick
    )
    gross_space_price = direction * (nearest_price - entry)
    gross_reward_price = direction * (rounded_target - entry)
    if gross_reward_price <= 0:
        raise BlockedPlanError("TARGET_ROUNDING_REMOVED_REWARD")
    fee_price = float(metadata.stressed_round_trip_fee_cash) / float(metadata.contract_size)
    slippage_price = float(metadata.stressed_round_trip_slippage_ticks) * tick
    all_in_cost_price = fee_price + slippage_price
    directional_limit = float(metadata.limit_up if direction == 1 else metadata.limit_down)
    return PlanGeometry(
        entry=float(entry),
        stop=rounded_stop,
        stop_distance=actual_stop_distance,
        target=rounded_target,
        nearest_obstacle=nearest_price,
        gross_space_R=gross_space_price / actual_stop_distance,
        cost_R=all_in_cost_price / actual_stop_distance,
        net_space_R=(gross_space_price - all_in_cost_price) / actual_stop_distance,
        net_reward_R=(gross_reward_price - all_in_cost_price) / actual_stop_distance,
        near_price_limit=(
            direction * (directional_limit - entry)
            <= config.execution.limit_entry_buffer_atr * atr
        ),
        obstacle_kind=obstacle_kind,
    )


def geometry_tradeable(geometry: PlanGeometry, config: BrooksCycleConfig) -> bool:
    return bool(
        geometry.net_space_R >= config.execution.min_space_R
        and geometry.cost_R <= config.execution.max_cost_R
        and geometry.net_reward_R >= config.execution.min_net_reward_R
        and not geometry.near_price_limit
    )


def _obstacles(
    features: Mapping[str, Any],
    direction: int,
    metadata: Any,
) -> list[tuple[float, str]]:
    candidates: list[tuple[float, str]] = [
        (float(metadata.limit_up if direction == 1 else metadata.limit_down), "PRICE_LIMIT")
    ]
    swings = features.get("confirmed_directional_swing_obstacles", {}).get(direction, ())
    candidates.extend((float(value), "CONFIRMED_SWING") for value in swings if value is not None)
    boundary = features.get("range_high" if direction == 1 else "range_low")
    if boundary is not None:
        candidates.append((float(boundary), "RANGE_BOUNDARY"))
    higher = features.get("completed_higher_tf_obstacles", {}).get(direction, ())
    candidates.extend((float(value), "HIGHER_TF") for value in higher if value is not None)
    return candidates


__all__ = [
    "BlockedPlanError", "build_plan_geometry", "geometry_tradeable", "round_for_order"
]
