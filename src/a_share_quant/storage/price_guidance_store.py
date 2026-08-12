"""Durable, append-only and tamper-evident price guidance artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from a_share_quant.advisory.price_contracts import (
    PriceGuidanceObservation,
    PriceGuidancePlan,
)


class PriceGuidanceStore:
    _FORMAT_VERSION = 1
    _MAX_BYTES = 16 * 1024 * 1024

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._plans: dict[str, PriceGuidancePlan] = {}
        self._observations: dict[str, PriceGuidanceObservation] = {}
        if self.path.exists():
            self._load()

    def replace_plans(self, plans: tuple[PriceGuidancePlan, ...]) -> None:
        if len(plans) > 5000 or any(not isinstance(item, PriceGuidancePlan) for item in plans):
            raise ValueError("price guidance artifact is invalid")
        identifiers = [item.plan_id for item in plans]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("price guidance artifact is invalid")
        self._plans = {item.plan_id: item for item in plans}
        self._observations = {
            key: value for key, value in self._observations.items() if value.plan_id in self._plans
        }
        self._persist()

    def plans(self) -> tuple[PriceGuidancePlan, ...]:
        return tuple(sorted(self._plans.values(), key=lambda item: (item.valid_for, item.symbol)))

    def append_observation(self, observation: PriceGuidanceObservation) -> None:
        if not isinstance(observation, PriceGuidanceObservation):
            raise TypeError("price guidance store accepts only observations")
        if observation.plan_id not in self._plans:
            raise ValueError("unknown plan for observation")
        if observation.observation_id in self._observations:
            raise ValueError("duplicate observation")
        if len(self._observations) >= 100000:
            raise ValueError("price guidance artifact is invalid")
        self._observations[observation.observation_id] = observation
        self._persist()

    def observations(self, plan_id: str | None = None) -> tuple[PriceGuidanceObservation, ...]:
        values = tuple(self._observations.values())
        if plan_id is not None:
            values = tuple(item for item in values if item.plan_id == plan_id)
        return tuple(sorted(values, key=lambda item: (item.observed_at, item.observation_id)))

    def _load(self) -> None:
        if self.path.is_symlink() or not self.path.is_file():
            raise ValueError("price guidance artifact is invalid")
        try:
            raw = self.path.read_bytes()
            if not raw or len(raw) > self._MAX_BYTES:
                raise ValueError
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict) or set(payload) != {
                "format_version",
                "body",
                "sha256",
            }:
                raise ValueError
            body = payload["body"]
            if payload["format_version"] != self._FORMAT_VERSION or not isinstance(body, dict):
                raise ValueError
            if set(body) != {"operating_mode", "plans", "observations"}:
                raise ValueError
            if body["operating_mode"] != "PAPER_ONLY":
                raise ValueError
            canonical = _canonical(body)
            if payload["sha256"] != hashlib.sha256(canonical).hexdigest():
                raise ValueError
            plans = tuple(PriceGuidancePlan.from_dict(item) for item in body["plans"])
            observations = tuple(
                PriceGuidanceObservation.from_dict(item) for item in body["observations"]
            )
            if len({item.plan_id for item in plans}) != len(plans):
                raise ValueError
            if len({item.observation_id for item in observations}) != len(observations):
                raise ValueError
            plan_ids = {item.plan_id for item in plans}
            if any(item.plan_id not in plan_ids for item in observations):
                raise ValueError
            now = datetime.now(timezone.utc)
            if any(
                item.observed_at > now + timedelta(minutes=5)
                for item in observations
            ):
                raise ValueError
        except Exception as exc:
            if isinstance(exc, ValueError) and str(exc) == "price guidance artifact is invalid":
                raise
            raise ValueError("price guidance artifact is invalid") from exc
        self._plans = {item.plan_id: item for item in plans}
        self._observations = {item.observation_id: item for item in observations}

    def _persist(self) -> None:
        body = {
            "operating_mode": "PAPER_ONLY",
            "plans": [item.to_dict() for item in self.plans()],
            "observations": [item.to_dict() for item in self.observations()],
        }
        payload = {
            "format_version": self._FORMAT_VERSION,
            "body": body,
            "sha256": hashlib.sha256(_canonical(body)).hexdigest(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(
                    payload,
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            raise ValueError("price guidance artifact cannot be written") from exc
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()


def _canonical(body: dict[str, Any]) -> bytes:
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


__all__ = ["PriceGuidanceStore"]
