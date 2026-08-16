"""Operator CLI for the local paper-only quant workbench."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from a_share_quant.account.import_inbox import AccountImportInbox
from a_share_quant.account.snapshot_store import AccountSnapshotStore
from a_share_quant.config import Settings
from a_share_quant.data.providers.baostock import BaoStockDataProvider
from a_share_quant.data.realtime.diagnostics import (
    collect_network_diagnostics,
    write_network_diagnostics_report,
)
from a_share_quant.research.daily_candidates import (
    generate_from_data_root,
    load_name_map,
    model_bundle_digest,
)
from a_share_quant.research.evolution import EvolutionRegistry
from a_share_quant.research.prospective_competition import ProspectiveContest
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
    research_subcommands.add_parser(
        "contest-start", help="冻结未来预测竞赛版本；结果只能由未来观测决定"
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
            # Screen execution belongs exclusively to the workbench supervisor.
            # The CLI remains a read-only operator inspection route.
            print(json.dumps(_research_screen_status(repo_root), ensure_ascii=False, indent=2))
            return 0
        payload = _freeze_contest(repo_root)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if args.command == "history":
        repo_root = _project_root()
        if args.history_command == "backfill":
            if not args.network:
                raise SystemExit("history backfill requires explicit --network authorization")
            raise SystemExit(
                "历史回填仅由带 --network 的工作台受监督生命周期执行；"
                "命令行不再直接创建数据抓取进程"
            )
        coordinator = _create_history_coordinator(
            repo_root,
            allow_network=False,
        )
        targets = {
            "网络访问": False,
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
    if args.command == "workbench":
        repo_root = _project_root()
        official_signal_path = _inside(repo_root, args.official_signal_path)
        account_import_dir = _operator_path(repo_root, args.account_import_dir)
        account_snapshot_path = _operator_path(repo_root, args.account_snapshot_path)
        price_guidance_path = _inside(repo_root, args.price_guidance_path)
        research_checkpoint_path = _inside(repo_root, args.research_checkpoint)
        storage_policy = ProjectStoragePolicy(repo_root, required_drive="D:")
        network_enabled = bool(args.network and not args.offline)
        research_supervisor = ResearchJobSupervisor(
            research_checkpoint_path.parent,
            storage_policy=storage_policy,
            network_enabled=network_enabled,
        )
        governance = EvolutionRegistry(
            state_path=repo_root / ".runtime" / "research" / "evolution-registry.json"
        )
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
            allow_network=network_enabled,
            advisory_service=advisory_service,
            official_signal_store=official_signal_store,
            price_guidance_store=price_guidance_store,
            supervisor=research_supervisor,
            governance=governance,
            research_context_supplier=lambda tick_now, _service: _workbench_research_context(
                repo_root, tick_now
            ),
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
        network_enabled=False,
        create_root=False,
    )
    contest_path = policy.authorize(".runtime/research/prospective-contest.json")
    contest: dict[str, object] | None = None
    if contest_path.exists():
        try:
            policy.revalidate(contest_path)
            contest = _read_local_json(contest_path)
            _verify_frozen_contest(contest)
        except SystemExit:
            contest = {"状态": "冻结文件校验失败"}
    return {
        "状态": "离线",
        "网络访问": False,
        "D盘项目根": str(policy.repo_root),
        "检查点": str(checkpoint_path),
        "可恢复任务": list(supervisor.resume_eligible_jobs()),
        "未来竞赛": contest,
    }


def _research_screen_status(repo_root: Path) -> dict[str, object]:
    """Read the latest screen artifact; never create a worker from the CLI."""

    policy = ProjectStoragePolicy(repo_root, required_drive="D:")
    directory = policy.authorize(".runtime/research/status")
    if not directory.exists():
        return {
            "状态": "等待工作台调度",
            "网络访问": False,
            "说明": "工程筛查仅在打开的工作台生命周期内由受监督子进程执行。",
            "晋级": "禁止",
        }
    try:
        policy.revalidate(directory)
        records: list[tuple[str, dict[str, object]]] = []
        for path in directory.glob("screen-*.json"):
            policy.revalidate(path)
            if not path.is_file():
                continue
            payload = _read_local_json(path)
            if payload.get("job") != "screen" or payload.get("job_id") != path.stem:
                continue
            timestamp = str(payload.get("updated_at", ""))
            records.append((timestamp, payload))
    except (OSError, SystemExit, ValueError):
        records = []
    if not records:
        return {
            "状态": "等待工作台调度",
            "网络访问": False,
            "说明": "工程筛查仅在打开的工作台生命周期内由受监督子进程执行。",
            "晋级": "禁止",
        }
    _, payload = max(records, key=lambda item: item[0])
    return {
        **payload,
        "来源": "工作台受监督任务",
        "晋级": "禁止",
    }


def _freeze_contest(repo_root: Path) -> dict[str, object]:
    """Freeze verified model provenance for future-only observation."""

    policy = ProjectStoragePolicy(repo_root, required_drive="D:")
    destination = policy.authorize(".runtime/research/prospective-contest.json")
    if destination.exists():
        policy.revalidate(destination)
        existing = _read_local_json(destination)
        _verify_frozen_contest(existing)
        return existing
    registration = _derived_model_registration(repo_root, policy)
    terms = _require_fixed_contest_terms(_contest_terms(repo_root))
    now = datetime.now(timezone.utc)
    contest = ProspectiveContest(
        now=now,
        provisional_sessions=terms["provisional_sessions"],
        provisional_predictions=terms["provisional_matured_predictions"],
        approval_sessions=terms["approval_sessions"],
        approval_predictions=terms["approval_matured_predictions"],
    ).start(
        model_id=registration["model_id"],
        model_version=registration["model_version"],
        config_hash=registration["config_hash"],
        training_snapshot_hash=registration["training_snapshot_hash"],
        model_bundle_digest=registration.get("model_bundle_digest"),
        primary_metric=terms["primary_metric"],
        tie_break=terms["tie_break"],
        started_at=now,
    )
    state = contest.to_dict()
    body: dict[str, object] = {
        "format_version": 3,
        "contest_started_at": state["contest_started_at"],
        "model_id": registration["model_id"],
        "model_version": registration["model_version"],
        "config_hash": registration["config_hash"],
        "training_snapshot_hash": registration["training_snapshot_hash"],
        "official_signal_digest": registration["official_signal_digest"],
        "primary_metric": terms["primary_metric"],
        "tie_break": terms["tie_break"],
        "provisional_sessions": 20,
        "provisional_matured_predictions": 100,
        "approval_sessions": 60,
        "approval_matured_predictions": 200,
        "evidence_mode": "PROSPECTIVE_ONLY",
        "status": "PROSPECTIVE_COLLECTING",
        "promotion": "NEVER",
    }
    if registration.get("model_bundle_digest"):
        body["model_bundle_digest"] = registration["model_bundle_digest"]
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    payload = {**body, "sha256": hashlib.sha256(encoded).hexdigest()}
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    policy.revalidate(destination.parent)
    destination.parent.mkdir(parents=True, exist_ok=True)
    policy.revalidate(destination.parent)
    # First writer wins.  There is deliberately no temporary replace: an
    # existing contest is immutable and a concurrent caller may only validate
    # the object written by the process that won this exclusive create.
    try:
        policy.revalidate(destination)
        with destination.open("xb") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        policy.revalidate(destination)
        return payload
    except FileExistsError:
        policy.revalidate(destination)
        existing = _read_local_json(destination)
        _verify_frozen_contest(existing)
        return existing
    except OSError as exc:
        raise SystemExit("未来竞赛冻结文件无法安全创建") from exc


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
    required = {
        "format_version",
        "contest_started_at",
        "model_id",
        "model_version",
        "config_hash",
        "training_snapshot_hash",
        "official_signal_digest",
        "primary_metric",
        "tie_break",
        "provisional_sessions",
        "provisional_matured_predictions",
        "approval_sessions",
        "approval_matured_predictions",
        "evidence_mode",
        "status",
        "promotion",
    }
    optional = {"model_bundle_digest"}
    if (
        not set(body).issubset(required | optional)
        or not required.issubset(body)
        or body.get("format_version") != 3
    ):
        raise SystemExit("未来竞赛冻结文件字段无效")
    if (
        body.get("provisional_sessions") != 20
        or body.get("provisional_matured_predictions") != 100
        or body.get("approval_sessions") != 60
        or body.get("approval_matured_predictions") != 200
        or body.get("evidence_mode") != "PROSPECTIVE_ONLY"
        or body.get("status") != "PROSPECTIVE_COLLECTING"
        or body.get("primary_metric") != "net_cost_return"
        or body.get("tie_break")
        != ["max_drawdown", "brier", "ece", "rank_ic", "turnover"]
        or body.get("promotion") != "NEVER"
    ):
        raise SystemExit("未来竞赛冻结门槛无效")
    try:
        started = datetime.fromisoformat(str(body["contest_started_at"]))
        if started.tzinfo is None or started.utcoffset() is None:
            raise ValueError
        for key in ("config_hash", "training_snapshot_hash", "official_signal_digest"):
            _sha256_value(body[key], key)
        model_bundle = body.get("model_bundle_digest")
        if model_bundle is not None:
            _sha256_value(model_bundle, "model_bundle_digest")
        model_id = _frozen_text(body["model_id"], "model_id")
        model_version = _frozen_text(body["model_version"], "model_version")
        primary_metric = _frozen_text(body["primary_metric"], "primary_metric")
        tie_break = body["tie_break"]
        if not isinstance(tie_break, list) or not tie_break:
            raise ValueError
        contest = ProspectiveContest(now=max(datetime.now(timezone.utc), started))
        contest.start(
            model_id=model_id,
            model_version=model_version,
            config_hash=_sha256_value(body["config_hash"], "config_hash"),
            training_snapshot_hash=_sha256_value(
                body["training_snapshot_hash"], "training_snapshot_hash"
            ),
            model_bundle_digest=(
                _sha256_value(model_bundle, "model_bundle_digest")
                if model_bundle is not None
                else None
            ),
            primary_metric=primary_metric,
            tie_break=tuple(_frozen_text(item, "tie_break") for item in tie_break),
            started_at=started,
        )
    except (TypeError, ValueError) as exc:
        raise SystemExit("未来竞赛冻结文件字段无效") from exc


def _workbench_research_context(repo_root: Path, now: datetime) -> dict[str, object]:
    """Derive lifecycle facts from fixed local research artifacts only."""

    policy = ProjectStoragePolicy(repo_root, required_drive="D:")
    fingerprint = _verified_research_manifest_digest(policy)
    session_completed = _completed_trading_session(now)
    data_refreshed = _fresh_daily_signal_available(policy, now)
    outcome_cutoff = None
    contest_path = policy.authorize(".runtime/research/prospective-contest.json")
    # A frozen model is not itself a forecast input.  The supervisor receives
    # a predict window only after this session's verified official daily
    # artifact is present, so a BLOCKED pre-refresh child cannot consume the
    # one allowed job instance for that daily cycle.
    if data_refreshed and contest_path.exists():
        try:
            policy.revalidate(contest_path)
            _verify_frozen_contest(_read_local_json(contest_path))
            shanghai = now.astimezone(ZoneInfo("Asia/Shanghai"))
            outcome_cutoff = (
                datetime.combine(
                    shanghai.date() + timedelta(days=1), time.min, tzinfo=shanghai.tzinfo
                ).astimezone(timezone.utc)
            )
        except (OSError, SystemExit, ValueError):
            outcome_cutoff = None
    return {
        "session_completed": session_completed,
        "data_fingerprint": fingerprint,
        "data_refreshed": data_refreshed,
        "outcome_cutoff": outcome_cutoff,
    }


def _derived_model_registration(
    repo_root: Path, policy: ProjectStoragePolicy
) -> dict[str, str]:
    """Derive immutable provenance from verified D-drive research artifacts."""

    signals, signal_digest = _verified_official_signal_artifact(policy)
    try:
        models = {(signal.strategy_version, signal.model_version) for signal in signals}
        if len(models) != 1:
            raise ValueError
        model_id, model_version = next(iter(models))
        registration = {
            "model_id": _frozen_text(model_id, "model_id"),
            "model_version": _frozen_text(model_version, "model_version"),
            "official_signal_digest": _sha256_value(
                signal_digest, "official_signal_digest"
            ),
        }
    except (TypeError, ValueError) as exc:
        raise SystemExit("尚无已核验模型登记，不能开始未来竞赛") from exc
    config_path = policy.authorize(repo_root / "config" / "strategy.yaml")
    policy.revalidate(config_path)
    registration["config_hash"] = hashlib.sha256(config_path.read_bytes()).hexdigest()
    bundle_digest = model_bundle_digest(repo_root)
    if any(signal.model_bundle_digest != bundle_digest for signal in signals):
        raise SystemExit("官方日选与当前模型包摘要不一致，不能开始未来竞赛")
    registration["model_bundle_digest"] = bundle_digest
    snapshot_hash = _verified_research_manifest_digest(policy)
    if snapshot_hash is None:
        raise SystemExit("模型登记训练快照未通过D盘研究数据校验")
    registration["training_snapshot_hash"] = snapshot_hash
    return registration


def _contest_terms(repo_root: Path) -> dict[str, object]:
    maturity = _yaml_mapping(repo_root / "config" / "research_maturity.yaml")
    raw = maturity.get("prospective_competition")
    if not isinstance(raw, dict):
        raise SystemExit("未来竞赛配置无效")
    tie_break = raw.get("tie_break")
    terms = {
        "primary_metric": _frozen_text(raw.get("primary_metric"), "primary_metric"),
        "tie_break": [
            _frozen_text(item, "tie_break")
            for item in tie_break
        ] if isinstance(tie_break, list) else [],
        "provisional_sessions": raw.get("provisional_sessions"),
        "provisional_matured_predictions": raw.get("provisional_matured_predictions"),
        "approval_sessions": raw.get("approval_sessions"),
        "approval_matured_predictions": raw.get("approval_matured_predictions"),
    }
    return _require_fixed_contest_terms(terms)


def _require_fixed_contest_terms(terms: object) -> dict[str, object]:
    """Reject configuration drift before any immutable contest is created."""

    if not isinstance(terms, dict):
        raise SystemExit("未来竞赛固定指标与门槛无效")
    expected_tie_break = ["max_drawdown", "brier", "ece", "rank_ic", "turnover"]
    if (
        terms.get("primary_metric") != "net_cost_return"
        or terms.get("tie_break") != expected_tie_break
        or tuple(
            terms.get(key)
            for key in (
                "provisional_sessions",
                "provisional_matured_predictions",
                "approval_sessions",
                "approval_matured_predictions",
            )
        )
        != (20, 100, 60, 200)
    ):
        raise SystemExit("未来竞赛固定指标与门槛无效")
    return {
        "primary_metric": "net_cost_return",
        "tie_break": expected_tie_break,
        "provisional_sessions": 20,
        "provisional_matured_predictions": 100,
        "approval_sessions": 60,
        "approval_matured_predictions": 200,
    }


def _verified_research_manifest_digest(policy: ProjectStoragePolicy) -> str | None:
    """Accept only a mature coverage checkpoint backed by verified artifacts."""

    try:
        store = ResearchDataStore(policy)
        coverage = HistoricalBackfillCoordinator(store, None).coverage()
        if coverage.symbol_count < 30 or coverage.session_count < 1750:
            return None
        policy.revalidate(store.manifest_path)
        return hashlib.sha256(store.manifest_path.read_bytes()).hexdigest()
    except Exception:
        return None


def _verified_official_signal_artifact(
    policy: ProjectStoragePolicy,
) -> tuple[tuple[object, ...], str]:
    """Read a fixed daily signal artifact and return its integrity digest."""

    path = policy.authorize(".runtime/signals/official-daily.json")
    try:
        policy.revalidate(path)
        raw = path.read_bytes()
        if not raw:
            raise ValueError
        digest = hashlib.sha256(raw).hexdigest()
        signals = OfficialSignalStore(path=path).latest()
        policy.revalidate(path)
        if not signals or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError
        return tuple(signals), digest
    except (OSError, TypeError, ValueError) as exc:
        raise SystemExit("尚无已核验模型登记，不能开始未来竞赛") from exc


def _fresh_daily_signal_available(policy: ProjectStoragePolicy, now: datetime) -> bool:
    """Use only a current, parseable paper-only official artifact as refresh proof."""

    try:
        signals, _ = _verified_official_signal_artifact(policy)
        session_date = now.astimezone(ZoneInfo("Asia/Shanghai")).date()
        return bool(
            signals
            and all(
                signal.signal_date == session_date
                and signal.data_cutoff == signal.signal_date
                and signal.generated_at <= now
                for signal in signals
            )
        )
    except SystemExit:
        return False


def _completed_trading_session(now: datetime) -> bool:
    local = now.astimezone(ZoneInfo("Asia/Shanghai"))
    return local.weekday() < 5 and local.timetz().replace(tzinfo=None) >= time(15, 10)


def _frozen_text(value: object, field: str) -> str:
    normalized = str(value).strip()
    if (
        not normalized
        or len(normalized) > 128
        or any(token in normalized for token in ("..", "/", "\\", "--"))
    ):
        raise ValueError(f"{field} is invalid")
    return normalized


def _sha256_value(value: object, field: str) -> str:
    normalized = str(value).strip().lower()
    if len(normalized) != 64 or any(item not in "0123456789abcdef" for item in normalized):
        raise ValueError(f"{field} must be sha256")
    return normalized


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
