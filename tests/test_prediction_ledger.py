from datetime import date, datetime, timezone
from decimal import Decimal

from a_share_quant.advisory.contracts import ForecastRecord
from a_share_quant.advisory.store import PredictionLedgerStore


def test_matured_prediction_appends_outcome_without_mutating_original() -> None:
    generated_at = datetime(2026, 8, 10, 8, tzinfo=timezone.utc)
    prediction = ForecastRecord.for_horizons(
        symbol="000001",
        generated_at=generated_at,
        data_cutoff=generated_at,
        reference_price=Decimal("10"),
        model_version="champion-v1",
        data_version="data-v1",
        feature_version="features-v1",
        horizons=(5, 10, 20),
        maturity_dates={5: date(2026, 8, 15), 10: date(2026, 8, 22), 20: date(2026, 9, 5)},
    )[0]
    store = PredictionLedgerStore()
    store.append_prediction(prediction)

    matured = store.mature(
        as_of=date(2026, 8, 15),
        price_lookup=lambda symbol, maturity_date: Decimal("11"),
    )

    assert store.predictions() == (prediction,)
    assert len(matured) == 1
    assert matured[0].forecast_id == prediction.forecast_id
    assert matured[0].realized_return == Decimal("0.100000")
    assert store.outcomes() == matured
