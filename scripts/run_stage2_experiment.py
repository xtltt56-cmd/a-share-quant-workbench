"""Run the Stage 2 baseline comparison on local or explicitly synthetic data.

The default mode is ``local`` and refuses to fabricate a benchmark or historical
universe.  ``--mode fixture`` is a deterministic integration smoke test only; its
artifacts must never be interpreted as investment performance.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from a_share_quant.config import Stage2Config
from a_share_quant.experiments.evaluator import (
    EvaluationConfig,
    FairEvaluationResult,
    FairPortfolioEvaluator,
)
from a_share_quant.experiments.pipeline import (
    build_rule_features,
    load_historical_universe,
    load_local_daily_bars,
    make_fixture_data,
)
from a_share_quant.experiments.runner import ExperimentRunner, ExperimentSpec
from a_share_quant.experiments.splits import TimeSplit, fixed_time_split, rolling_walk_forward
from a_share_quant.features.rule_factors import RuleFactorEngine
from a_share_quant.features.universe import HistoricalUniverse
from a_share_quant.integrations.qlib.dataset_builder import QlibDatasetBuilder
from a_share_quant.integrations.qlib.model_runner import QlibModelRunner
from a_share_quant.signals.adapter import prediction_frame_to_records
from a_share_quant.signals.rule import RuleBasedSignalProvider

LOGGER = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Stage 2 Qlib baseline experiments")
    parser.add_argument("--mode", choices=("local", "fixture"), default="local")
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--output-root", type=Path, default=Path("experiments/stage2_baseline"))
    parser.add_argument("--provider-root", type=Path, default=Path("data/qlib_provider/stage2"))
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--benchmark", default=None)
    parser.add_argument("--symbols", nargs="+")
    parser.add_argument("--fixture-periods", type=int, default=700)
    parser.add_argument(
        "--skip-walk-forward",
        action="store_true",
        help="only for a quick smoke run; fixed OOS metadata still records the planned windows",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        _run(args)
    except Exception as exc:  # operator-facing entry point; details stay in logs, not secrets
        LOGGER.error("Stage 2 experiment failed: %s: %s", type(exc).__name__, exc)
        return 2
    return 0


def _run(args: argparse.Namespace) -> None:
    repo_root = Path(args.repo_root).resolve()
    config_dir = repo_root / "config"
    stage2 = Stage2Config.load(config_dir=config_dir)
    qlib_raw = _read_yaml(config_dir / "qlib.yaml").get("qlib", {})
    benchmark = args.benchmark or stage2.comparison.benchmark
    bars, universe = _load_run_data(args, benchmark)
    symbols = _select_symbols(bars, benchmark, args.symbols)
    calendar = sorted(pd.to_datetime(bars["date"]).dt.date.unique())
    usable_dates = calendar[:-stage2.label.horizon_days]
    split = fixed_time_split(
        usable_dates,
        train_ratio=stage2.comparison.split.train_ratio,
        validation_ratio=stage2.comparison.split.validation_ratio,
        test_ratio=stage2.comparison.split.test_ratio,
    )
    walk_forward_config = _read_yaml(config_dir / "experiments.yaml").get("comparison", {}).get(
        "walk_forward", {}
    )
    windows = rolling_walk_forward(
        usable_dates,
        min_train_dates=int(walk_forward_config.get("min_train_dates", 252)),
        validation_dates=int(walk_forward_config.get("validation_dates", 63)),
        test_dates=int(walk_forward_config.get("test_dates", 63)),
        step_dates=int(walk_forward_config.get("step_dates", 63)),
    )
    if not windows:
        LOGGER.warning(
            "not enough dates for configured Walk-Forward windows; fixed OOS will still run"
        )

    provider_root = repo_root / args.provider_root
    builder = QlibDatasetBuilder(
        provider_root,
        feature_version=str(qlib_raw.get("feature_version", "alpha158_v1")),
        kernels=1,
    )
    dataset_artifact = builder.build(
        bars,
        universe=universe,
        symbols=symbols,
        benchmark=benchmark,
        start_date=calendar[0],
        end_date=calendar[-1],
        segments=_qlib_segments(split),
    )
    dataset_hash = dataset_artifact.provider.dataset_hash
    base_config = _experiment_config(
        repo_root,
        args,
        benchmark,
        qlib_raw,
        split,
        windows,
    )
    evaluator = FairPortfolioEvaluator(EvaluationConfig.from_yaml(config_dir / "backtest.yaml"))
    artifact_root = repo_root / args.output_root
    experiment_runner = ExperimentRunner(root=artifact_root, repo_root=repo_root)

    rule_features = build_rule_features(bars, benchmark=benchmark)
    rule_engine = RuleFactorEngine.from_yaml(config_dir / "strategy.yaml")
    rule_provider = RuleBasedSignalProvider(rule_engine)
    rule_predictions = _rule_predictions(rule_provider, rule_features, _date_range(split.test))
    rule_result = evaluator.evaluate(rule_predictions, bars, benchmark=benchmark)
    _write_strategy(
        experiment_runner,
        evaluator,
        rule_result,
        strategy_id="rule_multifactor",
        strategy_version=rule_provider.strategy_version,
        model_version=rule_provider.model_version,
        feature_version=rule_engine.feature_version,
        data_version=rule_engine.data_version,
        dataset_hash=dataset_hash,
        seed=stage2.comparison.seed,
        params={"factor_config": str(config_dir / "strategy.yaml")},
        split=split,
        windows=windows,
        config=base_config,
        run_label="oos",
    )

    qlib_runner = QlibModelRunner(
        seed=stage2.comparison.seed,
        experiment_db=repo_root / "data" / "qlib_mlflow.db",
    )
    model_specs = (
        (
            "qlib_lightgbm_alpha158",
            str(qlib_raw.get("model_versions", {}).get("lightgbm", "qlib_lgb_alpha158_v1")),
            dict(qlib_raw.get("models", {}).get("lightgbm", {})),
            "lightgbm",
        ),
        (
            "qlib_double_ensemble_alpha158",
            str(
                qlib_raw.get("model_versions", {}).get(
                    "double_ensemble", "qlib_doubleensemble_alpha158_v1"
                )
            ),
            dict(qlib_raw.get("models", {}).get("double_ensemble", {})),
            "double_ensemble",
        ),
    )
    for strategy_id, model_version, params, model_name in model_specs:
        model_result = qlib_runner.run(
            dataset_artifact,
            model_name=model_name,
            strategy_id=strategy_id,
            strategy_version="stage2-v1",
            model_version=model_version,
            params={**params, "seed": stage2.comparison.seed},
        )
        evaluation_result = evaluator.evaluate(model_result.predictions, bars, benchmark=benchmark)
        _write_strategy(
            experiment_runner,
            evaluator,
            evaluation_result,
            strategy_id=strategy_id,
            strategy_version=model_result.strategy_version,
            model_version=model_result.model_version,
            feature_version=model_result.feature_version,
            data_version=str(bars.get("data_version", pd.Series(["canonical-v1"])).iloc[0]),
            dataset_hash=dataset_hash,
            seed=stage2.comparison.seed,
            params=params,
            split=split,
            windows=windows,
            config=base_config,
            model=model_result.model,
            run_label="oos",
        )

    if windows and not args.skip_walk_forward:
        _run_walk_forward(
            builder=builder,
            bars=bars,
            universe=universe,
            symbols=symbols,
            benchmark=benchmark,
            windows=windows,
            qlib_raw=qlib_raw,
            stage2=stage2,
            rule_features=rule_features,
            rule_provider=rule_provider,
            evaluator=evaluator,
            qlib_runner=qlib_runner,
            experiment_runner=experiment_runner,
            base_config=base_config,
        )
    elif args.skip_walk_forward:
        LOGGER.warning(
            "Walk-Forward execution skipped by operator; fixed OOS artifacts are not a full "
            "robustness result"
        )
    _write_comparison_report(artifact_root, artifact_root, split, rule_result, model_specs)
    LOGGER.info("Stage 2 fixed OOS artifacts written under %s", artifact_root)


def _load_run_data(
    args: argparse.Namespace,
    benchmark: str,
) -> tuple[pd.DataFrame, HistoricalUniverse]:
    if args.mode == "fixture":
        bars, universe_frame = make_fixture_data(
            start_date=args.start_date or "2020-01-01",
            periods=args.fixture_periods,
            symbols=(
                tuple(args.symbols)
                if args.symbols
                else ("000001", "000002", "000003", "000004")
            ),
            benchmark=benchmark,
        )
        return bars, HistoricalUniverse(
            universe_frame,
            exclude_new_days=60,
            min_average_amount=10_000_000,
            min_average_turnover_pct=0.5,
        )
    bars = load_local_daily_bars(
        args.data_root,
        symbols=[*args.symbols, benchmark] if args.symbols else None,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    if benchmark not in set(bars["symbol"]):
        raise ValueError(
            f"local daily bars do not contain benchmark {benchmark}; "
            "ingest an index snapshot before running"
        )
    universe = load_historical_universe(args.data_root)
    first_date = min(bars["date"])
    if universe.tradable_universe(first_date).empty:
        raise ValueError(
            "historical universe has no snapshot visible at the first bar date; "
            "refusing survivor-biased fallback"
        )
    return bars, universe


def _select_symbols(bars: pd.DataFrame, benchmark: str, requested: list[str] | None) -> list[str]:
    if requested:
        symbols = [str(symbol) for symbol in requested if str(symbol) != benchmark]
    else:
        symbols = sorted(set(bars["symbol"]) - {benchmark})
    if len(symbols) < 2:
        raise ValueError(
            "Stage 2 comparison requires at least two tradable symbols plus the benchmark"
        )
    return symbols


def _rule_predictions(
    provider: RuleBasedSignalProvider,
    features: pd.DataFrame,
    dates: list[date],
) -> pd.DataFrame:
    records = []
    allowed_dates = set(dates)
    for signal_date, group in features.groupby("date", sort=True):
        if signal_date not in allowed_dates:
            continue
        records.extend(
            provider.generate_signals(
                group.drop(columns=["date"], errors="ignore"),
                signal_date=signal_date,
                experiment_id="stage2_baseline",
            )
        )
    if not records:
        raise ValueError("rule baseline generated no OOS signals")
    return pd.DataFrame([record.to_dict() for record in records]).drop(columns=["date"])


def _write_strategy(
    runner: ExperimentRunner,
    evaluator: FairPortfolioEvaluator,
    result: FairEvaluationResult,
    *,
    strategy_id: str,
    strategy_version: str,
    model_version: str,
    feature_version: str,
    data_version: str,
    dataset_hash: str,
    seed: int,
    params: dict[str, Any],
    split: TimeSplit,
    windows: list[TimeSplit],
    config: dict[str, Any],
    model: Any | None = None,
    run_label: str = "oos",
    extra_metadata: dict[str, Any] | None = None,
) -> None:
    experiment_id = f"{strategy_id}_{run_label}"
    predictions = result.predictions.copy()
    predictions["strategy_id"] = strategy_id
    predictions["strategy_version"] = strategy_version
    predictions["model_version"] = model_version
    predictions["feature_version"] = feature_version
    signals = pd.DataFrame(
        record.to_dict()
        for record in prediction_frame_to_records(
            predictions,
            data_version=data_version,
            experiment_id=experiment_id,
        )
    )
    spec = ExperimentSpec(
        experiment_id=experiment_id,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        model_version=model_version,
        feature_version=feature_version,
        data_version=data_version,
        dataset_hash=dataset_hash,
        seed=seed,
        config=config,
        params=params,
        split=split,
        walk_forward=tuple(windows),
    )
    runner.write(
        spec,
        result,
        model=model,
        signals=signals,
        extra_metadata=extra_metadata or {"evaluation": "fixed_oos"},
    )


def _run_walk_forward(
    *,
    builder: QlibDatasetBuilder,
    bars: pd.DataFrame,
    universe: HistoricalUniverse,
    symbols: list[str],
    benchmark: str,
    windows: list[TimeSplit],
    qlib_raw: dict[str, Any],
    stage2: Stage2Config,
    rule_features: pd.DataFrame,
    rule_provider: RuleBasedSignalProvider,
    evaluator: FairPortfolioEvaluator,
    qlib_runner: QlibModelRunner,
    experiment_runner: ExperimentRunner,
    base_config: dict[str, Any],
) -> None:
    model_specs = (
        (
            "qlib_lightgbm_alpha158",
            str(qlib_raw.get("model_versions", {}).get("lightgbm", "qlib_lgb_alpha158_v1")),
            dict(qlib_raw.get("models", {}).get("lightgbm", {})),
            "lightgbm",
        ),
        (
            "qlib_double_ensemble_alpha158",
            str(
                qlib_raw.get("model_versions", {}).get(
                    "double_ensemble", "qlib_doubleensemble_alpha158_v1"
                )
            ),
            dict(qlib_raw.get("models", {}).get("double_ensemble", {})),
            "double_ensemble",
        ),
    )
    collected: dict[str, list[pd.DataFrame]] = {"rule_multifactor": []}
    aggregate_dataset_hash: str | None = None
    for strategy_id, _, _, _ in model_specs:
        collected[strategy_id] = []

    for index, window in enumerate(windows):
        LOGGER.info("running Walk-Forward window %s/%s", index + 1, len(windows))
        dataset_artifact = builder.build(
            bars,
            universe=universe,
            symbols=symbols,
            benchmark=benchmark,
            start_date=min(bars["date"]),
            end_date=max(bars["date"]),
            segments=_qlib_segments(window),
        )
        aggregate_dataset_hash = dataset_artifact.provider.dataset_hash
        rule_predictions = _rule_predictions(
            rule_provider,
            rule_features,
            _date_range(window.test),
        )
        collected["rule_multifactor"].append(rule_predictions)
        rule_result = evaluator.evaluate(rule_predictions, bars, benchmark=benchmark)
        _write_strategy(
            experiment_runner,
            evaluator,
            rule_result,
            strategy_id="rule_multifactor",
            strategy_version=rule_provider.strategy_version,
            model_version=rule_provider.model_version,
            feature_version=rule_provider.engine.feature_version,
            data_version=rule_provider.engine.data_version,
            dataset_hash=dataset_artifact.provider.dataset_hash,
            seed=stage2.comparison.seed,
            params={"factor_config": "config/strategy.yaml"},
            split=window,
            windows=windows,
            config={**base_config, "walk_forward_window": window.as_dict()},
            run_label=f"wf_{index:03d}",
            extra_metadata={"evaluation": "walk_forward_window", "window_index": index},
        )
        for strategy_id, model_version, params, model_name in model_specs:
            model_result = qlib_runner.run(
                dataset_artifact,
                model_name=model_name,
                strategy_id=strategy_id,
                strategy_version="stage2-v1",
                model_version=model_version,
                params={**params, "seed": stage2.comparison.seed},
            )
            collected[strategy_id].append(model_result.predictions)
            evaluation_result = evaluator.evaluate(
                model_result.predictions,
                bars,
                benchmark=benchmark,
            )
            _write_strategy(
                experiment_runner,
                evaluator,
                evaluation_result,
                strategy_id=strategy_id,
                strategy_version=model_result.strategy_version,
                model_version=model_result.model_version,
                feature_version=model_result.feature_version,
                data_version=str(
                    bars.get("data_version", pd.Series(["canonical-v1"])).iloc[0]
                ),
                dataset_hash=dataset_artifact.provider.dataset_hash,
                seed=stage2.comparison.seed,
                params=params,
                split=window,
                windows=windows,
                config={**base_config, "walk_forward_window": window.as_dict()},
                model=model_result.model,
                run_label=f"wf_{index:03d}",
                extra_metadata={"evaluation": "walk_forward_window", "window_index": index},
            )

    for strategy_id, predictions_list in collected.items():
        combined_predictions = pd.concat(predictions_list, ignore_index=True)
        combined_result = evaluator.evaluate(combined_predictions, bars, benchmark=benchmark)
        if strategy_id == "rule_multifactor":
            strategy_version = rule_provider.strategy_version
            model_version = rule_provider.model_version
            feature_version = rule_provider.engine.feature_version
            data_version = rule_provider.engine.data_version
            params = {"factor_config": "config/strategy.yaml", "window_count": len(windows)}
        else:
            spec = next(item for item in model_specs if item[0] == strategy_id)
            strategy_version = "stage2-v1"
            model_version = spec[1]
            feature_version = str(qlib_raw.get("feature_version", "alpha158_v1"))
            data_version = str(bars.get("data_version", pd.Series(["canonical-v1"])).iloc[0])
            params = {**spec[2], "window_count": len(windows)}
        _write_strategy(
            experiment_runner,
            evaluator,
            combined_result,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            model_version=model_version,
            feature_version=feature_version,
            data_version=data_version,
            dataset_hash=aggregate_dataset_hash or "unknown",
            seed=stage2.comparison.seed,
            params=params,
            split=windows[-1],
            windows=windows,
            config=base_config,
            run_label="wf",
            extra_metadata={
                "evaluation": "walk_forward_aggregate",
                "window_count": len(windows),
            },
        )
    LOGGER.info("Walk-Forward execution completed for %s windows", len(windows))


def _write_comparison_report(
    root: Path,
    artifact_root: Path,
    split: TimeSplit,
    rule_result: FairEvaluationResult,
    model_specs: tuple[tuple[str, str, dict[str, Any], str], ...],
) -> None:
    rows = [{"strategy_id": "rule_multifactor", **_summary_metrics(rule_result.metrics)}]
    for strategy_id, model_version, _, _ in model_specs:
        metrics_path = artifact_root / f"{strategy_id}_oos" / "metrics.json"
        if metrics_path.exists():
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            rows.append(
                {
                    "strategy_id": strategy_id,
                    "model_version": model_version,
                    **_summary_metrics(metrics),
                }
            )
    report_dir = root / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    table = pd.DataFrame(rows)
    table.to_parquet(root / "comparison_metrics.parquet", index=False)
    lines = [
        "# Stage 2 baseline comparison",
        "",
        "This report is fixed OOS. It is not a live-trading recommendation.",
        "",
        f"- Train: `{split.train[0]}` to `{split.train[1]}`",
        f"- Validation: `{split.validation[0]}` to `{split.validation[1]}`",
        f"- Test: `{split.test[0]}` to `{split.test[1]}`",
        "",
        table.to_markdown(index=False),
        "",
    ]
    (report_dir / "baseline_comparison.md").write_text("\n".join(lines), encoding="utf-8")


def _summary_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    names = (
        "ic",
        "rank_ic",
        "top_k_return",
        "excess_return",
        "hit_rate",
        "cumulative_return",
        "cagr",
        "max_drawdown",
        "sharpe",
        "sortino",
        "calmar",
        "profit_factor",
        "payoff_ratio",
        "turnover",
        "max_consecutive_loss",
        "benchmark_cumulative_return",
        "benchmark_excess_return",
    )
    return {name: metrics.get(name) for name in names}


def _qlib_segments(split: TimeSplit) -> dict[str, tuple[date, date]]:
    return {"train": split.train, "valid": split.validation, "test": split.test}


def _date_range(bounds: tuple[date, date]) -> list[date]:
    return list(pd.bdate_range(bounds[0], bounds[1]).date)


def _experiment_config(
    repo_root: Path,
    args: argparse.Namespace,
    benchmark: str,
    qlib_raw: dict[str, Any],
    split: TimeSplit,
    windows: list[TimeSplit],
) -> dict[str, Any]:
    return {
        "mode": args.mode,
        "benchmark": benchmark,
        "qlib": qlib_raw,
        "split": split.as_dict(),
        "walk_forward": [window.as_dict() for window in windows],
        "repo_root": str(repo_root),
    }


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle) or {}
    return value if isinstance(value, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
