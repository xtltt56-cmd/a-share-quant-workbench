from __future__ import annotations

import sys
import types
from datetime import date

import pandas as pd
import pytest

from a_share_quant.contracts.data import ProviderRequestError
from a_share_quant.data.providers.baostock import BaoStockDataProvider


class _FakeResult:
    def __init__(
        self,
        fields: list[str],
        rows: list[list[str]],
        *,
        error_code: str = "0",
    ) -> None:
        self.fields = fields
        self._rows = iter(rows)
        self._current: list[str] | None = None
        self.error_code = error_code
        self.error_msg = "sensitive sdk details" if error_code != "0" else ""

    def next(self) -> bool:
        try:
            self._current = next(self._rows)
        except StopIteration:
            self._current = None
            return False
        return True

    def get_row_data(self) -> list[str]:
        assert self._current is not None
        return self._current


def _history_rows(
    *,
    adjusted: bool = False,
    duplicate_research_date: bool = False,
    missing_research_date: bool = False,
    non_numeric_research_close: bool = False,
    omit_status: bool = False,
) -> tuple[list[str], list[list[str]]]:
    fields = [
        "date",
        "code",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
    ]
    if not omit_status:
        fields.extend(["tradestatus", "isST"])
    rows = [
        ["2020-01-02", "sh.600001", "10", "10.5", "9.5", "10", "100", "1000"],
        ["2020-01-03", "sh.600001", "10", "10.5", "9.5", "10", "100", "1000"],
        ["2020-01-06", "sh.600001", "12", "12.5", "11.5", "12", "100", "1200"],
    ]
    if adjusted:
        rows[0][5] = "100"
        rows[1][5] = "80"
        rows[2][5] = "120"
        if non_numeric_research_close:
            rows[1][5] = "not-a-number"
        if missing_research_date:
            rows.pop(1)
        if duplicate_research_date:
            rows.insert(2, rows[1].copy())
    if not omit_status:
        for row, trade_status, is_st in zip(
            rows,
            ["1", "0", "1"] if not adjusted else ["1"] * len(rows),
            ["0", "1", "0"] if not adjusted else ["0"] * len(rows),
            strict=False,
        ):
            row.extend([trade_status, is_st])
    return fields, rows


def _fake_baostock(
    *,
    duplicate_research_date: bool = False,
    missing_research_date: bool = False,
    non_numeric_research_close: bool = False,
    omit_status: bool = False,
    state_conflict: bool = False,
    duplicate_execution_date: bool = False,
    history_error: bool = False,
) -> types.SimpleNamespace:
    module = types.SimpleNamespace()
    module.login_calls = 0
    module.logout_calls = 0
    module.history_calls: list[dict[str, str]] = []

    def login() -> types.SimpleNamespace:
        module.login_calls += 1
        return types.SimpleNamespace(error_code="0", error_msg="success")

    def logout() -> None:
        module.logout_calls += 1

    def query_stock_basic() -> _FakeResult:
        return _FakeResult(
            ["code", "code_name", "ipoDate", "outDate", "type", "status"],
            [
                ["sh.600001", "普通退市股", "2010-01-01", "2020-06-01", "1", "0"],
                ["sh.600002", "ST示例", "2011-01-01", "", "1", "1"],
                ["sh.000001", "上证指数", "1991-07-15", "", "2", "1"],
            ],
        )

    def query_all_stock(day: str) -> _FakeResult:
        assert day == "2020-06-01"
        return _FakeResult(
            ["code", "tradeStatus", "code_name"],
            [["sh.600001", "0", "普通退市股"], ["sh.600002", "1", "ST示例"]],
        )

    def query_history_k_data_plus(**kwargs: str) -> _FakeResult:
        module.history_calls.append(kwargs)
        if history_error:
            return _FakeResult([], [], error_code="10001001")
        fields = kwargs["fields"]
        if "pctChg" in fields:
            row_fields, rows = _history_rows(omit_status=True)
            row_fields.append("pctChg")
            for row in rows:
                row.append("1.2")
            return _FakeResult(row_fields, rows)
        if kwargs["adjustflag"] == "2":
            row_fields, rows = _history_rows(
                adjusted=True,
                duplicate_research_date=duplicate_research_date,
                missing_research_date=missing_research_date,
                non_numeric_research_close=non_numeric_research_close,
                omit_status=omit_status,
            )
            if state_conflict and not omit_status:
                rows[1][-2:] = ["1", "0"]
        else:
            row_fields, rows = _history_rows(omit_status=omit_status)
            if duplicate_execution_date:
                rows.insert(1, rows[1].copy())
        if not omit_status:
            assert fields.split(",") == row_fields
        return _FakeResult(row_fields, rows)

    module.login = login
    module.logout = logout
    module.query_stock_basic = query_stock_basic
    module.query_all_stock = query_all_stock
    module.query_history_k_data_plus = query_history_k_data_plus
    return module


def test_list_research_instruments_keeps_delisted_ordinary_shares(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _fake_baostock()
    monkeypatch.setitem(sys.modules, "baostock", fake)

    instruments = BaoStockDataProvider().list_research_instruments(date(2020, 6, 1))

    assert instruments["symbol"].tolist() == ["600001", "600002"]
    assert instruments.loc[0, "status"] == "0"
    assert instruments.loc[0, "delisted_date"] == date(2020, 6, 1)
    assert bool(instruments.loc[0, "is_suspended"])
    assert bool(instruments.loc[1, "is_st"])
    assert instruments.loc[0, "as_of"] == date(2020, 6, 1)
    assert {"exchange", "listed_date", "source", "data_version"}.issubset(
        instruments.columns
    )


def test_research_history_separates_adjusted_return_from_execution_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _fake_baostock()
    monkeypatch.setitem(sys.modules, "baostock", fake)

    provider = BaoStockDataProvider()
    history = provider.get_research_history("600001", "2020-01-02", "2020-01-06")

    assert history["close"].tolist() == [10.0, 10.0, 12.0]
    assert pd.isna(history.loc[0, "research_return"])
    assert history.loc[1, "research_return"] == pytest.approx(-0.2)
    assert history.loc[2, "research_return"] == pytest.approx(0.5)
    assert not bool(history.loc[1, "tradable"])  # trade_status=0
    assert bool(history.loc[2, "tradable"])  # trade_status=1 and isST=0
    assert bool(history.loc[1, "is_st"])
    assert "adjusted_close" not in history.columns
    assert not bool(history.loc[0, "research_usable"])
    assert history.loc[0, "unusable_reason"]
    assert [call["adjustflag"] for call in fake.history_calls] == ["3", "2"]
    for call in fake.history_calls:
        assert "tradestatus" in call["fields"]
        assert "isST" in call["fields"]


def test_research_history_marks_missing_adjusted_date_unusable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _fake_baostock(missing_research_date=True)
    monkeypatch.setitem(sys.modules, "baostock", fake)

    history = BaoStockDataProvider().get_research_history(
        "600001", "2020-01-02", "2020-01-06"
    )

    assert len(history) == 3
    assert not bool(history.loc[1, "research_usable"])
    assert "missing" in history.loc[1, "unusable_reason"]


def test_research_history_marks_duplicate_or_non_numeric_adjusted_rows_unusable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    duplicate_fake = _fake_baostock(duplicate_research_date=True)
    monkeypatch.setitem(sys.modules, "baostock", duplicate_fake)
    duplicate = BaoStockDataProvider().get_research_history(
        "600001", "2020-01-02", "2020-01-06"
    )
    assert duplicate["research_usable"].eq(False).all()
    assert duplicate["unusable_reason"].str.contains("duplicate").all()

    non_numeric_fake = _fake_baostock(non_numeric_research_close=True)
    monkeypatch.setitem(sys.modules, "baostock", non_numeric_fake)
    non_numeric = BaoStockDataProvider().get_research_history(
        "600001", "2020-01-02", "2020-01-06"
    )
    assert not bool(non_numeric.loc[1, "research_usable"])
    assert "numeric" in non_numeric.loc[1, "unusable_reason"]


def test_research_history_requires_status_columns_and_sanitizes_sdk_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing_status = _fake_baostock(omit_status=True)
    monkeypatch.setitem(sys.modules, "baostock", missing_status)
    with pytest.raises(ProviderRequestError, match="status"):
        BaoStockDataProvider().get_research_history("600001", "2020-01-02", "2020-01-06")

    error_fake = _fake_baostock(history_error=True)
    monkeypatch.setitem(sys.modules, "baostock", error_fake)
    with pytest.raises(ProviderRequestError, match="BaoStock request failed") as error:
        BaoStockDataProvider().get_research_history("600001", "2020-01-02", "2020-01-06")
    assert "sensitive sdk details" not in str(error.value)


def test_research_history_marks_status_conflicts_and_duplicate_execution_dates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conflict_fake = _fake_baostock(state_conflict=True)
    monkeypatch.setitem(sys.modules, "baostock", conflict_fake)
    conflict = BaoStockDataProvider().get_research_history(
        "600001", "2020-01-02", "2020-01-06"
    )
    assert not bool(conflict.loc[1, "research_usable"])
    assert "status conflict" in conflict.loc[1, "unusable_reason"]
    assert not bool(conflict.loc[1, "tradable"])

    duplicate_fake = _fake_baostock(duplicate_execution_date=True)
    monkeypatch.setitem(sys.modules, "baostock", duplicate_fake)
    duplicate = BaoStockDataProvider().get_research_history(
        "600001", "2020-01-02", "2020-01-06"
    )
    assert duplicate["research_usable"].eq(False).all()
    assert duplicate["unusable_reason"].str.contains("duplicate execution").all()


def test_research_history_logs_in_once_for_multiple_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _fake_baostock()
    monkeypatch.setitem(sys.modules, "baostock", fake)
    provider = BaoStockDataProvider()

    provider.list_research_instruments("2020-06-01")
    provider.get_research_history("600001", "2020-01-02", "2020-01-06")
    provider.get_daily_bars("600001", "2020-01-02", "2020-01-06")

    assert fake.login_calls == 1
