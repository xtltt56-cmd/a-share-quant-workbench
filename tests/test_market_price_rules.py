from __future__ import annotations

from decimal import Decimal

import pytest

from a_share_quant.data.market_rules import UnsupportedSecurityRules, resolve_security_rule


def test_plain_shenzhen_and_shanghai_a_shares_have_one_cent_tick_and_lot() -> None:
    for symbol in ("000001", "002001", "300001", "600000", "601000", "603000"):
        rule = resolve_security_rule(symbol, {})
        assert rule.tick_size == Decimal("0.01")
        assert rule.lot_size == 100


@pytest.mark.parametrize(
    "symbol,flags",
    [
        ("688001", {"board": "STAR"}),
        ("000001", {"st": True}),
        ("000001", {"ipo_first_five_days": True}),
        ("000001", {"delisting": True}),
        ("430001", {}),
    ],
)
def test_unsupported_security_rules_fail_closed(symbol: str, flags: dict[str, object]) -> None:
    with pytest.raises(UnsupportedSecurityRules, match="UNSUPPORTED_SECURITY_RULES"):
        resolve_security_rule(symbol, flags)


def test_missing_or_invalid_flags_fail_closed() -> None:
    with pytest.raises(UnsupportedSecurityRules):
        resolve_security_rule("000001", {"suspended": True})
