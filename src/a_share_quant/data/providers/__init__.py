"""External data adapters."""

from .akshare import AKShareDataProvider
from .baostock import BaoStockDataProvider
from .tushare import TushareDataProvider

__all__ = ["AKShareDataProvider", "BaoStockDataProvider", "TushareDataProvider"]
