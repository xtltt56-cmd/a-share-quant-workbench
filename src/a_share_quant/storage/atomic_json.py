"""Bounded atomic JSON files, kept beside their destination on the same drive."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def write_checked_json(path: Path, body: dict[str, Any], *, maximum_bytes: int) -> None:
    raw = canonical_bytes(
        {"body": body, "sha256": hashlib.sha256(canonical_bytes(body)).hexdigest()}
    )
    if len(raw) + 1 > maximum_bytes:
        raise ValueError("workflow artifact exceeds size limit")
    atomic_write_bytes(path, raw + b"\n")


def read_checked_json(path: Path, *, maximum_bytes: int) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum_bytes:
        raise ValueError("invalid workflow artifact")
    payload = json.loads(path.read_bytes().decode("utf-8"))
    if not isinstance(payload, dict) or set(payload) != {"body", "sha256"}:
        raise ValueError("invalid workflow artifact")
    body = payload["body"]
    if (
        not isinstance(body, dict)
        or payload["sha256"] != hashlib.sha256(canonical_bytes(body)).hexdigest()
    ):
        raise ValueError("invalid workflow artifact checksum")
    return body


def atomic_write_bytes(path: Path, raw: bytes) -> None:
    if path.is_symlink() or path.exists() and not path.is_file():
        raise ValueError("invalid workflow destination")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".workflow-", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
