import sys
import types
from subprocess import CompletedProcess

import pytest

from a_share_quant.data.realtime import diagnostics
from a_share_quant.data.realtime.diagnostics import (
    EndpointProbe,
    ProxyObservation,
    _assess_network,
    _probe_dns,
    _probe_http,
    _proxy_host_and_port,
    _read_system_proxy_observation,
    _requests_get,
    _run_command,
    inspect_system_proxy_listener,
    inspect_windows_system_proxy,
    inspect_winhttp_proxy,
    is_local_listener,
    read_windows_system_proxy,
)


def test_proxy_diagnostic_helpers_fail_closed_without_raw_values() -> None:
    invalid_port = ProxyObservation.from_value(
        source="HTTP_PROXY",
        value="http://user:password@proxy.example:not-a-port",
    )
    assert invalid_port.port is None
    assert invalid_port.credentials_configured is True
    assert "password" not in str(invalid_port.to_dict())

    command_error = inspect_winhttp_proxy(
        lambda command, timeout: (_ for _ in ()).throw(RuntimeError("raw proxy failure")),
        1,
    )
    command_failed = inspect_winhttp_proxy(
        lambda command, timeout: CompletedProcess(command, 1, "", "failure"),
        1,
    )
    reader_error = inspect_windows_system_proxy(
        lambda: (_ for _ in ()).throw(RuntimeError("raw registry failure"))
    )

    assert command_error.error_type == "RuntimeError"
    assert command_failed.error_type == "CommandFailed"
    assert reader_error.error_type == "RuntimeError"


def test_proxy_listener_and_local_helpers_cover_safe_edge_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert inspect_system_proxy_listener({}, lambda port: True).status == "NOT_CONFIGURED"
    assert (
        inspect_system_proxy_listener(
            {"ProxyServer": "127.0.0.1"},
            lambda port: True,
        ).status
        == "NO_PORT"
    )
    assert (
        inspect_system_proxy_listener(
            {"ProxyServer": "127.0.0.1:7892"},
            lambda port: (_ for _ in ()).throw(OSError("listener failure")),
        ).error_type
        == "OSError"
    )
    assert _proxy_host_and_port("http://proxy.example:not-a-port") == (
        "proxy.example",
        None,
    )

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

    monkeypatch.setattr(
        diagnostics.socket,
        "create_connection",
        lambda address, timeout: Connection(),
    )
    assert is_local_listener(7892) is True
    monkeypatch.setattr(
        diagnostics.socket,
        "create_connection",
        lambda address, timeout: (_ for _ in ()).throw(OSError("not listening")),
    )
    assert is_local_listener(7892) is False

    monkeypatch.setattr(diagnostics.os, "name", "posix")
    assert read_windows_system_proxy() == {}


def test_network_probe_helpers_classify_failures_without_exception_payloads() -> None:
    dns_error = _probe_dns(lambda host: (_ for _ in ()).throw(RuntimeError("secret")))
    dns_empty = _probe_dns(lambda host: ())
    http_unavailable = _probe_http(
        "optional client",
        lambda: (_ for _ in ()).throw(ImportError("missing")),
    )
    http_failed = _probe_http(
        "failing client",
        lambda: (_ for _ in ()).throw(RuntimeError("secret")),
    )
    http_rejected = _probe_http("rate limited", lambda: 429)

    assert dns_error.error_type == "RuntimeError"
    assert dns_empty.error_type == "NoAddressReturned"
    assert http_unavailable.status == "UNAVAILABLE"
    assert http_failed.error_type == "RuntimeError"
    assert http_rejected.status == "HTTP_REJECTED"


def test_network_assessment_distinguishes_proxy_dns_endpoint_and_stack_failures() -> None:
    pass_probe = EndpointProbe(label="pass", status="PASS")
    failed_probe = EndpointProbe(label="failed", status="FAILED")
    unavailable_probe = EndpointProbe(label="unavailable", status="UNAVAILABLE")
    proxy = {"HTTPS_PROXY": ProxyObservation(source="HTTPS_PROXY", configured=True)}
    direct = {"HTTPS_PROXY": ProxyObservation(source="HTTPS_PROXY", configured=False)}

    assert (
        _assess_network(
            allow_network=False,
            environment_proxy=direct,
            dns=pass_probe,
            https=pass_probe,
            endpoint=pass_probe,
            quote_endpoint=pass_probe,
            requests_probe=pass_probe,
            aiohttp_probe=pass_probe,
        )
        == "NETWORK_PROBES_NOT_REQUESTED"
    )
    assert (
        _assess_network(
            allow_network=True,
            environment_proxy=direct,
            dns=failed_probe,
            https=pass_probe,
            endpoint=pass_probe,
            quote_endpoint=pass_probe,
            requests_probe=pass_probe,
            aiohttp_probe=pass_probe,
        )
        == "DNS_RESOLUTION_FAILURE"
    )
    assert (
        _assess_network(
            allow_network=True,
            environment_proxy=proxy,
            dns=pass_probe,
            https=failed_probe,
            endpoint=pass_probe,
            quote_endpoint=pass_probe,
            requests_probe=pass_probe,
            aiohttp_probe=pass_probe,
        )
        == "PROXY_OR_TLS_OR_PYTHON_CONNECTIVITY_FAILURE"
    )
    assert (
        _assess_network(
            allow_network=True,
            environment_proxy=direct,
            dns=pass_probe,
            https=failed_probe,
            endpoint=pass_probe,
            quote_endpoint=pass_probe,
            requests_probe=pass_probe,
            aiohttp_probe=pass_probe,
        )
        == "HTTPS_CONNECTIVITY_FAILURE"
    )
    assert (
        _assess_network(
            allow_network=True,
            environment_proxy=direct,
            dns=pass_probe,
            https=pass_probe,
            endpoint=failed_probe,
            quote_endpoint=pass_probe,
            requests_probe=pass_probe,
            aiohttp_probe=pass_probe,
        )
        == "EASTMONEY_ENDPOINT_UNAVAILABLE_OR_REJECTED"
    )
    assert (
        _assess_network(
            allow_network=True,
            environment_proxy=direct,
            dns=pass_probe,
            https=pass_probe,
            endpoint=pass_probe,
            quote_endpoint=failed_probe,
            requests_probe=pass_probe,
            aiohttp_probe=pass_probe,
        )
        == "AKSHARE_INDIVIDUAL_QUOTE_ENDPOINT_UNAVAILABLE_OR_REJECTED"
    )
    assert (
        _assess_network(
            allow_network=True,
            environment_proxy=direct,
            dns=pass_probe,
            https=pass_probe,
            endpoint=pass_probe,
            quote_endpoint=pass_probe,
            requests_probe=failed_probe,
            aiohttp_probe=unavailable_probe,
        )
        == "PYTHON_NETWORK_STACK_DIFFERENCE"
    )


def test_default_requests_wrapper_keeps_tls_verification_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    class Response:
        status_code = 204

    def fake_get(url: str, **kwargs: object) -> Response:
        observed["url"] = url
        observed.update(kwargs)
        return Response()

    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=fake_get))

    assert _requests_get("https://example.invalid/", timeout_seconds=3, verify=True) == 204
    assert observed["verify"] is True
    assert observed["timeout"] == 3
    assert isinstance(observed["headers"], dict)


def test_command_and_reader_adapters_are_injectable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> CompletedProcess[str]:
        captured["args"] = args
        captured.update(kwargs)
        return CompletedProcess(args[0], 0, "Direct access", "")

    monkeypatch.setattr(diagnostics.subprocess, "run", fake_run)
    result = _run_command(("netsh", "winhttp", "show", "proxy"), 2)
    observation, values = _read_system_proxy_observation(lambda: {"ProxyEnable": "yes"})

    assert result.returncode == 0
    assert captured["timeout"] == 2
    assert captured["check"] is False
    assert observation.configured is True
    assert values == {"ProxyEnable": "yes"}
