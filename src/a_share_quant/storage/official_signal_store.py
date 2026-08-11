"""Independent, optionally durable store for official daily model candidates."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from a_share_quant.signals.realtime import OfficialModelSignal


class OfficialSignalStore:
    """Store daily model outputs without importing intraday overlay concerns."""

    _FORMAT_VERSION = 1
    _MAX_BYTES = 4 * 1024 * 1024
    _TOP_LEVEL_FIELDS = frozenset(
        {"format_version", "data_mode", "operating_mode", "signals"}
    )
    _SIGNAL_FIELDS = frozenset(
        {
            "signal_date",
            "symbol",
            "name",
            "normalized_score",
            "strategy_version",
            "frequency",
            "model_version",
            "feature_version",
            "data_mode",
            "source",
            "data_cutoff",
            "generated_at",
            "rank",
            "reasons",
            "reference_price",
            "average_amount",
            "invalidation_price",
        }
    )

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._signals: dict[tuple[date, str, str], OfficialModelSignal] = {}
        if self.path is not None and self.path.exists():
            self._load()

    def put_signals(self, signals: Iterable[OfficialModelSignal]) -> None:
        incoming = tuple(signals)
        for signal in incoming:
            if not isinstance(signal, OfficialModelSignal):
                raise TypeError("official signal store accepts only OfficialModelSignal")
            key = (signal.signal_date, signal.symbol, signal.strategy_version)
            self._signals[key] = signal
        if incoming and self.path is not None:
            self._persist()

    def signals(self, *, signal_date: date | None = None) -> tuple[OfficialModelSignal, ...]:
        selected = tuple(
            signal
            for signal in self._signals.values()
            if signal_date is None or signal.signal_date == signal_date
        )
        return tuple(
            sorted(
                sorted(
                    selected,
                    key=lambda item: (
                        -float(item.normalized_score),
                        item.symbol,
                        item.strategy_version,
                    ),
                ),
                key=lambda item: item.signal_date,
                reverse=True,
            )
        )

    def latest(self) -> tuple[OfficialModelSignal, ...]:
        if not self._signals:
            return ()
        latest_date = max(signal.signal_date for signal in self._signals.values())
        return tuple(
            sorted(
                (
                    signal
                    for signal in self._signals.values()
                    if signal.signal_date == latest_date
                ),
                key=lambda item: (
                    -float(item.normalized_score),
                    item.symbol,
                    item.strategy_version,
                ),
            )
        )

    def _load(self) -> None:
        assert self.path is not None
        path = self.path
        if path.is_symlink() or not path.is_file():
            raise ValueError("official signal artifact is invalid")
        try:
            raw = path.read_bytes()
            if not raw or len(raw) > self._MAX_BYTES:
                raise ValueError
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict) or set(payload) != self._TOP_LEVEL_FIELDS:
                raise ValueError
            if payload["format_version"] != self._FORMAT_VERSION:
                raise ValueError
            if payload["data_mode"] != "historical":
                raise ValueError
            if payload["operating_mode"] != "PAPER_ONLY":
                raise ValueError
            entries = payload["signals"]
            if not isinstance(entries, list) or not entries:
                raise ValueError
            loaded = tuple(self._decode_signal(item) for item in entries)
            if any(signal.data_mode != payload["data_mode"] for signal in loaded):
                raise ValueError
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as exc:
            raise ValueError("official signal artifact is invalid") from exc
        for signal in loaded:
            key = (signal.signal_date, signal.symbol, signal.strategy_version)
            if key in self._signals:
                raise ValueError("official signal artifact is invalid")
            self._signals[key] = signal

    def _persist(self) -> None:
        assert self.path is not None
        path = self.path
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise ValueError("official signal artifact is invalid")
        latest = self.latest()
        if not latest:
            return
        payload = {
            "format_version": self._FORMAT_VERSION,
            "data_mode": "historical",
            "operating_mode": "PAPER_ONLY",
            "signals": [self._encode_signal(signal) for signal in latest],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(
                    payload,
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            raise ValueError("official signal artifact cannot be written") from exc
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    @classmethod
    def _decode_signal(cls, payload: Any) -> OfficialModelSignal:
        if not isinstance(payload, dict) or set(payload) != cls._SIGNAL_FIELDS:
            raise ValueError("invalid official signal row")
        reasons = payload["reasons"]
        if not isinstance(reasons, list) or not all(isinstance(item, str) for item in reasons):
            raise ValueError("invalid official signal reasons")
        if payload["frequency"] != "daily":
            raise ValueError("invalid official signal frequency")
        return OfficialModelSignal(
            signal_date=date.fromisoformat(payload["signal_date"]),
            symbol=payload["symbol"],
            name=payload["name"],
            normalized_score=payload["normalized_score"],
            strategy_version=payload["strategy_version"],
            model_version=payload["model_version"],
            feature_version=payload["feature_version"],
            data_mode=payload["data_mode"],
            source=payload["source"],
            data_cutoff=date.fromisoformat(payload["data_cutoff"]),
            generated_at=datetime.fromisoformat(payload["generated_at"]),
            rank=payload["rank"],
            reasons=tuple(reasons),
            reference_price=payload["reference_price"],
            average_amount=payload["average_amount"],
            invalidation_price=payload["invalidation_price"],
        )

    @staticmethod
    def _encode_signal(signal: OfficialModelSignal) -> dict[str, Any]:
        return {
            "signal_date": signal.signal_date.isoformat(),
            "symbol": signal.symbol,
            "name": signal.name,
            "normalized_score": signal.normalized_score,
            "strategy_version": signal.strategy_version,
            "frequency": signal.frequency.value,
            "model_version": signal.model_version,
            "feature_version": signal.feature_version,
            "data_mode": signal.data_mode,
            "source": signal.source,
            "data_cutoff": signal.data_cutoff.isoformat(),
            "generated_at": signal.generated_at.isoformat(),
            "rank": signal.rank,
            "reasons": list(signal.reasons),
            "reference_price": signal.reference_price,
            "average_amount": signal.average_amount,
            "invalidation_price": signal.invalidation_price,
        }


__all__ = ["OfficialSignalStore"]
