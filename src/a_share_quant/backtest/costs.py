"""A-share order-size and transaction-cost rules shared by research paths."""

from __future__ import annotations

from dataclasses import dataclass
from math import floor, isfinite
from typing import Any


@dataclass(frozen=True)
class FeeBreakdown:
    side: str
    price: float
    quantity: int
    notional: float
    slippage: float
    commission: float
    stamp_duty: float
    transfer_fee: float

    @property
    def total(self) -> float:
        return self.slippage + self.commission + self.stamp_duty + self.transfer_fee


@dataclass(frozen=True)
class AshareCostModel:
    """Conservative cash-cost model for ordinary long-only A-share orders."""

    commission_rate: float = 0.0003
    minimum_commission: float = 5.0
    stamp_duty_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    slippage_bps: float = 5.0
    lot_size: int = 100

    def __post_init__(self) -> None:
        for name in (
            "commission_rate",
            "minimum_commission",
            "stamp_duty_rate",
            "transfer_fee_rate",
            "slippage_bps",
        ):
            value = float(getattr(self, name))
            if not isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if int(self.lot_size) != self.lot_size or int(self.lot_size) <= 0:
            raise ValueError("lot_size must be a positive integer")
        object.__setattr__(self, "lot_size", int(self.lot_size))

    def fillable_quantity(self, quantity: int | float) -> int:
        value = float(quantity)
        if not isfinite(value) or value < 0:
            raise ValueError("quantity must be finite and non-negative")
        return floor(value / self.lot_size) * self.lot_size

    def estimate(self, *, side: str, price: float, quantity: int | float) -> FeeBreakdown:
        normalized_side = str(side).strip().upper()
        if normalized_side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        numeric_price = float(price)
        if not isfinite(numeric_price) or numeric_price <= 0:
            raise ValueError("price must be finite and positive")
        filled_quantity = self.fillable_quantity(quantity)
        if filled_quantity <= 0 or float(quantity) != filled_quantity:
            raise ValueError("quantity must be a positive whole lot")
        notional = numeric_price * filled_quantity
        slippage = notional * self.slippage_bps / 10_000
        commission = max(notional * self.commission_rate, self.minimum_commission)
        stamp_duty = notional * self.stamp_duty_rate if normalized_side == "SELL" else 0.0
        transfer_fee = notional * self.transfer_fee_rate
        return FeeBreakdown(
            side=normalized_side,
            price=numeric_price,
            quantity=filled_quantity,
            notional=notional,
            slippage=slippage,
            commission=commission,
            stamp_duty=stamp_duty,
            transfer_fee=transfer_fee,
        )

    @classmethod
    def from_execution_spec(cls, execution_spec: Any) -> AshareCostModel:
        """Translate the existing execution contract without importing config."""

        return cls(
            commission_rate=float(execution_spec.commission),
            minimum_commission=float(getattr(execution_spec, "minimum_commission", 5.0)),
            stamp_duty_rate=float(execution_spec.tax),
            transfer_fee_rate=float(getattr(execution_spec, "transfer_fee", 0.00001)),
            slippage_bps=float(execution_spec.slippage) * 10_000,
            lot_size=int(getattr(execution_spec, "lot_size", 100)),
        )
