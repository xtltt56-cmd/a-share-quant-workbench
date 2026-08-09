"""Deterministic, append-only manual A-share cash-account accounting."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from a_share_quant.data.normalization import normalize_symbol

from .contracts import (
    AccountSnapshot,
    FeeSchedule,
    FillEvent,
    LedgerCorrection,
    PositionSnapshot,
    TradeSide,
    money,
)


def _next_weekday(value: date) -> date:
    candidate = value + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


@dataclass(frozen=True)
class LedgerReceipt:
    event: FillEvent
    cash_after: Decimal
    position: PositionSnapshot
    realized_pnl: Decimal
    idempotent: bool = False


@dataclass
class _Lot:
    symbol: str
    name: str
    quantity: int
    remaining_quantity: int
    remaining_cost: Decimal
    available_from: date


class AccountLedger:
    """Local account state reconstructed solely from recorded fills.

    This class has no broker connectivity. The only mutation is appending an
    immutable fill, and duplicate caller-supplied event IDs are idempotent.
    """

    def __init__(
        self,
        *,
        initial_cash: Decimal | float | int | str,
        fee_schedule: FeeSchedule | None = None,
        known_instruments: Mapping[str, str] | None = None,
        next_trading_day: Callable[[date], date] = _next_weekday,
        lot_size: int = 100,
    ) -> None:
        initial = money(initial_cash)
        if initial < 0:
            raise ValueError("initial_cash must be non-negative")
        if lot_size <= 0:
            raise ValueError("lot_size must be positive")
        self.initial_cash = initial
        self.fee_schedule = fee_schedule or FeeSchedule()
        self.known_instruments = {
            normalize_symbol(symbol): str(name).strip()
            for symbol, name in (known_instruments or {}).items()
        }
        if any(not name for name in self.known_instruments.values()):
            raise ValueError("known instrument names cannot be empty")
        self.next_trading_day = next_trading_day
        self.lot_size = lot_size
        self._events: list[FillEvent] = []
        self._events_by_id: dict[str, FillEvent] = {}
        self._corrections: list[LedgerCorrection] = []
        self._voided_event_ids: set[str] = set()

    @classmethod
    def from_records(
        cls,
        *,
        initial_cash: Decimal | float | int | str,
        records: Iterable[FillEvent | LedgerCorrection],
        fee_schedule: FeeSchedule | None = None,
        known_instruments: Mapping[str, str] | None = None,
        next_trading_day: Callable[[date], date] = _next_weekday,
        lot_size: int = 100,
    ) -> AccountLedger:
        """Reconstruct state from verified append-only records in their log order."""

        ledger = cls(
            initial_cash=initial_cash,
            fee_schedule=fee_schedule,
            known_instruments=known_instruments,
            next_trading_day=next_trading_day,
            lot_size=lot_size,
        )
        for record in records:
            if isinstance(record, FillEvent):
                ledger._append(record)
            elif isinstance(record, LedgerCorrection):
                ledger.append_correction(
                    original_event_id=record.original_event_id,
                    replacement=record.replacement,
                    correction_id=record.correction_id,
                )
            else:
                raise TypeError("unsupported ledger record")
        return ledger

    def events(self) -> tuple[FillEvent, ...]:
        return tuple(self._events)

    def corrections(self) -> tuple[LedgerCorrection, ...]:
        return tuple(self._corrections)

    def record_buy(
        self,
        *,
        name: str,
        symbol: str,
        quantity: int,
        price: Decimal | float | int | str,
        trade_date: date,
        event_id: str | None = None,
        source: str = "manual",
    ) -> LedgerReceipt:
        normalized = normalize_symbol(symbol)
        self._validate_name(normalized, name)
        if quantity % self.lot_size != 0:
            raise ValueError(f"buy quantity must be a multiple of {self.lot_size}")
        return self._append(
            FillEvent(
                event_id=event_id or f"manual-buy-{uuid4().hex}",
                side=TradeSide.BUY,
                symbol=normalized,
                quantity=quantity,
                price=price,
                trade_date=trade_date,
                name=name,
                source=source,
            )
        )

    def record_sell(
        self,
        *,
        symbol: str,
        quantity: int,
        price: Decimal | float | int | str,
        trade_date: date,
        event_id: str | None = None,
        source: str = "manual",
    ) -> LedgerReceipt:
        normalized = normalize_symbol(symbol)
        snapshot = self.snapshot(as_of=trade_date)
        current = snapshot.position(normalized)
        if current is None or current.available_quantity < quantity:
            raise ValueError("available quantity is insufficient for this sell")
        return self._append(
            FillEvent(
                event_id=event_id or f"manual-sell-{uuid4().hex}",
                side=TradeSide.SELL,
                symbol=normalized,
                quantity=quantity,
                price=price,
                trade_date=trade_date,
                name=current.name,
                source=source,
            )
        )

    def append_correction(
        self,
        *,
        original_event_id: str,
        replacement: FillEvent,
        correction_id: str | None = None,
    ) -> LedgerReceipt:
        """Append a correction while preserving the original fill for audit."""

        original = self._events_by_id.get(original_event_id)
        if original is None:
            raise ValueError("original event does not exist")
        if original_event_id in self._voided_event_ids:
            raise ValueError("original event has already been corrected")
        if replacement.event_id in self._events_by_id:
            raise ValueError("replacement event_id already exists")
        if replacement.side is not original.side or replacement.symbol != original.symbol:
            raise ValueError("replacement must keep the original side and symbol")
        if replacement.trade_date != original.trade_date:
            raise ValueError("replacement must keep the original trade_date")
        correction = LedgerCorrection(
            correction_id=correction_id or f"correction-{uuid4().hex}",
            original_event_id=original_event_id,
            replacement=replacement,
        )
        original_events = self._events
        original_index = self._events_by_id
        original_corrections = self._corrections
        original_voided = self._voided_event_ids
        self._events = [*self._events, replacement]
        self._events_by_id = {**self._events_by_id, replacement.event_id: replacement}
        self._corrections = [*self._corrections, correction]
        self._voided_event_ids = {*self._voided_event_ids, original_event_id}
        try:
            receipt = self._receipt_for(replacement, idempotent=False)
            self.snapshot()
        except Exception:
            self._events = original_events
            self._events_by_id = original_index
            self._corrections = original_corrections
            self._voided_event_ids = original_voided
            raise
        return receipt

    def snapshot(self, *, as_of: date | None = None) -> AccountSnapshot:
        effective_date = as_of or (self._events[-1].trade_date if self._events else date.today())
        cash = self.initial_cash
        realized_pnl = Decimal("0.00")
        lots: dict[str, list[_Lot]] = {}
        names: dict[str, str] = {}
        for event in self._effective_events():
            if event.trade_date > effective_date:
                continue
            if event.side is TradeSide.BUY:
                fees = self.fee_schedule.calculate(
                    side=event.side,
                    symbol=event.symbol,
                    notional=event.notional,
                )
                total_cost = money(event.notional + fees.total)
                if cash < total_cost:
                    raise ValueError("insufficient cash for this buy")
                cash = money(cash - total_cost)
                names[event.symbol] = event.name or names.get(event.symbol, "")
                lots.setdefault(event.symbol, []).append(
                    _Lot(
                        symbol=event.symbol,
                        name=event.name,
                        quantity=event.quantity,
                        remaining_quantity=event.quantity,
                        remaining_cost=total_cost,
                        available_from=self.next_trading_day(event.trade_date),
                    )
                )
                continue

            sell_fees = self.fee_schedule.calculate(
                side=event.side,
                symbol=event.symbol,
                notional=event.notional,
            )
            proceeds = money(event.notional - sell_fees.total)
            remaining_to_sell = event.quantity
            cost_basis = Decimal("0.00")
            for lot in lots.get(event.symbol, []):
                if lot.available_from > event.trade_date or remaining_to_sell <= 0:
                    continue
                take = min(lot.remaining_quantity, remaining_to_sell)
                if take <= 0:
                    continue
                allocation = (
                    lot.remaining_cost
                    if take == lot.remaining_quantity
                    else money(lot.remaining_cost * Decimal(take) / Decimal(lot.remaining_quantity))
                )
                lot.remaining_quantity -= take
                lot.remaining_cost = money(lot.remaining_cost - allocation)
                cost_basis = money(cost_basis + allocation)
                remaining_to_sell -= take
            if remaining_to_sell:
                raise ValueError("available quantity is insufficient for this sell")
            cash = money(cash + proceeds)
            realized_pnl = money(realized_pnl + proceeds - cost_basis)

        positions: list[PositionSnapshot] = []
        for symbol, symbol_lots in lots.items():
            total_quantity = sum(lot.remaining_quantity for lot in symbol_lots)
            total_cost = money(sum((lot.remaining_cost for lot in symbol_lots), Decimal("0.00")))
            available_quantity = sum(
                lot.remaining_quantity
                for lot in symbol_lots
                if lot.available_from <= effective_date
            )
            if total_quantity:
                average_cost = (total_cost / Decimal(total_quantity)).quantize(Decimal("0.0001"))
            else:
                average_cost = Decimal("0.0000")
            positions.append(
                PositionSnapshot(
                    symbol=symbol,
                    name=names.get(symbol, ""),
                    total_quantity=total_quantity,
                    available_quantity=available_quantity,
                    frozen_quantity=total_quantity - available_quantity,
                    average_cost=average_cost,
                )
            )
        return AccountSnapshot(
            as_of=effective_date,
            cash=cash,
            realized_pnl=realized_pnl,
            positions=tuple(sorted(positions, key=lambda item: item.symbol)),
        )

    def _append(self, event: FillEvent) -> LedgerReceipt:
        existing = self._events_by_id.get(event.event_id)
        if existing is not None:
            if _same_event(existing, event):
                return self._receipt_for(existing, idempotent=True)
            raise ValueError("event_id already exists with different details")
        if self._events and event.trade_date < max(item.trade_date for item in self._events):
            raise ValueError("trade_date cannot precede the latest recorded event")
        candidate_events = [*self._events, event]
        original_events = self._events
        self._events = candidate_events
        try:
            receipt = self._receipt_for(event, idempotent=False)
        except Exception:
            self._events = original_events
            raise
        self._events_by_id[event.event_id] = event
        return receipt

    def _receipt_for(self, event: FillEvent, *, idempotent: bool) -> LedgerReceipt:
        snapshot = self.snapshot(as_of=event.trade_date)
        position = snapshot.position(event.symbol)
        if position is None:
            position = PositionSnapshot(
                symbol=event.symbol,
                name=event.name,
                total_quantity=0,
                available_quantity=0,
                frozen_quantity=0,
                average_cost=Decimal("0.0000"),
            )
        return LedgerReceipt(
            event=event,
            cash_after=snapshot.cash,
            position=position,
            realized_pnl=snapshot.realized_pnl,
            idempotent=idempotent,
        )

    def _validate_name(self, symbol: str, name: str) -> None:
        normalized_name = str(name).strip()
        if not normalized_name:
            raise ValueError("name is required")
        expected = self.known_instruments.get(symbol)
        if expected is not None and expected != normalized_name:
            raise ValueError("name does not match the stock code")

    def _effective_events(self) -> tuple[FillEvent, ...]:
        return tuple(
            sorted(
                (event for event in self._events if event.event_id not in self._voided_event_ids),
                key=lambda item: (item.trade_date, item.recorded_at, item.event_id),
            )
        )


def _same_event(left: FillEvent, right: FillEvent) -> bool:
    return (
        left.side == right.side
        and left.symbol == right.symbol
        and left.quantity == right.quantity
        and left.price == right.price
        and left.trade_date == right.trade_date
        and left.name == right.name
        and left.source == right.source
    )
