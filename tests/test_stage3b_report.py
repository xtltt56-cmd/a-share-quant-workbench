import json
from pathlib import Path

from a_share_quant.analysis.fast_research_report import write_fast_research_report


def test_fast_research_report_labels_fixture_data_and_persists_mode(tmp_path: Path) -> None:
    markdown = tmp_path / "stage3b.md"
    payload_path = tmp_path / "stage3b.json"
    payload = {
        "metadata": {
            "data_mode": "fixture",
            "source_commit": "abc123",
            "vectorbt_version": "not-installed",
            "vectorbt_reason": "optional dependency absent",
        },
        "limitations": ["synthetic fixture only"],
        "historical_dry_run": {"status": "NOT_RUN", "missing": ["000300"]},
    }

    write_fast_research_report(payload, markdown_path=markdown, json_path=payload_path)

    assert "TEST / FIXTURE DATA - NOT INVESTMENT EVIDENCE" in markdown.read_text(
        encoding="utf-8"
    )
    saved = json.loads(payload_path.read_text(encoding="utf-8"))
    assert saved["metadata"]["data_mode"] == "fixture"
