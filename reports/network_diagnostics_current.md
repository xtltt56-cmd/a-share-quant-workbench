# Network Diagnostics

> This report is redacted by construction: it contains no proxy URI, user name, password, token, or raw exception payload.

- Generated at: 2026-08-10T02:36:09.781960+00:00
- Assessment: EASTMONEY_ENDPOINT_UNAVAILABLE_OR_REJECTED
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
| Eastmoney DNS | PASS | n/a | 94.0 | none |
| HTTPS connectivity | PASS | 200 | 500.0 | none |
| AKShare endpoint reachability | FAILED | n/a | 312.0 | ProxyError |
| AKShare individual quote endpoint | FAILED | n/a | 344.0 | ProxyError |
| Python requests connectivity | PASS | 200 | 281.0 | none |
| aiohttp connectivity | PASS | 200 | 5219.0 | none |

## Runtime and browser boundary

- Browser: Windows Internet Settings were inspected; browser transport is not driven by this diagnostic. Python requests uses environment proxies and the aiohttp probe explicitly enables trust_env.
- Virtual environment: True
