"""Bounded research workers launched only by the local workbench."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
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
    """Run one internal stage with only project-owned output paths.

    The historical coordinator, screen, prediction ledger, and settlement
    ledger are intentionally invoked by their respective lifecycle owners in
    later stages. Until their verified inputs exist, this worker records a
    bounded blocked state rather than inventing data or promoting a model.
    """

    if job not in _JOBS:
        raise ValueError("research worker job is not allowlisted")
    root = Path(repo_root).resolve(strict=True)
    # Direct library tests may use an isolated temporary root. The real
    # process entry point supplies the strict D-drive policy below.
    policy = storage_policy or ProjectStoragePolicy(root, required_drive=None)
    status_path = policy.authorize(f".runtime/research/{job}-status.json")
    payload: dict[str, Any] = {
        "format_version": 1,
        "job": job,
        "status": "BLOCKED",
        "promotion": "NEVER",
        "reason_code": _REASONS[job],
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "status_path": str(status_path),
    }
    _write_status(policy, status_path, payload)
    return 1


def run_forecast(repo_root: str | Path) -> int:
    """Compatibility entry point for old callers without a legacy process path."""

    return run_job("predict", repo_root)


def _write_status(
    policy: ProjectStoragePolicy, status_path: Path, payload: dict[str, Any]
) -> None:
    policy.revalidate(status_path.parent)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = status_path.with_name(f".{status_path.name}.tmp")
    policy.revalidate(temporary)
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    policy.revalidate(status_path)
    temporary.replace(status_path)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path.cwd().resolve(strict=True)
    return run_job(args.job, root, storage_policy=ProjectStoragePolicy(root))


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "parse_args", "run_forecast", "run_job"]
