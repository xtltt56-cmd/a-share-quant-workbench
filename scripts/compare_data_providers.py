"""Compare two canonical Parquet snapshots without promoting either source."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import pandas as pd

from a_share_quant.data.providers.comparison import compare_daily_frames
from a_share_quant.data.providers.registry import ProviderRegistry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--incumbent", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--incumbent-name", required=True)
    parser.add_argument("--candidate-name", required=True)
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--evidence", type=Path, default=None)
    args = parser.parse_args()
    comparison = compare_daily_frames(
        pd.read_parquet(args.incumbent),
        pd.read_parquet(args.candidate),
        incumbent=args.incumbent_name,
        candidate=args.candidate_name,
        as_of=args.as_of,
    )
    if args.evidence is not None:
        registry = ProviderRegistry(
            formal_provider=args.incumbent_name,
            evidence_path=args.evidence,
        )
        registry.record_comparison(comparison)
    print(
        json.dumps(
            {
                "incumbent": comparison.incumbent,
                "candidate": comparison.candidate,
                "as_of": comparison.as_of.isoformat(),
                "coverage_ratio": comparison.coverage_ratio,
                "max_timestamp_drift_seconds": comparison.max_timestamp_drift_seconds,
                "adjustment_match": comparison.adjustment_match,
                "identifier_match": comparison.identifier_match,
                "suspension_match": comparison.suspension_match,
                "passes_default_policy": comparison.passes(
                    registry.policy if args.evidence is not None else _default_policy()
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _default_policy():
    from a_share_quant.data.providers.registry import ProviderAcceptancePolicy

    return ProviderAcceptancePolicy()


if __name__ == "__main__":
    raise SystemExit(main())
