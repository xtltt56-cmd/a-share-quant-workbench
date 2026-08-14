# Historical Maturity Acceleration and D-Drive Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a leakage-resistant historical validation pipeline that can grant a clearly marked 20-session provisional status while retaining the 60-session, human-approved production gate, with every new byte constrained to `D:\量化交易`.

**Architecture:** Add a single project-root storage policy beneath all new download, cache, temporary, model, evidence, and report writers. Build versioned research datasets beside—never over—the existing unadjusted execution data, then feed frozen walk-forward evaluations into a durable maturity ledger and the existing human-controlled evolution registry. The workbench owns background backfill/training workers and exposes storage and maturity evidence in Simplified Chinese.

**Tech Stack:** Python 3.12, pandas, PyArrow/Parquet, DuckDB, BaoStock, AKShare, scikit-learn, LightGBM, existing reference backtester, stdlib HTTP/server/process APIs, pytest, Ruff, PowerShell launcher.

---

## File and responsibility map

- `src/a_share_quant/storage/project_storage.py`: canonical D-drive path authorization, reparse-point rejection, quotas, child-process cache environment.
- `src/a_share_quant/storage/research_data_store.py`: atomic Parquet storage and integrity manifests for instrument history, research returns, corporate actions, trials, and evidence.
- `src/a_share_quant/data/providers/baostock.py`: free historical daily status and research-return retrieval without changing execution-bar semantics.
- `src/a_share_quant/research/history_contracts.py`: typed point-in-time instrument, corporate-action, and dataset-profile contracts.
- `src/a_share_quant/runtime/historical_backfill.py`: resumable seven-year backfill orchestration and coverage reporting.
- `src/a_share_quant/research/historical_validation.py`: frozen walk-forward/CPCV evaluation, trial accounting, PBO/DSR inputs, regime and reproducibility evidence.
- `src/a_share_quant/research/maturity.py`: separate historical evidence, provisional live maturity, and formal live maturity state machine.
- `src/a_share_quant/runtime/research_jobs.py`: allowlisted owned jobs, D-drive-only child environment, checkpoints, and shutdown.
- `src/a_share_quant/runtime/research_worker.py`: backfill, validate, and settle commands using only repository-owned paths.
- `src/a_share_quant/workbench/service.py`: storage and maturity state publication.
- `src/a_share_quant/workbench/app.py`: Simplified Chinese maturity/storage UI and JSON endpoints.
- `scripts/quant_cli.py`: bounded operator commands for history status/backfill and research validation.
- `scripts/start_quant_workbench.ps1`: process-local D-drive cache/temp environment.
- `config/research_maturity.yaml`: frozen historical, provisional, formal, quota, and scheduling thresholds.
- `reports/research/`: generated Chinese coverage and validation reports.

### Task 1: Enforce the D-drive project storage boundary

**Files:**
- Create: `src/a_share_quant/storage/project_storage.py`
- Create: `tests/test_project_storage_policy.py`
- Modify: `scripts/start_quant_workbench.ps1`
- Modify: `tests/test_launcher_security.py`

- [ ] **Step 1: Write failing policy tests**

```python
def test_policy_authorizes_only_real_paths_below_d_drive_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    policy = ProjectStoragePolicy(root, required_drive=None)
    assert policy.authorize("data/lake/research_returns/a.parquet") == (
        root / "data/lake/research_returns/a.parquet"
    )
    with pytest.raises(StorageBoundaryError, match="项目目录"):
        policy.authorize(Path("C:/temp/a.parquet"))
    with pytest.raises(StorageBoundaryError, match="项目目录"):
        policy.authorize("../outside.parquet")


def test_child_environment_moves_only_owned_caches_to_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    policy = ProjectStoragePolicy(root, required_drive=None)
    env = policy.child_environment({"PATH": "existing"})
    assert Path(env["TEMP"]).is_relative_to(root)
    assert Path(env["PIP_CACHE_DIR"]).is_relative_to(root)
    assert env["PATH"] == "existing"
    assert "HOME" not in env


def test_production_policy_requires_d_drive(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    with pytest.raises(StorageBoundaryError, match="D盘"):
        ProjectStoragePolicy(root)
```

- [ ] **Step 2: Run the tests and observe RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_project_storage_policy.py -q`  
Expected: collection fails because `ProjectStoragePolicy` does not exist.

- [ ] **Step 3: Implement canonical authorization and process-local environment**

```python
class StorageBoundaryError(ValueError):
    pass


class ProjectStoragePolicy:
    def __init__(self, repo_root: str | Path, *, required_drive: str | None = "D:") -> None:
        self.repo_root = Path(repo_root).resolve(strict=True)
        if required_drive is not None and self.repo_root.drive.upper() != required_drive.upper():
            raise StorageBoundaryError("量化交易存储根目录必须位于D盘")

    def authorize(self, value: str | Path) -> Path:
        candidate = Path(value)
        candidate = candidate if candidate.is_absolute() else self.repo_root / candidate
        normalized = Path(os.path.abspath(os.path.normpath(candidate)))
        if normalized != self.repo_root and self.repo_root not in normalized.parents:
            raise StorageBoundaryError("写入路径必须位于D盘项目目录")
        _reject_reparse_ancestors(self.repo_root, normalized.parent)
        return normalized

    def child_environment(self, base: Mapping[str, str]) -> dict[str, str]:
        result = dict(base)
        cache = self.authorize(".runtime/cache")
        temporary = self.authorize(".runtime/tmp")
        result.update({
            "TEMP": str(temporary), "TMP": str(temporary),
            "PIP_CACHE_DIR": str(cache / "pip"),
            "JOBLIB_TEMP_FOLDER": str(temporary / "joblib"),
            "XDG_CACHE_HOME": str(cache / "xdg"),
            "MPLCONFIGDIR": str(cache / "matplotlib"),
        })
        return result
```

The implementation must reuse the repository's existing junction/reparse detection patterns from `src/a_share_quant/workbench/backup.py`, without importing private backup functions.

- [ ] **Step 4: Update the launcher without global environment changes**

Add process-scoped environment variables before `Start-Process`; values must be below `$repoRoot\.runtime`. Extend `test_launcher_security.py` to assert that no `setx`, registry write, `$HOME`, or `CODEX_HOME` mutation is present.

- [ ] **Step 5: Run GREEN and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_project_storage_policy.py tests/test_launcher_security.py -q`  
Expected: all tests pass.  
Commit: `git commit -am "feat: enforce D-drive project storage"`

### Task 2: Add atomic research data stores, manifests, and quotas

**Files:**
- Create: `src/a_share_quant/storage/research_data_store.py`
- Create: `src/a_share_quant/research/history_contracts.py`
- Create: `tests/test_research_data_store.py`
- Create: `config/research_maturity.yaml`

- [ ] **Step 1: Write failing atomicity, integrity, and quota tests**

```python
def test_research_store_writes_versioned_parquet_and_manifest(tmp_path):
    root = tmp_path / "D-repo"
    root.mkdir()
    policy = ProjectStoragePolicy(root, required_drive=None)
    store = ResearchDataStore(policy)
    artifact = store.replace_dataset(
        "research_returns", "000001", frame(), data_version="baostock-forward-return-v1"
    )
    assert artifact.row_count == len(frame())
    assert artifact.sha256 == hashlib.sha256(artifact.path.read_bytes()).hexdigest()
    assert store.verify(artifact)


def test_research_store_rejects_quota_before_creating_target(tmp_path):
    store = bounded_store(tmp_path, dataset_limit_bytes=64)
    with pytest.raises(StorageQuotaError, match="配额"):
        store.replace_dataset("research_returns", "000001", large_frame(), data_version="v1")
    assert not list((tmp_path / "data").rglob("*.parquet"))
```

- [ ] **Step 2: Run RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_research_data_store.py -q`  
Expected: collection fails because the store and contracts do not exist.

- [ ] **Step 3: Implement focused contracts and atomic store**

Define immutable `ResearchArtifact`, `DatasetCoverage`, `PointInTimeInstrument`, and `CorporateAction` dataclasses. `ResearchDataStore.replace_dataset()` must write a same-directory temporary Parquet file, `fsync`, calculate SHA-256 and size, enforce per-file/dataset quotas, then `os.replace` and atomically append a JSONL manifest record. Valid dataset names are a fixed enum, never caller-created directories.

- [ ] **Step 4: Freeze configuration**

```yaml
history:
  minimum_symbols: 30
  minimum_sessions: 1750
  start_date: "2019-01-01"
validation:
  train_sessions: 756
  validation_sessions: 252
  embargo_sessions: 126
  test_sessions: 126
  minimum_oos_windows: 3
maturity:
  provisional_shadow_sessions: 20
  provisional_matured_predictions: 100
  formal_shadow_sessions: 60
  formal_matured_predictions: 200
storage:
  minimum_free_bytes: 21474836480
  maximum_single_download_bytes: 536870912
  maximum_research_data_bytes: 53687091200
```

- [ ] **Step 5: Run GREEN and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_research_data_store.py -q`  
Expected: all tests pass, including injected `fsync` and `os.replace` failures leaving the previous artifact intact.  
Commit: `git commit -am "feat: add governed research data storage"`

### Task 3: Retrieve point-in-time status and separate research returns

**Files:**
- Modify: `src/a_share_quant/data/providers/baostock.py`
- Modify: `src/a_share_quant/contracts/data.py`
- Create: `tests/test_baostock_research_history.py`
- Modify: `tests/test_baostock_provider.py`

- [ ] **Step 1: Write failing provider tests**

```python
def test_research_history_keeps_inactive_security_and_daily_status(fake_baostock):
    provider = BaoStockDataProvider()
    instruments = provider.list_research_instruments(as_of=date(2020, 6, 1))
    history = provider.get_research_history("600001", date(2019, 1, 1), date(2026, 8, 13))
    assert "600001" in instruments["symbol"].tolist()
    assert {"trade_status", "is_st", "research_return"}.issubset(history.columns)
    assert history.loc[history["trade_status"].eq("0"), "tradable"].eq(False).all()
```

- [ ] **Step 2: Run RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_baostock_research_history.py -q`  
Expected: `list_research_instruments` and `get_research_history` are missing.

- [ ] **Step 3: Add research-only provider methods**

`list_research_instruments()` must retain inactive/delisted ordinary A shares and expose `listed_date` and `delisted_date`. `get_research_history()` must request daily `tradestatus` and `isST`, obtain a separately versioned adjusted series for return labels, align it one-to-one with unadjusted sessions, and return only returns—not adjusted execution prices—to the research store. Missing or conflicting dates are marked unusable rather than filled.

- [ ] **Step 4: Prove execution data is unchanged**

Extend the existing BaoStock test to assert `get_daily_bars()` remains `baostock-unadjusted-v1` and retains its current schema. Add a test showing a corporate-action discontinuity changes `research_return` but never rewrites unadjusted `close`.

- [ ] **Step 5: Run GREEN and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_baostock_provider.py tests/test_baostock_research_history.py -q`  
Expected: all tests pass.  
Commit: `git commit -am "feat: add point-in-time research history provider"`

### Task 4: Build resumable seven-year backfill on D drive

**Files:**
- Create: `src/a_share_quant/runtime/historical_backfill.py`
- Create: `tests/test_historical_backfill.py`
- Modify: `scripts/quant_cli.py`
- Modify: `tests/test_quant_cli.py`

- [ ] **Step 1: Write failing orchestration tests**

```python
def test_backfill_resumes_only_missing_ranges_and_keeps_delisted_symbols(tmp_path):
    provider = RecordingHistoryProvider()
    coordinator = HistoricalBackfillCoordinator(store(tmp_path), provider, clock=fixed_clock)
    first = coordinator.run(start=date(2019, 1, 1), end=date(2026, 8, 13), limit=2)
    second = coordinator.run(start=date(2019, 1, 1), end=date(2026, 8, 13), limit=2)
    assert first.symbols_updated == 2
    assert second.rows_written == 0
    assert "600001" in coordinator.coverage().symbols  # inactive fixture


def test_backfill_stops_before_download_when_d_drive_space_is_low(tmp_path):
    coordinator = coordinator_with_free_bytes(tmp_path, free_bytes=1024)
    with pytest.raises(StorageQuotaError, match="剩余空间"):
        coordinator.run(start=date(2019, 1, 1), end=date(2026, 8, 13))
    assert coordinator.provider.calls == []
```

- [ ] **Step 2: Run RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_historical_backfill.py -q`  
Expected: coordinator is missing.

- [ ] **Step 3: Implement checkpointed batches**

Use batches of at most 100 symbols, persist a hashed checkpoint after every completed symbol, and delay requests according to `config/data.yaml`. The coordinator must retry only bounded transient failures, retain the last valid artifact, and produce `DatasetCoverage` with rows, sessions, earliest/latest dates, unavailable status fields, bytes, and exclusion reasons.

- [ ] **Step 4: Add explicit CLI commands**

```text
quant_cli.py history status
quant_cli.py history backfill --start 2019-01-01 --end 2026-08-13 --network
```

The backfill command must require `--network`, print the authorized D-drive targets before requests, and reject custom output paths. `history status` never accesses the network.

- [ ] **Step 5: Run GREEN and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_historical_backfill.py tests/test_quant_cli.py -q`  
Expected: all tests pass.  
Commit: `git commit -am "feat: add resumable historical backfill"`

### Task 5: Construct leakage-safe frozen research snapshots

**Files:**
- Create: `src/a_share_quant/research/research_snapshot.py`
- Create: `tests/test_research_snapshot.py`
- Modify: `src/a_share_quant/features/universe.py`
- Modify: `tests/test_historical_universe.py`

- [ ] **Step 1: Write failing leakage and survivorship tests**

```python
def test_snapshot_uses_only_information_visible_at_signal_date():
    snapshot = builder().build(signal_date=date(2021, 6, 30))
    assert snapshot.features["available_at"].le(date(2021, 6, 30)).all()
    assert "delisted-later" in snapshot.universe["symbol"].tolist()
    assert "listed-later" not in snapshot.universe["symbol"].tolist()


def test_unknown_st_or_trade_status_is_not_tradable():
    snapshot = builder_with_unknown_status().build(signal_date=date(2021, 6, 30))
    assert "unknown-status" not in snapshot.tradable_symbols
    assert snapshot.exclusions["unknown-status"] == "UNKNOWN_TRADE_STATUS"
```

- [ ] **Step 2: Run RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_research_snapshot.py tests/test_historical_universe.py -q`  
Expected: research snapshot builder is missing.

- [ ] **Step 3: Implement immutable snapshot fingerprints**

Build a snapshot from manifest-verified artifacts only. Store signal cutoff, universe version, feature version, label version, source artifact hashes, eligible symbols, exclusions, and a canonical SHA-256. Any later source change creates a new snapshot ID; it never mutates an evaluated snapshot.

- [ ] **Step 4: Run GREEN and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_research_snapshot.py tests/test_historical_universe.py -q`  
Expected: all tests pass.  
Commit: `git commit -am "feat: freeze point-in-time research snapshots"`

### Task 6: Evaluate challengers with frozen walk-forward evidence

**Files:**
- Create: `src/a_share_quant/research/historical_validation.py`
- Create: `tests/test_historical_validation.py`
- Modify: `src/a_share_quant/research/forecasting.py`
- Modify: `src/a_share_quant/research/production_gate.py`
- Modify: `tests/test_forecasting_pipeline.py`
- Modify: `tests/test_production_research_gate.py`

- [ ] **Step 1: Write failing fold, trial, and repeatability tests**

```python
def test_validator_never_selects_parameters_on_test_window():
    result = validator().run(snapshot(), candidates())
    for fold in result.folds:
        assert fold.train_end < fold.validation_start <= fold.validation_end
        assert fold.validation_end < fold.test_start <= fold.test_end
        assert fold.embargo_sessions == 126
    assert result.trial_count == len(candidates())


def test_same_snapshot_seed_and_candidates_are_bitwise_reproducible(tmp_path):
    first = validator(tmp_path / "one").run(snapshot(), candidates(), seed=20260814)
    second = validator(tmp_path / "two").run(snapshot(), candidates(), seed=20260814)
    assert first.canonical_digest == second.canonical_digest
```

- [ ] **Step 2: Run RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_historical_validation.py -q`  
Expected: historical validator is missing.

- [ ] **Step 3: Implement one fair evaluation path**

The validator must use the configured 756/252/126/126 windows, existing A-share cost/execution rules, and one shared snapshot for rule baseline, portable logistic regression, LightGBM, and optional Qlib models. Parameters are selected on validation data; test data is evaluated once. Record every attempted configuration, not only winners. Generate per-window excess return, rank IC, Brier/ECE, turnover, costs, drawdown, capacity, regime tags, PBO inputs, Deflated Sharpe inputs, and leakage/reproducibility flags.

- [ ] **Step 4: Harden the production gate**

Require at least three sample-out windows and reject missing/NaN evidence. Historical pass sets `HISTORICAL_PASS` only; it cannot create an approval token or modify `champion_id`.

- [ ] **Step 5: Run GREEN and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_historical_validation.py tests/test_forecasting_pipeline.py tests/test_production_research_gate.py -q`  
Expected: all tests pass.  
Commit: `git commit -am "feat: validate challengers on frozen historical windows"`

### Task 7: Add the 20/60-session maturity ledger

**Files:**
- Create: `src/a_share_quant/research/maturity.py`
- Create: `src/a_share_quant/storage/maturity_store.py`
- Create: `tests/test_maturity_ledger.py`
- Modify: `src/a_share_quant/research/evolution.py`
- Modify: `tests/test_controlled_model_evolution.py`

- [ ] **Step 1: Write failing state-transition tests**

```python
def test_historical_pass_needs_twenty_live_sessions_for_provisional():
    ledger = MaturityLedger(history=passing_history())
    ledger.settle(live_observations(sessions=19, matured=100))
    assert ledger.status == "SHADOW"
    ledger.settle(live_observations(sessions=20, matured=100))
    assert ledger.status == "PROVISIONAL"
    assert ledger.can_replace_champion is False


def test_sixty_sessions_still_requires_human_confirmation():
    ledger = mature_ledger(sessions=60, matured=200)
    assert ledger.status == "AWAITING_MANUAL_APPROVAL"
    assert registry.champion_id == "champion-v1"
    with pytest.raises(TypeError):
        registry.approve(ledger.report_id, auto_approve=True)
```

- [ ] **Step 2: Run RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_maturity_ledger.py -q`  
Expected: maturity ledger and store are missing.

- [ ] **Step 3: Implement distinct historical and live counters**

Persist `historical_oos_windows`, `historical_matured_samples`, `live_shadow_sessions`, `live_matured_predictions`, live Brier/ECE, net performance, drawdown, drift, data-quality failures, and status. Never add historical observations to live counters. Reprocessing an observation ID must be idempotent; future timestamps, duplicate outcomes with changed values, and predictions made after their outcome cutoff are rejected.

- [ ] **Step 4: Connect governance without weakening formal gates**

Extend `EvolutionEvaluator` to accept a verified maturity snapshot. It may issue an approval-ready report only at 60/200 plus all existing checks. `PROVISIONAL` is a display state and cannot issue a confirmation token.

- [ ] **Step 5: Run GREEN and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_maturity_ledger.py tests/test_controlled_model_evolution.py tests/test_model_governance_http.py -q`  
Expected: all tests pass.  
Commit: `git commit -am "feat: govern provisional and formal model maturity"`

### Task 8: Own backfill, validation, and settlement worker lifecycles

**Files:**
- Modify: `src/a_share_quant/runtime/research_jobs.py`
- Modify: `src/a_share_quant/runtime/research_worker.py`
- Modify: `scripts/quant_cli.py`
- Modify: `tests/test_research_job_supervisor.py`
- Create: `tests/test_research_worker.py`

- [ ] **Step 1: Write failing ownership and D-path tests**

```python
def test_worker_process_receives_only_d_drive_owned_environment(tmp_path, monkeypatch):
    supervisor = ResearchJobSupervisor(tmp_path / ".runtime/research", storage_policy=policy())
    supervisor.register_default_jobs(now=fixed_now)
    supervisor.start_due_jobs(now=fixed_now)
    env = captured_popen_kwargs["env"]
    assert env["TEMP"].startswith("D:\\量化交易\\.runtime")
    assert captured_popen_kwargs["cwd"] == "D:\\量化交易"


def test_shutdown_checkpoints_then_stops_download_and_training_children():
    supervisor = supervisor_with_running("history-backfill", "forecast-validation")
    result = supervisor.shutdown(timeout_seconds=5)
    assert result.checkpoint_saved
    assert result.children_stopped
    assert all(not child.is_running() for child in supervisor.owned_children())
```

- [ ] **Step 2: Run RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_research_job_supervisor.py tests/test_research_worker.py -q`  
Expected: storage policy and new job commands are unsupported.

- [ ] **Step 3: Implement fixed command mapping and schedule**

Allow only `("research", "history")`, `("research", "validate")`, and `("research", "settle")`. Map them internally to module invocations; callers cannot add paths or flags. Schedule history once per completed session, settlement after data refresh, and validation only when the dataset fingerprint changes. Keep one CPU training worker and one network worker maximum. The checkpoint records command, input fingerprint, stage, completed artifact digest, and next eligible time.

Add operator commands `quant_cli.py research status` and `quant_cli.py research validate`. `status` is read-only and offline; `validate` accepts no path arguments, reads only manifest-verified D-drive datasets, and never changes the champion.

- [ ] **Step 4: Run GREEN and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_research_job_supervisor.py tests/test_research_worker.py tests/test_quant_cli.py -q`  
Expected: all tests pass, including corrupt checkpoint and forced shutdown cases.  
Commit: `git commit -am "feat: own accelerated research job lifecycle"`

### Task 9: Publish Simplified Chinese storage and maturity evidence

**Files:**
- Modify: `src/a_share_quant/workbench/service.py`
- Modify: `src/a_share_quant/workbench/app.py`
- Modify: `tests/test_workbench_service.py`
- Modify: `tests/test_workbench_app.py`

- [ ] **Step 1: Write failing state and rendered-page tests**

```python
def test_state_separates_historical_and_live_maturity():
    state = service_with_maturity().state()
    assert state["research_maturity"]["status_zh"] == "临时挑战者"
    assert state["research_maturity"]["historical_oos_windows"] == 4
    assert state["research_maturity"]["live_shadow_sessions"] == 20
    assert state["research_maturity"]["formal_remaining_sessions"] == 40


def test_dashboard_explains_missing_guidance_and_d_drive_storage():
    html = render_dashboard(state_with_block("INSUFFICIENT_HISTORY"))
    assert "历史数据不足" in html
    assert "D:\\量化交易" in html
    assert "C盘写入已阻止" in html
    assert "预测准确率" not in html
```

- [ ] **Step 2: Run RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_workbench_service.py tests/test_workbench_app.py -q`  
Expected: maturity and storage fields are absent.

- [ ] **Step 3: Add bounded state objects and Chinese UI**

Expose only aggregate storage and research evidence: repo root, free bytes, dataset bytes, last download bytes, rejected-path count, champion/challenger IDs, historical windows, live sessions, matured predictions, status, remaining thresholds, risk checks, and reason codes. Do not expose local tokens, full provider URLs, environment variables, raw exception strings, or arbitrary filesystem paths.

Render distinct badges for `历史不足`, `历史验证中`, `影子观察`, `临时挑战者`, `待人工批准`, and `阻塞`. Historical and live metrics must occupy separate columns. Provisional recommendations must include `临时、未成熟、仅供观察` and never replace the official daily ranking.

- [ ] **Step 4: Run GREEN and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_workbench_service.py tests/test_workbench_app.py -q`  
Expected: all tests pass and all new user-visible strings are Simplified Chinese.  
Commit: `git commit -am "feat: show research maturity and D-drive storage"`

### Task 10: Perform real D-drive backfill and generate evidence

**Files:**
- Create at runtime: `data/lake/research_returns/*.parquet`
- Create at runtime: `data/lake/instrument_history/*.parquet`
- Create at runtime: `data/manifests/*.jsonl`
- Create at runtime: `.runtime/research/evidence/*.json`
- Create: `reports/research/historical_coverage_2026-08-14.md`
- Create: `reports/research/historical_validation_2026-08-14.md`

- [ ] **Step 1: Prepare process-local D-drive caches**

Run in PowerShell:

```powershell
$quantRuntime = 'D:\量化交易\.runtime'
$env:TEMP = "$quantRuntime\tmp"
$env:TMP = "$quantRuntime\tmp"
$env:PIP_CACHE_DIR = "$quantRuntime\cache\pip"
New-Item -ItemType Directory -Force $env:TEMP,$env:PIP_CACHE_DIR | Out-Null
```

Expected: both resolved directories are below `D:\量化交易`; no system-level variables are changed.

- [ ] **Step 2: Inspect before network use**

Run: `.venv\Scripts\python.exe scripts\quant_cli.py history status`  
Expected: JSON/Chinese output shows target paths on D, available bytes, current coverage, and no network request.

- [ ] **Step 3: Run bounded real backfill**

Run: `.venv\Scripts\python.exe scripts\quant_cli.py history backfill --start 2019-01-01 --end 2026-08-13 --network`  
Expected: at least 30 symbols reach 1750 sessions, all output paths resolve below `D:\量化交易`, failed symbols are enumerated without aborting completed symbols, and the checkpoint permits safe continuation.

- [ ] **Step 4: Run historical validation without promotion**

Run: `.venv\Scripts\python.exe scripts\quant_cli.py research validate`  
Expected: versioned evidence and Chinese report are written, champion ID is unchanged, and any insufficient gate is explicitly `BLOCKED`.

- [ ] **Step 5: Commit code and human-readable reports only**

Do not commit Parquet, caches, runtime state, tokens, or temporary files.  
Commit: `git add reports/research config && git commit -m "docs: record historical validation evidence"`

### Task 11: Full regression, storage audit, desktop lifecycle, and handoff

**Files:**
- Modify: `config/production_readiness.yaml`
- Modify: `reports/reliability_upgrade_handoff_2026-08-13.md`
- Create: `reports/research/historical_acceleration_acceptance_2026-08-14.md`

- [ ] **Step 1: Run complete automated verification**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m compileall -q src scripts
.venv\Scripts\python.exe -m pip check
git diff --check
```

Expected: zero test failures, zero Ruff errors, compileall and pip check exit 0, and no whitespace errors. Capability-based Windows symlink skips must be listed, not described as passes.

- [ ] **Step 2: Audit all new material locations**

Use `ProjectStoragePolicy.audit()` plus a read-only filesystem inventory captured before and after the run. Expected: every project-created historical, model, cache, temporary, manifest, and report path begins with `D:\量化交易`; no new project artifact is found on C. Record exact bytes by dataset in the acceptance report.

- [ ] **Step 3: Verify desktop start and stop**

Run `scripts\stop_quant_workbench.ps1`, then `scripts\start_quant_workbench.ps1`. Expected within the launch timeout: both pages open, `/api/health` reports `process_ready=true`, background history status is visible, and existing official daily/monitor data remains available or honestly stale. Run the stop script and verify no owned `research_worker` or backfill child remains.

- [ ] **Step 4: Update readiness honestly**

Set only evidenced checks to `PASS`. Keep formal model `BLOCKED` until the 60-session gate and manual approval are actually satisfied; keep broker read-only `BLOCKED` until QMT/XtQuant authorization exists. Add links to coverage, validation, storage audit, and test evidence.

- [ ] **Step 5: Commit final handoff**

Commit: `git add config/production_readiness.yaml reports && git commit -m "docs: hand off accelerated research pipeline"`

## Execution safeguards

- Every implementation task follows RED → minimal GREEN → focused regression → commit.
- Before any network call or dependency installation, set process-local D-drive cache/temp paths and print their resolved values.
- Do not lower the 60-session formal gate, auto-promote a model, overwrite unadjusted execution bars, scrape the broker UI, or add order APIs.
- If a free source cannot provide trustworthy historical status or corporate-action evidence, mark the affected sample unavailable and keep the corresponding readiness gate blocked.
- Stop execution on any attempted C-drive/project-external write, insufficient D-drive space, integrity mismatch, or uncontrolled child process.
