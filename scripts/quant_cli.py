"""Operator CLI for the local paper-only quant workbench."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from a_share_quant.account.import_inbox import AccountImportInbox
from a_share_quant.account.snapshot_store import AccountSnapshotStore
from a_share_quant.config import Settings
from a_share_quant.data.providers.baostock import BaoStockDataProvider
from a_share_quant.data.realtime.diagnostics import (
    collect_network_diagnostics,
    write_network_diagnostics_report,
)
from a_share_quant.research.daily_candidates import generate_from_data_root, load_name_map
from a_share_quant.research.evolution import EvolutionRegistry
from a_share_quant.runtime import research_worker
from a_share_quant.runtime.historical_backfill import HistoricalBackfillCoordinator
from a_share_quant.runtime.official_daily import load_or_generate_official_store
from a_share_quant.runtime.price_guidance import (
    PriceGuidanceRuntime,
    load_bars,
    load_or_generate_price_guidance_store,
)
from a_share_quant.runtime.research_jobs import ResearchJobSupervisor
from a_share_quant.storage.market_store import MarketDataStore
from a_share_quant.storage.official_signal_store import OfficialSignalStore
from a_share_quant.storage.price_guidance_store import PriceGuidanceStore
from a_share_quant.storage.project_storage import ProjectStoragePolicy
from a_share_quant.storage.research_data_store import ResearchDataStore
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
    workbench.add_argument(
        "--account-import-dir",
        type=Path,
        default=Path(".runtime/advisory/import-inbox"),
        help="fixed local directory for official account CSV/XLS exports",
    )
    workbench.add_argument(
        "--account-snapshot-path",
        type=Path,
        default=Path(".runtime/advisory/imported-account-snapshot.json"),
        help="local integrity-checked imported position snapshot",
    )
    workbench.add_argument(
        "--price-guidance-path",
        type=Path,
        default=Path(".runtime/advisory/price-guidance.json"),
        help="冻结价格指导计划存储路径",
    )
    workbench.add_argument(
        "--research-checkpoint",
        type=Path,
        default=Path(".runtime/research/research-checkpoint.json"),
        help="研究任务的可校验检查点路径",
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
    daily.add_argument(
        "--allow-stale",
        action="store_true",
        help="允许生成研究用途的过期日线候选；默认拒绝并保持上次结果",
    )
    price_guidance = subcommands.add_parser("price-guidance", help="生成或查看冻结价格指导")
    price_guidance_sub = price_guidance.add_subparsers(dest="price_guidance_command", required=True)
    generate = price_guidance_sub.add_parser("generate")
    generate.add_argument("--data-root", type=Path, default=Path("data"))
    generate.add_argument(
        "--output", type=Path, default=Path(".runtime/advisory/price-guidance.json")
    )
    generate.add_argument("--calculation-date", required=True)
    generate.add_argument("--valid-for", required=True)
    generate.add_argument("--symbols", nargs="+", required=True)
    inspect = price_guidance_sub.add_parser("inspect")
    inspect.add_argument(
        "--output", type=Path, default=Path(".runtime/advisory/price-guidance.json")
    )
    inspect.add_argument("--symbol", default=None)

    history = subcommands.add_parser("history", help="历史研究数据工具")
    history_subcommands = history.add_subparsers(dest="history_command", required=True)
    history_subcommands.add_parser("status", help="离线查看历史数据覆盖与D盘目标")
    history_backfill = history_subcommands.add_parser(
        "backfill", help="在明确授权网络后执行一个有界历史回填批次"
    )
    history_backfill.add_argument("--start", required=True, help="开始日期 YYYY-MM-DD")
    history_backfill.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
    history_backfill.add_argument(
        "--network",
        action="store_true",
        help="显式允许本批次访问免费历史数据源",
    )

    research = subcommands.add_parser("research", help="受工作台生命周期管理的研究任务")
    research_subcommands = research.add_subparsers(
        dest="research_command", required=True
    )
    research_subcommands.add_parser(
        "status", help="离线查看研究任务、检查点和未来竞赛状态"
    )
    research_subcommands.add_parser(
        "screen", help="执行仅用于工程筛查的研究任务，不得晋级模型"
    )
    contest_start = research_subcommands.add_parser(
        "contest-start", help="冻结未来预测竞赛版本；结果只能由未来观测决定"
    )
    contest_start.add_argument(
        "--version",
        default=None,
        help="要冻结的模型/策略版本；不提供时使用当前固定冠军版本",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "research":
        repo_root = _project_root()
        if args.research_command == "status":
            print(json.dumps(_research_status(repo_root), ensure_ascii=False, indent=2))
            return 0
        if args.research_command == "screen":
            exit_code = research_worker.run_job("screen", repo_root)
            status_path = repo_root / ".runtime" / "research" / "screen-status.json"
            payload = _read_local_json(status_path)
            payload["worker_exit_code"] = exit_code
            # A blocked engineering screen is an auditable result, not a CLI
            # crash. It must never be interpreted as a promotion.
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        version = args.version or "champion-v1"
        payload = _freeze_contest(repo_root, version)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if args.command == "history":
        repo_root = _project_root()
        if args.history_command == "backfill" and not args.network:
            raise SystemExit("history backfill requires explicit --network authorization")
        coordinator = _create_history_coordinator(
            repo_root,
            allow_network=args.history_command == "backfill" and bool(args.network),
        )
        targets = {
            "网络访问": bool(args.history_command == "backfill" and args.network),
            "D盘研究数据目录": str(coordinator.data_root),
            "D盘检查点": str(coordinator.checkpoint_path),
        }
        if args.history_command == "status":
            print(
                json.dumps(
                    {**targets, "覆盖状态": coordinator.coverage().to_dict()},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        # This announcement is deliberately flushed before coordinator.run(),
        # which is the first point at which provider calls are permitted.
        print(json.dumps({"授权写入目标": targets}, ensure_ascii=False, indent=2), flush=True)
        result = coordinator.run(start=args.start, end=args.end)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "workbench":
        repo_root = _project_root()
        official_signal_path = _inside(repo_root, args.official_signal_path)
        account_import_dir = _operator_path(repo_root, args.account_import_dir)
        account_snapshot_path = _operator_path(repo_root, args.account_snapshot_path)
        price_guidance_path = _inside(repo_root, args.price_guidance_path)
        research_checkpoint_path = _inside(repo_root, args.research_checkpoint)
        storage_policy = ProjectStoragePolicy(repo_root, required_drive="D:")
        research_supervisor = ResearchJobSupervisor(
            research_checkpoint_path.parent, storage_policy=storage_policy
        )
        governance = EvolutionRegistry(
            state_path=repo_root / ".runtime" / "research" / "evolution-registry.json"
        )
        research_supervisor.register_job(
            "forecast-on-launch",
            ("research", "predict"),
            due_at=datetime.now(timezone.utc),
        )
        research_supervisor.start_due_jobs(now=datetime.now(timezone.utc))
        official_signal_store = load_or_generate_official_store(
            official_signal_path,
            repo_root=repo_root,
        )
        price_guidance_store = load_or_generate_price_guidance_store(
            price_guidance_path,
            repo_root=repo_root,
            official_signal_store=official_signal_store,
        )
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
            account_import_inbox=AccountImportInbox(account_import_dir),
            account_snapshot_store=AccountSnapshotStore(account_snapshot_path),
            price_guidance_store=price_guidance_store,
        )
        run_server(
            port=args.port,
            repo_root=repo_root,
            allow_network=bool(args.network and not args.offline),
            advisory_service=advisory_service,
            official_signal_store=official_signal_store,
            price_guidance_store=price_guidance_store,
            supervisor=research_supervisor,
            governance=governance,
        )
        return 0
    if args.command == "price-guidance":
        repo_root = Path(__file__).resolve().parents[1]
        output = _inside(repo_root, args.output)
        store = PriceGuidanceStore(output)
        if args.price_guidance_command == "inspect":
            plans = store.plans()
            if args.symbol:
                plans = tuple(item for item in plans if item.symbol == args.symbol)
            print(json.dumps([item.to_dict() for item in plans], ensure_ascii=False, indent=2))
            return 0
        data_root = _inside(repo_root, args.data_root)
        symbols = tuple(args.symbols)
        result = PriceGuidanceRuntime(
            bars_by_symbol=load_bars(data_root, symbols),
            store=store,
            candidate_symbols=symbols,
        ).generate(args.calculation_date, args.valid_for)
        print(json.dumps([item.to_dict() for item in result.plans], ensure_ascii=False, indent=2))
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
            require_fresh=not args.allow_stale,
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


def _project_root() -> Path:
    """Return the fixed repository root; CLI callers cannot redirect workers."""

    return Path(__file__).resolve().parents[1]


def _research_status(repo_root: Path) -> dict[str, object]:
    """Read local research state without constructing or calling a provider."""

    policy = ProjectStoragePolicy(repo_root, required_drive="D:")
    checkpoint_path = policy.authorize(".runtime/research/research-checkpoint.json")
    supervisor = ResearchJobSupervisor(
        checkpoint_path.parent,
        storage_policy=policy,
    )
    contest_path = policy.authorize(".runtime/research/prospective-contest.json")
    contest = _read_local_json(contest_path) if contest_path.exists() else None
    return {
        "状态": "离线",
        "网络访问": False,
        "D盘项目根": str(policy.repo_root),
        "检查点": str(checkpoint_path),
        "可恢复任务": list(supervisor.resume_eligible_jobs()),
        "未来竞赛": contest,
    }


def _freeze_contest(repo_root: Path, version: str) -> dict[str, object]:
    """Freeze one version for prospective-only observation, idempotently."""

    normalized = str(version).strip()
    if (
        not normalized
        or len(normalized) > 128
        or any(token in normalized for token in ("..", "/", "\\", "--"))
    ):
        raise SystemExit("contest version is not safe")
    policy = ProjectStoragePolicy(repo_root, required_drive="D:")
    destination = policy.authorize(".runtime/research/prospective-contest.json")
    if destination.exists():
        existing = _read_local_json(destination)
        _verify_frozen_contest(existing)
        if existing.get("contest_version") != normalized:
            raise SystemExit("future contest is already frozen to a different version")
        return existing
    body: dict[str, object] = {
        "format_version": 1,
        "contest_version": normalized,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "evidence_mode": "PROSPECTIVE_ONLY",
        "status": "OBSERVING",
        "promotion": "NEVER",
    }
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    payload = {**body, "sha256": hashlib.sha256(encoded).hexdigest()}
    policy.revalidate(destination.parent)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    policy.revalidate(temporary)
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    policy.revalidate(destination)
    temporary.replace(destination)
    return payload


def _read_local_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit("本地研究状态不可读取") from exc
    if not isinstance(payload, dict):
        raise SystemExit("本地研究状态格式无效")
    return payload


def _verify_frozen_contest(payload: dict[str, object]) -> None:
    digest = payload.get("sha256")
    body = {key: value for key, value in payload.items() if key != "sha256"}
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if not isinstance(digest, str) or digest != hashlib.sha256(encoded).hexdigest():
        raise SystemExit("未来竞赛冻结文件校验失败")


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


def _operator_path(root: Path, value: Path) -> Path:
    """Resolve a fixed operator-selected path without exposing it to HTTP."""

    return (value if value.is_absolute() else root / value).resolve()


def _create_history_coordinator(
    repo_root: Path, *, allow_network: bool
) -> HistoricalBackfillCoordinator:
    """Build the fixed-path history runtime without initiating network access."""

    policy = ProjectStoragePolicy(repo_root, required_drive="D:")
    maturity = _yaml_mapping(repo_root / "config" / "research_maturity.yaml")
    storage = maturity.get("storage", {})
    if not isinstance(storage, dict):
        raise SystemExit("config/research_maturity.yaml storage must be a mapping")
    store = ResearchDataStore(
        policy,
        maximum_single_file_bytes=int(
            storage.get(
                "maximum_single_download_bytes",
                ResearchDataStore.DEFAULT_MAXIMUM_SINGLE_FILE_BYTES,
            )
        ),
        maximum_research_data_bytes=int(
            storage.get(
                "maximum_research_data_bytes",
                ResearchDataStore.DEFAULT_MAXIMUM_RESEARCH_DATA_BYTES,
            )
        ),
        minimum_free_bytes=int(
            storage.get(
                "minimum_free_bytes", ResearchDataStore.DEFAULT_MINIMUM_FREE_BYTES
            )
        ),
    )
    data_config = _yaml_mapping(repo_root / "config" / "data.yaml")
    request = data_config.get("data", {}).get("request", {})
    if not isinstance(request, dict):
        raise SystemExit("config/data.yaml data.request must be a mapping")
    provider = BaoStockDataProvider() if allow_network else None
    return HistoricalBackfillCoordinator(
        store,
        provider,
        retry_count=int(request.get("retry_count", 3)),
        delay_seconds=float(request.get("delay_seconds", 0.25)),
    )


def _yaml_mapping(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise SystemExit(f"cannot load required project config: {path.name}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"project config must be a mapping: {path.name}")
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
