"""Fail-closed market rules for the initially supported A-share universe."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from a_share_quant.data.normalization import normalize_symbol


class UnsupportedSecurityRules(ValueError):
    """Raised when a symbol is outside the conservative v1 rule boundary."""


@dataclass(frozen=True)
class SecurityPriceRule:
    tick_size: Decimal
    lot_size: int
    daily_limit: Decimal


def resolve_security_rule(symbol: str, flags: Mapping[str, Any] | None) -> SecurityPriceRule:
    normalized = normalize_symbol(symbol)
    values = dict(flags or {})
    supported_prefix = normalized.startswith(
        ("000", "001", "002", "003", "300", "600", "601", "603", "605")
    )
    if not supported_prefix:
        raise UnsupportedSecurityRules("UNSUPPORTED_SECURITY_RULES")
    if any(
        bool(values.get(field, False))
        for field in ("st", "ipo_first_five_days", "delisting", "suspended")
    ):
        raise UnsupportedSecurityRules("UNSUPPORTED_SECURITY_RULES")
    board = str(values.get("board", "A_SHARE")).upper()
    if board not in {"A_SHARE", "MAIN", "SZSE", "SSE"}:
        raise UnsupportedSecurityRules("UNSUPPORTED_SECURITY_RULES")
    if any(key in values for key in ("price_limit", "tick_size", "lot_size")):
        raise UnsupportedSecurityRules("UNSUPPORTED_SECURITY_RULES")
    return SecurityPriceRule(tick_size=Decimal("0.01"), lot_size=100, daily_limit=Decimal("0.10"))


__all__ = ["SecurityPriceRule", "UnsupportedSecurityRules", "resolve_security_rule"]
