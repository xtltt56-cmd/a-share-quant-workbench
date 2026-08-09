import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from subprocess import CompletedProcess

NOW = datetime(2026, 8, 9, 3, 0, tzinfo=timezone.utc)


def _secret_environment() -> dict[str, str]:
    return {
        "HTTP_PROXY": "http://diagnostic-user:diagnostic-password@proxy.example:8080/path?token=x",
        "HTTPS_PROXY": "https://another-user:another-password@proxy.example:8443",
        "ALL_PROXY": "socks5://all-user:all-password@proxy.example:1080",
        "NO_PROXY": "localhost,internal.example",
    }


def test_network_diagnostics_redacts_proxy_values_and_uses_verified_clients(
    tmp_path: Path,
) -> None:
    assert importlib.util.find_spec("a_share_quant.data.realtime.diagnostics") is not None
    from a_share_quant.data.realtime.diagnostics import (
        collect_network_diagnostics,
        write_network_diagnostics_report,
    )

    request_calls: list[tuple[str, bool]] = []
    aiohttp_calls: list[tuple[str, bool]] = []

    def command_runner(command: tuple[str, ...], timeout_seconds: float) -> CompletedProcess[str]:
        assert command == ("netsh", "winhttp", "show", "proxy")
        assert timeout_seconds == 2
        return CompletedProcess(
            command,
            0,
            "Current WinHTTP proxy settings:\n    Proxy Server(s) : proxy.example:8080",
            "",
        )

    def system_proxy_reader() -> dict[str, object]:
        return {
            "ProxyEnable": 1,
            "ProxyServer": "http://system-user:system-password@system.example:8080",
            "AutoConfigURL": "https://pac-user:pac-password@pac.example/config?token=y",
        }

    def requests_get(url: str, *, timeout_seconds: float, verify: bool) -> int:
        assert timeout_seconds == 2
        request_calls.append((url, verify))
        return 200

    def aiohttp_probe(url: str, *, timeout_seconds: float, trust_env: bool) -> int:
        assert timeout_seconds == 2
        aiohttp_calls.append((url, trust_env))
        return 200

    report = collect_network_diagnostics(
        allow_network=True,
        environ=_secret_environment(),
        command_runner=command_runner,
        system_proxy_reader=system_proxy_reader,
        dns_resolver=lambda host: ("203.0.113.9",),
        requests_get=requests_get,
        aiohttp_probe=aiohttp_probe,
        clock=lambda: NOW,
        timeout_seconds=2,
    )

    payload = report.to_dict()
    serialized = json.dumps(payload, ensure_ascii=False)
    for secret in (
        "diagnostic-user",
        "diagnostic-password",
        "another-user",
        "another-password",
        "all-user",
        "all-password",
        "system-user",
        "system-password",
        "pac-user",
        "pac-password",
        "token=x",
        "token=y",
        "internal.example",
        "proxy.example",
    ):
        assert secret not in serialized

    assert payload["environment_proxy"]["HTTP_PROXY"]["configured"] is True
    assert payload["environment_proxy"]["HTTP_PROXY"]["scheme"] == "http"
    assert payload["environment_proxy"]["HTTP_PROXY"]["port"] == 8080
    assert payload["environment_proxy"]["HTTP_PROXY"]["credentials_configured"] is True
    assert payload["environment_proxy"]["HTTP_PROXY"]["address"] == "<redacted>"
    assert payload["system_proxy"]["configured"] is True
    assert payload["system_proxy_listener"]["status"] == "NOT_LOCAL_PROXY"
    assert payload["winhttp_proxy"]["configured"] is True
    assert payload["dns"]["status"] == "PASS"
    assert payload["https_connectivity"]["status"] == "PASS"
    assert payload["akshare_endpoint_reachability"]["status"] == "PASS"
    assert payload["akshare_quote_endpoint_reachability"]["status"] == "PASS"
    assert payload["python_requests_connectivity"]["status"] == "PASS"
    assert payload["aiohttp_connectivity"]["status"] == "PASS"
    assert request_calls and all(verify for _, verify in request_calls)
    assert aiohttp_calls and all(trust_env for _, trust_env in aiohttp_calls)
    assert any(
        "82.push2.eastmoney.com/api/qt/clist/get" in url for url, _ in request_calls
    )
    assert any(
        "push2.eastmoney.com/api/qt/stock/get" in url for url, _ in request_calls
    )

    output = tmp_path / "network_diagnostics.md"
    write_network_diagnostics_report(report, output)
    report_text = output.read_text(encoding="utf-8")
    assert "AKShare endpoint reachability" in report_text
    assert "diagnostic-password" not in report_text
    assert "proxy.example" not in report_text


def test_network_diagnostics_without_network_does_not_call_http_clients() -> None:
    assert importlib.util.find_spec("a_share_quant.data.realtime.diagnostics") is not None
    from a_share_quant.data.realtime.diagnostics import collect_network_diagnostics

    def unexpected_http(*args: object, **kwargs: object) -> int:
        raise AssertionError("network transport should not be called")

    report = collect_network_diagnostics(
        allow_network=False,
        environ={},
        command_runner=lambda command, timeout_seconds: CompletedProcess(
            command,
            0,
            "Direct access",
            "",
        ),
        system_proxy_reader=lambda: {},
        dns_resolver=lambda host: ("203.0.113.9",),
        requests_get=unexpected_http,
        aiohttp_probe=unexpected_http,
        clock=lambda: NOW,
    )

    payload = report.to_dict()
    assert payload["dns"]["status"] == "NOT_REQUESTED"
    assert payload["https_connectivity"]["status"] == "NOT_REQUESTED"
    assert payload["akshare_endpoint_reachability"]["status"] == "NOT_REQUESTED"
    assert payload["akshare_quote_endpoint_reachability"]["status"] == "NOT_REQUESTED"
    assert payload["assessment"] == "NETWORK_PROBES_NOT_REQUESTED"


def test_network_diagnostics_reports_local_system_proxy_listener_without_address() -> None:
    from a_share_quant.data.realtime.diagnostics import collect_network_diagnostics

    checked_ports: list[int] = []
    report = collect_network_diagnostics(
        allow_network=False,
        environ={},
        command_runner=lambda command, timeout_seconds: CompletedProcess(
            command,
            0,
            "Direct access",
            "",
        ),
        system_proxy_reader=lambda: {"ProxyEnable": 1, "ProxyServer": "127.0.0.1:7892"},
        listener_probe=lambda port: checked_ports.append(port) is None or True,
        clock=lambda: NOW,
    )

    payload = report.to_dict()
    assert checked_ports == [7892]
    assert payload["system_proxy_listener"]["status"] == "LISTENING"
    assert payload["system_proxy_listener"]["port"] == 7892
    assert "127.0.0.1" not in json.dumps(payload)


def test_quant_cli_accepts_redacted_network_diagnostics_command() -> None:
    from scripts.quant_cli import build_parser

    args = build_parser().parse_args(["realtime", "diagnose-network"])

    assert args.command == "realtime"
    assert args.realtime_command == "diagnose-network"
    assert args.network is False
