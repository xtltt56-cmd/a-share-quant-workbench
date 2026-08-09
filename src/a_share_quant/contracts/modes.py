"""Shared data-mode validation for research, historical, and paper artifacts."""

from __future__ import annotations

DATA_MODES = frozenset({"fixture", "historical", "paper"})


def validate_data_mode(value: str) -> str:
    mode = str(value).strip().lower()
    if mode not in DATA_MODES:
        allowed = ", ".join(sorted(DATA_MODES))
        raise ValueError(f"data_mode must be one of: {allowed}")
    return mode
