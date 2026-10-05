"""Fixed, bounded daily-data child process; no arbitrary commands or credentials."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict
from datetime import date
from pathlib import Path

from a_share_quant.runtime.daily_refresh import (
    DailyRefreshSummary,
    DailySymbolResult,
    refresh_daily_data_if_due,
)
from a_share_quant.storage.atomic_json import read_checked_json, write_checked_json


def run_bounded_daily_refresh(
    root: Path,
    day: date,
    symbols: tuple[str, ...],
    *,
    stop_event: threading.Event,
    timeout_seconds: float = 120,
) -> DailyRefreshSummary:
    """Contain BaoStock's process-global socket/session and terminate on shutdown."""
    if timeout_seconds <= 0 or timeout_seconds > 600:
        raise ValueError("daily fetch deadline must be within 600 seconds")
    if stop_event.is_set():
        raise RuntimeError("DAILY_REFRESH_CANCELLED")
    root = root.resolve()
    temporary_root = root / ".runtime" / "temp" / "daily-refresh"
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=temporary_root, prefix="fetch-") as directory:
        result_path = Path(directory) / "result.json"
        env = os.environ.copy()
        env.update(
            {
                "TEMP": directory,
                "TMP": directory,
                "PYTHONPATH": str(root / "src") + os.pathsep + env.get("PYTHONPATH", ""),
            }
        )
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "a_share_quant.runtime.daily_refresh_worker",
                "--root",
                str(root),
                "--end-date",
                day.isoformat(),
                "--result",
                str(result_path),
                "--symbols",
                *symbols,
            ],
            cwd=root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.monotonic() + timeout_seconds
        try:
            while process.poll() is None:
                if stop_event.is_set():
                    raise RuntimeError("DAILY_REFRESH_CANCELLED")
                if time.monotonic() >= deadline:
                    raise TimeoutError("DAILY_REFRESH_DEADLINE")
                stop_event.wait(min(0.2, max(0, deadline - time.monotonic())))
            if process.returncode != 0:
                raise RuntimeError("DAILY_REFRESH_WORKER_FAILED")
            body = read_checked_json(result_path, maximum_bytes=262144)
            if body["format_version"] != 1 or body["session"] != day.isoformat():
                raise ValueError("daily worker result does not match request")
            if body.get("error_code"):
                if body["error_code"] == "PermissionError":
                    raise PermissionError("daily data destination denied")
                raise RuntimeError("DAILY_REFRESH_PROVIDER_FAILED")
            summary = body["summary"]
            rows = tuple(
                DailySymbolResult(
                    row["symbol"],
                    row["status"],
                    date.fromisoformat(row["latest_session"]) if row["latest_session"] else None,
                    row["history_rows"],
                )
                for row in summary.pop("symbol_results")
            )
            summary["errors"] = tuple(summary["errors"])
            return DailyRefreshSummary(**summary, symbol_results=rows)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--symbols", nargs="*", default=[])
    args = parser.parse_args()
    root = args.root.resolve()
    result_path = args.result.resolve()
    if not result_path.is_relative_to(root / ".runtime" / "temp" / "daily-refresh"):
        raise ValueError("worker output must stay inside governed temporary directory")
    body = {"format_version": 1, "session": args.end_date.isoformat()}
    try:
        summary = refresh_daily_data_if_due(
            root / "data",
            end_date=args.end_date,
            required_symbols=tuple(args.symbols),
        )
        values = asdict(summary)
        for row in values["symbol_results"]:
            if row["latest_session"] is not None:
                row["latest_session"] = row["latest_session"].isoformat()
        body["summary"] = values
    except Exception as exc:
        # Provider payloads, tokens and private file paths never enter status output.
        body["error_code"] = type(exc).__name__
    write_checked_json(result_path, body, maximum_bytes=262144)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
