"""Generate next-session daily-candidate and holding guidance plans."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from a_share_quant.advisory.price_contracts import (
    GuidanceState,
    PriceGuidancePlan,
    PricePlanType,
)
from a_share_quant.advisory.price_engine import PriceGuidanceEngine
from a_share_quant.features.price_guidance import build_price_features
from a_share_quant.storage.price_guidance_store import PriceGuidanceStore


@dataclass(frozen=True)
class PriceGuidanceRuntimeResult:
    plans: tuple[PriceGuidancePlan, ...]
    calculation_date: date
    valid_for: date


class PriceGuidanceRuntime:
    def __init__(
        self,
        *,
        bars_by_symbol: Mapping[str, pd.DataFrame],
        store: PriceGuidanceStore,
        candidate_symbols: tuple[str, ...] = (),
        holding_positions: tuple[Mapping[str, Any], ...] = (),
        engine: PriceGuidanceEngine | None = None,
    ) -> None:
        self.bars_by_symbol = dict(bars_by_symbol)
        self.store = store
        self.candidate_symbols = tuple(candidate_symbols)
        self.holding_positions = tuple(holding_positions)
        self.engine = engine or PriceGuidanceEngine()

    def generate(
        self,
        calculation_date: date | str,
        valid_for: date | str,
    ) -> PriceGuidanceRuntimeResult:
        calculation = _parse_date(calculation_date)
        valid = _parse_date(valid_for)
        if valid <= calculation:
            raise ValueError("valid_for must be after calculation_date")
        holding_by_symbol = {
            str(item["symbol"]): item for item in self.holding_positions if item.get("symbol")
        }
        requested = {
            (str(symbol), PricePlanType.DAILY_CANDIDATE) for symbol in self.candidate_symbols
        }
        requested.update((symbol, PricePlanType.HOLDING) for symbol in holding_by_symbol)
        plans: list[PriceGuidancePlan] = []
        for symbol, plan_type in sorted(requested):
            bars = self.bars_by_symbol.get(symbol)
            position = holding_by_symbol.get(symbol)
            try:
                if bars is None:
                    raise ValueError("NO_RELIABLE_GUIDANCE")
                features = build_price_features(bars, cutoff=calculation)
                previous = position.get("previous_protection") if position else None
                corporate_action = previous is not None and abs(
                    features.adjustment_factor - Decimal("1")
                ) > Decimal("0.000001")
                plan = self.engine.candidate_plan(
                    features,
                    promoted=False,
                    previous_protection=None if corporate_action else previous,
                    calculation_date=calculation,
                    valid_for=valid,
                )
                if plan_type is PricePlanType.HOLDING:
                    reasons = list(plan.reason_codes)
                    if corporate_action:
                        reasons.append("CORPORATE_ACTION_RECALCULATED")
                    if previous is not None:
                        reasons.append("PREVIOUS_PROTECTION_REBASED")
                    plan = _as_holding_plan(plan, position, tuple(reasons))
                plans.append(plan)
            except Exception as exc:
                plans.append(_unavailable_plan(symbol, plan_type, calculation, valid, _reason(exc)))
        result = PriceGuidanceRuntimeResult(tuple(plans), calculation, valid)
        self.store.replace_plans(result.plans)
        return result


def _as_holding_plan(
    plan: PriceGuidancePlan,
    position: Mapping[str, Any],
    reasons: tuple[str, ...],
) -> PriceGuidancePlan:
    return replace(
        plan,
        plan_id=plan.plan_id.replace("price-", "price-holding-", 1),
        plan_type=PricePlanType.HOLDING,
        reason_codes=reasons,
    )


def _unavailable_plan(
    symbol: str,
    plan_type: PricePlanType,
    calculation: date,
    valid: date,
    reason: str,
) -> PriceGuidancePlan:
    return PriceGuidancePlan(
        plan_id=f"price-{symbol}-{plan_type.value.lower()}-{calculation.isoformat()}",
        symbol=symbol,
        plan_type=plan_type,
        guidance_level="RESEARCH_REFERENCE",
        state=GuidanceState.NO_RELIABLE_GUIDANCE,
        calculation_date=calculation,
        valid_for=valid,
        entry_lower=None,
        entry_upper=None,
        maximum_acceptable_price=None,
        invalidation_price=None,
        protection_price=None,
        reduce_lower=None,
        reduce_upper=None,
        suggested_quantity=0,
        evidence_cutoff=datetime(
            calculation.year,
            calculation.month,
            calculation.day,
            tzinfo=timezone.utc,
        ),
        model_version="rule-v1",
        feature_version="price-features-v1",
        config_version="price-guidance-rule-v1",
        data_version="unavailable",
        reason_codes=(reason,),
    )


def _reason(exc: Exception) -> str:
    text = str(exc).strip()
    if text == "NO_RELIABLE_GUIDANCE":
        return text
    if "252" in text:
        return "INSUFFICIENT_HISTORY"
    if "UNSUPPORTED_SECURITY_RULES" in text:
        return "UNSUPPORTED_SECURITY_RULES"
    if "risk distance" in text:
        return "RISK_DISTANCE_INVALID"
    return "NO_RELIABLE_GUIDANCE"


def _parse_date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def load_bars(data_root: Path, symbols: tuple[str, ...]) -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        path = (data_root / "lake" / "daily_bars" / f"{symbol}.parquet").resolve()
        if path.exists() and path.is_file():
            result[symbol] = pd.read_parquet(path)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成纸面价格指导计划")
    subcommands = parser.add_subparsers(dest="command", required=True)
    generate = subcommands.add_parser("generate")
    generate.add_argument("--data-root", type=Path, required=True)
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--calculation-date", required=True)
    generate.add_argument("--valid-for", required=True)
    generate.add_argument("--symbols", nargs="+", required=True)
    inspect = subcommands.add_parser("inspect")
    inspect.add_argument("--output", type=Path, required=True)
    inspect.add_argument("--symbol", required=False)
    args = parser.parse_args(argv)
    store = PriceGuidanceStore(args.output)
    if args.command == "inspect":
        plans = store.plans()
        if args.symbol:
            plans = tuple(item for item in plans if item.symbol == args.symbol)
        print(
            pd.DataFrame([item.to_dict() for item in plans]).to_json(
                orient="records", force_ascii=False
            )
        )
        return 0
    symbols = tuple(str(symbol) for symbol in args.symbols)
    result = PriceGuidanceRuntime(
        bars_by_symbol=load_bars(args.data_root.resolve(), symbols),
        store=store,
        candidate_symbols=symbols,
    ).generate(args.calculation_date, args.valid_for)
    print(
        pd.DataFrame([item.to_dict() for item in result.plans]).to_json(
            orient="records", force_ascii=False
        )
    )
    return 0


__all__ = ["PriceGuidanceRuntime", "PriceGuidanceRuntimeResult", "load_bars", "main"]
