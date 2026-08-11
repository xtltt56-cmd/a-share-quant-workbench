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
from a_share_quant.research.daily_candidates import generate_from_data_root, load_name_map
from a_share_quant.storage.market_store import MarketDataStore
from a_share_quant.storage.official_signal_store import OfficialSignalStore
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
    workbench.add_argument(
        "--official-signal-path",
        type=Path,
        default=Path(".runtime/signals/official-daily.json"),
        help="durable official daily candidate artifact",
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

    daily = subcommands.add_parser(
        "daily-candidates",
        help="generate and persist real BaoStock daily candidates",
    )
    daily.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    daily.add_argument("--data-root", type=Path, default=None)
    daily.add_argument(
        "--output",
        type=Path,
        default=Path(".runtime/signals/official-daily.json"),
    )
    daily.add_argument(
        "--report",
        type=Path,
        default=Path("reports/official_daily_generation.md"),
    )
    daily.add_argument("--top-k", type=int, default=10)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "workbench":
        repo_root = Path(__file__).resolve().parents[1]
        official_signal_path = _inside(repo_root, args.official_signal_path)
        official_signal_store = OfficialSignalStore(path=official_signal_path)
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
            official_signal_store=official_signal_store,
            context_provider=context_provider,
        )
        run_server(
            port=args.port,
            repo_root=repo_root,
            allow_network=bool(args.network and not args.offline),
            advisory_service=advisory_service,
            official_signal_store=official_signal_store,
        )
        return 0
    if args.command == "daily-candidates":
        repo_root = args.repo_root.resolve()
        data_root = (args.data_root or repo_root / "data").resolve()
        output = _inside(repo_root, args.output)
        report = _inside(repo_root, args.report)
        signals = generate_from_data_root(
            data_root,
            top_k=args.top_k,
            name_map=load_name_map(data_root),
        )
        OfficialSignalStore(path=output).put_signals(signals)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            "\n".join(
                [
                    "# 官方日选生成记录",
                    "",
                    f"- 状态：成功（{len(signals)} 条）",
                    f"- 信号日期：{signals[0].signal_date}",
                    "- 数据源：BaoStock",
                    "- 策略版本：initial-free-data-v1",
                    "- 说明：固定权重横截面候选排序，不是经过校准的收益预测。",
                    "",
                    *(
                        f"{signal.rank}. {signal.symbol} {signal.name} "
                        f"score={signal.normalized_score:.2f} "
                        f"price={signal.reference_price:.4f}"
                        for signal in signals
                    ),
                    "",
                ]
            ),
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "status": "SUCCESS",
                    "signal_date": signals[0].signal_date.isoformat(),
                    "candidate_count": len(signals),
                    "output": str(output),
                    "report": str(report),
                },
                ensure_ascii=False,
                indent=2,
            )
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


def _inside(root: Path, value: Path) -> Path:
    candidate = (value if value.is_absolute() else root / value).resolve()
    if candidate != root and root not in candidate.parents:
        raise SystemExit(f"path must stay inside repo root: {value}")
    return candidate


if __name__ == "__main__":
    raise SystemExit(main())
