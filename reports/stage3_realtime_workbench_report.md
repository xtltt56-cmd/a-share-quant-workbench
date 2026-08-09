# Stage 3RT Real-Time Quant Workbench Acceptance Report

Date: 2026-08-09  
Scope: local A-share real-time monitoring, descriptive intraday analysis, and
paper/signal observation only.

## Acceptance status

**ACCEPTED WITH DATA-SOURCE LIMITATION**

The local workbench, loopback dashboard, CLI, launcher scripts, provider
boundary, stale-data circuit breaker, EOD boundary, and security checks are
implemented. No broker SDK, account login, order API, or live-execution state
is present. `READY` is a monitor state only.

## Implemented path

```text
RQData -> Tushare -> AKShare public fallback
       -> normalized RealTimeQuote/MinuteBar
       -> freshness/timestamp circuit breaker
       -> RealTimeStore (provisional bars isolated)
       -> intraday features / breadth / monitor triggers
       -> localhost dashboard and paper-only state
       -> explicit EOD reconciliation -> PIT -> official daily signal
```

The daily model remains `DAILY`; `RuleBasedSignalProvider` rejects an
intraday invocation. Intraday triggers are returned as
`RealtimeMonitorSignal` and are never converted into `OfficialModelSignal` or
an order.

## Data-source evidence

- Provider priority is RQData, Tushare, then AKShare.
- The explicit smoke result is recorded in
  [`reports/realtime_data_smoke_test.md`](realtime_data_smoke_test.md).
- The current machine discovered AKShare as the public fallback. RQData and
  Tushare were unavailable without configured credentials/permissions.
- The AKShare network request was blocked by the configured proxy. The smoke
  report records only status, exception class, timestamps, and metadata; it
  does not claim real-market success.
- The historical dry-run remains `NOT_READY` because the correctly mapped
  `000300` benchmark and historical model artifacts are not both available.
  Fixture symbols and fixture model results are not substituted.

## Local operation

```powershell
# Create/update the Desktop shortcut; the .lnk remains a local user artifact.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\create_desktop_shortcut.ps1

# Start the local dashboard and explicitly allow provider polling.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_quant_workbench.ps1

# Offline UI smoke without external requests.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_quant_workbench.ps1 -Offline -Port 8876

# Stop the process owned by the launcher.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop_quant_workbench.ps1

# Inspect capabilities without provider endpoint calls.
.\.venv\Scripts\python.exe scripts\quant_cli.py status --json
```

The dashboard binds only to `http://127.0.0.1:<port>/`. The verified local
shortcut is `C:\Users\lenovo\Desktop\A股量化交易系统.lnk`; it is not inside
the repository and is not committed.

## Verification

- Full regression: **175 passed**.
- Coverage: **88%**.
- Ruff: passed.
- `pip check`: passed.
- `compileall`: passed.
- `git diff --check`: passed.
- PowerShell launcher parse: passed.
- Offline launcher smoke: `/api/health` returned HTTP 200; stop script removed
  the owned PID file.
- Dashboard security tests reject `0.0.0.0` and assert paper-only API state.
- Secret and broker-import scans found no project secret value or broker import;
  the only token-like test text is a deliberately non-secret placeholder used
  by an existing settings test.

## Known limitations and next boundary

1. Public AKShare availability depends on the current network/proxy and its
   upstream schema. The workbench reports failure/degraded data instead of
   fabricating quotes.
2. RQData/Tushare require separately configured credentials and permissions in
   `.env`; their absence is shown as unavailable and is not logged as a secret.
3. The dashboard does not present an official daily candidate until a valid
   daily model/report pipeline supplies one. It does not infer daily candidates
   from intraday states.
4. The Desktop shortcut is machine-local and must be recreated on another
   Windows account.
5. Stage 3RT ends here. No Stage 3C or real-money execution work is started by
   this acceptance.
