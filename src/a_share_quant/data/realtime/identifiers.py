"""Provider-bound helpers for explicit security identifiers."""

from __future__ import annotations

from a_share_quant.contracts.identifiers import (
    AssetType,
    SecurityIdentifier,
    Vendor,
    VendorSecurityIdentifier,
    VendorSymbolAdapter,
)


def akshare_symbol(identifier: SecurityIdentifier) -> VendorSecurityIdentifier:
    return VendorSymbolAdapter.adapt(identifier, Vendor.AKSHARE)


def tushare_symbol(identifier: SecurityIdentifier) -> VendorSecurityIdentifier:
    return VendorSymbolAdapter.adapt(identifier, Vendor.TUSHARE)


def rqdata_symbol(identifier: SecurityIdentifier) -> VendorSecurityIdentifier:
    return VendorSymbolAdapter.adapt(identifier, Vendor.RQDATA)


def qlib_symbol(identifier: SecurityIdentifier) -> VendorSecurityIdentifier:
    return VendorSymbolAdapter.adapt(identifier, Vendor.QLIB)


__all__ = [
    "AssetType",
    "SecurityIdentifier",
    "Vendor",
    "VendorSecurityIdentifier",
    "VendorSymbolAdapter",
    "akshare_symbol",
    "qlib_symbol",
    "rqdata_symbol",
    "tushare_symbol",
]
