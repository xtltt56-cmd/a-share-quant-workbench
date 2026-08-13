# A股量化系统可靠性升级 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复阻止系统形成可靠日选、盘中监控、持仓指导和可审计模型升级的已复现缺陷。

**Architecture:** 以数据契约和失败关闭为中心，先修复实时与日线真值，再统一运行时发布，最后接通持仓及研究治理。每项变更先写能复现旧缺陷的测试，再做最小实现并提交独立变更。

**Tech Stack:** Python 3.12、pandas、PyArrow、BaoStock、AKShare、pytest、Ruff、Windows PowerShell。

---

### Task 1: 实时行情质量与生命周期

**Files:**
- Modify: `src/a_share_quant/data/realtime/akshare.py`
- Modify: `src/a_share_quant/storage/realtime_store.py`
- Modify: `src/a_share_quant/runtime/scheduler.py`
- Modify: `src/a_share_quant/workbench/service.py`
- Test: `tests/test_realtime_providers.py`
- Test: `tests/test_realtime_scheduler.py`
- Test: `tests/test_workbench_service.py`

- [ ] 写回归测试：优先股票复用带交易所时间的全市场快照、完整快照清除上一代缺失报价、单只坏报价不降级其他股票、健康状态不得在数据失败时报告就绪。
- [ ] 运行目标测试并确认因旧行为失败。
- [ ] 实现快照筛选、代际替换、逐行质量和分层健康状态。
- [ ] 运行实时链路测试并提交。

### Task 2: 日线完整性与复权一致性

**Files:**
- Modify: `src/a_share_quant/data/providers/baostock.py`
- Modify: `src/a_share_quant/data/pipeline.py`
- Modify: `src/a_share_quant/runtime/daily_refresh.py`
- Modify: `src/a_share_quant/runtime/price_guidance.py`
- Test: `tests/test_baostock_provider.py`
- Test: `tests/test_data_pipeline.py`
- Test: `tests/test_daily_refresh.py`

- [ ] 写回归测试：前复权修订不能形成混合序列；不足最小历史和损坏文件不得跳过；数据文件记录复权模式。
- [ ] 运行目标测试并观察正确失败。
- [ ] 以不复权原价为持久化边界，增加有界回补、损坏修复和模式校验。
- [ ] 运行数据测试并提交。

### Task 3: 可交易股票池与交易日历

**Files:**
- Create: `src/a_share_quant/market/trading_calendar.py`
- Modify: `src/a_share_quant/research/daily_candidates.py`
- Modify: `src/a_share_quant/runtime/price_guidance.py`
- Test: `tests/test_daily_candidates.py`
- Test: `tests/test_price_guidance_runtime.py`

- [ ] 写回归测试：停牌/ST/上市不足股票被排除；单只落后股票不拖回共同日期；周五与假期返回下一交易日。
- [ ] 运行测试确认旧实现失败。
- [ ] 增加日历接口和点时资格连接；以覆盖率充分的合格股票确定截止日。
- [ ] 运行研究与指导价测试并提交。

### Task 4: 单一日终发布与持仓指导

**Files:**
- Create: `src/a_share_quant/runtime/eod_coordinator.py`
- Modify: `scripts/quant_cli.py`
- Modify: `src/a_share_quant/workbench/app.py`
- Modify: `src/a_share_quant/workbench/advisory_service.py`
- Modify: `src/a_share_quant/storage/official_signal_store.py`
- Test: `tests/test_workbench_app.py`
- Test: `tests/test_advisory_service.py`
- Test: `tests/test_official_signal_persistence.py`

- [ ] 写回归测试：启动仅刷新一次、运行跨日后刷新、刷新状态与实际激活版本一致、导入持仓可使用验证行情生成指导。
- [ ] 运行测试确认失败。
- [ ] 实现单一协调器、原子发布和持仓并集合并。
- [ ] 运行工作台与账户测试并提交。

### Task 5: 可持久化研究治理与交付保障

**Files:**
- Modify: `src/a_share_quant/research/evolution.py`
- Modify: `src/a_share_quant/research/forecasting.py`
- Modify: `src/a_share_quant/runtime/research_jobs.py`
- Modify: `scripts/quant_cli.py`
- Create: `constraints/runtime-py312.txt`
- Create: `.github/workflows/windows-tests.yml`
- Test: `tests/test_evolution_governance.py`
- Test: `tests/test_forecasting.py`
- Test: `tests/test_research_jobs.py`

- [ ] 写回归测试：重启恢复冠军和审计、训练模型产物可加载、工作台关闭停止研究子进程。
- [ ] 运行测试确认失败。
- [ ] 持久化治理和模型产物，注册工作台生命周期任务；增加依赖锁定与Windows CI。
- [ ] 运行研究测试并提交。

### Task 6: 整体验收

- [ ] 运行 `python -m pytest -q`，预期零失败。
- [ ] 运行 `python -m ruff check .`、`python -m compileall -q src scripts`、`python -m pip check`，预期退出码为0。
- [ ] 在交易时段验证 `/api/health`、`/api/state`、日选、盘中监控和持仓页面；非交易时段验证缓存明确标记为过期且不产生买卖结论。
- [ ] 更新交接报告，记录免费数据限制、QMT接入点、未消除风险和恢复步骤。
