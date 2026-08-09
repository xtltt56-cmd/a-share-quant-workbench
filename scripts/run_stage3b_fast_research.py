"""Run the deterministic Stage 3B candidate-strategy research matrix."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from a_share_quant.analysis.fast_research_report import write_fast_research_report
from a_share_quant.backtest.fast import FastResearchEngine, ResearchPeriod
from a_share_quant.contracts.stage3 import ExecutionSpec, SignalFrame
from a_share_quant.experiments.baseline_manifest import BaselineManifest
from a_share_quant.experiments.pipeline import make_fixture_data
from a_share_quant.integrations.vectorbt import check_vectorbt_available
from a_share_quant.strategies import (
    EqualRankEnsemble,
    EveryNDays,
    PortfolioSpec,
    RankWeighted,
    TopKDropout,
    TopKEqualWeight,
    TopKScoreWeight,
)


def _repo_path(repo_root: Path, path: Path) -> Path:
    candidate = path if path.is_absolute() else repo_root / path
    resolved = candidate.resolve()
    root = repo_root.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"output path is outside repository: {path}")
    return resolved


def _source_commit(repo_root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _load_config(repo_root: Path) -> dict[str, Any]:
    path = repo_root / "config" / "backtest.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load_signals(
    repo_root: Path,
    manifest: BaselineManifest,
) -> dict[str, SignalFrame]:
    signals: dict[str, SignalFrame] = {}
    for entry in manifest.entries:
        predictions = pd.read_parquet(repo_root / entry.prediction_artifact_path)
        predictions["strategy_id"] = entry.strategy_id
        predictions["strategy_version"] = entry.strategy_version
        predictions["model_version"] = entry.model_version
        predictions["feature_version"] = entry.feature_version
        predictions["experiment_id"] = entry.experiment_id
        sessions = sorted(pd.to_datetime(predictions["signal_date"]).dt.date.unique().tolist())
        signals[entry.strategy_id] = SignalFrame.from_predictions(
            predictions,
            experiment_id=entry.experiment_id,
            trading_dates=sessions,
            data_mode=entry.data_mode,
        )
    return signals


def _fixture_market_data(signals: dict[str, SignalFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    frames = [signal.to_frame() for signal in signals.values()]
    all_signals = pd.concat(frames, ignore_index=True)
    dates = pd.to_datetime(all_signals["date"])
    symbols = tuple(sorted(all_signals["symbol"].astype(str).unique().tolist()))
    periods = max(int((dates.max() - dates.min()).days * 1.7), len(dates.unique()) + 5)
    bars, _ = make_fixture_data(
        start_date=dates.min().date(),
        periods=periods,
        symbols=symbols,
        benchmark="000300",
    )
    benchmark = bars.loc[bars["symbol"] == "000300", ["date", "close"]].copy()
    return bars, benchmark


def _execution_spec(signal_frame: SignalFrame, costs: dict[str, Any]) -> ExecutionSpec:
    first = signal_frame.to_frame().sort_values("date").iloc[0]
    return ExecutionSpec(
        signal_date=first["date"],
        execution_date=first["intended_execution_date"],
        execution_price_rule="open",
        slippage=float(costs.get("slippage_bps", 5)) / 10_000,
        commission=float(costs.get("commission_rate", 0.0003)),
        tax=float(costs.get("stamp_duty_rate", 0.0005)),
        t_plus_one=bool(costs.get("enforce_t_plus_one", True)),
        limit_rule=bool(costs.get("enforce_limit_rules", True)),
        suspension_rule=bool(costs.get("enforce_suspension_rules", True)),
        minimum_order_size=100,
        cash_constraint=True,
    )


def _portfolio_spec(top_k: int, rebalance_days: int) -> PortfolioSpec:
    return PortfolioSpec(
        top_k=top_k,
        target_gross_exposure=0.60,
        max_single_position=0.15,
        max_positions=10,
        cash_buffer=0.40,
        rebalance_policy=EveryNDays(rebalance_days),
        n_drop=1,
    )


def _run_one(
    signal: SignalFrame,
    strategy: object,
    *,
    top_k: int,
    rebalance_days: int,
    bars: pd.DataFrame,
    benchmark: pd.DataFrame,
    execution: ExecutionSpec,
) -> dict[str, Any]:
    start = min(signal.to_frame()["date"])
    end = max(bars["date"])
    result = FastResearchEngine(prefer_vectorbt=True).run(
        signals=signal,
        strategy=strategy,
        portfolio_spec=_portfolio_spec(top_k, rebalance_days),
        execution_spec=execution,
        market_data=bars,
        benchmark=benchmark,
        period=ResearchPeriod(start, end, data_mode=signal.data_mode),
    )
    return {
        "metrics": dict(result.metrics),
        "engine": result.engine,
        "engine_version": result.engine_version,
        "warnings": list(result.warnings),
        "data_mode": result.data_mode,
        "result": result,
    }


def _row(
    *,
    strategy: str,
    top_k: int,
    rebalance_days: int,
    run: dict[str, Any],
    model: str | None = None,
    selection_status: str = "CANDIDATE",
) -> dict[str, Any]:
    metrics = run["metrics"]
    return {
        "model": model,
        "strategy": strategy,
        "top_k": top_k,
        "rebalance_days": rebalance_days,
        "cagr": metrics.get("cagr"),
        "total_return": metrics.get("total_return"),
        "sharpe": metrics.get("sharpe"),
        "sortino": metrics.get("sortino"),
        "max_drawdown": metrics.get("max_drawdown"),
        "calmar": metrics.get("calmar"),
        "volatility": metrics.get("volatility"),
        "turnover": metrics.get("turnover"),
        "raw_turnover": metrics.get("raw_turnover"),
        "normalized_turnover": metrics.get("normalized_turnover"),
        "estimated_transaction_cost": metrics.get("estimated_transaction_cost"),
        "selection_status": selection_status,
    }


def _historical_dry_run(repo_root: Path, symbols: set[str], benchmark: str) -> dict[str, Any]:
    missing: list[str] = []
    bars_path = repo_root / "data" / "lake" / "daily_bars"
    instrument_path = repo_root / "data" / "lake" / "instruments"
    files = sorted(bars_path.glob("*.parquet"))
    available_symbols: set[str] = set()
    if not files:
        missing.append(f"historical daily bars under {bars_path}")
    else:
        for path in files:
            try:
                available_symbols.update(
                    pd.read_parquet(path, columns=["symbol"])["symbol"].astype(str).tolist()
                )
            except (OSError, ValueError, KeyError):
                missing.append(f"readable schema in {path}")
        absent_symbols = sorted(symbols.difference(available_symbols))
        if absent_symbols:
            missing.append(f"historical bars for candidate symbols: {', '.join(absent_symbols)}")
        if benchmark not in available_symbols:
            missing.append(f"historical benchmark snapshot: {benchmark}")
    if not list(instrument_path.glob("*.parquet")):
        missing.append(f"historical PIT instrument snapshots under {instrument_path}")
    if missing:
        return {
            "status": "NOT_RUN",
            "missing": missing,
            "note": "Historical dry run was not promoted or replaced by fixture results.",
        }
    return {
        "status": "AVAILABLE_BUT_NOT_RUN",
        "missing": [],
        "note": "Local historical inputs exist; a separate operator-approved run is required.",
    }


def run(repo_root: Path, manifest_path: Path) -> dict[str, Any]:
    manifest = BaselineManifest.load(manifest_path)
    manifest.verify(repo_root=repo_root)
    modes = sorted({entry.data_mode for entry in manifest.entries})
    if modes != ["fixture"]:
        raise ValueError("Stage 3B fixture matrix requires a fixture-only manifest")
    signals = _load_signals(repo_root, manifest)
    bars, benchmark = _fixture_market_data(signals)
    config = _load_config(repo_root)["backtest"]["transaction_costs"]
    availability = check_vectorbt_available()
    strategies = {
        "topk_equal_weight": TopKEqualWeight(),
        "topk_score_weight": TopKScoreWeight(),
        "rank_weighted": RankWeighted(),
        "topk_dropout": TopKDropout(),
    }
    execution = _execution_spec(next(iter(signals.values())), config)
    top_k_values = [10, 20, 30, 50]
    rebalance_values = [1, 5, 10, 20]
    matrix: list[dict[str, Any]] = []
    parameter_surface: list[dict[str, Any]] = []
    for model, signal in signals.items():
        status = "ENSEMBLE_INELIGIBLE" if "rule" in model else "CANDIDATE"
        for strategy_name, strategy in strategies.items():
            default_run = _run_one(
                signal,
                strategy,
                top_k=20,
                rebalance_days=1,
                bars=bars,
                benchmark=benchmark,
                execution=execution,
            )
            matrix.append(
                _row(
                    model=model,
                    strategy=strategy_name,
                    top_k=20,
                    rebalance_days=1,
                    run=default_run,
                    selection_status=status,
                )
            )
            for top_k in top_k_values:
                for rebalance_days in rebalance_values:
                    grid_run = _run_one(
                        signal,
                        strategy,
                        top_k=top_k,
                        rebalance_days=rebalance_days,
                        bars=bars,
                        benchmark=benchmark,
                        execution=execution,
                    )
                    parameter_surface.append(
                        _row(
                            model=model,
                            strategy=strategy_name,
                            top_k=top_k,
                            rebalance_days=rebalance_days,
                            run=grid_run,
                            selection_status=status,
                        )
                    )

    topk_comparison = [row for row in parameter_surface if row["rebalance_days"] == 1]
    rebalance_comparison = [row for row in parameter_surface if row["top_k"] == 20]
    aggregate: list[dict[str, Any]] = []
    for strategy_name in strategies:
        row = next(
            row
            for row in matrix
            if row["strategy"] == strategy_name
            and row["model"] == "qlib_lightgbm_alpha158"
        )
        aggregate.append(row)
    ensemble_inputs = [
        signal for model, signal in signals.items() if "rule" not in model
    ]
    ensemble_result = EqualRankEnsemble().combine(ensemble_inputs, data_mode="fixture")
    ensemble_run = _run_one(
        ensemble_result,
        TopKEqualWeight(),
        top_k=20,
        rebalance_days=1,
        bars=bars,
        benchmark=benchmark,
        execution=execution,
    )
    ensemble_row = _row(
        strategy="equal_rank_ensemble_fixture",
        top_k=20,
        rebalance_days=1,
        run=ensemble_run,
        selection_status="FIXTURE_PIPELINE_ONLY",
    )
    dry_run = _historical_dry_run(
        repo_root,
        set(pd.concat([signal.to_frame() for signal in signals.values()])["symbol"]),
        "000300",
    )
    best_by_model: dict[str, dict[str, Any]] = {}
    for model in signals:
        candidates = [
            row
            for row in matrix
            if row["model"] == model and row["selection_status"] == "CANDIDATE"
        ]
        if candidates:
            best_by_model[model] = max(
                candidates, key=lambda row: float(row["sharpe"] or -1e9)
            )
        else:
            best_by_model[model] = {
                "model": model,
                "selection_status": "ENSEMBLE_INELIGIBLE",
                "reason": "current fixture signal monotonicity is weak",
            }
    equal_vs_score: dict[str, dict[str, float | str]] = {}
    for model in ("qlib_lightgbm_alpha158", "qlib_double_ensemble_alpha158"):
        equal = next(
            row
            for row in matrix
            if row["model"] == model and row["strategy"] == "topk_equal_weight"
        )
        score = next(
            row
            for row in matrix
            if row["model"] == model and row["strategy"] == "topk_score_weight"
        )
        equal_vs_score[model] = {
            "cagr_delta_score_minus_equal": float(score["cagr"] - equal["cagr"]),
            "turnover_delta_score_minus_equal": float(score["turnover"] - equal["turnover"]),
            "cost_delta_score_minus_equal": float(
                score["estimated_transaction_cost"] - equal["estimated_transaction_cost"]
            ),
        }
    metadata = {
        "data_mode": "fixture",
        "source_commit": _source_commit(repo_root),
        "vectorbt_version": availability.version or "not-installed",
        "vectorbt_reason": availability.reason,
        "candidate_count": len(strategies),
        "model_count": len(signals),
        "best_by_model": best_by_model,
        "equal_weight_vs_score_weight": equal_vs_score,
        "reasonable_zones": {
            "top_k": (
                "Not distinguishable in the four-symbol fixture universe; "
                "historical breadth is required."
            ),
            "rebalance": (
                "Five to ten calendar days is a diagnostic stability zone for "
                "some fixture rows; not a production conclusion."
            ),
        },
        "parameter_island": (
            "not identified: four-symbol fixture universe and no historical evidence"
        ),
    }
    return {
        "metadata": metadata,
        "candidate_strategies": [
            {
                "strategy": name,
                "top_k": ",".join(map(str, top_k_values)),
                "rebalance_days": ",".join(map(str, rebalance_values)),
                "candidate_count": len(strategies),
            }
            for name in strategies
        ],
        "topk_comparison": topk_comparison,
        "rebalance_comparison": rebalance_comparison,
        "turnover_comparison": aggregate,
        "cost_comparison": aggregate,
        "drawdown_comparison": aggregate,
        "parameter_surface": parameter_surface,
        "model_strategy_matrix": matrix,
        "equal_rank_ensemble": [ensemble_row],
        "historical_dry_run": dry_run,
        "limitations": [
            "All Stage 3B numbers use the accepted Stage 2 fixture artifacts and "
            "synthetic fixture bars.",
            "Fixture results are diagnostic only and cannot promote to "
            "FAST_BACKTEST_PASS or PAPER_TRADING.",
            "RuleBasedMultiFactor is retained but marked ENSEMBLE_INELIGIBLE for "
            "this weak fixture signal.",
            "VectorBT is optional; the current machine may use the reference fallback.",
            "No large Optuna search, weighted ensemble, RQAlpha validation, broker, "
            "or live order path is included.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("artifacts/baselines/STAGE2_BASELINE_MANIFEST.json"),
    )
    parser.add_argument(
        "--output", type=Path, default=Path("reports/stage3_fast_research_report.md")
    )
    parser.add_argument(
        "--json-output", type=Path, default=Path("reports/stage3_fast_research_report.json")
    )
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()
    manifest_path = _repo_path(repo_root, args.manifest)
    payload = run(repo_root, manifest_path)
    output = _repo_path(repo_root, args.output)
    json_output = _repo_path(repo_root, args.json_output)
    write_fast_research_report(payload, markdown_path=output, json_path=json_output)
    print(f"wrote {output}")
    print(f"wrote {json_output}")


if __name__ == "__main__":
    main()
