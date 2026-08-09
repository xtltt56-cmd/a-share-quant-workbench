"""Record truthful Stage 3RT-E real-market validation evidence.

This runner is deliberately paper-only.  It can inspect a live market-data
provider only after an explicit network acknowledgement; it never imports a
broker SDK, creates an order, or changes operating-system proxy settings.
"""

from __future__ import annotations

import argparse
import random
import re
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from a_share_quant.analysis.breadth import calculate_market_breadth
from a_share_quant.contracts.identifiers import AssetType, SecurityIdentifier
from a_share_quant.contracts.realtime import MarketSnapshot, MinuteBar, RealTimeQuote
from a_share_quant.data.realtime.registry import ProviderRegistry, build_default_registry
from a_share_quant.runtime.realtime_telemetry import LiveDataQualityGate
from a_share_quant.runtime.scheduler import MarketSession, SessionResolver

REQUIRED_REAL_GATES = (
    "desktop_shortcut",
    "dashboard",
    "real_provider",
    "snapshot_schema",
    "csi300_index_identity",
    "fresh_quotes",
    "continuous_updates",
    "market_breadth",
    "intraday_features",
    "pit_isolation",
    "eod_finalization",
    "security_review",
)

_EXPECTED_STOCK_SYMBOLS = ("000001", "000002")
_SAFE_STATUS = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,79}$")


def _safe_path(repo_root: Path, value: Path) -> Path:
    root = repo_root.resolve()
    candidate = (value if value.is_absolute() else root / value).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"output path is outside repository: {value}")
    return candidate


def validate_snapshot(
    snapshot: MarketSnapshot,
    *,
    csi300: SecurityIdentifier,
    now: datetime,
    minimum_full_market_quotes: int,
) -> dict[str, Any]:
    """Validate real stock samples and the explicit CSI300 index identity."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if minimum_full_market_quotes < len(_EXPECTED_STOCK_SYMBOLS):
        raise ValueError("minimum_full_market_quotes must cover both stock samples")
    observed = {quote.symbol: quote for quote in snapshot.quotes}
    missing = tuple(symbol for symbol in _EXPECTED_STOCK_SYMBOLS if symbol not in observed)
    sample_issues: dict[str, tuple[str, ...]] = {}
    fresh = True
    for symbol in _EXPECTED_STOCK_SYMBOLS:
        quote = observed.get(symbol)
        if quote is None:
            continue
        issues = _quote_schema_issues(quote, now=now)
        if issues:
            sample_issues[symbol] = issues
        fresh = fresh and quote.data_age_seconds(now=now) <= 60 and not quote.is_stale
    csi300_identity = (
        csi300.symbol == "000300"
        and csi300.exchange == "SSE"
        and csi300.asset_type is AssetType.INDEX
    )
    return {
        "quote_count": len(snapshot.quotes),
        "required_symbols": list(_EXPECTED_STOCK_SYMBOLS),
        "missing_symbols": list(missing),
        "sample_schema_issues": {
            symbol: list(issues) for symbol, issues in sample_issues.items()
        },
        "required_symbols_pass": not missing,
        "schema_pass": not missing and not sample_issues,
        "freshness_pass": not missing and fresh,
        "full_market_snapshot": len(snapshot.quotes) >= minimum_full_market_quotes,
        "csi300_index_identity": csi300_identity,
        "source": _safe_label(snapshot.source),
    }


def finalize_validation(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Derive the only permissible stage state from concrete evidence."""

    evidence_mode = str(payload.get("evidence_mode", "OFFLINE")).upper()
    if evidence_mode not in {"OFFLINE", "REPLAY", "REAL_MARKET"}:
        evidence_mode = "OFFLINE"
    raw_gates = payload.get("gates", {})
    gates = {
        gate: bool(raw_gates.get(gate, False))
        for gate in REQUIRED_REAL_GATES
    }
    snapshot = dict(payload.get("snapshot") or {})
    snapshot_pass = all(
        bool(snapshot.get(name, False))
        for name in (
            "schema_pass",
            "required_symbols_pass",
            "freshness_pass",
            "full_market_snapshot",
            "csi300_index_identity",
        )
    )
    provider_available = bool(payload.get("provider_available", False))
    real_ready = (
        evidence_mode == "REAL_MARKET"
        and provider_available
        and snapshot_pass
        and all(gates.values())
    )
    if real_ready:
        stage_status = "STAGE_3RT_REAL_MARKET_VALIDATED"
    elif not provider_available:
        stage_status = "NO_REALTIME_PROVIDER_AVAILABLE"
    else:
        stage_status = "STAGE_3RT_OFFLINE_VALIDATED"
    result = dict(payload)
    result.update(
        {
            "evidence_mode": evidence_mode,
            "provider_available": provider_available,
            "gates": gates,
            "snapshot": snapshot,
            "snapshot_pass": snapshot_pass,
            "missing_gates": [name for name, value in gates.items() if not value],
            "stage_status": stage_status,
            "errors": [_safe_error_type(item) for item in payload.get("errors", ())],
        }
    )
    return result


def run_validation(
    *,
    allow_network: bool,
    repo_root: Path,
    now: datetime | None = None,
    snapshot_count: int = 2,
    snapshot_interval_seconds: float = 15.0,
    minimum_full_market_quotes: int = 1000,
    registry_builder: Callable[[], ProviderRegistry] = build_default_registry,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Collect live evidence only in an actual open session.

    The default session resolver is intentionally conservative: without an
    externally verified exchange calendar, a weekday result is insufficient for
    promotion because the actual-session gate remains false.
    """

    if snapshot_count < 2:
        raise ValueError("snapshot_count must be at least two")
    if snapshot_interval_seconds < 0:
        raise ValueError("snapshot_interval_seconds cannot be negative")
    if minimum_full_market_quotes < len(_EXPECTED_STOCK_SYMBOLS):
        raise ValueError("minimum_full_market_quotes is too small")

    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    resolver = SessionResolver()
    session = resolver.resolve(reference)
    capabilities = discover_provider_capabilities(allow_network=allow_network)
    initial_available = any(bool(item["configured"]) for item in capabilities)
    smoke_evidence = _load_realtime_smoke_evidence(repo_root)
    no_provider_from_smoke = (
        allow_network
        and smoke_evidence["status"] == "FAILED"
        and not _professional_realtime_capability_available(capabilities)
    )
    if no_provider_from_smoke:
        initial_available = False
    payload: dict[str, Any] = {
        "started_at": reference.isoformat(),
        "completed_at": None,
        "evidence_mode": "OFFLINE",
        "market_session": session.value,
        "calendar_evidence": (
            "WEEKEND_NON_TRADING"
            if session is MarketSession.NON_TRADING
            else "EXCHANGE_CALENDAR_NOT_YET_VERIFIED"
        ),
        "provider": None,
        "source": None,
        "provider_available": initial_available,
        "capabilities": capabilities,
        "samples": [],
        "latencies_ms": [],
        "average_latency_ms": None,
        "p95_latency_ms": None,
        "freshness": "NOT_RUN",
        "data_quality": "FAILED",
        "market_breadth": {"status": "NOT_RUN"},
        "intraday_features": {"status": "NOT_RUN"},
        "historical_dry_run": _historical_readiness(repo_root),
        "realtime_smoke": smoke_evidence,
        "eod_status": "PENDING_REAL_TRADING_DAY",
        "security_review_status": "PASS_CODE_REVIEW_PENDING_REAL_MARKET",
        "fallback_events": [],
        "gates": {name: False for name in REQUIRED_REAL_GATES},
        "snapshot": {},
        "errors": ["NoRealtimeProviderAvailableFromSmoke"] if no_provider_from_smoke else [],
    }
    if not allow_network:
        payload["errors"].append("NetworkAcknowledgementRequired")
        payload["completed_at"] = datetime.now(timezone.utc).isoformat()
        return finalize_validation(payload)
    if session is not MarketSession.OPEN:
        payload["errors"].append("MarketSessionNotOpen")
        payload["completed_at"] = datetime.now(timezone.utc).isoformat()
        return finalize_validation(payload)

    try:
        registry = registry_builder()
        registry.discover()
        if not registry.providers:
            payload["provider_available"] = False
            payload["errors"].append("NoProviderPassedCapabilityDiscovery")
            payload["completed_at"] = datetime.now(timezone.utc).isoformat()
            return finalize_validation(payload)

        provider = registry.build_failover()
        payload["provider"] = provider.active_provider_name
        payload["source"] = _source_label(provider.active_provider_name)
        gate = LiveDataQualityGate()
        latest_snapshot: MarketSnapshot | None = None
        latest_validation: dict[str, Any] = {}
        for position in range(snapshot_count):
            started = time.monotonic()
            snapshot = provider.get_market_snapshot()
            elapsed = round((time.monotonic() - started) * 1000, 3)
            latest_snapshot = snapshot
            csi300 = SecurityIdentifier(
                symbol="000300",
                exchange="SSE",
                asset_type=AssetType.INDEX,
            )
            latest_validation = validate_snapshot(
                snapshot,
                csi300=csi300,
                now=datetime.now(timezone.utc),
                minimum_full_market_quotes=minimum_full_market_quotes,
            )
            quality = gate.evaluate(
                snapshot.quotes,
                provider_connected=True,
                circuit_breaker_open=False,
                expected_symbols=_EXPECTED_STOCK_SYMBOLS,
                now=datetime.now(timezone.utc),
            )
            payload["samples"].append(
                _sample_record(snapshot, latest_validation, quality.status.value)
            )
            payload["latencies_ms"].append(elapsed)
            payload["data_quality"] = quality.status.value
            payload["freshness"] = (
                "PASS" if latest_validation["freshness_pass"] else "FAILED"
            )
            if position + 1 < snapshot_count:
                sleeper(snapshot_interval_seconds)

        assert latest_snapshot is not None
        index_quotes = provider.get_index_snapshot(("000300",))
        index_pass = _valid_index_quote(index_quotes)
        payload["snapshot"] = latest_validation
        payload["snapshot"]["csi300_index_identity"] = bool(
            latest_validation["csi300_index_identity"] and index_pass
        )
        payload["provider"] = provider.active_provider_name
        payload["source"] = _source_label(provider.active_provider_name)
        payload["provider_available"] = True
        payload["evidence_mode"] = "REAL_MARKET"
        payload["market_breadth"] = _market_breadth_payload(
            latest_snapshot,
            full_market=bool(latest_validation["full_market_snapshot"]),
        )
        payload["intraday_features"] = _collect_intraday_features(
            provider,
            snapshot=latest_snapshot,
            now=datetime.now(timezone.utc),
        )
        payload["gates"].update(
            {
                "real_provider": True,
                "snapshot_schema": bool(latest_validation["schema_pass"]),
                "csi300_index_identity": bool(
                    latest_validation["csi300_index_identity"]
                ),
                "fresh_quotes": bool(latest_validation["freshness_pass"]),
                "continuous_updates": quality.continuous_updates,
                "market_breadth": payload["market_breadth"]["status"] == "PASS",
                "intraday_features": payload["intraday_features"]["status"] == "PASS",
            }
        )
        payload["fallback_events"] = [
            {
                "source_from": _safe_label(item.source_from),
                "source_to": _safe_label(item.source_to),
                "reason": _safe_error_type(item.reason),
                "timestamp": item.timestamp.isoformat(),
            }
            for item in provider.switch_events
        ]
    except Exception as exc:
        payload["provider_available"] = False
        payload["errors"].append(type(exc).__name__)

    payload["completed_at"] = datetime.now(timezone.utc).isoformat()
    payload["average_latency_ms"] = _average(payload["latencies_ms"])
    payload["p95_latency_ms"] = _p95(payload["latencies_ms"])
    return finalize_validation(payload)


def discover_provider_capabilities(*, allow_network: bool) -> list[dict[str, Any]]:
    """Return a credential-safe capability matrix without reading secret values."""

    import os

    return [
        _discover_tushare_capability(
            token=os.getenv("TUSHARE_TOKEN"),
            allow_network=allow_network,
        ),
        _discover_rqdata_capability(
            username=os.getenv("RQDATA_USERNAME"),
            password=os.getenv("RQDATA_PASSWORD"),
            config_path=os.getenv("RQDATA_CONFIG_PATH"),
            allow_network=allow_network,
        ),
        _akshare_capability(allow_network=allow_network),
    ]


def write_validation_report(payload: Mapping[str, Any], output: Path) -> None:
    """Write a redacted Markdown evidence report."""

    output.parent.mkdir(parents=True, exist_ok=True)
    stage_status = str(payload.get("stage_status", "STAGE_3RT_OFFLINE_VALIDATED"))
    lines = [
        "# Stage 3RT Real Market Validation",
        "",
        "> Paper-only validation report. It contains status categories, aggregate telemetry, "
        "and redacted error classes only; it contains no credentials, proxy URI, or raw "
        "provider payload.",
        "",
        f"- Current status: {stage_status}",
        f"- Evidence mode: {_safe_label(payload.get('evidence_mode'))}",
        f"- Started: {_safe_label(payload.get('started_at'))}",
        f"- Completed: {_safe_label(payload.get('completed_at'))}",
        f"- Market session: {_safe_label(payload.get('market_session'))}",
        f"- Calendar evidence: {_safe_label(payload.get('calendar_evidence'))}",
        f"- Active provider: {_safe_label(payload.get('provider')) or 'none'}",
        f"- Active source: {_safe_label(payload.get('source')) or 'none'}",
        "",
        "## Evidence modes",
        "",
        "| Mode | Meaning |",
        "|---|---|",
        "| OFFLINE | No live quote was accepted as current market evidence. |",
        "| REPLAY | Deterministic test data; never market evidence. |",
        (
            "| REAL MARKET | A provider was observed during an open session; "
            "promotion still requires every gate below. |"
        ),
        "",
        "## Provider capability matrix",
        "",
        (
            "| Provider | Configured | Authenticated | Status | Snapshot | Historical daily | "
            "Historical minute | Live minute | Live tick |"
        ),
        "|---|---:|---:|---|---|---|---|---|---|",
    ]
    for capability in payload.get("capabilities", []):
        lines.append(
            (
                "| {provider} | {configured} | {authenticated} | {status} | {snapshot} | "
                "{historical_daily} | {historical_minute} | {live_minute} | {live_tick} |"
            ).format(
                provider=_safe_label(capability.get("provider")),
                configured=bool(capability.get("configured", False)),
                authenticated=bool(capability.get("authenticated", False)),
                status=_safe_label(capability.get("status")),
                snapshot=_safe_label(capability.get("snapshot")),
                historical_daily=_safe_label(capability.get("historical_daily")),
                historical_minute=_safe_label(capability.get("historical_minute")),
                live_minute=_safe_label(capability.get("live_minute")),
                live_tick=_safe_label(capability.get("live_tick")),
            )
        )
    snapshot = dict(payload.get("snapshot") or {})
    smoke = dict(payload.get("realtime_smoke") or _empty_smoke_evidence())
    lines.extend(
        [
            "",
            "## Snapshot and latency",
            "",
            f"- Snapshot quote count: {snapshot.get('quote_count', 0)}",
            f"- Required stocks: {', '.join(snapshot.get('required_symbols', [])) or 'none'}",
            f"- Missing stocks: {', '.join(snapshot.get('missing_symbols', [])) or 'none'}",
            f"- Snapshot schema: {bool(snapshot.get('schema_pass', False))}",
            f"- Freshness: {_safe_label(payload.get('freshness'))}",
            (
                "- Continuous updates: "
                f"{bool(payload.get('gates', {}).get('continuous_updates', False))}"
            ),
            f"- CSI300 index identity: {bool(snapshot.get('csi300_index_identity', False))}",
            f"- Average latency (ms): {payload.get('average_latency_ms', 'n/a')}",
            f"- P95 latency (ms): {payload.get('p95_latency_ms', 'n/a')}",
            "",
            "## Explicit provider smoke attempt",
            "",
            f"- Status: {_safe_error_type(smoke.get('status'))}",
            f"- Active provider: {_safe_error_type(smoke.get('active_provider'))}",
            f"- Latency (ms): {smoke.get('latency_ms', 'n/a')}",
            f"- Data quality: {_safe_error_type(smoke.get('data_quality'))}",
            f"- Error class: {_safe_error_type(smoke.get('error'))}",
            "",
            "## Market breadth, intraday features, historical and EOD",
            "",
            f"- Breadth status: {_safe_label(payload.get('market_breadth', {}).get('status'))}",
            (
                "- Intraday feature status: "
                f"{_safe_label(payload.get('intraday_features', {}).get('status'))}"
            ),
            (
                "- Historical dry-run status: "
                f"{_safe_label(payload.get('historical_dry_run', {}).get('status'))}"
            ),
            f"- EOD status: {_safe_label(payload.get('eod_status'))}",
            (
                "- Security review: "
                f"{_safe_error_type(payload.get('security_review_status') or 'NOT_REVIEWED')}"
            ),
            "",
            "## Final gates",
            "",
            "| Gate | Pass |",
            "|---|---:|",
        ]
    )
    for name in REQUIRED_REAL_GATES:
        lines.append(f"| {name} | {bool(payload.get('gates', {}).get(name, False))} |")
    errors = [_safe_error_type(item) for item in payload.get("errors", ())]
    lines.extend(["", "## Sanitized observations", ""])
    if errors:
        lines.extend(f"- {item}" for item in errors)
    else:
        lines.append("- None.")
    if stage_status == "STAGE_3RT_REAL_MARKET_VALIDATED":
        lines.extend(["", "## Promotion", "", "- STAGE_3RT_REAL_MARKET_VALIDATED"])
    else:
        lines.extend(
            [
                "",
                "## Promotion",
                "",
                "- No promotion: this report does not establish a completed real-market gate.",
            ]
        )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _quote_schema_issues(quote: RealTimeQuote, *, now: datetime) -> tuple[str, ...]:
    issues: list[str] = []
    if not quote.name:
        issues.append("missing_name")
    for name in ("last", "open", "high", "low", "previous_close", "volume", "amount", "change_pct"):
        if getattr(quote, name) is None:
            issues.append(f"missing_{name}")
    if quote.last is None or quote.last <= 0:
        issues.append("invalid_last")
    if quote.high is not None and quote.low is not None and quote.high < quote.low:
        issues.append("invalid_ohlc")
    if quote.volume is not None and quote.volume < 0:
        issues.append("invalid_volume")
    if quote.timestamp_exchange > now or quote.timestamp_received > now:
        issues.append("future_timestamp")
    if not quote.source:
        issues.append("missing_source")
    return tuple(dict.fromkeys(issues))


def validate_intraday_features(
    *,
    stock_bars: tuple[MinuteBar, ...],
    index_bars: tuple[MinuteBar, ...],
    quotes_by_symbol: Mapping[str, RealTimeQuote],
    now: datetime,
    symbols: tuple[str, ...] = _EXPECTED_STOCK_SYMBOLS,
) -> dict[str, Any]:
    """Compute required intraday features using only same-or-earlier index bars."""

    import pandas as pd

    from a_share_quant.features.intraday import IntradayFeatureEngine

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    expected = set(symbols)
    valid_index = tuple(
        sorted(
            (
                bar
                for bar in index_bars
                if bar.symbol == "000300" and bar.timestamp <= now
            ),
            key=lambda item: item.timestamp,
        )
    )
    rows: list[dict[str, Any]] = []
    for bar in sorted(stock_bars, key=lambda item: (item.symbol, item.timestamp)):
        if bar.symbol not in expected or bar.timestamp > now:
            continue
        quote = quotes_by_symbol.get(bar.symbol)
        benchmark_return = _causal_benchmark_return(valid_index, bar.timestamp)
        rows.append(
            {
                "symbol": bar.symbol,
                "timestamp": bar.timestamp,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "amount": bar.amount,
                "previous_close": quote.previous_close if quote is not None else None,
                "benchmark_return": benchmark_return,
            }
        )
    if not rows:
        return {
            "status": "NOT_AVAILABLE",
            "symbols": [],
            "feature_rows": 0,
            "missing_required_features": ["no_causal_stock_minute_bars"],
        }
    frame = pd.DataFrame(rows)
    try:
        features = IntradayFeatureEngine().compute(frame)
    except Exception as exc:
        return {
            "status": "FAILED",
            "symbols": sorted(set(frame["symbol"])),
            "feature_rows": len(frame),
            "missing_required_features": [_safe_error_type(exc)],
        }
    required_columns = (
        "price_vs_previous_close",
        "price_vs_open",
        "vwap",
        "price_vs_vwap",
        "volume_ratio",
        "intraday_return",
        "intraday_volatility",
        "market_relative_strength",
    )
    missing: list[str] = []
    for symbol in sorted(expected):
        rows_for_symbol = features.loc[features["symbol"] == symbol]
        if rows_for_symbol.empty:
            missing.append(f"{symbol}:missing_minute_bars")
            continue
        latest = rows_for_symbol.iloc[-1]
        for column in required_columns:
            if column not in latest or pd.isna(latest[column]):
                missing.append(f"{symbol}:{column}")
    return {
        "status": "PASS" if not missing else "INCOMPLETE",
        "symbols": sorted(set(features["symbol"])),
        "feature_rows": len(features),
        "missing_required_features": missing,
    }


def _collect_intraday_features(
    provider: Any,
    *,
    snapshot: MarketSnapshot,
    now: datetime,
) -> dict[str, Any]:
    symbols = _select_feature_symbols(snapshot, count=5, seed=now.date().isoformat())
    if len(symbols) < 5:
        return {
            "status": "INCOMPLETE",
            "symbols": list(symbols),
            "feature_rows": 0,
            "missing_required_features": ["at_least_five_real_tradable_symbols_required"],
        }
    try:
        stock_bars = tuple(provider.get_minute_bars(symbols, "1m"))
        index_bars = tuple(provider.get_minute_bars(("000300",), "1m"))
    except Exception as exc:
        return {
            "status": "FAILED",
            "symbols": list(symbols),
            "feature_rows": 0,
            "missing_required_features": [_safe_error_type(exc)],
        }
    quotes_by_symbol = {quote.symbol: quote for quote in snapshot.quotes}
    result = validate_intraday_features(
        stock_bars=stock_bars,
        index_bars=index_bars,
        quotes_by_symbol=quotes_by_symbol,
        now=now,
        symbols=symbols,
    )
    return {**result, "selected_symbols": list(symbols)}


def _select_feature_symbols(
    snapshot: MarketSnapshot,
    *,
    count: int,
    seed: str,
) -> tuple[str, ...]:
    candidates = sorted(
        {
            quote.symbol
            for quote in snapshot.quotes
            if quote.symbol != "000300"
            and quote.market.upper() == "A"
            and quote.last is not None
            and quote.last > 0
            and not quote.is_stale
        }
    )
    mandatory = [symbol for symbol in _EXPECTED_STOCK_SYMBOLS if symbol in candidates]
    available = [symbol for symbol in candidates if symbol not in mandatory]
    remaining_count = max(0, count - len(mandatory))
    if len(available) < remaining_count:
        return tuple(mandatory + available)
    selected = random.Random(seed).sample(available, remaining_count)
    return tuple(mandatory + sorted(selected))


def _causal_benchmark_return(
    index_bars: tuple[MinuteBar, ...],
    timestamp: datetime,
) -> float | None:
    visible = tuple(bar for bar in index_bars if bar.timestamp <= timestamp)
    if not visible or visible[0].close <= 0:
        return None
    return visible[-1].close / visible[0].close - 1


def _sample_record(
    snapshot: MarketSnapshot,
    validation: Mapping[str, Any],
    quality: str,
) -> dict[str, Any]:
    observed = {quote.symbol: quote for quote in snapshot.quotes}
    samples = []
    for symbol in _EXPECTED_STOCK_SYMBOLS:
        quote = observed.get(symbol)
        if quote is None:
            continue
        samples.append(
            {
                "symbol": symbol,
                "last": quote.last,
                "volume": quote.volume,
                "timestamp": quote.timestamp_exchange.isoformat(),
                "source": _safe_label(quote.source),
            }
        )
    return {
        "quality": _safe_label(quality),
        "quote_count": validation.get("quote_count", 0),
        "samples": samples,
    }


def _valid_index_quote(quotes: tuple[RealTimeQuote, ...]) -> bool:
    return any(
        quote.symbol == "000300"
        and quote.last is not None
        and quote.last > 0
        and quote.market.upper() in {"INDEX", "A"}
        for quote in quotes
    )


def _market_breadth_payload(
    snapshot: MarketSnapshot,
    *,
    full_market: bool,
) -> dict[str, Any]:
    if not full_market:
        return {"status": "PARTIAL_UNIVERSE"}
    breadth = calculate_market_breadth(snapshot.quotes)
    return {
        "status": "PASS" if breadth.total > 0 else "FAILED",
        "total": breadth.total,
        "up": breadth.up,
        "down": breadth.down,
        "flat": breadth.flat,
        "limit_up": breadth.limit_up,
        "limit_down": breadth.limit_down,
        "median_change_pct": breadth.median_change_pct,
        "up_ratio": breadth.up_ratio,
        "amount": breadth.amount,
    }


def _historical_readiness(repo_root: Path) -> dict[str, Any]:
    try:
        from scripts.run_historical_dry_run import inspect_inputs

    except ModuleNotFoundError:  # direct Python script execution places scripts/ first
        from run_historical_dry_run import inspect_inputs

    try:
        return inspect_inputs(
            repo_root,
            fixture_symbols={"000001", "000002", "000003", "000004"},
            limit=10,
        )
    except Exception as exc:
        return {"status": "NOT_READY", "error": _safe_error_type(exc)}


def _empty_smoke_evidence() -> dict[str, Any]:
    return {
        "status": "NOT_RUN",
        "active_provider": "none",
        "latency_ms": None,
        "data_quality": "NOT_RUN",
        "error": "none",
    }


def _load_realtime_smoke_evidence(repo_root: Path) -> dict[str, Any]:
    """Read only fixed, sanitized fields from the local smoke report."""

    report = repo_root / "reports" / "realtime_data_smoke_test.md"
    if not report.is_file():
        return _empty_smoke_evidence()
    try:
        text = report.read_text(encoding="utf-8")
    except OSError as exc:
        return {**_empty_smoke_evidence(), "error": _safe_error_type(exc)}
    latency = (
        _smoke_report_field(text, "Latency")
        .replace("`", "")
        .removesuffix("ms")
        .strip()
    )
    try:
        latency_ms = float(latency) if latency and latency != "n/a" else None
    except ValueError:
        latency_ms = None
    return {
        "status": _safe_error_type(_smoke_report_field(text, "Status")),
        "active_provider": _safe_error_type(
            _smoke_report_field(text, "Active provider") or "none"
        ),
        "latency_ms": latency_ms,
        "data_quality": _safe_error_type(_smoke_report_field(text, "Data quality")),
        "error": _safe_error_type(_smoke_report_field(text, "Error") or "none"),
    }


def _smoke_report_field(text: str, label: str) -> str:
    prefix = f"- {label}:"
    for line in text.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip().strip("`").strip("*").strip()
    return ""


def _capability_record(
    *,
    provider: str,
    configured: bool,
    allow_network: bool,
    configuration_status: str,
) -> dict[str, Any]:
    if not configured:
        return {
            "provider": provider,
            "configured": False,
            "authenticated": False,
            "status": configuration_status,
            "snapshot": "NOT_CONFIGURED",
            "historical_daily": "NOT_CONFIGURED",
            "historical_minute": "NOT_CONFIGURED",
            "live_minute": "NOT_CONFIGURED",
            "live_tick": "NOT_CONFIGURED",
        }
    if not allow_network:
        return {
            "provider": provider,
            "configured": True,
            "authenticated": False,
            "status": "NETWORK_NOT_REQUESTED",
            "snapshot": "NOT_PROBED",
            "historical_daily": "NOT_PROBED",
            "historical_minute": "NOT_PROBED",
            "live_minute": "NOT_PROBED",
            "live_tick": "NOT_PROBED",
        }
    return {
        "provider": provider,
        "configured": True,
        "authenticated": True,
        "status": "PENDING_NETWORK_PROBE",
        "snapshot": "PENDING_NETWORK_PROBE",
        "historical_daily": "PENDING_NETWORK_PROBE",
        "historical_minute": "PENDING_NETWORK_PROBE",
        "live_minute": "PENDING_NETWORK_PROBE",
        "live_tick": "PENDING_NETWORK_PROBE",
    }


def _discover_tushare_capability(*, token: str | None, allow_network: bool) -> dict[str, Any]:
    base = _capability_record(
        provider="tushare",
        configured=bool(token),
        allow_network=allow_network,
        configuration_status="DISABLED",
    )
    if not token or not allow_network:
        return base
    try:
        from a_share_quant.data.realtime.tushare import TushareRealTimeProvider

        health = TushareRealTimeProvider(token=token).health_check()
    except Exception as exc:
        return {
            **base,
            "authenticated": True,
            "status": _safe_error_type(exc),
            "snapshot": "SYMBOL_LIST_ONLY",
            "historical_daily": "NOT_PROBED_BY_REALTIME_ADAPTER",
            "historical_minute": "NOT_PROBED_BY_REALTIME_ADAPTER",
            "live_minute": "REALTIME_PERMISSION_UNAVAILABLE",
            "live_tick": "NOT_PROVIDED",
        }
    if health.connected:
        return {
            **base,
            "authenticated": True,
            "status": "AUTHENTICATED",
            "snapshot": "SYMBOL_LIST_ONLY",
            "historical_daily": "NOT_PROBED_BY_REALTIME_ADAPTER",
            "historical_minute": "NOT_PROBED_BY_REALTIME_ADAPTER",
            "live_minute": "AVAILABLE",
            "live_tick": "NOT_PROVIDED",
        }
    return {
        **base,
        "authenticated": True,
        "status": "AUTHENTICATED_BUT_REALTIME_PERMISSION_UNAVAILABLE",
        "snapshot": "SYMBOL_LIST_ONLY",
        "historical_daily": "NOT_PROBED_BY_REALTIME_ADAPTER",
        "historical_minute": "NOT_PROBED_BY_REALTIME_ADAPTER",
        "live_minute": "REALTIME_PERMISSION_UNAVAILABLE",
        "live_tick": "NOT_PROVIDED",
    }


def _discover_rqdata_capability(
    *,
    username: str | None,
    password: str | None,
    config_path: str | None,
    allow_network: bool,
) -> dict[str, Any]:
    configured = bool(config_path or (username and password))
    base = _capability_record(
        provider="rqdata",
        configured=configured,
        allow_network=allow_network,
        configuration_status="NOT_CONFIGURED",
    )
    if not configured or not allow_network:
        return base
    try:
        from a_share_quant.data.realtime.rqdata import RQDataRealTimeProvider

        health = RQDataRealTimeProvider(
            username=username,
            password=password,
            config_path=config_path,
        ).health_check()
    except Exception as exc:
        return {
            **base,
            "authenticated": True,
            "status": _safe_error_type(exc),
            "snapshot": "REALTIME_PERMISSION_UNAVAILABLE",
            "historical_daily": "NOT_PROBED_BY_REALTIME_ADAPTER",
            "historical_minute": "NOT_PROBED_BY_REALTIME_ADAPTER",
            "live_minute": "REALTIME_PERMISSION_UNAVAILABLE",
            "live_tick": "NOT_PROBED",
        }
    if health.connected:
        return {
            **base,
            "authenticated": True,
            "status": "AUTHENTICATED",
            "snapshot": "AVAILABLE",
            "historical_daily": "NOT_PROBED_BY_REALTIME_ADAPTER",
            "historical_minute": "NOT_PROBED_BY_REALTIME_ADAPTER",
            "live_minute": "AVAILABLE",
            "live_tick": "NOT_PROBED",
        }
    return {
        **base,
        "authenticated": True,
        "status": "AUTHENTICATED_BUT_REALTIME_PERMISSION_UNAVAILABLE",
        "snapshot": "REALTIME_PERMISSION_UNAVAILABLE",
        "historical_daily": "NOT_PROBED_BY_REALTIME_ADAPTER",
        "historical_minute": "NOT_PROBED_BY_REALTIME_ADAPTER",
        "live_minute": "REALTIME_PERMISSION_UNAVAILABLE",
        "live_tick": "NOT_PROBED",
    }


def _akshare_capability(*, allow_network: bool) -> dict[str, Any]:
    return {
        "provider": "akshare",
        "configured": True,
        "authenticated": False,
        "status": "PUBLIC_AVAILABLE_FOR_PROBE" if allow_network else "NETWORK_NOT_REQUESTED",
        "snapshot": "PUBLIC_SNAPSHOT",
        "historical_daily": "AVAILABLE_VIA_HISTORICAL_PROVIDER",
        "historical_minute": "PENDING_LIVE_PROBE",
        "live_minute": "PENDING_LIVE_PROBE",
        "live_tick": "PUBLIC_SNAPSHOT_ONLY",
    }


def _professional_realtime_capability_available(
    capabilities: list[Mapping[str, Any]],
) -> bool:
    return any(
        item.get("provider") in {"rqdata", "tushare"}
        and item.get("live_minute") == "AVAILABLE"
        for item in capabilities
    )


def _source_label(provider: str | None) -> str | None:
    if provider == "akshare":
        return "AKShare / Eastmoney"
    if provider == "rqdata":
        return "RQData"
    if provider == "tushare":
        return "Tushare"
    return _safe_label(provider) or None


def _safe_label(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"[^A-Za-z0-9_./: +()-]", "?", str(value))[:160]


def _safe_error_type(value: object) -> str:
    if isinstance(value, BaseException):
        return type(value).__name__
    candidate = str(value)
    return candidate if _SAFE_STATUS.fullmatch(candidate) else "SanitizedError"


def _average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, int(len(ordered) * 0.95 + 0.999999) - 1)
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", action="store_true", help="permit live provider probes")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/stage3_real_market_validation.md"),
    )
    parser.add_argument("--snapshot-count", type=int, default=2)
    parser.add_argument("--snapshot-interval-seconds", type=float, default=15.0)
    parser.add_argument("--minimum-full-market-quotes", type=int, default=1000)
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()
    if args.network:
        from dotenv import load_dotenv

        load_dotenv(repo_root / ".env", override=False)
    output = _safe_path(repo_root, args.output)
    payload = run_validation(
        allow_network=args.network,
        repo_root=repo_root,
        snapshot_count=args.snapshot_count,
        snapshot_interval_seconds=args.snapshot_interval_seconds,
        minimum_full_market_quotes=args.minimum_full_market_quotes,
    )
    write_validation_report(payload, output)
    print(f"wrote {output}")
    print(f"status={payload['stage_status']} evidence={payload['evidence_mode']}")
    return 0 if payload["stage_status"] == "STAGE_3RT_REAL_MARKET_VALIDATED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
