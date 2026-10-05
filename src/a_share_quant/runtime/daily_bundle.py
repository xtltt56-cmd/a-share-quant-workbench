"""Recoverable logical transaction for daily candidates and matching price plans.

Two Windows file replacements are NOT an atomic multi-file operation. A small
write-ahead journal and startup recovery provide the app's logical commit; live
readers additionally use the service state lock and price-store transaction lock.
Independent legacy file consumers must not read while the app is publishing.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import date
from pathlib import Path

from a_share_quant.advisory.price_contracts import PricePlanType
from a_share_quant.market.trading_calendar import AShareTradingCalendar
from a_share_quant.storage.atomic_json import (
    atomic_write_bytes,
    canonical_bytes,
    read_checked_json,
    write_checked_json,
)
from a_share_quant.storage.official_signal_store import OfficialSignalStore
from a_share_quant.storage.price_guidance_store import PriceGuidanceStore


class DailyBundleTransaction:
    MAX_BYTES = 24 * 1024 * 1024

    def __init__(self, root: Path, signal_path: Path, guidance_path: Path | None) -> None:
        self.root = root.resolve()
        self.paths = {"signals": self._inside(signal_path)}
        if guidance_path is not None:
            self.paths["guidance"] = self._inside(guidance_path)
        if len(set(self.paths.values())) != len(self.paths):
            raise ValueError("daily artifact paths must be distinct")
        self.journal = self.root / ".runtime" / "workflow" / "daily-publication.json"

    def _inside(self, path: Path) -> Path:
        if path.is_symlink():
            raise ValueError("daily artifact cannot be a symlink")
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError("daily artifact must stay inside project")
        return resolved

    def publish(
        self,
        day: date,
        staged: dict[str, Path],
        *,
        holding_symbols: tuple[str, ...] = (),
    ) -> str:
        if self.journal.exists():
            raise ValueError("pending daily transaction must be recovered first")
        documents = {key: json.loads(path.read_bytes()) for key, path in staged.items()}
        body = {
            "format_version": 1,
            "session": day.isoformat(),
            "paths": {
                key: path.relative_to(self.root).as_posix() for key, path in self.paths.items()
            },
            "documents": documents,
            "holding_symbols": list(holding_symbols),
        }
        self._validate(body)
        write_checked_json(self.journal, body, maximum_bytes=self.MAX_BYTES)
        return self._apply(body)

    def recover(self) -> bool:
        if not self.journal.exists():
            return False
        body = read_checked_json(self.journal, maximum_bytes=self.MAX_BYTES)
        self._validate(body)
        self._apply(body)
        return True

    def _validate(self, body: dict) -> None:
        if set(body) != {"format_version", "session", "paths", "documents", "holding_symbols"}:
            raise ValueError("invalid daily transaction")
        if (
            body["format_version"] != 1
            or body["paths"]
            != {key: path.relative_to(self.root).as_posix() for key, path in self.paths.items()}
            or set(body["documents"]) != set(self.paths)
        ):
            raise ValueError("daily transaction targets do not match configuration")
        day = date.fromisoformat(body["session"])
        holdings = body["holding_symbols"]
        if (
            not isinstance(holdings, list)
            or len(holdings) > 50
            or len(set(holdings)) != len(holdings)
        ):
            raise ValueError("invalid daily transaction account universe")
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.journal.parent, prefix="validate-") as directory:
            staged = {}
            for key, document in body["documents"].items():
                path = Path(directory) / f"{key}.json"
                atomic_write_bytes(path, canonical_bytes(document))
                staged[key] = path
            signals = OfficialSignalStore(staged["signals"]).latest()
            if not signals or any(signal.data_cutoff != day for signal in signals):
                raise ValueError("daily candidates do not match target session")
            if "guidance" in staged:
                plans = PriceGuidanceStore(staged["guidance"]).plans()
                candidates = {
                    item.symbol
                    for item in plans
                    if item.plan_type is PricePlanType.DAILY_CANDIDATE
                    and item.calculation_date == day
                }
                if candidates != {item.symbol for item in signals}:
                    raise ValueError("daily plans do not cover the published candidates")
                if any(item.calculation_date != day for item in plans):
                    raise ValueError("daily bundle contains mixed calculation dates")
                if any(
                    item.valid_for != AShareTradingCalendar().next_session(day) for item in plans
                ):
                    raise ValueError("daily bundle validity does not match shared calendar")
                if {
                    item.symbol for item in plans if item.plan_type is PricePlanType.HOLDING
                } != set(holdings):
                    raise ValueError("daily holding plans do not cover the account universe")

    def _apply(self, body: dict) -> str:
        for key, path in self.paths.items():
            atomic_write_bytes(path, canonical_bytes(body["documents"][key]) + b"\n")
        # Only remove this exact regenerable intent AFTER both replacements.
        self.journal.unlink()
        return hashlib.sha256(canonical_bytes(body)).hexdigest()
