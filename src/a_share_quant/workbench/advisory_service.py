"""Local composition for manual-only account recording and advisory reports."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from threading import RLock
from uuid import uuid4

from a_share_quant.account.contracts import money
from a_share_quant.account.ledger import AccountLedger, LedgerReceipt
from a_share_quant.account.store import JsonlLedgerStore
from a_share_quant.advisory.engine import AdvisoryContext, AdvisoryEngine
from a_share_quant.advisory.risk import RiskPolicy
from a_share_quant.advisory.store import PredictionLedgerStore
from a_share_quant.storage.official_signal_store import OfficialSignalStore

_INITIALIZATION_FORMAT_VERSION = 1


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
        official_signal_store: OfficialSignalStore | None = None,
        context_provider: Callable[[], AdvisoryContext | None] | None = None,
        today: Callable[[], date] | None = None,
    ) -> None:
        self._ledger_store = JsonlLedgerStore(ledger_path)
        self._initialization_path = _initialization_path(self._ledger_store.path)
        self._known_instruments = dict(known_instruments or {})
        requested_initial_cash = money(initial_cash)
        if requested_initial_cash < 0:
            raise ValueError("initial_cash must be non-negative")
        recorded_initial_cash = _load_initialization_metadata(self._initialization_path)
        ledger_exists = self._ledger_store.path.exists()
        if recorded_initial_cash is not None:
            if requested_initial_cash > 0 and requested_initial_cash != recorded_initial_cash:
                raise ValueError("initial_cash conflicts with recorded account initialization")
            effective_initial_cash = recorded_initial_cash
        else:
            if ledger_exists and requested_initial_cash <= 0:
                raise ValueError(
                    "existing ledger requires initialization metadata "
                    "or explicit positive initial cash"
                )
            effective_initial_cash = requested_initial_cash
        self._ledger = AccountLedger.from_records(
            initial_cash=effective_initial_cash,
            records=self._ledger_store.load_records(),
            known_instruments=self._known_instruments,
        )
        self._pending_initialization_cash: Decimal | None = None
        if recorded_initial_cash is None and requested_initial_cash > 0:
            if ledger_exists:
                _write_initialization_metadata(self._initialization_path, requested_initial_cash)
            else:
                self._pending_initialization_cash = requested_initial_cash
        self._advisory_engine = AdvisoryEngine(risk_policy or RiskPolicy.conservative())
        self._prediction_store = prediction_store or PredictionLedgerStore()
        self._official_signal_store = official_signal_store
        self._context_provider = context_provider
        self._today = today or date.today
        self._manual_buy_previews: dict[str, _ManualBuyPreview] = {}
        self._manual_buy_lock = RLock()

    def preview_manual_buy(
        self,
        *,
        name: str,
        code: str,
        quantity: int,
        price: Decimal | float | int | str,
    ) -> dict[str, object]:
        """Validate the four manual fields without writing the durable ledger."""

        with self._manual_buy_lock:
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

        with self._manual_buy_lock:
            preview = self._manual_buy_previews.pop(str(confirmation_token), None)
            if preview is None:
                raise ValueError("unknown or expired manual buy confirmation")
            prospective = self._copy_ledger()
            prospective_receipt = prospective.record_fill(preview.receipt.event)
            initialization_written = False
            durable_append_completed = False
            try:
                if (
                    not prospective_receipt.idempotent
                    and self._pending_initialization_cash is not None
                ):
                    _write_initialization_metadata(
                        self._initialization_path,
                        self._pending_initialization_cash,
                    )
                    initialization_written = True
                if not prospective_receipt.idempotent:
                    self._ledger_store.append_fill(preview.receipt.event)
                    durable_append_completed = True
                receipt = self._ledger.record_fill(preview.receipt.event)
            except Exception:
                if initialization_written and not durable_append_completed:
                    _remove_initialization_metadata(self._initialization_path)
                raise
            if initialization_written:
                self._pending_initialization_cash = None
            if receipt.idempotent != prospective_receipt.idempotent:
                raise RuntimeError("manual ledger state changed during confirmation")
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

        with self._manual_buy_lock:
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

        if context is None and self._context_provider is not None:
            context = self._context_provider()
        if context is None:
            if self._official_signal_store is not None:
                latest = self._official_signal_store.latest()
                if latest:
                    signal = latest[0]
                    return {
                        "state": "BLOCKED",
                        "action_zh": "暂不操作",
                        "reason_codes": [
                            "OFFICIAL_RANKING_ONLY",
                            "NO_CALIBRATED_RETURN_FORECAST",
                        ],
                        "suggested_quantity": 0,
                        "market_validation": "LOCAL_DAILY_RANKING_ONLY",
                        "manual_execution_required": True,
                        "evidence_cutoff": signal.data_cutoff.isoformat(),
                        "valid_until": signal.signal_date.isoformat(),
                        "confidence": "0",
                        "explanation_zh": (
                            f"最新日选排名为 {signal.symbol}，但当前只有固定权重排序，"
                            "尚无经过样本外校准的收益预测；在实时数据和模型验证完成前不生成买卖结论。"
                        ),
                        "notice_zh": "仅供人工复核；系统不会提交委托。",
                    }
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
                else (
                    "RANKING_CANDIDATES_PRESENT"
                    if self._official_signal_store is not None
                    and self._official_signal_store.latest()
                    else "NO_FORECAST_RECORDS"
                )
            ),
            "data_status": "NO_LIVE_MARKET_VALIDATION",
            "manual_execution_required": True,
        }

    def managed_local_files(self) -> dict[str, Path]:
        """Return the explicitly managed, credential-free local account files."""

        return {
            "account-ledger.jsonl": self._ledger_store.path,
            "account-ledger.jsonl.initialization.json": self._initialization_path,
        }

    def managed_local_file_consistency_groups(self) -> dict[str, tuple[str, ...]]:
        """Require the durable account ledger and baseline metadata together."""

        return {
            "account-state": (
                "account-ledger.jsonl",
                "account-ledger.jsonl.initialization.json",
            )
        }

    def _copy_ledger(self) -> AccountLedger:
        return AccountLedger.from_records(
            initial_cash=self._ledger.initial_cash,
            records=self._ledger.records(),
            fee_schedule=self._ledger.fee_schedule,
            known_instruments=self._ledger.known_instruments,
            next_trading_day=self._ledger.next_trading_day,
            lot_size=self._ledger.lot_size,
        )


def _initialization_path(ledger_path: Path) -> Path:
    return ledger_path.with_name(f"{ledger_path.name}.initialization.json")


def _load_initialization_metadata(path: Path) -> Decimal | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError("account initialization metadata is invalid")
    try:
        raw_content = path.read_bytes()
    except OSError as exc:
        raise ValueError("account initialization metadata cannot be read") from exc
    if not raw_content or len(raw_content) > 4096:
        raise ValueError("account initialization metadata is invalid")
    try:
        payload = json.loads(raw_content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("account initialization metadata is invalid") from exc
    if not isinstance(payload, dict) or set(payload) != {"format_version", "initial_cash"}:
        raise ValueError("account initialization metadata is invalid")
    if payload["format_version"] != _INITIALIZATION_FORMAT_VERSION:
        raise ValueError("account initialization metadata is invalid")
    encoded_initial_cash = payload["initial_cash"]
    if not isinstance(encoded_initial_cash, str):
        raise ValueError("account initialization metadata is invalid")
    try:
        parsed_initial_cash = money(encoded_initial_cash)
    except ValueError as exc:
        raise ValueError("account initialization metadata is invalid") from exc
    if parsed_initial_cash <= 0 or encoded_initial_cash != f"{parsed_initial_cash:.2f}":
        raise ValueError("account initialization metadata is invalid")
    return parsed_initial_cash


def _write_initialization_metadata(path: Path, initial_cash: Decimal) -> None:
    payload = json.dumps(
        {
            "format_version": _INITIALIZATION_FORMAT_VERSION,
            "initial_cash": f"{initial_cash:.2f}",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(payload)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except OSError as exc:
        raise ValueError("account initialization metadata cannot be written") from exc
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _remove_initialization_metadata(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise ValueError("account initialization metadata cannot be removed") from exc
