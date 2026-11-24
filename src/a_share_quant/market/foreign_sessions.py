"""Exchange-local session dates for the US and Hong Kong workspaces."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars
import pandas as pd

CALENDARS = {"US": "XNYS", "HK": "XHKG"}
TIMEZONES = {"US": "America/New_York", "HK": "Asia/Hong_Kong"}


@lru_cache(maxsize=16)
def schedule(market: str, year: int) -> pd.DataFrame:
    return exchange_calendars.get_calendar(
        CALENDARS[market], start=f"{year - 1}-01-01", end=f"{year + 1}-12-31"
    ).schedule


def market_session(
    market: str, now: datetime, *, vendor_day_bar: bool = False
) -> tuple[str, date, date, pd.DataFrame]:
    local = now.astimezone(ZoneInfo(TIMEZONES[market]))
    today = pd.Timestamp(local.date())
    sessions = schedule(market, today.year)
    if today not in sessions.index:
        status, available = "非交易日", False
    else:
        row = sessions.loc[today]
        if now < row["open"]:
            status = "盘前"
        elif now >= row["close"]:
            status = "已收盘"
        elif market == "HK" and row["break_start"] <= now < row["break_end"]:
            status = "午间休市"
        else:
            status = "常规交易时段"
        # Alpaca day bars can include volume outside the core session.
        grace = timedelta(minutes=30 if market == "US" else 20)
        available = not vendor_day_bar and now >= row["close"] + grace
    previous = sessions.index[sessions.index < today]
    if vendor_day_bar and local.time() < time(0, 15):
        previous = previous[previous < today - pd.Timedelta(days=1)]
    cutoff = today if available else previous[-1]
    next_session = sessions.index[sessions.index > cutoff][0]
    return status, cutoff.date(), next_session.date(), sessions
