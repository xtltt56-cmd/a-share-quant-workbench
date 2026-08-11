# Real-Time Data Smoke Test

> This report records an explicit provider smoke attempt. It is not investment evidence and contains no credentials.

- Status: **DEGRADED**
- Started: `2026-08-10T03:01:27.880288+00:00`
- Completed: `2026-08-10T03:02:58.287880+00:00`
- Active provider: `akshare`
- Latency: `90407.59` ms
- Data quality: `GOOD`
- Error: `SMOKE_REQUIREMENTS_NOT_MET`

## Capability discovery

| Provider | Authenticated | Market | Frequency | Permissions | Status |
|---|---:|---|---|---|---|
| rqdata | False | A | n/a | none | UNAVAILABLE |
| tushare | False | A | n/a | none | UNAVAILABLE |
| akshare | False | A | snapshot,1m | snapshot, 1m | READY |

## Snapshot schema and CSI300 identity

- Full market snapshot: True
- Required stock samples: True
- Quote schema: False
- Freshness: True
- CSI300 is an index: True

## Stock samples

| Symbol | Last | Volume | Source | Received | Age (s) |
|---|---:|---:|---|---|---:|
| 000001 | 11.35 | 55847600.0 | akshare | 2026-08-10T03:02:41.150651+00:00 | 17.137 |
| 000002 | 3.25 | 85946000.0 | akshare | 2026-08-10T03:02:41.150651+00:00 | 17.137 |

## Index samples

| Symbol | Last | Source | Received |
|---|---:|---|---|
| 000300 | 4682.07 | akshare | 2026-08-10T03:02:48.582344+00:00 |

## Provider switches

- None recorded.
