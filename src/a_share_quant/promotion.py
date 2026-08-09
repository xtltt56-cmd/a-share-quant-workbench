"""Paper-only promotion state machine and structural gate checks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any


class PromotionState(str, Enum):
    RESEARCH = "RESEARCH"
    SIGNAL_VALIDATED = "SIGNAL_VALIDATED"
    FAST_BACKTEST_PASS = "FAST_BACKTEST_PASS"
    ROBUSTNESS_PASS = "ROBUSTNESS_PASS"
    RQALPHA_PASS = "RQALPHA_PASS"
    WALK_FORWARD_PASS = "WALK_FORWARD_PASS"
    PAPER_TRADING = "PAPER_TRADING"
    PAPER_VALIDATED = "PAPER_VALIDATED"

    @classmethod
    def parse(cls, value: str | PromotionState) -> PromotionState:
        if isinstance(value, cls):
            return value
        if str(value).upper() == "LIVE":
            raise ValueError("LIVE promotion state is forbidden")
        try:
            return cls(str(value).upper())
        except ValueError as exc:
            raise ValueError(f"invalid promotion state: {value}") from exc


@dataclass(frozen=True)
class GateDecision:
    accepted: bool
    from_state: PromotionState
    to_state: PromotionState
    checks: dict[str, bool]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "from_state": self.from_state.value,
            "to_state": self.to_state.value,
            "checks": dict(self.checks),
            "reason": self.reason,
        }


class PromotionGate:
    """Structural checks required before a candidate can be promoted."""

    _REQUIRED_CHECKS = (
        "no_pit_leak",
        "no_look_ahead",
        "dataset_valid",
        "test_split_valid",
        "signal_frame_valid",
        "ic_valid",
        "rank_ic_valid",
        "minimum_sample_count_valid",
    )

    @classmethod
    def required_checks(cls) -> tuple[str, ...]:
        return cls._REQUIRED_CHECKS

    @classmethod
    def evaluate(cls, checks: Mapping[str, bool]) -> tuple[bool, dict[str, bool]]:
        normalized = {name: bool(checks.get(name, False)) for name in cls._REQUIRED_CHECKS}
        return all(normalized.values()), normalized


class PromotionStateMachine:
    """Sequential, persisted-evidence state machine with no live state."""

    _ORDER = (
        PromotionState.RESEARCH,
        PromotionState.SIGNAL_VALIDATED,
        PromotionState.FAST_BACKTEST_PASS,
        PromotionState.ROBUSTNESS_PASS,
        PromotionState.RQALPHA_PASS,
        PromotionState.WALK_FORWARD_PASS,
        PromotionState.PAPER_TRADING,
        PromotionState.PAPER_VALIDATED,
    )

    def __init__(self, state: PromotionState | str = PromotionState.RESEARCH) -> None:
        self._state = PromotionState.parse(state)
        self.history: list[GateDecision] = []

    @property
    def state(self) -> PromotionState:
        return self._state

    def transition(
        self,
        target: PromotionState | str,
        checks: Mapping[str, bool],
    ) -> GateDecision:
        target_state = PromotionState.parse(target)
        current_index = self._ORDER.index(self._state)
        target_index = self._ORDER.index(target_state)
        if target_index != current_index + 1:
            raise ValueError("promotion transitions must be sequential")
        passed, normalized = PromotionGate.evaluate(checks)
        decision = GateDecision(
            accepted=passed,
            from_state=self._state,
            to_state=target_state,
            checks=normalized,
            reason=(
                "all structural checks passed"
                if passed
                else "one or more structural checks failed"
            ),
        )
        self.history.append(decision)
        if passed:
            self._state = target_state
        return decision
