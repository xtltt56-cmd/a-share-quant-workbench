# A-Share Advisory and Model-Evolution System Design

Date: 2026-08-09

Status: approved design pending written-spec review

Target broker: 财信证券（原财富证券）
Primary user mode: cash-account, long-only A-share swing guidance with manual order entry

## 1. Purpose

Extend the existing A-share quantitative research and real-time workbench into a
reliable local decision-support system for a non-specialist user. The system
collects and validates market data, trains and evaluates competing models,
generates evidence-backed buy/hold/watch/reduce/exit guidance, tracks the user's
actual holdings, and verifies each matured prediction against later outcomes.

The first production interaction with 财信证券 is manual order placement. The
system may later use an officially authorized 迅投 QMT installation for market
data and read-only account synchronization. Automated order submission is not
part of the approved scope and remains fail-closed.

## 2. Reliability and governance principles

1. Correct data timing, reproducible implementation, sound validation, and risk
   control can be required; future prediction accuracy or profit cannot be
   guaranteed.
2. Evidence overrides user or developer preference. A requested feature or
   strategy must be rejected when it conflicts with data integrity, finance and
   econometric reasoning, regulation, security, or verifiability.
3. Every rejection must state the conflict, supporting evidence, and a safer
   alternative. The system must not silently reinterpret an unsafe request.
4. Simple, explainable baselines remain permanent. A complex model is accepted
   only when it demonstrates robust incremental value after costs and risk.
5. Research code, candidate models, formal guidance, account state, and broker
   execution are separate trust domains.
6. Missing, stale, conflicting, or unverifiable data results in degraded status,
   `WATCH`, `BLOCKED`, or `INSUFFICIENT_DATA`, never a fabricated recommendation.
7. The formal model cannot change itself intraday. Candidate discovery and
   retraining occur offline and require governed promotion.
8. The product remains local-first, paper/manual-execution-first, and bound to
   loopback by default.

## 3. Approved user and trading scope

- Intended holding period: several trading days to several weeks.
- Formal forecasts: 5-, 10-, and 20-trading-day horizons.
- Trading universe: ordinary long-only A-share equities and broad-market ETFs.
- Account: ordinary cash account.
- Excluded from V1: margin trading, short selling, options, convertible bonds,
  Beijing Stock Exchange securities, leverage, and automatic order submission.
- Formal guidance is generated after the close for the next session. Intraday
  behavior is limited to price, risk, market-state, and data-quality alerts.
- Orders are entered manually in an official 财信证券 client.
- Actual fills are initially registered through a four-field form or imported
  from a broker file; QMT read-only synchronization is a later adapter.

## 4. System architecture

```text
Free data / exchange disclosures / later paid providers / QMT read-only data
                              |
                              v
                 Immutable raw evidence store
                              |
                              v
          Canonical data + PIT store + historical universe
                              |
                 +------------+------------+
                 |                         |
                 v                         v
        Research feature views     Formal inference view
                 |                         |
                 v                         v
       Automated research lab       Champion model(s)
                 |                         |
        Challenger evaluation               |
                 |                         v
                 +--> governed gate --> portfolio and risk engine
                                                |
                         actual ledger ---------+
                                                v
                                     advisory state engine
                                                |
                                                v
                            local dashboard and daily report
                                                |
                                                v
                                  user manually places order
                                                |
                                                v
                                fill registration / reconciliation
```

### 4.1 Trust boundaries

- Provider adapters may contact only configured data services and emit canonical,
  validated records with sanitized failures.
- Research workers cannot read broker credentials or write formal model aliases.
- Candidate artifacts are immutable and cannot overwrite frozen baselines.
- Only the promotion service can update the champion alias, and it requires a
  persisted validation bundle plus explicit user approval.
- The advisory engine consumes only champion predictions and formal account
  state. It cannot invoke training or broker-order APIs.
- `NoopExecutionProvider` is the only enabled execution provider in the approved
  scope and always rejects order submission.

## 5. Data architecture

### 5.1 Data layers

1. **Raw**: immutable provider payloads or normalized evidence snapshots,
   fetched-at timestamps, source versions, request identity, and content hashes.
2. **Canonical**: common identifiers, trading calendar, corporate actions,
   adjusted/unadjusted OHLCV, indices, fundamentals, and disclosures.
3. **PIT**: report period, announcement time, effective time, ingest time, and
   revision history. A value is visible only when it was knowable.
4. **Historical universe**: listing, delisting, ST, suspension, liquidity,
   market-board, and eligibility state for each historical date.
5. **Features**: versioned market, value, quality, growth, momentum, reversal,
   liquidity, volatility, breadth, event, and regime features.
6. **Labels**: 5/10/20-day benchmark-relative return, realized rank, maximum
   favorable/adverse excursion, and simulated risk-trigger outcomes.

### 5.2 Provider strategy

The free-first path uses AKShare and official exchange/disclosure sources where
available. It must remain useful but may lower confidence when coverage,
freshness, or upstream stability is insufficient.

Provider interfaces must support later Tushare Pro, RQData, and officially
authorized QMT data without changing feature, model, or advisory contracts. A new
provider runs in comparison mode before promotion. Provider disagreement,
timestamp drift, adjustment differences, missing suspensions, and identifier
mapping differences are reported and resolved before cutover.

### 5.3 Data gates

- No duplicate canonical key or backwards/future timestamp.
- OHLC, amount, volume, adjustment, identifier, and trading-date invariants pass.
- Selected-universe critical fields have complete, explainable coverage.
- Benchmark and constituent identity are explicit; CSI300 is an index, not an
  equity.
- Incomplete current-day data cannot finalize a formal report.
- A future append cannot change an earlier PIT feature result.
- Fixture, historical, paper, and real-market evidence modes never mix.

## 6. Economic and econometric validity

### 6.1 Economic hypothesis registry

Every factor records its economic mechanism, required source fields, expected
direction, neutralization rules, plausible decay horizon, failure modes, and
applicable regimes. Historical correlation alone is insufficient.

The initial categories are value, quality, growth, momentum, reversal,
liquidity, volatility/tail risk, market breadth/flows, and formally timestamped
events. A-share implementation additionally models T+1, price limits,
suspensions, ST/delisting status, IPO age, lot size, and retail-dominated market
microstructure.

### 6.2 Validation statistics

Formal comparison includes:

- out-of-sample Rank IC, ICIR, quantile monotonicity, and Top-K spread;
- 5/10/20-day direction accuracy and probability calibration;
- net-of-cost return, drawdown, CVaR, turnover, liquidity, and capacity;
- regime, industry, size, year, and volatility subsamples;
- block bootstrap and autocorrelation-robust uncertainty;
- multiple-testing control, Deflated Sharpe Ratio, and Probability of Backtest
  Overfitting;
- Model Confidence Set or equivalent evidence that avoids declaring a unique
  winner when data cannot distinguish models;
- parameter, date-boundary, universe, and cost perturbation tests.

The system must report uncertainty and abstain when confidence intervals are too
wide or expected advantage is not material after costs.

### 6.3 Implementation correctness

Each formal algorithm requires a mathematical definition, small hand-checkable
fixtures, property tests, time-causality tests, deterministic seeds, artifact
hashes, environment versions, differential checks against mature frameworks,
and a model card describing its intended use and limitations.

## 7. Model ladder and automated evolution

### 7.1 Permanent baselines

- benchmark buy-and-hold and cash;
- equal-weight Top-K;
- fixed rule multifactor;
- linear Ridge/ElasticNet.

### 7.2 Initial formal candidates

- Qlib LightGBM Alpha158-style model on project-owned PIT data;
- Qlib DoubleEnsemble;
- XGBoost;
- CatBoost;
- controlled rank/probability ensembles.

### 7.3 Later candidates

ALSTM, TCN, Transformer-style time-series models, regime experts, event-text
features, and horizon-specific specialists are admitted only after the tabular
pipeline and real-data evidence are stable.

FinRL, reinforcement learning, and LLM-generated strategies are research-only by
default. Reinforcement learning may later study allocation or execution, but it
is not the first formal stock-direction predictor.

### 7.4 Experiment automation

- Qlib provides the common dataset/model workflow.
- Optuna performs constrained multi-objective search without TEST access.
- MLflow tracks every success and failure, data/code/config hashes, parameters,
  predictions, reports, and model aliases.
- RD-Agent(Q) may propose factors or model code in a sandbox. Generated code has
  no credentials, formal registry write, or broker access and must pass license,
  dependency, security, causality, and regression checks.

### 7.5 Prediction-to-outcome ledger

Every forecast is append-only and stores its generation time, data cutoff,
symbol, model/data/feature versions, horizons, predicted rank and probability,
uncertainty, proposed advisory state, price/risk plan, expiry dates, and report
ID.

When a horizon matures, the system appends realized relative return, realized
rank, maximum favorable/adverse excursion, risk-trigger order, cost-adjusted
outcome, and calibration contribution. It separately evaluates model quality,
portfolio/advisory quality, and the user's actual execution.

### 7.6 Cadence and promotion

- Daily: ingest, infer, mature labels, and monitor model/data health.
- Weekly: performance, calibration, drift, and advisory-quality report.
- Monthly: retrain challengers on the current rolling window.
- Quarterly: nested walk-forward, event validation, stress, and promotion review.
- Emergency: data/model failure freezes new buys and reverts to the last valid
  model or abstention mode.

A challenger must pass causality, reproducibility, walk-forward stability,
net-of-cost improvement, drawdown/tail constraints, concentration, perturbation,
drift, and model-card gates. It first enters shadow mode and can become champion
only after a persisted comparison bundle and explicit user approval.

## 8. Advisory and risk design

### 8.1 States

- `BUY_CANDIDATE`
- `ADD_CANDIDATE`
- `HOLD`
- `WATCH`
- `REDUCE`
- `EXIT`
- `BLOCKED`
- `INSUFFICIENT_DATA`

Buy guidance passes, in order, tradability, market regime, champion-model,
fundamental/event, and portfolio gates. Threshold hysteresis and cooldown periods
avoid turnover from small rank changes.

### 8.2 Initial conservative-balanced risk profile

- normal-regime gross exposure cap: 70%;
- cautious-regime cap: 40%;
- risk-off cap: 0-20%;
- initial position: 2-4%;
- absolute single-name cap: 8%;
- maximum positions: 10;
- industry cap: 25%;
- planned risk per position: 0.75% of account equity;
- minimum cash reserve: 30%;
- liquidity and participation caps based on recent traded amount;
- no leverage, shorting, or use of unsettled funds.

Position size is calculated from risk budget and the distance to a volatility-
and-structure-based invalidation level, then clipped by all portfolio caps.

### 8.3 Drawdown protection

- below 6%: normal governed operation;
- 6-8%: no new high-volatility additions and exposure cap reduced to 50%;
- 8-12%: no new buys and exposure reduced toward 30%;
- at or above 12%: protection mode permits only risk reduction, freezes model
  promotion, and requires data/model/execution review before recovery.

### 8.4 Guidance content

Each formal item states action, validity period, evidence cutoff, position range,
observation/entry range, maximum acceptable price, invalidation/stop reference,
expected holding horizon, confidence, supporting factors, risks, and conditions
that cancel the guidance.

An intraday price jump, new disclosure, stale feed, limit state, or abnormal
liquidity can downgrade a plan to `WATCH` or `BLOCKED`; intraday noise cannot
silently retrain or replace the daily champion.

## 9. Account ledger and simple manual entry

### 9.1 Four-field buy form

The normal form exposes only:

```text
stock name | stock code | quantity | buy price
```

Code lookup fills or validates the name. Trade date defaults to the current
selected trading date, side defaults to buy, and fees use the configured broker
schedule until reconciled with an imported statement. Advanced fields remain
collapsed.

The submission preview checks identifier/name agreement, positive finite price,
100-share lot rules where applicable, cash, duplicate event identity, and risk
limits. A confirmed entry appends a fill event and automatically recomputes cash,
cost basis, positions, holding age, profit/loss, portfolio risk, and later
guidance.

### 9.2 Sales, imports, and corrections

The simple sell form selects an existing holding and asks only quantity and sell
price. CSV/Excel import maps broker columns through a preview and validation
step. Ledger rows are never edited or deleted; corrections append reversal and
replacement events. Idempotency uses event/run IDs and source-file hashes.

The system never stores a broker login password.

## 10. Local product and operating workflow

### 10.1 Product pages

1. Today's guidance and required actions.
2. Holdings, cash, P&L, risk, and advised action.
3. Top-20 candidates.
4. Stock detail with explanation, uncertainty, events, and historical guidance.
5. Prediction-to-outcome verification.
6. Champion/challenger research status and promotion decisions.
7. Data/provider/system health.

The default view uses plain Chinese and answers: what to do, why, evidence time,
maximum allocation, invalidation point, and cancellation conditions. Advanced
metrics are expandable rather than required for normal use.

### 10.2 Daily workflow

After the close, the system finalizes data, updates disclosures and PIT state,
matures labels, runs champion inference, applies portfolio risk, and writes an
immutable next-session report. Before the open, it refreshes overnight events
and cancels invalid plans. Intraday it monitors price/risk/data conditions only.
After the close, manual or file-imported fills reconcile the account ledger.

No formal report is produced when critical inputs or gates fail.

### 10.3 Local security

- bind only to `127.0.0.1` by default;
- serve UI assets locally;
- sanitize provider and broker errors;
- keep secrets out of Git, artifacts, reports, and logs;
- use Windows Credential Manager for supported provider secrets;
- audit data imports, configuration changes, advice generation, model promotion,
  and QMT synchronization;
- provide protection mode, backup, restore, and last-known-good rollback.

## 11. 财信证券 and QMT integration

财信证券 lists 迅投 QMT, PTrade, ATX, PB, and related quantitative systems,
but account eligibility, SDK, market-data rights, and programmatic-trading
obligations are controlled by the broker.

The adapter sequence is fixed:

1. detect the officially supplied terminal, SDK, versions, and capabilities;
2. add market-data read-only mode and compare it with existing providers;
3. add cash, position, and fill read-only synchronization;
4. reconcile QMT account state with the append-only local ledger;
5. export a human-readable and machine-readable order basket for manual review;
6. keep `NoopExecutionProvider` enabled.

No UI automation, reverse-engineered protocol, unofficial credential relay, or
GitHub broker-login library is permitted. Any future automated order path is a
new scope requiring broker authorization, applicable programmatic-trading
reporting, simulation, kill switches, reconciliation, and explicit user approval.

## 12. Open-source adoption policy

### Core or approved adapters

- Microsoft Qlib: core research workflow and model adapters, MIT.
- Optuna: constrained hyperparameter optimization, MIT.
- MLflow: experiment and model lifecycle tracking, Apache-2.0.
- RQAlpha: optional event validation for personal non-commercial research;
  preserve its non-commercial boundary.
- QuantStats: return-series analytics and reports, Apache-2.0.
- PyPortfolioOpt: HRP/minimum-risk comparison, MIT.
- VectorBT: optional fast research; preserve its Apache-2.0 plus Commons Clause
  boundary and never treat it as final execution evidence.

### Sandboxed research only

- Microsoft RD-Agent(Q).
- FinRL and later reinforcement-learning frameworks.
- LLM-generated factor/model proposals.

New GitHub resources require original-source provenance, active maintenance,
tests, reproducibility, license compatibility, dependency/security review, and a
project-owned adapter. The system may discover candidates automatically but may
not install or execute unknown code in the formal environment automatically.

## 13. Delivery stages and acceptance

### Stage 0 - Recover and close the current checkpoint

Commit the pending Stage 3RT-E work without claiming real-market validation.
All tests, Ruff, dependency, compilation, diff, documentation, and security gates
pass and the working tree becomes clean.

### Stage 1 - Real historical data foundation

Build multi-regime A-share history, benchmark, PIT fundamentals, historical
universe, free-provider operation, and paid/QMT provider interfaces. Historical
dates reproduce their then-available universe and features.

### Stage 2 - Simple account and paper ledger

Deliver the four-field entry, simple sale, import preview, append-only ledger,
cash/cost/P&L calculations, idempotency, and correction events. A new user can
record a purchase in under one minute.

### Stage 3 - Economically governed formal forecasting

Run permanent baselines and initial candidate models under purged walk-forward,
multiple-testing, uncertainty, and cost controls. Persist all predictions for
later verification.

### Stage 4 - Portfolio risk and advisory engine

Deliver all advisory states, risk limits, drawdown protection, Chinese evidence,
entry/invalidation plans, and immutable daily reports. No model bypasses risk.

### Stage 5 - Event and cross-engine validation

Use fast research for screening and RQAlpha for T+1, price limits, suspension,
cost, slippage, unfilled order, and delayed-rebalance evidence. Every engine
difference is reconciled.

### Stage 6 - Automated model evolution

Deliver prediction maturation, monitoring, monthly challenger retraining,
quarterly full evaluation, MLflow registry aliases, shadow mode, explicit
promotion, drift freeze, and rollback.

### Stage 7 - Local guidance product

Complete the Chinese local dashboard, guidance, holdings, candidates, stock
detail, verification, model/data health, Windows notification option, backup,
restore, and protection-mode workflows.

### Stage 8 - Official QMT read-only integration

After the user obtains 财信证券 permission and SDK, deliver official market and
account read-only adapters, reconciliation, and basket export while automatic
submission stays disabled.

### Stage 9 - Real shadow and paper observation

Run for at least 60 trading days and preferably 120, covering multiple complete
20-day label horizons. Compare backtest, shadow guidance, paper account, and
actual user execution separately. `PAPER_VALIDATED` requires real elapsed
evidence and cannot be accelerated by code generation.

### Stage 10 - Advanced research

Only after the governed V1 is stable, admit deep sequence models, event text,
regime experts, RD-Agent proposals, and RL allocation/execution experiments.

## 14. Definition of complete

The complete system has real historical evidence; reproducible economic,
statistical, and event validation; automatic prediction and maturation; governed
model evolution; a simple account ledger; complete buy/hold/watch/reduce/exit
guidance; risk and drawdown protection; a usable local Chinese product; free data
operation with paid and QMT adapters; automatic order submission disabled; full
tests, security, backup, and recovery; and a truthful distinction between code
completion and real shadow/paper maturity.

External dependencies that cannot be fabricated are paid-provider credentials,
official QMT access/SDK, broker permissions, user confirmation of actual fills
and promotion, and 60-120 real trading days of observation.

## 15. Explicit non-goals

- Guaranteed prediction accuracy or profit.
- Real-money automated trading in the approved scope.
- Broker UI automation, reverse engineering, or credential capture.
- Intraday high-frequency trading.
- Replacing mature open-source frameworks with local clones.
- Letting an LLM, RL agent, GitHub project, or challenger bypass data, economic,
  risk, security, and human-promotion gates.

## 16. Primary references

- 财信证券 official software list: https://stock.hnchasing.com/main/include/software.html
- 财信证券 external system disclosure: https://stock.hnchasing.com/main/khfw/ywgg/detail/1697416829291868162.html
- Shanghai Stock Exchange programmatic trading rules: https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/c_20250612_10781696.shtml
- Microsoft Qlib: https://github.com/microsoft/qlib
- Microsoft RD-Agent: https://github.com/microsoft/RD-Agent
- Optuna: https://github.com/optuna/optuna
- MLflow: https://github.com/mlflow/mlflow
- RQAlpha: https://github.com/ricequant/rqalpha
- QuantStats: https://github.com/ranaroussi/quantstats
- PyPortfolioOpt: https://github.com/robertmartin8/PyPortfolioOpt
- FinRL: https://github.com/AI4Finance-Foundation/FinRL
- Gu, Kelly, and Xiu, Empirical Asset Pricing via Machine Learning: https://www.nber.org/papers/w25398
- Bailey et al., Probability of Backtest Overfitting: https://papers.ssrn.com/sol3/Papers.cfm?abstract_id=2326253
- Hansen, Lunde, and Nason, Model Confidence Set: https://doi.org/10.3982/ECTA5771
