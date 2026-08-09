"""Service boundary that makes manual and imported ledger writes durable."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from pathlib import Path
from typing import Any

from .importer import ImportPreview, preview_broker_rows, read_broker_file
from .ledger import AccountLedger, LedgerReceipt
from .store import JsonlLedgerStore


class AccountEntryService:
    """Coordinates preview, durable append, and in-memory ledger replay.

    A preview is always validated before any local JSONL append. Idempotent
    import event IDs make a repeated confirmation safe after a process restart.
    """

    def __init__(self, *, ledger: AccountLedger, store: JsonlLedgerStore) -> None:
        self.ledger = ledger
        self.store = store
        self._previews: dict[str, ImportPreview] = {}

    def preview_rows(
        self,
        *,
        rows: Iterable[Mapping[str, Any]],
        mapping: Mapping[str, str],
        default_trade_date: date,
        source_bytes: bytes,
    ) -> ImportPreview:
        preview = preview_broker_rows(
            rows=rows,
            mapping=mapping,
            default_trade_date=default_trade_date,
            source_bytes=source_bytes,
        )
        self._previews[preview.preview_id] = preview
        return preview

    def preview_file(
        self,
        *,
        source: Path,
        mapping: Mapping[str, str],
        default_trade_date: date,
    ) -> ImportPreview:
        rows, source_bytes = read_broker_file(source)
        return self.preview_rows(
            rows=rows,
            mapping=mapping,
            default_trade_date=default_trade_date,
            source_bytes=source_bytes,
        )

    def confirm_preview(self, preview_id: str) -> tuple[LedgerReceipt, ...]:
        preview = self._previews.get(preview_id)
        if preview is None:
            raise ValueError("unknown or expired import preview")
        prospective = self._copy_ledger()
        prospective_receipts = [
            prospective.record_fill(event) for event in preview.candidate_events
        ]
        receipts: list[LedgerReceipt] = []
        pairs = zip(preview.candidate_events, prospective_receipts, strict=True)
        for event, prospective_receipt in pairs:
            if not prospective_receipt.idempotent:
                self.store.append_fill(event)
            receipt = self.ledger.record_fill(event)
            if receipt.idempotent != prospective_receipt.idempotent:
                raise RuntimeError("ledger idempotency changed during confirmation")
            receipts.append(receipt)
        return tuple(receipts)

    def _copy_ledger(self) -> AccountLedger:
        return AccountLedger.from_records(
            initial_cash=self.ledger.initial_cash,
            records=self.ledger.records(),
            fee_schedule=self.ledger.fee_schedule,
            known_instruments=self.ledger.known_instruments,
            next_trading_day=self.ledger.next_trading_day,
            lot_size=self.ledger.lot_size,
        )
