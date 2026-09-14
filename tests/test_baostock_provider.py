import sys
import types

import pytest

from a_share_quant.contracts.data import (
    CANONICAL_DAILY_COLUMNS,
    ProviderConfigurationError,
    ProviderRequestError,
)
from a_share_quant.data.providers.baostock import BaoStockDataProvider


class _FakeResult:
    def __init__(self, fields: list[str], rows: list[list[str]], *, error_code: str = "0") -> None:
        self.fields = fields
        self._rows = iter(rows)
        self._current: list[str] | None = None
        self.error_code = error_code
        self.error_msg = "permission denied" if error_code != "0" else ""

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


def _fake_baostock(*, daily_error: bool = False) -> types.SimpleNamespace:
    module = types.SimpleNamespace()
    module.login_calls = 0
    module.logout_calls = 0
    module.daily_calls: list[dict[str, str]] = []

    def login() -> types.SimpleNamespace:
        module.login_calls += 1
        return types.SimpleNamespace(error_code="0", error_msg="success")

    def logout() -> None:
        module.logout_calls += 1

    def query_stock_basic() -> _FakeResult:
        return _FakeResult(
            ["code", "code_name", "ipoDate", "outDate", "type", "status"],
            [
                ["sh.000001", "上证指数", "1991-07-15", "", "2", "1"],
                ["sh.600000", "浦发银行", "1999-11-10", "", "1", "1"],
            ],
        )

    def query_history_k_data_plus(**kwargs: str) -> _FakeResult:
        module.daily_calls.append(kwargs)
        if daily_error:
            return _FakeResult([], [], error_code="10001001")
        return _FakeResult(
            [
                "date",
                "code",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "pctChg",
                "tradestatus",
            ],
            [
                [
                    "2026-08-08",
                    "sh.600000",
                    "10",
                    "10.8",
                    "9.9",
                    "10.5",
                    "100",
                    "1000",
                    "1.2",
                    "1",
                ]
            ],
        )

    module.login = login
    module.logout = logout
    module.query_stock_basic = query_stock_basic
    module.query_history_k_data_plus = query_history_k_data_plus
    return module


def test_baostock_provider_logs_in_once_and_normalizes_universe_and_daily(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _fake_baostock()
    monkeypatch.setitem(sys.modules, "baostock", fake)

    provider = BaoStockDataProvider()
    instruments = provider.list_instruments(as_of="2026-08-08")
    daily = provider.get_daily_bars("600000.SH", "2026-08-08", "2026-08-08")
    provider.close()

    assert fake.login_calls == 1
    assert fake.logout_calls == 1
    assert instruments.loc[0, "symbol"] == "600000"
    assert instruments.loc[0, "name"] == "浦发银行"
    assert daily.loc[0, "symbol"] == "600000"
    assert daily.loc[0, "close"] == pytest.approx(10.5)
    assert daily.loc[0, "change_pct"] == pytest.approx(1.2)
    assert fake.daily_calls[0]["code"] == "sh.600000"
    assert fake.daily_calls[0]["frequency"] == "d"
    assert fake.daily_calls[0]["adjustflag"] == "3"
    assert daily.loc[0, "data_version"] == "baostock-unadjusted-v1"
    assert tuple(daily.columns) == CANONICAL_DAILY_COLUMNS


def test_baostock_provider_excludes_indexes_and_inactive_securities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _fake_baostock()
    monkeypatch.setitem(sys.modules, "baostock", fake)

    instruments = BaoStockDataProvider().list_instruments(as_of="2026-08-08")

    assert instruments["symbol"].tolist() == ["600000"]


def test_baostock_provider_uses_daily_trade_status_for_suspensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _fake_baostock()

    def query_all_stock(day: str) -> _FakeResult:
        assert day == "2026-08-08"
        return _FakeResult(
            ["code", "tradeStatus", "code_name"],
            [["sh.600000", "0", "浦发银行"]],
        )

    fake.query_all_stock = query_all_stock
    monkeypatch.setitem(sys.modules, "baostock", fake)

    instruments = BaoStockDataProvider().list_instruments(as_of="2026-08-08")

    assert instruments.loc[0, "is_suspended"]


def test_baostock_provider_sanitizes_result_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _fake_baostock(daily_error=True)
    monkeypatch.setitem(sys.modules, "baostock", fake)

    provider = BaoStockDataProvider()
    with pytest.raises(ProviderRequestError, match="BaoStock request failed") as error:
        provider.get_daily_bars("600000", "2026-08-08", "2026-08-08")

    assert "permission denied" not in str(error.value)


def test_baostock_provider_maps_csi300_to_sh_index_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _fake_baostock()
    monkeypatch.setitem(sys.modules, "baostock", fake)

    daily = BaoStockDataProvider().get_index_daily_bars(
        "000300", "2026-08-08", "2026-08-08"
    )

    assert daily.loc[0, "symbol"] == "000300"
    assert fake.daily_calls[0]["code"] == "sh.000300"


def test_baostock_provider_excludes_suspended_daily_rows_before_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _fake_baostock()

    def query_history_k_data_plus(**kwargs: str) -> _FakeResult:
        fake.daily_calls.append(kwargs)
        return _FakeResult(
            [
                "date",
                "code",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "pctChg",
                "tradestatus",
            ],
            [
                [
                    "2026-08-07",
                    "sh.600000",
                    "10",
                    "10",
                    "10",
                    "10",
                    "",
                    "",
                    "",
                    "0",
                ],
                [
                    "2026-08-08",
                    "sh.600000",
                    "10",
                    "10.8",
                    "9.9",
                    "10.5",
                    "100",
                    "1000",
                    "1.2",
                    "1",
                ],
            ],
        )

    fake.query_history_k_data_plus = query_history_k_data_plus
    monkeypatch.setitem(sys.modules, "baostock", fake)

    daily = BaoStockDataProvider().get_daily_bars(
        "600000", "2026-08-07", "2026-08-08"
    )

    assert daily["date"].astype(str).tolist() == ["2026-08-08"]
    assert "tradestatus" in fake.daily_calls[0]["fields"]


def test_baostock_provider_requires_optional_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "baostock", None)

    with pytest.raises(ProviderConfigurationError, match="BaoStock is not installed"):
        BaoStockDataProvider().list_instruments(as_of="2026-08-08")
