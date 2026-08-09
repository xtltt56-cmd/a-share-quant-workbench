# Stage 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Validate the frozen Stage 2 strategies through shared contracts,
point-in-time signal analysis, framework adapters, robustness tests, and a
paper-only monitoring loop without creating a live-trading path.

**Architecture:** The repository owns immutable experiment metadata, PIT and
time semantics, contract validation, risk boundaries, promotion state, and
paper ledgers. Qlib, Optuna, QuantStats, VectorBT, and RQAlpha remain optional
orchestration engines behind adapters. Every engine consumes and returns shared
contracts.

**Tech Stack:** Python 3.12, pandas, NumPy, DuckDB, Parquet, PyYAML, Qlib,
LightGBM, Optuna, QuantStats, optional VectorBT, optional RQAlpha, pytest,
Ruff, and the repository report renderer.

**Baseline:** `a293a13`, Stage 2 accepted and frozen by the project owner.

**Safety boundary:** `PAPER_TRADING` and `PAPER_VALIDATED` are the highest
permitted operational states. There is no `LIVE` state, broker client, account
credential, or automatic real order path.

---

## Working rules for every sub-stage

- [ ] Start from a clean worktree and confirm the parent commit.
- [ ] Read the relevant architecture section before changing code.
- [ ] Add or update tests before implementation code for each behavior.
- [ ] Run the focused tests after each small task, then the complete suite.
- [ ] Run `ruff check .`, `pip check`, and `compileall` at the sub-stage gate.
- [ ] Preserve existing Stage 2 artifact directories and never rewrite a frozen
      artifact in place.
- [ ] Keep secrets in `.env`; do not put tokens in test fixtures, metadata,
      reports, or logs.
- [ ] Update README progress and record limitations, data mode, and commit.
- [ ] Commit one coherent sub-stage with a descriptive message.

## Stage 3A - Baseline freeze, contracts, signal quality, and promotion gate

### A1. Freeze the Stage 2 manifest

Files:

- `src/a_share_quant/experiments/baseline_manifest.py`
- `scripts/freeze_stage2_baseline.py`
- `artifacts/baselines/STAGE2_BASELINE_MANIFEST.json`
- `tests/test_stage3_baseline_manifest.py`

Steps:

- [ ] Define immutable `BaselineManifestEntry` and `BaselineManifest` models
      with the fields in `STAGE3_ARCHITECTURE.md`.
- [ ] Read Stage 2 `metadata.json` and verify strategy, model, feature,
      experiment, commit, dataset hash, config hash, split periods, benchmark,
      cost assumptions, seed, and library versions.
- [ ] Resolve artifact paths under the repository root and reject path escape,
      missing files, duplicate experiment IDs, and metadata/hash mismatch.
- [ ] Make writing append-only: an existing entry with different content is an
      error; an identical re-run is a no-op.
- [ ] Add the three accepted Stage 2 baseline entries without altering their
      source artifact directories. Mark fixture data explicitly when the
      accepted artifact was generated from a fixture.
- [ ] Test valid freeze, required fields, missing artifact, hash mismatch,
      duplicate entry, immutable re-run, and path traversal rejection.
- [ ] Run focused tests and inspect the generated JSON before committing.

Expected command:

```powershell
.\.venv\Scripts\python.exe scripts/freeze_stage2_baseline.py `
  --output artifacts/baselines/STAGE2_BASELINE_MANIFEST.json
```

### A2. Add shared Stage 3 contracts

Files:

- `src/a_share_quant/contracts/stage3.py`
- `src/a_share_quant/contracts/__init__.py`
- `tests/test_stage3_contracts.py`

Steps:

- [ ] Define immutable `PortfolioTarget` and `ExecutionSpec` dataclasses with
      explicit validation for dates, rates, weights, minimum order size, and
      paper-only execution assumptions.
- [ ] Define a validated `SignalFrame` wrapper around a DataFrame and retain
      strategy, model, feature, experiment, score, rank, confidence, timestamp,
      and intended-execution columns.
- [ ] Reject duplicate signal keys, missing provenance, non-finite scores,
      invalid normalized-score ranges, and inconsistent dates.
- [ ] Serialize contracts deterministically to dictionaries/DataFrames for
      adapters and artifacts.
- [ ] Keep the existing Stage 2 signal adapter backward compatible and add the
      Stage 3 timestamp fields through an explicit conversion helper.
- [ ] Test round trips, validation failures, and compatibility with existing
      `SignalRecord` consumers.

### A3. Enforce timestamp semantics

Files:

- `src/a_share_quant/contracts/timing.py`
- `src/a_share_quant/signals/adapter.py`
- `tests/test_signal_timestamp_semantics.py`

Steps:

- [ ] Define the five named times: data available, signal, decision, order,
      and execution.
- [ ] Add a deterministic trading-calendar helper that maps a signal date to
      the next valid execution date.
- [ ] Require signal timestamps after the input data cutoff and execution date
      strictly after signal date when T+1 is enabled.
- [ ] Reject same-bar execution, a missing next session, and an execution date
      that does not match the calendar.
- [ ] Add the exact regression tests `test_signal_timestamp_semantics`,
      `test_no_same_bar_execution`, and `test_execution_date_alignment`.

### A4. Add signal-quality analysis

Files:

- `src/a_share_quant/analysis/signal_quality.py`
- `src/a_share_quant/analysis/correlation.py`
- `src/a_share_quant/analysis/regime.py`
- `src/a_share_quant/analysis/__init__.py`
- `tests/test_signal_quality.py`
- `tests/test_signal_correlation.py`
- `tests/test_market_regime.py`

Steps:

- [ ] Define a read-only analyzer that accepts frozen OOS predictions and an
      existing forward-return label; it must not fit a model or optimize a
      parameter.
- [ ] Compute daily IC, Rank IC, IC mean, standard deviation, ICIR, positive-IC
      ratio, sample counts, and invalid-group counts per strategy.
- [ ] Compute quantile/decile summaries, Top-K statistics, excess returns,
      hit rates, turnover, and a monotonicity score; emit
      `WEAK_SIGNAL_MONOTONICITY` for weak ordering.
- [ ] Group outputs by month, quarter, year, and available market regime.
      Regime labels must use only causal benchmark observations.
- [ ] Add optional sector-neutral and volatility-regime summaries when columns
      are present and state `not_available` when they are absent.
- [ ] Compute score correlation, rank correlation, Top-K overlap, portfolio
      return correlation, rank stability, score drift, and cross-period
      correlation for model pairs.
- [ ] Test perfect positive/negative signals, ties, missing labels, too-small
      groups, monotonic and non-monotonic quantiles, causal regimes, and
      deterministic correlation output.

### A5. Add the promotion gate and report

Files:

- `src/a_share_quant/promotion.py`
- `src/a_share_quant/analysis/report.py`
- `scripts/run_stage3a_signal_quality.py`
- `tests/test_promotion_gate.py`
- `tests/test_stage3a_report.py`

Steps:

- [ ] Define the sequential promotion states from `RESEARCH` through
      `PAPER_VALIDATED` and reject `LIVE` at parsing and transition time.
- [ ] Define structural checks for PIT leak, look-ahead, dataset validity,
      test-split validity, SignalFrame validity, IC/Rank IC validity, and
      minimum sample count.
- [ ] Require persisted check evidence for a transition and reject skipped
      states or failed checks.
- [ ] Render a Markdown and JSON report at
      `reports/stage3_signal_quality_report.md` and
      `reports/stage3_signal_quality_report.json` with source artifact IDs,
      commit, data mode, limitations, all three model summaries, quantiles,
      regimes, and correlations.
- [ ] Test that a failed structural check cannot promote a strategy and that
      metrics are reported without being turned into unapproved thresholds.

### A6. Stage 3A acceptance gate

- [ ] Run the focused Stage 3A suite.
- [ ] Run the full Stage 2 and Stage 3 suite.
- [ ] Run Ruff, `pip check`, and Python compilation.
- [ ] Confirm coverage is not below the Stage 2 baseline of 86 percent.
- [ ] Review the report for fixture-versus-market labelling and absence of
      secrets.
- [ ] Update `README.md` with the 3A commit, commands, report paths, and any
      data/network limitations.
- [ ] Commit with `feat: add stage3a signal validation foundation`.

## Stage 3B - Candidate strategies and VectorBT fast research

Files:

- `src/a_share_quant/strategies/registry.py`
- `src/a_share_quant/strategies/candidates.py`
- `src/a_share_quant/strategies/contracts.py`
- `src/a_share_quant/strategies/ensemble.py`
- `src/a_share_quant/integrations/vectorbt/adapter.py`
- `src/a_share_quant/integrations/vectorbt/__init__.py`
- `src/a_share_quant/backtest/contracts.py`
- `src/a_share_quant/backtest/fast.py`
- `src/a_share_quant/backtest/reference.py`
- `src/a_share_quant/backtest/turnover.py`
- `tests/test_candidate_strategies.py`
- `tests/test_vectorbt_adapter.py`

Steps:

- [ ] Implement `TopKEqualWeight`, `TopKScoreWeight`, `RankWeighted`, and
      `TopKDropout` against `SignalFrame` and `PortfolioTarget`.
- [ ] Record candidate name, version, parameters, seed, and source strategy
      versions in metadata.
- [ ] Define shared `BacktestResult` fields: NAV, returns, benchmark returns,
      positions, orders, trades, turnover, transaction cost, metrics, and
      warnings.
- [ ] Keep VectorBT imports inside the adapter package and provide a clear
      optional-dependency error when it is absent.
- [ ] Verify the adapter applies the shared execution spec rather than
      duplicating rules.
- [ ] Run a deterministic fast-research smoke and commit the stage.

Stage 3B implementation notes:

- [x] Preserve `fixture`, `historical`, and `paper` as explicit data modes and
      block fixture promotion above `SIGNAL_VALIDATED_FIXTURE`.
- [x] Keep RuleBasedMultiFactor in the matrix but mark it
      `ENSEMBLE_INELIGIBLE` for the current weak fixture experiment only.
- [x] Use a small manual TopK/rebalance grid; do not run Optuna or weighted
      ensemble optimization in this stage.
- [x] Generate `reports/stage3_fast_research_report.md` with a visible fixture
      warning and a historical dry-run status.
- [x] Record VectorBT availability/version and the reference fallback warning.
- [ ] Commit only after the full regression suite, lint, dependency checks,
      coverage, report review, and security boundary review pass.

## Stage 3C - Ensemble, correlation, Optuna, and nested walk-forward

Files:

- `src/a_share_quant/ensemble/weights.py`
- `src/a_share_quant/ensemble/runner.py`
- `src/a_share_quant/optimization/optuna_runner.py`
- `src/a_share_quant/experiments/dataset_views.py`
- `tests/test_ensemble_test_isolation.py`
- `tests/test_optuna_boundaries.py`
- `tests/test_nested_walk_forward.py`

Steps:

- [ ] Define `DatasetView` objects that make TRAIN and VALIDATION available to
      optimization while making TEST inaccessible to the objective.
- [ ] Add ensemble methods only after signal and model-correlation artifacts
      are available.
- [ ] Record source strategy versions, weights, sampler seed, search space,
      and determinism level.
- [ ] Prove that modifying test labels or returns cannot change weights.
- [ ] Run nested walk-forward with an independent search per window.
- [ ] Commit the stage only after test isolation and boundary tests pass.

## Stage 3D - RQAlpha and cross-engine validation

Files:

- `src/a_share_quant/integrations/rqalpha/adapter.py`
- `src/a_share_quant/integrations/rqalpha/config.py`
- `src/a_share_quant/integrations/rqalpha/hooks.py`
- `src/a_share_quant/backtest/comparison.py`
- `tests/test_rqalpha_adapter.py`
- `tests/test_cross_engine_consistency.py`

Steps:

- [ ] Keep RQAlpha optional and isolated from core modules.
- [ ] Translate shared contracts into event callbacks without changing timing,
      limit, suspension, T+1, cost, or cash semantics.
- [ ] Map both fast and event engines to `BacktestResult`.
- [ ] Compare orders, fills, costs, turnover, and metrics with tolerances that
      are documented in configuration.
- [ ] Record framework version, adapter version, and license/availability
      status in the artifact.

## Stage 3E - Portfolio risk, stress, concentration, and regime

Files:

- `src/a_share_quant/risk/portfolio.py`
- `src/a_share_quant/risk/stress.py`
- `src/a_share_quant/risk/regime.py`
- `src/a_share_quant/config.py`
- `tests/test_stage3_portfolio_risk.py`
- `tests/test_stage3_stress.py`

Steps:

- [ ] Apply single-stock, position-count, gross-exposure, cash, and minimum
      order limits to portfolio targets.
- [ ] Add fixed, ATR, and trend stops using only decision-time information.
- [ ] Add maximum portfolio drawdown suspension and recovery rules.
- [ ] Measure concentration, sector exposure, liquidity exposure, and regime
      sensitivity.
- [ ] Run deterministic stress scenarios for gaps, limit locks, suspensions,
      spread/slippage changes, and benchmark shocks.

## Stage 3F - Paper trading, daily report, and promotion update

Files:

- `src/a_share_quant/paper/ledger.py`
- `src/a_share_quant/paper/monitor.py`
- `src/a_share_quant/paper/report.py`
- `src/a_share_quant/observability/provenance.py`
- `scripts/run_paper_monitor.py`
- `tests/test_paper_ledger.py`
- `tests/test_paper_monitor.py`
- `tests/test_paper_only_boundary.py`

Steps:

- [ ] Consume `SignalFrame`, generate `PortfolioTarget`, and apply
      `ExecutionSpec`; do not create a second paper execution rule.
- [ ] Record simulated orders, fills, positions, cash, realized/unrealized PnL,
      rejected orders, and strategy/model/feature/experiment versions.
- [ ] Make ledger writes append-only and idempotent by run ID and event ID.
- [ ] Produce a daily report with Top20 candidates and Top5 details: score,
      factor scores, price, observation range, support, risk/stop level,
      target range, risk/reward, suggested weight, reasons, and risks.
- [ ] Update promotion state only through persisted evidence and keep the
      highest state at `PAPER_VALIDATED`.
- [ ] Test restart/idempotency, T+1 paper sells, rejection reasons, risk
      limits, provenance, and the absence of live-provider imports.

## Security review and release evidence

- [ ] Run the repository-grounded `security-threat-model` skill against the
      Stage 3 paths before the first paper-monitor commit.
- [ ] Review trust boundaries for `.env`, external data, Parquet/DuckDB files,
      logs, reports, optional engines, and any future broker boundary.
- [ ] Confirm no secret is present in Git, experiment metadata, reports, or
      log output.
- [ ] Record the threat-model findings and mitigations in the Stage 3 report or
      a linked repository document.
- [ ] Run the complete release evidence command set:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m compileall -q src scripts tests
```

Every stage is complete only after its tests, report, README progress, and Git
commit are present. A framework that is not installed is reported as an
optional adapter limitation; it is never silently replaced by an untested
local implementation.
