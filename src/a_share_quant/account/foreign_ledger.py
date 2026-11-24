"""Local accounting for manually recorded US and Hong Kong fills."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import ROUND_CEILING, Decimal
from zoneinfo import ZoneInfo

import pandas as pd

from a_share_quant.account.contracts import money, price
from a_share_quant.market.foreign_sessions import TIMEZONES, schedule

_ZERO = Decimal("0.00")
_CENT = Decimal("0.01")
_EVENT_KEYS = {"id", "action", "date", "symbol", "quantity", "price", "fee", "amount"}


def _amount(value: str, *, max_places: int) -> Decimal:
    if not re.fullmatch(rf"[0-9]{{1,12}}(?:\.[0-9]{{1,{max_places}}})?", value):
        raise ValueError("金额格式无效，请填写非负数字")
    return Decimal(value)


def prepare_event(market: str, data: dict, pool: dict, now: datetime) -> dict:
    if not isinstance(data, dict) or set(data) != _EVENT_KEYS or not all(
        isinstance(value, str) for value in data.values()
    ):
        raise ValueError("账本记录格式无效")
    if not re.fullmatch(r"[a-f0-9-]{36}", data["id"]):
        raise ValueError("账本记录编号无效")
    day = date.fromisoformat(data["date"])
    today = now.astimezone(ZoneInfo(TIMEZONES[market])).date()
    if day > today or day.year < today.year - 10:
        raise ValueError("账本日期须在最近十年内，且不能晚于市场当地今天")
    action = data["action"]
    event = {"id": data["id"], "action": action, "date": day.isoformat()}
    if action == "deposit":
        amount = money(_amount(data["amount"], max_places=2))
        if amount <= 0:
            raise ValueError("入金金额必须大于零")
        return {**event, "amount": str(amount)}
    if action not in {"buy", "sell"} or data["symbol"] not in pool:
        raise ValueError("请选择当前市场名录中与账本币种一致的股票")
    if not re.fullmatch(r"[1-9][0-9]{0,8}", data["quantity"]):
        raise ValueError("股数须为正整数")
    quantity = int(data["quantity"])
    execution = price(_amount(data["price"], max_places=4))
    fee = money(_amount(data["fee"], max_places=2))
    notional = money(quantity * execution)
    if notional <= 0 or (action == "sell" and fee > notional):
        raise ValueError("成交金额或费用无效")
    sessions = schedule(market, day.year)
    label = pd.Timestamp(day)
    if label not in sessions.index:
        raise ValueError("成交日并非该市场交易日")
    next_sessions = sessions.index[sessions.index > label]
    offset = 0 if market == "US" and day >= date(2024, 5, 28) else 1
    if market == "US" and day < date(2017, 9, 5):
        offset = 2
    settle = next_sessions[offset].date().isoformat()
    return {
        **event, "symbol": data["symbol"], "quantity": quantity, "price": str(execution),
        "fee": str(fee), "notional": str(notional), "settles_on": settle,
    }


def account_snapshot(events: list[dict], today: str) -> dict:
    cash, realized = _ZERO, _ZERO
    unsettled: list[tuple[str, Decimal]] = []
    positions: dict[str, tuple[int, Decimal]] = {}
    symbols: dict[str, str] = {}
    previous = ""
    for event in events:
        day = event["date"]
        if day < previous:
            raise ValueError("账本交易日须按先后顺序登记")
        previous = day
        cash += sum(amount for settled, amount in unsettled if settled <= day)
        unsettled = [(settled, amount) for settled, amount in unsettled if settled > day]
        if event["action"] == "deposit":
            cash += Decimal(event["amount"])
            continue
        symbol, quantity = event["symbol"], event["quantity"]
        listing_id = event.get("listing_id", symbol)
        symbols[listing_id] = symbol
        shares, cost = positions.get(listing_id, (0, _ZERO))
        notional, fee = Decimal(event["notional"]), Decimal(event["fee"])
        if event["action"] == "buy":
            total = notional + fee
            if total > cash:
                raise ValueError("买入金额和费用超过当日已结算可用资金")
            cash -= total
            positions[listing_id] = (shares + quantity, cost + total)
        else:
            if quantity > shares:
                raise ValueError("卖出股数超过账本可用持仓")
            released = cost if quantity == shares else money(cost * quantity / shares)
            proceeds = notional - fee
            realized += proceeds - released
            positions[listing_id] = (shares - quantity, cost - released)
            unsettled.append((event["settles_on"], proceeds))
    cash += sum(amount for settled, amount in unsettled if settled <= today)
    pending = sum((amount for settled, amount in unsettled if settled > today), _ZERO)
    return {
        "cash": str(cash), "pending": str(pending), "realized": str(realized),
        "positions": [
            {"symbol": symbols[listing_id], "listing_id": listing_id,
             "quantity": shares, "available": shares,
             "cost": str(cost), "average": str(price(cost / shares))}
            for listing_id, (shares, cost) in sorted(positions.items()) if shares
        ],
    }


def estimated_hk_levies(event: dict, security: dict) -> dict:
    if "stamp_duty" not in security or event["action"] == "deposit":
        return {}
    notional = Decimal(event["notional"])
    stamp = (
        (notional * Decimal("0.001")).to_integral_value(rounding=ROUND_CEILING)
        if security["stamp_duty"] else _ZERO
    )
    levies = {
        "印花税": stamp.quantize(_CENT),
        "证监会交易征费": money(notional * Decimal("0.000027")),
        "会财局交易征费": money(notional * Decimal("0.0000015")),
        "交易费": money(notional * Decimal("0.0000565")),
    }
    return {label: str(value) for label, value in levies.items()}
