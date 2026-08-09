# Stage 3RT-E Real Market Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** Safely diagnose the current market-data networking failure and add a truthful, paper-only real-market verification path without fabricating a live-data result.

**Architecture:** Keep all provider calls behind the existing provider contract.  Add a redacting diagnostics module and an identifier adapter layer before changing any provider behavior; the default transport continues to respect Windows/Python proxy configuration and TLS validation.  Live validation, telemetry, dashboard state, official EOD signals, and realtime overlays stay independent so intraday data can never change the Stage 2 daily score.

**Tech Stack:** Python 3.12, AKShare, requests/aiohttp (optional diagnostic clients), stdlib Windows registry/subprocess/socket/SSL, YAML, pytest, DuckDB/Parquet, local stdlib HTTP dashboard.

---

### Task 1: Record the approved scope and baseline

**Files:**
- Create: \`docs/superpowers/plans/2026-08-09-stage3rt-real-market-verification.md\`
- Modify: \`README.md\`
- Test: full existing suite

- [x] **Step 1: Run the existing complete test suite**

Run: \`.\.venv\Scripts\python.exe -m pytest --cov=src/a_share_quant --cov-report=term -q\`

Expected: 175 passing tests and at least 88% total coverage before any Stage 3RT-E code.

- [x] **Step 2: Document the three evidence modes**

Add a README progress entry that distinguishes OFFLINE, REPLAY, and REAL MARKET evidence.  State that only the full final gate may use STAGE_3RT_REAL_MARKET_VALIDATED and that no broker/order path is in scope.

- [x] **Step 3: Commit the E1 checkpoint only after diagnostics tests pass**

Run:

~~~
git add README.md docs/superpowers/plans/2026-08-09-stage3rt-real-market-verification.md
git commit -m "docs: plan stage3 real market verification"
~~~

Expected: a commit that contains only documentation if this task is committed independently; otherwise include the E1 implementation commit below.

### Task 2: Add redacted Windows/Python network diagnostics

**Files:**
- Create: \`src/a_share_quant/data/realtime/diagnostics.py\`
- Create: \`tests/test_realtime_network_diagnostics.py\`
- Modify: \`scripts/quant_cli.py\`
- Modify: \`src/a_share_quant/data/realtime/__init__.py\`

- [x] **Step 1: Write failing tests for redaction and no-secret diagnostic payloads**

Create deterministic tests using injected environment, DNS resolver, command runner, registry reader, requests client, and aiohttp client.  The test fixture must include:

~~~
HTTP_PROXY=http://diagnostic-user:diagnostic-password@proxy.example:8080/path?token=x
HTTPS_PROXY=https://another-user:another-password@proxy.example:8443
~~~

Assert that serialized output contains proxy enabled/source/scheme/port and credential presence, but contains neither user name, password, token, query string, nor the raw URI.  Assert the report records DNS, requests, aiohttp, WinHTTP, and WinINET/system-proxy observations without an exception traceback.

- [x] **Step 2: Run the focused tests and confirm RED**

Run: \`.\.venv\Scripts\python.exe -m pytest tests/test_realtime_network_diagnostics.py -q\`

Expected: import failure because \`diagnostics.py\` and the CLI command do not yet exist.

- [x] **Step 3: Implement a fail-closed diagnostic value model**

Implement immutable result types for proxy state, endpoint reachability, and the aggregate report.  Use \`urllib.parse.urlsplit\` only to classify a proxy URI and serialize a safe shape such as:

~~~
{
  "configured": true,
  "scheme": "http",
  "port": 8080,
  "credentials_configured": true,
  "address": "<redacted>"
}
~~~

Use \`netsh winhttp show proxy\` through an argument list with a timeout; on Windows read only \`ProxyEnable\`, \`ProxyServer\`, and \`AutoConfigURL\` from the current-user Internet Settings registry and redact values.  Resolve public Eastmoney host names with \`socket.getaddrinfo\`; issue bounded HTTPS probes through requests and aiohttp with certificate verification enabled.  Save only status, elapsed time, error class, and a fixed target label.  Do not use \`verify=False\`, log raw exceptions, mutate proxy state, or call an AKShare endpoint in a diagnostic that lacks explicit network acknowledgement.

- [x] **Step 4: Add the safe operator command**

Extend the parser to support:

~~~
quant realtime diagnose-network
quant realtime diagnose-network --network
~~~

Without \`--network\`, output only local proxy/DNS configuration and label endpoint checks NOT_REQUESTED.  With it, run bounded endpoint checks.  JSON and Markdown output must use the serializer above and never print the process environment wholesale.

- [x] **Step 5: Run focused tests and static checks**

Run:

~~~
.\.venv\Scripts\python.exe -m pytest tests/test_realtime_network_diagnostics.py tests/test_realtime_scripts.py -q
.\.venv\Scripts\ruff.exe check src/a_share_quant/data/realtime/diagnostics.py scripts/quant_cli.py tests/test_realtime_network_diagnostics.py
~~~

Expected: all pass and test strings prove credentials cannot appear in console/report output.

### Task 3: Add explicit proxy policy and vendor-aware security identifiers

**Files:**
- Create: \`src/a_share_quant/contracts/identifiers.py\`
- Create: \`src/a_share_quant/data/realtime/identifiers.py\`
- Create: \`tests/test_realtime_identifiers.py\`
- Modify: \`src/a_share_quant/data/realtime/akshare.py\`
- Modify: \`src/a_share_quant/data/realtime/registry.py\`
- Modify: \`config/realtime.yaml\`
- Modify: \`.env.example\`

- [x] **Step 1: Write failing identifier and transport-policy tests**

Test that CSI300 is:

~~~
SecurityIdentifier(symbol="000300", exchange="SSE", asset_type=AssetType.INDEX)
~~~

and that vendor symbols are isolated: AKShare \`000300\`, Tushare \`000300.SH\`, RQData \`000300.XSHG\`, Qlib \`SH000300\`.  Test that it cannot be placed in an A-share tradable universe.  Test that AKShare defaults to \`use_system_proxy=True\`, and a false setting is not silently honored unless a diagnostic remediation authorization is present.

- [x] **Step 2: Run the focused tests and confirm RED**

Run: \`.\.venv\Scripts\python.exe -m pytest tests/test_realtime_identifiers.py -q\`

Expected: import failure for the explicit identifier and proxy-policy types.

- [x] **Step 3: Implement identifiers and a non-bypass policy**

Add \`AssetType\`, \`SecurityIdentifier\`, and \`VendorSymbolAdapter\`.  Make each adapter select the vendor spelling at the boundary rather than teaching core schemas provider-specific formats.  Add \`use_system_proxy: true\` to the AKShare configuration and constructor.  Default behavior must leave system/environment transport unchanged.  A false value must raise a clear configuration/status result unless a previously saved, redacted diagnostic decision explicitly identifies an environment-proxy fault; it must never disable TLS or mutate the global process proxy.

- [x] **Step 4: Run tests and regression suite**

Run:

~~~
.\.venv\Scripts\python.exe -m pytest tests/test_realtime_identifiers.py tests/test_realtime_providers.py tests/test_realtime_registry.py -q
.\.venv\Scripts\python.exe -m pytest --cov=src/a_share_quant --cov-report=term -q
~~~

Expected: existing provider/replay behavior remains compatible and total coverage does not fall below 88%.

- [x] **Step 5: Commit E1**

Run:

~~~
git add README.md .env.example config/realtime.yaml docs/superpowers/plans/2026-08-09-stage3rt-real-market-verification.md src/a_share_quant/contracts src/a_share_quant/data/realtime scripts/quant_cli.py tests
git commit -m "feat: add safe realtime network diagnostics"
~~~

Expected: a single E1 commit with no generated runtime data, credentials, or proxy values.

### Task 4: Make real-time quality depend on continuous live updates

**Files:**
- Create: \`src/a_share_quant/runtime/realtime_telemetry.py\`
- Create: \`tests/test_realtime_telemetry.py\`
- Modify: \`src/a_share_quant/data/realtime/validation.py\`
- Modify: \`src/a_share_quant/workbench/service.py\`
- Modify: \`config/realtime.yaml\`

- [x] **Step 1: Write failing tests for telemetry and continuous-update gates**

Use injected clock and synthetic, valid \`RealTimeQuote\` objects.  Assert:

~~~
first valid snapshot -> DEGRADED (continuous_updates=false)
second newer snapshot -> GOOD (continuous_updates=true)
stale/invalid/breaker-open -> not GOOD
~~~

Assert telemetry exposes provider, connect time, first/last quote time, quote/error/fallback/stale counts, average latency, p95 latency, and never contains a credential or raw exception.

- [x] **Step 2: Run the focused tests and confirm RED**

Run: \`.\.venv\Scripts\python.exe -m pytest tests/test_realtime_telemetry.py -q\`

Expected: import/test failure before the tracker is implemented.

- [x] **Step 3: Implement bounded telemetry and continuous evidence**

Track a bounded latency window and only define real-time GOOD as provider connected + schema-valid quote + freshness + two nondecreasing, distinct retrievals + no open circuit breaker.  Preserve replay as REPLAY evidence; never let it claim real-market continuous updates.  Expose sanitized telemetry through \`WorkbenchState\`.

- [x] **Step 4: Run focused tests**

Run:

~~~
.\.venv\Scripts\python.exe -m pytest tests/test_realtime_telemetry.py tests/test_realtime_quality.py tests/test_workbench_service.py -q
.\.venv\Scripts\ruff.exe check src/a_share_quant/runtime/realtime_telemetry.py src/a_share_quant/data/realtime/validation.py src/a_share_quant/workbench/service.py
~~~

Expected: passing tests demonstrate that a single successful HTTP response cannot be displayed as REALTIME GOOD.

### Task 5: Separate official EOD signals from realtime overlays and enhance the dashboard

**Files:**
- Create: \`src/a_share_quant/contracts/realtime_overlay.py\`
- Create: \`src/a_share_quant/storage/official_signal_store.py\`
- Create: \`src/a_share_quant/storage/realtime_overlay_store.py\`
- Create: \`tests/test_realtime_overlay.py\`
- Modify: \`src/a_share_quant/workbench/service.py\`
- Modify: \`src/a_share_quant/workbench/app.py\`
- Modify: \`tests/test_workbench_app.py\`

- [x] **Step 1: Write failing isolation and UI-state tests**

Create an \`OfficialSignal\` with a daily score, then add a \`RealtimeOverlay\` for the same symbol.  Assert the overlay includes last, vwap, volume ratio, intraday return, market relative strength, trigger state, risk state, and quality, but cannot mutate the official score.  Assert \`READY\` remains a monitoring state and no payload contains BUY, order, broker, or execution fields.  Assert the dashboard response and HTML display active source, PUBLIC/PROFESSIONAL source type, backend quote timestamp, data age, latency, quality, and fallback count with \`Cache-Control: no-store\`.

- [x] **Step 2: Run the focused tests and confirm RED**

Run: \`.\.venv\Scripts\python.exe -m pytest tests/test_realtime_overlay.py tests/test_workbench_app.py -q\`

Expected: missing overlay-store/service/dashboard fields.

- [x] **Step 3: Implement the two-store boundary and dashboard fields**

Use separate in-memory store objects for official daily candidates and intraday overlays.  Populate overlays only from quote/feature facts.  The dashboard merges copies for display; its JS must render the backend timestamp, not a browser-generated quote time, and fetches with \`cache: "no-store"\`.

- [x] **Step 4: Run tests and commit E2**

Run:

~~~
.\.venv\Scripts\python.exe -m pytest tests/test_realtime_overlay.py tests/test_workbench_service.py tests/test_workbench_app.py -q
.\.venv\Scripts\python.exe -m pytest --cov=src/a_share_quant --cov-report=term -q
git add src/a_share_quant/contracts src/a_share_quant/runtime src/a_share_quant/storage src/a_share_quant/workbench config/realtime.yaml tests README.md
git commit -m "feat: add live validation telemetry and overlays"
~~~

Expected: all tests pass, coverage remains at least 88%, dashboard remains loopback-only and paper-only.

### Task 6: Execute safe real-data evidence collection and produce truthful reports

**Files:**
- Create: \`scripts/run_network_diagnostics.py\`
- Create: \`scripts/run_real_market_validation.py\`
- Modify: \`scripts/run_realtime_smoke_test.py\`
- Modify: \`scripts/run_historical_dry_run.py\`
- Modify: \`reports/network_diagnostics.md\`
- Modify: \`reports/stage3_real_market_validation.md\`
- Modify: \`reports/historical_dry_run_readiness.md\`
- Modify: \`a-share-quant-threat-model.md\`
- Modify: \`README.md\`

- [ ] **Step 1: Write failing report-generation tests**

Test a report fixture that has an offline diagnostic, a replay run, and a failed live provider.  Assert it writes:

~~~
STAGE_3RT_OFFLINE_VALIDATED
NO_REALTIME_PROVIDER_AVAILABLE
OFFLINE / REPLAY / REAL MARKET
~~~

as appropriate, never the real-market validated state.  Test a real-success fixture verifies 000001, 000002, CSI300 as an INDEX, snapshot schema, update count, breadth completeness, and EOD status before it may state \`STAGE_3RT_REAL_MARKET_VALIDATED\`.

- [ ] **Step 2: Run focused tests and confirm RED**

Run: \`.\.venv\Scripts\python.exe -m pytest tests/test_real_market_validation.py -q\`

Expected: import failure before the report runner exists.

- [ ] **Step 3: Implement safe runners and execute diagnostics**

Require \`--network\` for every live endpoint probe.  Use real symbols 000001 and 000002, request CSI300 through the index adapter, and never use 000003/000004 as production smoke samples.  Collect repeated snapshots only when the market is actually in an OPEN session; otherwise record that continuous live validation is pending.  Query Tushare/RQData capability only when credentials exist, redacting all values and caching permission denials.  Do not change the Windows proxy, system time, installed AKShare package, or use unofficial patches.

- [ ] **Step 4: Run a production historical dry-run only when real data is available**

Select 10-20 real, non-ST, non-suspended, non-delisting equities dynamically and use CSI300 as an explicit non-tradable benchmark.  Make the pipeline fail closed when the historical universe/model artifacts remain fixtures, not a real result.  Do not report strategy performance as part of this validation.

- [ ] **Step 5: Update the threat model and final report**

Append a Stage 3RT-E addendum covering proxy/environment/system settings, diagnostic output, provider credential capability checks, local dashboard caching, PowerShell launcher/logs, and future provider patches.  The validation report must list the date, session, provider/source, samples, latency/freshness/quality, breadth/features, fallback, PIT isolation, EOD status, and all final-gate checks.

- [ ] **Step 6: Full verification, commit, and stop**

Run:

~~~
.\.venv\Scripts\python.exe -m pytest --cov=src/a_share_quant --cov-report=term -q
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m compileall -q src scripts
git diff --check
~~~

Commit:

~~~
git add README.md a-share-quant-threat-model.md reports scripts src tests config
git commit -m "docs: record stage3 real market validation"
~~~

Expected: report shows STAGE_3RT_REAL_MARKET_VALIDATED only if all real-session gates have concrete evidence.  On a non-trading day, proxy failure, absent provider rights, or missing real EOD completion, it must retain STAGE_3RT_OFFLINE_VALIDATED or NO_REALTIME_PROVIDER_AVAILABLE and stop without entering Stage 3C.

## Plan review

- Spec coverage: Tasks 2-3 cover network cause, proxy/TLS rules, AkShare version decision record, AKShare/Tushare/RQData capability boundaries, and identifier correctness.  Tasks 4-5 cover continuous-update quality, telemetry, breadth/feature display prerequisites, dashboard freshness, and official-signal/overlay isolation.  Task 6 covers real samples, real-session rules, fallback evidence, historical dry run, EOD, reports, desktop shortcut evidence, security review, and the final truthful gate.
- Deliberate non-scope: no broker SDK, account login, order API, automatic execution, system-proxy mutation, clock manipulation, unofficial AKShare proxy patch, or untested dependency upgrade.
- Constraint: 2026-08-09 is not an A-share trading day.  Code and safe network checks may be completed now; fresh continuous/live-session and EOD evidence must remain pending until an actual trading session.
