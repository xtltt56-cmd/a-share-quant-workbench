"""Local-only real-time monitoring workbench."""

from .app import create_server
from .service import WorkbenchService

__all__ = ["WorkbenchService", "create_server"]
