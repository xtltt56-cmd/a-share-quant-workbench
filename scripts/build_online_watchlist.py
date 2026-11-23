"""Build the standalone watchlist using the last verified catalog snapshot."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from a_share_quant.storage.atomic_json import atomic_write_bytes
from a_share_quant.workbench.security_catalog import SecurityCatalog

_REPO = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    catalog = SecurityCatalog(_REPO, allow_network=False)
    snapshot = catalog.export()
    args.output.mkdir(parents=True, exist_ok=True)
    for name in ("index.html", "online.css", "watchlist.js"):
        shutil.copyfile(_REPO / "online" / name, args.output / name)
    shutil.copyfile(
        _REPO / "src/a_share_quant/workbench/static/workbench.css",
        args.output / "workbench.css",
    )
    data = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    atomic_write_bytes(args.output / "catalog.json", data)
    print(f"{args.output.resolve()} ({len(snapshot['rows'])} stocks, {snapshot['updated_at']})")


if __name__ == "__main__":
    main()
