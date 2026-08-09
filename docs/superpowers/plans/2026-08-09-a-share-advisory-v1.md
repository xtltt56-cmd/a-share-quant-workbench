# A-Share Advisory V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a local, paper/manual-execution A-share decision-support system with auditable data, account records, governed forecasts, portfolio risk controls, Chinese guidance, and a read-only QMT integration boundary.

**Architecture:** Preserve the existing PIT, Qlib, realtime-monitoring, and paper-only safety boundaries. Add independent account, advisory, prediction-evidence, model-governance, and broker-read-only modules behind explicit contracts; the dashboard composes immutable snapshots from them and never submits an order.

**Tech Stack:** Python 3.12, Pydantic/dataclasses, DuckDB/Parquet, JSONL audit logs, pandas/numpy, existing Qlib adapters, optional MLflow/Optuna/QMT SDKs, stdlib loopback HTTP UI.

---

## File structure

| Path | Responsibility |
|---|---|
| `src/a_share_quant/account/contracts.py` | Immutable fill, position, cash, import, and correction value objects. |
| `src/a_share_quant/account/ledger.py` | Append-only accounting, A-share T+1 and lot rules, cost basis, P&L, idempotency. |
| `src/a_share_quant/account/store.py` | Atomic local JSONL persistence and deterministic replay. |
| `src/a_share_quant/account/importer.py` | Safe broker-file column mapping and preview; no credentials. |
| `src/a_share_quant/advisory/contracts.py` | Forecast, risk-input, recommendation, advisory-state, and daily-report contracts. |
| `src/a_share_quant/advisory/risk.py` | Exposure, concentration, drawdown, cash, liquidity, and position-size gates. |
| `src/a_share_quant/advisory/engine.py` | Deterministic BUY/HOLD/WATCH/REDUCE/EXIT/BLOCKED/INSUFFICIENT_DATA decisions. |
| `src/a_share_quant/advisory/store.py` | Append-only prediction/outcome/recommendation ledger and report snapshots. |
| `src/a_share_quant/research/governance.py` | Economic hypothesis, model card, challenger evidence, shadow, promotion, rollback. |
| `src/a_share_quant/research/validation.py` | Cost-aware multi-horizon, uncertainty, perturbation, and promotion metrics. |
| `src/a_share_quant/data/provenance.py` | Provider evidence, canonical-key, freshness, hash, and data-cutoff gates. |
| `src/a_share_quant/integrations/qmt/read_only.py` | Optional official-QMT detection/read-only protocol and permanent order rejection. |
| `src/a_share_quant/workbench/advisory_service.py` | Local composition layer for account, guidance, model/data health, and backups. |
| `src/a_share_quant/workbench/app.py` | Loopback-only API and plain-Chinese dashboard pages. |

### Task 1: Add durable account contracts and append-only ledger

**Files:**
- Create: `src/a_share_quant/account/__init__.py`
- Create: `src/a_share_quant/account/contracts.py`
- Create: `src/a_share_quant/account/ledger.py`
- Create: `src/a_share_quant/account/store.py`
- Create: `tests/test_account_ledger.py`
- Create: `tests/test_account_store.py`

- [ ] **Step 1: Write the failing four-field purchase tests.**

```python
def test_four_field_buy_creates_a_t_plus_one_position_and_deducts_cash() -> None:
    ledger = AccountLedger(initial_cash=100_000, fee_schedule=FeeSchedule())
    receipt = ledger.record_buy(name="平安银行", symbol="000001", quantity=100, price=10)
    assert receipt.position.available_quantity == 0
    assert receipt.position.total_quantity == 100
    assert receipt.cash < 100_000
```

- [ ] **Step 2: Run the focused test and observe the expected import failure.**

Run: `python -m pytest tests/test_account_ledger.py::test_four_field_buy_creates_a_t_plus_one_position_and_deducts_cash -q`

Expected: FAIL because `a_share_quant.account` does not yet exist.

- [ ] **Step 3: Implement immutable `FillEvent`, `PositionSnapshot`, `FeeSchedule`, and `AccountLedger`.**

```python
@dataclass(frozen=True)
class FillEvent:
    event_id: str
    side: Literal["BUY", "SELL", "REVERSAL"]
    symbol: str
    quantity: int
    price: Decimal
    trade_date: date

class AccountLedger:
    def record_buy(self, *, name: str, symbol: str, quantity: int, price: Decimal | float) -> LedgerReceipt: ...
    def record_sell(self, *, symbol: str, quantity: int, price: Decimal | float) -> LedgerReceipt: ...
    def append_correction(self, *, original_event_id: str, replacement: FillEvent) -> LedgerReceipt: ...
```

Enforce 100-share lots for normal shares, positive finite values, name/code agreement, available-cash checks, T+1 sellability, duplicate event rejection, and deterministic weighted-average cost basis.

- [ ] **Step 4: Add tests for sale, correction, insufficient cash, duplicate event, and deterministic replay; run them.**

Run: `python -m pytest tests/test_account_ledger.py tests/test_account_store.py -q`

Expected: PASS.

- [ ] **Step 5: Implement `JsonlLedgerStore.append` with fsync and `load` replay, then commit.**

Run: `python -m ruff check src/a_share_quant/account tests/test_account_ledger.py tests/test_account_store.py`

Commit: `git commit -m "feat: add append-only account ledger"`.

### Task 2: Add safe broker-file preview and a simple manual input service

**Files:**
- Create: `src/a_share_quant/account/importer.py`
- Create: `src/a_share_quant/account/service.py`
- Create: `tests/test_account_importer.py`
- Modify: `src/a_share_quant/account/__init__.py`

- [ ] **Step 1: Write failing preview tests for an Excel/CSV-like column mapping without writing a ledger event.**

```python
def test_import_preview_maps_columns_and_does_not_mutate_ledger(tmp_path: Path) -> None:
    preview = preview_broker_rows(rows=[{"证券代码": "000001", "成交数量": 100, "成交价格": 10}])
    assert preview.accepted_rows == 1
    assert preview.events == ()
```

- [ ] **Step 2: Run the focused test and confirm failure.**

Run: `python -m pytest tests/test_account_importer.py::test_import_preview_maps_columns_and_does_not_mutate_ledger -q`

- [ ] **Step 3: Implement whitelisted mapping, preview errors, source-file SHA-256 idempotency, and explicit confirmation.**

```python
class AccountEntryService:
    def preview_import(self, source: Path, mapping: Mapping[str, str]) -> ImportPreview: ...
    def confirm_preview(self, preview_id: str) -> tuple[LedgerReceipt, ...]: ...
```

Only normalized trade fields cross the boundary; raw broker files remain local and no password/token column is read or stored.

- [ ] **Step 4: Test malformed rows, duplicate file hash, named-code mismatch, and confirmation replay; run the test module.**

Run: `python -m pytest tests/test_account_importer.py -q`

- [ ] **Step 5: Commit the import boundary.**

Commit: `git commit -m "feat: add safe account import preview"`.

### Task 3: Add data provenance and swappable source gates

**Files:**
- Create: `src/a_share_quant/data/provenance.py`
- Create: `src/a_share_quant/data/providers/registry.py`
- Create: `tests/test_data_provenance.py`
- Create: `tests/test_provider_registry.py`
- Modify: `src/a_share_quant/data/providers/base.py`
- Modify: `src/a_share_quant/config.py`

- [ ] **Step 1: Write failing tests that reject a future cutoff, duplicate canonical key, mismatched source hash, and unapproved provider cutover.**

```python
def test_provider_comparison_must_pass_before_source_becomes_formal() -> None:
    registry = ProviderRegistry(formal_provider="akshare")
    with pytest.raises(ValueError, match="comparison evidence"):
        registry.promote("tushare")
```

- [ ] **Step 2: Run the focused provenance tests and confirm failure.**

Run: `python -m pytest tests/test_data_provenance.py tests/test_provider_registry.py -q`

- [ ] **Step 3: Implement immutable evidence records and a provider comparison gate.**

```python
@dataclass(frozen=True)
class DataEvidence:
    provider: str
    fetched_at: datetime
    effective_at: datetime
    content_sha256: str
    canonical_key: tuple[str, date]

class ProviderRegistry:
    def record_comparison(self, report: ProviderComparison) -> None: ...
    def promote(self, provider: str) -> None: ...
```

The formal provider remains unchanged when coverage, timestamps, adjustment basis, identifier mapping, or quality differs outside configured tolerance.

- [ ] **Step 4: Run focused and existing provider/PIT regressions.**

Run: `python -m pytest tests/test_data_provenance.py tests/test_provider_registry.py tests/test_future_leak.py tests/test_pit_financial_data.py -q`

- [ ] **Step 5: Commit.**

Commit: `git commit -m "feat: add provenance and provider promotion gates"`.

### Task 4: Add multi-horizon forecast evidence and economic/model governance

**Files:**
- Create: `src/a_share_quant/advisory/contracts.py`
- Create: `src/a_share_quant/advisory/store.py`
- Create: `src/a_share_quant/research/governance.py`
- Create: `src/a_share_quant/research/validation.py`
- Create: `tests/test_prediction_ledger.py`
- Create: `tests/test_model_governance.py`
- Modify: `src/a_share_quant/promotion.py`

- [ ] **Step 1: Write failing tests for 5/10/20-day prediction immutability, outcome maturation, and a challenger that cannot self-promote.**

```python
def test_matured_prediction_appends_outcome_without_mutating_original() -> None:
    store = PredictionLedgerStore()
    prediction = ForecastRecord.for_horizons(symbol="000001", horizons=(5, 10, 20))
    store.append_prediction(prediction)
    store.mature(as_of=date(2026, 8, 20), price_lookup=lambda *_: 11.0)
    assert store.predictions()[0] == prediction
    assert store.outcomes()[0].horizon_days == 5
```

- [ ] **Step 2: Run the focused tests and confirm failure.**

Run: `python -m pytest tests/test_prediction_ledger.py tests/test_model_governance.py -q`

- [ ] **Step 3: Implement `ForecastRecord`, `OutcomeRecord`, model cards, economic hypotheses, shadow-mode records, and human-approval promotion.**

```python
class ModelGovernance:
    def register_challenger(self, card: ModelCard, evidence: ValidationBundle) -> None: ...
    def request_promotion(self, candidate_id: str) -> PromotionRequest: ...
    def approve_promotion(self, request_id: str, approved_by: str) -> PromotionDecision: ...
```

Promotion must require causal validation, walk-forward stability, post-cost value, drawdown/tail constraints, calibration, perturbation results, drift status, shadow evidence, and explicit approval; no call may create a `LIVE` state.

- [ ] **Step 4: Implement deterministic multi-horizon validation summary and add tests for abstention with wide uncertainty.**

Run: `python -m pytest tests/test_prediction_ledger.py tests/test_model_governance.py tests/test_promotion_gate.py tests/test_fair_evaluator.py -q`

- [ ] **Step 5: Commit.**

Commit: `git commit -m "feat: add governed forecast evidence ledger"`.

### Task 5: Add risk gates and deterministic Chinese advisory guidance

**Files:**
- Create: `src/a_share_quant/advisory/risk.py`
- Create: `src/a_share_quant/advisory/engine.py`
- Create: `src/a_share_quant/advisory/reporting.py`
- Create: `tests/test_advisory_risk.py`
- Create: `tests/test_advisory_engine.py`
- Modify: `config/risk.yaml`

- [ ] **Step 1: Write failing tests for stale-data abstention, risk-off blocking, T+1 holdings, drawdown protection, and a valid BUY candidate.**

```python
def test_risk_off_and_stale_data_produce_blocked_not_buy() -> None:
    decision = AdvisoryEngine(RiskPolicy.conservative()).evaluate(stale_input)
    assert decision.state is AdvisoryState.BLOCKED
    assert decision.action_zh == "暂不操作"
```

- [ ] **Step 2: Run the focused test and confirm failure.**

Run: `python -m pytest tests/test_advisory_engine.py tests/test_advisory_risk.py -q`

- [ ] **Step 3: Implement `RiskPolicy` and ordered gates.**

```python
class AdvisoryEngine:
    def evaluate(self, context: AdvisoryContext) -> AdvisoryDecision: ...

class RiskEngine:
    def assess(self, portfolio: PortfolioSnapshot, candidate: ForecastRecord) -> RiskAssessment: ...
```

Order gates as: data/tradability → market regime → formal forecast → event/fundamental flag → portfolio concentration/cash/drawdown/liquidity. Calculate an initial position by risk budget and invalidation distance, then clip to 70%/40%/20% gross exposure, 8% single-name, 25% industry, ten holdings, and 30% cash reserve.

- [ ] **Step 4: Add report tests that require action, evidence cutoff, holding horizon, position range, invalidation, cancellation conditions, confidence, and Chinese explanation.**

Run: `python -m pytest tests/test_advisory_engine.py tests/test_advisory_risk.py -q`

- [ ] **Step 5: Commit.**

Commit: `git commit -m "feat: add risk-gated advisory engine"`.

### Task 6: Add local dashboard/API, backup, restore, and audit trails

**Files:**
- Create: `src/a_share_quant/workbench/advisory_service.py`
- Create: `src/a_share_quant/workbench/backup.py`
- Create: `tests/test_advisory_service.py`
- Create: `tests/test_backup_restore.py`
- Modify: `src/a_share_quant/workbench/app.py`
- Modify: `scripts/quant_cli.py`

- [ ] **Step 1: Write failing API tests for four-field buy preview/confirmation, holdings, today guidance, model/data health, and non-loopback rejection.**

```python
def test_local_api_accepts_only_posted_four_field_buy_with_confirmation() -> None:
    response = request_json("POST", "/api/account/buys", {"name": "平安银行", "code": "000001", "quantity": 100, "price": 10})
    assert response["status"] == "PREVIEW"
```

- [ ] **Step 2: Run focused API tests and confirm failure.**

Run: `python -m pytest tests/test_advisory_service.py -q`

- [ ] **Step 3: Implement loopback routes and a plain-Chinese dashboard.**

The route handler may accept only JSON `POST` requests carrying the local request header. It must return `Cache-Control: no-store`, sanitized errors, no credential fields, and explicit `manual_execution_required: true`; it must never expose an order submission route.

- [ ] **Step 4: Implement versioned ZIP backup manifest and restore preflight.**

```python
class BackupService:
    def create(self, destination: Path) -> BackupManifest: ...
    def preflight_restore(self, archive: Path) -> RestorePreview: ...
    def restore(self, archive: Path, *, confirmation_token: str) -> RestoreResult: ...
```

The restore path validates archive hashes and rejects path traversal; it writes a local audit entry before replacing a recoverable account store.

- [ ] **Step 5: Run service, backup, launcher, and security tests; commit.**

Run: `python -m pytest tests/test_advisory_service.py tests/test_backup_restore.py tests/test_workbench_app.py tests/test_launcher_security.py -q`

Commit: `git commit -m "feat: add local advisory workbench"`.

### Task 7: Add official-QMT read-only capability boundary and basket export

**Files:**
- Create: `src/a_share_quant/integrations/qmt/__init__.py`
- Create: `src/a_share_quant/integrations/qmt/read_only.py`
- Create: `src/a_share_quant/integrations/qmt/basket.py`
- Create: `tests/test_qmt_read_only.py`
- Modify: `src/a_share_quant/integrations/__init__.py`

- [ ] **Step 1: Write failing tests proving absent SDK produces `SDK_NOT_FOUND` and all order calls are rejected.**

```python
def test_qmt_adapter_never_submits_orders() -> None:
    adapter = QmtReadOnlyAdapter(client=FakeClient())
    with pytest.raises(PermissionError, match="manual execution"):
        adapter.submit_order(symbol="000001", quantity=100, price=10)
```

- [ ] **Step 2: Run focused tests and confirm failure.**

Run: `python -m pytest tests/test_qmt_read_only.py -q`

- [ ] **Step 3: Implement optional official client protocol, market/account snapshot normalization, ledger reconciliation preview, and CSV/JSON basket export.**

```python
class QmtReadOnlyAdapter:
    def detect(self) -> QmtCapability: ...
    def fetch_account_snapshot(self) -> AccountSnapshot: ...
    def reconciliation_preview(self, local: AccountLedger) -> ReconciliationPreview: ...
    def submit_order(self, **_: object) -> NoReturn: raise PermissionError(...)
```

No UI automation, reverse-engineered protocol, credential storage, or network call is permitted without the official user-installed SDK.

- [ ] **Step 4: Run the focused suite and commit.**

Run: `python -m pytest tests/test_qmt_read_only.py -q`

Commit: `git commit -m "feat: add QMT read-only boundary"`.

### Task 8: Produce end-to-end fixtures, quality report, and release evidence

**Files:**
- Create: `tests/test_end_to_end_advisory.py`
- Create: `scripts/run_advisory_acceptance.py`
- Create: `reports/a_share_advisory_v1_acceptance.md`
- Modify: `README.md`

- [ ] **Step 1: Write the end-to-end failing test for data-quality failure, manual buy registration, forecast maturity, advisory report, and QMT order rejection.**

```python
def test_end_to_end_local_manual_advisory_flow(tmp_path: Path) -> None:
    result = run_acceptance_fixture(tmp_path)
    assert result["manual_execution_required"] is True
    assert result["qmt_order_submission"] == "REJECTED"
```

- [ ] **Step 2: Run the test and confirm failure.**

Run: `python -m pytest tests/test_end_to_end_advisory.py -q`

- [ ] **Step 3: Implement only the acceptance orchestrator and report renderer inputs; do not embed strategy logic in the script.**

- [ ] **Step 4: Run the full quality gate.**

Run:

```powershell
python -m pytest --cov=src/a_share_quant --cov-report=term -q
python -m ruff check .
python -m pip check
python -m compileall -q src scripts
git diff --check
```

Expected: every test passes; the report identifies fixture, offline, historical, real-market, and paper evidence separately.

- [ ] **Step 5: Commit the acceptance assets.**

Commit: `git commit -m "docs: add advisory V1 acceptance evidence"`.

## External evidence gates

The following are deliberately not satisfiable by code generation and remain hard release gates: official 财信证券 QMT permission/SDK availability; paid-provider credentials if free-source quality is insufficient; real historical data that passes PIT/coverage gates; and 60–120 observed A-share trading days with matured 20-day outcomes. The system must keep automatic order submission disabled in every state.

## Plan self-review

- Spec coverage: Tasks 1–3 implement account, PIT/provenance, and free-to-paid/QMT data-provider boundaries; Tasks 4–5 implement governed forecasting, prediction maturity, advisory states, economic/risk gates, and drawdown protections; Tasks 6–7 implement the local product, backup/audit, and official-QMT read-only boundary; Task 8 records deterministic and full-suite acceptance evidence.
- Scope: The plan intentionally excludes automated orders, broker UI automation, unofficial broker protocols, fabricated data, and promotion based on fixture/replay results.
- Type consistency: `ForecastRecord`, `AccountLedger`, `RiskPolicy`, `AdvisoryContext`, `AdvisoryDecision`, `QmtReadOnlyAdapter`, and `BackupService` are each created before an API/service consumes them.
