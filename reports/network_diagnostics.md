# Network Diagnostics

> This report is redacted by construction: it contains no proxy URI, user name, password, token, or raw exception payload.

- Generated at: 2026-08-09T08:51:29.665054+00:00
- Assessment: NETWORK_PROBES_NOT_REQUESTED
- TLS verification: ENABLED

## Proxy observations

| Layer | Configured | Scheme | Port | Credentials configured | Error type |
|---|---:|---|---:|---:|---|
| HTTP_PROXY | False | n/a | n/a | False | none |
| HTTPS_PROXY | False | n/a | n/a | False | none |
| ALL_PROXY | False | n/a | n/a | False | none |
| NO_PROXY | False | n/a | n/a | False | none |
| WinHTTP | False | n/a | n/a | False | none |
| Windows system proxy | True | n/a | 7892 | False | none |
- Local system-proxy listener: LISTENING
- Local system-proxy port: 7892

## Connectivity

| Check | Status | HTTP status | Elapsed ms | Error type |
|---|---|---:|---:|---|
| Eastmoney DNS | NOT_REQUESTED | n/a | n/a | none |
| HTTPS connectivity | NOT_REQUESTED | n/a | n/a | none |
| AKShare endpoint reachability | NOT_REQUESTED | n/a | n/a | none |
| AKShare individual quote endpoint | NOT_REQUESTED | n/a | n/a | none |
| Python requests connectivity | NOT_REQUESTED | n/a | n/a | none |
| aiohttp connectivity | NOT_REQUESTED | n/a | n/a | none |

## Runtime and browser boundary

- Browser: Windows Internet Settings were inspected; browser transport is not driven by this diagnostic. Python requests uses environment proxies and the aiohttp probe explicitly enables trust_env.
- Virtual environment: True
