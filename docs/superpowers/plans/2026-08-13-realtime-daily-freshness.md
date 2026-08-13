# 实时行情与日线新鲜度升级实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让工作台优先更新官方日选、持仓和人工登记股票，限制全市场慢请求，并在日线历史落后时明确提示而不是继续伪装成最新推荐。

**Architecture:** 实时调度器在开盘时先请求优先股票，非全市场刷新周期只返回这些快速结果；全市场快照按至少 60 秒刷新且失败只尝试一次。日线候选生成增加交易日新鲜度闸门，启动时保留上次可审计结果但标记为过期/更新失败。工作台状态增加最新、最旧、过期数量和日线截止日期等字段，前端以简体中文展示。

**Tech Stack:** Python 3.12、pytest、AKShare 公共行情、BaoStock 本地日线、现有 WorkbenchService/RealTimeScheduler、原生 HTML/JavaScript。

---

### Task 1: 实时优先股票与全市场节流

**Files:**
- Modify: `src/a_share_quant/data/realtime/akshare.py`
- Modify: `src/a_share_quant/runtime/scheduler.py`
- Modify: `src/a_share_quant/workbench/service.py`
- Modify: `src/a_share_quant/data/realtime/registry.py`（仅在接口转发需要时）
- Test: `tests/test_realtime_scheduler.py`
- Test: `tests/test_realtime_providers.py`

- [x] **Step 1: Write failing tests** for priority quotes being returned before the next full-market interval, full-market failure being attempted once, and AKShare priority requests not falling back to a full-market endpoint.
- [x] **Step 2: Run the focused tests** and confirm they fail because the scheduler/provider have no priority path.
- [x] **Step 3: Implement the minimal priority path**: add `get_priority_quotes`, add scheduler `priority_symbols`, `expected_symbols`, monotonic full-market interval and one-attempt full snapshot; wire WorkbenchService to derive official signal symbols and pass configured priority symbols.
- [x] **Step 4: Run focused realtime tests** and then all realtime/scheduler/workbench tests.

### Task 2: 日线数据新鲜度闸门

**Files:**
- Modify: `src/a_share_quant/research/daily_candidates.py`
- Modify: `src/a_share_quant/runtime/official_daily.py`
- Modify: `src/a_share_quant/storage/official_signal_store.py`
- Modify: `scripts/quant_cli.py`
- Test: `tests/test_daily_candidates.py`
- Test: `tests/test_official_daily_bootstrap.py`

- [x] **Step 1: Write failing tests** for business-day cutoff validation and bootstrap status `STALE_DATA`/`FRESH`.
- [x] **Step 2: Run the focused tests** and confirm the stale dataset is currently accepted without a status.
- [x] **Step 3: Implement** `DailyDataStaleError`, an Asia/Shanghai expected-complete-date helper, `require_fresh` generation, explicit bootstrap status/notice, and an explicit CLI `--allow-stale` escape hatch.
- [x] **Step 4: Run daily candidate and bootstrap tests** plus the CLI parser tests.

### Task 3: 工作台状态和简体中文页面

**Files:**
- Modify: `src/a_share_quant/workbench/service.py`
- Modify: `src/a_share_quant/workbench/app.py`
- Test: `tests/test_workbench_service.py`
- Test: `tests/test_workbench_app.py`

- [x] **Step 1: Write failing tests** for stale/fresh quote counters, oldest/latest timestamps, daily cutoff/status fields, and Chinese labels.
- [x] **Step 2: Run the focused tests** and confirm the fields/labels are absent.
- [x] **Step 3: Implement** state fields and rendering; retain cached rows as `CACHED/STALE` and never mark them actionable.
- [x] **Step 4: Run workbench tests and render/API smoke checks.**

### Task 4: Full verification and runtime restart

**Files:**
- No new production files; update `docs/` only if the handoff evidence needs a note.

- [x] **Step 1: Run the complete Python test suite, Ruff, and `git diff --check`.**
- [x] **Step 2: Restart the local workbench using the project scripts and query `/api/state`.**
- [x] **Step 3: Verify the page reports Chinese source/quality/status, a non-empty daily list when the durable artifact exists, and an honest stale/failed notice when the free provider is slow or history is behind.
- [x] **Step 4: Record exact commands and observed limitations in the final handoff report.**

## Self-review

- The plan covers the approved real-time, daily, UI, and verification requirements; no QMT credentials are required for this phase.
- The priority path does not claim fresh data when a provider call fails: cached values remain explicitly stale.
- Daily freshness uses weekdays as a conservative public-data guard; exchange holidays can only reduce freshness, so the UI reports the cutoff rather than inventing a signal date.
- Full-market polling remains no faster than 60 seconds to respect AKShare public endpoint limits.
