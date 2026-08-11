"""Crash-safe, payload-validated persistence for stale real-time snapshots."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from a_share_quant.contracts.realtime import DataQualityStatus, RealTimeQuote

_FORMAT_VERSION = 1
_PAYLOAD_KEYS = frozenset({"format_version", "saved_at", "quotes", "sha256"})


@dataclass(frozen=True)
class CachedQuoteSnapshot:
    quotes: tuple[RealTimeQuote, ...]
    saved_at: datetime

    def age_seconds(self, *, now: datetime | None = None) -> float:
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None or reference.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        return max(0.0, (reference - self.saved_at).total_seconds())


class RealtimeQuoteCache:
    """Persist normalized quotes while making cached data permanently stale.

    The cache is an availability aid for the dashboard, never a freshness
    source.  Every load reconstructs quotes with ``STALE`` quality even when a
    provider originally reported ``GOOD``.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        max_bytes: int = 8 * 1024 * 1024,
        max_quotes: int = 10_000,
        future_tolerance_seconds: float = 2.0,
    ) -> None:
        if max_bytes <= 0 or max_quotes <= 0:
            raise ValueError("cache limits must be positive")
        if future_tolerance_seconds < 0:
            raise ValueError("future_tolerance_seconds cannot be negative")
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.max_quotes = max_quotes
        self.future_tolerance_seconds = future_tolerance_seconds

    def save(
        self,
        quotes: tuple[RealTimeQuote, ...] | list[RealTimeQuote],
        *,
        saved_at: datetime | None = None,
    ) -> None:
        values = tuple(quotes)
        if len(values) > self.max_quotes:
            raise ValueError("cache quote count exceeds configured limit")
        timestamp = saved_at or datetime.now(timezone.utc)
        _ensure_aware(timestamp, "saved_at")
        quote_payload = [quote.to_dict() for quote in values]
        body = _canonical_json(quote_payload)
        payload = {
            "format_version": _FORMAT_VERSION,
            "saved_at": timestamp.isoformat(),
            "quotes": quote_payload,
            "sha256": hashlib.sha256(body).hexdigest(),
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > self.max_bytes:
            raise ValueError("cache payload exceeds configured byte limit")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and (self.path.is_symlink() or not self.path.is_file()):
            raise ValueError("cache target is not a regular file")

        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{self.path.name}.",
                suffix=f".{uuid4().hex}.tmp",
                dir=self.path.parent,
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            raise ValueError("cache could not be written") from exc
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink(missing_ok=True)

    def load(self, *, now: datetime | None = None) -> CachedQuoteSnapshot | None:
        if not self.path.exists():
            return None
        if self.path.is_symlink() or not self.path.is_file():
            raise ValueError("cache path is not a regular file")
        try:
            raw = self.path.read_bytes()
        except OSError as exc:
            raise ValueError("cache could not be read") from exc
        if not raw or len(raw) > self.max_bytes:
            raise ValueError("cache size is invalid")

        try:
            payload: Any = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("cache payload is invalid") from exc
        if not isinstance(payload, dict) or set(payload) != _PAYLOAD_KEYS:
            raise ValueError("cache payload is invalid")
        if payload.get("format_version") != _FORMAT_VERSION:
            raise ValueError("cache format version is unsupported")
        quote_payload = payload.get("quotes")
        if not isinstance(quote_payload, list) or len(quote_payload) > self.max_quotes:
            raise ValueError("cache quote list is invalid")
        if not isinstance(payload.get("sha256"), str):
            raise ValueError("cache digest is invalid")
        expected_digest = hashlib.sha256(_canonical_json(quote_payload)).hexdigest()
        if not hmac.compare_digest(payload["sha256"], expected_digest):
            raise ValueError("cache digest mismatch")

        try:
            saved_at = datetime.fromisoformat(str(payload["saved_at"]))
        except (TypeError, ValueError) as exc:
            raise ValueError("cache timestamp is invalid") from exc
        _ensure_aware(saved_at, "cache saved_at")
        reference = now or datetime.now(timezone.utc)
        _ensure_aware(reference, "now")
        tolerance = self.future_tolerance_seconds
        if (saved_at - reference).total_seconds() > tolerance:
            raise ValueError("cache contains future timestamp")

        quotes: list[RealTimeQuote] = []
        for item in quote_payload:
            if not isinstance(item, dict):
                raise ValueError("cache quote is invalid")
            try:
                normalized_item = dict(item)
                for field in ("timestamp_exchange", "timestamp_received"):
                    normalized_item[field] = datetime.fromisoformat(
                        str(normalized_item[field])
                    )
                quote = RealTimeQuote(**normalized_item)
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("cache quote is invalid") from exc
            if (quote.timestamp_exchange - reference).total_seconds() > tolerance:
                raise ValueError("cache contains future quote timestamp")
            if (quote.timestamp_received - reference).total_seconds() > tolerance:
                raise ValueError("cache contains future received timestamp")
            quotes.append(
                replace(
                    quote,
                    quality_flag=DataQualityStatus.STALE,
                    is_stale=True,
                )
            )
        return CachedQuoteSnapshot(quotes=tuple(quotes), saved_at=saved_at)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _ensure_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
