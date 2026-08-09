"""Validate production historical inputs without substituting fixture signals."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from a_share_quant.data.normalization import normalize_symbol


def choose_production_symbols(
    rows: Iterable[dict[str, Any]],
    *,
    fixture_symbols: set[str],
    limit: int,
) -> tuple[str, ...]:
    selected: set[str] = set()
    for row in rows:
        try:
            symbol = normalize_symbol(row["symbol"])
        except (KeyError, TypeError, ValueError):
            continue
        if symbol in fixture_symbols:
            continue
        if any(
            bool(row.get(field, False))
            for field in ("is_st", "is_delisting_risk", "is_suspended")
        ):
            continue
        selected.add(symbol)
    return tuple(sorted(selected))[: max(0, limit)]


def inspect_inputs(
    repo_root: Path,
    *,
    fixture_symbols: set[str],
    benchmark: str = "000300",
    limit: int = 4,
) -> dict[str, Any]:
    instrument_paths = sorted((repo_root / "data" / "lake" / "instruments").glob("*.parquet"))
    if not instrument_paths:
        return {
            "status": "NOT_READY",
            "symbols": [],
            "benchmark": benchmark,
            "missing": ["historical PIT instrument snapshot"],
            "note": "No current universe was used as a substitute.",
        }
    frame = pd.read_parquet(instrument_paths[-1])
    symbols = choose_production_symbols(
        frame.to_dict(orient="records"), fixture_symbols=fixture_symbols, limit=limit
    )
    available = {
        path.stem
        for path in (repo_root / "data" / "lake" / "daily_bars").glob("*.parquet")
    }
    missing = [f"historical bars for {symbol}" for symbol in symbols if symbol not in available]
    if benchmark not in available:
        missing.append(f"benchmark daily bars for {benchmark}")
    manifest_path = repo_root / "artifacts" / "baselines" / "STAGE2_BASELINE_MANIFEST.json"
    baseline_is_fixture = False
    if manifest_path.exists():
        raw = __import__("json").loads(manifest_path.read_text(encoding="utf-8"))
        baseline_is_fixture = any(
            entry.get("data_mode") == "fixture" for entry in raw.get("entries", [])
        )
    if baseline_is_fixture:
        missing.append("historical Stage 2 model artifacts (current baseline is fixture-only)")
    return {
        "status": "READY_FOR_HISTORICAL_PIPELINE" if not missing else "NOT_READY",
        "symbols": list(symbols),
        "benchmark": benchmark,
        "missing": missing,
        "note": (
            "No fixture signal or fixture symbol was substituted. A historical model run requires "
            "PIT data and historical model artifacts."
        ),
    }


def fetch_production_inputs(
    repo_root: Path,
    *,
    start_date: date,
    end_date: date,
    limit: int,
) -> dict[str, Any]:
    """Fetch only selected production symbols and the mapped CSI 300 benchmark."""

    from a_share_quant.config import Settings
    from a_share_quant.data.providers.akshare import AKShareDataProvider
    from a_share_quant.storage.market_store import MarketDataStore

    settings = Settings.load()
    provider = AKShareDataProvider(
        timeout_seconds=settings.request_timeout_seconds,
        retry_count=settings.request_retry_count,
        delay_seconds=settings.request_delay_seconds,
    )
    store = MarketDataStore(root=settings.data_dir, database_path=settings.database_path)
    summary: dict[str, Any] = {
        "symbols": [],
        "benchmark": "000300",
        "skipped": [],
        "errors": [],
    }
    try:
        instruments = provider.list_instruments(as_of=end_date)
        store.write_instruments(instruments)
        rows = instruments.to_dict(orient="records")
        symbols = choose_production_symbols(
            rows,
            fixture_symbols={"000001", "000002", "000003", "000004"},
            limit=limit,
        )
        summary["symbols"] = list(symbols)
        for symbol in symbols:
            existing = store.read_daily_bars(
                symbol=symbol, start_date=start_date, end_date=end_date
            )
            if not existing.empty:
                minimum = pd.to_datetime(existing["date"]).dt.date.min()
                maximum = pd.to_datetime(existing["date"]).dt.date.max()
                coverage_start = start_date + timedelta(days=7)
                coverage_end = end_date - timedelta(days=7)
                if minimum <= coverage_start and maximum >= coverage_end:
                    summary["skipped"].append(symbol)
                    continue
            try:
                bars = provider.get_daily_bars(symbol, start_date, end_date)
                store.write_daily_bars(bars)
            except Exception as exc:
                summary["errors"].append(f"{symbol}: {type(exc).__name__}")
        existing_benchmark = store.read_daily_bars(
            symbol="000300", start_date=start_date, end_date=end_date
        )
        if not existing_benchmark.empty:
            minimum = pd.to_datetime(existing_benchmark["date"]).dt.date.min()
            maximum = pd.to_datetime(existing_benchmark["date"]).dt.date.max()
            coverage_start = start_date + timedelta(days=7)
            coverage_end = end_date - timedelta(days=7)
            if minimum <= coverage_start and maximum >= coverage_end:
                summary["skipped"].append("000300")
            else:
                existing_benchmark = pd.DataFrame()
        if existing_benchmark.empty:
            try:
                benchmark = provider.get_index_daily_bars("000300", start_date, end_date)
                store.write_daily_bars(benchmark)
            except Exception as exc:
                summary["errors"].append(f"000300: {type(exc).__name__}")
    except Exception as exc:
        summary["errors"].append(f"universe: {type(exc).__name__}")
    summary["status"] = "SUCCESS" if not summary["errors"] else "FAILED"
    return summary


def write_report(payload: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Historical Dry Run Readiness",
        "",
        "> This is a production-input readiness check. Fixture symbols and fixture "
        "model outputs are never substituted.",
        "",
        f"- Status: **{payload['status']}**",
        f"- Production symbols: `{', '.join(payload['symbols']) or 'none'}`",
        f"- Benchmark mapping: `{payload['benchmark']}`",
        f"- Missing: `{'; '.join(payload['missing']) or 'none'}`",
        f"- Note: {payload['note']}",
        "",
    ]
    fetch = payload.get("fetch")
    if fetch is not None:
        lines.extend(
            [
                "## Explicit network fetch",
                "",
                f"- Fetch status: `{fetch.get('status', 'UNKNOWN')}`",
                f"- Selected symbols: `{', '.join(fetch.get('symbols', [])) or 'none'}`",
                f"- Skipped existing inputs: `{', '.join(fetch.get('skipped', [])) or 'none'}`",
                f"- Errors: `{'; '.join(fetch.get('errors', [])) or 'none'}`",
                "",
            ]
        )
    output.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--output", type=Path, default=Path("reports/historical_dry_run_readiness.md")
    )
    parser.add_argument("--limit", type=int, default=4)
    parser.add_argument(
        "--network", action="store_true", help="explicitly fetch external historical data"
    )
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2020, 1, 1))
    parser.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()
    payload = inspect_inputs(
        repo_root,
        fixture_symbols={"000001", "000002", "000003", "000004"},
        limit=args.limit,
    )
    if args.network:
        from dotenv import load_dotenv

        load_dotenv(repo_root / ".env", override=False)
        fetch = fetch_production_inputs(
            repo_root,
            start_date=args.start_date,
            end_date=args.end_date,
            limit=args.limit,
        )
        payload = inspect_inputs(
            repo_root,
            fixture_symbols={"000001", "000002", "000003", "000004"},
            limit=args.limit,
        )
        payload["fetch"] = fetch
    output = args.output if args.output.is_absolute() else repo_root / args.output
    output = output.resolve()
    if repo_root not in output.parents:
        parser.error("output path must stay inside repo root")
    write_report(payload, output)
    print(f"wrote {output}")
    print(f"status={payload['status']}")
    return 0 if payload["status"] == "READY_FOR_HISTORICAL_PIPELINE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
