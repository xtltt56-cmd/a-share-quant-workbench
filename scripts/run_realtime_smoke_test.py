"""Run an explicit, sanitized real-market real-time provider smoke test."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from a_share_quant.data.realtime.registry import build_default_registry
from a_share_quant.data.realtime.validation import assess_quote_quality


def _safe_path(repo_root: Path, value: Path) -> Path:
    root = repo_root.resolve()
    candidate = (value if value.is_absolute() else root / value).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"output path is outside repository: {value}")
    return candidate


def run_smoke() -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    registry = build_default_registry()
    capabilities = registry.discover()
    payload: dict[str, Any] = {
        "status": "FAILED",
        "started_at": started.isoformat(),
        "completed_at": None,
        "providers": [_capability_dict(item) for item in capabilities],
        "active_provider": registry.active_provider_name,
        "fallback_events": [],
        "sample_symbols": [],
        "index_symbols": [],
        "latency_ms": None,
        "data_quality": "FAILED",
        "error": None,
    }
    if not registry.providers:
        payload["error"] = "no provider passed capability discovery"
        payload["completed_at"] = datetime.now(timezone.utc).isoformat()
        return payload

    failover = registry.build_failover()
    try:
        snapshot = failover.get_market_snapshot()
        observed = {quote.symbol: quote for quote in snapshot.quotes}
        sample_symbols = tuple(symbol for symbol in ("000001", "000002") if symbol in observed)
        quality = assess_quote_quality(
            [observed[symbol] for symbol in sample_symbols],
            expected_symbols=("000001", "000002"),
            now=datetime.now(timezone.utc),
            stale_after_seconds=60,
        )
        index_quotes = failover.get_index_snapshot(("000300",))
        payload.update(
            {
                "status": "SUCCESS" if sample_symbols else "DEGRADED",
                "active_provider": failover.active_provider_name,
                "sample_symbols": [_quote_dict(observed[symbol]) for symbol in sample_symbols],
                "index_symbols": [_quote_dict(quote) for quote in index_quotes],
                "latency_ms": round(
                    (datetime.now(timezone.utc) - started).total_seconds() * 1000, 2
                ),
                "data_quality": quality.status.value,
                "error": None if sample_symbols else "requested sample symbols were not returned",
            }
        )
    except Exception as exc:
        payload["error"] = type(exc).__name__
        payload["active_provider"] = failover.active_provider_name
    payload["fallback_events"] = [
        {
            "source_from": event.source_from,
            "source_to": event.source_to,
            "reason": event.reason,
            "timestamp": event.timestamp.isoformat(),
        }
        for event in failover.switch_events
    ]
    payload["completed_at"] = datetime.now(timezone.utc).isoformat()
    return payload


def write_smoke_report(payload: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    latency = payload.get("latency_ms") if payload.get("latency_ms") is not None else "n/a"
    lines = [
        "# Real-Time Data Smoke Test",
        "",
        "> This report records an explicit provider smoke attempt. It is not investment "
        "evidence and contains no credentials.",
        "",
        f"- Status: **{payload.get('status', 'UNKNOWN')}**",
        f"- Started: `{payload.get('started_at', '')}`",
        f"- Completed: `{payload.get('completed_at', '')}`",
        f"- Active provider: `{payload.get('active_provider') or 'none'}`",
        f"- Latency: `{latency}` ms",
        f"- Data quality: `{payload.get('data_quality', 'UNKNOWN')}`",
        f"- Error: `{payload.get('error') or 'none'}`",
        "",
        "## Capability discovery",
        "",
        "| Provider | Authenticated | Market | Frequency | Permissions | Status |",
        "|---|---:|---|---|---|---|",
    ]
    for item in payload.get("providers", []):
        permissions = ", ".join(item.get("permissions", [])) or "none"
        lines.append(
            f"| {item.get('provider', '')} | {item.get('authenticated', False)} | "
            f"{item.get('market', '')} | {item.get('frequency', '') or 'n/a'} | "
            f"{permissions} | {item.get('status', '')} |"
        )
    lines.extend(
        [
            "",
            "## Stock samples",
            "",
            "| Symbol | Last | Volume | Source | Received | Age (s) |",
            "|---|---:|---:|---|---|---:|",
        ]
    )
    for item in payload.get("sample_symbols", []):
        lines.append(
            f"| {item['symbol']} | {item.get('last', '')} | {item.get('volume', '')} | "
            f"{item.get('source', '')} | {item.get('timestamp_received', '')} | "
            f"{item.get('data_age_seconds', '')} |"
        )
    lines.extend(
        [
            "",
            "## Index samples",
            "",
            "| Symbol | Last | Source | Received |",
            "|---|---:|---|---|",
        ]
    )
    for item in payload.get("index_symbols", []):
        lines.append(
            f"| {item['symbol']} | {item.get('last', '')} | {item.get('source', '')} | "
            f"{item.get('timestamp_received', '')} |"
        )
    lines.extend(["", "## Provider switches", ""])
    if payload.get("fallback_events"):
        for event in payload["fallback_events"]:
            lines.append(
                f"- `{event['timestamp']}`: `{event['source_from']}` -> "
                f"`{event['source_to']}`; reason `{event['reason']}`"
            )
    else:
        lines.append("- None recorded.")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _capability_dict(item: Any) -> dict[str, Any]:
    return {
        "provider": item.provider,
        "authenticated": item.authenticated,
        "market": item.market,
        "frequency": item.frequency,
        "latency_ms": item.latency_ms,
        "last_update": item.last_update.isoformat() if item.last_update else None,
        "permissions": list(item.permissions),
        "status": item.status,
    }


def _quote_dict(quote: Any) -> dict[str, Any]:
    return {
        "symbol": quote.symbol,
        "last": quote.last,
        "volume": quote.volume,
        "source": quote.source,
        "timestamp_received": quote.timestamp_received.isoformat(),
        "data_age_seconds": round(quote.data_age_seconds(), 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--network", action="store_true", help="explicitly permit external provider calls"
    )
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=Path("reports/realtime_data_smoke_test.md"))
    args = parser.parse_args()
    if not args.network:
        parser.error("add --network to explicitly permit an external provider call")
    from dotenv import load_dotenv

    load_dotenv(args.repo_root.resolve() / ".env", override=False)
    output = _safe_path(args.repo_root.resolve(), args.output)
    payload = run_smoke()
    write_smoke_report(payload, output)
    print(f"wrote {output}")
    print(f"status={payload['status']} active_provider={payload.get('active_provider') or 'none'}")
    return 0 if payload["status"] == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
