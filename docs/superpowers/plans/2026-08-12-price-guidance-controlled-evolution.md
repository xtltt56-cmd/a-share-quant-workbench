# A股价格指导与受控进化系统实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为官方日选和本机持仓提供可复现的研究级/条件性价格指导，并建立只能人工批准晋升的冠军—挑战者学习闭环。

**Architecture:** 新增独立的价格指导契约、因果特征、确定性执行引擎和原子存储；工作台只把冻结日线计划与实时行情合并，不在浏览器计算金融指标。预测训练、结果登记和模型治理作为工作台监督的可停止子进程运行，任何数据、校准或风险门失败都降级为研究参考或暂无可靠指导。

**Tech Stack:** Python 3.12、pandas、NumPy、PyYAML、scikit-learn、LightGBM、Microsoft Qlib、原生 HTTP/JavaScript、pytest、Ruff、PowerShell。

**Design spec:** `docs/superpowers/specs/2026-08-12-price-guidance-controlled-evolution-design.md`

---

## 文件边界

- `config/price_guidance.yaml`：版本化执行、风险、校准和晋升阈值。
- `advisory/price_contracts.py`：计划、盘中观察、状态和持仓指导契约。
- `features/price_guidance.py`：ATR14、MA20、MA60、S20、A20 与复权因子。
- `data/market_rules.py`：普通沪深 A 股报价单位与失败关闭规则门。
- `advisory/price_engine.py`：价格公式、方向性舍入、仓位和保护价。
- `storage/price_guidance_store.py`：计划/观察的摘要校验和原子存储。
- `runtime/price_guidance.py`：日选、持仓、日线和账户权益的次日计划编排。
- `advisory/price_overlay.py`：冻结计划的盘中状态机。
- `advisory/holding_guidance.py`：持仓、保护价、减仓区间和 T+1。
- `research/forecasting.py`、`research/calibration.py`：预测、概率和区间校准。
- `research/evolution.py`：到期结果、影子评估、晋升和回滚。
- `runtime/research_jobs.py`：工作台拥有的研究子进程与检查点。
- `workbench/service.py`、`advisory_service.py`、`app.py`：API 和简体中文页面。

---

### Task 1: 配置与不可变价格契约

**Files:** Create `config/price_guidance.yaml`, `src/a_share_quant/advisory/price_contracts.py`, `tests/test_price_guidance_contracts.py`; modify `src/a_share_quant/advisory/__init__.py`.

- [ ] **Step 1: 写失败测试**

```python
def test_research_plan_is_never_executable():
    with pytest.raises(ValueError, match="research guidance quantity must be zero"):
        valid_plan(guidance_level="RESEARCH_REFERENCE", suggested_quantity=100)

def test_price_order_is_strict():
    with pytest.raises(ValueError, match="price boundaries are inconsistent"):
        valid_plan(invalidation_price="10.80", entry_lower="10.70")

def test_plan_round_trip_is_stable():
    plan = valid_plan()
    assert PriceGuidancePlan.from_dict(plan.to_dict()) == plan
```

- [ ] **Step 2: 验证 RED**

Run: `D:\量化交易\.venv\Scripts\python.exe -m pytest tests/test_price_guidance_contracts.py -q`  
Expected: collection FAIL, module `price_contracts` absent.

- [ ] **Step 3: 写配置**

```yaml
version: price-guidance-rule-v1
features: {minimum_history_days: 252, atr_period: 14, ma_short_period: 20, ma_long_period: 60, support_period: 20, liquidity_period: 20, minimum_average_amount: 10000000}
entry: {anchor_atr: 0.50, lower_atr: 0.50, upper_atr: 0.25, maximum_close_multiplier: 1.01, minimum_risk_distance: 0.02, maximum_risk_distance: 0.12}
risk: {single_trade_fraction: 0.005, single_position_fraction: 0.05, industry_fraction: 0.20, lot_size: 100}
calibration: {target_coverage: 0.80, rolling_trading_days: 252, minimum_interval_samples: 500, minimum_isotonic_samples: 1000}
promotion: {minimum_shadow_days: 60, minimum_matured_samples: 1000, minimum_oos_windows: 3}
```

- [ ] **Step 4: 实现契约**

```python
class GuidanceLevel(str, Enum):
    RESEARCH_REFERENCE = "RESEARCH_REFERENCE"
    CONDITIONS_MET = "CONDITIONS_MET"

class GuidanceState(str, Enum):
    RESEARCH_REFERENCE = "RESEARCH_REFERENCE"
    CONDITIONS_MET = "CONDITIONS_MET"
    WAIT_FOR_PRICE = "WAIT_FOR_PRICE"
    PRICE_TOO_HIGH = "PRICE_TOO_HIGH"
    INVALIDATED = "INVALIDATED"
    RISK_ALERT = "RISK_ALERT"
    NO_RELIABLE_GUIDANCE = "NO_RELIABLE_GUIDANCE"

@dataclass(frozen=True)
class PriceGuidancePlan:
    plan_id: str
    symbol: str
    plan_type: PricePlanType
    guidance_level: GuidanceLevel
    state: GuidanceState
    calculation_date: date
    valid_for: date
    entry_lower: Decimal | None
    entry_upper: Decimal | None
    maximum_acceptable_price: Decimal | None
    invalidation_price: Decimal | None
    protection_price: Decimal | None
    reduce_lower: Decimal | None
    reduce_upper: Decimal | None
    suggested_quantity: int
    evidence_cutoff: datetime
    model_version: str
    feature_version: str
    config_version: str
    data_version: str
    reason_codes: tuple[str, ...]
    manual_execution_required: bool = True
```

显式实现 `to_dict/from_dict`，拒绝未知字段、NaN/Infinity、非正价格、非 UTC 时间、无效日期、研究级非零数量和不满足 `invalidation < lower <= upper <= maximum` 的边界。

- [ ] **Step 5: GREEN 并提交**

Run: `D:\量化交易\.venv\Scripts\python.exe -m pytest tests/test_price_guidance_contracts.py -q`  
Expected: PASS.

```powershell
git add config/price_guidance.yaml src/a_share_quant/advisory tests/test_price_guidance_contracts.py
git commit -m "feat: define price guidance contracts"
```

### Task 2: 因果日线特征与复权换算

**Files:** Create `src/a_share_quant/features/price_guidance.py`, `tests/test_price_guidance_features.py`; modify `features/__init__.py`.

- [ ] **Step 1: 写失败测试**

```python
def test_future_row_cannot_change_cutoff_features():
    first = build_price_features(bars(), cutoff=date(2026, 8, 12))
    changed = bars_with_future_row(close=999)
    assert build_price_features(changed, cutoff=date(2026, 8, 12)) == first

def test_adjustment_factor_maps_to_actual_price():
    result = build_price_features(bars(raw_close=11, close=10), cutoff=date(2026, 8, 12))
    assert result.to_actual(Decimal("10")) == Decimal("11")

def test_251_rows_are_rejected():
    with pytest.raises(ValueError, match="252"):
        build_price_features(bars(count=251), cutoff=date(2026, 8, 12))
```

- [ ] **Step 2: RED**

Run: `D:\量化交易\.venv\Scripts\python.exe -m pytest tests/test_price_guidance_features.py -q`  
Expected: import FAIL.

- [ ] **Step 3: 实现特征**

```python
previous_close = bounded["close"].shift(1)
true_range = pd.concat([
    bounded["high"] - bounded["low"],
    (bounded["high"] - previous_close).abs(),
    (bounded["low"] - previous_close).abs(),
], axis=1).max(axis=1)
atr14 = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean().iloc[-1]
ma20 = bounded["close"].tail(20).mean()
ma60 = bounded["close"].tail(60).mean()
support20 = bounded["low"].tail(20).min()
amount20 = bounded["amount"].tail(20).mean()
adjustment_factor = bounded.iloc[-1]["raw_close"] / bounded.iloc[-1]["close"]
```

要求 `symbol,date,high,low,close,raw_close,amount,source`；拒绝重复日期、未来/缺口截止日、测试来源、非正值和 `low > high`，并对截止日规范行做 SHA-256 数据版本。

- [ ] **Step 4: GREEN 并提交**

Run: `D:\量化交易\.venv\Scripts\python.exe -m pytest tests/test_price_guidance_features.py tests/test_daily_candidates.py -q`  
Expected: PASS.

```powershell
git add src/a_share_quant/features tests/test_price_guidance_features.py
git commit -m "feat: compute causal price guidance features"
```

### Task 3: 市场规则门与价格执行引擎

**Files:** Create `src/a_share_quant/data/market_rules.py`, `src/a_share_quant/advisory/price_engine.py`, `tests/test_market_price_rules.py`, `tests/test_price_guidance_engine.py`.

- [ ] **Step 1: 写公式与失败关闭测试**

```python
def test_v1_formula_is_deterministic_and_research_quantity_is_zero():
    plan = engine.candidate_plan(features(close="10", atr="0.4", ma20="9.9", ma60="9.4", support="9.2"), promoted=False)
    assert (plan.entry_lower, plan.entry_upper, plan.maximum_acceptable_price) == (Decimal("9.80"), Decimal("10.10"), Decimal("10.10"))
    assert plan.suggested_quantity == 0

def test_unsupported_board_fails_closed():
    with pytest.raises(UnsupportedSecurityRules):
        resolve_security_rule("688001", {"board": "STAR"})

def test_position_size_obeys_all_caps_and_round_lot():
    assert engine.position_size(equity="100000", cash="30000", price="10", invalidation="9.5", industry_value="18000") == 200
```

- [ ] **Step 2: RED**

Run: `D:\量化交易\.venv\Scripts\python.exe -m pytest tests/test_market_price_rules.py tests/test_price_guidance_engine.py -q`  
Expected: import FAIL.

- [ ] **Step 3: 实现普通沪深 A 股规则**

```python
if flags.get("st") or flags.get("ipo_first_five_days") or flags.get("delisting"):
    raise UnsupportedSecurityRules("UNSUPPORTED_SECURITY_RULES")
if not code.startswith(("000", "001", "002", "003", "600", "601", "603", "605")):
    raise UnsupportedSecurityRules("UNSUPPORTED_SECURITY_RULES")
return SecurityPriceRule(tick_size=Decimal("0.01"), lot_size=100, daily_limit=Decimal("0.10"))
```

- [ ] **Step 4: 实现公式与方向性舍入**

```python
anchor = min(C, MA20 + Decimal("0.50") * ATR)
lower = anchor - Decimal("0.50") * ATR
upper = anchor + Decimal("0.25") * ATR
maximum = min(upper, C * Decimal("1.01"))
invalidation = max(S20 - Decimal("0.25") * ATR, MA60 - Decimal("0.50") * ATR, C - Decimal("2.00") * ATR)
protection = max(previous_protection, invalidation) if previous_protection else invalidation
reduce_lower, reduce_upper = MA20 + 2 * ATR, MA20 + 3 * ATR
```

先校验 2%–12% 风险距离，再换算实际价格；下限/失效价向上取 tick，上限/最高价向下取 tick，舍入后重新验证。数量取风险、单股 5%、行业 20%、现金和整手上限的最小值。

- [ ] **Step 5: GREEN 并提交**

Run: `D:\量化交易\.venv\Scripts\python.exe -m pytest tests/test_market_price_rules.py tests/test_price_guidance_engine.py tests/test_advisory_engine.py -q`  
Expected: PASS.

```powershell
git add src/a_share_quant/data/market_rules.py src/a_share_quant/advisory/price_engine.py tests/test_market_price_rules.py tests/test_price_guidance_engine.py
git commit -m "feat: calculate conservative price guidance"
```

### Task 4: 原子存储冻结计划和观察记录

**Files:** Create `src/a_share_quant/storage/price_guidance_store.py`, `tests/test_price_guidance_store.py`; modify `storage/__init__.py`.

- [ ] **Step 1: 写存储 RED 测试**

```python
def test_store_round_trip_and_append_only_observations(tmp_path):
    store = PriceGuidanceStore(tmp_path / "guidance.json")
    store.replace_plans((plan(),))
    store.append_observation(observation())
    assert PriceGuidanceStore(store.path).observations(plan().plan_id) == (observation(),)

@pytest.mark.parametrize("fault", ["tamper", "truncate", "future", "unknown_field", "duplicate"])
def test_invalid_artifact_is_rejected(tmp_path, fault):
    with pytest.raises(ValueError, match="artifact is invalid"):
        PriceGuidanceStore(invalid_artifact(tmp_path, fault))
```

- [ ] **Step 2: RED**

Run: `D:\量化交易\.venv\Scripts\python.exe -m pytest tests/test_price_guidance_store.py -q`  
Expected: import FAIL.

- [ ] **Step 3: 实现严格格式**

```python
body = {"format_version": 1, "operating_mode": "PAPER_ONLY", "plans": encoded_plans, "observations": encoded_observations}
canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
payload = {**body, "sha256": hashlib.sha256(canonical).hexdigest()}
```

沿用同父目录临时文件、`flush/fsync/os.replace`；上限 16 MiB、5,000 计划、100,000 观察；拒绝链接/重解析点、未知字段、重复 ID、悬空引用、未来时间和摘要错误。

- [ ] **Step 4: GREEN 并提交**

Run: `D:\量化交易\.venv\Scripts\python.exe -m pytest tests/test_price_guidance_store.py tests/test_official_signal_store.py tests/test_backup_restore.py -q`  
Expected: PASS.

```powershell
git add src/a_share_quant/storage tests/test_price_guidance_store.py
git commit -m "feat: persist auditable price guidance plans"
```

### Task 5: 生成日选与持仓次日计划

**Files:** Create `src/a_share_quant/runtime/price_guidance.py`, `tests/test_price_guidance_runtime.py`, `tests/test_price_guidance_cli.py`; modify `runtime/__init__.py`, `scripts/quant_cli.py`.

- [ ] **Step 1: 写运行时 RED 测试**

```python
def test_candidate_and_holding_plans_share_cutoff():
    result = runtime().generate(calculation_date=date(2026, 8, 12), valid_for=date(2026, 8, 13))
    assert {p.plan_type.value for p in result.plans} == {"DAILY_CANDIDATE", "HOLDING"}
    assert all(p.suggested_quantity == 0 for p in result.plans)

def test_adjustment_factor_change_recalculates_old_protection():
    result = runtime(previous_plan=factor_one_plan()).generate(
        calculation_date=date(2026, 8, 12), valid_for=date(2026, 8, 13)
    )
    assert "CORPORATE_ACTION_RECALCULATED" in result.plans[0].reason_codes
```

- [ ] **Step 2: RED**

Run: `D:\量化交易\.venv\Scripts\python.exe -m pytest tests/test_price_guidance_runtime.py tests/test_price_guidance_cli.py -q`  
Expected: import/command FAIL.

- [ ] **Step 3: 实现按标的隔离的编排器**

```python
symbols = sorted({s.symbol for s in signals} | {h.symbol for h in holdings})
for symbol in symbols:
    try:
        features = build_price_features(bars.load(symbol), cutoff=calculation_date)
        plans.extend(build_symbol_plans(symbol, features, signals, holdings, valid_for))
    except (ValueError, UnsupportedSecurityRules) as exc:
        plans.append(unavailable_plan(symbol, reason_code(exc)))
store.replace_plans(plans)
```

- [ ] **Step 4: 增加命令**

`price-guidance generate --calculation-date YYYY-MM-DD --valid-for YYYY-MM-DD` 和 `price-guidance inspect --symbol 000001`；只允许项目数据根与固定存储路径，不接收券商凭证或任意模型路径。

- [ ] **Step 5: GREEN 并提交**

Run: `D:\量化交易\.venv\Scripts\python.exe -m pytest tests/test_price_guidance_runtime.py tests/test_price_guidance_cli.py tests/test_official_daily_bootstrap.py -q`  
Expected: PASS.

```powershell
git add src/a_share_quant/runtime scripts/quant_cli.py tests/test_price_guidance_runtime.py tests/test_price_guidance_cli.py
git commit -m "feat: generate frozen daily price plans"
```

### Task 6: 冻结计划盘中状态机与日选页面

**Files:** Create `src/a_share_quant/advisory/price_overlay.py`, `tests/test_price_guidance_overlay.py`; modify `workbench/service.py`, `workbench/app.py`, `tests/test_workbench_service.py`, `tests/test_workbench_app.py`.

- [ ] **Step 1: 写状态机 RED 测试**

```python
@pytest.mark.parametrize(("price", "quality", "state"), [
    ("10.50", "GOOD", "WAIT_FOR_PRICE"), ("10.80", "GOOD", "RESEARCH_REFERENCE"),
    ("11.10", "GOOD", "PRICE_TOO_HIGH"), ("10.20", "GOOD", "INVALIDATED"),
    ("10.80", "STALE", "NO_RELIABLE_GUIDANCE"),
])
def test_overlay(price, quality, state):
    result = PriceGuidanceOverlay().evaluate(plan(), quote(price, quality))
    assert result.state.value == state
    assert result.maximum_acceptable_price == plan().maximum_acceptable_price
```

- [ ] **Step 2: RED**

Run: `D:\量化交易\.venv\Scripts\python.exe -m pytest tests/test_price_guidance_overlay.py tests/test_workbench_service.py -q`  
Expected: import/field FAIL.

- [ ] **Step 3: 实现纯覆盖层**

```python
if quote.quality != "GOOD" or expired: state = NO_RELIABLE_GUIDANCE
elif quote.last > plan.maximum_acceptable_price: state = PRICE_TOO_HIGH
elif quote.last < plan.invalidation_price: state = INVALIDATED
elif plan.entry_lower <= quote.last <= plan.entry_upper: state = plan.guidance_level.value
else: state = WAIT_FOR_PRICE
```

任何分支只创建观察记录，绝不修改计划价格。

- [ ] **Step 4: API/UI 增加摘要和 `<details>`**

列名固定为“当前价、参考买入区间、最高可接受价、失效价、有效期、价格指导”。`null` 显示“暂无可靠指导价”；研究级显示“尚未通过模型晋升门槛，不构成买入建议”。

- [ ] **Step 5: GREEN 并提交**

Run: `D:\量化交易\.venv\Scripts\python.exe -m pytest tests/test_price_guidance_overlay.py tests/test_workbench_service.py tests/test_workbench_app.py -q`  
Expected: PASS.

```powershell
git add src/a_share_quant/advisory/price_overlay.py src/a_share_quant/workbench tests/test_price_guidance_overlay.py tests/test_workbench_service.py tests/test_workbench_app.py
git commit -m "feat: show frozen intraday price guidance"
```

### Task 7: 持仓保护、减仓区间和 T+1

**Files:** Create `advisory/holding_guidance.py`, `tests/test_holding_price_guidance.py`; modify `workbench/advisory_service.py`, `workbench/app.py`, `tests/test_advisory_service.py`, `tests/test_account_import_workflow.py`.

- [ ] **Step 1: 写持仓 RED 测试**

```python
def test_protection_never_moves_down():
    result = engine.evaluate(holding(), plan(protection="9.50", previous="9.70"), current_price="10.20")
    assert result.protection_price == Decimal("9.70")

def test_breach_with_zero_available_is_t1_blocked():
    result = engine.evaluate(holding(total=1000, available=0), plan(protection="9.50"), current_price="9.40")
    assert result.state == "T_PLUS_ONE_BLOCKED"
    assert result.suggested_sell_quantity == 0
```

- [ ] **Step 2: RED**

Run: `D:\量化交易\.venv\Scripts\python.exe -m pytest tests/test_holding_price_guidance.py tests/test_advisory_service.py -q`  
Expected: import/field FAIL.

- [ ] **Step 3: 实现持仓状态**

```python
if current <= protection:
    state = "RISK_ALERT" if available > 0 else "T_PLUS_ONE_BLOCKED"
elif reduce_lower <= current <= reduce_upper and any((edge_weakened, overheated, portfolio_risk)):
    state = "REDUCE_WATCH"
else:
    state = "HOLD_WATCH"
sell_quantity = available if state == "RISK_ALERT" else 0
```

最新已确认券商快照优先提供总量/可卖量；无快照才回退本机账本。来源超过一个交易日即降级，不猜测可卖量。

- [ ] **Step 4: 页面持仓指导表**

显示成本、现价、盈亏、保护价、减仓区间、总量、可卖量、状态和来源；研究阶段固定显示“不构成强制卖出结论”。

- [ ] **Step 5: GREEN 并提交**

Run: `D:\量化交易\.venv\Scripts\python.exe -m pytest tests/test_holding_price_guidance.py tests/test_advisory_service.py tests/test_account_import_workflow.py tests/test_workbench_app.py -q`  
Expected: PASS.

```powershell
git add src/a_share_quant/advisory/holding_guidance.py src/a_share_quant/workbench tests/test_holding_price_guidance.py tests/test_advisory_service.py tests/test_account_import_workflow.py
git commit -m "feat: add holding protection guidance"
```

### Task 8: 成本后预测标签、线性基准和 LightGBM

**Files:** Create `research/forecasting.py`, `tests/test_forecasting_pipeline.py`; modify `pyproject.toml`, `tests/test_production_research_gate.py`.

- [ ] **Step 1: 写标签/走步 RED 测试**

```python
def test_label_is_cost_adjusted_excess_return():
    row = build_forecast_labels(prices(), benchmark(), round_trip_cost=.0012).iloc[0]
    assert row.excess_return_5 == pytest.approx(row.forward_return_5 - row.benchmark_return_5 - .0012)
    assert row.positive_edge_5 == ((row.forward_return_5 - row.benchmark_return_5) > .0062)

def test_folds_never_leak_future():
    assert all(f.train_end < f.validation_start <= f.validation_end < f.test_start for f in train_challengers(dataset()).folds)
```

- [ ] **Step 2: 添加依赖并确认 RED**

在 research extra 增加 `lightgbm>=4.5,<5`、`scikit-learn>=1.5,<2`。  
Run: `D:\量化交易\.venv\Scripts\python.exe -m pytest tests/test_forecasting_pipeline.py -q`  
Expected: module FAIL.

- [ ] **Step 3: 实现标签、756/252/126/126 fold 与简单基准**

```python
positive_edge = excess_return > (round_trip_cost + 0.005)
baseline = LogisticRegression(random_state=20260812, max_iter=2000)
challenger = lightgbm.LGBMClassifier(random_state=20260812, n_jobs=configured_threads)
```

每个 fold 独立拟合特征；固定特征 schema、随机种子、早停和线程数。制品只写 `models/challengers/<id>`，含元数据与 SHA-256 清单。

- [ ] **Step 4: GREEN 并提交**

Run: `D:\量化交易\.venv\Scripts\python.exe -m pytest tests/test_forecasting_pipeline.py tests/test_production_research_gate.py tests/test_qlib_model_runner.py -q`  
Expected: PASS.

```powershell
git add pyproject.toml src/a_share_quant/research/forecasting.py tests/test_forecasting_pipeline.py tests/test_production_research_gate.py
git commit -m "feat: train cost-aware forecast challengers"
```

### Task 9: 概率校准、共形区间和预测记录

**Files:** Create `research/calibration.py`, `tests/test_forecast_calibration.py`; modify `advisory/contracts.py`, `advisory/store.py`, `tests/test_advisory_store.py`.

- [ ] **Step 1: 写校准 RED 测试**

```python
def test_isotonic_requires_1000_independent_samples():
    with pytest.raises(ValueError, match="1000"):
        ProbabilityCalibrator("isotonic").fit(scores(999), outcomes(999))

def test_interval_is_uncalibrated_below_500():
    result = RollingConformalCalibrator().fit(residuals(499), horizon=5, as_of=NOW)
    assert result.status == "UNCALIBRATED" and result.radius is None

def test_future_maturities_are_excluded():
    assert calibrator.fit(outcomes_with_future()).sample_count == 500
```

- [ ] **Step 2: RED**

Run: `D:\量化交易\.venv\Scripts\python.exe -m pytest tests/test_forecast_calibration.py -q`  
Expected: import FAIL.

- [ ] **Step 3: 实现独立 sigmoid/isotonic 和滚动残差分位数**

```python
eligible = rows[(rows.matured_at <= as_of) & (rows.horizon_days == horizon)]
eligible = eligible[eligible.maturity_date.isin(last_252_matured_dates)]
if len(eligible) < 500: return IntervalCalibration("UNCALIBRATED", len(eligible), None)
radius = eligible.absolute_residual.quantile(.80, interpolation="higher")
```

- [ ] **Step 4: 扩展预测记录并 GREEN**

增加 `benchmark_symbol, minimum_edge, calibration_version, interval_lower, interval_upper, interval_status, artifact_sha256`，对旧格式显式迁移或拒绝。  
Run: `D:\量化交易\.venv\Scripts\python.exe -m pytest tests/test_forecast_calibration.py tests/test_advisory_store.py tests/test_advisory_engine.py -q`  
Expected: PASS.

- [ ] **Step 5: 提交**

```powershell
git add src/a_share_quant/research/calibration.py src/a_share_quant/advisory/contracts.py src/a_share_quant/advisory/store.py tests/test_forecast_calibration.py tests/test_advisory_store.py
git commit -m "feat: calibrate forecast probabilities and intervals"
```

### Task 10: 到期结果、DoubleEnsemble 和人工晋升

**Files:** Create `research/evolution.py`, `tests/test_controlled_model_evolution.py`; modify `research/governance.py`, `integrations/qlib/model_runner.py`, `scripts/quant_cli.py`, `tests/test_drift_and_rollback.py`.

- [ ] **Step 1: 写联合门 RED 测试**

```python
def test_passing_challenger_waits_for_human():
    report = evaluator.evaluate(passing_metrics())
    assert report.status == "AWAITING_MANUAL_APPROVAL"
    assert registry.champion_id == "champion-v1"

@pytest.mark.parametrize("gate", ["shadow_days", "samples", "windows", "rank_ic", "brier", "drawdown", "pbo", "reproducibility"])
def test_each_failed_gate_blocks(gate):
    assert evaluator.evaluate(metrics_with_failure(gate)).status == "BLOCKED"

def test_approval_is_audited_and_reversible():
    record = registry.approve(report_id="r2", confirmation_token=one_time_token())
    assert registry.rollback(record.record_id).champion_id == "champion-v1"
```

- [ ] **Step 2: RED**

Run: `D:\量化交易\.venv\Scripts\python.exe -m pytest tests/test_controlled_model_evolution.py tests/test_drift_and_rollback.py -q`  
Expected: import FAIL.

- [ ] **Step 3: 实现联合门**

```python
passes = shadow_days >= 60 and matured_samples >= 1000 and oos_windows >= 3
passes &= improved_horizons >= 2 and brier_not_worse and ece_not_worse
passes &= net_performance_better and drawdown_ok and capacity_ok
passes &= deflated_sharpe_pass and pbo_pass and regimes_pass
passes &= reproducible and not leakage_detected
status = "AWAITING_MANUAL_APPROVAL" if passes else "BLOCKED"
```

DoubleEnsemble 只产出挑战者与影子预测。缺失到期价格保持不可评估，不用 0 填充。

- [ ] **Step 4: 一次性审批/回滚命令**

增加 `model evaluate`、`model approve --report-id --confirmation-token`、`model rollback --promotion-id --confirmation-token`；确认前重验报告和制品哈希，不得有 `--auto-approve`。

- [ ] **Step 5: GREEN 并提交**

Run: `D:\量化交易\.venv\Scripts\python.exe -m pytest tests/test_controlled_model_evolution.py tests/test_drift_and_rollback.py tests/test_qlib_model_runner.py tests/test_production_research_gate.py -q`  
Expected: PASS.

```powershell
git add src/a_share_quant/research src/a_share_quant/integrations/qlib/model_runner.py scripts/quant_cli.py tests/test_controlled_model_evolution.py tests/test_drift_and_rollback.py
git commit -m "feat: govern champion challenger evolution"
```

### Task 11: 工作台研究任务与安全退出

**Files:** Create `runtime/research_jobs.py`, `tests/test_research_job_supervisor.py`; modify `workbench/app.py`, `scripts/quant_cli.py`, start/stop PowerShell scripts, `tests/test_launcher_security.py`, `tests/test_workbench_app.py`.

- [ ] **Step 1: 写生命周期 RED 测试**

```python
def test_shutdown_checkpoints_and_stops_owned_child(tmp_path):
    supervisor = ResearchJobSupervisor(tmp_path, launcher=fake_child_launcher)
    supervisor.start_due_jobs(now=monthly_due())
    result = supervisor.shutdown(timeout_seconds=5)
    assert result.checkpoint_saved and not fake_child_launcher.child.is_running()

def test_corrupt_checkpoint_is_not_resumed(tmp_path):
    write_corrupt_checkpoint(tmp_path)
    assert ResearchJobSupervisor(tmp_path).resume_eligible_jobs() == ()

def test_safe_exit_requires_guarded_loopback_post():
    assert post("/api/system/safe-exit", headers={}).status == 403
```

- [ ] **Step 2: RED**

Run: `D:\量化交易\.venv\Scripts\python.exe -m pytest tests/test_research_job_supervisor.py tests/test_launcher_security.py tests/test_workbench_app.py -q`  
Expected: import/route FAIL.

- [ ] **Step 3: 实现固定 allowlist 监督器**

```python
def shutdown(self, timeout_seconds):
    self.request_atomic_checkpoint()
    self.stop_accepting_jobs()
    return self.wait_then_terminate_owned_children(timeout_seconds)
```

PID 元数据含父 PID、固定命令、创建时间、任务 ID；只停止身份匹配的子进程。检查点用 JSON/安全模型格式、schema、摘要和原子替换，禁止加载不可信 pickle。

- [ ] **Step 4: 接入启动/停止与“安全退出系统”**

不创建开机启动/计划任务。停止脚本先调用受 guard 保护的 safe-exit，超时后只清理登记且身份匹配的进程。关闭后断言无研究/行情子进程。

- [ ] **Step 5: GREEN 并提交**

Run: `D:\量化交易\.venv\Scripts\python.exe -m pytest tests/test_research_job_supervisor.py tests/test_launcher_security.py tests/test_workbench_app.py -q`  
Expected: PASS.

```powershell
git add src/a_share_quant/runtime/research_jobs.py src/a_share_quant/workbench/app.py scripts tests/test_research_job_supervisor.py tests/test_launcher_security.py tests/test_workbench_app.py
git commit -m "feat: supervise research jobs with safe shutdown"
```

### Task 12: 中文模型治理页面和操作文档

**Files:** Create `tests/test_model_governance_http.py`, `docs/PRICE_GUIDANCE_GUIDE_ZH.md`; modify `workbench/app.py`, `workbench/advisory_service.py`, `tests/test_workbench_app.py`, handover report.

- [ ] **Step 1: 写治理 UI RED 测试**

```python
def test_governance_page_is_chinese():
    html = get("/advisory").text
    for text in ("模型治理", "当前冠军", "挑战者", "影子运行", "等待人工批准", "回滚"):
        assert text in html

def test_changed_report_invalidates_approval_token():
    preview = post_json("/api/models/promotion-preview", {"report_id": "r2"})
    mutate_report()
    response = post_json("/api/models/promotion-confirm", {"confirmation_token": preview["confirmation_token"]})
    assert response.status == 409 and response.json()["state"] == "REPORT_CHANGED"
```

- [ ] **Step 2: RED**

Run: `D:\量化交易\.venv\Scripts\python.exe -m pytest tests/test_model_governance_http.py tests/test_workbench_app.py -q`  
Expected: route/text FAIL.

- [ ] **Step 3: 实现比较、预览确认和回滚 UI**

显示样本数、影子天数、OOS 窗口、5/10/20 日 Rank IC、Brier/ECE、成本后收益、回撤、换手、容量、Deflated Sharpe/PBO 和市场状态。审批/回滚均为预览→一次性令牌→确认；对外错误不泄露路径和堆栈。

- [ ] **Step 4: 写中文指南**

指南逐项解释价格字段、研究级数量为何为 0、收盘/盘中节奏、持仓保护、模型晋升、安全退出、失败状态和免责声明；交接报告同步真实测试证据和尚未满足的影子期门槛。

- [ ] **Step 5: GREEN 并提交**

Run: `D:\量化交易\.venv\Scripts\python.exe -m pytest tests/test_model_governance_http.py tests/test_workbench_app.py tests/test_advisory_service.py -q`  
Expected: PASS.

```powershell
git add src/a_share_quant/workbench tests/test_model_governance_http.py tests/test_workbench_app.py docs/PRICE_GUIDANCE_GUIDE_ZH.md reports/a_share_quant_handover_2026-08-11.md
git commit -m "feat: expose controlled model governance"
```

### Task 13: 历史回放、故障注入与最终验收

**Files:** Create `scripts/run_price_guidance_acceptance.py`, `tests/test_price_guidance_acceptance.py`, `reports/price_guidance_acceptance_2026-08-12.md`; modify `config/production_readiness.yaml`.

- [ ] **Step 1: 写端到端 RED 测试**

```python
def test_acceptance_is_real_data_paper_only_and_fail_closed(tmp_path):
    result = run_acceptance(workspace=tmp_path, data_root=REAL_LOCAL_DATA_ROOT)
    assert result["status"] == "PASS"
    assert result["candidate_plans"] > 0 and result["holding_plans"] > 0
    assert result["research_quantities_are_zero"] is True
    assert result["frozen_boundaries_unchanged"] is True
    assert result["manual_execution_required"] is True
    assert result["order_capability_present"] is False
```

- [ ] **Step 2: RED**

Run: `D:\量化交易\.venv\Scripts\python.exe -m pytest tests/test_price_guidance_acceptance.py -q`  
Expected: runner import FAIL.

- [ ] **Step 3: 实现真实本地回放**

读取本地真实 BaoStock 日线、官方信号和脱敏验收账户；回放低于区间、进入区间、超过最高价、跌破失效价、过期行情和复权变化。输出仅含状态、数量、版本和摘要。

- [ ] **Step 4: 分层验证**

Run focused: `D:\量化交易\.venv\Scripts\python.exe -m pytest tests/test_price_guidance_contracts.py tests/test_price_guidance_features.py tests/test_market_price_rules.py tests/test_price_guidance_engine.py tests/test_price_guidance_store.py tests/test_price_guidance_runtime.py tests/test_price_guidance_overlay.py tests/test_holding_price_guidance.py tests/test_forecasting_pipeline.py tests/test_forecast_calibration.py tests/test_controlled_model_evolution.py tests/test_research_job_supervisor.py tests/test_model_governance_http.py tests/test_price_guidance_acceptance.py -q`  
Expected: PASS.

Run full: `D:\量化交易\.venv\Scripts\python.exe -m pytest -q`  
Expected: PASS; only capability-aware Windows symlink tests may SKIP.

Run static: `D:\量化交易\.venv\Scripts\python.exe -m ruff check .` and `git diff --check`  
Expected: exit 0.

- [ ] **Step 5: 真实启动和退出验收**

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_quant_workbench.ps1
Invoke-RestMethod http://127.0.0.1:8765/api/state | ConvertTo-Json -Depth 12
Invoke-RestMethod http://127.0.0.1:8765/api/advisory/holdings | ConvertTo-Json -Depth 12
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop_quant_workbench.ps1
```

确认两个页面中文、日选/持仓指导非空或有明确失败原因、研究数量 0、边界不漂移、训练保存检查点后停止、退出后没有登记的工作台/训练/行情进程。

- [ ] **Step 6: readiness 与报告提交**

`price_guidance` 通过真实计划和失败关闭验收后可标 `PASS`；`formal_model` 在 60 日、1,000 样本和人工晋升前继续 `BLOCKED`。

```powershell
git add scripts/run_price_guidance_acceptance.py tests/test_price_guidance_acceptance.py config/production_readiness.yaml reports/price_guidance_acceptance_2026-08-12.md
git commit -m "test: accept controlled price guidance workflow"
```

---

## 实施检查点

- **A（Task 1–5）：** CLI 能生成真实日线研究计划，数量固定为 0。
- **B（Task 6–7）：** 日选与持仓价格栏可见，盘中冻结、保护价和 T+1 生效。
- **C（Task 8–10）：** 预测、校准、到期结果和人工晋升闭环可运行；样本不足继续研究级。
- **D（Task 11–13）：** 训练随工作台启动/退出，治理页面、故障注入和全量验收完成。

任何检查点失败都先修复并重新运行相关测试。不得使用 fixture、硬编码价格、降低门槛或跳过失败测试填充生产页面。
