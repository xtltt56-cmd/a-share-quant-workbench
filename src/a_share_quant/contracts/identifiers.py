"""Provider-neutral instrument identity contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from a_share_quant.data.normalization import normalize_symbol


class AssetType(str, Enum):
    EQUITY = "EQUITY"
    INDEX = "INDEX"


class Vendor(str, Enum):
    AKSHARE = "AKSHARE"
    TUSHARE = "TUSHARE"
    RQDATA = "RQDATA"
    QLIB = "QLIB"


@dataclass(frozen=True)
class SecurityIdentifier:
    """Canonical identity without a provider-specific code assumption."""

    symbol: str
    exchange: str
    asset_type: AssetType | str

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        object.__setattr__(self, "exchange", self.exchange.strip().upper())
        object.__setattr__(self, "asset_type", AssetType(self.asset_type))
        if self.exchange not in {"SSE", "SZSE", "BSE"}:
            raise ValueError(f"unsupported exchange: {self.exchange}")

    @property
    def is_tradable_equity(self) -> bool:
        return self.asset_type is AssetType.EQUITY

    def require_tradable_equity(self) -> SecurityIdentifier:
        if not self.is_tradable_equity:
            raise ValueError("security identifier is not a tradable equity")
        return self


@dataclass(frozen=True)
class VendorSecurityIdentifier:
    """Canonical identity plus a spelling valid only at one provider boundary."""

    symbol: str
    exchange: str
    asset_type: AssetType
    vendor: Vendor
    vendor_symbol: str


class VendorSymbolAdapter:
    """Translate an explicit canonical identifier at a provider boundary."""

    @staticmethod
    def adapt(identifier: SecurityIdentifier, vendor: Vendor | str) -> VendorSecurityIdentifier:
        selected_vendor = Vendor(vendor)
        suffix = _exchange_suffix(identifier.exchange)
        if selected_vendor is Vendor.AKSHARE:
            vendor_symbol = identifier.symbol
        elif selected_vendor is Vendor.TUSHARE:
            vendor_symbol = f"{identifier.symbol}.{suffix['tushare']}"
        elif selected_vendor is Vendor.RQDATA:
            vendor_symbol = f"{identifier.symbol}.{suffix['rqdata']}"
        else:
            vendor_symbol = f"{suffix['qlib']}{identifier.symbol}"
        return VendorSecurityIdentifier(
            symbol=identifier.symbol,
            exchange=identifier.exchange,
            asset_type=identifier.asset_type,
            vendor=selected_vendor,
            vendor_symbol=vendor_symbol,
        )


def _exchange_suffix(exchange: str) -> dict[str, str]:
    mapping = {
        "SSE": {"tushare": "SH", "rqdata": "XSHG", "qlib": "SH"},
        "SZSE": {"tushare": "SZ", "rqdata": "XSHE", "qlib": "SZ"},
        "BSE": {"tushare": "BJ", "rqdata": "XBSE", "qlib": "BJ"},
    }
    return mapping[exchange]
