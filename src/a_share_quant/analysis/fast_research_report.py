"""Stage 3B fast-research report writer with explicit data-mode labelling."""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (date, datetime, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _display(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, (float, np.floating)):
        return "n/a" if not math.isfinite(float(value)) else f"{float(value):.6f}"
    return str(value)


def _table(lines: list[str], rows: list[dict[str, Any]], columns: list[str]) -> None:
    if not rows:
        lines.append("No result was available.")
        return
    lines.append("| " + " | ".join(columns) + " |")
    lines.append("| " + " | ".join("---" for _ in columns) + " |")
    for row in rows:
        lines.append("| " + " | ".join(_display(row.get(column)) for column in columns) + " |")


def write_fast_research_report(
    payload: dict[str, Any],
    *,
    markdown_path: Path,
    json_path: Path | None = None,
) -> tuple[Path, Path | None]:
    markdown_path = Path(markdown_path)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    if json_path is not None:
        json_path = Path(json_path)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )

    metadata = payload.get("metadata", {})
    fixture = metadata.get("data_mode") == "fixture"
    zones = metadata.get("reasonable_zones", {})
    lines = [
        "# Stage 3B Fast Research Report",
        "",
        f"- Data mode: `{metadata.get('data_mode', 'unknown')}`",
        f"- Source commit: `{metadata.get('source_commit', 'unknown')}`",
        f"- VectorBT: `{metadata.get('vectorbt_version', 'not-installed')}`",
    ]
    if fixture:
        lines.extend(["", "> TEST / FIXTURE DATA - NOT INVESTMENT EVIDENCE", ""])
    lines.extend(
        [
            "",
            "## 1. Data mode",
            "",
            "Every signal, experiment, result, and report row carries the same data mode. "
            "Fixture results cannot promote beyond `SIGNAL_VALIDATED_FIXTURE`.",
            "",
            "## 2. VectorBT version and limitations",
            "",
            f"- Availability: {metadata.get('vectorbt_reason', 'unknown')}",
            "- VectorBT is an optional fast-research adapter. It does not provide final "
            "A-share execution evidence for T+1, limit queues, suspension rejection, lots, "
            "or exact fills.",
            "- The reference fallback remains available when the optional dependency is absent.",
            "",
            "## 3. Candidate strategies",
            "",
        ]
    )
    _table(
        lines,
        payload.get("candidate_strategies", []),
        ["strategy", "top_k", "rebalance_days", "candidate_count"],
    )
    lines.extend(["", "## 4. TopK comparison", ""])
    _table(
        lines,
        payload.get("topk_comparison", []),
        [
            "model",
            "top_k",
            "strategy",
            "cagr",
            "sharpe",
            "sortino",
            "max_drawdown",
            "turnover",
        ],
    )
    lines.extend(["", "## 5. Rebalance comparison", ""])
    _table(
        lines,
        payload.get("rebalance_comparison", []),
        [
            "model",
            "rebalance_days",
            "strategy",
            "cagr",
            "sharpe",
            "sortino",
            "max_drawdown",
            "turnover",
        ],
    )
    lines.extend(["", "## 6. Turnover comparison", ""])
    _table(
        lines,
        payload.get("turnover_comparison", []),
        ["strategy", "raw_turnover", "normalized_turnover", "estimated_transaction_cost"],
    )
    lines.extend(["", "## 7. Transaction cost estimates", ""])
    _table(
        lines,
        payload.get("cost_comparison", []),
        ["strategy", "estimated_transaction_cost", "total_return"],
    )
    comparison = metadata.get("equal_weight_vs_score_weight", {})
    if comparison:
        lines.extend(["", "EqualWeight vs ScoreWeight (Score minus Equal):", ""])
        _table(
            lines,
            [dict({"model": model}, **values) for model, values in comparison.items()],
            [
                "model",
                "cagr_delta_score_minus_equal",
                "turnover_delta_score_minus_equal",
                "cost_delta_score_minus_equal",
            ],
        )
    lines.extend(["", "## 8. Drawdown comparison", ""])
    _table(
        lines,
        payload.get("drawdown_comparison", []),
        ["model", "strategy", "max_drawdown", "calmar", "volatility"],
    )
    lines.extend(["", "## 9. Parameter surface", ""])
    _table(
        lines,
        payload.get("parameter_surface", []),
        [
            "model",
            "strategy",
            "top_k",
            "rebalance_days",
            "cagr",
            "sharpe",
            "sortino",
            "max_drawdown",
            "volatility",
            "turnover",
        ],
    )
    lines.extend(
        [
            "",
            f"- Parameter Island: `{metadata.get('parameter_island', 'not identified')}`",
            f"- TopK zone: `{zones.get('top_k', 'not identified')}`",
            f"- Rebalance zone: `{zones.get('rebalance', 'not identified')}`",
            "- The grid is intentionally small and diagnostic; it is not a "
            "return-maximizing optimizer.",
            "",
            "## 10. Model x strategy matrix",
            "",
        ]
    )
    _table(
        lines,
        payload.get("model_strategy_matrix", []),
        [
            "model",
            "strategy",
            "cagr",
            "sharpe",
            "sortino",
            "max_drawdown",
            "volatility",
            "turnover",
            "selection_status",
        ],
    )
    lines.extend(["", "## 11. EqualRank Ensemble fixture result", ""])
    _table(
        lines,
        payload.get("equal_rank_ensemble", []),
        [
            "strategy",
            "cagr",
            "sharpe",
            "sortino",
            "max_drawdown",
            "volatility",
            "turnover",
            "selection_status",
        ],
    )
    lines.extend(["", "## 12. Historical dry run", ""])
    dry_run = payload.get("historical_dry_run", {})
    lines.append(f"- Status: `{dry_run.get('status', 'unknown')}`")
    if dry_run.get("missing"):
        lines.extend(f"- Missing: {item}" for item in dry_run["missing"])
    if dry_run.get("note"):
        lines.append(f"- Note: {dry_run['note']}")
    lines.extend(
        [
            "",
            "## 13. Known limitations",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in payload.get("limitations", []))
    lines.extend(
        [
            "",
            "## 14. Next-stage recommendation",
            "",
            "Stop after Stage 3B. Stage 3C may add equal-rank ensemble research, "
            "correlation controls, robust optimization, and nested walk-forward only "
            "after historical data and benchmark coverage are available.",
            "",
        ]
    )
    markdown_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return markdown_path, json_path
