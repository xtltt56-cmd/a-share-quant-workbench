"""Safe, redacted diagnostics for Windows real-time market-data networking."""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit

_PROXY_ENV_NAMES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
_EASTMONEY_HOME_URL = "https://www.eastmoney.com/"
_AKSHARE_EASTMONEY_URL = (
    "https://82.push2.eastmoney.com/api/qt/clist/get?"
    "pn=1&pz=100&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2&"
    "fid=f12&fs=m%3A0%20t%3A6%2Cm%3A0%20t%3A80%2Cm%3A1%20t%3A2%2Cm%3A1%20t%3A23%2C"
    "m%3A0%20t%3A81%20s%3A2048&fields=f1%2Cf2%2Cf3%2Cf4%2Cf5%2Cf6%2Cf7%2Cf8%2Cf9%2C"
    "f10%2Cf12%2Cf13%2Cf14%2Cf15%2Cf16%2Cf17%2Cf18%2Cf20%2Cf21%2Cf23%2Cf24%2Cf25%2C"
    "f22%2Cf11%2Cf62%2Cf128%2Cf136%2Cf115%2Cf152"
)
_AKSHARE_QUOTE_URL = "https://push2.eastmoney.com/api/qt/stock/get"
_AKSHARE_QUOTE_PARAMS = {
    "fltt": "2",
    "invt": "2",
    "fields": (
        "f120,f121,f122,f174,f175,f59,f163,f43,f57,f58,f169,f170,f46,f44,f51,"
        "f168,f47,f164,f116,f60,f45,f52,f50,f48,f167,f117,f71,f161,f49,f530,"
        "f135,f136,f137,f138,f139,f141,f142,f144,f145,f147,f148,f140,f143,f146,"
        "f149,f55,f62,f162,f92,f173,f104,f105,f84,f85,f183,f184,f185,f186,f187,"
        "f188,f189,f190,f191,f192,f107,f111,f86,f177,f78,f110,f262,f263,f264,"
        "f267,f268,f255,f256,f257,f258,f127,f199,f128,f198,f259,f260,f261,f171,"
        "f277,f278,f279,f288,f152,f250,f251,f252,f253,f254,f269,f270,f271,f272,"
        "f273,f274,f275,f276,f265,f266,f289,f290,f286,f285,f292,f293,f294,f295"
    ),
    "secid": "0.000001",
}
_AKSHARE_QUOTE_REQUEST_URL = f"{_AKSHARE_QUOTE_URL}?{urlencode(_AKSHARE_QUOTE_PARAMS)}"
_EASTMONEY_DNS_HOST = "push2.eastmoney.com"


@dataclass(frozen=True)
class ProxyObservation:
    """A proxy observation that deliberately omits host names and credentials."""

    source: str
    configured: bool | None
    scheme: str | None = None
    port: int | None = None
    credentials_configured: bool = False
    address: str | None = None
    entry_count: int | None = None
    error_type: str | None = None

    @classmethod
    def from_value(cls, *, source: str, value: object) -> ProxyObservation:
        text = str(value or "").strip()
        if not text:
            return cls(source=source, configured=False)
        if source == "NO_PROXY":
            entries = tuple(item for item in text.split(",") if item.strip())
            return cls(
                source=source,
                configured=True,
                address="<redacted>",
                entry_count=len(entries),
            )
        parsed = urlsplit(text if "://" in text else f"//{text}")
        try:
            port = parsed.port
        except ValueError:
            port = None
        return cls(
            source=source,
            configured=True,
            scheme=parsed.scheme or None,
            port=port,
            credentials_configured=bool(parsed.username or parsed.password or "@" in text),
            address="<redacted>",
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "configured": self.configured,
            "scheme": self.scheme,
            "port": self.port,
            "credentials_configured": self.credentials_configured,
            "address": self.address,
            "entry_count": self.entry_count,
            "error_type": self.error_type,
        }


@dataclass(frozen=True)
class EndpointProbe:
    label: str
    status: str
    elapsed_ms: float | None = None
    status_code: int | None = None
    error_type: str | None = None
    address_count: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "status": self.status,
            "elapsed_ms": self.elapsed_ms,
            "status_code": self.status_code,
            "error_type": self.error_type,
            "address_count": self.address_count,
        }


@dataclass(frozen=True)
class BrowserNetworkObservation:
    status: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"status": self.status, "detail": self.detail}


@dataclass(frozen=True)
class ProxyListenerObservation:
    status: str
    port: int | None = None
    error_type: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "port": self.port,
            "error_type": self.error_type,
        }


@dataclass(frozen=True)
class NetworkDiagnostics:
    generated_at: datetime
    environment_proxy: dict[str, ProxyObservation]
    winhttp_proxy: ProxyObservation
    system_proxy: ProxyObservation
    system_proxy_listener: ProxyListenerObservation
    dns: EndpointProbe
    https_connectivity: EndpointProbe
    akshare_endpoint_reachability: EndpointProbe
    akshare_quote_endpoint_reachability: EndpointProbe
    python_requests_connectivity: EndpointProbe
    aiohttp_connectivity: EndpointProbe
    browser_networking: BrowserNetworkObservation
    runtime_context: dict[str, object]
    assessment: str

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "environment_proxy": {
                name: observation.to_dict()
                for name, observation in self.environment_proxy.items()
            },
            "winhttp_proxy": self.winhttp_proxy.to_dict(),
            "system_proxy": self.system_proxy.to_dict(),
            "system_proxy_listener": self.system_proxy_listener.to_dict(),
            "dns": self.dns.to_dict(),
            "https_connectivity": self.https_connectivity.to_dict(),
            "akshare_endpoint_reachability": self.akshare_endpoint_reachability.to_dict(),
            "akshare_quote_endpoint_reachability": (
                self.akshare_quote_endpoint_reachability.to_dict()
            ),
            "python_requests_connectivity": self.python_requests_connectivity.to_dict(),
            "aiohttp_connectivity": self.aiohttp_connectivity.to_dict(),
            "browser_networking": self.browser_networking.to_dict(),
            "runtime_context": dict(self.runtime_context),
            "assessment": self.assessment,
        }


CommandRunner = Callable[[tuple[str, ...], float], subprocess.CompletedProcess[str]]
DnsResolver = Callable[[str], Sequence[str]]
RequestsGet = Callable[..., int]
AsyncProbe = Callable[..., int]
SystemProxyReader = Callable[[], Mapping[str, object]]
ListenerProbe = Callable[[int], bool]


def collect_network_diagnostics(
    *,
    allow_network: bool,
    environ: Mapping[str, str] | None = None,
    command_runner: CommandRunner | None = None,
    system_proxy_reader: SystemProxyReader | None = None,
    listener_probe: ListenerProbe | None = None,
    dns_resolver: DnsResolver | None = None,
    requests_get: RequestsGet | None = None,
    aiohttp_probe: AsyncProbe | None = None,
    clock: Callable[[], datetime] | None = None,
    timeout_seconds: float = 5.0,
) -> NetworkDiagnostics:
    """Inspect proxy settings and optionally run bounded TLS-verified probes.

    When allow_network is false, no DNS, requests, aiohttp, or provider endpoint
    check is invoked. External failures are represented by an exception class only.
    """

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    now = clock or (lambda: datetime.now(timezone.utc))
    environment = environ if environ is not None else os.environ
    command_runner = command_runner or _run_command
    system_proxy_reader = system_proxy_reader or read_windows_system_proxy
    listener_probe = listener_probe or is_local_listener
    dns_resolver = dns_resolver or resolve_dns
    requests_get = requests_get or _requests_get
    aiohttp_probe = aiohttp_probe or _aiohttp_probe

    environment_proxy = {
        name: ProxyObservation.from_value(
            source=name,
            value=_get_environment_value(environment, name),
        )
        for name in _PROXY_ENV_NAMES
    }
    winhttp_proxy = inspect_winhttp_proxy(command_runner, timeout_seconds)
    system_proxy, system_proxy_values = _read_system_proxy_observation(system_proxy_reader)
    system_proxy_listener = inspect_system_proxy_listener(
        system_proxy_values,
        listener_probe,
    )
    browser_networking = BrowserNetworkObservation(
        status="WININET_INSPECTED_BROWSER_NOT_AUTOMATED",
        detail=(
            "Windows Internet Settings were inspected; browser transport is not driven by "
            "this diagnostic. Python requests uses environment proxies and the aiohttp "
            "probe explicitly enables trust_env."
        ),
    )
    runtime_context = {
        "virtual_environment": sys.prefix != getattr(sys, "base_prefix", sys.prefix),
        "requests_respects_environment_proxy": True,
        "aiohttp_probe_uses_environment_proxy": True,
        "tls_verification": "ENABLED",
    }

    if not allow_network:
        dns = _not_requested("Eastmoney DNS")
        https = _not_requested("HTTPS connectivity")
        endpoint = _not_requested("AKShare endpoint reachability")
        quote_endpoint = _not_requested("AKShare individual quote endpoint")
        requests_probe = _not_requested("Python requests connectivity")
        aiohttp_result = _not_requested("aiohttp connectivity")
    else:
        dns = _probe_dns(dns_resolver)
        https = _probe_http(
            "HTTPS connectivity",
            lambda: requests_get(
                _EASTMONEY_HOME_URL,
                timeout_seconds=timeout_seconds,
                verify=True,
            ),
        )
        endpoint = _probe_http(
            "AKShare endpoint reachability",
            lambda: requests_get(
                _AKSHARE_EASTMONEY_URL,
                timeout_seconds=timeout_seconds,
                verify=True,
            ),
        )
        quote_endpoint = _probe_http(
            "AKShare individual quote endpoint",
            lambda: requests_get(
                _AKSHARE_QUOTE_REQUEST_URL,
                timeout_seconds=timeout_seconds,
                verify=True,
            ),
        )
        requests_probe = _probe_http(
            "Python requests connectivity",
            lambda: requests_get(
                _EASTMONEY_HOME_URL,
                timeout_seconds=timeout_seconds,
                verify=True,
            ),
        )
        aiohttp_result = _probe_http(
            "aiohttp connectivity",
            lambda: aiohttp_probe(
                _EASTMONEY_HOME_URL,
                timeout_seconds=timeout_seconds,
                trust_env=True,
            ),
        )

    assessment = _assess_network(
        allow_network=allow_network,
        environment_proxy=environment_proxy,
        dns=dns,
        https=https,
        endpoint=endpoint,
        quote_endpoint=quote_endpoint,
        requests_probe=requests_probe,
        aiohttp_probe=aiohttp_result,
    )
    return NetworkDiagnostics(
        generated_at=now(),
        environment_proxy=environment_proxy,
        winhttp_proxy=winhttp_proxy,
        system_proxy=system_proxy,
        system_proxy_listener=system_proxy_listener,
        dns=dns,
        https_connectivity=https,
        akshare_endpoint_reachability=endpoint,
        akshare_quote_endpoint_reachability=quote_endpoint,
        python_requests_connectivity=requests_probe,
        aiohttp_connectivity=aiohttp_result,
        browser_networking=browser_networking,
        runtime_context=runtime_context,
        assessment=assessment,
    )


def write_network_diagnostics_report(report: NetworkDiagnostics, output: Path) -> None:
    """Write a readable report from the already redacted diagnostic contract."""

    output.parent.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    lines = [
        "# Network Diagnostics",
        "",
        "> This report is redacted by construction: it contains no proxy URI, user name, "
        "password, token, or raw exception payload.",
        "",
        f"- Generated at: {payload['generated_at']}",
        f"- Assessment: {payload['assessment']}",
        f"- TLS verification: {payload['runtime_context']['tls_verification']}",
        "",
        "## Proxy observations",
        "",
        "| Layer | Configured | Scheme | Port | Credentials configured | Error type |",
        "|---|---:|---|---:|---:|---|",
    ]
    for name, observation in payload["environment_proxy"].items():
        lines.append(_proxy_row(name, observation))
    lines.append(_proxy_row("WinHTTP", payload["winhttp_proxy"]))
    lines.append(_proxy_row("Windows system proxy", payload["system_proxy"]))
    lines.extend(
        [
            f"- Local system-proxy listener: {payload['system_proxy_listener']['status']}",
            (
                f"- Local system-proxy port: "
                f"{payload['system_proxy_listener']['port'] or 'n/a'}"
            ),
            "",
            "## Connectivity",
            "",
            "| Check | Status | HTTP status | Elapsed ms | Error type |",
            "|---|---|---:|---:|---|",
        ]
    )
    for key in (
        "dns",
        "https_connectivity",
        "akshare_endpoint_reachability",
        "akshare_quote_endpoint_reachability",
        "python_requests_connectivity",
        "aiohttp_connectivity",
    ):
        observation = payload[key]
        lines.append(
            f"| {observation['label']} | {observation['status']} | "
            f"{observation['status_code'] if observation['status_code'] is not None else 'n/a'} | "
            f"{observation['elapsed_ms'] if observation['elapsed_ms'] is not None else 'n/a'} | "
            f"{observation['error_type'] or 'none'} |"
        )
    lines.extend(
        [
            "",
            "## Runtime and browser boundary",
            "",
            f"- Browser: {payload['browser_networking']['detail']}",
            f"- Virtual environment: {payload['runtime_context']['virtual_environment']}",
            "",
        ]
    )
    output.write_text("\n".join(lines), encoding="utf-8")


def inspect_winhttp_proxy(
    command_runner: CommandRunner,
    timeout_seconds: float,
) -> ProxyObservation:
    try:
        result = command_runner(("netsh", "winhttp", "show", "proxy"), timeout_seconds)
    except Exception as exc:
        return ProxyObservation(source="WINHTTP", configured=None, error_type=type(exc).__name__)
    output = "\n".join(
        value for value in (getattr(result, "stdout", ""), getattr(result, "stderr", "")) if value
    )
    if getattr(result, "returncode", 1) != 0:
        return ProxyObservation(
            source="WINHTTP",
            configured=None,
            error_type="CommandFailed",
        )
    normalized = output.casefold()
    direct_markers = ("direct access", "direct connection", "直接访问", "无代理")
    if any(marker in normalized for marker in direct_markers):
        return ProxyObservation(source="WINHTTP", configured=False)
    return ProxyObservation(
        source="WINHTTP",
        configured=bool(output.strip()),
        address="<redacted>" if output.strip() else None,
    )


def inspect_windows_system_proxy(reader: SystemProxyReader) -> ProxyObservation:
    try:
        values = dict(reader())
    except Exception as exc:
        return ProxyObservation(source="WININET", configured=None, error_type=type(exc).__name__)
    proxy_enabled = _as_bool(values.get("ProxyEnable"))
    proxy_value = values.get("ProxyServer")
    pac_value = values.get("AutoConfigURL")
    selected = proxy_value or pac_value
    observation = ProxyObservation.from_value(source="WININET", value=selected)
    return ProxyObservation(
        source=observation.source,
        configured=bool(proxy_enabled or observation.configured),
        scheme=observation.scheme,
        port=observation.port,
        credentials_configured=observation.credentials_configured,
        address=observation.address,
    )


def inspect_system_proxy_listener(
    values: Mapping[str, object] | None,
    listener_probe: ListenerProbe,
) -> ProxyListenerObservation:
    if values is None:
        return ProxyListenerObservation(status="UNKNOWN")
    value = values.get("ProxyServer")
    if not value:
        return ProxyListenerObservation(status="NOT_CONFIGURED")
    host, port = _proxy_host_and_port(str(value))
    if port is None:
        return ProxyListenerObservation(status="NO_PORT")
    if host not in {"127.0.0.1", "::1", "localhost"}:
        return ProxyListenerObservation(status="NOT_LOCAL_PROXY", port=port)
    try:
        listening = listener_probe(port)
    except Exception as exc:
        return ProxyListenerObservation(
            status="UNKNOWN",
            port=port,
            error_type=type(exc).__name__,
        )
    return ProxyListenerObservation(
        status="LISTENING" if listening else "NOT_LISTENING",
        port=port,
    )


def read_windows_system_proxy() -> Mapping[str, object]:
    if os.name != "nt":
        return {}
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    values: dict[str, object] = {}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        for name in ("ProxyEnable", "ProxyServer", "AutoConfigURL"):
            try:
                values[name] = winreg.QueryValueEx(key, name)[0]
            except FileNotFoundError:
                continue
    return values


def resolve_dns(host: str) -> Sequence[str]:
    return tuple(
        sorted({item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
    )


def is_local_listener(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def _run_command(
    command: tuple[str, ...],
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout_seconds,
    )


def _requests_get(url: str, *, timeout_seconds: float, verify: bool) -> int:
    import requests

    response = requests.get(
        url,
        timeout=timeout_seconds,
        verify=verify,
        headers={"User-Agent": "a-share-quant-network-diagnostics/1.0"},
    )
    return int(response.status_code)


def _aiohttp_probe(url: str, *, timeout_seconds: float, trust_env: bool) -> int:
    import aiohttp

    async def request() -> int:
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=trust_env) as session:
            async with session.get(url, ssl=True) as response:
                return int(response.status)

    return asyncio.run(request())


def _get_environment_value(environment: Mapping[str, str], name: str) -> str | None:
    return environment.get(name) or environment.get(name.lower())


def _read_system_proxy_observation(
    reader: SystemProxyReader,
) -> tuple[ProxyObservation, Mapping[str, object] | None]:
    try:
        values = dict(reader())
    except Exception as exc:
        return (
            ProxyObservation(
                source="WININET",
                configured=None,
                error_type=type(exc).__name__,
            ),
            None,
        )
    return inspect_windows_system_proxy(lambda: values), values


def _proxy_host_and_port(value: str) -> tuple[str | None, int | None]:
    parsed = urlsplit(value if "://" in value else f"//{value}")
    try:
        port = parsed.port
    except ValueError:
        port = None
    return parsed.hostname, port


def _probe_dns(resolver: DnsResolver) -> EndpointProbe:
    started = time.monotonic()
    try:
        addresses = tuple(resolver(_EASTMONEY_DNS_HOST))
    except Exception as exc:
        return EndpointProbe(
            label="Eastmoney DNS",
            status="FAILED",
            elapsed_ms=_elapsed_ms(started),
            error_type=type(exc).__name__,
        )
    if not addresses:
        return EndpointProbe(
            label="Eastmoney DNS",
            status="FAILED",
            elapsed_ms=_elapsed_ms(started),
            error_type="NoAddressReturned",
        )
    return EndpointProbe(
        label="Eastmoney DNS",
        status="PASS",
        elapsed_ms=_elapsed_ms(started),
        address_count=len(addresses),
    )


def _probe_http(label: str, request: Callable[[], int]) -> EndpointProbe:
    started = time.monotonic()
    try:
        status_code = int(request())
    except ImportError as exc:
        return EndpointProbe(
            label=label,
            status="UNAVAILABLE",
            elapsed_ms=_elapsed_ms(started),
            error_type=type(exc).__name__,
        )
    except Exception as exc:
        return EndpointProbe(
            label=label,
            status="FAILED",
            elapsed_ms=_elapsed_ms(started),
            error_type=type(exc).__name__,
        )
    return EndpointProbe(
        label=label,
        status="PASS" if 200 <= status_code < 400 else "HTTP_REJECTED",
        elapsed_ms=_elapsed_ms(started),
        status_code=status_code,
    )


def _not_requested(label: str) -> EndpointProbe:
    return EndpointProbe(label=label, status="NOT_REQUESTED")


def _elapsed_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 2)


def _as_bool(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return bool(value)


def _assess_network(
    *,
    allow_network: bool,
    environment_proxy: Mapping[str, ProxyObservation],
    dns: EndpointProbe,
    https: EndpointProbe,
    endpoint: EndpointProbe,
    quote_endpoint: EndpointProbe,
    requests_probe: EndpointProbe,
    aiohttp_probe: EndpointProbe,
) -> str:
    if not allow_network:
        return "NETWORK_PROBES_NOT_REQUESTED"
    if dns.status != "PASS":
        return "DNS_RESOLUTION_FAILURE"
    if https.status != "PASS":
        if any(observation.configured for observation in environment_proxy.values()):
            return "PROXY_OR_TLS_OR_PYTHON_CONNECTIVITY_FAILURE"
        return "HTTPS_CONNECTIVITY_FAILURE"
    if endpoint.status != "PASS":
        return "EASTMONEY_ENDPOINT_UNAVAILABLE_OR_REJECTED"
    if quote_endpoint.status != "PASS":
        return "AKSHARE_INDIVIDUAL_QUOTE_ENDPOINT_UNAVAILABLE_OR_REJECTED"
    if requests_probe.status != "PASS" or aiohttp_probe.status not in {"PASS", "UNAVAILABLE"}:
        return "PYTHON_NETWORK_STACK_DIFFERENCE"
    return "NETWORK_PATH_REACHABLE"


def _proxy_row(label: str, observation: Mapping[str, object]) -> str:
    return (
        f"| {label} | {observation['configured']} | {observation['scheme'] or 'n/a'} | "
        f"{observation['port'] if observation['port'] is not None else 'n/a'} | "
        f"{observation['credentials_configured']} | {observation['error_type'] or 'none'} |"
    )
