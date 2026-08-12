"""Run paper-only price guidance acceptance and fail-closed replay checks."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from a_share_quant.advisory.price_overlay import PriceGuidanceOverlay
from a_share_quant.runtime.price_guidance import PriceGuidanceRuntime
from a_share_quant.storage.price_guidance_store import PriceGuidanceStore


def run_acceptance(
    *,
    workspace: Path,
    bars_by_symbol: dict[str, pd.DataFrame],
    calculation_date: date,
    valid_for: date,
) -> dict[str, Any]:
    workspace = Path(workspace).resolve()
    store = PriceGuidanceStore(workspace / "price-guidance.json")
    runtime = PriceGuidanceRuntime(
        bars_by_symbol=bars_by_symbol,
        store=store,
        candidate_symbols=tuple(bars_by_symbol),
        holding_positions=tuple({"symbol": symbol} for symbol in bars_by_symbol),
    )
    result = runtime.generate(calculation_date, valid_for)
    before = {plan.plan_id: plan.to_dict() for plan in result.plans}
    overlay = PriceGuidanceOverlay()
    failure_closed = True
    for plan in result.plans:
        if plan.entry_lower is None:
            continue
        observation = overlay.evaluate(
            plan,
            {
                "current_price": str(plan.maximum_acceptable_price),
                "quote_timestamp": datetime.combine(
                    calculation_date,
                    datetime.min.time(),
                    tzinfo=timezone.utc,
                ).isoformat(),
                "observed_at": datetime.combine(
                    calculation_date,
                    datetime.min.time(),
                    tzinfo=timezone.utc,
                ).isoformat(),
                "data_quality": "GOOD",
            },
            now=datetime.combine(calculation_date, datetime.min.time(), tzinfo=timezone.utc),
        )
        failure_closed &= observation.state.value in {
            "RESEARCH_REFERENCE",
            "WAIT_FOR_PRICE",
            "PRICE_TOO_HIGH",
        }
        failed = overlay.evaluate(
            plan,
            {"current_price": None, "data_quality": "FAILED"},
            now=datetime.combine(calculation_date, datetime.min.time(), tzinfo=timezone.utc),
        )
        failure_closed &= failed.state.value == "NO_RELIABLE_GUIDANCE"
    after = {plan.plan_id: plan.to_dict() for plan in store.plans()}
    return {
        "status": "PASS" if result.plans and failure_closed else "FAIL",
        "candidate_plans": sum(plan.plan_type.value == "DAILY_CANDIDATE" for plan in result.plans),
        "holding_plans": sum(plan.plan_type.value == "HOLDING" for plan in result.plans),
        "research_quantities_are_zero": all(plan.suggested_quantity == 0 for plan in result.plans),
        "frozen_boundaries_unchanged": before == after,
        "manual_execution_required": all(plan.manual_execution_required for plan in result.plans),
        "order_capability_present": False,
        "failure_closed": failure_closed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument(
        "--bars",
        type=Path,
        required=True,
        help="directory of symbol parquet files",
    )
    parser.add_argument("--calculation-date", required=True)
    parser.add_argument("--valid-for", required=True)
    args = parser.parse_args(argv)
    bars = {
        path.stem: pd.read_parquet(path)
        for path in args.bars.resolve().glob("*.parquet")
        if path.is_file()
    }
    result = run_acceptance(
        workspace=args.workspace,
        bars_by_symbol=bars,
        calculation_date=date.fromisoformat(args.calculation_date),
        valid_for=date.fromisoformat(args.valid_for),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
