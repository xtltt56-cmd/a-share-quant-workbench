# Desktop Dual-Page Launch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing desktop shortcut open both the official daily/intraday dashboard and the manual advisory page without changing the address manually.

**Architecture:** Keep the `.lnk` target unchanged and centralize the two fixed loopback URLs in one PowerShell helper inside the existing launcher. Call that helper only when an existing owned process is detected or after a newly started process passes its bounded health check.

**Tech Stack:** Windows PowerShell 5.1, Python 3.12, pytest static launcher security tests.

---

### Task 1: Open both local pages from every successful launcher path

**Files:**
- Modify: `tests/test_launcher_security.py`
- Modify: `scripts/start_quant_workbench.ps1`

- [x] **Step 1: Write the failing launcher regression**

Add a focused test that reads `scripts/start_quant_workbench.ps1` and asserts the helper, both fixed URLs, and both successful call sites:

```python
def test_start_launcher_opens_dashboard_and_advisory_for_both_success_paths() -> None:
    launcher = (ROOT / "scripts" / "start_quant_workbench.ps1").read_text(
        encoding="utf-8"
    )

    assert 'function Open-QuantWorkbenchPages' in launcher
    assert '$dashboardUrl = "http://127.0.0.1:$Port/"' in launcher
    assert '$advisoryUrl = "http://127.0.0.1:$Port/advisory"' in launcher
    assert 'Start-Process $dashboardUrl' in launcher
    assert 'Start-Process $advisoryUrl' in launcher
    assert launcher.count('Open-QuantWorkbenchPages -Port $Port') == 2
```

- [x] **Step 2: Run the regression and confirm RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_launcher_security.py::test_start_launcher_opens_dashboard_and_advisory_for_both_success_paths -q
```

Expected: one failure because `Open-QuantWorkbenchPages` does not exist.

- [x] **Step 3: Add the minimal PowerShell helper and use it twice**

Add after the launcher path variables:

```powershell
function Open-QuantWorkbenchPages {
    param([int]$Port)

    $dashboardUrl = "http://127.0.0.1:$Port/"
    $advisoryUrl = "http://127.0.0.1:$Port/advisory"
    Start-Process $dashboardUrl
    Start-Process $advisoryUrl
}
```

Replace the existing-process branch browser call with:

```powershell
Open-QuantWorkbenchPages -Port $Port
```

Replace the final advisory-only browser call with the same helper call. Do not call it before `$ready` is true.

- [x] **Step 4: Run focused and adjacent tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_launcher_security.py tests/test_workbench_app.py -q
& .\.venv\Scripts\python.exe -m ruff check .
git diff --check
```

Expected: all tests and checks pass.

- [x] **Step 5: Verify the existing shortcut and launcher behavior**

Inspect `C:\Users\lenovo\Desktop\A股量化交易系统.lnk` and confirm it still targets `scripts\start_quant_workbench.ps1`. Run the launcher while the owned workbench process is active; confirm the command exits successfully after issuing both fixed local URLs.

- [x] **Step 6: Commit the implementation**

```powershell
git add tests/test_launcher_security.py scripts/start_quant_workbench.ps1 docs/superpowers/plans/2026-08-11-dual-page-desktop-launch.md
git commit -m "feat: open both workbench pages from desktop"
```
