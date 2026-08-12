from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd

from a_share_quant.advisory.price_contracts import PricePlanType
from a_share_quant.runtime.price_guidance import (
    PriceGuidanceRuntime,
    load_bars,
    load_or_generate_price_guidance_store,
)
from a_share_quant.signals.realtime import OfficialModelSignal
from a_share_quant.storage.official_signal_store import OfficialSignalStore
from a_share_quant.storage.price_guidance_store import PriceGuidanceStore


def _bars(symbol: str, *, factor: float = 1.0) -> pd.DataFrame:
    start = date(2025, 8, 1)
    rows = []
    for index in range(260):
        day = start + timedelta(days=index)
        value = (10 + index * 0.01) * factor
        rows.append(
            {
                "symbol": symbol,
                "date": day,
                "open": value,
                "high": value + 0.2,
                "low": value - 0.2,
                "close": value,
                "raw_close": value,
                "volume": 100000,
                "amount": 20000000,
                "source": "baostock",
            }
        )
    return pd.DataFrame(rows)


def test_candidate_and_holding_plans_share_cutoff(tmp_path) -> None:
    store = PriceGuidanceStore(tmp_path / "guidance.json")
    runtime = PriceGuidanceRuntime(
        bars_by_symbol={"000001": _bars("000001")},
        store=store,
        candidate_symbols=("000001",),
        holding_positions=(),
    )
    result = runtime.generate(
        calculation_date=date(2026, 4, 15), valid_for=date(2026, 4, 16)
    )
    assert {item.plan_type.value for item in result.plans} == {"DAILY_CANDIDATE"}
    assert all(item.suggested_quantity == 0 for item in result.plans)
    assert {item.calculation_date for item in result.plans} == {date(2026, 4, 15)}


def test_holdings_are_added_and_missing_data_is_fail_closed(tmp_path) -> None:
    store = PriceGuidanceStore(tmp_path / "guidance.json")
    runtime = PriceGuidanceRuntime(
        bars_by_symbol={"000001": _bars("000001")},
        store=store,
        candidate_symbols=(),
        holding_positions=(
            {"symbol": "000001", "average_cost": "10.00", "total_quantity": 100},
            {"symbol": "000002", "average_cost": "8.00", "total_quantity": 100},
        ),
    )
    result = runtime.generate(date(2026, 4, 15), date(2026, 4, 16))
    assert {item.plan_type for item in result.plans} == {
        PricePlanType.HOLDING,
    }
    missing = next(item for item in result.plans if item.symbol == "000002")
    assert missing.state.value == "NO_RELIABLE_GUIDANCE"
    assert missing.suggested_quantity == 0


def test_adjustment_factor_change_recalculates_old_protection(tmp_path) -> None:
    store = PriceGuidanceStore(tmp_path / "guidance.json")
    runtime = PriceGuidanceRuntime(
        bars_by_symbol={
            "000001": _bars("000001", factor=1.0).assign(
                raw_close=lambda frame: frame["close"] * 2
            )
        },
        store=store,
        candidate_symbols=(),
        holding_positions=(
            {
                "symbol": "000001",
                "average_cost": "10.00",
                "total_quantity": 100,
                "previous_protection": "9.70",
            },
        ),
    )
    result = runtime.generate(date(2026, 4, 15), date(2026, 4, 16))
    assert "CORPORATE_ACTION_RECALCULATED" in result.plans[0].reason_codes


def test_load_bars_marks_standard_unadjusted_close_as_raw_close(tmp_path) -> None:
    path = tmp_path / "lake" / "daily_bars"
    path.mkdir(parents=True)
    frame = _bars("000001").drop(columns=["raw_close"])
    frame.to_parquet(path / "000001.parquet", index=False)

    loaded = load_bars(tmp_path, ("000001",))

    assert "raw_close" in loaded["000001"]
    assert loaded["000001"]["raw_close"].equals(loaded["000001"]["close"])


def test_startup_loader_refreshes_daily_plans_from_official_signals(tmp_path) -> None:
    bars_dir = tmp_path / "data" / "lake" / "daily_bars"
    bars_dir.mkdir(parents=True)
    _bars("000001").to_parquet(bars_dir / "000001.parquet", index=False)
    official = OfficialSignalStore(tmp_path / "signals.json")
    official.put_signals(
        (
            OfficialModelSignal(
                signal_date=date(2026, 4, 15),
                symbol="000001",
                name="平安银行",
                normalized_score=80.0,
                strategy_version="initial-free-data-v1",
                model_version="rule-ranking-v1",
                feature_version="rule-features-v1-no-valuation",
                data_mode="historical",
                source="baostock",
                data_cutoff=date(2026, 4, 15),
                generated_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
                rank=1,
            ),
        )
    )

    store = load_or_generate_price_guidance_store(
        tmp_path / "guidance.json",
        repo_root=tmp_path,
        official_signal_store=official,
    )

    assert len(store.plans()) == 1
    assert store.plans()[0].symbol == "000001"
