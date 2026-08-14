# Prospective Model Competition and D-Drive Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use historical data only for training and engineering screening, then compare frozen model versions exclusively on future, pre-registered out-of-sample predictions; 20 sessions/100 matured predictions are observation-only, while 60 sessions/200 matured predictions plus every frozen gate may enter human approval, with every new byte constrained to `D:\量化交易`.

**Architecture:** Add a single project-root storage policy beneath all new download, cache, temporary, model, evidence, and report writers. Build versioned research datasets beside—never over—the existing unadjusted execution data, use historical walk-forward/CPCV results only to detect engineering failures, freeze each contest version and its metrics before launch, and atomically append predictions before outcomes are known. A durable prospective ledger settles only matured, quality-qualified future outcomes and feeds the existing human-controlled evolution registry. The workbench owns background backfill/training/settlement workers and exposes storage and prospective maturity evidence in Simplified Chinese.

**Tech Stack:** Python 3.12, pandas, PyArrow/Parquet, DuckDB, BaoStock, AKShare, scikit-learn, LightGBM, existing reference backtester, stdlib HTTP/server/process APIs, pytest, Ruff, PowerShell launcher.

---

## File and responsibility map

- `src/a_share_quant/storage/project_storage.py`: canonical D-drive path authorization, reparse-point rejection, quotas, child-process cache environment.
- `src/a_share_quant/storage/research_data_store.py`: atomic Parquet storage and integrity manifests for instrument history, research returns, corporate actions, trials, and evidence.
- `src/a_share_quant/data/providers/baostock.py`: free historical daily status and research-return retrieval without changing execution-bar semantics.
- `src/a_share_quant/research/history_contracts.py`: typed point-in-time instrument, corporate-action, and dataset-profile contracts.
- `src/a_share_quant/runtime/historical_backfill.py`: resumable seven-year backfill orchestration and coverage reporting.
- `src/a_share_quant/research/historical_screening.py`: frozen historical engineering checks, trial accounting, and explicitly non-promotional PBO/DSR/CPCV diagnostics.
- `src/a_share_quant/research/prospective_competition.py`: frozen contest registration, append-only predictions, quality-aware settlement, metrics, and 20/100 versus 60/200 state machine.
- `src/a_share_quant/storage/prospective_ledger_store.py`: atomic append, idempotency, immutable failed predictions, and versioned outcome records.
- `src/a_share_quant/runtime/research_jobs.py`: allowlisted owned jobs, D-drive-only child environment, checkpoints, and shutdown.
- `src/a_share_quant/runtime/research_worker.py`: backfill, validate, and settle commands using only repository-owned paths.
- `src/a_share_quant/workbench/service.py`: storage and maturity state publication.
- `src/a_share_quant/workbench/app.py`: Simplified Chinese maturity/storage UI and JSON endpoints.
- `scripts/quant_cli.py`: bounded operator commands for history status/backfill, engineering screening, contest freeze/start, and prospective status/settlement.
- `scripts/start_quant_workbench.ps1`: process-local D-drive cache/temp environment.
- `config/research_maturity.yaml`: historical coverage, prospective primary metric/tie-break, 20/100, 60/200, quota, and scheduling thresholds.
- `reports/research/`: generated Chinese coverage, engineering-screening, and prospective contest reports.

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


def test_duplicate_digest_is_idempotent_and_cleanup_removes_only_rebuildable_temp(tmp_path):
    store = bounded_store(tmp_path)
    first = store.replace_dataset("research_returns", "000001", frame(), data_version="v1")
    second = store.replace_dataset("research_returns", "000001", frame(), data_version="v1")
    assert second.sha256 == first.sha256
    assert store.manifest_count(first.sha256) == 1
    store.cleanup_rebuildable_temporary_files()
    assert first.path.exists()
```

- [ ] **Step 2: Run RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_research_data_store.py -q`  
Expected: collection fails because the store and contracts do not exist.

- [ ] **Step 3: Implement focused contracts and atomic store**

Define immutable `ResearchArtifact`, `DatasetCoverage`, `PointInTimeInstrument`, and `CorporateAction` dataclasses. `ResearchDataStore.replace_dataset()` must write a same-directory temporary Parquet file, `fsync`, calculate SHA-256 and size, enforce per-file/dataset quotas before publication, then `os.replace` and atomically append a JSONL manifest record. Repeated content hashes are idempotently deduplicated. Cleanup may remove only repository-owned, safely rebuildable temporary files; datasets, manifests, models, predictions, settlements, audit records, and failed predictions are never cleanup targets. Valid dataset names are a fixed enum, never caller-created directories.

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
  evidence_mode: "NON_PROMOTIONAL_ENGINEERING"
prospective_competition:
  primary_metric: "net_cost_return"
  tie_break: ["max_drawdown", "brier", "ece", "rank_ic", "turnover"]
  provisional_sessions: 20
  provisional_matured_predictions: 100
  approval_sessions: 60
  approval_matured_predictions: 200
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

### Task 6: Run historical engineering screening without promotion

**Files:**
- Create: `src/a_share_quant/research/historical_screening.py`
- Create: `tests/test_historical_engineering_screening.py`
- Modify: `src/a_share_quant/research/forecasting.py`
- Modify: `src/a_share_quant/research/production_gate.py`
- Modify: `tests/test_forecasting_pipeline.py`
- Modify: `tests/test_production_research_gate.py`

- [ ] **Step 1: Write failing fold, repeatability, and non-promotional tests**

```python
def test_screen_never_selects_parameters_on_test_window():
    result = screen().run(snapshot(), candidates())
    for fold in result.folds:
        assert fold.train_end < fold.validation_start <= fold.validation_end
        assert fold.validation_end < fold.test_start <= fold.test_end
        assert fold.embargo_sessions == 126
    assert result.trial_count == len(candidates())


def test_same_snapshot_seed_and_candidates_are_bitwise_reproducible(tmp_path):
    first = screen(tmp_path / "one").run(snapshot(), candidates(), seed=20260814)
    second = screen(tmp_path / "two").run(snapshot(), candidates(), seed=20260814)
    assert first.canonical_digest == second.canonical_digest


def test_historical_metrics_can_only_block_engineering_failures():
    result = screen_with_exceptional_historical_metrics().run(snapshot(), candidates())
    assert result.evidence_mode == "NON_PROMOTIONAL_ENGINEERING"
    assert result.can_rank_models is False
    assert result.can_issue_provisional is False
    assert result.can_issue_approval_token is False
    assert production_gate.champion_id == "champion-v1"
```

- [ ] **Step 2: Run RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_historical_engineering_screening.py -q`
Expected: historical engineering screen is missing.

- [ ] **Step 3: Implement one diagnostic evaluation path**

The screen must use the configured 756/252/126/126 windows, existing A-share cost/execution rules, and one shared snapshot for rule baseline, portable logistic regression, LightGBM, and optional Qlib models. Parameters are selected on validation data; test data is evaluated once. Record every attempted configuration, not only favorable ones. Generate per-window excess return, rank IC, Brier/ECE, turnover, costs, drawdown, capacity, regime tags, PBO/Deflated Sharpe/CPCV diagnostics, and leakage/reproducibility flags, all labeled `NON_PROMOTIONAL_ENGINEERING`. Historical output may train models, diagnose interfaces/cost/leakage, or exclude models that cannot run or are plainly wrong; it must never rank a winner or trigger provisional/formal/promotion state.

- [ ] **Step 4: Prove the production gate ignores historical performance**

Missing/NaN evidence, leakage, execution failure, or inconsistent artifacts may set `ENGINEERING_BLOCKED`. There is no `HISTORICAL_PASS`: PBO, DSR, CPCV, historical Sharpe, historical rank IC, and every other historical score cannot create a favorable governance state, approval token, model ordering, or `champion_id` change.

- [ ] **Step 5: Run GREEN and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_historical_engineering_screening.py tests/test_forecasting_pipeline.py tests/test_production_research_gate.py -q`
Expected: all tests pass.  
Commit: `git commit -am "feat: restrict history to engineering screening"`

### Task 7: Add the pre-registered prospective competition ledger

**Files:**
- Create: `src/a_share_quant/research/prospective_competition.py`
- Create: `src/a_share_quant/storage/prospective_ledger_store.py`
- Create: `tests/test_prospective_competition_ledger.py`
- Modify: `src/a_share_quant/research/evolution.py`
- Modify: `tests/test_controlled_model_evolution.py`

- [ ] **Step 1: Write failing pre-registration, settlement, and governance tests**

```python
def test_prediction_is_atomically_appended_before_outcome_and_never_deleted():
    prediction = ledger.append_prediction(
        model_id="challenger", model_version="v1", config_hash="cfg",
        training_snapshot_hash="train", symbol="600001", name="示例",
        prediction_at=frozen_now, as_of=frozen_session, horizon=5,
        score=0.73, probability=0.68, guidance_price_bands=price_bands(),
        evidence_mode="PROSPECTIVE",
    )
    assert ledger.read(prediction.id) == prediction
    with pytest.raises(ImmutablePredictionError):
        ledger.delete(prediction.id)


def test_missing_suspended_or_stale_outcome_delays_settlement():
    result = ledger.settle_due(outcome(status="SUSPENDED", data_version="bars-v3"))
    assert result.matured is False
    assert result.delay_reason == "SUSPENDED"
    assert ledger.matured_predictions == 0


def test_twenty_sessions_is_observation_only_and_sixty_requires_manual_approval():
    ledger.settle(qualified_future_observations(sessions=20, matured=100))
    assert ledger.status == "PROVISIONAL_UNMATURED_OBSERVATION"
    assert ledger.can_replace_champion is False
    ledger.settle(qualified_future_observations(sessions=60, matured=200))
    assert ledger.status == "AWAITING_MANUAL_APPROVAL"
    assert registry.champion_id == "champion-v1"


def test_new_version_resets_future_counts_and_metrics_are_immutable_after_start():
    contest = started_contest(primary_metric="net_cost_return", tie_break=["max_drawdown", "brier"])
    contest.register_version(model_version="v2", config_hash="changed")
    assert contest.future_sessions == 0
    assert contest.matured_predictions == 0
    with pytest.raises(FrozenContestError):
        contest.change_primary_metric("directional_hit_rate")
```

- [ ] **Step 2: Run RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_prospective_competition_ledger.py -q`
Expected: prospective competition ledger and store are missing.

- [ ] **Step 3: Implement immutable prediction and quality-aware settlement records**

Before the contest starts, freeze `contest_started_at`, model/version, `config_hash`, `training_snapshot_hash`, primary metric, tie-break order, horizons, thresholds, costs, and regime definitions. Atomically append each prediction before its result is known with model/version/config hash/training snapshot hash/symbol/name/prediction_at/as_of/horizon/score/probability/guidance price bands/evidence mode. Records are immutable: failed predictions cannot be overwritten or deleted, and revisions require a new model version.

Settle only after the horizon expires and the realized market data passes freshness, completeness, session alignment, and corporate-action checks. Missing, suspended, stale, or conflicting data remains pending with a delay reason and is neither success nor failure. Append the outcome data version and SHA-256 to each settlement. Reprocessing a prediction/outcome ID is idempotent; changed duplicate outcomes, future timestamps, predictions appended after their outcome cutoff, and any mutation are rejected.

- [ ] **Step 4: Calculate frozen prospective metrics and connect governance**

For each frozen model version calculate directional hit rate, Brier, ECE, rank IC, net-of-commission/tax/slippage return, maximum drawdown, turnover, coverage/rejection rate, and regime stability. The pre-registered primary metric drives comparison and the frozen tie-break resolves ties; every mandatory quality/risk threshold must pass, and neither may change after results are visible. At 20 future sessions/100 matured predictions expose only `PROVISIONAL_UNMATURED_OBSERVATION`, which never replaces the champion or issues a token. Only 60 future sessions/200 matured predictions plus every frozen gate may issue an approval-ready report; human approval remains mandatory, and there is no automatic promotion or order path. A model/version/config change creates a new contestant with counters reset to zero.

- [ ] **Step 5: Run GREEN and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_prospective_competition_ledger.py tests/test_controlled_model_evolution.py tests/test_model_governance_http.py -q`
Expected: all tests pass.  
Commit: `git commit -am "feat: govern prospective model competition"`

### Task 8: Own backfill, engineering-screening, prediction, and settlement worker lifecycles

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
    supervisor = supervisor_with_running("history-backfill", "historical-screen", "prospective-settle")
    result = supervisor.shutdown(timeout_seconds=5)
    assert result.checkpoint_saved
    assert result.children_stopped
    assert all(not child.is_running() for child in supervisor.owned_children())
```

- [ ] **Step 2: Run RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_research_job_supervisor.py tests/test_research_worker.py -q`  
Expected: storage policy and new job commands are unsupported.

- [ ] **Step 3: Implement fixed command mapping and schedule**

Allow only `("research", "history")`, `("research", "screen")`, `("research", "predict")`, and `("research", "settle")`. Map them internally to module invocations; callers cannot add paths or flags. Schedule history once per completed session, historical engineering screening only when the training dataset fingerprint changes, prediction before the eligible outcome window opens, and settlement after data refresh. Keep one CPU training worker and one network worker maximum. The checkpoint records command, input fingerprint, stage, completed artifact digest, and next eligible time.

Add operator commands `quant_cli.py research status`, `quant_cli.py research screen`, and `quant_cli.py research contest-start`. `status` is read-only and offline; `screen` accepts no path arguments, reads only manifest-verified D-drive datasets, labels all outputs non-promotional, and never changes the champion. `contest-start` freezes the selected engineering-valid model versions, training snapshot hashes, primary metric, tie-break, thresholds, and start time before any prospective outcomes exist.

- [ ] **Step 4: Run GREEN and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_research_job_supervisor.py tests/test_research_worker.py tests/test_quant_cli.py -q`  
Expected: all tests pass, including corrupt checkpoint and forced shutdown cases.  
Commit: `git commit -am "feat: own prospective research job lifecycle"`

### Task 9: Publish Simplified Chinese storage and prospective contest evidence

**Files:**
- Modify: `src/a_share_quant/workbench/service.py`
- Modify: `src/a_share_quant/workbench/app.py`
- Modify: `tests/test_workbench_service.py`
- Modify: `tests/test_workbench_app.py`

- [ ] **Step 1: Write failing state and rendered-page tests**

```python
def test_state_separates_historical_screening_and_prospective_maturity():
    state = service_with_maturity().state()
    contest = state["prospective_competition"]
    assert contest["status_zh"] == "临时未成熟观察"
    assert contest["contest_started_at"] == "2026-08-14"
    assert contest["future_matured_sessions"] == 20
    assert contest["future_matured_predictions"] == 100
    assert contest["approval_remaining_sessions"] == 40
    assert contest["approval_remaining_predictions"] == 100


def test_dashboard_explains_missing_guidance_and_d_drive_storage():
    html = render_dashboard(state_with_contest())
    assert "历史结果不参与晋升" in html
    assert "失败预测不可删除" in html
    assert "比赛开始日" in html
    assert "距临时门槛" in html
    assert "距正式审批门槛" in html
    assert "D:\\量化交易" in html
    assert "C盘写入已阻止" in html
    assert "当前状态" in html
```

- [ ] **Step 2: Run RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_workbench_service.py tests/test_workbench_app.py -q`  
Expected: prospective contest and storage fields are absent.

- [ ] **Step 3: Add bounded state objects and Chinese UI**

Expose only aggregate storage and research evidence: repo root, free bytes, dataset bytes, last download bytes, rejected-path count, champion/contestant version IDs, contest start date, future matured sessions, future matured predictions, current status, remaining 20/100 and 60/200 thresholds, frozen primary metric/tie-break, risk checks, and reason codes. Do not expose local tokens, full provider URLs, environment variables, raw exception strings, or arbitrary filesystem paths.

Render distinct badges for `历史工程筛查中`, `工程阻塞`, `未来竞赛中`, `临时未成熟观察`, and `待人工审批`. The Chinese UI must always show `比赛开始日`, `未来成熟交易日/预测数`, `失败预测不可删除`, `历史结果不参与晋升`, `距临时门槛`, `距正式审批门槛`, and `当前状态`. Historical diagnostics and prospective metrics occupy separate columns. Provisional observations include `临时、未成熟、仅供观察`, never declare a winner, and never replace the official daily ranking.

- [ ] **Step 4: Run GREEN and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_workbench_service.py tests/test_workbench_app.py -q`  
Expected: all tests pass and all new user-visible strings are Simplified Chinese.  
Commit: `git commit -am "feat: show prospective contest and D-drive storage"`

### Task 10: Backfill training data, freeze contestants, and start the prospective contest

**Files:**
- Create at runtime: `data/lake/research_returns/*.parquet`
- Create at runtime: `data/lake/instrument_history/*.parquet`
- Create at runtime: `data/manifests/*.jsonl`
- Create at runtime: `.runtime/research/evidence/*.json`
- Create at runtime: `.runtime/research/prospective/predictions/*.jsonl`
- Create: `reports/research/historical_coverage_2026-08-14.md`
- Create: `reports/research/historical_engineering_screening_2026-08-14.md`
- Create: `reports/research/prospective_contest_start_2026-08-14.md`

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

- [ ] **Step 4: Run historical engineering screening, then freeze and start the contest**

Run:

```powershell
.venv\Scripts\python.exe scripts\quant_cli.py research screen
.venv\Scripts\python.exe scripts\quant_cli.py research contest-start
```

Expected: training inputs and non-promotional engineering evidence are versioned; models that cannot run or are plainly wrong are excluded without naming a historical winner. `contest-start` freezes model/version/config hashes, training snapshot hashes, primary metric, tie-break, thresholds, and contest start time before future outcomes exist. The champion ID is unchanged and all prospective counters start at zero.

- [ ] **Step 5: Commit code and human-readable reports only**

Do not commit Parquet, caches, runtime state, tokens, or temporary files.  
Commit: `git add reports/research config && git commit -m "docs: record prospective contest start"`

### Task 11: Full regression, storage audit, desktop lifecycle, and handoff

**Files:**
- Modify: `config/production_readiness.yaml`
- Modify: `reports/reliability_upgrade_handoff_2026-08-13.md`
- Create: `reports/research/prospective_competition_acceptance_2026-08-14.md`

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

Set only evidenced engineering checks to `PASS`; never translate a historical score into model preference. Keep the contest `PROSPECTIVE_COLLECTING` until 60 future sessions/200 matured future predictions and every frozen gate are actually satisfied, then at most `AWAITING_MANUAL_APPROVAL`. Keep broker read-only `BLOCKED` until QMT/XtQuant authorization exists. Add links to coverage, engineering screening, contest registration, storage audit, and test evidence.

- [ ] **Step 5: Commit final handoff**

Commit: `git add config/production_readiness.yaml reports && git commit -m "docs: hand off prospective competition pipeline"`

## Execution safeguards

- Every implementation task follows RED → minimal GREEN → focused regression → commit.
- Before any network call or dependency installation, set process-local D-drive cache/temp paths and print their resolved values.
- Historical PBO/DSR/CPCV/Sharpe and every other historical result are diagnostic only: never use them to choose a winner, issue provisional/formal status, shorten prospective collection, or promote a model.
- Do not lower the 60-session/200-matured approval-entry gate, mutate frozen metrics after contest start, delete failed predictions, auto-promote a model, overwrite unadjusted execution bars, scrape the broker UI, or add order APIs.
- If a free source cannot provide trustworthy historical status or corporate-action evidence, mark the affected sample unavailable and keep the corresponding readiness gate blocked.
- Stop execution on any attempted C-drive/project-external write, insufficient D-drive space, integrity mismatch, or uncontrolled child process.
