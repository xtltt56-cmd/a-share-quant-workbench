from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from a_share_quant.workbench.advisory_context import load_advisory_context


def _payload() -> dict[str, object]:
    return {
        "forecast": {
            "forecast_id": "forecast-1",
            "symbol": "000001",
            "generated_at": "2026-08-10T08:00:00+00:00",
            "data_cutoff": "2026-08-10T08:00:00+00:00",
            "reference_price": "10",
            "model_version": "champion-v1",
            "data_version": "data-v1",
            "feature_version": "features-v1",
            "horizon_days": 5,
            "maturity_date": "2026-08-15",
            "predicted_return": "0.05",
            "predicted_probability": "0.65",
            "predicted_rank": 1,
            "uncertainty": "0.10",
            "proposed_state": "WATCH",
            "report_id": "report-1",
            "data_mode": "historical",
        },
        "portfolio": {
            "equity": "100000",
            "cash": "100000",
            "drawdown": "0",
            "positions": [],
        },
        "data_quality": "GOOD",
        "tradeable": True,
        "market_regime": "NORMAL",
        "market_price": "10",
        "invalidation_price": "9.5",
        "industry": "银行",
        "liquidity_amount": "100000000",
        "event_risk": False,
    }


def test_load_advisory_context_parses_strict_json_artifact(tmp_path) -> None:
    path = tmp_path / "context.json"
    path.write_text(json.dumps(_payload(), ensure_ascii=False), encoding="utf-8")

    context = load_advisory_context(path)

    assert context.forecast.symbol == "000001"
    assert context.forecast.maturity_date == date(2026, 8, 15)
    assert context.market_price == Decimal("10")
    assert context.portfolio.cash == Decimal("100000")


def test_load_advisory_context_rejects_unknown_top_level_fields(tmp_path) -> None:
    payload = _payload()
    payload["unexpected"] = True
    path = tmp_path / "context.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="context artifact"):
        load_advisory_context(path)
