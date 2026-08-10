"""Run a deterministic, offline end-to-end acceptance fixture for the workbench."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from a_share_quant.account.ledger import AccountLedger
from a_share_quant.account.store import JsonlLedgerStore
from a_share_quant.advisory.contracts import ForecastRecord
from a_share_quant.advisory.engine import AdvisoryContext
from a_share_quant.advisory.risk import PortfolioRiskSnapshot
from a_share_quant.advisory.store import PredictionLedgerStore
from a_share_quant.integrations.qmt.read_only import QmtReadOnlyAdapter
from a_share_quant.workbench.advisory_service import AdvisoryWorkbenchService


class _FixtureQmtClient:
    def fetch_account_snapshot(self) -> dict[str, object]:
        return {
            "as_of": "2026-08-10",
            "cash": "98995.00",
            "positions": [
                {
                    "symbol": "000001",
                    "name": "平安银行",
                    "total_quantity": 100,
                    "available_quantity": 0,
                    "frozen_quantity": 100,
                }
            ],
        }


def run_acceptance_fixture(root: Path) -> dict[str, Any]:
    """Exercise the local paper-only flow without network or broker access."""

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    ledger_path = root / "account-ledger.jsonl"
    prediction_store = PredictionLedgerStore()
    service = AdvisoryWorkbenchService(
        initial_cash=Decimal("100000"),
        ledger_path=ledger_path,
        known_instruments={"000001": "平安银行"},
        prediction_store=prediction_store,
        today=lambda: date(2026, 8, 10),
    )

    data_quality_failure = service.today_guidance()
    preview = service.preview_manual_buy(
        name="平安银行",
        code="000001",
        quantity=100,
        price=Decimal("10"),
    )
    confirmed = service.confirm_manual_buy(str(preview["confirmation_token"]))
    guidance = service.today_guidance(_fixture_context())
    outcomes = prediction_store.mature(
        as_of=date(2026, 8, 20),
        price_lookup=lambda _symbol, _maturity_date: Decimal("11"),
    )

    local_ledger = AccountLedger.from_records(
        initial_cash=Decimal("100000"),
        records=JsonlLedgerStore(ledger_path).load_records(),
        known_instruments={"000001": "平安银行"},
    )
    qmt = QmtReadOnlyAdapter(client=_FixtureQmtClient())
    reconciliation = qmt.reconciliation_preview(local_ledger)
    try:
        qmt.submit_order(symbol="000001", quantity=100, price=10)
    except PermissionError:
        qmt_order_submission = "REJECTED"
    else:  # pragma: no cover - the adapter contract must always reject
        qmt_order_submission = "UNEXPECTEDLY_ACCEPTED"

    return {
        "manual_execution_required": bool(confirmed["manual_execution_required"]),
        "data_quality_failure": data_quality_failure,
        "manual_buy_recorded": bool(confirmed["recorded"]),
        "guidance_state": guidance["state"],
        "guidance_action_zh": guidance["action_zh"],
        "prediction_outcomes": len(outcomes),
        "qmt_reconciliation": reconciliation.status,
        "qmt_order_submission": qmt_order_submission,
        "ledger_path": str(ledger_path),
    }


def _fixture_context() -> AdvisoryContext:
    generated_at = datetime(2026, 8, 10, 8, tzinfo=timezone.utc)
    return AdvisoryContext(
        forecast=ForecastRecord(
            forecast_id="acceptance-forecast-1",
            symbol="000001",
            generated_at=generated_at,
            data_cutoff=generated_at,
            reference_price=Decimal("10"),
            model_version="acceptance-champion-v1",
            data_version="acceptance-data-v1",
            feature_version="acceptance-features-v1",
            horizon_days=5,
            maturity_date=date(2026, 8, 15),
            predicted_return=Decimal("0.05"),
            predicted_probability=Decimal("0.65"),
            predicted_rank=1,
            uncertainty=Decimal("0.10"),
        ),
        portfolio=PortfolioRiskSnapshot(
            equity=Decimal("100000"),
            cash=Decimal("100000"),
            drawdown=Decimal("0"),
            positions=(),
        ),
        data_quality="GOOD",
        tradeable=True,
        market_regime="NORMAL",
        market_price=Decimal("10"),
        invalidation_price=Decimal("9.50"),
        industry="银行",
        liquidity_amount=Decimal("100000000"),
        event_risk=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / ".runtime" / "acceptance-fixture",
        help="local disposable fixture directory",
    )
    parser.add_argument("--output", type=Path, default=None, help="optional JSON output path")
    args = parser.parse_args()
    result = run_acceptance_fixture(args.root)
    encoded = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        output = args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
