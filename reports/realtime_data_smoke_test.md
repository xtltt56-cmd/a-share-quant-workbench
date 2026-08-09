# Real-Time Data Smoke Test

> This report records an explicit provider smoke attempt. It is not investment evidence and contains no credentials.

- Status: **FAILED**
- Started: `2026-08-09T06:28:22.159273+00:00`
- Completed: `2026-08-09T06:28:49.729453+00:00`
- Active provider: `akshare`
- Latency: `n/a` ms
- Data quality: `FAILED`
- Error: `ProviderRequestError`

## Capability discovery

| Provider | Authenticated | Market | Frequency | Permissions | Status |
|---|---:|---|---|---|---|
| rqdata | False | A | n/a | none | UNAVAILABLE |
| tushare | False | A | n/a | none | UNAVAILABLE |
| akshare | False | A | snapshot,1m | snapshot, SanitizedValue | READY |

## Snapshot schema and CSI300 identity

- Full market snapshot: False
- Required stock samples: False
- Quote schema: False
- Freshness: False
- CSI300 is an index: False

## Stock samples

| Symbol | Last | Volume | Source | Received | Age (s) |
|---|---:|---:|---|---|---:|

## Index samples

| Symbol | Last | Source | Received |
|---|---:|---|---|

## Provider switches

- None recorded.
