"""Human-readable and machine-readable Stage 3A report writers."""

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


def write_signal_quality_report(
    result: dict[str, Any],
    *,
    markdown_path: Path,
    json_path: Path,
    metadata: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    payload = {"metadata": metadata or {}, "result": _json_safe(result)}
    json_output = Path(json_path)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Stage 3A Signal Quality Report",
        "",
        f"- Data mode: `{(metadata or {}).get('data_mode', 'unknown')}`",
        f"- Source commit: `{(metadata or {}).get('source_commit', 'unknown')}`",
        f"- Label: `{result.get('label_column', 'unknown')}`",
        "",
    ]
    if (metadata or {}).get("data_mode") == "fixture":
        lines.extend(["> TEST / FIXTURE DATA - NOT INVESTMENT EVIDENCE", ""])
    lines.extend(
        [
            "- This report is diagnostic evidence; Stage 3A uses structural gates and "
            "does not invent return thresholds.",
            "",
            "## Model summary",
            "",
            "| Strategy | Sample count | IC mean | Rank IC mean | ICIR | "
            "Positive IC ratio | Diagnostics |",
            "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for strategy_id, model in result.get("models", {}).items():
        overall = model.get("overall", {})
        diagnostics = ", ".join(model.get("diagnostics", [])) or "none"
        lines.append(
            (
                "| {strategy} | {sample} | {ic} | {rank_ic} | {icir} | "
                "{positive} | {diagnostics} |"
            ).format(
                strategy=strategy_id,
                sample=overall.get("sample_count", ""),
                ic=_display(overall.get("ic_mean")),
                rank_ic=_display(overall.get("rank_ic_mean")),
                icir=_display(overall.get("icir")),
                positive=_display(overall.get("positive_ic_ratio")),
                diagnostics=diagnostics,
            )
        )
    lines.extend(["", "## Model correlation", ""])
    correlations = result.get("model_correlation", [])
    if correlations:
        lines.extend(
            [
                "| Left | Right | Score correlation | Rank correlation | Top-K overlap | "
                "Portfolio return correlation |",
                "| --- | --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for pair in correlations:
            lines.append(
                "| {left} | {right} | {score} | {rank} | {overlap} | {portfolio} |".format(
                    left=pair.get("left_strategy", ""),
                    right=pair.get("right_strategy", ""),
                    score=_display(pair.get("score_correlation")),
                    rank=_display(pair.get("rank_correlation")),
                    overlap=_display(pair.get("top_k_overlap")),
                    portfolio=_display(pair.get("portfolio_return_correlation")),
                )
            )
    else:
        lines.append("No pairwise model correlation was available.")
    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "- Results are only as valid as the frozen source artifacts.",
        ]
    )
    if (metadata or {}).get("limitations"):
        lines.extend(f"- {item}" for item in metadata["limitations"])
    lines.append("")
    markdown_output = Path(markdown_path)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return markdown_output, json_output


def _display(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, (float, np.floating)) and not math.isfinite(float(value)):
        return "n/a"
    if isinstance(value, (float, np.floating)):
        return f"{float(value):.6f}"
    return str(value)
