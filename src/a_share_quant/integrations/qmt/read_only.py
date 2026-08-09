"""A deliberately narrow, injected-only QMT account-inspection boundary.

This module never imports the optional QMT SDK, opens a session, or validates
live data.  A caller may inject an already-authorized read-only client solely
to obtain an account snapshot for local review and reconciliation.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol

from a_share_quant.account.contracts import AccountSnapshot, money
from a_share_quant.account.ledger import AccountLedger
from a_share_quant.data.normalization import normalize_symbol

QMT_SDK_MODULE = "xtquant"


class QmtCapabilityStatus(str, Enum):
    """Truthful availability states that do not imply a live connection."""

    SDK_NOT_FOUND = "SDK_NOT_FOUND"
    SDK_AVAILABLE = "SDK_AVAILABLE"
    SDK_DISCOVERY_FAILED = "SDK_DISCOVERY_FAILED"


@dataclass(frozen=True)
class QmtCapability:
    """Availability only; it never represents an authenticated QMT session."""

    status: QmtCapabilityStatus
    sdk_available: bool
    manual_execution_required: bool = True
    live_connection_validated: bool = False
    order_submission_supported: bool = False
    sdk_module: str = QMT_SDK_MODULE
    message: str = ""


@dataclass(frozen=True)
class QmtPositionSnapshot:
    """Normalized account position supplied by an injected read-only client."""

    symbol: str
    name: str
    total_quantity: int
    available_quantity: int
    frozen_quantity: int


@dataclass(frozen=True)
class QmtAccountSnapshot:
    """Normalized QMT snapshot with no inferred time, cash, or positions."""

    as_of: date | None
    cash: Decimal
    positions: tuple[QmtPositionSnapshot, ...]
    manual_execution_required: bool = True
    source: str = "QMT_READ_ONLY_INJECTED"


class QmtReadOnlyClient(Protocol):
    """Minimal protocol for an already-authorized official read-only client."""

    def fetch_account_snapshot(self) -> Mapping[str, Any] | QmtAccountSnapshot:
        """Return one local account snapshot without any submission capability."""


@dataclass(frozen=True)
class QmtPositionDelta:
    """One non-mutating comparison between the local ledger and QMT view."""

    symbol: str
    local_name: str
    qmt_name: str
    local_total_quantity: int
    qmt_total_quantity: int
    total_quantity_delta: int
    local_available_quantity: int
    qmt_available_quantity: int
    available_quantity_delta: int
    local_frozen_quantity: int
    qmt_frozen_quantity: int
    frozen_quantity_delta: int
    name_mismatch: bool

    @property
    def has_discrepancy(self) -> bool:
        return any(
            (
                self.total_quantity_delta,
                self.available_quantity_delta,
                self.frozen_quantity_delta,
                self.name_mismatch,
            )
        )


@dataclass(frozen=True)
class QmtReconciliationPreview:
    """Read-only reconciliation result; neither source is changed."""

    status: str
    local_snapshot: AccountSnapshot
    qmt_snapshot: QmtAccountSnapshot
    cash_delta: Decimal
    position_deltas: tuple[QmtPositionDelta, ...]
    manual_execution_required: bool = True

    @property
    def is_reconciled(self) -> bool:
        return self.status == "RECONCILED"


SdkFinder = Callable[[str], object | None]
_MISSING = object()


def detect(
    *,
    sdk_module: str = QMT_SDK_MODULE,
    sdk_finder: SdkFinder | None = None,
) -> QmtCapability:
    """Check SDK import metadata only; never import it or open a QMT session."""

    module_name = _official_sdk_module_name(sdk_module)
    finder = sdk_finder or importlib.util.find_spec
    try:
        sdk_available = finder(module_name) is not None
    except Exception as exc:
        return QmtCapability(
            status=QmtCapabilityStatus.SDK_DISCOVERY_FAILED,
            sdk_available=False,
            sdk_module=module_name,
            message=f"QMT SDK discovery failed without opening a session: {type(exc).__name__}",
        )
    if not sdk_available:
        return QmtCapability(
            status=QmtCapabilityStatus.SDK_NOT_FOUND,
            sdk_available=False,
            sdk_module=module_name,
            message=(
                "Official QMT SDK was not found; no session was opened and no data was fetched."
            ),
        )
    return QmtCapability(
        status=QmtCapabilityStatus.SDK_AVAILABLE,
        sdk_available=True,
        sdk_module=module_name,
        message=(
            "Official QMT SDK appears available; no session was opened and no live data was "
            "validated."
        ),
    )


class QmtReadOnlyAdapter:
    """Read an injected account snapshot and compare it to the local ledger.

    The adapter deliberately has no automatic client construction.  That keeps
    QMT SDK discovery lazy and prevents any connection, account selection, or
    submission behavior from entering the application.
    """

    def __init__(
        self,
        *,
        client: QmtReadOnlyClient | None = None,
        sdk_module: str = QMT_SDK_MODULE,
        sdk_finder: SdkFinder | None = None,
    ) -> None:
        self._client = client
        self._sdk_module = _official_sdk_module_name(sdk_module)
        self._sdk_finder = sdk_finder

    def detect(self) -> QmtCapability:
        """Return SDK availability without importing it or opening a session."""

        return detect(sdk_module=self._sdk_module, sdk_finder=self._sdk_finder)

    def fetch_account_snapshot(self) -> QmtAccountSnapshot:
        """Normalize one supplied snapshot without fabricating unavailable values."""

        if self._client is None:
            capability = self.detect()
            if capability.status is QmtCapabilityStatus.SDK_NOT_FOUND:
                raise ValueError(
                    "QMT read-only snapshot is unavailable because the official SDK was not found; "
                    "no data was fabricated."
                )
            raise ValueError(
                "QMT read-only snapshot is unavailable until an already-authorized read-only "
                "client is injected; no connection is opened automatically."
            )
        try:
            raw_snapshot = self._client.fetch_account_snapshot()
        except Exception as exc:
            raise ValueError(
                "QMT read-only snapshot is unavailable from the injected client"
            ) from exc
        return _normalize_snapshot(raw_snapshot)

    def reconciliation_preview(self, local: AccountLedger) -> QmtReconciliationPreview:
        """Compare local manual accounting with QMT data without changing either."""

        if not isinstance(local, AccountLedger):
            raise TypeError("local must be an AccountLedger")
        qmt_snapshot = self.fetch_account_snapshot()
        local_snapshot = (
            local.snapshot(as_of=qmt_snapshot.as_of)
            if qmt_snapshot.as_of is not None
            else local.snapshot()
        )
        local_positions = {position.symbol: position for position in local_snapshot.positions}
        qmt_positions = {position.symbol: position for position in qmt_snapshot.positions}
        position_deltas: list[QmtPositionDelta] = []
        for symbol in sorted(set(local_positions).union(qmt_positions)):
            local_position = local_positions.get(symbol)
            qmt_position = qmt_positions.get(symbol)
            local_name = local_position.name if local_position is not None else ""
            qmt_name = qmt_position.name if qmt_position is not None else ""
            local_total = local_position.total_quantity if local_position is not None else 0
            qmt_total = qmt_position.total_quantity if qmt_position is not None else 0
            local_available = local_position.available_quantity if local_position is not None else 0
            qmt_available = qmt_position.available_quantity if qmt_position is not None else 0
            local_frozen = local_position.frozen_quantity if local_position is not None else 0
            qmt_frozen = qmt_position.frozen_quantity if qmt_position is not None else 0
            delta = QmtPositionDelta(
                symbol=symbol,
                local_name=local_name,
                qmt_name=qmt_name,
                local_total_quantity=local_total,
                qmt_total_quantity=qmt_total,
                total_quantity_delta=qmt_total - local_total,
                local_available_quantity=local_available,
                qmt_available_quantity=qmt_available,
                available_quantity_delta=qmt_available - local_available,
                local_frozen_quantity=local_frozen,
                qmt_frozen_quantity=qmt_frozen,
                frozen_quantity_delta=qmt_frozen - local_frozen,
                name_mismatch=bool(local_name and qmt_name and local_name != qmt_name),
            )
            if delta.has_discrepancy:
                position_deltas.append(delta)
        cash_delta = money(qmt_snapshot.cash - local_snapshot.cash)
        return QmtReconciliationPreview(
            status=(
                "RECONCILED" if cash_delta == 0 and not position_deltas else "DISCREPANCIES_FOUND"
            ),
            local_snapshot=local_snapshot,
            qmt_snapshot=qmt_snapshot,
            cash_delta=cash_delta,
            position_deltas=tuple(position_deltas),
        )

    def submit_order(self, *_: Any, **__: Any) -> None:
        """Reject the only compatibility submission name permanently."""

        raise PermissionError("QMT requires manual execution; submission is permanently disabled.")


def _normalize_snapshot(raw_snapshot: Mapping[str, Any] | QmtAccountSnapshot) -> QmtAccountSnapshot:
    if isinstance(raw_snapshot, QmtAccountSnapshot):
        if raw_snapshot.manual_execution_required is not True:
            raise ValueError("QMT snapshots must require manual execution")
        raw_snapshot = _direct_snapshot_mapping(raw_snapshot)
    if not isinstance(raw_snapshot, Mapping):
        raise ValueError("QMT read-only snapshot must be a mapping")
    cash = _money_value(
        _required_value(raw_snapshot, ("cash", "available_cash", "cash_balance"), "cash")
    )
    as_of = _as_of(_optional_value(raw_snapshot, ("as_of", "snapshot_date", "date")))
    raw_positions = _required_value(raw_snapshot, ("positions", "position_list"), "positions")
    if not isinstance(raw_positions, Sequence) or isinstance(
        raw_positions, (str, bytes, bytearray)
    ):
        raise ValueError("positions must be a sequence")
    positions = tuple(
        sorted((_normalize_position(item) for item in raw_positions), key=lambda item: item.symbol)
    )
    symbols = [position.symbol for position in positions]
    if len(symbols) != len(set(symbols)):
        raise ValueError("positions must not contain duplicate symbols")
    return QmtAccountSnapshot(as_of=as_of, cash=cash, positions=positions)


def _normalize_position(raw_position: object) -> QmtPositionSnapshot:
    if not isinstance(raw_position, Mapping):
        raise ValueError("each position must be a mapping")
    symbol = normalize_symbol(
        _required_value(
            raw_position,
            ("symbol", "stock_code", "code", "证券代码"),
            "position symbol",
        )
    )
    name_value = _optional_value(raw_position, ("name", "stock_name", "证券名称"))
    name = "" if name_value is _MISSING or name_value is None else str(name_value).strip()
    total_quantity = _quantity(
        _required_value(
            raw_position,
            ("total_quantity", "volume", "total_volume", "持仓数量"),
            "total_quantity",
        ),
        field="total_quantity",
    )
    available_quantity = _quantity(
        _required_value(
            raw_position,
            ("available_quantity", "can_use_volume", "available_volume", "可用数量"),
            "available_quantity",
        ),
        field="available_quantity",
    )
    frozen_quantity = _quantity(
        _required_value(
            raw_position,
            ("frozen_quantity", "frozen_volume", "冻结数量"),
            "frozen_quantity",
        ),
        field="frozen_quantity",
    )
    if available_quantity > total_quantity or frozen_quantity < 0:
        raise ValueError("position quantities are inconsistent")
    if available_quantity + frozen_quantity != total_quantity:
        raise ValueError("position quantities are inconsistent")
    return QmtPositionSnapshot(
        symbol=symbol,
        name=name,
        total_quantity=total_quantity,
        available_quantity=available_quantity,
        frozen_quantity=frozen_quantity,
    )


def _required_value(payload: Mapping[str, Any], names: tuple[str, ...], field: str) -> Any:
    value = _optional_value(payload, names)
    if value is _MISSING:
        raise ValueError(f"QMT snapshot is missing {field}")
    return value


def _optional_value(payload: Mapping[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in payload:
            return payload[name]
    return _MISSING


def _official_sdk_module_name(sdk_module: str) -> str:
    if not isinstance(sdk_module, str) or sdk_module != QMT_SDK_MODULE:
        raise ValueError("sdk_module must be the exact top-level official module 'xtquant'")
    return sdk_module


def _direct_snapshot_mapping(snapshot: QmtAccountSnapshot) -> Mapping[str, Any]:
    try:
        positions = tuple(
            {
                "symbol": position.symbol,
                "name": position.name,
                "total_quantity": position.total_quantity,
                "available_quantity": position.available_quantity,
                "frozen_quantity": position.frozen_quantity,
            }
            for position in snapshot.positions
        )
    except (AttributeError, TypeError) as exc:
        raise ValueError("QMT direct snapshot positions are invalid") from exc
    return {
        "as_of": snapshot.as_of,
        "cash": snapshot.cash,
        "positions": positions,
    }


def _money_value(value: Any) -> Decimal:
    try:
        return money(value)
    except ValueError as exc:
        raise ValueError("cash must be a finite decimal") from exc


def _quantity(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a non-negative integer")
    try:
        parsed = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{field} must be a non-negative integer") from exc
    if not parsed.is_finite() or parsed != parsed.to_integral_value() or parsed < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return int(parsed)


def _as_of(value: Any) -> date | None:
    if value is _MISSING or value is None or not str(value).strip():
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            try:
                return datetime.fromisoformat(value).date()
            except ValueError as exc:
                raise ValueError("as_of must be an ISO date or datetime") from exc
    raise ValueError("as_of must be an ISO date or datetime")


__all__ = [
    "QMT_SDK_MODULE",
    "QmtAccountSnapshot",
    "QmtCapability",
    "QmtCapabilityStatus",
    "QmtPositionDelta",
    "QmtPositionSnapshot",
    "QmtReadOnlyAdapter",
    "QmtReadOnlyClient",
    "QmtReconciliationPreview",
    "detect",
]
