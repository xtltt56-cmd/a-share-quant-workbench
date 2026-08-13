"""Local composition for manual-only account recording and advisory reports."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import RLock
from uuid import uuid4

from a_share_quant.account.contracts import money
from a_share_quant.account.import_inbox import (
    AccountFilePreview,
    AccountImportInbox,
    AccountImportKind,
)
from a_share_quant.account.ledger import AccountLedger, LedgerReceipt
from a_share_quant.account.service import AccountEntryService
from a_share_quant.account.snapshot_store import AccountSnapshotStore, ImportedAccountSnapshot
from a_share_quant.account.store import JsonlLedgerStore
from a_share_quant.advisory.engine import AdvisoryContext, AdvisoryEngine
from a_share_quant.advisory.holding_guidance import HoldingPriceGuidanceEngine
from a_share_quant.advisory.price_contracts import PricePlanType
from a_share_quant.advisory.risk import RiskPolicy
from a_share_quant.advisory.store import PredictionLedgerStore
from a_share_quant.storage.official_signal_store import OfficialSignalStore
from a_share_quant.storage.price_guidance_store import PriceGuidanceStore

_INITIALIZATION_FORMAT_VERSION = 1


@dataclass(frozen=True)
class _ManualBuyPreview:
    confirmation_token: str
    receipt: LedgerReceipt


@dataclass(frozen=True)
class _AccountImportConfirmation:
    confirmation_token: str
    preview: AccountFilePreview


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
        price_guidance_store: PriceGuidanceStore | None = None,
        context_provider: Callable[[], AdvisoryContext | None] | None = None,
        today: Callable[[], date] | None = None,
        account_import_inbox: AccountImportInbox | None = None,
        account_snapshot_store: AccountSnapshotStore | None = None,
        quote_provider: Callable[[str], Mapping[str, object] | None] | None = None,
    ) -> None:
        if (account_import_inbox is None) != (account_snapshot_store is None):
            raise ValueError("account import inbox and snapshot store must be provided together")
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
        self._account_entry_service = AccountEntryService(
            ledger=self._ledger,
            store=self._ledger_store,
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
        self._price_guidance_store = price_guidance_store
        self._context_provider = context_provider
        self._today = today or date.today
        self._account_import_inbox = account_import_inbox
        self._account_snapshot_store = account_snapshot_store
        self._quote_provider = quote_provider
        self._manual_buy_previews: dict[str, _ManualBuyPreview] = {}
        self._account_import_confirmations: dict[str, _AccountImportConfirmation] = {}
        self._manual_buy_lock = RLock()
        self._holding_guidance_engine = HoldingPriceGuidanceEngine()

    def set_quote_provider(
        self,
        provider: Callable[[str], Mapping[str, object] | None],
    ) -> None:
        self._quote_provider = provider

    def list_account_imports(self) -> dict[str, object]:
        """List safe file identifiers without exposing filesystem paths."""

        with self._manual_buy_lock:
            if self._account_import_inbox is None:
                return {
                    "files": [],
                    "manual_execution_required": True,
                    "notice_zh": "尚未配置券商导出收件箱。",
                }
            files = self._account_import_inbox.scan()
        return {
            "files": [
                {
                    "file_id": item.file_id,
                    "file_name": item.file_name,
                    "size_bytes": item.size_bytes,
                    "modified_ns": item.modified_ns,
                }
                for item in files
            ],
            "manual_execution_required": True,
            "notice_zh": "仅列出固定收件箱中的导出文件，不会读取券商登录状态。",
        }

    def preview_account_import(self, file_id: str) -> dict[str, object]:
        """Generate a one-time preview; no ledger or snapshot is written."""

        with self._manual_buy_lock:
            if self._account_import_inbox is None:
                raise ValueError("account import service is unavailable")
            preview = self._account_import_inbox.preview(
                file_id,
                default_trade_date=self._today(),
            )
            confirmation_token = f"account-import-{uuid4().hex}"
            self._account_import_confirmations[confirmation_token] = _AccountImportConfirmation(
                confirmation_token=confirmation_token,
                preview=preview,
            )
        return _account_preview_payload(preview, confirmation_token)

    def confirm_account_import(self, confirmation_token: str) -> dict[str, object]:
        """Confirm a preview after rechecking its source digest."""

        with self._manual_buy_lock:
            pending = self._account_import_confirmations.pop(str(confirmation_token), None)
            if pending is None:
                raise ValueError("unknown or expired account import confirmation")
            if self._account_import_inbox is None:
                raise ValueError("account import service is unavailable")
            self._account_import_inbox.verify_unchanged(pending.preview)
            preview = pending.preview
            if preview.kind is AccountImportKind.FILLS:
                if preview.fill_preview is None:
                    raise ValueError("fill import preview is invalid")
                receipts = self._account_entry_service.confirm_validated_preview(
                    preview.fill_preview
                )
                return {
                    "kind": preview.kind.value,
                    "source_name": preview.source_name,
                    "recorded_rows": sum(not item.idempotent for item in receipts),
                    "idempotent_rows": sum(item.idempotent for item in receipts),
                    "manual_execution_required": True,
                    "notice_zh": "成交明细已按确认结果记录到本机账本；系统不会提交委托。",
                }
            if self._account_snapshot_store is None:
                raise ValueError("account snapshot store is unavailable")
            snapshot = ImportedAccountSnapshot(
                snapshot_id=f"snapshot-{preview.preview_id}",
                source_sha256=preview.source_sha256,
                source_name=preview.source_name,
                as_of=preview.as_of,
                imported_at=datetime.now(timezone.utc),
                cash=preview.cash,
                positions=preview.positions,
            )
            self._account_snapshot_store.save(snapshot)
            return {
                "kind": preview.kind.value,
                "source_name": preview.source_name,
                "position_rows": len(preview.positions),
                "manual_execution_required": True,
                "notice_zh": "券商持仓快照已独立保存；它没有被伪造成历史成交。",
            }

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
            imported = (
                self._account_snapshot_store.load()
                if self._account_snapshot_store is not None
                else None
            )
        local_positions = [
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
        ]
        imported_payload = None
        if imported is not None:
            imported_payload = {
                "source_name": imported.source_name,
                "source_sha256": imported.source_sha256,
                "as_of": imported.as_of.isoformat(),
                "cash": f"{imported.cash:.2f}" if imported.cash is not None else None,
                "positions": [
                    {
                        "name": position.name,
                        "code": position.symbol,
                        "total_quantity": position.total_quantity,
                        "available_quantity": position.available_quantity,
                        "frozen_quantity": position.frozen_quantity,
                        "average_cost": f"{position.average_cost:.4f}",
                    }
                    for position in imported.positions
                ],
                "notice_zh": "券商导入快照独立展示，未伪造成历史成交。",
            }
        guidance = self._holding_guidance(imported, local_positions)
        return {
            "as_of": snapshot.as_of.isoformat(),
            "cash": f"{snapshot.cash:.2f}",
            "realized_pnl": f"{snapshot.realized_pnl:.2f}",
            "positions": local_positions,
            "local_ledger": {
                "cash": f"{snapshot.cash:.2f}",
                "positions": local_positions,
                "notice_zh": "本机账本来自已确认的人工成交或成交明细导入。",
            },
            "imported_account_snapshot": imported_payload,
            "price_guidance": guidance,
            "manual_execution_required": True,
            "notice_zh": "持仓来自本机账本回放，请以券商成交与持仓为准。",
        }

    def _holding_guidance(
        self,
        imported: ImportedAccountSnapshot | None,
        local_positions: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        if self._official_signal_store is None:
            return []
        imported_positions = (
            {item.symbol: item for item in imported.positions} if imported is not None else {}
        )
        plans = (
            {
                item.symbol: item
                for item in self._price_guidance_store.plans()
                if item.plan_type is PricePlanType.HOLDING
            }
            if self._price_guidance_store is not None
            else {}
        )
        positions_by_symbol = {str(item["code"]): dict(item) for item in local_positions}
        for symbol, imported_position in imported_positions.items():
            positions_by_symbol.setdefault(
                symbol,
                {
                    "name": imported_position.name,
                    "code": symbol,
                    "total_quantity": imported_position.total_quantity,
                    "available_quantity": imported_position.available_quantity,
                    "frozen_quantity": imported_position.frozen_quantity,
                    "average_cost": f"{imported_position.average_cost:.4f}",
                },
            )
        result: list[dict[str, object]] = []
        for item in positions_by_symbol.values():
            plan = plans.get(str(item["code"]))
            if plan is None:
                result.append(
                    {
                        "symbol": item["code"],
                        "state": "NO_RELIABLE_GUIDANCE",
                        "manual_execution_required": True,
                    }
                )
                continue
            try:
                quote = (
                    self._quote_provider(str(item["code"]))
                    if self._quote_provider is not None
                    else None
                )
                if quote is None or "current_price" not in quote:
                    result.append(
                        {
                            **plan.to_dict(),
                            "state": "NO_RELIABLE_GUIDANCE",
                            "current_price": None,
                            "manual_execution_required": True,
                            "notice_zh": "暂无经过核验的当前价；不使用成本价替代行情。",
                        }
                    )
                    continue
                guidance = self._holding_guidance_engine.evaluate(
                    item,
                    plan,
                    current_price=quote["current_price"],
                )
                result.append(guidance.to_dict())
            except (TypeError, ValueError):
                result.append(
                    {
                        "symbol": item["code"],
                        "state": "NO_RELIABLE_GUIDANCE",
                        "manual_execution_required": True,
                    }
                )
        return result

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

        files = {
            "account-ledger.jsonl": self._ledger_store.path,
            "account-ledger.jsonl.initialization.json": self._initialization_path,
        }
        if self._account_snapshot_store is not None:
            files["imported-account-snapshot.json"] = self._account_snapshot_store.path
        return files

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


def _account_preview_payload(
    preview: AccountFilePreview,
    confirmation_token: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "confirmation_token": confirmation_token,
        "preview_id": preview.preview_id,
        "file_id": preview.file_id,
        "source_name": preview.source_name,
        "source_sha256": preview.source_sha256,
        "kind": preview.kind.value,
        "detected_mapping": dict(preview.detected_mapping),
        "accepted_rows": (
            preview.fill_preview.accepted_rows
            if preview.fill_preview is not None
            else len(preview.positions)
        ),
        "rejected_rows": [
            {"row_number": issue.row_number, "reason": issue.reason}
            for issue in preview.rejected_rows
        ],
        "warnings": list(preview.warnings),
        "as_of": preview.as_of.isoformat(),
        "manual_execution_required": True,
        "notice_zh": "请先核对预览；确认前不会修改本机账本或持仓快照。",
    }
    if preview.fill_preview is not None:
        payload["rows"] = [
            {
                "name": event.name,
                "code": event.symbol,
                "side": event.side.value,
                "quantity": event.quantity,
                "price": str(event.price),
                "trade_date": event.trade_date.isoformat(),
            }
            for event in preview.fill_preview.candidate_events[:100]
        ]
    else:
        payload["cash"] = str(preview.cash) if preview.cash is not None else None
        payload["rows"] = [
            {
                "name": position.name,
                "code": position.symbol,
                "total_quantity": position.total_quantity,
                "available_quantity": position.available_quantity,
                "frozen_quantity": position.frozen_quantity,
                "average_cost": str(position.average_cost),
            }
            for position in preview.positions[:100]
        ]
    return payload


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
