"""Governed model promotion with explicit evidence and local human approval."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from a_share_quant.contracts.modes import validate_data_mode

_HORIZONS = frozenset({5, 10, 20})
_VALIDATION_CHECKS = (
    "causality_pass",
    "reproducible_pass",
    "walk_forward_pass",
    "post_cost_pass",
    "drawdown_pass",
    "tail_risk_pass",
    "calibration_pass",
    "perturbation_pass",
    "drift_pass",
    "shadow_pass",
)


def _utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class EconomicHypothesis:
    factor_name: str
    mechanism: str
    expected_direction: str
    required_fields: tuple[str, ...]
    decay_horizon_days: int
    failure_modes: tuple[str, ...]
    neutralization_rules: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field in ("factor_name", "mechanism"):
            if not str(getattr(self, field)).strip():
                raise ValueError(f"{field} is required")
            object.__setattr__(self, field, str(getattr(self, field)).strip())
        direction = str(self.expected_direction).strip().lower()
        if direction not in {"positive", "negative", "neutral"}:
            raise ValueError("expected_direction must be positive, negative, or neutral")
        object.__setattr__(self, "expected_direction", direction)
        if not self.required_fields or any(not str(item).strip() for item in self.required_fields):
            raise ValueError("required_fields cannot be empty")
        if self.decay_horizon_days <= 0:
            raise ValueError("decay_horizon_days must be positive")
        if not self.failure_modes or any(not str(item).strip() for item in self.failure_modes):
            raise ValueError("failure_modes cannot be empty")


@dataclass(frozen=True)
class ModelCard:
    candidate_id: str
    model_family: str
    intended_use: str
    horizons: tuple[int, ...]
    hypotheses: tuple[EconomicHypothesis, ...]
    created_at: datetime
    code_hash: str = "unrecorded"
    data_mode: str = "historical"

    def __post_init__(self) -> None:
        for field in ("candidate_id", "model_family", "intended_use", "code_hash"):
            if not str(getattr(self, field)).strip():
                raise ValueError(f"{field} is required")
            object.__setattr__(self, field, str(getattr(self, field)).strip())
        if not self.horizons or set(self.horizons).difference(_HORIZONS):
            raise ValueError("horizons must be a non-empty subset of 5, 10, and 20")
        if len(set(self.horizons)) != len(self.horizons):
            raise ValueError("horizons must be unique")
        if not self.hypotheses or not all(
            isinstance(item, EconomicHypothesis) for item in self.hypotheses
        ):
            raise ValueError("hypotheses must contain EconomicHypothesis records")
        object.__setattr__(self, "created_at", _utc(self.created_at, field="created_at"))
        object.__setattr__(self, "data_mode", validate_data_mode(self.data_mode))


@dataclass(frozen=True)
class ValidationBundle:
    bundle_id: str
    causality_pass: bool
    reproducible_pass: bool
    walk_forward_pass: bool
    post_cost_pass: bool
    drawdown_pass: bool
    tail_risk_pass: bool
    calibration_pass: bool
    perturbation_pass: bool
    drift_pass: bool
    shadow_pass: bool
    data_mode: str = "historical"

    def __post_init__(self) -> None:
        if not str(self.bundle_id).strip():
            raise ValueError("bundle_id is required")
        object.__setattr__(self, "bundle_id", str(self.bundle_id).strip())
        object.__setattr__(self, "data_mode", validate_data_mode(self.data_mode))

    @classmethod
    def all_passed(cls, bundle_id: str, **overrides: bool) -> ValidationBundle:
        unknown = set(overrides).difference(set(_VALIDATION_CHECKS) | {"data_mode"})
        if unknown:
            raise ValueError("unknown validation override")
        values = {name: bool(overrides.get(name, True)) for name in _VALIDATION_CHECKS}
        data_mode = str(overrides.get("data_mode", "historical"))
        return cls(bundle_id=bundle_id, data_mode=data_mode, **values)

    def checks(self) -> dict[str, bool]:
        return {name: bool(getattr(self, name)) for name in _VALIDATION_CHECKS}

    def passes(self) -> bool:
        return self.data_mode != "fixture" and all(self.checks().values())


@dataclass(frozen=True)
class PromotionRequest:
    request_id: str
    candidate_id: str
    bundle_id: str
    requested_at: datetime
    status: str = "PENDING_APPROVAL"


@dataclass(frozen=True)
class PromotionDecision:
    request_id: str
    accepted: bool
    champion_id: str | None
    previous_champion_id: str | None
    approved_by: str
    decided_at: datetime
    reason: str


class ModelGovernance:
    """Candidates can request promotion but only a local human approval changes alias."""

    def __init__(self) -> None:
        self._cards: dict[str, ModelCard] = {}
        self._bundles: dict[str, ValidationBundle] = {}
        self._candidate_bundle_ids: dict[str, str] = {}
        self._requests: dict[str, PromotionRequest] = {}
        self._decisions: list[PromotionDecision] = []
        self.champion_id: str | None = None

    def register_challenger(self, card: ModelCard, evidence: ValidationBundle) -> None:
        if card.candidate_id in self._cards:
            raise ValueError("candidate_id is already registered")
        self._cards[card.candidate_id] = card
        self._bundles[evidence.bundle_id] = evidence
        self._candidate_bundle_ids[card.candidate_id] = evidence.bundle_id

    def request_promotion(self, candidate_id: str) -> PromotionRequest:
        bundle = self._validation_for(candidate_id)
        if not bundle.passes():
            raise ValueError("validation evidence does not authorize promotion")
        request = PromotionRequest(
            request_id=f"promotion-{uuid4().hex}",
            candidate_id=candidate_id,
            bundle_id=bundle.bundle_id,
            requested_at=datetime.now(timezone.utc),
        )
        self._requests[request.request_id] = request
        return request

    def approve_promotion(self, request_id: str, *, approved_by: str) -> PromotionDecision:
        request = self._requests.get(request_id)
        if request is None:
            raise ValueError("unknown promotion request")
        approver = str(approved_by).strip()
        if not approver:
            raise ValueError("approved_by is required")
        bundle = self._validation_for(request.candidate_id)
        if not bundle.passes():
            raise ValueError("validation evidence no longer authorizes promotion")
        previous = self.champion_id
        self.champion_id = request.candidate_id
        decision = PromotionDecision(
            request_id=request_id,
            accepted=True,
            champion_id=self.champion_id,
            previous_champion_id=previous,
            approved_by=approver,
            decided_at=datetime.now(timezone.utc),
            reason="explicit local approval after complete validation evidence",
        )
        self._decisions.append(decision)
        return decision

    def decisions(self) -> tuple[PromotionDecision, ...]:
        return tuple(self._decisions)

    def rollback(
        self,
        *,
        to_candidate_id: str | None,
        approved_by: str,
    ) -> PromotionDecision:
        """Record an explicit rollback to a registered candidate or no champion."""

        approver = str(approved_by).strip()
        if not approver:
            raise ValueError("approved_by is required")
        target = None if to_candidate_id is None else str(to_candidate_id).strip()
        if target is not None and target not in self._cards:
            raise ValueError("rollback target is not a registered candidate")
        previous = self.champion_id
        self.champion_id = target
        decision = PromotionDecision(
            request_id=f"rollback-{uuid4().hex}",
            accepted=True,
            champion_id=target,
            previous_champion_id=previous,
            approved_by=approver,
            decided_at=datetime.now(timezone.utc),
            reason="explicit local rollback after model or data-quality review",
        )
        self._decisions.append(decision)
        return decision

    def _validation_for(self, candidate_id: str) -> ValidationBundle:
        if candidate_id not in self._cards:
            raise ValueError("candidate_id is not registered")
        bundle_id = self._candidate_bundle_ids[candidate_id]
        return self._bundles[bundle_id]
