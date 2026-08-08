from datetime import date

import pandas as pd
import pytest

from a_share_quant.contracts.data import DataValidationError
from a_share_quant.data.normalization import (
    normalize_daily_bars,
    normalize_instruments,
    normalize_symbol,
)


def test_normalize_symbol_accepts_common_a_share_forms() -> None:
    assert normalize_symbol("000001") == "000001"
    assert normalize_symbol("000001.SZ") == "000001"
    assert normalize_symbol("SH.600000") == "600000"
    assert normalize_symbol("sz000001") == "000001"


def test_normalize_symbol_rejects_path_like_or_non_a_share_values() -> None:
    with pytest.raises(DataValidationError):
        normalize_symbol("../../secret")
    with pytest.raises(DataValidationError):
        normalize_symbol("ABC")


def test_normalize_daily_bars_maps_source_columns_and_deduplicates() -> None:
    raw = pd.DataFrame(
        [
            {
                "日期": "2026-08-07",
                "开盘": "10",
                "收盘": "10.5",
                "最高": "11",
                "最低": "9.8",
                "成交量": "1000",
                "成交额": "10500",
                "振幅": "12",
                "涨跌幅": "5",
                "涨跌额": "0.5",
                "换手率": "1.2",
            },
            {
                "日期": "2026-08-07",
                "开盘": "10",
                "收盘": "10.6",
                "最高": "11",
                "最低": "9.8",
                "成交量": "1000",
                "成交额": "10600",
                "振幅": "12",
                "涨跌幅": "6",
                "涨跌额": "0.6",
                "换手率": "1.2",
            },
            {
                "日期": "2026-08-08",
                "开盘": "10.6",
                "收盘": "10.7",
                "最高": "10.9",
                "最低": "10.5",
                "成交量": "1200",
                "成交额": "12800",
                "振幅": "4",
                "涨跌幅": "0.9",
                "涨跌额": "0.1",
                "换手率": "1.4",
            },
        ]
    )

    result = normalize_daily_bars(raw, symbol="000001.SZ", source="akshare")

    assert result["symbol"].tolist() == ["000001", "000001"]
    assert result["date"].tolist() == [date(2026, 8, 7), date(2026, 8, 8)]
    assert result.loc[result["date"] == date(2026, 8, 7), "close"].item() == pytest.approx(10.6)
    assert result["volume"].dtype.kind in "iu"
    assert result.loc[0, "source"] == "akshare"
    assert result.loc[0, "data_version"] == "canonical-v1"


def test_normalize_daily_bars_rejects_missing_columns_and_invalid_ohlc() -> None:
    with pytest.raises(DataValidationError, match="missing required columns"):
        normalize_daily_bars(pd.DataFrame({"日期": ["2026-08-08"]}), symbol="000001", source="test")

    invalid = pd.DataFrame(
        [
            {
                "日期": "2026-08-08",
                "开盘": 10,
                "收盘": 10,
                "最高": 9,
                "最低": 8,
                "成交量": 1,
                "成交额": 10,
            }
        ]
    )
    with pytest.raises(DataValidationError, match="OHLC"):
        normalize_daily_bars(invalid, symbol="000001", source="test")


def test_normalize_instruments_adds_exchange_and_status_flags() -> None:
    raw = pd.DataFrame(
        {
            "代码": ["000001", "600000", "300001"],
            "名称": ["平安银行", "*ST示例", "创业板样例"],
            "上市日期": ["1991-04-03", "2000-01-01", "2026-07-01"],
        }
    )

    result = normalize_instruments(raw, source="akshare", as_of=date(2026, 8, 8))

    assert result["symbol"].tolist() == ["000001", "600000", "300001"]
    assert result["exchange"].tolist() == ["SZ", "SH", "SZ"]
    assert result["is_st"].tolist() == [False, True, False]
    assert result.loc[2, "listed_date"] == date(2026, 7, 1)
    assert result["as_of"].tolist() == [date(2026, 8, 8)] * 3


def test_normalize_instruments_allows_missing_optional_listed_date() -> None:
    raw = pd.DataFrame({"代码": ["000001"], "名称": ["平安银行"], "上市日期": [None]})

    result = normalize_instruments(raw, source="akshare", as_of=date(2026, 8, 8))

    assert pd.isna(result.loc[0, "listed_date"])


def test_point_in_time_filter_rejects_unpublished_fundamental_rows() -> None:
    from a_share_quant.data.normalization import filter_point_in_time

    rows = pd.DataFrame(
        {
            "symbol": ["000001", "000002"],
            "announced_at": ["2026-08-07 18:00:00", "2026-08-09 18:00:00"],
            "roe": [0.1, 0.2],
        }
    )

    result = filter_point_in_time(rows, as_of=date(2026, 8, 8))

    assert result["symbol"].tolist() == ["000001"]
