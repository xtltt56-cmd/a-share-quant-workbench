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
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from a_share_quant.research.daily_candidates import latest_complete_signal_date
from a_share_quant.storage.project_storage import ProjectStoragePolicy

_JOBS = ("history", "screen", "predict", "settle")
_REASONS = {
    "history": "HISTORY_INPUT_NOT_READY",
    "screen": "VERIFIED_DATASET_NOT_READY",
    "predict": "FUTURE_CONTEST_NOT_READY",
    "settle": "REFRESHED_OUTCOME_NOT_READY",
}
_MAX_INTEGRITY_ARTIFACT_BYTES = 1_048_576
_MAX_OUTPUT_BYTES = 1_048_576


class _Blocked(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class _Partial(_Blocked):
    """A bounded batch persisted some evidence but cannot claim success."""


class _Failed(_Blocked):
    """A verified operation ran but its entire bounded batch failed."""


@dataclass(frozen=True)
class _DerivedScreenSnapshot:
    """Minimal immutable view assembled from verified return artifacts only."""

    features: Any
    labels: Any
    signal_cutoff: date
    canonical_sha256: str


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
    instance_id = _worker_instance_id(job)
    status_path = _status_path(policy, job, instance_id)
    try:
        if os.environ.get("A_SHARE_QUANT_RESEARCH_WORKBENCH") == "1" and instance_id is None:
            raise _Blocked("WORKER_INSTANCE_INVALID")
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
        artifact_relpath, digest = _write_instance_evidence(
            policy, job, instance_id, result
        )
        payload: dict[str, Any] = {
            "format_version": 3,
            "job": job,
            "job_id": instance_id,
            "status": "SUCCESS",
            "promotion": "NEVER",
            "reason_code": None,
            "artifact_digest": digest,
            "artifact_relpath": artifact_relpath,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "status_path": str(status_path),
        }
        _write_status(policy, status_path, payload)
        return 0
    except _Partial as exc:
        payload = _blocked_payload(
            job,
            status_path,
            exc.reason_code,
            status="PARTIAL",
            instance_id=instance_id,
        )
        _write_status(policy, status_path, payload)
        return 1
    except _Failed as exc:
        payload = _blocked_payload(
            job,
            status_path,
            exc.reason_code,
            status="FAILED",
            instance_id=instance_id,
        )
        _write_status(policy, status_path, payload)
        return 2
    except _Blocked as exc:
        payload = _blocked_payload(job, status_path, exc.reason_code, instance_id=instance_id)
        _write_status(policy, status_path, payload)
        return 1
    except Exception:
        # Do not persist provider exceptions, paths, tokens, or command text.
        payload = _blocked_payload(
            job,
            status_path,
            "COORDINATOR_FAILED",
            status="FAILED",
            instance_id=instance_id,
        )
        _write_status(policy, status_path, payload)
        return 2


def run_forecast(repo_root: str | Path) -> int:
    """Compatibility entry point for old callers without a legacy process path."""

    return run_job("predict", repo_root)


def _blocked_payload(
    job: str,
    status_path: Path,
    reason: str,
    *,
    status: str = "BLOCKED",
    instance_id: str | None = None,
) -> dict[str, Any]:
    return {
        "format_version": 3,
        "job": job,
        "job_id": instance_id,
        "status": status,
        "promotion": "NEVER",
        "reason_code": _safe_reason(reason),
        "artifact_digest": None,
        "artifact_relpath": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "status_path": str(status_path),
    }


def _worker_instance_id(job: str) -> str | None:
    """Read only the supervisor-provided opaque ID, never a user path."""

    value = os.environ.get("A_SHARE_QUANT_RESEARCH_JOB_ID")
    if value is None:
        return None
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 128
        or (normalized != job and not normalized.startswith(f"{job}-"))
        or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789_-"
            for character in normalized
        )
    ):
        return None
    return normalized


def _status_path(
    policy: ProjectStoragePolicy, job: str, instance_id: str | None
) -> Path:
    """Use per-instance status for children, preserving direct test compatibility."""

    if instance_id is not None:
        return policy.authorize(f".runtime/research/status/{instance_id}.json")
    return policy.authorize(f".runtime/research/{job}-status.json")


def _write_instance_evidence(
    policy: ProjectStoragePolicy,
    job: str,
    instance_id: str | None,
    result: dict[str, Any],
) -> tuple[str | None, str]:
    """Persist the exact bytes whose digest a supervisor will verify."""

    if instance_id is None:
        # Direct library callers have no supervising instance.  They retain the
        # legacy status compatibility contract but cannot claim an owned child
        # success to a supervisor.
        return None, _artifact_digest(result)
    body = {
        "format_version": 1,
        "job": job,
        "job_id": instance_id,
        "result": result,
    }
    encoded = _canonical_json(body)
    if len(encoded) > _MAX_OUTPUT_BYTES:
        raise _Blocked("RESEARCH_OUTPUT_TOO_LARGE")
    relpath = f"evidence/{instance_id}.json"
    destination = policy.authorize(f".runtime/research/{relpath}")
    _atomic_write(policy, destination, encoded)
    return relpath, hashlib.sha256(encoded).hexdigest()


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

    if job == "screen":
        return _derive_screen_context(policy)

    contest = _read_frozen_contest(policy)
    if contest is None:
        return None
    if job == "predict":
        return _derive_predict_context(policy, contest)
    return _derive_settle_context(policy, contest)


def _run_history(
    root: Path, policy: ProjectStoragePolicy, _context: dict[str, Any]
) -> dict[str, Any]:
    """Perform one bounded incremental historical collection batch."""

    from a_share_quant.data.providers.baostock import BaoStockDataProvider
    from a_share_quant.runtime.historical_backfill import HistoricalBackfillCoordinator
    from a_share_quant.storage.research_data_store import ResearchDataStore

    provider = BaoStockDataProvider()
    try:
        coordinator = HistoricalBackfillCoordinator(
            ResearchDataStore(policy), provider, max_request_days=366
        )
        start = _configured_history_start(root, policy)
        end = _current_time().astimezone(ZoneInfo("Asia/Shanghai")).date()
        # The coordinator itself limits each lifecycle batch to 100 symbols;
        # coverage advances from the configured maturity start over repeated
        # workbench sessions instead of silently using a two-week window.
        result = coordinator.run(start=start, end=end, limit=100)
        failures = getattr(result, "failures", {})
        if failures:
            if int(getattr(result, "rows_written", 0)) > 0:
                raise _Partial("HISTORY_PARTIAL")
            raise _Failed("HISTORY_ALL_FAILED")
        result_payload = result.to_dict()
        if int(getattr(result, "rows_written", 0)) > 0 or int(
            getattr(result, "symbols_updated", 0)
        ) > 0:
            coordinator_store = getattr(coordinator, "store", None)
            manifest_digest = _verified_file_digest(
                policy, getattr(coordinator_store, "manifest_path", None)
            )
            checkpoint_digest = _verified_file_digest(
                policy, getattr(coordinator, "checkpoint_path", None)
            )
            if manifest_digest is None or checkpoint_digest is None:
                raise _Failed("HISTORY_EVIDENCE_NOT_READY")
            result_payload.update(
                {
                    "history_manifest_digest": manifest_digest,
                    "history_checkpoint_digest": checkpoint_digest,
                }
            )
        return {"kind": "history", "result": result_payload}
    finally:
        close = getattr(provider, "close", None)
        if callable(close):
            close()


def _verified_file_digest(
    policy: ProjectStoragePolicy, path: Any
) -> str | None:
    """Return a bounded digest for a durable project-owned evidence file."""

    if not isinstance(path, (str, os.PathLike, Path)):
        return None
    try:
        candidate = policy.authorize(Path(path))
        policy.revalidate(candidate)
        raw = candidate.read_bytes()
        if not raw or len(raw) > _MAX_INTEGRITY_ARTIFACT_BYTES:
            return None
        policy.revalidate(candidate)
        if candidate.stat().st_size != len(raw):
            return None
        return hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError, TypeError):
        return None


def _configured_history_start(root: Path, policy: ProjectStoragePolicy) -> date:
    """Read the fail-closed historical maturity start from the local config."""

    import yaml

    config_path = policy.authorize(root / "config" / "research_maturity.yaml")
    try:
        policy.revalidate(config_path)
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        raw_history = payload.get("history") if isinstance(payload, dict) else None
        raw_start = raw_history.get("start_date") if isinstance(raw_history, dict) else None
        start = date.fromisoformat(str(raw_start))
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise _Blocked("HISTORY_CONFIG_INVALID") from exc
    if start > _current_time().astimezone(ZoneInfo("Asia/Shanghai")).date():
        raise _Blocked("HISTORY_CONFIG_INVALID")
    return start


def _run_screen(
    _root: Path, policy: ProjectStoragePolicy, context: dict[str, Any]
) -> dict[str, Any]:
    """Run non-promotional historical engineering screening on verified inputs."""

    from a_share_quant.research.historical_screening import HistoricalEngineeringScreen
    snapshot = context.get("snapshot")
    candidates = context.get("candidates")
    if snapshot is None or not isinstance(candidates, tuple) or not candidates:
        raise _Blocked("VERIFIED_DATASET_NOT_READY")
    try:
        result = HistoricalEngineeringScreen().run(snapshot, candidates)
    except _Blocked:
        raise
    except (KeyError, TypeError, ValueError):
        raise _Blocked("VERIFIED_DATASET_NOT_READY") from None
    payload = result.to_dict()
    return {
        "kind": "screen",
        "result": payload,
        "evidence_mode": "NON_PROMOTIONAL_ENGINEERING",
        "promotion": "NEVER",
    }


def _run_predict(
    _root: Path, policy: ProjectStoragePolicy, context: dict[str, Any]
) -> dict[str, Any]:
    """Append a future-only prediction derived from a verified daily signal."""

    from a_share_quant.research.prospective_competition import (
        ProspectiveCompetition,
        ProspectivePrediction,
    )
    from a_share_quant.storage.prospective_ledger_store import ProspectiveLedgerStore

    contest = _contest_from_payload(context.get("contest"))
    signals = context.get("signals")
    session_calendar = context.get("session_calendar")
    session_calendar_digest = context.get("session_calendar_digest")
    model_bundle_digest = context.get("model_bundle_digest")
    if model_bundle_digest is None and contest.model_bundle_digest is not None:
        model_bundle_digest = contest.model_bundle_digest
    if (
        not isinstance(signals, tuple)
        or not signals
        or not isinstance(session_calendar, tuple)
        or not session_calendar
        or not _is_sha256(session_calendar_digest)
        or (
            contest.model_bundle_digest is not None
            and not _is_sha256(model_bundle_digest)
        )
    ):
        raise _Blocked("VERIFIED_DAILY_SIGNAL_NOT_READY")
    now = _current_time()
    # Prediction identity is derived from the immutable daily evidence, not
    # from the supervisor process/job id.  A restart or a changed research
    # manifest must not register the same daily forecast twice.
    cycle_key = _prediction_cycle_key(signals, str(context["daily_signal_digest"]))
    try:
        competition = ProspectiveCompetition(
            store=ProspectiveLedgerStore(policy=policy),
            contest=contest,
            now=now,
            session_calendar=session_calendar,
        )
        appended = tuple(
            competition.append_prediction(
                ProspectivePrediction(
                    model_id=contest.model_id,
                    model_version=contest.model_version,
                    config_hash=contest.config_hash,
                    training_snapshot_hash=contest.training_snapshot_hash,
                    symbol=signal.symbol,
                    name=signal.name,
                    # The source generation time is immutable across a replay;
                    # using the process clock here would make an otherwise
                    # identical cycle conflict with its ledger id.
                    prediction_at=signal.generated_at,
                    as_of=signal.data_cutoff,
                    horizon=5,
                    score=float(signal.normalized_score),
                    # The daily rule emits a rank score, not a calibrated
                    # probability.  Keep probability absent so Brier/ECE
                    # cannot be mistaken for calibration evidence.
                    probability=None,
                    probability_calibrated=False,
                    prediction_id=(
                        _stable_prediction_id(
                            contest,
                            cycle_key,
                            signal,
                            str(context["daily_signal_digest"]),
                            str(session_calendar_digest),
                        )
                        if cycle_key
                        else ""
                    ),
                    source_input_digest=str(context["daily_signal_digest"]),
                    session_calendar_digest=str(session_calendar_digest),
                    cycle_key=cycle_key,
                    model_bundle_digest=str(model_bundle_digest or ""),
                    source_artifact_path=str(context.get("source_artifact_path") or ""),
                    maturity_date=_calendar_maturity_date(
                        session_calendar, signal.data_cutoff, horizon=5
                    ),
                    guidance_price_bands={
                        "reference": (float(signal.reference_price), float(signal.reference_price)),
                        "invalidation": (
                            float(signal.invalidation_price),
                            float(signal.invalidation_price),
                        ),
                    },
                )
            )
            for signal in signals
        )
    except (TypeError, ValueError, KeyError):
        raise _Blocked("VERIFIED_DAILY_SIGNAL_NOT_READY") from None
    return {
        "kind": "predict",
        "predictions": [item.to_dict() for item in appended],
        "input_integrity_digest": context.get("daily_signal_digest"),
        "session_calendar_digest": session_calendar_digest,
        "cycle_key": cycle_key or None,
        "probability_semantics": "NORMALIZED_SCORE_NOT_CALIBRATED",
    }


def _run_settle(
    _root: Path, policy: ProjectStoragePolicy, context: dict[str, Any]
) -> dict[str, Any]:
    """Settle only fresh actual prices derived after a verified refresh."""

    from a_share_quant.research.prospective_competition import (
        ProspectiveCompetition,
    )
    from a_share_quant.storage.prospective_ledger_store import ProspectiveLedgerStore

    outcomes = context.get("outcomes")
    if not isinstance(outcomes, tuple) or not outcomes:
        raise _Blocked("REFRESHED_OUTCOME_NOT_READY")
    try:
        competition = ProspectiveCompetition(
            store=ProspectiveLedgerStore(policy=policy),
            now=_current_time(),
        )
        results = competition.settle(outcomes)
    except (TypeError, ValueError, KeyError):
        raise _Blocked("REFRESHED_OUTCOME_NOT_READY") from None
    ledger_digest = _ledger_integrity_digest(policy)
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
        "ledger_integrity_digest": ledger_digest,
    }


def _derive_screen_context(policy: ProjectStoragePolicy) -> dict[str, Any] | None:
    """Build an engineering snapshot directly from verified return artifacts."""

    import pandas as pd

    from a_share_quant.research.historical_screening import HistoricalCandidate
    from a_share_quant.runtime.historical_backfill import HistoricalBackfillCoordinator
    from a_share_quant.storage.research_data_store import ResearchDataset, ResearchDataStore

    try:
        store = ResearchDataStore(policy)
        coverage = HistoricalBackfillCoordinator(store, None).coverage()
        if coverage.symbol_count < 30 or coverage.session_count < 1750:
            raise _Blocked("VERIFIED_DATASET_NOT_READY")
        feature_frames: list[Any] = []
        label_frames: list[Any] = []
        artifacts: list[dict[str, str]] = []
        for symbol in coverage.symbols:
            artifact = store.active_artifact(ResearchDataset.RESEARCH_RETURNS, symbol)
            if not store.verify(artifact):
                raise _Blocked("VERIFIED_DATASET_NOT_READY")
            policy.revalidate(artifact.path)
            frame = pd.read_parquet(artifact.path)
            features, labels = _derived_screen_frames(frame, symbol)
            if not features.empty:
                feature_frames.append(features)
            if not labels.empty:
                label_frames.append(labels)
            artifacts.append(
                {
                    "symbol": str(symbol),
                    "data_version": artifact.data_version,
                    "sha256": artifact.sha256,
                }
            )
        if not feature_frames or not label_frames:
            raise _Blocked("VERIFIED_DATASET_NOT_READY")
        features = pd.concat(feature_frames, ignore_index=True)
        labels = pd.concat(label_frames, ignore_index=True)
        if features.empty or labels.empty:
            raise _Blocked("VERIFIED_DATASET_NOT_READY")
        cutoff = max(pd.to_datetime(features["date"]).dt.date)
        digest = hashlib.sha256(
            _canonical_json(
                {
                    "kind": "verified-return-screen-v1",
                    "coverage": coverage.to_dict(),
                    "artifacts": artifacts,
                }
            )
        ).hexdigest()
        snapshot = _DerivedScreenSnapshot(
            features=features,
            labels=labels,
            signal_cutoff=cutoff,
            canonical_sha256=digest,
        )
        return {
            "snapshot": snapshot,
            "candidates": (
                HistoricalCandidate(
                    "derived-momentum-5",
                    model_family="rule-baseline",
                    score_column="feature_momentum_5",
                ),
                HistoricalCandidate(
                    "derived-momentum-20",
                    model_family="rule-baseline",
                    score_column="feature_momentum_20",
                ),
            ),
            "source_integrity_digest": digest,
            "evidence_mode": "NON_PROMOTIONAL_ENGINEERING",
        }
    except _Blocked:
        raise
    except Exception:
        # Do not make an unverifiable local artifact look like a screened
        # candidate; the supervisor records only this bounded reason.
        raise _Blocked("VERIFIED_DATASET_NOT_READY") from None


def _derived_screen_frames(frame: Any, symbol: str) -> tuple[Any, Any]:
    """Construct lagged features and matured labels without request-file input."""

    import pandas as pd

    required = {"date", "close", "research_usable"}
    if not hasattr(frame, "columns") or not required.issubset(frame.columns):
        raise ValueError("verified return schema is incomplete")
    working = frame.copy(deep=True)
    working["symbol"] = str(symbol).zfill(6)
    working["date"] = pd.to_datetime(working["date"], errors="coerce")
    working["close"] = pd.to_numeric(working["close"], errors="coerce")
    usable = working["research_usable"].astype(bool)
    working = working.loc[
        usable & working["date"].notna() & working["close"].gt(0)
    ].sort_values("date", kind="stable")
    if working.empty or working["date"].duplicated().any():
        raise ValueError("verified return sessions are invalid")
    daily_return = working["close"].pct_change()
    # Each feature is lagged by one observed session, preventing the same-day
    # close from entering its own signal.  Labels are kept separately and have
    # their exact outcome maturity date.
    working["feature_momentum_5"] = daily_return.rolling(5).sum().shift(1)
    working["feature_momentum_20"] = daily_return.rolling(20).sum().shift(1)
    working["feature_volatility_20"] = daily_return.rolling(20).std().shift(1)
    working["forward_return_5"] = working["close"].shift(-5) / working["close"] - 1.0
    working["maturity_date"] = working["date"].shift(-5)
    feature_columns = [
        "symbol",
        "date",
        # Keep the unadjusted execution price alongside lagged features so
        # historical screening can apply the same conservative cost model as
        # the execution path.  It is not used as a same-day feature because
        # the scorer still receives only explicitly selected columns.
        "close",
        "feature_momentum_5",
        "feature_momentum_20",
        "feature_volatility_20",
    ]
    features = working.loc[:, feature_columns].copy()
    features["available_at"] = features["date"]
    features = features.dropna(
        subset=["feature_momentum_5", "feature_momentum_20", "feature_volatility_20"]
    )
    labels = working.loc[
        :, ["symbol", "date", "forward_return_5", "maturity_date"]
    ].dropna(subset=["forward_return_5", "maturity_date"])
    return features, labels


def _derive_predict_context(
    policy: ProjectStoragePolicy, contest: dict[str, Any]
) -> dict[str, Any] | None:
    """Use current verified daily model output; never accept a prediction file."""

    signals, digest, source_artifact_path = _verified_official_signals(policy)
    frozen = _contest_from_payload(contest)
    now = _current_time()
    local_signal_date = latest_complete_signal_date(now)
    selected = tuple(
        signal
        for signal in signals
        if signal.strategy_version == frozen.model_id
        and signal.model_version == frozen.model_version
        and signal.signal_date == local_signal_date
        and signal.data_cutoff == signal.signal_date
        and signal.generated_at <= now
        and signal.reference_price is not None
        and signal.invalidation_price is not None
    )
    if not selected:
        raise _Blocked("VERIFIED_DAILY_SIGNAL_NOT_READY")
    frozen_signal_digest = str(contest.get("official_signal_digest", ""))
    if digest != frozen_signal_digest:
        _require_forward_signal_rollover(policy, contest, selected)
    if frozen.model_bundle_digest is not None and any(
        signal.model_bundle_digest != frozen.model_bundle_digest for signal in selected
    ):
        raise _Blocked("MODEL_BUNDLE_MISMATCH")
    session_calendar, session_calendar_digest = _verified_session_calendar(
        policy, selected[0].data_cutoff
    )
    return {
        "contest": contest,
        "signals": selected,
        "daily_signal_digest": digest,
        "source_artifact_path": source_artifact_path,
        "session_calendar": session_calendar,
        "session_calendar_digest": session_calendar_digest,
        "model_bundle_digest": frozen.model_bundle_digest,
    }


def _require_forward_signal_rollover(
    policy: ProjectStoragePolicy,
    contest: dict[str, Any],
    signals: tuple[Any, ...],
) -> None:
    """Allow a later daily artifact without weakening same-cycle binding.

    ``official_signal_digest`` freezes the first contest input, while a
    prospective contest must also collect later daily sessions.  A digest
    change is therefore accepted only for a strictly later signal date than
    an already recorded prediction.  On the first rollover (before any
    prediction exists), the new date must differ from the contest's local
    start date; a same-day rewrite remains blocked.
    """

    from a_share_quant.storage.prospective_ledger_store import ProspectiveLedgerStore

    current_dates = {
        signal.signal_date
        for signal in signals
        if isinstance(getattr(signal, "signal_date", None), date)
    }
    if len(current_dates) != 1:
        raise _Blocked("OFFICIAL_SIGNAL_DIGEST_MISMATCH")
    current_date = next(iter(current_dates))
    ledger = ProspectiveLedgerStore(policy=policy)
    prior_dates = {
        prediction.as_of
        for prediction in ledger.predictions()
        if (
            prediction.model_id == str(contest.get("model_id", ""))
            and prediction.model_version == str(contest.get("model_version", ""))
            and prediction.config_hash == str(contest.get("config_hash", ""))
            and prediction.training_snapshot_hash
            == str(contest.get("training_snapshot_hash", ""))
        )
    }
    if prior_dates:
        if current_date <= max(prior_dates):
            raise _Blocked("OFFICIAL_SIGNAL_DIGEST_MISMATCH")
        return
    try:
        started = datetime.fromisoformat(str(contest["contest_started_at"]))
        if started.tzinfo is None or started.utcoffset() is None:
            raise ValueError
        start_local_date = started.astimezone(ZoneInfo("Asia/Shanghai")).date()
    except (KeyError, TypeError, ValueError):
        raise _Blocked("OFFICIAL_SIGNAL_DIGEST_MISMATCH") from None
    if current_date == start_local_date:
        raise _Blocked("OFFICIAL_SIGNAL_DIGEST_MISMATCH")


def _verified_session_calendar(
    policy: ProjectStoragePolicy, as_of: date, *, horizon: int = 20
) -> tuple[tuple[date, ...], str]:
    """Build and archive a bounded, holiday-aware A-share session calendar."""

    from a_share_quant.market.trading_calendar import AShareTradingCalendar

    if horizon < 5 or horizon > 256:
        raise ValueError("calendar horizon is outside the bounded range")
    try:
        calendar = AShareTradingCalendar()
        sessions: list[date] = []
        current = as_of
        for _ in range(horizon):
            current = calendar.next_session(current)
            sessions.append(current)
    except (TypeError, ValueError) as exc:
        raise _Blocked("TRADING_CALENDAR_NOT_READY") from exc
    body = {
        "format_version": 1,
        "source": "A_SHARE_EXCHANGE_CALENDAR_2026_V1",
        "as_of": as_of.isoformat(),
        "sessions": [item.isoformat() for item in sessions],
    }
    encoded = _canonical_json(body)
    digest = hashlib.sha256(encoded).hexdigest()
    destination = policy.authorize(f".runtime/research/calendars/{digest}.json")
    try:
        if destination.exists():
            policy.revalidate(destination)
            if destination.read_bytes() != encoded:
                raise ValueError("session calendar artifact changed")
        else:
            _atomic_write(policy, destination, encoded)
            policy.revalidate(destination)
    except (OSError, ValueError) as exc:
        raise _Blocked("TRADING_CALENDAR_NOT_READY") from exc
    return tuple(sessions), digest


def _calendar_maturity_date(
    session_calendar: tuple[date, ...], as_of: date, *, horizon: int
) -> date:
    future = sorted({item for item in session_calendar if isinstance(item, date) and item > as_of})
    if len(future) < horizon:
        raise ValueError("session calendar is insufficient for horizon")
    return future[horizon - 1]


def _stable_prediction_id(
    contest: Any,
    cycle_key: str,
    signal: Any,
    source_input_digest: str,
    session_calendar_digest: str,
) -> str:
    """Derive an idempotent identity independent of process timestamps."""

    payload = {
        "cycle_key": cycle_key,
        "model_id": contest.model_id,
        "model_version": contest.model_version,
        "config_hash": contest.config_hash,
        "training_snapshot_hash": contest.training_snapshot_hash,
        "symbol": signal.symbol,
        "signal_date": signal.signal_date,
        "data_cutoff": signal.data_cutoff,
        "horizon": 5,
        "score": signal.normalized_score,
        "reference_price": signal.reference_price,
        "invalidation_price": signal.invalidation_price,
        "source_input_digest": source_input_digest,
        "session_calendar_digest": session_calendar_digest,
        "model_bundle_digest": getattr(contest, "model_bundle_digest", None),
    }
    return f"prediction-{hashlib.sha256(_canonical_json(payload)).hexdigest()[:32]}"


def _prediction_cycle_key(signals: tuple[Any, ...], source_input_digest: str) -> str:
    """Build one stable cycle identity for one verified daily signal artifact."""

    if not signals or not _is_sha256(source_input_digest):
        raise _Blocked("VERIFIED_DAILY_SIGNAL_NOT_READY")
    signal_dates = {
        signal.signal_date
        for signal in signals
        if isinstance(getattr(signal, "signal_date", None), date)
    }
    if len(signal_dates) != 1:
        raise _Blocked("VERIFIED_DAILY_SIGNAL_NOT_READY")
    signal_date = next(iter(signal_dates))
    return f"predict-{signal_date:%Y%m%d}-{source_input_digest[:24]}"


def _derive_settle_context(
    policy: ProjectStoragePolicy, contest: dict[str, Any]
) -> dict[str, Any] | None:
    """Derive actual outcomes from verified refreshed returns, never a JSON request."""

    from a_share_quant.storage.prospective_ledger_store import ProspectiveLedgerStore

    # Validate the active descriptor, but do not use it to filter old rows.
    # Archived contests must keep settling after a new model becomes active.
    _contest_from_payload(contest)
    now = _current_time()
    ledger = ProspectiveLedgerStore(policy=policy)
    settled = {item.prediction_id for item in ledger.settlements()}
    due = tuple(
        prediction
        for prediction in ledger.predictions()
        if prediction.id not in settled
        and prediction.maturity_date <= now.date()
    )
    if not due:
        raise _Blocked("NO_MATURED_PREDICTIONS")
    outcomes = tuple(_derived_outcome(policy, prediction, now) for prediction in due)
    return {
        "contest": contest,
        "outcomes": outcomes,
        "input_integrity_digest": _ledger_integrity_digest(policy),
    }


def _derived_outcome(policy: ProjectStoragePolicy, prediction: Any, now: datetime) -> Any:
    """Read one mature actual price from verified post-prediction evidence.

    The small canonical daily lake is checked first because it is the normal
    production refresh path.  A verified research-return artifact remains a
    fallback when the lake does not contain the maturity session.  Both paths
    are schema-checked and hashed before their digest is appended to the
    immutable prospective ledger.
    """

    try:
        return _derived_market_lake_outcome(policy, prediction, now)
    except _Blocked:
        return _derived_research_outcome(policy, prediction, now)


def _derived_research_outcome(
    policy: ProjectStoragePolicy, prediction: Any, now: datetime
) -> Any:
    """Fallback to a verified immutable research-return artifact."""

    import pandas as pd

    from a_share_quant.storage.research_data_store import ResearchDataset, ResearchDataStore

    try:
        store = ResearchDataStore(policy)
        try:
            artifact = store.active_artifact(
                ResearchDataset.RESEARCH_RETURNS, prediction.symbol
            )
        except KeyError:
            raise _Blocked("REFRESHED_OUTCOME_NOT_READY") from None
        if not store.verify(artifact):
            raise _Blocked("REFRESHED_OUTCOME_NOT_READY")
        if artifact.created_at < prediction.prediction_at:
            raise _Blocked("REFRESHED_OUTCOME_NOT_READY")
        policy.revalidate(artifact.path)
        frame = pd.read_parquet(artifact.path)
        required = {"date", "close", "research_usable", "trade_status"}
        if not required.issubset(frame.columns):
            raise _Blocked("REFRESHED_OUTCOME_NOT_READY")
        rows = frame.loc[
            pd.to_datetime(frame["date"], errors="coerce").dt.date
            == prediction.maturity_date
        ].copy()
        if len(rows) != 1:
            raise _Blocked("REFRESHED_OUTCOME_NOT_READY")
        row = rows.iloc[0]
        price = float(row["close"])
        reference = float(prediction.guidance_price_bands["reference"][0])
        if (
            not price > 0
            or not reference > 0
            or not bool(row["research_usable"])
            or str(row["trade_status"]).strip() != "1"
        ):
            raise _Blocked("REFRESHED_OUTCOME_NOT_READY")
        return _outcome_observation(
            prediction,
            now,
            price=price,
            data_version=artifact.data_version,
            data_sha256=artifact.sha256,
        )
    except _Blocked:
        raise
    except Exception:
        raise _Blocked("REFRESHED_OUTCOME_NOT_READY") from None


def _derived_market_lake_outcome(
    policy: ProjectStoragePolicy, prediction: Any, now: datetime
) -> Any:
    """Derive one outcome from a stable, canonical daily Parquet snapshot."""

    import pandas as pd

    path = policy.authorize(f"data/lake/daily_bars/{prediction.symbol}.parquet")
    try:
        policy.revalidate(path)
        raw = path.read_bytes()
        if not raw or len(raw) > _MAX_INTEGRITY_ARTIFACT_BYTES:
            raise ValueError("daily outcome artifact is outside the bounded size")
        digest = hashlib.sha256(raw).hexdigest()
        frame = pd.read_parquet(path)
        policy.revalidate(path)
        if (
            path.stat().st_size != len(raw)
            or hashlib.sha256(path.read_bytes()).hexdigest() != digest
        ):
            raise ValueError("daily outcome artifact changed while reading")
        required = {
            "symbol",
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "source",
            "fetched_at",
            "data_version",
        }
        if not required.issubset(frame.columns):
            raise ValueError("daily outcome schema is incomplete")
        parsed_dates = pd.to_datetime(frame["date"], errors="raise").dt.date
        rows = frame.loc[parsed_dates == prediction.maturity_date].copy()
        if len(rows) != 1:
            raise ValueError("daily outcome session is not uniquely available")
        row = rows.iloc[0]
        if str(row["symbol"]).strip() != prediction.symbol:
            raise ValueError("daily outcome symbol does not match")
        source = str(row["source"]).strip().casefold()
        data_version = str(row["data_version"]).strip()
        if (
            not source
            or source in {"fixture", "replay", "synthetic", "test", "test-data"}
            or not data_version
        ):
            raise ValueError("daily outcome provenance is not usable")
        fetched_at = pd.to_datetime(row["fetched_at"], utc=True, errors="raise").to_pydatetime()
        if fetched_at > now or fetched_at.date() < prediction.maturity_date:
            raise ValueError("daily outcome fetch time is invalid")
        prices = tuple(float(row[field]) for field in ("open", "high", "low", "close"))
        open_price, high, low, close = prices
        if (
            any(not value > 0 for value in prices)
            or high < max(open_price, close)
            or low > min(open_price, close)
            or float(row["volume"]) < 0
            or float(row["amount"]) < 0
        ):
            raise ValueError("daily outcome values are invalid")
        # BaoStock supplies exchange daily percentage changes.  Across a
        # corporate action the compounded exchange return and the raw close
        # ratio diverge; accepting the latter would score a mechanical
        # ex-right adjustment as model performance.  Require all horizon
        # sessions and agreement within rounding tolerance before asserting
        # ``corporate_action_ok``.
        horizon_rows = frame.loc[
            (parsed_dates > prediction.as_of)
            & (parsed_dates <= prediction.maturity_date)
        ].sort_values("date")
        changes = pd.to_numeric(horizon_rows["change_pct"], errors="coerce")
        if (
            len(horizon_rows) != int(prediction.horizon)
            or changes.isna().any()
            or (changes <= -100.0).any()
            or (changes > 100.0).any()
        ):
            raise ValueError("daily outcome adjustment evidence is incomplete")
        compounded_return = float((1.0 + changes / 100.0).prod() - 1.0)
        reference = float(prediction.guidance_price_bands["reference"][0])
        raw_return = close / reference - 1.0
        if abs(compounded_return - raw_return) > 0.001:
            raise ValueError("daily outcome contains a corporate action conflict")
        return _outcome_observation(
            prediction,
            now,
            price=close,
            data_version=f"market-lake:{data_version}",
            data_sha256=digest,
            realized_return=compounded_return,
        )
    except _Blocked:
        raise
    except Exception:
        raise _Blocked("REFRESHED_OUTCOME_NOT_READY") from None


def _outcome_observation(
    prediction: Any,
    now: datetime,
    *,
    price: float,
    data_version: str,
    data_sha256: str,
    realized_return: float | None = None,
) -> Any:
    from a_share_quant.research.prospective_competition import OutcomeObservation

    reference = float(prediction.guidance_price_bands["reference"][0])
    if not price > 0 or not reference > 0:
        raise _Blocked("REFRESHED_OUTCOME_NOT_READY")
    return OutcomeObservation(
        prediction_id=prediction.id,
        symbol=prediction.symbol,
        maturity_date=prediction.maturity_date,
        outcome_at=now,
        realized_price=price,
        realized_return=(
            price / reference - 1.0
            if realized_return is None
            else float(realized_return)
        ),
        data_version=data_version,
        data_sha256=data_sha256,
        status="OK",
        fresh=True,
        complete=True,
        session_aligned=True,
        corporate_action_ok=True,
    )


def _verified_official_signals(
    policy: ProjectStoragePolicy,
) -> tuple[tuple[Any, ...], str, str]:
    """Load a fixed, integrity-checked daily artifact without creating it."""

    from a_share_quant.storage.official_signal_store import OfficialSignalStore

    path = policy.authorize(".runtime/signals/official-daily.json")
    try:
        policy.revalidate(path)
        raw = path.read_bytes()
        if not raw or len(raw) > _MAX_INTEGRITY_ARTIFACT_BYTES:
            raise ValueError
        digest = hashlib.sha256(raw).hexdigest()
        signals = OfficialSignalStore(path=path).latest()
        policy.revalidate(path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest or not signals:
            raise ValueError
        archive_relpath = f".runtime/research/signal-archive/{digest}.json"
        archive = policy.authorize(archive_relpath)
        if archive.exists():
            policy.revalidate(archive)
            if archive.read_bytes() != raw:
                raise ValueError
        else:
            _atomic_write(policy, archive, raw)
            policy.revalidate(archive)
        return signals, digest, archive_relpath
    except (OSError, TypeError, ValueError):
        raise _Blocked("VERIFIED_DAILY_SIGNAL_NOT_READY") from None


def _ledger_integrity_digest(policy: ProjectStoragePolicy) -> str:
    path = policy.authorize(".runtime/research/prospective/predictions.jsonl")
    try:
        policy.revalidate(path)
        raw = path.read_bytes()
    except OSError:
        raise _Blocked("REFRESHED_OUTCOME_NOT_READY") from None
    return hashlib.sha256(raw).hexdigest()


def _current_time() -> datetime:
    return datetime.now(timezone.utc)


def _read_integrity_artifact(policy: ProjectStoragePolicy, name: str) -> dict[str, Any] | None:
    """Read a project-owned SHA-256 integrity artifact, not a signature."""

    path = policy.authorize(f".runtime/research/{name}")
    try:
        policy.revalidate(path)
        raw = path.read_bytes()
        if not raw or len(raw) > _MAX_INTEGRITY_ARTIFACT_BYTES:
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
    payload = _read_integrity_artifact(policy, "prospective-contest.json")
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
    required = frozenset(
        {
            "format_version",
            "contest_started_at",
            "model_id",
            "model_version",
            "config_hash",
            "training_snapshot_hash",
            "official_signal_digest",
            "primary_metric",
            "tie_break",
            "provisional_sessions",
            "provisional_matured_predictions",
            "approval_sessions",
            "approval_matured_predictions",
            "evidence_mode",
            "status",
            "promotion",
        }
    )
    optional = frozenset({"model_bundle_digest"})
    if not required.issubset(payload) or not set(payload).issubset(required | optional):
        raise ValueError("frozen contest fields are invalid")
    required_values = (
        "model_id",
        "model_version",
        "config_hash",
        "training_snapshot_hash",
        "primary_metric",
        "tie_break",
        "contest_started_at",
    )
    if any(not payload.get(key) for key in required_values):
        raise ValueError("contest fields are missing")
    if (
        payload.get("format_version") != 3
        or _sha256_text(payload.get("official_signal_digest"), "official_signal_digest")
        != payload.get("official_signal_digest")
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
    bundle_digest = payload.get("model_bundle_digest")
    if bundle_digest is not None:
        bundle_digest = _sha256_text(bundle_digest, "model_bundle_digest")
    contest.start(
        model_id=str(payload["model_id"]),
        model_version=str(payload["model_version"]),
        config_hash=_sha256_text(payload["config_hash"], "config_hash"),
        training_snapshot_hash=_sha256_text(
            payload["training_snapshot_hash"], "training_snapshot_hash"
        ),
        model_bundle_digest=bundle_digest,
        primary_metric=str(payload["primary_metric"]),
        tie_break=tuple(str(item) for item in payload["tie_break"]),
        started_at=started,
    )
    return contest


def _sha256_text(value: Any, name: str) -> str:
    text = str(value).strip().lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{name} must be sha256")
    return text


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value.lower()
    )


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
