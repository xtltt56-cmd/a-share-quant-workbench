"""Freeze accepted Stage 2 experiment bundles into a Stage 3 manifest."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from a_share_quant.experiments.baseline_manifest import freeze_stage2_baselines


def _costs(repo_root: Path) -> dict[str, object]:
    path = repo_root / "config" / "backtest.yaml"
    if not path.exists():
        return {}
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    backtest = config.get("backtest", {}) or {}
    return {
        "commission_rate": backtest.get("transaction_costs", {}).get("commission_rate"),
        "stamp_duty_rate": backtest.get("transaction_costs", {}).get("stamp_duty_rate"),
        "transfer_fee_rate": backtest.get("transaction_costs", {}).get("transfer_fee_rate"),
        "slippage_bps": backtest.get("transaction_costs", {}).get("slippage_bps"),
        "enforce_t_plus_one": backtest.get("enforce_t_plus_one"),
        "enforce_limit_rules": backtest.get("enforce_limit_rules"),
        "enforce_suspension_rules": backtest.get("enforce_suspension_rules"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=Path("experiments/stage2_wf_smoke2"),
        help="Stage 2 experiment directory containing *_oos bundles",
    )
    parser.add_argument(
        "--strategy-dir",
        type=Path,
        action="append",
        help="Explicit experiment bundle; may be repeated",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/baselines/STAGE2_BASELINE_MANIFEST.json"),
    )
    parser.add_argument("--benchmark", default="000300")
    parser.add_argument("--baseline-commit", default="a293a13")
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    artifact_root = args.artifact_root
    if not artifact_root.is_absolute():
        artifact_root = repo_root / artifact_root
    roots = args.strategy_dir
    if roots:
        resolved_roots = [path if path.is_absolute() else repo_root / path for path in roots]
    else:
        resolved_roots = sorted(artifact_root.glob("*_oos"))
    if not resolved_roots:
        raise SystemExit("no Stage 2 *_oos bundles found")
    output = args.output if args.output.is_absolute() else repo_root / args.output
    manifest = freeze_stage2_baselines(
        repo_root=repo_root,
        artifact_roots=resolved_roots,
        output=output,
        baseline_commit=args.baseline_commit,
        benchmark=args.benchmark,
        transaction_cost_assumptions=_costs(repo_root),
    )
    print(f"wrote {output}")
    for entry in manifest.entries:
        print(f"- {entry.strategy_id}: {entry.experiment_id} ({entry.data_mode})")


if __name__ == "__main__":
    main()
