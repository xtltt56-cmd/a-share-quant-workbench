from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


def code_fingerprint(repo_root: Path) -> str:
    root = repo_root.resolve(strict=True)
    files = (
        path
        for source_root in (root / "src", root / "scripts")
        for path in source_root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".py", ".ps1"}
    )
    records = [
        f"{path.relative_to(root).as_posix()}:{_file_digest(path)}"
        for path in files
    ]
    records.sort()
    digest = hashlib.sha256("\n".join(records).encode()).hexdigest()
    return f"sha256:{digest}"


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo_root", type=Path)
    args = parser.parse_args()
    print(code_fingerprint(args.repo_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
