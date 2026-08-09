"""Explicit end-of-day boundary from provisional intraday data to daily signals."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from a_share_quant.signals.realtime import OfficialModelSignal
from a_share_quant.storage.official_signal_store import OfficialSignalStore
from a_share_quant.storage.realtime_store import EODFinalizationReceipt, RealTimeStore


@dataclass(frozen=True)
class EODPipelineResult:
    trade_date: date
    status: str
    steps: tuple[str, ...]
    receipt: EODFinalizationReceipt | None
    official_signals: tuple[OfficialModelSignal, ...]
    error: str = ""


class EODPipeline:
    """Run EOD stages in order and expose official signals only on success."""

    def __init__(
        self,
        *,
        store: RealTimeStore,
        confirm_close: Callable[[date], bool],
        load_final_daily: Callable[[date], Iterable[Any]],
        reconcile: Callable[[tuple[Any, ...], tuple[Any, ...]], Iterable[Any]],
        update_pit: Callable[[tuple[Any, ...]], None],
        generate_official_signals: Callable[[tuple[Any, ...]], Iterable[OfficialModelSignal]],
        write_report: Callable[[tuple[OfficialModelSignal, ...]], None],
        official_signal_store: OfficialSignalStore | None = None,
    ) -> None:
        self.store = store
        self.confirm_close = confirm_close
        self.load_final_daily = load_final_daily
        self.reconcile = reconcile
        self.update_pit = update_pit
        self.generate_official_signals = generate_official_signals
        self.write_report = write_report
        self.official_signal_store = official_signal_store

    def run(self, trade_date: date | str | datetime) -> EODPipelineResult:
        parsed_date = _parse_date(trade_date)
        steps: list[str] = []
        receipt: EODFinalizationReceipt | None = None
        try:
            if not self.confirm_close(parsed_date):
                return EODPipelineResult(parsed_date, "WAITING_CLOSE", tuple(steps), None, ())
            steps.append("market_close_confirmed")
            final_daily = tuple(self.load_final_daily(parsed_date))
            steps.append("final_daily_loaded")
            receipt = self.store.finalize_eod(
                trade_date=parsed_date,
                reconciler=lambda bars: self.reconcile(tuple(bars), final_daily),
            )
            steps.append("intraday_reconciled")
            self.update_pit(final_daily)
            steps.append("pit_updated")
            signals = tuple(self.generate_official_signals(final_daily))
            if any(signal.frequency.value != "daily" for signal in signals):
                raise ValueError("EOD pipeline received a non-daily official signal")
            steps.append("official_daily_signals_generated")
            if self.official_signal_store is not None:
                self.official_signal_store.put_signals(signals)
            steps.append("official_daily_signals_stored")
            self.write_report(signals)
            steps.append("candidate_report_written")
            return EODPipelineResult(parsed_date, "COMPLETED", tuple(steps), receipt, signals)
        except Exception as exc:  # keep external data/provider details out of results
            return EODPipelineResult(
                parsed_date,
                "FAILED",
                tuple(steps),
                receipt,
                (),
                error=type(exc).__name__,
            )


def _parse_date(value: date | str | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()
