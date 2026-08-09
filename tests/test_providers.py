import sys
import types

import pandas as pd
import pytest

from a_share_quant.contracts.data import ProviderConfigurationError
from a_share_quant.data.providers.akshare import AKShareDataProvider
from a_share_quant.data.providers.tushare import TushareDataProvider


def _daily_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": "2026-08-08",
                "open": 10,
                "close": 10.5,
                "high": 10.8,
                "low": 9.9,
                "volume": 100,
                "amount": 1000,
            }
        ]
    )


def test_akshare_provider_is_lazy_and_normalizes_source_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict]] = []

    def stock_info_a_code_name() -> pd.DataFrame:
        calls.append(("universe", {}))
        return pd.DataFrame({"code": ["000001"], "name": ["平安银行"]})

    def stock_zh_a_hist(**kwargs: str) -> pd.DataFrame:
        calls.append(("daily", kwargs))
        return _daily_frame()

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
        sys.modules,
        "akshare",
        types.SimpleNamespace(stock_info_a_code_name=stock_info_a_code_name),
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
        return _daily_frame()

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        types.SimpleNamespace(
            stock_zh_a_hist=stock_zh_a_hist,
            stock_zh_a_hist_tx=stock_zh_a_hist_tx,
        ),
    )

    result = AKShareDataProvider(adjust="qfq", retry_count=0, delay_seconds=0).get_daily_bars(
        "000001", start_date="2026-08-08", end_date="2026-08-08"
    )

    assert result.loc[0, "close"] == pytest.approx(10.5)
    assert calls[0][0] == "eastmoney"
    assert calls[1][0] == "tencent"
    assert calls[1][1]["symbol"] == "sz000001"


def test_akshare_provider_normalizes_index_daily_bars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, str]] = []

    def index_zh_a_hist(**kwargs: str) -> pd.DataFrame:
        calls.append(kwargs)
        return pd.DataFrame(
            [
                {
                    "date": "2026-08-08",
                    "open": 4000,
                    "close": 4010,
                    "high": 4020,
                    "low": 3990,
                    "volume": 100,
                    "amount": 400000,
                }
            ]
        )

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        types.SimpleNamespace(index_zh_a_hist=index_zh_a_hist),
    )

    result = AKShareDataProvider(retry_count=0, delay_seconds=0).get_index_daily_bars(
        "000300",
        start_date="2026-08-08",
        end_date="2026-08-08",
    )

    assert result.loc[0, "symbol"] == "000300"
    assert result.loc[0, "close"] == pytest.approx(4010)
    assert calls[0]["symbol"] == "000300"


def test_akshare_provider_uses_csi_index_mapping_when_primary_index_endpoint_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def index_zh_a_hist(**kwargs: str) -> pd.DataFrame:
        calls.append(("legacy", kwargs))
        raise ConnectionError("index endpoint unavailable")

    def stock_zh_index_daily_em(**kwargs: str) -> pd.DataFrame:
        calls.append(("eastmoney", kwargs))
        return pd.DataFrame(
            [
                {
                    "date": "2026-08-08",
                    "open": 4000,
                    "close": 4010,
                    "high": 4020,
                    "low": 3990,
                    "volume": 100,
                    "amount": 400000,
                }
            ]
        )

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        types.SimpleNamespace(
            index_zh_a_hist=index_zh_a_hist,
            stock_zh_index_daily_em=stock_zh_index_daily_em,
        ),
    )

    result = AKShareDataProvider(retry_count=0, delay_seconds=0).get_index_daily_bars(
        "000300",
        start_date="2026-08-08",
        end_date="2026-08-08",
    )

    assert result.loc[0, "symbol"] == "000300"
    assert calls[1] == (
        "eastmoney",
        {"symbol": "csi000300", "start_date": "20260808", "end_date": "20260808"},
    )


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
        sys.modules,
        "akshare",
        types.SimpleNamespace(stock_info_a_code_name=stock_info_a_code_name),
    )

    result = AKShareDataProvider(retry_count=2, delay_seconds=0).list_instruments(
        as_of="2026-08-08"
    )

    assert attempts == 3
    assert result.loc[0, "symbol"] == "000001"


def test_tushare_provider_requires_token_before_importing_client() -> None:
    with pytest.raises(ProviderConfigurationError, match="TUSHARE_TOKEN"):
        TushareDataProvider(token="")
