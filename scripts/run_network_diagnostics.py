"""Write the redacted Windows/Python market-data connectivity diagnostic."""

from __future__ import annotations

import argparse
from pathlib import Path

from a_share_quant.data.realtime.diagnostics import (
    NetworkDiagnostics,
    collect_network_diagnostics,
    write_network_diagnostics_report,
)


def _safe_path(repo_root: Path, value: Path) -> Path:
    root = repo_root.resolve()
    candidate = (value if value.is_absolute() else root / value).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"output path is outside repository: {value}")
    return candidate


def run_diagnostics(
    *,
    allow_network: bool,
    repo_root: Path,
    output: Path,
) -> NetworkDiagnostics:
    """Collect only explicitly authorized endpoint probes and write redacted output."""

    report = collect_network_diagnostics(allow_network=allow_network)
    safe_output = _safe_path(repo_root, output)
    write_network_diagnostics_report(report, safe_output)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--network",
        action="store_true",
        help="explicitly permit bounded DNS and TLS-verified endpoint probes",
    )
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=Path("reports/network_diagnostics.md"))
    args = parser.parse_args()
    report = run_diagnostics(
        allow_network=args.network,
        repo_root=args.repo_root.resolve(),
        output=args.output,
    )
    print(f"assessment={report.assessment}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
