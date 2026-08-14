"""Bounded research workers launched only by the local workbench.

Workers expose no path, model, or provider flags.  Each fixed stage consumes
only an integrity-checked project-local contract and records a small status
artifact for the lifecycle supervisor.  Missing inputs are an auditable
``BLOCKED`` outcome; workers never fabricate predictions or promotions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from a_share_quant.storage.project_storage import ProjectStoragePolicy

_JOBS = ("history", "screen", "predict", "settle")
_REASONS = {
    "history": "HISTORY_INPUT_NOT_READY",
    "screen": "VERIFIED_DATASET_NOT_READY",
    "predict": "FUTURE_CONTEST_NOT_READY",
    "settle": "REFRESHED_OUTCOME_NOT_READY",
}
_MAX_CONTROL_BYTES = 1_048_576
_MAX_OUTPUT_BYTES = 1_048_576


class _Blocked(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse exactly one fixed worker verb and no user-controlled options."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", choices=_JOBS)
    return parser.parse_args(argv)


def run_job(
    job: str,
    repo_root: str | Path,
    *,
    storage_policy: ProjectStoragePolicy | None = None,
) -> int:
    """Run one internal stage with only project-owned output paths."""

    if job not in _JOBS:
        raise ValueError("research worker job is not allowlisted")
    root = Path(repo_root).resolve(strict=True)
    # Direct library tests may use a dedicated D-drive root. The process entry
    # point below always supplies the production D-drive policy.
    policy = storage_policy or ProjectStoragePolicy(root, required_drive=None)
    status_path = policy.authorize(f".runtime/research/{job}-status.json")
    try:
        context = _verified_job_context(job, policy)
        if context is None:
            raise _Blocked(_REASONS[job])
        operation = {
            "history": _run_history,
            "screen": _run_screen,
            "predict": _run_predict,
            "settle": _run_settle,
        }[job]
        result = operation(root, policy, context)
        digest = _artifact_digest(result)
        payload: dict[str, Any] = {
            "format_version": 2,
            "job": job,
            "status": "SUCCESS",
            "promotion": "NEVER",
            "reason_code": None,
            "artifact_digest": digest,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "status_path": str(status_path),
        }
        _write_status(policy, status_path, payload)
        return 0
    except _Blocked as exc:
        payload = _blocked_payload(job, status_path, exc.reason_code)
        _write_status(policy, status_path, payload)
        return 1
    except Exception:
        # Do not persist provider exceptions, paths, tokens, or command text.
        payload = _blocked_payload(job, status_path, "COORDINATOR_FAILED", status="FAILED")
        _write_status(policy, status_path, payload)
        return 2


def run_forecast(repo_root: str | Path) -> int:
    """Compatibility entry point for old callers without a legacy process path."""

    return run_job("predict", repo_root)


def _blocked_payload(
    job: str, status_path: Path, reason: str, *, status: str = "BLOCKED"
) -> dict[str, Any]:
    return {
        "format_version": 2,
        "job": job,
        "status": status,
        "promotion": "NEVER",
        "reason_code": _safe_reason(reason),
        "artifact_digest": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "status_path": str(status_path),
    }


def _verified_job_context(
    job: str, policy: ProjectStoragePolicy
) -> dict[str, Any] | None:
    """Return only fixed, integrity-checked D-drive inputs for ``job``."""

    if os.environ.get("A_SHARE_QUANT_RESEARCH_WORKBENCH") != "1":
        return None
    if job == "history":
        if (
            os.environ.get("A_SHARE_QUANT_RESEARCH_NETWORK") != "1"
            or os.environ.get("A_SHARE_QUANT_RESEARCH_HISTORY_READY") != "1"
        ):
            return None
        return {"session_completed": True}

    contest = _read_frozen_contest(policy)
    if job == "screen":
        request = _read_control(policy, "screen-request.json")
        return {"request": request} if request is not None else None
    if contest is None:
        return None
    name = "predict-request.json" if job == "predict" else "settle-request.json"
    request = _read_control(policy, name)
    return {"contest": contest, "request": request} if request is not None else None


def _run_history(
    _root: Path, policy: ProjectStoragePolicy, _context: dict[str, Any]
) -> dict[str, Any]:
    """Perform one bounded incremental historical collection batch."""

    from a_share_quant.data.providers.baostock import BaoStockDataProvider
    from a_share_quant.runtime.historical_backfill import HistoricalBackfillCoordinator
    from a_share_quant.storage.research_data_store import ResearchDataStore

    provider = BaoStockDataProvider()
    try:
        coordinator = HistoricalBackfillCoordinator(ResearchDataStore(policy), provider)
        end = datetime.now(timezone.utc).date()
        # Never bootstrap an exchange-wide multi-year download from a workbench
        # opening.  The coordinator itself has a bounded 100-symbol default.
        result = coordinator.run(start=end - timedelta(days=14), end=end, limit=100)
        return {"kind": "history", "result": result.to_dict()}
    finally:
        close = getattr(provider, "close", None)
        if callable(close):
            close()


def _run_screen(
    _root: Path, policy: ProjectStoragePolicy, context: dict[str, Any]
) -> dict[str, Any]:
    """Run non-promotional historical engineering screening on verified inputs."""

    from a_share_quant.research.historical_screening import HistoricalEngineeringScreen
    from a_share_quant.research.research_snapshot import ResearchSnapshotBuilder
    from a_share_quant.storage.research_data_store import ResearchDataStore

    request = context.get("request")
    if not isinstance(request, dict):
        raise _Blocked("VERIFIED_DATASET_NOT_READY")
    store = ResearchDataStore(policy)
    try:
        universe = store.active_artifact(
            _request_text(request, "universe_dataset"),
            _request_text(request, "universe_key"),
        )
        features = store.active_artifact(
            _request_text(request, "feature_dataset"),
            _request_text(request, "feature_key"),
        )
        labels = store.active_artifact(
            _request_text(request, "label_dataset"),
            _request_text(request, "label_key"),
        )
        if not all(store.verify(item) for item in (universe, features, labels)):
            raise _Blocked("VERIFIED_DATASET_NOT_READY")
        snapshot = ResearchSnapshotBuilder(
            store,
            universe_artifact=universe,
            feature_artifact=features,
            label_artifact=labels,
        ).build(signal_cutoff=_request_date(request, "signal_cutoff"))
        candidates = request.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise _Blocked("SCREEN_CANDIDATES_NOT_READY")
        result = HistoricalEngineeringScreen().run(snapshot, candidates)
    except _Blocked:
        raise
    except (KeyError, TypeError, ValueError):
        raise _Blocked("VERIFIED_DATASET_NOT_READY") from None
    payload = result.to_dict()
    _write_evidence(policy, "screen-result.json", payload)
    return {"kind": "screen", "result": payload}


def _run_predict(
    _root: Path, policy: ProjectStoragePolicy, context: dict[str, Any]
) -> dict[str, Any]:
    """Append one precomputed, immutable future prediction to the frozen contest."""

    from a_share_quant.research.prospective_competition import (
        ProspectiveCompetition,
        ProspectivePrediction,
    )
    from a_share_quant.storage.prospective_ledger_store import ProspectiveLedgerStore

    contest = _contest_from_payload(context.get("contest"))
    request = context.get("request")
    if not isinstance(request, dict) or not isinstance(request.get("prediction"), dict):
        raise _Blocked("PREDICTION_REQUEST_NOT_READY")
    try:
        prediction = ProspectivePrediction(**request["prediction"])
        competition = ProspectiveCompetition(
            store=ProspectiveLedgerStore(policy=policy), contest=contest
        )
        appended = competition.append_prediction(prediction)
    except (TypeError, ValueError, KeyError):
        raise _Blocked("PREDICTION_REQUEST_NOT_READY") from None
    return {"kind": "predict", "prediction": appended.to_dict()}


def _run_settle(
    _root: Path, policy: ProjectStoragePolicy, context: dict[str, Any]
) -> dict[str, Any]:
    """Settle only supplied, fresh, complete, post-refresh outcome observations."""

    from a_share_quant.research.prospective_competition import (
        OutcomeObservation,
        ProspectiveCompetition,
    )
    from a_share_quant.storage.prospective_ledger_store import ProspectiveLedgerStore

    contest = _contest_from_payload(context.get("contest"))
    request = context.get("request")
    raw_outcomes = request.get("outcomes") if isinstance(request, dict) else None
    if not isinstance(raw_outcomes, list) or not raw_outcomes:
        raise _Blocked("REFRESHED_OUTCOME_NOT_READY")
    try:
        outcomes = tuple(
            OutcomeObservation(**item) for item in raw_outcomes if isinstance(item, dict)
        )
        if len(outcomes) != len(raw_outcomes):
            raise ValueError
        competition = ProspectiveCompetition(
            store=ProspectiveLedgerStore(policy=policy), contest=contest
        )
        results = competition.settle(outcomes)
    except (TypeError, ValueError, KeyError):
        raise _Blocked("REFRESHED_OUTCOME_NOT_READY") from None
    return {
        "kind": "settle",
        "settlements": [
            {
                "prediction_id": item.prediction_id,
                "matured": item.matured,
                "delay_reason": item.delay_reason,
                "idempotent": item.idempotent,
            }
            for item in results
        ],
    }


def _read_control(policy: ProjectStoragePolicy, name: str) -> dict[str, Any] | None:
    path = policy.authorize(f".runtime/research/{name}")
    try:
        policy.revalidate(path)
        raw = path.read_bytes()
        if not raw or len(raw) > _MAX_CONTROL_BYTES:
            return None
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            return None
        digest = payload.get("sha256")
        body = {key: value for key, value in payload.items() if key != "sha256"}
        encoded = _canonical_json(body)
        if not isinstance(digest, str) or digest != hashlib.sha256(encoded).hexdigest():
            return None
        return body
    except (OSError, UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _read_frozen_contest(policy: ProjectStoragePolicy) -> dict[str, Any] | None:
    payload = _read_control(policy, "prospective-contest.json")
    if payload is None:
        return None
    try:
        _contest_from_payload(payload)
    except (TypeError, ValueError):
        return None
    return payload


def _contest_from_payload(payload: Any):
    """Restore the immutable contest fields without trusting mutable counters."""

    from a_share_quant.research.prospective_competition import ProspectiveContest

    if not isinstance(payload, dict):
        raise ValueError("contest is required")
    required = (
        "model_id",
        "model_version",
        "config_hash",
        "training_snapshot_hash",
        "primary_metric",
        "tie_break",
        "contest_started_at",
    )
    if any(not payload.get(key) for key in required):
        raise ValueError("contest fields are missing")
    if (
        payload.get("format_version") != 2
        or payload.get("provisional_sessions") != 20
        or payload.get("provisional_matured_predictions") != 100
        or payload.get("approval_sessions") != 60
        or payload.get("approval_matured_predictions") != 200
        or payload.get("evidence_mode") != "PROSPECTIVE_ONLY"
        or payload.get("promotion") != "NEVER"
        or payload.get("status") != "PROSPECTIVE_COLLECTING"
        or payload.get("primary_metric") != "net_cost_return"
        or tuple(payload.get("tie_break", ()))
        != ("max_drawdown", "brier", "ece", "rank_ic", "turnover")
    ):
        raise ValueError("contest terms are not fixed")
    started = datetime.fromisoformat(str(payload["contest_started_at"]))
    now = max(datetime.now(timezone.utc), started.astimezone(timezone.utc))
    contest = ProspectiveContest(now=now)
    contest.start(
        model_id=str(payload["model_id"]),
        model_version=str(payload["model_version"]),
        config_hash=_sha256_text(payload["config_hash"], "config_hash"),
        training_snapshot_hash=_sha256_text(
            payload["training_snapshot_hash"], "training_snapshot_hash"
        ),
        primary_metric=str(payload["primary_metric"]),
        tie_break=tuple(str(item) for item in payload["tie_break"]),
        started_at=started,
    )
    return contest


def _request_text(request: dict[str, Any], name: str) -> str:
    value = str(request.get(name, "")).strip()
    if not value or len(value) > 128 or any(token in value for token in ("..", "/", "\\")):
        raise ValueError(f"{name} is invalid")
    return value


def _request_date(request: dict[str, Any], name: str) -> date:
    return date.fromisoformat(_request_text(request, name))


def _sha256_text(value: Any, name: str) -> str:
    text = str(value).strip().lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{name} must be sha256")
    return text


def _artifact_digest(value: Any) -> str:
    candidate = value.get("artifact_digest") if isinstance(value, dict) else None
    if isinstance(candidate, str):
        try:
            return _sha256_text(candidate, "artifact_digest")
        except ValueError:
            pass
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


def _safe_reason(value: str) -> str:
    normalized = str(value).strip().upper()
    if (
        not normalized
        or len(normalized) > 96
        or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for character in normalized)
    ):
        return "RESEARCH_WORKER_FAILED"
    return normalized


def _write_evidence(policy: ProjectStoragePolicy, name: str, body: dict[str, Any]) -> None:
    payload = {**body, "sha256": hashlib.sha256(_canonical_json(body)).hexdigest()}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    if len(encoded) > _MAX_OUTPUT_BYTES:
        raise _Blocked("RESEARCH_OUTPUT_TOO_LARGE")
    destination = policy.authorize(f".runtime/research/{name}")
    _atomic_write(policy, destination, encoded)


def _write_status(
    policy: ProjectStoragePolicy, status_path: Path, payload: dict[str, Any]
) -> None:
    _atomic_write(
        policy,
        status_path,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"),
    )


def _atomic_write(policy: ProjectStoragePolicy, destination: Path, payload: bytes) -> None:
    policy.revalidate(destination.parent)
    destination.parent.mkdir(parents=True, exist_ok=True)
    policy.revalidate(destination.parent)
    temporary = destination.with_name(f".{destination.name}.tmp")
    policy.revalidate(temporary)
    temporary.write_bytes(payload)
    policy.revalidate(temporary)
    policy.revalidate(destination)
    os.replace(temporary, destination)
    policy.revalidate(destination)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path.cwd().resolve(strict=True)
    return run_job(args.job, root, storage_policy=ProjectStoragePolicy(root))


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "parse_args", "run_forecast", "run_job"]
