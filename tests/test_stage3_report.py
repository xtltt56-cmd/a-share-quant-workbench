import json
from pathlib import Path

from a_share_quant.analysis.report import write_signal_quality_report


def test_signal_quality_report_writes_json_and_markdown_with_data_mode(tmp_path: Path) -> None:
    result = {
        "models": {
            "model-a": {
                "overall": {"ic_mean": 0.1, "rank_ic_mean": 0.2, "icir": 1.0},
                "diagnostics": ["WEAK_SIGNAL_MONOTONICITY"],
            }
        },
        "model_correlation": [],
    }
    markdown = tmp_path / "report.md"
    payload = tmp_path / "report.json"

    write_signal_quality_report(
        result,
        markdown_path=markdown,
        json_path=payload,
        metadata={"data_mode": "fixture", "source_commit": "abc123"},
    )

    assert "fixture" in markdown.read_text(encoding="utf-8")
    assert "WEAK_SIGNAL_MONOTONICITY" in markdown.read_text(encoding="utf-8")
    assert json.loads(payload.read_text(encoding="utf-8"))["metadata"]["data_mode"] == "fixture"
