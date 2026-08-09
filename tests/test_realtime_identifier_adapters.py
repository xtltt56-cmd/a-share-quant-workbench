from a_share_quant.contracts import AssetType, SecurityIdentifier, Vendor
from a_share_quant.data.realtime.identifiers import (
    akshare_symbol,
    qlib_symbol,
    rqdata_symbol,
    tushare_symbol,
)


def test_realtime_identifier_helpers_keep_vendor_formats_at_the_boundary() -> None:
    identifier = SecurityIdentifier(
        symbol="600000",
        exchange="SSE",
        asset_type=AssetType.EQUITY,
    )

    assert akshare_symbol(identifier).vendor is Vendor.AKSHARE
    assert tushare_symbol(identifier).vendor_symbol == "600000.SH"
    assert rqdata_symbol(identifier).vendor_symbol == "600000.XSHG"
    assert qlib_symbol(identifier).vendor_symbol == "SH600000"
