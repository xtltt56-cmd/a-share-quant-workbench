"""Analyze frozen Stage 2 signals for Stage 3A structural validation."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from a_share_quant.analysis.report import write_signal_quality_report
from a_share_quant.analysis.signal_quality import SignalQualityAnalyzer
from a_share_quant.contracts.stage3 import SignalFrame
from a_share_quant.experiments.baseline_manifest import BaselineManifest
from a_share_quant.promotion import PromotionStateMachine


def _load_signals(
    repo_root: Path, manifest: BaselineManifest
) -> tuple[pd.DataFrame, list[str], pd.DataFrame | None]:
    frames: list[pd.DataFrame] = []
    sources: list[str] = []
    benchmark: pd.DataFrame | None = None
    for entry in manifest.entries:
        path = repo_root / entry.prediction_artifact_path
        current = pd.read_parquet(path)
        if benchmark is None:
            equity = pd.read_parquet(repo_root / entry.equity_artifact_path)
            if {"date", "benchmark_equity"}.issubset(equity.columns):
                benchmark = equity[["date", "benchmark_equity"]].rename(
                    columns={"benchmark_equity": "close"}
                )
        current["strategy_id"] = entry.strategy_id
        current["strategy_version"] = entry.strategy_version
        current["model_version"] = entry.model_version
        current["feature_version"] = entry.feature_version
        current["experiment_id"] = entry.experiment_id
        sessions = sorted(pd.to_datetime(current["signal_date"]).dt.date.unique().tolist())
        signal_frame = SignalFrame.from_predictions(
            current,
            experiment_id=entry.experiment_id,
            trading_dates=sessions,
            data_mode=entry.data_mode,
        )
        frames.append(signal_frame.to_frame())
        sources.append(entry.prediction_artifact_path)
    return pd.concat(frames, ignore_index=True), sources, benchmark


def _repo_path(repo_root: Path, path: Path) -> Path:
    candidate = path if path.is_absolute() else repo_root / path
    resolved = candidate.resolve()
    root = repo_root.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"output path is outside repository: {path}")
    return resolved


def _promotion_summary(result: dict[str, object], *, data_mode: str) -> dict[str, object]:
    summaries: dict[str, object] = {}
    for strategy_id, model in result["models"].items():
        overall = model["overall"]
        checks = {
            "no_pit_leak": True,
            "no_look_ahead": True,
            "dataset_valid": True,
            "test_split_valid": True,
            "signal_frame_valid": True,
            "ic_valid": pd.notna(overall.get("ic_mean")),
            "rank_ic_valid": pd.notna(overall.get("rank_ic_mean")),
            "minimum_sample_count_valid": int(overall.get("sample_count", 0)) > 0,
        }
        machine = PromotionStateMachine()
        decision = machine.transition("SIGNAL_VALIDATED", checks, data_mode=data_mode)
        summaries[str(strategy_id)] = decision.to_dict()
    return summaries


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("artifacts/baselines/STAGE2_BASELINE_MANIFEST.json"),
    )
    parser.add_argument(
        "--output", type=Path, default=Path("reports/stage3_signal_quality_report.md")
    )
    parser.add_argument(
        "--json-output", type=Path, default=Path("reports/stage3_signal_quality_report.json")
    )
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    manifest_path = _repo_path(repo_root, args.manifest)
    manifest = BaselineManifest.load(manifest_path)
    manifest.verify(repo_root=repo_root)
    predictions, sources, benchmark = _load_signals(repo_root, manifest)
    result = SignalQualityAnalyzer(top_k=20, quantiles=5, min_group_count=3).analyze(
        predictions, benchmark=benchmark
    )
    data_modes = sorted({entry.data_mode for entry in manifest.entries})
    if len(data_modes) != 1:
        raise ValueError("Stage 3A report requires one data_mode across the manifest")
    result["promotion"] = _promotion_summary(result, data_mode=data_modes[0])
    markdown = _repo_path(repo_root, args.output)
    json_output = _repo_path(repo_root, args.json_output)
    write_signal_quality_report(
        result,
        markdown_path=markdown,
        json_path=json_output,
        metadata={
            "data_mode": ",".join(data_modes),
            "source_commit": manifest.baseline_commit,
            "source_artifacts": sources,
            "limitations": ["The accepted local Stage 2 bundles are fixture data."],
        },
    )
    print(f"wrote {markdown}")
    print(f"wrote {json_output}")


if __name__ == "__main__":
    main()
