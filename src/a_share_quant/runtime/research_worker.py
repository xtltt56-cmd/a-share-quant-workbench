"""Bounded research-only worker launched and owned by the local workbench."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from a_share_quant.research.forecasting import train_challengers


def run_forecast(repo_root: Path) -> int:
    daily_root = repo_root / "data" / "lake" / "daily_bars"
    benchmark_path = daily_root / "000300.parquet"
    status_path = repo_root / ".runtime" / "research" / "forecast-status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    symbol_paths = [path for path in sorted(daily_root.glob("*.parquet")) if path != benchmark_path]
    try:
        if not benchmark_path.exists() or len(symbol_paths) < 30:
            raise ValueError("至少需要沪深300基准和30只股票的本地历史数据")
        frames = [pd.read_parquet(path) for path in symbol_paths]
        prices = pd.concat(frames, ignore_index=True)
        benchmark = pd.read_parquet(benchmark_path)
        result = train_challengers(
            prices,
            benchmark,
            artifact_root=repo_root / ".runtime" / "research",
            n_jobs=1,
        )
        payload = {
            "status": result.status,
            "formal_eligible": result.formal_eligible,
            "artifacts": list(result.artifacts),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        exit_code = 0
    except Exception as exc:
        payload = {
            "status": "NOT_TRAINED",
            "reason": str(exc)[:500],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        exit_code = 1
    temporary = status_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(status_path)
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", choices=("forecast", "calibrate", "evaluate"))
    args = parser.parse_args(argv)
    repo_root = Path.cwd().resolve()
    if args.job == "forecast":
        return run_forecast(repo_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
