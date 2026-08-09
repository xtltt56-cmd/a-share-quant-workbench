# Stage 3RT Real-Time Quant Workbench Design

**Status:** approved for implementation by the Stage 3RT request dated 2026-08-09

## Goal

Add a Windows-local real-market monitoring workbench to the existing A-share
research system. It may consume real market data, compute transparent intraday
monitoring features, and emit paper/signal-monitoring states. It must not
contain broker login, account credentials, live order routing, or an automatic
live-trading path.

## Architecture

The existing daily `MarketDataProvider` and PIT `MarketDataStore` remain
unchanged. Stage 3RT adds a separate `RealTimeDataProvider` protocol and a
provider registry with capability discovery and explicit failover. The flow is:

```text
Provider -> normalization/validation -> RealTimeStore
         -> freshness/circuit breaker -> intraday features/breadth/triggers
         -> dashboard and paper/signal monitor
```

`HistoricalStore` and `RealTimeStore` are separate objects. Provisional minute
bars never enter the historical store; only an explicit EOD finalization step
can reconcile and archive validated data. Daily models remain `DAILY` models
and reject intraday invocation. Intraday analysis is descriptive and rule
based until an explicit intraday model exists.

## Components

- `contracts/realtime.py`: immutable quote/bar/health/capability/quality types.
- `data/realtime/`: provider protocol, registry, failover, validators, and
  replay provider. Optional provider SDK imports stay inside adapters.
- `storage/realtime_store.py`: bounded in-memory provisional cache with
  explicit finalization into the existing historical store.
- `features/intraday.py`, `analysis/breadth.py`, `signals/realtime.py`: simple
  explainable metrics and `WAIT/WATCH/READY/OVERHEATED/RISK/STALE_DATA` states.
- `runtime/scheduler.py`, `runtime/eod.py`: market-calendar-aware polling,
  retry/backoff and EOD sequencing.
- `workbench/app.py`: local dashboard bound only to `127.0.0.1`.
- `scripts/start_quant_workbench.ps1`, `stop_quant_workbench.ps1`, and
  `create_desktop_shortcut.ps1`: stable Windows launcher and shortcut creator.

## Data and safety rules

Every quote carries exchange and receive timestamps, source, quality flag and
staleness. Invalid prices, negative volume, impossible OHLC, future or
backwards timestamps, and symbol mismatches are quarantined. Stale or failed
data changes the runtime state to `DATA_STALE`/`DATA_UNAVAILABLE`; no new
`READY` state is emitted from stale data. Provider switches record source,
destination, reason, and timestamp.

RQData and Tushare are capability-detected only when credentials and the
provider's permission response prove availability. AKShare is an explicitly
labelled public fallback. Secrets are loaded from `.env`, never logged or
written to reports/artifacts. The dashboard is local-only by default.

## Testing and evidence

Offline tests use deterministic fake and replay providers. Network smoke tests
are explicit operator commands and write only sanitized metadata to
`reports/realtime_data_smoke_test.md`. Historical dry-run data uses production
symbols and a correctly mapped benchmark; fixture symbols are not silently
reused. The Stage 3RT report is not investment evidence and records unavailable
providers, latency, data quality, and all limitations.

