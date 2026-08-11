# A股量化指导系统生产可用化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保持“人工下单、禁止程序化报单”的安全边界下，把现有系统升级为可长期运行、全中文、数据可追溯、预测可验证、建议可解释，并可在获得财信证券官方授权后接入只读行情/资产/持仓的A股量化指导系统。

**Architecture:** 免费历史与实时数据先进入带来源证据和质量状态的数据层，再经过时间点正确的特征、成本真实的走步回测和受治理的冠军/挑战者模型，最终与人工账本及风险约束组合成建议。正式建议、盘中监控、研究实验、券商账户和下单执行保持隔离；任何缺失、过期、冲突或未经验证的数据都只能产生“观察/暂不操作”，不能伪装为可交易信号。

**Tech Stack:** Python 3.12、pytest、Ruff、pandas、NumPy、PyArrow/Parquet、DuckDB、BaoStock、AKShare、可选 Tushare Pro、Qlib LightGBM/DoubleEnsemble、MLflow、Windows PowerShell/任务计划程序、stdlib loopback HTTP UI、可选财信迅投 QMT XtQuant。

---

## 0. 范围、现状与不可妥协边界

### 已有基础（本计划不重复重做）

- 本地中文工作台和人工投顾页面、回环地址绑定、纸面模式和下单封禁。
- BaoStock 历史日线、AKShare 新浪/东财/腾讯免费实时源降级链。
- 官方日选持久化、盘中监控、四字段人工买入登记、账本、备份恢复。
- PIT、Qlib、走步切分、规则模型、LightGBM、DoubleEnsemble、模型治理和预测账本的基础合同。
- QMT 只读安全边界与篮子导出，但尚无真实 XtQuant 会话和账户同步。
- 最近一次全量验证基线：396 passed、1 skipped、Ruff 通过；实施开始时必须重新运行，不直接沿用旧结论。

### 永久边界

- 不实现自动下单、撤单、键鼠代点、界面抓取、OCR 交易或客户端逆向。
- 不承诺收益或预测准确率；只能承诺数据、验证、风控和报告过程满足明确标准。
- 不用 fixture、硬编码行情或未来数据填充生产栏目。
- 未通过数据质量、样本外、成本、风险和治理门槛时，建议必须降级为“暂不操作/仅观察”。
- 模型不能自行改写生产代码或直接自我晋升；所谓“进化”是离线训练、挑战者评估、影子观察和受控晋升。

## 1. 自动化范围总表

### A. 现在即可由工程代理自动完成

1. 冻结当前基线、建立生产验收矩阵和数据/模型版本清单。
2. 扩充并增量更新免费历史行情、基准、证券清单和交易日历。
3. 比较 BaoStock、AKShare、Tushare 等免费源，记录覆盖、时效、复权和差异，自动降级但不盲目混源。
4. 建立历史证券状态、停牌、ST、上市/退市、涨跌停、复权和公司行动边界。
5. 尽免费数据能力建立公告日正确的 PIT 基本面层；覆盖不足的字段自动禁用对应因子。
6. 建立跨进程实时快照缓存、摘要校验、过期标记和重启恢复。
7. 完善免费实时源熔断、冷却、端点粘性、交易时段调度、日志和健康监控。
8. 完善A股真实回测：T+1、100股、佣金、最低佣金、印花税、过户费、滑点、涨跌停、停牌、成交容量。
9. 运行规则、LightGBM、DoubleEnsemble 等基线与挑战者的固定样本外和走步实验。
10. 计算 Rank IC、ICIR、分层单调性、校准、回撤、CVaR、换手、容量、Deflated Sharpe、PBO 和稳健性。
11. 建立预测—结果成熟—漂移监控—挑战者晋升的受控闭环。
12. 将正式预测、市场状态、持仓、成本和风险合成为中文“买入候选/持有/减仓/退出/观察/暂不操作”指导。
13. 保留四字段人工登记，并增加券商导出 CSV 的预览、确认、幂等导入和对账。
14. 完成所有界面、错误、状态、理由码、模型卡、数据卡和操作报告的简体中文化。
15. 建立 Windows 一键启动/停止、每日更新、日选生成、备份、日志轮换、健康检查和失败报告。
16. 建立券商中立的只读合同、模拟适配器和正式 SDK 缺失时的失败关闭逻辑。
17. 完成全量测试、静态检查、恢复演练、性能测试、安全复核和交接报告。

### B. 完成一次人工授权后，可继续由工程代理自动完成

1. 发现财信迅投 QMT 安装目录、`userdata_mini` 和 `xtquant`。
2. 建立 XtQuant 只读会话健康检查和权限探测。
3. 接入官方行情，作为免费公开源之上的优先数据源，并进行并行对照后再提升。
4. 只读同步账户资产与持仓，映射到本地规范模型并做差异对账。
5. 在页面显示 QMT 连接、权限、行情质量、同步时间和降级原因。
6. 对真实 SDK 连接做回归、断线、重连、闭市、权限不足和数据冲突测试。

### C. 不能由工程代理代替用户或不能被技术保证

1. 联系财信证券申请 QMT/PTrade/ATX 等官方量化权限、签署协议、满足可能的资产或交易量门槛。
2. 安装后首次登录、短信/动态口令、风险揭示和其他身份认证。
3. 决定是否采纳建议并在官方客户端手工下单。
4. 等待真实交易日产生新的影子运行样本；时间不能靠模拟跳过。
5. 保证未来盈利、保证某个准确率或保证任何模型永远有效。

## 2. 依赖顺序与发布门

```text
基线与验收
   ├── 历史/PIT数据 ──> 真实回测 ──> 模型研究与治理 ──┐
   ├── 实时数据与缓存 ───────────────────────────────┤
   ├── 人工账户与导入 ───────────────────────────────┤
   └── 券商只读合同 ──> 授权后QMT适配 ───────────────┤
                                                        v
                                                指导引擎与中文界面
                                                        v
                                              影子观察与最终发布门
```

发布级别：

- **R0 研究版：** 可重复生成真实数据研究结果，但不给正式买卖动作。
- **R1 人工指导版：** 免费源、正式冠军模型、人工持仓和风险规则通过门槛；只供人工下单。
- **R2 券商只读版：** 获官方授权并完成 QMT 行情/资产/持仓只读接入；仍不自动下单。
- **R3 长期运行版：** 完成足够交易日的影子观察、恢复演练和审查，形成正式交接包。

## 3. 分阶段执行任务

### Task 1：重新冻结基线并建立生产验收矩阵

**Files:**
- Modify: `README.md`
- Modify: `reports/a_share_quant_handover_2026-08-11.md`
- Create: `config/production_readiness.yaml`
- Create: `tests/test_production_readiness.py`

- [ ] 写失败测试：验收配置必须列出数据、模型、建议、账户、备份、券商、UI 和禁止下单八类门槛。
- [ ] 运行 `\.venv\Scripts\python.exe -m pytest tests/test_production_readiness.py -q`，确认 RED。
- [ ] 实现只读验收清单加载器；每项输出 `PASS/FAIL/BLOCKED/NOT_OBSERVED` 和证据路径。
- [ ] 重新运行 `pytest -q`、`ruff check .`、`pip check`、`compileall`，把真实结果写入基线报告。
- [ ] 提交 `test: freeze production readiness baseline`。

**验收：** 基线不引用旧测试数字冒充当前结果；任何失败不会被汇总成“可投入使用”。

### Task 2：扩充免费历史数据和历史证券状态

**Files:**
- Modify: `src/a_share_quant/data/providers/baostock.py`
- Modify: `src/a_share_quant/data/pipeline.py`
- Modify: `src/a_share_quant/features/universe.py`
- Modify: `scripts/update_data.py`
- Modify: `scripts/update_benchmark.py`
- Create: `tests/test_historical_incremental_update.py`
- Extend: `tests/test_survivorship_bias.py`
- Extend: `tests/test_tradable_universe.py`

- [ ] 写失败测试：断点续传、重复下载幂等、最后完整交易日、退市/停牌/ST/上市不足过滤、基准不被当成股票。
- [ ] 先运行聚焦测试确认 RED。
- [ ] 以 BaoStock 为免费历史主源，按证券和日期增量更新；写临时文件、校验后原子替换。
- [ ] 将证券清单扩至明确、可复现的A股范围，并保存每个交易日的历史资格状态。
- [ ] 自动生成覆盖率、缺口、最新日期、重复键、OHLCV 不变量和来源摘要报告。
- [ ] 对失败证券有界重试并记录，不因少数失败覆盖已有好数据。
- [ ] 运行历史/PIT/存储回归并提交 `feat: harden free historical data updates`。

**验收：** 研究使用历史时点可见的证券池；不以当前成分回填历史，不混入未来上市或已不可交易状态。

### Task 3：数据源资格比较与可替换接口

**Files:**
- Modify: `src/a_share_quant/data/provenance.py`
- Modify: `src/a_share_quant/data/providers/registry.py`
- Modify: `config/data.yaml`
- Extend: `tests/test_data_provenance.py`
- Extend: `tests/test_provider_registry.py`
- Create: `scripts/compare_data_providers.py`

- [ ] 写失败测试：未经对照证据不能把新数据源升级为正式源。
- [ ] 比较 BaoStock、AKShare、已配置 Tushare 的共同证券/日期：价格、复权、成交量、停牌、时间戳、覆盖率。
- [ ] 保存脱敏比较结果和内容摘要；密钥只从环境读取，不写配置、日志或报告。
- [ ] 为每类数据独立选源，不用“数据源越多就越准”的简单投票。
- [ ] 新源只有在容差、覆盖和时点语义通过后才能进入正式链；否则只作观察或降级备援。
- [ ] 提交 `feat: add evidence-based provider qualification`。

**验收：** 多源用于交叉验证、补缺和故障切换，不把单位/复权/时间差异误当作更多有效样本。

### Task 4：建立公司行动和 PIT 基本面可用性门

**Files:**
- Modify: `src/a_share_quant/features/pit_store.py`
- Modify: `src/a_share_quant/data/provenance.py`
- Modify: `config/pit.yaml`
- Extend: `tests/test_announcement_date.py`
- Extend: `tests/test_pit_financial_data.py`
- Create: `tests/test_corporate_actions.py`

- [ ] 写失败测试：公告前不可见、修订不改写旧快照、送转/分红与复权一致、字段缺失时因子禁用。
- [ ] 自动评估免费源能否提供公告时间、报告期、修订和公司行动；形成字段级数据卡。
- [ ] 只启用通过 PIT 门的数据字段；免费数据不足时，估值/质量因子保持禁用并在中文界面解释。
- [ ] 保存原始值、公告日、有效日、抓取日和修订版本，不用报告期末日期冒充可知日期。
- [ ] 运行未来泄漏/PIT/复权回归并提交 `feat: enforce PIT fundamental availability`。

**验收：** 不因追求因子数量牺牲时点正确性；不能可靠获得的基本面不进入正式模型。

### Task 5：跨进程实时快照缓存与安全降级

**Files:**
- Create: `src/a_share_quant/data/realtime/cache.py`
- Modify: `src/a_share_quant/runtime/scheduler.py`
- Modify: `src/a_share_quant/workbench/service.py`
- Modify: `config/realtime.yaml`
- Create: `tests/test_realtime_cache.py`
- Extend: `tests/test_workbench_service.py`

- [ ] 写失败测试：重启恢复、损坏/截断拒绝、未来时间拒绝、文件大小/行数上限、失败回退。
- [ ] 实现同目录临时文件、fsync、摘要和原子替换；只缓存规范化后的有限字段。
- [ ] 启动时读取最后成功快照；缓存记录永久标为 `CACHED + STALE_DATA`，绝不使质量变为 `GOOD/READY`。
- [ ] 新鲜公开行情验证成功后更新缓存；源失败时保留缓存并显示真实年龄和失败原因分类。
- [ ] 运行全部 `test_realtime_*.py` 和工作台回归，提交 `feat: persist safe realtime snapshots`。

**验收：** 重启或闭市时栏目不必为空，但过期数据不会被呈现为当前可交易证据。

### Task 6：实时源、交易时段和运行监控硬化

**Files:**
- Modify: `src/a_share_quant/data/realtime/akshare.py`
- Modify: `src/a_share_quant/data/realtime/registry.py`
- Modify: `src/a_share_quant/data/realtime/validation.py`
- Modify: `src/a_share_quant/runtime/realtime_telemetry.py`
- Modify: `src/a_share_quant/runtime/scheduler.py`
- Extend: `tests/test_realtime_providers.py`
- Extend: `tests/test_realtime_scheduler.py`
- Extend: `tests/test_realtime_telemetry.py`

- [ ] 覆盖新浪→东财→腾讯的端点冷却、粘性、单位转换、局部异常隔离和有限重试测试。
- [ ] 区分盘前、上午、午休、下午、收盘确认、节假日和临时无数据；闭市不继续高频请求。
- [ ] 记录无载荷遥测：成功时间、交易所时间、延迟、错误计数、回退计数、隔离行数和数据年龄。
- [ ] 给出明确健康状态：`GOOD/DEGRADED/STALE/FAILED`，异常不得泄露 URL、代理、Token 或原始响应。
- [ ] 运行交易时段模拟和真实网络烟测，提交 `fix: harden realtime operations`。

**验收：** 公开源能用时稳定工作，不能用时快速、诚实降级，不形成重试风暴。

### Task 7：A股交易约束与成本真实回测

**Files:**
- Modify: `src/a_share_quant/backtest/contracts.py`
- Modify: `src/a_share_quant/backtest/reference.py`
- Modify: `src/a_share_quant/backtest/fast.py`
- Modify: `config/backtest.yaml`
- Extend: `tests/test_fair_evaluator.py`
- Create: `tests/test_a_share_execution_costs.py`
- Create: `tests/test_limit_and_suspension_execution.py`

- [ ] 写失败测试：T+1、100股、最低佣金、卖出印花税、过户费、滑点、涨跌停不可成交、停牌、容量限制。
- [ ] 建立统一费用和成交模型，使 reference 与 fast/vectorized 路径使用相同语义。
- [ ] 信号在收盘后生成，最早下一交易日执行；不得使用当天收盘价作可成交未来价格。
- [ ] 报告毛收益、成本、净收益、换手、未成交和容量影响。
- [ ] 对两套回测路径做小样本逐笔一致性测试并提交 `feat: model realistic A-share execution`。

**验收：** 模型排序不能靠忽略费用、涨跌停、停牌或未来成交价格获得虚假优势。

### Task 8：冠军/挑战者模型研究与统计门槛

**Files:**
- Modify: `src/a_share_quant/experiments/runner.py`
- Modify: `src/a_share_quant/experiments/evaluator.py`
- Modify: `src/a_share_quant/research/validation.py`
- Modify: `src/a_share_quant/promotion.py`
- Modify: `config/experiments.yaml`
- Modify: `config/qlib.yaml`
- Create: `scripts/run_production_research.py`
- Create: `tests/test_production_research_gate.py`

- [ ] 预注册数据区间、股票池、标签、主指标、成本、候选模型和超参预算，再运行实验。
- [ ] 永久保留可解释规则模型；训练 Qlib LightGBM 与 DoubleEnsemble 作为挑战者，不默认认定复杂模型更好。
- [ ] 使用固定样本外和多窗口 Walk-Forward；训练/验证/测试严格隔离，TEST 不参与调参。
- [ ] 计算 Rank IC/ICIR、分层单调性、5/10/20日校准、成本后收益、回撤、CVaR、换手、容量和分市场状态稳定性。
- [ ] 加入块自助法置信区间、Deflated Sharpe、PBO、参数/日期/成本/股票池扰动。
- [ ] 只有挑战者在预注册主指标胜过冠军、没有风险退化且全部数据门通过，才生成“可晋升请求”；不能自行晋升。
- [ ] 提交 `feat: add governed production model research`。

**验收：** 若没有模型通过，正式状态保持 `INSUFFICIENT_DATA/BLOCKED`；不得为了让页面出现“买入”而降低门槛。

### Task 9：受控模型“进化”与预测后验验证

**Files:**
- Modify: `src/a_share_quant/advisory/store.py`
- Modify: `src/a_share_quant/research/governance.py`
- Modify: `src/a_share_quant/research/validation.py`
- Extend: `tests/test_prediction_ledger.py`
- Extend: `tests/test_model_governance.py`
- Create: `tests/test_drift_and_rollback.py`

- [ ] 每次预测保存模型、数据截止日、特征版本、置信度、5/10/20日目标和当时可见证据摘要。
- [ ] 到期后自动追加真实结果、方向、相对收益、最大有利/不利波动；不修改原预测。
- [ ] 计算滚动校准、命中、Rank IC、收益、回撤和漂移；样本不足时明确 `NOT_OBSERVED`。
- [ ] 周期性离线训练挑战者；异常漂移触发降级或回滚到已冻结冠军。
- [ ] 晋升必须保留审批记录、模型卡、数据卡、验证包和可回滚别名。
- [ ] 提交 `feat: add controlled model evolution loop`。

**验收：** 系统会从新增数据中重新评估模型，但不会在线自我修改或用近期噪声自动替换正式模型。

### Task 10：正式中文指导引擎

**Files:**
- Modify: `src/a_share_quant/advisory/contracts.py`
- Modify: `src/a_share_quant/advisory/engine.py`
- Modify: `src/a_share_quant/advisory/risk.py`
- Modify: `src/a_share_quant/workbench/advisory_context.py`
- Modify: `src/a_share_quant/workbench/advisory_service.py`
- Extend: `tests/test_advisory_engine.py`
- Extend: `tests/test_end_to_end_advisory.py`

- [ ] 写情景测试：无持仓候选、已有持仓、T+1未解锁、超集中、流动性不足、止损、数据过期、模型失效。
- [ ] 组合冠军预测、市场状态、实时质量、持仓成本、现金、集中度和风险预算。
- [ ] 输出受约束动作：`买入候选/持有/观察/减仓/退出/暂不操作`，并附置信等级、有效期、理由、反例和失效条件。
- [ ] 建议数量满足100股和可用现金约束；没有账户信息时只给候选排序，不伪造仓位建议。
- [ ] 正式日选与盘中监控分离：盘中只改变风险/时效提示，不重写日线模型分数。
- [ ] 提交 `feat: compose evidence-backed Chinese guidance`。

**验收：** 每条动作可追溯到具体模型、数据截止、持仓和风控原因；不能解释则不行动。

### Task 11：四字段登记、CSV 导入和对账

**Files:**
- Modify: `src/a_share_quant/account/importer.py`
- Modify: `src/a_share_quant/account/service.py`
- Modify: `src/a_share_quant/workbench/advisory_service.py`
- Modify: `src/a_share_quant/workbench/app.py`
- Extend: `tests/test_account_importer.py`
- Extend: `tests/test_advisory_service.py`

- [ ] 保留名称、代码、数量、买入价四字段表单；其他字段由系统补充。
- [ ] 增加财信客户端人工导出 CSV 的白名单列映射、编码检测、预览和错误行提示。
- [ ] 预览不落盘，确认令牌一次性；文件摘要和成交标识防重复导入。
- [ ] 本地账本与导入持仓出现差异时生成对账报告，不静默覆盖。
- [ ] Token、密码、身份证和完整原始文件不进入日志或 Git。
- [ ] 提交 `feat: add simple broker-file reconciliation`。

**验收：** 不获得官方 API 也能用简单人工方式维持账户状态；用户不需要理解会计或量化字段。

### Task 12：全站简体中文和非空安全展示

**Files:**
- Modify: `src/a_share_quant/workbench/app.py`
- Modify: `src/a_share_quant/workbench/service.py`
- Modify: `src/a_share_quant/workbench/advisory_service.py`
- Extend: `tests/test_workbench_app.py`
- Extend: `tests/test_workbench_service.py`

- [ ] 建立中英文内部枚举到简体中文显示字典，覆盖来源、质量、模型、建议、错误、按钮和表头。
- [ ] 删除用户可见英文残留；股票代码、模型版本和 API 技术字段除外。
- [ ] 官方日选从持久化正式信号独立加载，不能因实时源失败而消失。
- [ ] 盘中无新鲜行情时展示最近安全缓存、时间和“已过期”，不能显示空白或虚假实时值。
- [ ] 所有空态给出中文原因和下一步，不显示底层异常类名。
- [ ] 以 HTTP、DOM 文本和浏览器截图做桌面分辨率回归，提交 `fix: finish Simplified Chinese workbench`。

**验收：** 主工作台与人工投顾无功能性英文残留；两大栏目有真实数据或明确、安全的中文空态。

### Task 13：Windows 长期运行与恢复

**Files:**
- Modify: `scripts/start_quant_workbench.ps1`
- Modify: `scripts/stop_quant_workbench.ps1`
- Create: `scripts/run_daily_operations.ps1`
- Create: `scripts/install_scheduled_tasks.ps1`
- Create: `scripts/uninstall_scheduled_tasks.ps1`
- Extend: `tests/test_launcher_security.py`
- Create: `tests/test_daily_operations.py`

- [ ] 编排收盘后：历史增量更新→质量检查→成熟预测→日选→备份→中文日报。
- [ ] 编排开盘时：启动工作台→数据源健康→缓存回退→只读状态检查。
- [ ] 任务有互斥锁、超时、退出码、有限重试和失败报告，不并发覆盖数据。
- [ ] 日志按日期/大小轮换且脱敏；备份执行恢复演练和摘要校验。
- [ ] 生成任务计划程序安装脚本；安装动作在执行阶段明确记录任务名、账户范围和触发时间。
- [ ] 提交 `feat: automate safe Windows operations`。

**验收：** 断网、重启、重复启动、进程崩溃和部分数据失败均有可解释恢复路径；不会自动触发交易。

### Task 14：券商中立只读边界（无需授权即可完成）

**Files:**
- Create: `src/a_share_quant/integrations/broker_read_only.py`
- Modify: `src/a_share_quant/integrations/qmt/read_only.py`
- Modify: `src/a_share_quant/workbench/service.py`
- Create: `tests/test_broker_read_only_contract.py`
- Extend: `tests/test_qmt_read_only.py`

- [ ] 定义只读能力：行情、资产、持仓、连接健康；合同中不存在报单和撤单方法。
- [ ] 用模拟客户端测试代码映射、金额精度、空账户、断线、权限不足、重复持仓和时间戳。
- [ ] SDK 缺失、路径不可信、非本地安装、权限不足时失败关闭并继续使用人工账本。
- [ ] 永久测试 `submit_order/order_stock/cancel_order` 等调用不可达或明确拒绝。
- [ ] 提交 `feat: add broker-neutral read-only boundary`。

**验收：** 在用户尚未开通 QMT 时，可完成的工程部分全部就绪，但页面诚实显示“未授权/未连接”。

### Task 15：财信 QMT 官方只读接入（等待用户授权）

**人工前置条件：** 用户联系 95317/客户经理开通迅投 QMT 与 XtQuant Python API；从财信官网下载并安装 QMT；首次登录 MiniQMT；确认授权包含所需行情、资产和持仓查询。

**Files:**
- Create: `src/a_share_quant/integrations/qmt/official_client.py`
- Create: `src/a_share_quant/data/realtime/qmt.py`
- Modify: `src/a_share_quant/data/realtime/registry.py`
- Modify: `src/a_share_quant/workbench/service.py`
- Create: `tests/test_qmt_official_client.py`
- Extend: `tests/test_qmt_read_only.py`

- [ ] 自动发现并校验本地 `xtquant` 与 `userdata_mini`，不把任意路径加入 `sys.path`。
- [ ] 先做元数据/权限健康检查，再建立只读会话。
- [ ] 适配官方行情到规范快照，和公开源并行比较通过后才能提升优先级。
- [ ] 仅调用资产与持仓查询并做本地对账；不导入任何报单函数。
- [ ] 覆盖 QMT 未启动、断线、会话冲突、闭市、权限拒绝、旧时间戳和部分数据。
- [ ] 真实机器烟测成功后提交 `feat: connect authorized QMT read-only data`。

**验收：** R2 页面显示真实 QMT 来源和同步时间；QMT 失败自动回到公开行情+人工账本，绝不转为自动交易。

### Task 16：影子观察、最终审查与交接发布

**Files:**
- Modify: `config/production_readiness.yaml`
- Create: `scripts/run_release_acceptance.py`
- Create: `reports/production_acceptance.md`
- Create: `reports/model_card_champion.md`
- Create: `reports/data_card.md`
- Create: `reports/operations_runbook.md`
- Create: `reports/final_handover.md`
- Extend: `tests/test_production_readiness.py`

- [ ] 在真实交易日持续记录候选、盘中状态、建议、结果和源差异；不把同日模拟当长期证据。
- [ ] 至少覆盖正常开盘、午休、收盘、休市、断网、重启、公开源失败和（若授权）QMT 断线。
- [ ] 运行全量 pytest、Ruff、pip check、compileall、diff check、覆盖率、备份恢复、性能和安全检查。
- [ ] 用更高推理强度模型做独立代码/经济学/数据泄漏/安全审查；发现问题先修复、再重跑全部门槛。
- [ ] 生成中文交接：架构、数据源、模型证据、风险限制、操作步骤、故障恢复、外部授权、残余风险和后续扩展接口。
- [ ] 只有验收矩阵允许时发布 R1/R2/R3；未观察到的交易日场景必须保留 `NOT_OBSERVED`。
- [ ] 提交 `docs: deliver production acceptance and handover`，创建可回滚版本标签。

**验收：** 报告中的每个“通过”都有命令输出或不可变证据；任何未完成项均明示，不使用“无 BUG”“保证准确”等无法证明的表述。

## 4. 模型晋升的最低判定规则

模型只有同时满足以下条件才可进入正式指导：

1. 无未来数据泄漏、幸存者偏差、TEST 调参或数据模式混用。
2. 至少覆盖多个年份和不同市场状态；若免费数据跨度不足，则保持研究状态。
3. 成本后结果在多数走步窗口优于预注册基线，且优势不依赖单一年份、行业或少数股票。
4. Rank IC/分层关系、概率校准和风险指标有稳定证据；置信区间不足时允许“无法区分”，不强选冠军。
5. 加大成本、移动切分日期、调整股票池和参数后，不出现结构性崩溃。
6. 回撤、尾部风险、换手和容量符合配置的风险预算。
7. 挑战者在影子期没有数据漂移、运行故障或安全回归。
8. 晋升记录可审计、可回滚；自动下单状态始终不存在。

## 5. 用户唯一需要提前准备的事项

目前不需要用户提供更多免费数据账号即可继续 R1。为 R2 券商只读版，用户只需完成以下外部动作：

1. 联系财信证券，申请“迅投 QMT 量化服务和 XtQuant Python API 权限”，明确只要行情、资产、持仓查询，不要程序化报单。
2. 询问费用、资产/交易量门槛、Level-1/历史行情范围和是否允许外部 Python。
3. 获批后安装财信官方 QMT，完成首次登录，并告知安装目录或让工程代理自动探测。

其他数据下载、代码、测试、模型实验、页面、报告、脚本和本地配置均由工程代理完成。已有 Token 只从本机环境读取；若验证发现失效或权限不足，才要求用户重新登录或申请权限。

## 6. 实施期间的汇报规则

- 每个 Task 必须先出现失败测试，再实现，再运行聚焦回归和相关全量回归。
- 每次汇报区分“代码完成”“测试通过”“真实交易日已观察”“外部授权完成”，不得混为一谈。
- 发现用户需求违反金融逻辑、数据时点、统计验证、券商规则或安全边界时，必须明确指出并采用更可靠替代方案。
- 发生需要登录、短信、协议确认、券商审批或付费选择时才暂停并通知用户；其他事项按计划自主推进。
