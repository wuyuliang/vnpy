"""Risk-band and capital-cap position sizing."""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class RiskBandDecision:
    quantity: int
    risk_budget: float
    loss_per_lot: float
    capital_basis: str
    capital_share: float
    reason: str = ""
    single_lot_relief: bool = False


def size_for_risk_band(
    *,
    equity: float,
    entry: float,
    stop: float,
    multiplier: float,
    stressed_round_trip_cost: float,
    min_risk_pct: float,
    max_risk_pct: float,
    max_capital_share: float,
    margin_rate: float | None,
    single_lot_capital_share: float | None = None,
) -> RiskBandDecision:
    """Size within the loss band, then enforce margin or notional capital use.

    ``single_lot_capital_share`` is the relief valve for expensive contracts:
    when one lot does not fit inside ``max_capital_share``, one lot is still
    allowed as long as it fits inside this wider share, and is refused outright
    beyond it. Without it an expensive contract is either never traded or
    traded at whatever share one lot happens to cost — the relief makes the
    ceiling explicit instead of implicit.
    """
    values = (equity, entry, stop, multiplier)
    if not all(math.isfinite(value) and value > 0 for value in values):
        raise ValueError("equity, prices, and multiplier must be finite and positive")
    if not math.isfinite(stressed_round_trip_cost) or stressed_round_trip_cost < 0:
        raise ValueError("stressed_round_trip_cost must be finite and nonnegative")
    if not 0 < min_risk_pct < max_risk_pct < 1:
        raise ValueError("risk percentages must satisfy 0 < min < max < 1")
    if not 0 < max_capital_share <= 1:
        raise ValueError("max_capital_share must be in (0, 1]")
    relief_share = (
        max_capital_share
        if single_lot_capital_share is None
        else float(single_lot_capital_share)
    )
    if not max_capital_share <= relief_share <= 1:
        raise ValueError(
            "single_lot_capital_share must be between max_capital_share and 1"
        )

    loss_per_lot = abs(stop - entry) * multiplier + stressed_round_trip_cost
    if loss_per_lot <= 0:
        raise ValueError("entry and stop must differ")
    risk_budget = equity * max_risk_pct
    risk_lots = math.floor(risk_budget / loss_per_lot)
    minimum_risk = equity * min_risk_pct
    if risk_lots * loss_per_lot < minimum_risk:
        risk_lots = math.ceil(minimum_risk / loss_per_lot)
    if risk_lots < 1 or risk_lots * loss_per_lot > risk_budget:
        risk_lots = 1

    use_margin = (
        margin_rate is not None
        and math.isfinite(float(margin_rate))
        and float(margin_rate) > 0
    )
    capital_basis = "margin" if use_margin else "notional"
    capital_per_lot = entry * multiplier
    if use_margin:
        capital_per_lot *= float(margin_rate)
    capital_lots = math.floor(equity * max_capital_share / capital_per_lot)
    single_lot_relief = False
    if capital_lots < 1 and capital_per_lot <= equity * relief_share:
        # 一手装不进常规上限，但装得进放宽上限：按一手开，并且记下来
        capital_lots = 1
        single_lot_relief = True
    quantity = min(risk_lots, capital_lots)

    if capital_lots < 1:
        reason = "CAPITAL_LIMIT"
    elif single_lot_relief:
        # 放宽档只有"一手"这一个选项，加不了手数。此时再用"风险太小"去拒绝，
        # 等于同时嫌它资金占用太大、风险敞口太小 —— 资金约束优先。
        reason = ""
    elif quantity * loss_per_lot < minimum_risk:
        reason = "RISK_BELOW_MINIMUM"
    else:
        reason = ""
    effective_quantity = quantity if not reason else 0
    capital_share = effective_quantity * capital_per_lot / equity
    return RiskBandDecision(
        quantity=effective_quantity,
        risk_budget=risk_budget,
        loss_per_lot=loss_per_lot,
        capital_basis=capital_basis,
        capital_share=capital_share,
        reason=reason,
        single_lot_relief=single_lot_relief and not reason,
    )
