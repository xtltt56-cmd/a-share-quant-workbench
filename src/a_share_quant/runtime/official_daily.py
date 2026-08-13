"""Safe startup loading and local regeneration for official daily signals."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from a_share_quant.research.daily_candidates import (
    DailyDataStaleError,
    generate_from_data_root,
    load_name_map,
)
from a_share_quant.signals.realtime import OfficialModelSignal
from a_share_quant.storage.official_signal_store import OfficialSignalStore

SignalGenerator = Callable[[], tuple[OfficialModelSignal, ...]]


def load_or_generate_official_store(
    path: str | Path,
    *,
    repo_root: str | Path | None = None,
    top_k: int = 10,
    generator: SignalGenerator | None = None,
) -> OfficialSignalStore:
    """Load a durable signal store and refresh it from local real bars when needed.

    Startup must never invent a fallback row.  If the local data lake is absent,
    incomplete, or fails validation, the existing valid artifact is retained and
    the returned store may remain empty.  Invalid existing artifacts still raise
    so tampering cannot be silently hidden.
    """

    store = OfficialSignalStore(path=Path(path))
    root = Path(repo_root).resolve() if repo_root is not None else None
    if generator is None:
        if root is None:
            return store

        data_root = root / "data"

        def generator() -> tuple[OfficialModelSignal, ...]:
            return generate_from_data_root(
                data_root,
                top_k=top_k,
                name_map=load_name_map(data_root),
                now=datetime.now(timezone.utc),
                require_fresh=True,
            )

    try:
        generated = tuple(generator())
    except DailyDataStaleError as exc:
        store.set_refresh_status("STALE_DATA", str(exc))
        return store
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        store.set_refresh_status("UPDATE_FAILED", "日线数据刷新失败，保留上次经过核验的候选。")
        return store
    if not generated:
        store.set_refresh_status("UPDATE_FAILED", "日线刷新未生成候选，保留上次经过核验的候选。")
        return store

    existing = store.latest()
    if not existing or max(signal.signal_date for signal in generated) >= max(
        signal.signal_date for signal in existing
    ):
        store.put_signals(generated)
    cutoff = max(signal.data_cutoff for signal in generated)
    store.set_refresh_status(
        "FRESH",
        f"日线已更新至 {cutoff.isoformat()}，来源：BaoStock。",
    )
    return store


__all__ = ["load_or_generate_official_store"]
