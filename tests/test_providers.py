import sys
import types

import pandas as pd
import pytest

from a_share_quant.contracts.data import ProviderConfigurationError
from a_share_quant.data.providers.akshare import AKShareDataProvider
from a_share_quant.data.providers.tushare import TushareDataProvider


def test_akshare_provider_is_lazy_and_normalizes_source_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict]] = []

    def stock_info_a_code_name() -> pd.DataFrame:
        calls.append(("universe", {}))
        return pd.DataFrame({"code": ["000001"], "name": ["平安银行"]})

    def stock_zh_a_hist(**kwargs: str) -> pd.DataFrame:
        calls.append(("daily", kwargs))
        return pd.DataFrame(
            [
                {
                    "日期": "2026-08-08",
                    "开盘": 10,
                    "收盘": 10.5,
                    "最高": 10.8,
                    "最低": 9.9,
                    "成交量": 100,
                    "成交额": 1000,
                }
            ]
        )

    fake_akshare = types.SimpleNamespace(
        stock_info_a_code_name=stock_info_a_code_name,
        stock_zh_a_hist=stock_zh_a_hist,
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)

    provider = AKShareDataProvider(adjust="qfq")
    universe = provider.list_instruments(as_of="2026-08-08")
    daily = provider.get_daily_bars("000001.SZ", start_date="2026-08-08", end_date="2026-08-08")

    assert universe.loc[0, "symbol"] == "000001"
    assert daily.loc[0, "close"] == pytest.approx(10.5)
    assert calls[0][0] == "universe"
    assert calls[1][0] == "daily"
    assert calls[1][1]["symbol"] == "000001"
    assert calls[1][1]["adjust"] == "qfq"


def test_akshare_provider_wraps_external_errors_without_logging_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def stock_info_a_code_name() -> pd.DataFrame:
        raise RuntimeError("provider failed with token=do-not-expose")

    monkeypatch.setitem(
        sys.modules, "akshare", types.SimpleNamespace(stock_info_a_code_name=stock_info_a_code_name)
    )

    with pytest.raises(RuntimeError, match="AKShare request failed") as error:
        AKShareDataProvider().list_instruments()

    assert "do-not-expose" not in str(error.value)


def test_akshare_provider_falls_back_to_tencent_daily_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict]] = []

    def stock_zh_a_hist(**kwargs: str) -> pd.DataFrame:
        calls.append(("eastmoney", kwargs))
        raise ConnectionError("eastmoney endpoint unavailable")

    def stock_zh_a_hist_tx(**kwargs: str) -> pd.DataFrame:
        calls.append(("tencent", kwargs))
        return pd.DataFrame(
            [
                {
                    "日期": "2026-08-08",
                    "开盘": 10,
                    "收盘": 10.5,
                    "最高": 10.8,
                    "最低": 9.9,
                    "成交量": 100,
                    "成交额": 1000,
                }
            ]
        )

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        types.SimpleNamespace(
            stock_zh_a_hist=stock_zh_a_hist, stock_zh_a_hist_tx=stock_zh_a_hist_tx
        ),
    )

    result = AKShareDataProvider(adjust="qfq", retry_count=0, delay_seconds=0).get_daily_bars(
        "000001", start_date="2026-08-08", end_date="2026-08-08"
    )

    assert result.loc[0, "close"] == pytest.approx(10.5)
    assert calls[0][0] == "eastmoney"
    assert calls[1][0] == "tencent"
    assert calls[1][1]["symbol"] == "sz000001"


def test_akshare_provider_uses_bounded_retry_without_leaking_exception_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def stock_info_a_code_name() -> pd.DataFrame:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionError("temporary endpoint failure")
        return pd.DataFrame({"code": ["000001"], "name": ["平安银行"]})

    monkeypatch.setitem(
        sys.modules, "akshare", types.SimpleNamespace(stock_info_a_code_name=stock_info_a_code_name)
    )

    result = AKShareDataProvider(retry_count=2, delay_seconds=0).list_instruments(
        as_of="2026-08-08"
    )

    assert attempts == 3
    assert result.loc[0, "symbol"] == "000001"


def test_tushare_provider_requires_token_before_importing_client() -> None:
    with pytest.raises(ProviderConfigurationError, match="TUSHARE_TOKEN"):
        TushareDataProvider(token="")
