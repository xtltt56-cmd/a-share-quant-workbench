# Qlib PIT Baselines Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a point-in-time A-share feature bridge, three fair baselines, one fixed out-of-sample comparison, and one rolling walk-forward demo without enabling live trading.

**Architecture:** PIT and historical universe logic live in `features/` and use project-owned canonical frames. Qlib is lazy-loaded only inside `integrations/qlib/`; models consume a project dataset artifact and emit project-owned predictions. `signals/` normalizes Rule, LightGBM, and DoubleEnsemble outputs. `experiments/` records immutable metadata, OOS metrics, predictions, and reports.

**Tech Stack:** Python 3.12, pandas, NumPy, DuckDB/Parquet, Qlib/Alpha158/LightGBM/DoubleEnsemble, pytest, Ruff, QuantStats-compatible metrics, installed `report-renderer` Skill.

---

### Task 1: Freeze Stage 2 configuration and contracts

**Files:**
- Create: `config/pit.yaml`, `config/qlib.yaml`, `config/experiments.yaml`
- Create: `src/a_share_quant/features/__init__.py`
- Create: `src/a_share_quant/signals/__init__.py`
- Create: `tests/test_stage2_config.py`, `tests/test_signal_schema.py`
- Modify: `README.md`, `docs/superpowers/plans/2026-08-08-framework-first-redesign.md`

- [ ] **Step 1: Write failing contract tests**

```python
def test_default_pit_policy_uses_next_trading_day():
    config = Stage2Config.load(...)
    assert config.pit.announcement_day_policy == "next_trading_day"
    assert config.pit.allow_same_day_announcement is False

def test_signal_schema_requires_common_version_fields():
    record = SignalRecord(...)
    assert record.strategy_id
    assert record.model_version
    assert record.feature_version
    assert record.data_version
```

- [ ] **Step 2: Run the focused tests and confirm the missing-contract failure**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_stage2_config.py tests/test_signal_schema.py -q`

Expected: collection failure because `Stage2Config` and `SignalRecord` do not yet exist.

- [ ] **Step 3: Implement minimal YAML settings and immutable signal record**

Implement `Stage2Config.load()` with the default next-trading-day policy, fixed label `forward_excess_return_5d`, no random split, and explicit shared benchmark/cost/position settings. Implement `SignalRecord` as a frozen dataclass and `SignalProvider` Protocol without importing Qlib.

- [ ] **Step 4: Run tests and lint**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_stage2_config.py tests/test_signal_schema.py -q` and `\.venv\Scripts\python.exe -m ruff check .`

Expected: focused tests pass and Ruff reports no errors.

### Task 2: Implement PIT records and as-of queries

**Files:**
- Create: `src/a_share_quant/features/pit_store.py`
- Create: `tests/test_pit_financial_data.py`, `tests/test_feature_asof.py`, `tests/test_announcement_date.py`, `tests/test_future_leak.py`

- [ ] **Step 1: Write the synthetic PIT tests first**

Use one record with `report_period=2025-12-31`, `announcement_date=2026-03-30`, `effective_date=2026-03-31`, and `ingest_time=2026-03-30T20:00:00+08:00`:

```python
assert store.get_features_asof("000001", date(2026, 3, 29)).empty
assert store.get_features_asof("000001", date(2026, 3, 30)).empty
assert store.get_features_asof("000001", date(2026, 3, 31)).loc[0, "roe"] == 0.12
```

Add a same-day-policy fixture that is visible on 2026-03-30 only when `allow_same_day_announcement=True`. Add a future-leak test proving `ingest_time` alone never changes visibility and no report-period-only forward fill occurs.

- [ ] **Step 2: Run the PIT tests and verify they fail for missing store/API**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_pit_financial_data.py tests/test_feature_asof.py tests/test_announcement_date.py tests/test_future_leak.py -q`

Expected: import/attribute failures for `PITFeatureStore`.

- [ ] **Step 3: Implement the minimal PIT store**

Define a canonical long-form schema, validate required timestamps, calculate `effective_date` from the configured policy, reject unknown announcement dates, and implement `get_features_asof(symbol, asof_date)` with an `effective_date <= asof_date` predicate and deterministic version selection. Never use `report_period` as a visibility predicate.

- [ ] **Step 4: Run the focused PIT tests, then the full existing suite**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_pit_financial_data.py tests/test_feature_asof.py tests/test_announcement_date.py tests/test_future_leak.py -q` and `\.venv\Scripts\python.exe -m pytest -q`

Expected: all PIT and previous Stage 1 tests pass.

### Task 3: Historical tradable universe

**Files:**
- Create: `src/a_share_quant/features/universe.py`
- Create: `tests/test_survivorship_bias.py`, `tests/test_tradable_universe.py`

- [ ] **Step 1: Write tests for listing, delisting, ST and suspension intervals**

Create synthetic history where `000001` is listed and tradable, `000002` delists on 2026-03-01, `000003` becomes ST on 2026-02-15, and `000004` is suspended on 2026-02-20. Assert `tradable_universe(date)` includes/excludes each symbol according to that date and never removes `000002` from the 2026-02-28 historical universe merely because it is absent from today’s list.

- [ ] **Step 2: Run the universe tests and confirm missing implementation failures**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_survivorship_bias.py tests/test_tradable_universe.py -q`

Expected: collection failure for `HistoricalUniverse`.

- [ ] **Step 3: Implement interval-based historical filtering**

Implement `HistoricalUniverse.tradable_universe(asof_date)` with inclusive listing start, exclusive delisting/ST/suspension end semantics, optional liquidity and price checks, and explicit `data_version`. Do not call the live AKShare list inside this class.

- [ ] **Step 4: Run universe and full tests**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_survivorship_bias.py tests/test_tradable_universe.py tests/test_pit_financial_data.py -q` and `\.venv\Scripts\python.exe -m pytest -q`

Expected: all tests pass.

### Task 4: Install Qlib research profile and create adapter boundary

**Files:**
- Create: `src/a_share_quant/integrations/__init__.py`
- Create: `src/a_share_quant/integrations/qlib/__init__.py`
- Create: `src/a_share_quant/integrations/qlib/provider.py`
- Create: `src/a_share_quant/integrations/qlib/calendar_adapter.py`
- Create: `src/a_share_quant/integrations/qlib/instrument_adapter.py`
- Create: `tests/test_qlib_availability.py`, `tests/test_qlib_calendar_adapter.py`
- Modify: `.gitignore`, `DEPENDENCIES.md`

- [ ] **Step 1: Install the optional research profile**

Run: `\.venv\Scripts\python.exe -m pip install -e ".[research]"`

Then record actual `pyqlib`, `lightgbm`, `optuna`, and `quantstats` versions in the local experiment metadata; do not commit the environment directory.

- [ ] **Step 2: Write adapter tests before Qlib integration code**

Test `QlibAvailability` reports a clear optional-dependency error when Qlib is not importable, and `CalendarAdapter` returns sorted unique trade dates without importing Qlib in the contract-only test.

- [ ] **Step 3: Implement lazy Qlib availability and project-owned calendar/instrument adapters**

`QlibProvider` must lazy-import Qlib, expose `require_available()`, and never leak a Token or provider payload. Calendar and instrument adapters accept canonical project frames and return deterministic data artifacts.

- [ ] **Step 4: Run focused tests, lint and pip check**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_qlib_availability.py tests/test_qlib_calendar_adapter.py -q`, `\.venv\Scripts\python.exe -m ruff check .`, and `\.venv\Scripts\python.exe -m pip check`.

### Task 5: Build Qlib-compatible PIT dataset and label

**Files:**
- Create: `src/a_share_quant/integrations/qlib/dataset_builder.py`
- Create: `src/a_share_quant/integrations/qlib/feature_adapter.py`
- Create: `src/a_share_quant/integrations/qlib/label_adapter.py`
- Create: `tests/test_dataset_split.py`, `tests/test_qlib_dataset_builder.py`, `tests/test_label_no_leak.py`

- [ ] **Step 1: Write time-split and label tests**

Assert no random split is used; the last five trade dates have missing labels; `forward_excess_return_5d` equals asset five-day return minus benchmark five-day return; label columns never appear in feature columns; and Qlib dataset artifacts include symbol/date index, feature schema and dataset hash.

- [ ] **Step 2: Run tests to observe missing builder failures**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_dataset_split.py tests/test_qlib_dataset_builder.py tests/test_label_no_leak.py -q`

Expected: import failures for `QlibDatasetBuilder`.

- [ ] **Step 3: Implement dataset builder and time split**

Materialize an immutable Parquet dataset from PIT features, historical universe and daily bars. Use project-owned `TimeSplit` and label code. The Qlib adapter may wrap the artifact with official `DatasetH`/Handler APIs discovered from the installed version, but the artifact and metadata remain project-owned.

- [ ] **Step 4: Run dataset tests and inspect one synthetic artifact**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_dataset_split.py tests/test_qlib_dataset_builder.py tests/test_label_no_leak.py -q` and verify the output schema has no future label in feature columns.

### Task 6: RuleBasedMultiFactor baseline and Signal Schema

**Files:**
- Create: `src/a_share_quant/features/rule_factors.py`
- Create: `src/a_share_quant/signals/schema.py`
- Create: `src/a_share_quant/signals/rule_provider.py`
- Create: `tests/test_rule_factors.py`, `tests/test_rule_signal_provider.py`

- [ ] **Step 1: Write factor tests for winsorization, missing values, standardization and fixed weights**

Test eight factor groups: Momentum, Trend, Relative Strength, Volume, Turnover, Volatility, Quality and Value. Assert cross-sectional clipping, deterministic missing-value behavior, fixed YAML weights, score range 0–100 and versioned factor snapshots. Money Flow is absent/optional in this baseline.

- [ ] **Step 2: Run rule tests and verify missing implementation failures**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_rule_factors.py tests/test_rule_signal_provider.py -q`

Expected: import failure for `RuleFactorEngine`.

- [ ] **Step 3: Implement the minimal fixed-weight factor engine and provider**

Compute factors only from the as-of feature frame, winsorize per date, standardize per date, map the weighted sum to 0–100, and emit `SignalRecord` with `strategy_id=rule_multifactor`, `strategy_version=rule_multifactor_v1`, `model_version=rule_none_v1`, `feature_version=rule_features_v1`.

- [ ] **Step 4: Run rule and full tests**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_rule_factors.py tests/test_rule_signal_provider.py -q` and `\.venv\Scripts\python.exe -m pytest -q`.

### Task 7: Official-style Alpha158 LightGBM runner

**Files:**
- Create: `src/a_share_quant/integrations/qlib/model_runner.py`
- Create: `src/a_share_quant/signals/qlib_provider.py`
- Create: `tests/test_qlib_model_runner.py`, `tests/test_qlib_signal_provider.py`

- [ ] **Step 1: Write runner tests against a tiny deterministic dataset**

Test the runner records Qlib/library versions, feature schema, label, split, seed, params, model artifact path and prediction schema. Test a missing Qlib installation produces a clear `QlibAvailabilityError` rather than silently falling back to a non-Qlib model.

- [ ] **Step 2: Run tests before implementation**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_qlib_model_runner.py tests/test_qlib_signal_provider.py -q`

Expected: import failures for `QlibAlpha158Runner`.

- [ ] **Step 3: Implement Qlib Alpha158 + LightGBM through the adapter**

Use the installed Qlib official Alpha158/LightGBM path as far as the installed release permits. Keep the project dataset builder and output conversion outside Qlib. Save `qlib_lgb_alpha158_v1` metadata, predictions and model artifact under one experiment ID.

- [ ] **Step 4: Run the real Qlib smoke**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_qlib_model_runner.py tests/test_qlib_signal_provider.py -q` and a bounded script using the synthetic/local dataset that ends with a non-empty predictions Parquet. Expected: Qlib imports, Alpha158 feature stage runs, LightGBM trains, predictions include `strategy_id` and version fields.

### Task 8: Official-style Alpha158 DoubleEnsemble runner

**Files:**
- Modify: `src/a_share_quant/integrations/qlib/model_runner.py`, `src/a_share_quant/signals/qlib_provider.py`
- Create: `tests/test_double_ensemble_runner.py`

- [ ] **Step 1: Write a parity test**

Assert DoubleEnsemble uses the exact same dataset hash, label, split dates, universe, cost metadata, seed and feature version as LightGBM, while its `model_version` is `qlib_doubleensemble_alpha158_v1`.

- [ ] **Step 2: Run the parity test and confirm missing runner behavior**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_double_ensemble_runner.py -q`

Expected: failure because the DoubleEnsemble branch is not registered.

- [ ] **Step 3: Add only the official-style DoubleEnsemble branch**

Reuse the same Qlib dataset/label/split adapter and call the official DoubleEnsemble implementation when available. Do not add LSTM, Transformer, RL or parameter search.

- [ ] **Step 4: Run both real model smokes**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_qlib_model_runner.py tests/test_double_ensemble_runner.py -q` and verify both prediction artifacts are non-empty and schema-compatible.

### Task 9: Experiment tracking, fixed OOS comparison and Walk-Forward

**Files:**
- Create: `src/a_share_quant/experiments/metadata.py`
- Create: `src/a_share_quant/experiments/runner.py`
- Create: `src/a_share_quant/experiments/walk_forward.py`
- Create: `src/a_share_quant/experiments/metrics.py`
- Create: `tests/test_experiment_metadata.py`, `tests/test_walk_forward.py`, `tests/test_fair_comparison.py`

- [ ] **Step 1: Write metadata, fixed OOS and OOS-only window tests**

Assert every experiment has a unique ID and required metadata; fair comparison rejects mismatched label/split/universe/cost metadata; Walk-Forward output contains only each window’s test segment and never concatenates validation predictions as OOS.

- [ ] **Step 2: Run tests to verify missing experiment runner failures**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_experiment_metadata.py tests/test_walk_forward.py tests/test_fair_comparison.py -q`

Expected: import failures for `ExperimentRunner` and `WalkForwardRunner`.

- [ ] **Step 3: Implement immutable local experiment artifacts and metrics**

Write `config.yaml`, `metadata.json`, `metrics.json`, `predictions.parquet`, `equity.parquet`, `model/` and `report/` below `experiments/<experiment_id>`. Implement IC, Rank IC, ICIR, Top-K Return, Excess Return, CAGR, Sharpe, Sortino, Max Drawdown, turnover, hit rate and yearly stability without reading the test window during fitting.

- [ ] **Step 4: Run one fixed OOS comparison and one Walk-Forward demo**

Use a bounded synthetic/local dataset; assert all three providers emit the same Signal Schema, comparison output has three rows, and Walk-Forward has at least one OOS window.

### Task 10: Report, README, quality gates and commit

**Files:**
- Create: `src/a_share_quant/experiments/reporting.py`
- Create: `scripts/run_baselines.py`
- Create: `scripts/run_walk_forward.py`
- Create: `tests/test_baseline_report.py`
- Modify: `README.md`, `config/`, `.gitignore`, `docs/superpowers/plans/2026-08-08-framework-first-redesign.md`

- [ ] **Step 1: Write report contract test**

Assert comparison Markdown includes data coverage, PIT rule, label, split, model parameters, IC/Rank IC/ICIR, returns, drawdown, Sharpe, turnover, yearly stability, failure cases and risk notes; no report is generated from missing predictions.

- [ ] **Step 2: Implement Markdown comparison and renderer handoff**

Generate `reports/baseline_comparison.md` and, only when the input Markdown exists, invoke the installed `report-renderer` Skill-compatible command to produce HTML. Keep generated reports ignored by Git.

- [ ] **Step 3: Run the full verification gate**

Run:

```powershell
\.venv\Scripts\python.exe -m pytest -q --cov=src/a_share_quant --cov-report=term-missing
\.venv\Scripts\python.exe -m ruff check .
\.venv\Scripts\python.exe -m pip check
\.venv\Scripts\python.exe -m compileall -q src scripts
```

Expected: all tests pass, coverage is at least 85% (target 88%+), Ruff/pip/compileall exit 0, and no live broker import exists.

- [ ] **Step 4: Update README and commit**

Record actual Qlib/LightGBM versions, fixed OOS and Walk-Forward results, known data gaps, and the next-stage recommendation. Run `git diff --check`, inspect that `experiments/`, `models/`, reports and local data remain ignored, then commit:

```powershell
git add -A
git commit -m "feat: add qlib pit baselines"
```
