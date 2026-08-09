# Stage 3RT Real-Time Quant Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with verification checkpoints.

**Goal:** Build a Windows-local real-market monitoring workbench with explicit provider, freshness, PIT, and paper-only safety boundaries.

**Architecture:** Keep the existing daily/PIT pipeline intact and add an isolated real-time provider/store/runtime layer. Provider adapters normalize into immutable contracts; feature and dashboard layers consume only validated snapshots. Launchers start a local-only process and never expose a broker or live execution path.

**Tech Stack:** Python 3.12, pandas, Pydantic/dataclasses, PyYAML, DuckDB/Parquet, optional AKShare/Tushare/RQData, optional Streamlit, PowerShell.

---

## Stage 3RT-A: Contracts, Provider Boundary, Store, and Quality Gates

Status: completed in the working tree; commit is gated on the full regression
and repository-quality checks below.

Files to create or modify:

- `src/a_share_quant/contracts/realtime.py`
- `src/a_share_quant/data/realtime/base.py`
- `src/a_share_quant/data/realtime/registry.py`
- `src/a_share_quant/data/realtime/validation.py`
- `src/a_share_quant/storage/realtime_store.py`
- `config/realtime.yaml`, `.env.example`, package `__init__.py` files
- `tests/test_realtime_contracts.py`, `tests/test_realtime_registry.py`,
  `tests/test_realtime_store.py`, `tests/test_realtime_quality.py`

Test-first behaviors: quote/bar schemas accept nullable provider fields but
reject invalid prices, volume, OHLC, symbols, future timestamps, and backwards
timestamps; registry capability discovery skips unavailable providers; failover
records every switch; stale data cannot produce a usable snapshot; provisional
bars are not historical; EOD finalization is explicit and idempotent.

Commit: `feat: add realtime provider and store contracts`.

## Stage 3RT-B: Providers, Replay, Smoke Test, and Historical Dry Run

Status: implementation and explicit network attempts completed. The smoke
report is `reports/realtime_data_smoke_test.md`; historical readiness is
`reports/historical_dry_run_readiness.md`. The benchmark and historical model
artifact gate remains blocked by the local proxy and fixture-only baseline.

Files to create or modify:

- `src/a_share_quant/data/realtime/akshare.py`
- `src/a_share_quant/data/realtime/tushare.py`
- `src/a_share_quant/data/realtime/rqdata.py`
- `src/a_share_quant/data/realtime/replay.py`
- `scripts/run_realtime_smoke_test.py`, `scripts/run_historical_dry_run.py`
- provider tests and `reports/realtime_data_smoke_test.md`

Use lazy optional imports. Run AKShare capability checks before endpoint calls;
Tushare/RQData permission failures are cached and skipped. The smoke command
is explicit, bounded, and redacts credentials. Historical dry-run uses symbols
that exist in the production universe and the mapped CSI 300 benchmark.

Commit: `feat: add realtime providers and replay path`.

## Stage 3RT-C: Intraday Analysis, Runtime, and EOD Boundaries

Status: completed in the working tree; the full regression and repository
quality checks below are the commit gate.

Files to create or modify:

- `src/a_share_quant/features/intraday.py`
- `src/a_share_quant/analysis/breadth.py`
- `src/a_share_quant/signals/realtime.py`
- `src/a_share_quant/runtime/scheduler.py`
- `src/a_share_quant/runtime/eod.py`
- `tests/test_intraday_features.py`, `tests/test_market_breadth.py`,
  `tests/test_realtime_triggers.py`, `tests/test_realtime_scheduler.py`,
  `tests/test_realtime_eod.py`, `tests/test_model_frequency_guard.py`

Implement transparent VWAP/return/volume-ratio/momentum/volatility and market
breadth. The scheduler uses an injectable calendar and clock, distinguishes
pre-market/open/lunch/closed/non-trading-day, retries with backoff, and does
not poll in closed periods. Trigger output is a monitor state only. Daily model
frequency metadata rejects intraday calls. EOD finalization validates and
reconciles before preparing the next daily signal.

Commit: `feat: add realtime analysis and safe scheduler`.

## Stage 3RT-D: Dashboard, CLI, Launchers, Security Review, and Acceptance

Files to create or modify:

- `src/a_share_quant/workbench/app.py`
- `src/a_share_quant/workbench/service.py`
- `scripts/start_quant_workbench.ps1`, `scripts/stop_quant_workbench.ps1`,
  `scripts/create_desktop_shortcut.ps1`
- `scripts/quant_cli.py` or the existing CLI entrypoint
- `reports/stage3_realtime_workbench_report.md`, `README.md`,
  `STAGE3/DEPENDENCIES.md`, and `a-share-quant-threat-model.md`
- dashboard/launcher tests

The dashboard binds to `127.0.0.1`, remains usable when providers fail, shows
provider/source/quality/freshness and separates official daily candidates from
intraday monitoring. The launcher owns a lock, logs to `logs/`, opens the
browser, and the shortcut calls the stable launcher rather than a temporary
Python path. A real `.lnk` is created only as a local user artifact.

Run the full suite, coverage, Ruff, pip check, compileall, sanitized smoke
test, and security scan. Update the report with actual availability and
limitations, then commit: `feat: add local realtime quant workbench`.

## Verification gate for every stage

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m compileall -q src scripts
git diff --check
```

No stage may add a broker import, live execution state, or `0.0.0.0` listener.
Fixture and replay outputs must remain explicitly non-investment evidence.
