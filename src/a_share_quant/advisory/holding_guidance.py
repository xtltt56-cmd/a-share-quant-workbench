"""Manual-only protection and reduction guidance for imported holdings."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from a_share_quant.advisory.price_contracts import PriceGuidancePlan


@dataclass(frozen=True)
class HoldingGuidanceResult:
    symbol: str
    state: str
    current_price: Decimal
    average_cost: Decimal
    total_quantity: int
    available_quantity: int
    protection_price: Decimal | None
    reduce_lower: Decimal | None
    reduce_upper: Decimal | None
    suggested_sell_quantity: int
    manual_execution_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "state": self.state,
            "current_price": _format(self.current_price),
            "average_cost": _format(self.average_cost),
            "total_quantity": self.total_quantity,
            "available_quantity": self.available_quantity,
            "protection_price": _format(self.protection_price),
            "reduce_lower": _format(self.reduce_lower),
            "reduce_upper": _format(self.reduce_upper),
            "suggested_sell_quantity": self.suggested_sell_quantity,
            "manual_execution_required": self.manual_execution_required,
            "notice_zh": "仅供人工复核，不构成强制卖出结论。",
        }


class HoldingPriceGuidanceEngine:
    def evaluate(
        self,
        holding: Mapping[str, Any],
        plan: PriceGuidancePlan,
        *,
        current_price: Decimal | float | int | str,
        edge_weakened: bool = False,
        overheated: bool = False,
        portfolio_risk: bool = False,
    ) -> HoldingGuidanceResult:
        price = Decimal(str(current_price))
        if not price.is_finite() or price <= 0:
            raise ValueError("current price must be positive")
        total = _quantity(holding.get("total_quantity", 0))
        available = _quantity(holding.get("available_quantity", total))
        if available > total:
            raise ValueError("available quantity cannot exceed total quantity")
        cost = Decimal(str(holding.get("average_cost", "0")))
        protection = plan.protection_price
        if protection is not None and "previous_protection" in holding:
            protection = max(protection, Decimal(str(holding["previous_protection"])))
        if protection is not None and price <= protection:
            state = "RISK_ALERT" if available > 0 else "T_PLUS_ONE_BLOCKED"
            sell = available if state == "RISK_ALERT" else 0
        elif (
            plan.reduce_lower is not None
            and plan.reduce_upper is not None
            and plan.reduce_lower <= price <= plan.reduce_upper
            and (edge_weakened or overheated or portfolio_risk)
        ):
            state = "REDUCE_WATCH"
            sell = 0
        else:
            state = "HOLD_WATCH"
            sell = 0
        return HoldingGuidanceResult(
            symbol=plan.symbol,
            state=state,
            current_price=price,
            average_cost=cost,
            total_quantity=total,
            available_quantity=available,
            protection_price=protection,
            reduce_lower=plan.reduce_lower,
            reduce_upper=plan.reduce_upper,
            suggested_sell_quantity=sell,
        )


def _quantity(value: Any) -> int:
    parsed = int(value)
    if parsed < 0:
        raise ValueError("quantity must be non-negative")
    return parsed


def _format(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


__all__ = ["HoldingGuidanceResult", "HoldingPriceGuidanceEngine"]
