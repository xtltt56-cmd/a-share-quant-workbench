"""Operator CLI for the local paper-only quant workbench."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from a_share_quant.config import Settings
from a_share_quant.data.realtime.diagnostics import (
    collect_network_diagnostics,
    write_network_diagnostics_report,
)
from a_share_quant.storage.market_store import MarketDataStore
from a_share_quant.workbench.advisory_context import load_advisory_context, load_instrument_map
from a_share_quant.workbench.advisory_service import AdvisoryWorkbenchService
from a_share_quant.workbench.app import run_server
from a_share_quant.workbench.service import WorkbenchService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    workbench = subcommands.add_parser("workbench", help="start the local dashboard")
    workbench.add_argument("--port", type=int, default=8765)
    workbench.add_argument(
        "--advisory-ledger",
        type=Path,
        required=True,
        help="local JSONL path for explicitly recorded manual fills",
    )
    workbench.add_argument(
        "--advisory-initial-cash",
        required=True,
        help="explicit initial cash used when replaying the local ledger",
    )
    workbench.add_argument(
        "--advisory-context",
        type=Path,
        default=None,
        help="optional local JSON context artifact used by the today-guidance route",
    )
    workbench.add_argument(
        "--advisory-instrument-map",
        type=Path,
        default=None,
        help="optional local JSON symbol-to-name map for manual ledger validation",
    )
    mode = workbench.add_mutually_exclusive_group()
    mode.add_argument("--network", action="store_true", help="allow provider requests")
    mode.add_argument("--offline", action="store_true", help="never call providers")

    status = subcommands.add_parser("status", help="show sanitized local capability status")
    status.add_argument("--json", action="store_true", dest="as_json")

    advisory_status = subcommands.add_parser(
        "advisory-status",
        help="show local manual-advisory ledger and data status without network access",
    )
    advisory_status.add_argument("--ledger", type=Path, required=True)
    advisory_status.add_argument("--initial-cash", required=True)

    refresh = subcommands.add_parser("refresh", help="perform one paper-monitor refresh")
    refresh.add_argument(
        "--network",
        action="store_true",
        help="explicit acknowledgement for an external provider request",
    )

    realtime = subcommands.add_parser("realtime", help="real-time operator tools")
    realtime_subcommands = realtime.add_subparsers(dest="realtime_command", required=True)
    diagnose = realtime_subcommands.add_parser(
        "diagnose-network",
        help="inspect redacted proxy and market-data connectivity state",
    )
    diagnose.add_argument(
        "--network",
        action="store_true",
        help="explicitly permit bounded DNS and HTTPS connectivity probes",
    )
    diagnose.add_argument(
        "--output",
        type=Path,
        default=None,
        help="optional Markdown report path inside the repository",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "workbench":
        known_instruments = _load_default_instrument_map()
        if args.advisory_instrument_map is not None:
            known_instruments = load_instrument_map(args.advisory_instrument_map)
        context_provider = (
            (lambda: load_advisory_context(args.advisory_context))
            if args.advisory_context is not None
            else None
        )
        advisory_service = AdvisoryWorkbenchService(
            initial_cash=args.advisory_initial_cash,
            ledger_path=args.advisory_ledger,
            known_instruments=known_instruments,
            context_provider=context_provider,
        )
        run_server(
            port=args.port,
            allow_network=bool(args.network and not args.offline),
            advisory_service=advisory_service,
        )
        return 0
    if args.command == "status":
        payload = WorkbenchService(allow_network=False).health()
        payload["live_trading_enabled"] = False
        payload["paper_only"] = True
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if args.command == "advisory-status":
        service = AdvisoryWorkbenchService(
            initial_cash=args.initial_cash,
            ledger_path=args.ledger,
        )
        print(
            json.dumps(
                {
                    "holdings": service.holdings(),
                    "model_data_health": service.model_data_health(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "realtime":
        report = collect_network_diagnostics(allow_network=bool(args.network))
        if args.output is not None:
            repo_root = Path(__file__).resolve().parents[1]
            output = args.output if args.output.is_absolute() else repo_root / args.output
            output = output.resolve()
            if output != repo_root and repo_root not in output.parents:
                raise SystemExit("diagnostic output path must stay inside the repository")
            write_network_diagnostics_report(report, output)
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return 0
    if not args.network:
        raise SystemExit(
            "refresh requires --network as an explicit external provider acknowledgement"
        )
    service = WorkbenchService(allow_network=True)
    service.refresh()
    print(json.dumps(service.snapshot(), ensure_ascii=False, indent=2, default=str))
    return 0


def _load_default_instrument_map() -> dict[str, str]:
    """Use the newest canonical local catalog when ingestion has produced one."""

    settings = Settings.load()
    instrument_dir = settings.data_dir / "lake" / "instruments"
    if not any(instrument_dir.glob("*.parquet")):
        return {}
    try:
        frame = MarketDataStore(
            root=settings.data_dir,
            database_path=settings.database_path,
        ).read_instruments()
    except Exception:
        return {}
    if frame.empty or not {"symbol", "name"}.issubset(frame.columns):
        return {}
    return {
        str(row.symbol): str(row.name).strip()
        for row in frame.itertuples(index=False)
        if str(row.name).strip()
    }


if __name__ == "__main__":
    raise SystemExit(main())
