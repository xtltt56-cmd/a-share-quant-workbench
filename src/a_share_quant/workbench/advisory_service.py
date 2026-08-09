"""Local composition for manual-only account recording and advisory reports."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from a_share_quant.account.ledger import AccountLedger, LedgerReceipt
from a_share_quant.account.store import JsonlLedgerStore
from a_share_quant.advisory.engine import AdvisoryContext, AdvisoryEngine
from a_share_quant.advisory.risk import RiskPolicy
from a_share_quant.advisory.store import PredictionLedgerStore


@dataclass(frozen=True)
class _ManualBuyPreview:
    confirmation_token: str
    receipt: LedgerReceipt


class AdvisoryWorkbenchService:
    """Compose local ledger and advisory components without execution capability.

    A manual buy is first simulated in a throwaway ledger.  Confirmation appends
    the validated fill to the local JSONL ledger; it never contacts a broker or
    submits an order.
    """

    def __init__(
        self,
        *,
        initial_cash: Decimal | float | int | str,
        ledger_path: Path,
        known_instruments: Mapping[str, str] | None = None,
        risk_policy: RiskPolicy | None = None,
        prediction_store: PredictionLedgerStore | None = None,
        today: Callable[[], date] | None = None,
    ) -> None:
        self._ledger_store = JsonlLedgerStore(ledger_path)
        self._known_instruments = dict(known_instruments or {})
        self._ledger = AccountLedger.from_records(
            initial_cash=initial_cash,
            records=self._ledger_store.load_records(),
            known_instruments=self._known_instruments,
        )
        self._advisory_engine = AdvisoryEngine(risk_policy or RiskPolicy.conservative())
        self._prediction_store = prediction_store or PredictionLedgerStore()
        self._today = today or date.today
        self._manual_buy_previews: dict[str, _ManualBuyPreview] = {}

    def preview_manual_buy(
        self,
        *,
        name: str,
        code: str,
        quantity: int,
        price: Decimal | float | int | str,
    ) -> dict[str, object]:
        """Validate the four manual fields without writing the durable ledger."""

        prospective = self._copy_ledger()
        receipt = prospective.record_buy(
            name=name,
            symbol=code,
            quantity=quantity,
            price=price,
            trade_date=self._today(),
            event_id=f"advisory-manual-buy-{uuid4().hex}",
            source="manual_workbench",
        )
        confirmation_token = f"manual-buy-{uuid4().hex}"
        self._manual_buy_previews[confirmation_token] = _ManualBuyPreview(
            confirmation_token=confirmation_token,
            receipt=receipt,
        )
        estimated_total_cost = receipt.event.notional + prospective.fee_schedule.calculate(
            side=receipt.event.side,
            symbol=receipt.event.symbol,
            notional=receipt.event.notional,
        ).total
        return {
            "confirmation_token": confirmation_token,
            "name": receipt.event.name,
            "code": receipt.event.symbol,
            "quantity": receipt.event.quantity,
            "price": f"{receipt.event.price:.4f}",
            "estimated_total_cost": f"{estimated_total_cost:.2f}",
            "cash_after": f"{receipt.cash_after:.2f}",
            "manual_execution_required": True,
            "notice_zh": "仅记录您已自行完成的人工交易；系统不会提交委托。",
        }

    def confirm_manual_buy(self, confirmation_token: str) -> dict[str, object]:
        """Durably record one previously previewed, manually executed fill."""

        preview = self._manual_buy_previews.get(str(confirmation_token))
        if preview is None:
            raise ValueError("unknown or expired manual buy confirmation")
        prospective = self._copy_ledger()
        prospective_receipt = prospective.record_fill(preview.receipt.event)
        if not prospective_receipt.idempotent:
            self._ledger_store.append_fill(preview.receipt.event)
        receipt = self._ledger.record_fill(preview.receipt.event)
        if receipt.idempotent != prospective_receipt.idempotent:
            raise RuntimeError("manual ledger state changed during confirmation")
        del self._manual_buy_previews[preview.confirmation_token]
        return {
            "recorded": not receipt.idempotent,
            "code": receipt.event.symbol,
            "quantity": receipt.event.quantity,
            "cash_after": f"{receipt.cash_after:.2f}",
            "manual_execution_required": True,
            "notice_zh": "已记录人工成交，不包含任何委托或自动执行。",
        }

    def holdings(self) -> dict[str, object]:
        """Return replayed local positions and make the manual boundary explicit."""

        snapshot = self._ledger.snapshot(as_of=self._today())
        return {
            "as_of": snapshot.as_of.isoformat(),
            "cash": f"{snapshot.cash:.2f}",
            "realized_pnl": f"{snapshot.realized_pnl:.2f}",
            "positions": [
                {
                    "name": position.name,
                    "code": position.symbol,
                    "total_quantity": position.total_quantity,
                    "available_quantity": position.available_quantity,
                    "frozen_quantity": position.frozen_quantity,
                    "average_cost": f"{position.average_cost:.4f}",
                }
                for position in snapshot.positions
                if position.total_quantity > 0
            ],
            "manual_execution_required": True,
            "notice_zh": "持仓来自本机账本回放，请以券商成交与持仓为准。",
        }

    def today_guidance(self, context: AdvisoryContext | None = None) -> dict[str, object]:
        """Evaluate a supplied formal context without inventing market inputs."""

        if context is None:
            return {
                "state": "INSUFFICIENT_DATA",
                "action_zh": "数据不足",
                "reason_codes": ["NO_CALLER_PROVIDED_CONTEXT"],
                "suggested_quantity": 0,
                "market_validation": "NO_LIVE_MARKET_VALIDATION",
                "manual_execution_required": True,
                "notice_zh": "未提供经核验的上下文，系统不生成市场建议。",
            }
        self._prediction_store.append_prediction(context.forecast)
        decision = self._advisory_engine.evaluate(context)
        return {
            "state": decision.state.value,
            "action_zh": decision.action_zh,
            "reason_codes": list(decision.reason_codes),
            "target_weight": str(decision.target_weight),
            "suggested_quantity": decision.suggested_quantity,
            "maximum_acceptable_price": (
                str(decision.maximum_acceptable_price)
                if decision.maximum_acceptable_price is not None
                else None
            ),
            "invalidation_price": (
                str(decision.invalidation_price)
                if decision.invalidation_price is not None
                else None
            ),
            "evidence_cutoff": decision.evidence_cutoff,
            "valid_until": decision.valid_until.isoformat(),
            "confidence": str(decision.confidence),
            "cancel_conditions": list(decision.cancel_conditions),
            "explanation_zh": decision.explanation_zh,
            "market_validation": "CALLER_PROVIDED_CONTEXT",
            "manual_execution_required": True,
            "notice_zh": "仅供人工复核与人工执行；系统不会提交委托。",
        }

    def model_data_health(self) -> dict[str, object]:
        """Expose honest local model/data availability without secret material."""

        return {
            "model_status": (
                "FORECAST_RECORDS_PRESENT"
                if self._prediction_store.predictions()
                else "NO_FORECAST_RECORDS"
            ),
            "data_status": "NO_LIVE_MARKET_VALIDATION",
            "manual_execution_required": True,
        }

    def managed_local_files(self) -> dict[str, Path]:
        """Return the explicitly managed, credential-free local ledger file only."""

        return {"account-ledger.jsonl": self._ledger_store.path}

    def _copy_ledger(self) -> AccountLedger:
        return AccountLedger.from_records(
            initial_cash=self._ledger.initial_cash,
            records=self._ledger.records(),
            fee_schedule=self._ledger.fee_schedule,
            known_instruments=self._ledger.known_instruments,
            next_trading_day=self._ledger.next_trading_day,
            lot_size=self._ledger.lot_size,
        )
