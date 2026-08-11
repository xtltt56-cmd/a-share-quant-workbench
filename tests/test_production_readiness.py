from pathlib import Path

import pytest

from a_share_quant.runtime.readiness import ReadinessReport


def _write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "readiness.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_readiness_report_requires_the_eight_release_categories(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
checks:
  - key: historical_data
    name: 历史数据
    category: data
    status: PASS
    evidence: reports/data.md
    blocking: true
""",
    )

    with pytest.raises(ValueError, match="categories"):
        ReadinessReport.load(path)


def test_readiness_report_does_not_call_blocked_system_ready(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
checks:
  - key: historical_data
    name: 历史数据
    category: data
    status: PASS
    evidence: reports/data.md
    blocking: true
  - key: qmt
    name: QMT
    category: broker
    status: BLOCKED
    evidence: reports/qmt.md
    blocking: true
  - key: model
    name: 模型
    category: model
    status: NOT_OBSERVED
    evidence: reports/model.md
    blocking: true
  - key: advisory
    name: 指导
    category: advisory
    status: PASS
    evidence: reports/advisory.md
    blocking: true
  - key: account
    name: 账户
    category: account
    status: PASS
    evidence: reports/account.md
    blocking: false
  - key: backup
    name: 备份
    category: backup
    status: PASS
    evidence: reports/backup.md
    blocking: true
  - key: ui
    name: 界面
    category: ui
    status: PASS
    evidence: reports/ui.md
    blocking: true
  - key: execution_safety
    name: 执行安全
    category: execution_safety
    status: PASS
    evidence: reports/safety.md
    blocking: true
""",
    )

    report = ReadinessReport.load(path)

    assert report.overall_status == "BLOCKED"
    assert report.is_release_ready is False
    assert report.blocking_failures == ()
    assert {check.category for check in report.checks} == {
        "data",
        "model",
        "advisory",
        "account",
        "backup",
        "broker",
        "ui",
        "execution_safety",
    }


def test_readiness_report_rejects_unknown_status_and_duplicate_key(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
checks:
  - key: first
    name: 第一项
    category: data
    status: MAYBE
    evidence: report.md
    blocking: true
  - key: first
    name: 重复项
    category: model
    status: PASS
    evidence: report.md
    blocking: true
""",
    )

    with pytest.raises(ValueError, match="status|duplicate"):
        ReadinessReport.load(path)
