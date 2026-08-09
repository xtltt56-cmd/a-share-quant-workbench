"""Value objects for the local append-only account ledger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any

from a_share_quant.data.normalization import normalize_symbol

_MONEY = Decimal("0.01")
_PRICE = Decimal("0.0001")


def as_decimal(value: Decimal | float | int | str, *, field: str) -> Decimal:
    """Parse a positive finite decimal value without binary-float arithmetic."""

    try:
        parsed = Decimal(str(value))
    except Exception as exc:  # Decimal raises several implementation-specific subclasses
        raise ValueError(f"{field} must be a decimal number") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field} must be finite")
    return parsed


def money(value: Decimal | float | int | str) -> Decimal:
    return as_decimal(value, field="money").quantize(_MONEY, rounding=ROUND_HALF_UP)


def price(value: Decimal | float | int | str) -> Decimal:
    return as_decimal(value, field="price").quantize(_PRICE, rounding=ROUND_HALF_UP)


def exchange_for_symbol(symbol: str) -> str:
    """Classify ordinary A-share codes for fee handling, not tradability."""

    normalized = normalize_symbol(symbol)
    return "SSE" if normalized.startswith(("5", "6", "9")) else "SZSE"


class TradeSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class FeeBreakdown:
    commission: Decimal
    stamp_duty: Decimal
    transfer_fee: Decimal

    @property
    def total(self) -> Decimal:
        return money(self.commission + self.stamp_duty + self.transfer_fee)


@dataclass(frozen=True)
class FeeSchedule:
    """Conservative configurable cash-account fee schedule.

    Commission has a broker-style minimum. Stamp duty is charged on sales only;
    the small transfer fee is modelled only for SSE codes. Actual broker statements
    can later replace this estimate through a correction event.
    """

    commission_rate: Decimal = Decimal("0.0003")
    minimum_commission: Decimal = Decimal("5")
    sell_stamp_duty_rate: Decimal = Decimal("0.0005")
    sse_transfer_fee_rate: Decimal = Decimal("0.00001")

    def __post_init__(self) -> None:
        for name in (
            "commission_rate",
            "minimum_commission",
            "sell_stamp_duty_rate",
            "sse_transfer_fee_rate",
        ):
            value = as_decimal(getattr(self, name), field=name)
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
            object.__setattr__(self, name, value)

    def calculate(self, *, side: TradeSide, symbol: str, notional: Decimal) -> FeeBreakdown:
        if notional <= 0:
            raise ValueError("notional must be positive")
        commission = money(max(notional * self.commission_rate, self.minimum_commission))
        stamp_duty = (
            money(notional * self.sell_stamp_duty_rate)
            if side is TradeSide.SELL
            else Decimal("0.00")
        )
        transfer_fee = (
            money(notional * self.sse_transfer_fee_rate)
            if exchange_for_symbol(symbol) == "SSE"
            else Decimal("0.00")
        )
        return FeeBreakdown(
            commission=commission,
            stamp_duty=stamp_duty,
            transfer_fee=transfer_fee,
        )


@dataclass(frozen=True)
class FillEvent:
    """One immutable manual or imported fill; it never represents an order."""

    event_id: str
    side: TradeSide | str
    symbol: str
    quantity: int
    price: Decimal | float | int | str
    trade_date: date
    name: str = ""
    source: str = "manual"
    recorded_at: datetime | None = None

    def __post_init__(self) -> None:
        if not str(self.event_id).strip():
            raise ValueError("event_id is required")
        object.__setattr__(self, "event_id", str(self.event_id).strip())
        parsed_side = (
            self.side if isinstance(self.side, TradeSide) else TradeSide(str(self.side).upper())
        )
        object.__setattr__(self, "side", parsed_side)
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        if (
            not isinstance(self.quantity, int)
            or isinstance(self.quantity, bool)
            or self.quantity <= 0
        ):
            raise ValueError("quantity must be a positive integer")
        parsed_price = price(self.price)
        if parsed_price <= 0:
            raise ValueError("price must be positive")
        object.__setattr__(self, "price", parsed_price)
        if not isinstance(self.trade_date, date):
            raise ValueError("trade_date must be a date")
        if not str(self.source).strip():
            raise ValueError("source is required")
        object.__setattr__(self, "name", str(self.name).strip())
        object.__setattr__(self, "source", str(self.source).strip())
        recorded_at = self.recorded_at or datetime.now(timezone.utc)
        if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
            raise ValueError("recorded_at must be timezone-aware")
        object.__setattr__(self, "recorded_at", recorded_at.astimezone(timezone.utc))

    @property
    def notional(self) -> Decimal:
        return money(self.price * self.quantity)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "side": self.side.value,
            "symbol": self.symbol,
            "quantity": self.quantity,
            "price": str(self.price),
            "trade_date": self.trade_date.isoformat(),
            "name": self.name,
            "source": self.source,
            "recorded_at": self.recorded_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FillEvent:
        return cls(
            event_id=payload["event_id"],
            side=payload["side"],
            symbol=payload["symbol"],
            quantity=int(payload["quantity"]),
            price=payload["price"],
            trade_date=date.fromisoformat(payload["trade_date"]),
            name=payload.get("name", ""),
            source=payload.get("source", "manual"),
            recorded_at=datetime.fromisoformat(payload["recorded_at"]),
        )


@dataclass(frozen=True)
class LedgerCorrection:
    """An append-only record that voids one fill and replaces its accounting effect."""

    correction_id: str
    original_event_id: str
    replacement: FillEvent
    recorded_at: datetime | None = None

    def __post_init__(self) -> None:
        if not str(self.correction_id).strip() or not str(self.original_event_id).strip():
            raise ValueError("correction_id and original_event_id are required")
        object.__setattr__(self, "correction_id", str(self.correction_id).strip())
        object.__setattr__(self, "original_event_id", str(self.original_event_id).strip())
        recorded_at = self.recorded_at or datetime.now(timezone.utc)
        if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
            raise ValueError("recorded_at must be timezone-aware")
        object.__setattr__(self, "recorded_at", recorded_at.astimezone(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "correction_id": self.correction_id,
            "original_event_id": self.original_event_id,
            "replacement": self.replacement.to_dict(),
            "recorded_at": self.recorded_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> LedgerCorrection:
        return cls(
            correction_id=payload["correction_id"],
            original_event_id=payload["original_event_id"],
            replacement=FillEvent.from_dict(payload["replacement"]),
            recorded_at=datetime.fromisoformat(payload["recorded_at"]),
        )


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    name: str
    total_quantity: int
    available_quantity: int
    frozen_quantity: int
    average_cost: Decimal


@dataclass(frozen=True)
class AccountSnapshot:
    as_of: date
    cash: Decimal
    realized_pnl: Decimal
    positions: tuple[PositionSnapshot, ...]

    def position(self, symbol: str) -> PositionSnapshot | None:
        normalized = normalize_symbol(symbol)
        return next((item for item in self.positions if item.symbol == normalized), None)
