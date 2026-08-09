"""Append-only in-memory prediction and realized-outcome ledger."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timezone
from decimal import ROUND_HALF_UP, Decimal

from .contracts import ForecastRecord, OutcomeRecord

_RETURN = Decimal("0.000001")


class PredictionLedgerStore:
    """Separates immutable forecast records from later realized outcomes."""

    def __init__(self) -> None:
        self._predictions: list[ForecastRecord] = []
        self._predictions_by_id: dict[str, ForecastRecord] = {}
        self._outcomes: list[OutcomeRecord] = []
        self._outcome_ids: set[str] = set()

    def append_prediction(self, prediction: ForecastRecord) -> bool:
        existing = self._predictions_by_id.get(prediction.forecast_id)
        if existing is not None:
            if existing == prediction:
                return False
            raise ValueError("forecast_id already exists with different details")
        self._predictions.append(prediction)
        self._predictions_by_id[prediction.forecast_id] = prediction
        return True

    def predictions(self) -> tuple[ForecastRecord, ...]:
        return tuple(self._predictions)

    def outcomes(self) -> tuple[OutcomeRecord, ...]:
        return tuple(self._outcomes)

    def mature(
        self,
        *,
        as_of: date,
        price_lookup: Callable[[str, date], Decimal | float | int | str],
    ) -> tuple[OutcomeRecord, ...]:
        if not isinstance(as_of, date):
            raise ValueError("as_of must be a date")
        matured: list[OutcomeRecord] = []
        for prediction in sorted(
            self._predictions,
            key=lambda item: (item.maturity_date, item.symbol, item.horizon_days),
        ):
            if prediction.forecast_id in self._outcome_ids or prediction.maturity_date > as_of:
                continue
            realized_price = Decimal(str(price_lookup(prediction.symbol, prediction.maturity_date)))
            if not realized_price.is_finite() or realized_price <= 0:
                raise ValueError("price_lookup must return a positive finite price")
            realized_return = (
                realized_price / prediction.reference_price - Decimal("1")
            ).quantize(_RETURN, rounding=ROUND_HALF_UP)
            outcome = OutcomeRecord(
                forecast_id=prediction.forecast_id,
                symbol=prediction.symbol,
                horizon_days=prediction.horizon_days,
                maturity_date=prediction.maturity_date,
                matured_at=datetime.combine(as_of, time.min, tzinfo=timezone.utc),
                realized_price=realized_price,
                realized_return=realized_return,
            )
            self._outcomes.append(outcome)
            self._outcome_ids.add(prediction.forecast_id)
            matured.append(outcome)
        return tuple(matured)
