# Stage 3 Architecture: Strategy Validation and Paper Trading

Status: approved architecture for implementation

Baseline: `a293a13` (Stage 2 accepted and frozen by the project owner)

This document defines the Stage 3 boundary for the A-share quantitative research
system. It is intentionally adapter-first: mature open-source frameworks remain
the engines, while this repository owns the contracts, data timing rules,
versioned artifacts, risk boundaries, and paper-trading state.

## 1. Objectives

Stage 3 turns the three Stage 2 baseline models into reproducible, comparable,
and promotable research candidates:

- freeze the exact Stage 2 inputs, code revision, configurations, and artifacts;
- measure signal quality independently from portfolio construction;
- make signal, portfolio, and execution semantics explicit and shared;
- validate candidates through fast research, event-driven checks, robustness,
  and walk-forward tests in later sub-stages;
- operate a paper-trading monitor without any real broker connection;
- make every signal and simulated order traceable to a strategy and experiment
  version.

Stage 3A is the first delivery slice. It contains baseline freeze, contracts,
timestamp semantics, signal-quality analysis, promotion gates, and automated
tests. It does not optimize parameters or connect a new backtest engine.

## 2. Non-goals and hard safety boundaries

The following are explicitly outside the Stage 3 scope:

- live broker APIs, account credentials, order gateways, or automatic live
  trading;
- an execution state named `LIVE` or any transition into it;
- replacing Qlib, Optuna, QuantStats, VectorBT, or RQAlpha with local clones;
- silently changing Stage 2 artifacts in place;
- using the current constituent list, revised financial data, or future labels
  to reconstruct historical decisions;
- optimizing on the test split or using test results to choose ensemble weights;
- presenting synthetic fixture results as real-market evidence.

The only permitted operational states in this project are research, validation,
and paper states. The paper layer is an observation and bookkeeping layer, not
an order-routing layer.

## 3. Frozen Stage 2 baseline

The frozen baseline is described by
`artifacts/baselines/STAGE2_BASELINE_MANIFEST.json`. The manifest is a
versioned, append-only index. It must not overwrite an existing entry. A
changed dataset, configuration, feature definition, model, or source revision
creates a new strategy or experiment version and a new artifact directory.

Each baseline entry records at least:

| Field | Meaning |
| --- | --- |
| `strategy_id` | Stable candidate identifier |
| `strategy_version` | Strategy logic version |
| `model_version` | Model implementation and parameter version |
| `feature_version` | Feature and PIT data definition version |
| `experiment_id` | Immutable experiment bundle identifier |
| `git_commit` | Source revision used to produce the bundle |
| `dataset_hash` | Hash of the input data snapshot |
| `config_hash` | Hash of the effective configuration |
| `signal_artifact_path` | Versioned signal artifact |
| `prediction_artifact_path` | Versioned prediction artifact |
| `train_period` | Training date boundary |
| `validation_period` | Validation date boundary |
| `test_period` | Final untouched test boundary |
| `benchmark` | Benchmark symbol and source |
| `transaction_cost_assumptions` | Commission, tax, transfer, slippage, and execution rules |
| `random_seed` | Seed used by all deterministic components |
| `library_versions` | Relevant framework versions |

The verifier checks that referenced files exist, hashes match metadata, the
experiment metadata points to the same commit and versions, and the manifest
has not been rewritten with a different entry. It also records whether an
artifact came from fixture or market data.

## 4. Unified contracts

All research engines and the paper layer consume the same contracts. Adapters
may translate a contract to framework-specific objects, but they may not change
its meaning.

### 4.1 `SignalFrame`

`SignalFrame` is a validated tabular contract. Each row contains:

```text
date
symbol
strategy_id
strategy_version
model_version
feature_version
experiment_id
raw_score
normalized_score
rank
confidence
signal_available_at
intended_execution_date
```

`date` is the market signal date. `signal_available_at` is a timezone-aware
timestamp after the data needed for the signal is available. The intended
execution date must be a later trading date when T+1 is enabled. NaN scores,
duplicate `(date, symbol, strategy_id, experiment_id)` keys, missing provenance,
and same-day execution are rejected.

### 4.2 `PortfolioTarget`

```text
date
symbol
target_weight
target_quantity (optional)
source_strategy
rebalance_reason
```

Weights are desired post-rebalance weights, not orders. A portfolio adapter
must apply cash, lot-size, position, risk, and execution constraints before
creating trades.

### 4.3 `ExecutionSpec`

```text
signal_date
execution_date
execution_price_rule
slippage
commission
tax
t_plus_one
limit_rule
suspension_rule
minimum_order_size
cash_constraint
```

The contract is immutable for a backtest run. Cost values are explicit decimal
rates or documented monetary rules. A missing execution price, a suspended
symbol, a locked limit, a T+1 sell violation, insufficient cash, or an order
below minimum size produces a deterministic no-fill or rejection record rather
than an optimistic fill.

## 5. Time semantics and point-in-time rules

Every signal path must preserve these five distinct times:

| Time | Definition | Allowed information |
| --- | --- | --- |
| Data Available Time | When a source row is publicly available | Only data announced or published by this time |
| Signal Time | When features and model score are computed | Data available by this time |
| Decision Time | When the target portfolio is selected | Signal and risk data available by this time |
| Order Time | When a paper order is recorded | Decision output and execution rules |
| Execution Time | When the simulated fill can occur | Next valid market event after order time |

For the daily strategy, the default sequence is:

```text
T close data -> after-close signal -> after-close decision
            -> order for T+1 -> T+1 open/defined execution price
```

No same-bar execution is permitted. Financial values are filtered by
`announced_at`/effective date. Historical universe membership is read from an
as-of snapshot. Forward labels are available to analysis only after the label
period ends and never enter feature construction or portfolio decisions.

Required regression tests are `test_signal_timestamp_semantics`,
`test_no_same_bar_execution`, and `test_execution_date_alignment`.

## 6. Stage 3A signal-quality pipeline

The signal-quality analyzer consumes frozen out-of-sample prediction artifacts
and an already-defined forward-return label. It does not fit a model and does
not select weights. For each of the three Stage 2 models it computes:

- IC and Rank IC by date;
- IC mean, IC standard deviation, ICIR, and positive-IC ratio;
- quintile/decile returns, excess returns, counts, and monotonicity;
- Top-K return, hit rate, and Top-K turnover where portfolio rows exist;
- monthly, quarterly, and yearly group summaries;
- bull, neutral, and bear regime summaries, plus high/low volatility when the
  required benchmark data is available;
- sector-neutral and exposure summaries when sector data is available;
- score distribution drift, rank stability, cross-period correlation, and
  Top-K turnover;
- pairwise model score correlation, rank correlation, Top-K overlap, and
  portfolio-return correlation.

The report must state the sample count and data mode for every result. A weak
relationship between score quantiles and realized returns is reported as
`WEAK_SIGNAL_MONOTONICITY`; it is a diagnostic, not an invented pass/fail
return threshold. Stage 3A promotion uses structural validity checks only.

## 7. Candidate strategy architecture

After signal quality is accepted, portfolio construction is selected from a
registry of explicit candidates:

- `TopKEqualWeight`;
- `TopKScoreWeight`;
- `RankWeighted`;
- `TopKDropout`.

Each candidate receives a `SignalFrame`, a `PortfolioSpec`, and risk limits and
returns `PortfolioTarget` rows. The registry records the candidate name,
version, parameters, and deterministic seed. Candidate construction never
reads future returns.

## 8. Ensemble architecture

The three baseline models can later be combined only after individual signal
quality and correlation are reported. Ensemble weights are produced by a
versioned procedure, not by hand-coded constants. The ensemble records all
source strategy versions and the correlation snapshot used.

Test data is isolated: changing test labels or test returns must not change
ensemble weights. The training procedure may use only the training and
validation views, with test data read once for final reporting.

## 9. Optimization boundary

Optuna is an optional research dependency. Its search space and sampler seed
are configuration data. Optimization can read TRAIN and VALIDATION only; the
TEST view is inaccessible to the objective. Nested walk-forward reruns the
search for each window and stores the study metadata and selected parameters.

No optimization is part of Stage 3A.

## 10. VectorBT fast-research adapter

The VectorBT integration is optional and isolated under
`src/a_share_quant/integrations/vectorbt/`. Core modules do not import
VectorBT. The adapter exposes:

```text
FastResearchEngine.run(
    signals: SignalFrame,
    portfolio_spec: PortfolioSpec,
    execution_spec: ExecutionSpec,
    period: TimePeriod,
) -> BacktestResult
```

The adapter translates the shared contracts, applies the same T+1, limit,
suspension, cost, cash, and position rules, and maps its output to the shared
`BacktestResult`. If VectorBT is not installed, the adapter reports an
actionable optional-dependency error and the core remains importable.

## 10A. Stage 3B data-mode boundary

Every Stage 3B `SignalFrame`, experiment, `BacktestResult`, and report carries
one of `fixture`, `historical`, or `paper` as `data_mode`. A fixture report
must display `TEST / FIXTURE DATA - NOT INVESTMENT EVIDENCE`. Fixture evidence
may reach `SIGNAL_VALIDATED_FIXTURE` only; it cannot reach
`FAST_BACKTEST_PASS`, `ROBUSTNESS_PASS`, `RQALPHA_PASS`, `PAPER_TRADING`, or
any future production state. Historical and paper data are separate modes and
are never silently substituted by fixture rows.

## 10B. Candidate portfolio strategy contract

Stage 3B portfolio strategies implement one protocol:

```text
SignalFrame + PortfolioSpec + ExecutionSpec -> list[PortfolioTarget]
```

The first candidates are `TopKEqualWeight`, `TopKScoreWeight`, `RankWeighted`,
and `TopKDropout`. They only consume the public signal contract; model objects,
training labels, and feature internals cannot cross this boundary. Position
caps, maximum position count, target gross exposure, and cash buffer are
applied without renormalizing above a cap. If caps leave residual cash, the
residual is preserved explicitly.

`RebalancePolicy` is shared by all candidates and adapters. Stage 3B supports
daily, weekly, and `EveryNDays` schedules. The shared turnover definition is:

```text
raw_turnover_t = sum_i(abs(target_weight_t_i - previous_weight_t_i))
normalized_turnover_t = raw_turnover_t / max(sum_i(abs(previous_weight_t_i)), 1.0)
```

The initial portfolio uses 1.0 initial capital as its denominator. Reports may
show both raw and normalized turnover, but neither engine may substitute an
engine-specific turnover meaning.

## 10C. Stage 3B fast-research result contract

`BacktestResult` contains NAV, returns, benchmark returns, positions, orders,
trades, turnover, estimated transaction costs, metrics, warnings, data mode,
engine/version, assumptions, and limitations. The fast engine reports CAGR,
total return, Sharpe, Sortino, maximum drawdown, Calmar, volatility, turnover,
estimated transaction cost, rebalances, average holding period,
concentration, and benchmark excess return.

VectorBT is a research accelerator, not the final A-share execution authority.
The adapter records an approximation warning: exact T+1 sellability, limit-up
and limit-down queues, suspensions, lot sizes, order rejection, and fills must
be validated later by the event engine. When the optional package is absent,
the reference fast-research fallback can run without changing the core
strategy contract.

## 11. RQAlpha event-validation adapter

The RQAlpha integration is optional and isolated under
`src/a_share_quant/integrations/rqalpha/`. It consists of an adapter, a
configuration builder, and event hooks. It consumes the same `SignalFrame`,
`PortfolioTarget`, and `ExecutionSpec`; it must not introduce a second set of
trading rules or modify core evaluator behavior.

The final validation engine exposes the same `BacktestResult` shape as the
fast engine. RQAlpha license and availability checks are part of the run
metadata. RQAlpha is not required for Stage 3A.

## 12. Portfolio risk layer

Risk is applied after targets are produced and before simulated orders:

- maximum single-stock weight;
- maximum number of positions;
- maximum gross portfolio exposure;
- fixed stop loss;
- ATR/trend stop loss;
- maximum portfolio drawdown;
- cash and minimum-order constraints.

Risk decisions are recorded with the affected target, input strategy version,
rule version, and reason. A risk reduction never creates an order outside the
execution contract. Stop prices are calculated from information available at
the decision time.

## 13. Paper-trading architecture

Paper trading has four components:

1. signal intake validates `SignalFrame` and strategy provenance;
2. target generation produces `PortfolioTarget` rows;
3. simulated execution consumes `ExecutionSpec` and market data;
4. an append-only ledger records orders, fills, positions, cash, PnL, and
   strategy versions.

The monitor can refresh data and produce daily reports, but it has no broker
client, account token, or live order code. Paper records include a run ID,
strategy/model/feature versions, execution assumptions, and an explicit
`paper_only=true` marker.

## 14. Promotion state machine

The only allowed forward state sequence is:

```text
RESEARCH
  -> SIGNAL_VALIDATED
  -> FAST_BACKTEST_PASS
  -> ROBUSTNESS_PASS
  -> RQALPHA_PASS
  -> WALK_FORWARD_PASS
  -> PAPER_TRADING
  -> PAPER_VALIDATED
```

Transitions are sequential and require a persisted gate result. `LIVE` is not
a valid state and is rejected by the state machine. Stage 3A may promote a
baseline only through `SIGNAL_VALIDATED` when all structural checks pass:

- no PIT leak;
- no look-ahead;
- dataset valid;
- test split valid and untouched;
- `SignalFrame` valid;
- IC and Rank IC calculations valid;
- minimum sample counts valid.

Return, Sharpe, and IC values are reported for evidence but Stage 3A does not
invent numeric return thresholds. Later stages define their own approved gates
in versioned configuration.

## 15. Artifact and version management

Every Stage 3 experiment is written under:

```text
experiments/stage3/<experiment_id>/
  config.yaml
  metadata.json
  signals.parquet
  portfolio.parquet
  metrics.json
  report/
```

The metadata contains the Git revision, config and dataset hashes, dependency
versions, random seeds, determinism level, split boundaries, benchmark, cost
assumptions, and source artifact IDs. Reports are derived artifacts and never
replace source data. The Stage 2 manifest is immutable; a new artifact path is
required for every changed input.

## 16. Security boundary

Secrets are loaded only from `.env` or an approved secret provider and remain
outside source, manifests, artifacts, reports, and logs. External data adapters
validate schema, symbol, date, and range before persistence. Paths supplied to
manifest/report commands are resolved and checked to stay inside the project
root. Logs redact tokens and account identifiers.

The paper-only boundary is enforced in code by the absence of a live execution
provider and by rejecting `LIVE` promotion. A future broker integration would
require a separate threat-model review, explicit human approval, a distinct
package, and a disabled-by-default configuration. The repository's
`security-threat-model` review covers these trust boundaries and abuse cases.

## 17. Testing strategy

Stage 3 tests are layered:

- contract validation and serialization;
- timestamp ordering and no same-bar execution;
- manifest immutability and hash verification;
- signal quality metrics and monotonicity;
- regime grouping and correlation analysis;
- promotion transition rules;
- adapter contract tests with optional dependencies skipped explicitly;
- paper ledger and risk behavior in later stages.

All Stage 2 tests remain a required regression suite. Deterministic fixtures
must use fixed seeds and be clearly labelled. Network tests require an explicit
operator flag and are never part of the default test command.

## 18. Failure modes and safe outcomes

| Failure | Safe outcome |
| --- | --- |
| Missing artifact or hash mismatch | Refuse to freeze or analyze |
| Same-day execution timestamp | Contract validation error |
| Missing announcement date | PIT validation error |
| Duplicate signal key | Contract validation error |
| Weak or insufficient signal sample | Diagnostic warning and no promotion |
| Locked limit or suspension | No fill with a reason |
| T+1 sell attempt | Rejected order with a reason |
| Optional engine missing | Adapter-only dependency error |
| Corrupt external data | Reject before storage |
| Secret-looking log value | Redact or reject logging |
| Attempt to enter `LIVE` | State-machine error |

## 19. Implementation sequence

Stage 3 is delivered in the following order:

1. **3A - Baseline Freeze, Contracts, Promotion Gate, Signal Quality, Tests**
2. **3B - Candidate Strategies, VectorBT Adapter, Fast Research**
3. **3C - Ensemble, Correlation, Optuna, Nested Walk-Forward**
4. **3D - RQAlpha, Cross-Engine Comparison, A-share Constraints**
5. **3E - Portfolio Risk, Stress, Concentration, Market Regime**
6. **3F - Paper Trading, Daily Report, Promotion State Update**

Each sub-stage has its own tests, report, README/progress update, and Git
commit. A failed quality gate stops that sub-stage; it does not get hidden by
fixture results or a later adapter.

## 22. Stage 3RT real-time workbench boundary

Stage 3RT is an independent operational-monitoring stage inserted before Stage
3C. It may consume real market data and compute intraday monitoring features,
but it does not change the Stage 2/3 historical PIT store, invoke a daily model
with intraday data, or expose any broker/live execution route. The approved
design and implementation plan are recorded in:

- `docs/superpowers/specs/2026-08-09-stage3rt-realtime-workbench-design.md`
- `docs/superpowers/plans/2026-08-09-stage3rt-realtime-workbench.md`

The runtime flow is:

```text
Provider -> normalize/validate -> RealTimeStore -> freshness circuit breaker
         -> transparent intraday analysis -> local dashboard / paper signal monitor
```

Provisional minute bars remain outside the historical store until explicit EOD
validation and reconciliation. The dashboard is local-only by default and
must bind to `127.0.0.1`. `READY` is a monitoring state, never a broker order.

## 20. Stage 3A acceptance criteria

Stage 3A is accepted only when all of the following are true:

- the frozen manifest verifies all three Stage 2 baseline entries;
- Stage 2 artifacts are not overwritten;
- the unified contracts serialize and validate;
- timestamp tests prove T+1 and no same-bar execution;
- all three models have IC, Rank IC, ICIR, positive-IC ratio, quantile,
  time-group, and available-regime results;
- model correlation and Top-20 overlap are reported;
- weak monotonicity is surfaced as a diagnostic;
- promotion checks reject PIT/look-ahead/test-split violations;
- ensemble test isolation is covered by a regression test stub or implementation
  boundary, without using test data for weights;
- Stage 2 tests still pass;
- Ruff, `pip check`, `compileall`, and coverage pass without falling below the
  Stage 2 baseline;
- `reports/stage3_signal_quality_report.md` and `.json` identify the dataset
  mode, source artifacts, commit, and all limitations;
- the README and implementation plan identify the exact completed commit.

## 21. Stage 3B acceptance criteria

Stage 3B is accepted only when the candidate strategy protocol, four baseline
strategies, shared rebalance policy, and shared turnover formula have contract
tests; the optional VectorBT adapter is import-isolated and non-blocking; signal
execution tests prove no same-bar execution; and `BacktestResult` preserves
data mode, costs, limitations, and provenance. The report must compare TopK,
rebalance frequency, turnover, costs, drawdown, a small parameter surface,
model-by-strategy results, and the fixture-only EqualRank pipeline. A missing
historical benchmark or universe snapshot is reported as `NOT_RUN` rather than
filled with synthetic data. Stage 3B stops here; ensemble robustness, Optuna,
nested walk-forward, RQAlpha, and paper trading remain later stages.
