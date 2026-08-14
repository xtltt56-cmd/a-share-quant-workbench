"""Future-only model competition and immutable prediction contracts.

Historical data may train and reject broken implementations, but it cannot
advance this contest.  A contestant enters only after its configuration and
training snapshot are frozen; every prediction is appended before its result
is available and every accepted result carries its source version and digest.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import date, datetime, timedelta, timezone
from types import MappingProxyType
from typing import Any

from a_share_quant.data.normalization import normalize_symbol
from a_share_quant.storage.prospective_ledger_store import ProspectiveLedgerStore

_ALLOWED_HORIZONS = frozenset({5, 10, 20})
_ALLOWED_EVIDENCE_MODES = frozenset({"PROSPECTIVE"})
_SHA256 = 64


class ImmutablePredictionError(RuntimeError):
    """Raised when a prediction is requested to be changed or deleted."""


class FrozenContestError(RuntimeError):
    """Raised when frozen contest registration or metrics are changed."""


class FutureTimestampError(ValueError):
    """Raised when an evidence timestamp is later than the local observation time."""


def _utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _as_date(value: date | datetime, *, field: str) -> date:
    if isinstance(value, datetime):
        return _utc(value, field=field).date()
    if not isinstance(value, date):
        raise ValueError(f"{field} must be a date")
    return value


def _finite(value: Any, *, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{field} must be finite")
    return parsed


def _future_weekday(start: date, horizon: int) -> date:
    current = start
    count = 0
    while count < horizon:
        current += timedelta(days=1)
        if current.weekday() < 5:
            count += 1
    return current


def _freeze_bands(value: Mapping[str, Any]) -> MappingProxyType:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("guidance_price_bands must be a non-empty mapping")
    result: dict[str, tuple[float, float]] = {}
    for raw_name, raw_bounds in value.items():
        name = str(raw_name).strip()
        if not name:
            raise ValueError("guidance price band name is required")
        if isinstance(raw_bounds, Mapping):
            lower = raw_bounds.get("lower")
            upper = raw_bounds.get("upper")
        else:
            try:
                lower, upper = raw_bounds
            except (TypeError, ValueError) as exc:
                raise ValueError("guidance price band must have two bounds") from exc
        lower_value = _finite(lower, field="guidance_price_band.lower")
        upper_value = _finite(upper, field="guidance_price_band.upper")
        if lower_value <= 0 or upper_value < lower_value:
            raise ValueError("guidance price band bounds are invalid")
        result[name] = (lower_value, upper_value)
    return MappingProxyType(result)


def _canonical(value: Any) -> Any:
    if isinstance(value, datetime):
        return _utc(value, field="timestamp").isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    return value


def _digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        _canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProspectivePrediction:
    """One immutable prediction registered before its label can be known."""

    model_id: str
    model_version: str
    config_hash: str
    training_snapshot_hash: str
    symbol: str
    name: str
    prediction_at: datetime
    as_of: date
    horizon: int
    score: float
    probability: float
    guidance_price_bands: Mapping[str, tuple[float, float]]
    evidence_mode: str = "PROSPECTIVE"
    maturity_date: date | None = None
    prediction_id: str = ""
    maturity_explicit: bool = dataclass_field(default=False, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        for field in (
            "model_id",
            "model_version",
            "config_hash",
            "training_snapshot_hash",
            "name",
        ):
            value = str(getattr(self, field)).strip()
            if not value:
                raise ValueError(f"{field} is required")
            object.__setattr__(self, field, value)
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        raw_prediction_at = self.prediction_at
        if isinstance(raw_prediction_at, str):
            raw_prediction_at = datetime.fromisoformat(raw_prediction_at)
        prediction_at = _utc(raw_prediction_at, field="prediction_at")
        object.__setattr__(self, "prediction_at", prediction_at)
        raw_as_of = self.as_of
        if isinstance(raw_as_of, str):
            raw_as_of = date.fromisoformat(raw_as_of)
        as_of = _as_date(raw_as_of, field="as_of")
        if as_of > prediction_at.date():
            raise ValueError("as_of cannot be in the future of prediction_at")
        object.__setattr__(self, "as_of", as_of)
        horizon = int(self.horizon)
        if horizon not in _ALLOWED_HORIZONS:
            raise ValueError("horizon must be one of 5, 10, or 20")
        object.__setattr__(self, "horizon", horizon)
        object.__setattr__(self, "score", _finite(self.score, field="score"))
        probability = _finite(self.probability, field="probability")
        if not 0 <= probability <= 1:
            raise ValueError("probability must be between zero and one")
        object.__setattr__(self, "probability", probability)
        object.__setattr__(self, "guidance_price_bands", _freeze_bands(self.guidance_price_bands))
        evidence_mode = str(self.evidence_mode).strip().upper()
        if evidence_mode not in _ALLOWED_EVIDENCE_MODES:
            raise ValueError("evidence_mode must be PROSPECTIVE")
        object.__setattr__(self, "evidence_mode", evidence_mode)
        raw_maturity = self.maturity_date
        maturity_was_explicit = raw_maturity is not None
        if isinstance(raw_maturity, str):
            raw_maturity = date.fromisoformat(raw_maturity)
        maturity = (
            _as_date(raw_maturity, field="maturity_date")
            if raw_maturity is not None
            else _future_weekday(as_of, horizon)
        )
        if maturity <= as_of:
            raise ValueError("maturity_date must be after as_of")
        if prediction_at.date() >= maturity:
            raise ValueError("prediction_at must precede maturity_date")
        object.__setattr__(self, "maturity_date", maturity)
        object.__setattr__(self, "maturity_explicit", maturity_was_explicit)
        provided_id = str(self.prediction_id).strip()
        object.__setattr__(self, "prediction_id", provided_id or self._computed_id())

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_version": self.model_version,
            "config_hash": self.config_hash,
            "training_snapshot_hash": self.training_snapshot_hash,
            "symbol": self.symbol,
            "name": self.name,
            "prediction_at": self.prediction_at,
            "as_of": self.as_of,
            "horizon": self.horizon,
            "score": self.score,
            "probability": self.probability,
            "guidance_price_bands": self.guidance_price_bands,
            "evidence_mode": self.evidence_mode,
            "maturity_date": self.maturity_date,
        }

    def _computed_id(self) -> str:
        return f"prediction-{_digest(self._identity_payload())[:32]}"

    @property
    def id(self) -> str:
        return self.prediction_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "prediction_id": self.prediction_id,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "config_hash": self.config_hash,
            "training_snapshot_hash": self.training_snapshot_hash,
            "symbol": self.symbol,
            "name": self.name,
            "prediction_at": self.prediction_at.isoformat(),
            "as_of": self.as_of.isoformat(),
            "horizon": self.horizon,
            "score": self.score,
            "probability": self.probability,
            "guidance_price_bands": dict(self.guidance_price_bands),
            "evidence_mode": self.evidence_mode,
            "maturity_date": self.maturity_date.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ProspectivePrediction:
        payload = dict(value)
        payload["prediction_at"] = datetime.fromisoformat(str(payload["prediction_at"]))
        payload["as_of"] = date.fromisoformat(str(payload["as_of"]))
        payload["maturity_date"] = date.fromisoformat(str(payload["maturity_date"]))
        return cls(**payload)


@dataclass(frozen=True)
class OutcomeObservation:
    """A possible future result; unusable observations remain pending."""

    prediction_id: str
    symbol: str
    maturity_date: date
    outcome_at: datetime
    realized_price: float | None
    realized_return: float | None
    data_version: str
    data_sha256: str
    status: str = "OK"
    fresh: bool = True
    complete: bool = True
    session_aligned: bool = True
    corporate_action_ok: bool = True

    def __post_init__(self) -> None:
        prediction_id = str(self.prediction_id).strip()
        if not prediction_id:
            raise ValueError("prediction_id is required")
        object.__setattr__(self, "prediction_id", prediction_id)
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        raw_maturity = self.maturity_date
        if isinstance(raw_maturity, str):
            raw_maturity = date.fromisoformat(raw_maturity)
        object.__setattr__(self, "maturity_date", _as_date(raw_maturity, field="maturity_date"))
        raw_outcome_at = self.outcome_at
        if isinstance(raw_outcome_at, str):
            raw_outcome_at = datetime.fromisoformat(raw_outcome_at)
        object.__setattr__(self, "outcome_at", _utc(raw_outcome_at, field="outcome_at"))
        data_version = str(self.data_version).strip()
        if not data_version:
            raise ValueError("data_version is required")
        object.__setattr__(self, "data_version", data_version)
        data_sha256 = str(self.data_sha256).strip().lower()
        if len(data_sha256) != _SHA256 or any(c not in "0123456789abcdef" for c in data_sha256):
            raise ValueError("data_sha256 must be a SHA-256 hex digest")
        object.__setattr__(self, "data_sha256", data_sha256)
        status = str(self.status).strip().upper()
        if status not in {"OK", "MISSING", "SUSPENDED", "STALE", "CONFLICT"}:
            raise ValueError("status is invalid")
        object.__setattr__(self, "status", status)
        for field in ("fresh", "complete", "session_aligned", "corporate_action_ok"):
            object.__setattr__(self, field, bool(getattr(self, field)))
        if self.realized_price is not None:
            price = _finite(self.realized_price, field="realized_price")
            if price <= 0:
                raise ValueError("realized_price must be positive")
            object.__setattr__(self, "realized_price", price)
        if self.realized_return is not None:
            object.__setattr__(
                self, "realized_return", _finite(self.realized_return, field="realized_return")
            )

    @property
    def id(self) -> str:
        return f"outcome-{_digest(self.to_dict())[:32]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "prediction_id": self.prediction_id,
            "symbol": self.symbol,
            "maturity_date": self.maturity_date.isoformat(),
            "outcome_at": self.outcome_at.isoformat(),
            "realized_price": self.realized_price,
            "realized_return": self.realized_return,
            "data_version": self.data_version,
            "data_sha256": self.data_sha256,
            "status": self.status,
            "fresh": self.fresh,
            "complete": self.complete,
            "session_aligned": self.session_aligned,
            "corporate_action_ok": self.corporate_action_ok,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> OutcomeObservation:
        payload = dict(value)
        payload["maturity_date"] = date.fromisoformat(str(payload["maturity_date"]))
        payload["outcome_at"] = datetime.fromisoformat(str(payload["outcome_at"]))
        return cls(**payload)


ProspectiveOutcome = OutcomeObservation


@dataclass(frozen=True)
class SettlementResult:
    prediction_id: str
    matured: bool
    outcome: OutcomeObservation | None = None
    delay_reason: str | None = None
    idempotent: bool = False


@dataclass(frozen=True)
class ProspectiveMetrics:
    """Cost-aware future metrics, kept separate from historical diagnostics."""

    directional_hit_rate: float | None = None
    brier: float | None = None
    ece: float | None = None
    rank_ic: float | None = None
    net_cost_return: float | None = None
    max_drawdown: float | None = None
    turnover: float | None = None
    coverage: float | None = None
    rejection_rate: float | None = None
    regime_stability: float | None = None


def _expected_calibration_error(probabilities: list[float], labels: list[float]) -> float:
    bins: list[list[tuple[float, float]]] = [[] for _ in range(10)]
    for probability, label in zip(probabilities, labels):
        bins[min(9, int(probability * 10))].append((probability, label))
    total = len(labels)
    return sum(
        len(bucket) / total * abs(
            sum(item[0] for item in bucket) / len(bucket)
            - sum(item[1] for item in bucket) / len(bucket)
        )
        for bucket in bins if bucket
    )


def _rank_correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    left_order = {value: rank for rank, value in enumerate(sorted(left))}
    right_order = {value: rank for rank, value in enumerate(sorted(right))}
    left_ranks = [left_order[value] for value in left]
    right_ranks = [right_order[value] for value in right]
    left_mean = sum(left_ranks) / len(left_ranks)
    right_mean = sum(right_ranks) / len(right_ranks)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left_ranks, right_ranks))
    denominator = math.sqrt(
        sum((a - left_mean) ** 2 for a in left_ranks)
        * sum((b - right_mean) ** 2 for b in right_ranks)
    )
    return numerator / denominator if denominator else 0.0


class ProspectiveCompetition:
    """Append predictions and settle only quality-approved future observations."""

    def __init__(
        self,
        *,
        store: ProspectiveLedgerStore,
        contest: ProspectiveContest | None = None,
        now: datetime | None = None,
        session_calendar: Iterable[date] | None = None,
    ) -> None:
        self.store = store
        self.contest = contest
        self.now = _utc(now or datetime.now(timezone.utc), field="now")
        if isinstance(session_calendar, date):
            raise ValueError("session_calendar must be an iterable of dates")
        self.session_calendar = (
            frozenset(_as_date(item, field="session_calendar") for item in session_calendar)
            if session_calendar is not None else None
        )

    def append_prediction(
        self,
        prediction: ProspectivePrediction | None = None,
        **fields: Any,
    ) -> ProspectivePrediction:
        if prediction is not None and fields:
            raise TypeError("provide prediction or prediction fields, not both")
        if prediction is None and "prediction_at" in fields:
            raw_timestamp = fields["prediction_at"]
            if isinstance(raw_timestamp, str):
                raw_timestamp = datetime.fromisoformat(raw_timestamp)
            if _utc(raw_timestamp, field="prediction_at") > self.now:
                raise FutureTimestampError("prediction timestamp is in the future")
        candidate = prediction if prediction is not None else ProspectivePrediction(**fields)
        if (
            self.session_calendar is None and candidate.maturity_explicit
        ):
            raise ValueError("explicit maturity_date requires a session calendar")
        if not isinstance(candidate, ProspectivePrediction):
            raise TypeError("prediction must be ProspectivePrediction")
        if candidate.prediction_at > self.now:
            raise FutureTimestampError("prediction timestamp is in the future")
        if candidate.maturity_date <= self.now.date():
            raise ValueError("prediction maturity is past the competition cutoff")
        if (
            self.session_calendar is not None
            and candidate.maturity_date not in self.session_calendar
        ):
            raise ValueError("maturity_date is not in the supplied session calendar")
        if self.session_calendar is not None:
            future_sessions = sorted(
                day for day in self.session_calendar if day > candidate.as_of
            )
            if len(future_sessions) < candidate.horizon:
                raise ValueError("session calendar is insufficient for horizon")
            if future_sessions[candidate.horizon - 1] != candidate.maturity_date:
                raise ValueError("maturity_date does not match calendar horizon")
        if self.contest is not None:
            if not self.contest.started:
                raise FrozenContestError("contest has not started")
            expected = (
                self.contest.model_id,
                self.contest.model_version,
                self.contest.config_hash,
                self.contest.training_snapshot_hash,
            )
            actual = (
                candidate.model_id,
                candidate.model_version,
                candidate.config_hash,
                candidate.training_snapshot_hash,
            )
            if actual != expected or candidate.evidence_mode != "PROSPECTIVE":
                raise ValueError("prediction does not match frozen contest")
            if candidate.prediction_at < self.contest.contest_started_at:
                raise ValueError("prediction_at precedes contest start")
        return self.store.append_prediction(candidate)

    def read(self, prediction_id: str) -> ProspectivePrediction:
        return self.store.read(prediction_id)

    def predictions(self) -> tuple[ProspectivePrediction, ...]:
        return self.store.predictions()

    def settlements(self) -> tuple[OutcomeObservation, ...]:
        return self.store.settlements()

    @property
    def matured_predictions(self) -> int:
        return self.store.matured_predictions

    def compute_metrics(self, *, cost_bps: float = 20.0) -> ProspectiveMetrics:
        """Compute future-only metrics from settled ledger records."""

        if cost_bps < 0 or not math.isfinite(float(cost_bps)):
            raise ValueError("cost_bps must be finite and non-negative")
        predictions = {item.id: item for item in self.store.predictions()}
        outcomes = [item for item in self.store.settlements() if item.prediction_id in predictions]
        total = len(predictions)
        coverage = len(outcomes) / total if total else 0.0
        if not outcomes:
            return ProspectiveMetrics(coverage=coverage, rejection_rate=1.0)
        returns = [
            float(item.realized_return)
            for item in outcomes
            if item.realized_return is not None
        ]
        scores = [predictions[item.prediction_id].score for item in outcomes]
        probabilities = [predictions[item.prediction_id].probability for item in outcomes]
        labels = [1.0 if value > 0 else 0.0 for value in returns]
        turnover = len(outcomes) / max(total, 1)
        net = sum(returns) / len(returns) - float(cost_bps) / 10000.0 * turnover
        running = peak = drawdown = 0.0
        for value in returns:
            running += value - float(cost_bps) / 10000.0 * turnover / len(returns)
            peak = max(peak, running)
            drawdown = max(drawdown, peak - running)
        brier = sum(
            (probability - label) ** 2
            for probability, label in zip(probabilities, labels)
        ) / len(labels)
        ece = _expected_calibration_error(probabilities, labels)
        rank_ic = _rank_correlation(scores, returns)
        return ProspectiveMetrics(
            directional_hit_rate=sum(
                (score >= 0.5) == bool(label)
                for score, label in zip(probabilities, labels)
            ) / len(labels),
            brier=brier, ece=ece, rank_ic=rank_ic, net_cost_return=net,
            max_drawdown=drawdown, turnover=turnover, coverage=coverage,
            rejection_rate=1.0 - coverage,
        )

    def delete(self, _prediction_id: str) -> None:
        raise ImmutablePredictionError("失败预测也必须保留，预测不可删除")

    def settle_due(self, outcome: OutcomeObservation) -> SettlementResult:
        if not isinstance(outcome, OutcomeObservation):
            raise TypeError("outcome must be OutcomeObservation")
        if outcome.outcome_at > self.now:
            raise FutureTimestampError("outcome timestamp is in the future")
        prediction = self.store.read(outcome.prediction_id)
        if prediction.symbol != outcome.symbol:
            raise ValueError("outcome symbol does not match prediction")
        existing = next(
            (item for item in self.store.settlements() if item.prediction_id == prediction.id),
            None,
        )
        if existing is not None:
            if existing == outcome:
                return SettlementResult(prediction.id, True, existing, idempotent=True)
            raise ValueError("prediction already has a different settlement")
        if outcome.maturity_date != prediction.maturity_date:
            raise ValueError("outcome maturity date does not match prediction")
        delay_reason = self._delay_reason(prediction, outcome)
        if delay_reason is not None:
            self.store.append_pending(outcome, delay_reason)
            return SettlementResult(prediction.id, False, delay_reason=delay_reason)
        self.store.append_settlement(outcome)
        return SettlementResult(prediction.id, True, outcome=outcome)

    def settle(
        self, outcomes: OutcomeObservation | Iterable[OutcomeObservation]
    ) -> SettlementResult | tuple[SettlementResult, ...]:
        if isinstance(outcomes, OutcomeObservation):
            return self.settle_due(outcomes)
        return tuple(self.settle_due(item) for item in outcomes)

    @staticmethod
    def _delay_reason(
        prediction: ProspectivePrediction,
        outcome: OutcomeObservation,
    ) -> str | None:
        if outcome.outcome_at.date() < prediction.maturity_date:
            return "NOT_DUE"
        if outcome.status != "OK":
            return {
                "MISSING": "MISSING",
                "SUSPENDED": "SUSPENDED",
                "STALE": "STALE",
                "CONFLICT": "CONFLICT",
            }[outcome.status]
        if not outcome.fresh:
            return "STALE"
        if not outcome.complete:
            return "INCOMPLETE"
        if not outcome.session_aligned:
            return "SESSION_MISMATCH"
        if not outcome.corporate_action_ok:
            return "CORPORATE_ACTION_CONFLICT"
        if outcome.realized_price is None or outcome.realized_return is None:
            return "MISSING"
        return None


class ProspectiveContest:
    """Frozen registration and future-only maturity state machine."""

    def __init__(
        self,
        *,
        champion_id: str | None = "champion-v1",
        now: datetime | None = None,
        provisional_sessions: int = 20,
        provisional_predictions: int = 100,
        approval_sessions: int = 60,
        approval_predictions: int = 200,
    ) -> None:
        self.champion_id = champion_id
        self.now = _utc(now or datetime.now(timezone.utc), field="now")
        thresholds = (
            int(provisional_sessions), int(provisional_predictions),
            int(approval_sessions), int(approval_predictions),
        )
        if thresholds != (20, 100, 60, 200):
            raise ValueError("contest thresholds are fixed at 20/100 and 60/200")
        self._provisional_sessions, self._provisional_predictions = thresholds[:2]
        self._approval_sessions, self._approval_predictions = thresholds[2:]
        self._started = False
        self._started_at: datetime | None = None
        self._model_id: str | None = None
        self._model_version: str | None = None
        self._config_hash: str | None = None
        self._training_snapshot_hash: str | None = None
        self._primary_metric: str | None = None
        self._tie_break: tuple[str, ...] = ()
        self._future_sessions = 0
        self._matured_predictions = 0
        self._gates_pass = False
        self._status = "NOT_STARTED"
        self._metrics: ProspectiveMetrics | None = None

    def start(
        self,
        *,
        model_id: str,
        model_version: str,
        config_hash: str,
        training_snapshot_hash: str,
        primary_metric: str = "net_cost_return",
        tie_break: Iterable[str] = ("max_drawdown", "brier"),
        started_at: datetime | None = None,
    ) -> ProspectiveContest:
        values = {
            "model_id": str(model_id).strip(),
            "model_version": str(model_version).strip(),
            "config_hash": str(config_hash).strip(),
            "training_snapshot_hash": str(training_snapshot_hash).strip(),
            "primary_metric": str(primary_metric).strip(),
            "tie_break": tuple(str(item).strip() for item in tie_break),
        }
        if any(
            not values[key]
            for key in (
                "model_id",
                "model_version",
                "config_hash",
                "training_snapshot_hash",
                "primary_metric",
            )
        ):
            raise ValueError("contest registration fields are required")
        if not values["tie_break"] or any(not item for item in values["tie_break"]):
            raise ValueError("tie_break must be non-empty")
        at = _utc(started_at or self.now, field="contest_started_at")
        if at > self.now:
            raise FutureTimestampError("contest start timestamp is in the future")
        if self._started:
            existing = (
                self._model_id,
                self._model_version,
                self._config_hash,
                self._training_snapshot_hash,
                self._primary_metric,
                self._tie_break,
            )
            if existing != (
                values["model_id"],
                values["model_version"],
                values["config_hash"],
                values["training_snapshot_hash"],
                values["primary_metric"],
                values["tie_break"],
            ):
                raise FrozenContestError("contest registration is frozen")
            return self
        self._started = True
        self._started_at = at
        self._model_id = values["model_id"]
        self._model_version = values["model_version"]
        self._config_hash = values["config_hash"]
        self._training_snapshot_hash = values["training_snapshot_hash"]
        self._primary_metric = values["primary_metric"]
        self._tie_break = values["tie_break"]
        self._status = "PROSPECTIVE_COLLECTING"
        return self

    def register_version(
        self,
        *,
        model_version: str,
        config_hash: str,
        training_snapshot_hash: str,
        model_id: str | None = None,
    ) -> None:
        if not self._started:
            raise FrozenContestError("contest has not started")
        for value in (model_version, config_hash, training_snapshot_hash):
            if not str(value).strip():
                raise ValueError("contest version fields are required")
        self._model_version = str(model_version).strip()
        self._config_hash = str(config_hash).strip()
        self._training_snapshot_hash = str(training_snapshot_hash).strip()
        if model_id is not None:
            if not str(model_id).strip():
                raise ValueError("model_id is required")
            self._model_id = str(model_id).strip()
        self._future_sessions = 0
        self._matured_predictions = 0
        self._gates_pass = False
        self._status = "PROSPECTIVE_COLLECTING"
        self._metrics = None

    def record_observations(
        self,
        *,
        sessions: int,
        matured: int,
        gates_pass: bool = False,
        historical_matured: int = 0,
        metrics: ProspectiveMetrics | None = None,
    ) -> None:
        if not self._started:
            raise FrozenContestError("contest has not started")
        sessions_value = int(sessions)
        matured_value = int(matured)
        if min(sessions_value, matured_value, int(historical_matured)) < 0:
            raise ValueError("observation counters cannot be negative")
        if sessions_value < self._future_sessions or matured_value < self._matured_predictions:
            raise ValueError("future observation counters cannot move backwards")
        # ``historical_matured`` is deliberately accepted for diagnostics but
        # never enters the prospective counters.
        _ = historical_matured
        self._future_sessions = sessions_value
        self._matured_predictions = matured_value
        if metrics is not None and not isinstance(metrics, ProspectiveMetrics):
            raise TypeError("metrics must be ProspectiveMetrics")
        if metrics is None and gates_pass:
            raise ValueError("metrics are required to derive contest gates")
        self._gates_pass = self._metrics_pass(metrics) if metrics is not None else False
        self._metrics = metrics
        self._refresh_status()

    def record_session(
        self,
        *,
        matured_predictions: int,
        gates_pass: bool = False,
        metrics: ProspectiveMetrics | None = None,
    ) -> None:
        if not self._started:
            raise FrozenContestError("contest has not started")
        matured_value = int(matured_predictions)
        if matured_value < 0:
            raise ValueError("matured_predictions cannot be negative")
        self.record_observations(
            sessions=self._future_sessions + 1,
            matured=self._matured_predictions + matured_value,
            gates_pass=gates_pass,
            metrics=metrics,
        )

    settle = record_observations

    def _refresh_status(self) -> None:
        if (
            self._future_sessions >= self._approval_sessions
            and self._matured_predictions >= self._approval_predictions
            and self._gates_pass
        ):
            self._status = "AWAITING_MANUAL_APPROVAL"
        elif self._gates_pass and (
            self._future_sessions >= self._provisional_sessions
            and self._matured_predictions >= self._provisional_predictions
        ):
            self._status = "PROVISIONAL_UNMATURED_OBSERVATION"
        else:
            self._status = "PROSPECTIVE_COLLECTING"

    def _metrics_pass(self, metrics: ProspectiveMetrics) -> bool:
        """Apply frozen conservative quality gates; no caller boolean override."""

        required = (metrics.net_cost_return, metrics.max_drawdown, metrics.coverage)
        return (
            all(value is not None and math.isfinite(float(value)) for value in required)
            and float(metrics.net_cost_return) > 0
            and 0 <= float(metrics.max_drawdown) <= 0.35
            and float(metrics.coverage) >= 0.8
        )

    def change_primary_metric(self, _metric: str) -> None:
        raise FrozenContestError("primary_metric is frozen after contest start")

    def change_tie_break(self, _tie_break: Iterable[str]) -> None:
        raise FrozenContestError("tie_break is frozen after contest start")

    @property
    def status(self) -> str:
        return self._status

    @property
    def started(self) -> bool:
        return self._started

    @property
    def model_id(self) -> str | None:
        return self._model_id

    @property
    def provisional_sessions(self) -> int:
        return self._provisional_sessions

    @property
    def provisional_predictions(self) -> int:
        return self._provisional_predictions

    @property
    def approval_sessions(self) -> int:
        return self._approval_sessions

    @property
    def approval_predictions(self) -> int:
        return self._approval_predictions

    @property
    def future_sessions(self) -> int:
        return self._future_sessions

    @property
    def matured_predictions(self) -> int:
        return self._matured_predictions

    @property
    def can_replace_champion(self) -> bool:
        return False

    @property
    def can_issue_approval(self) -> bool:
        return self._status == "AWAITING_MANUAL_APPROVAL"

    @property
    def approval_ready(self) -> bool:
        return self.can_issue_approval

    @property
    def contest_started_at(self) -> datetime | None:
        return self._started_at

    @property
    def primary_metric(self) -> str | None:
        return self._primary_metric

    @property
    def tie_break(self) -> tuple[str, ...]:
        return self._tie_break

    @property
    def model_version(self) -> str | None:
        return self._model_version

    @property
    def config_hash(self) -> str | None:
        return self._config_hash

    @property
    def training_snapshot_hash(self) -> str | None:
        return self._training_snapshot_hash

    @property
    def metrics(self) -> ProspectiveMetrics | None:
        return self._metrics

    @property
    def history_is_promotional(self) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "champion_id": self.champion_id,
            "contest_started_at": self._started_at.isoformat() if self._started_at else None,
            "model_id": self._model_id,
            "model_version": self._model_version,
            "config_hash": self._config_hash,
            "training_snapshot_hash": self._training_snapshot_hash,
            "primary_metric": self._primary_metric,
            "tie_break": list(self._tie_break),
            "future_sessions": self._future_sessions,
            "matured_predictions": self._matured_predictions,
            "history_results_promotional": False,
            "can_replace_champion": self.can_replace_champion,
            "can_issue_approval": self.can_issue_approval,
            "provisional_remaining_sessions": max(
                0, self.provisional_sessions - self._future_sessions
            ),
            "provisional_remaining_predictions": max(
                0, self.provisional_predictions - self._matured_predictions
            ),
            "approval_remaining_sessions": max(0, self.approval_sessions - self._future_sessions),
            "approval_remaining_predictions": max(
                0, self.approval_predictions - self._matured_predictions
            ),
        }


def started_contest(**kwargs: Any) -> ProspectiveContest:
    """Small test/CLI convenience helper that freezes a contest immediately."""

    contest = ProspectiveContest(
        champion_id=kwargs.pop("champion_id", "champion-v1"),
        now=kwargs.pop("now", None),
    )
    contest.start(**kwargs)
    return contest


def outcome(**kwargs: Any) -> OutcomeObservation:
    """Factory kept intentionally explicit for offline worker call sites."""

    return OutcomeObservation(**kwargs)


__all__ = [
    "FrozenContestError",
    "FutureTimestampError",
    "ImmutablePredictionError",
    "OutcomeObservation",
    "ProspectiveCompetition",
    "ProspectiveContest",
    "ProspectiveMetrics",
    "ProspectiveOutcome",
    "ProspectivePrediction",
    "SettlementResult",
    "outcome",
    "started_contest",
]
