import importlib.util

import pytest


def test_vendor_adapters_keep_csi300_as_an_index() -> None:
    assert importlib.util.find_spec("a_share_quant.contracts.identifiers") is not None
    from a_share_quant.contracts.identifiers import (
        AssetType,
        SecurityIdentifier,
        Vendor,
        VendorSymbolAdapter,
    )

    csi300 = SecurityIdentifier(symbol="000300", exchange="SSE", asset_type=AssetType.INDEX)

    assert csi300.symbol == "000300"
    assert csi300.asset_type is AssetType.INDEX
    assert csi300.is_tradable_equity is False
    assert VendorSymbolAdapter.adapt(csi300, Vendor.AKSHARE).vendor_symbol == "000300"
    assert VendorSymbolAdapter.adapt(csi300, Vendor.TUSHARE).vendor_symbol == "000300.SH"
    assert VendorSymbolAdapter.adapt(csi300, Vendor.RQDATA).vendor_symbol == "000300.XSHG"
    assert VendorSymbolAdapter.adapt(csi300, Vendor.QLIB).vendor_symbol == "SH000300"

    with pytest.raises(ValueError, match="tradable equity"):
        csi300.require_tradable_equity()


def test_akshare_proxy_policy_defaults_to_system_and_rejects_unapproved_bypass() -> None:
    from a_share_quant.contracts.data import ProviderConfigurationError
    from a_share_quant.data.realtime.akshare import AKShareRealTimeProvider

    provider = AKShareRealTimeProvider()
    assert provider.transport_policy.use_system_proxy is True
    assert provider.transport_policy.is_isolated is False

    with pytest.raises(ProviderConfigurationError, match="diagnostic"):
        AKShareRealTimeProvider(use_system_proxy=False)

    isolated = AKShareRealTimeProvider(
        use_system_proxy=False,
        isolated_transport_authorized=True,
    )
    assert isolated.transport_policy.is_isolated is True
