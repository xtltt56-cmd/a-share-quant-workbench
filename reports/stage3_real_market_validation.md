# Stage 3RT Real Market Validation

> Paper-only validation report. It contains status categories, aggregate telemetry, and redacted error classes only; it contains no credentials, proxy URI, or raw provider payload.

- Current status: STAGE_3RT_OFFLINE_VALIDATED
- Evidence mode: OFFLINE
- Started: 2026-08-09T08:51:37.630344+00:00
- Completed: 2026-08-09T08:51:37.746980+00:00
- Market session: NON_TRADING
- Calendar evidence: WEEKEND_NON_TRADING
- Active provider: none
- Active source: none

## Evidence modes

| Mode | Meaning |
|---|---|
| OFFLINE | No live quote was accepted as current market evidence. |
| REPLAY | Deterministic test data; never market evidence. |
| REAL MARKET | A provider was observed during an open session; promotion still requires every gate below. |

## Provider capability matrix

| Provider | Configured | Authenticated | Status | Snapshot | Historical daily | Historical minute | Live minute | Live tick |
|---|---:|---:|---|---|---|---|---|---|
| tushare | False | False | DISABLED | NOT_CONFIGURED | NOT_CONFIGURED | NOT_CONFIGURED | NOT_CONFIGURED | NOT_CONFIGURED |
| rqdata | False | False | NOT_CONFIGURED | NOT_CONFIGURED | NOT_CONFIGURED | NOT_CONFIGURED | NOT_CONFIGURED | NOT_CONFIGURED |
| akshare | True | False | NETWORK_NOT_REQUESTED | PUBLIC_SNAPSHOT | AVAILABLE_VIA_HISTORICAL_PROVIDER | PENDING_LIVE_PROBE | PENDING_LIVE_PROBE | PUBLIC_SNAPSHOT_ONLY |

## Snapshot and latency

- Snapshot quote count: 0
- Required stocks: none
- Missing stocks: none
- Snapshot schema: False
- Freshness: NOT_RUN
- Continuous updates: False
- CSI300 index identity: False
- Average latency (ms): None
- P95 latency (ms): None

## Explicit provider smoke attempt

- Status: FAILED
- Active provider: akshare
- Latency (ms): None
- Data quality: FAILED
- Error class: ProviderRequestError

## Market breadth, intraday features, historical and EOD

- Breadth status: NOT_RUN
- Intraday feature status: NOT_RUN
- Historical dry-run status: NOT_READY
- EOD status: PENDING_REAL_TRADING_DAY
- Security review: PASS_CODE_REVIEW_PENDING_REAL_MARKET

## Final gates

| Gate | Pass |
|---|---:|
| desktop_shortcut | False |
| dashboard | False |
| real_provider | False |
| snapshot_schema | False |
| csi300_index_identity | False |
| fresh_quotes | False |
| continuous_updates | False |
| market_breadth | False |
| intraday_features | False |
| pit_isolation | False |
| eod_finalization | False |
| security_review | False |

## Sanitized observations

- NetworkAcknowledgementRequired

## Promotion

- No promotion: this report does not establish a completed real-market gate.
