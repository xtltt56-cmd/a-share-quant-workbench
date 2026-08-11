# A 股量化系统开源框架评估

更新时间：2026-08-08

本文件记录框架选择、许可证边界、Windows/Python 兼容性和项目中的使用方式。评估以各项目公开 GitHub 仓库、README、`pyproject.toml`、许可证、测试目录和变更记录为依据。GitHub 的 star、release、commit 等数字只是本次评估时的活跃度快照，不作为稳定性承诺。

## 结论

第一版采用“开源框架负责通用能力，本项目只负责 A 股适配和策略边界”的路线：

```text
AKShare / future Tushare or RQData
              -> DataProvider contract
              -> DuckDB + Parquet local lake
              -> PIT Feature Store / Qlib DataHandler
              -> Rule factors or Qlib models
              -> Unified Signal Schema
              -> VectorBT quick research (optional)
              -> RQAlpha event validation (optional, personal research)
              -> Walk-Forward / OOS gate
              -> Portfolio & Risk
              -> Paper Trading only
```

必选路径不依赖付费数据、RQData CLI、券商接口、RQAlpha 或 VectorBT。这样即使可选组件安装失败，AKShare 免费数据、Qlib 研究和本地数据湖仍然可以运行。

## 组件决策表

| 项目 | 调研结论（截至 2026-08-08） | 在本项目中的角色 | Python/Windows | 许可证与风险 | 决策 |
|---|---|---|---|---|---|
| [ricequant/ricequant-skills](https://github.com/ricequant/ricequant-skills) | Codex/Claude 风格的 Markdown Skill；包含 `idea-generation`、`morning-note`、`earnings-analysis`、`report-renderer`、`ricequant`、`rqdata-python` 等。研究 Skill 以 `rqdata` CLI 为量化筛选数据源，并使用 Bash、`python3` 和 RQData 配置。 | 复用报告、研究笔记和催化剂日历的工作流结构；由 Windows wrapper 把数据读取替换为本项目 DataProvider。 | Skill 本身不是 Python 包；显式要求 Bash/RQData CLI，Windows 需适配。 | 仓库内容可直接参考，但 RQData 是可选/可能需要许可证的外部服务；不能让它成为免费 V1 的硬依赖。 | **Optional / Adapt** |
| [microsoft/qlib](https://github.com/microsoft/qlib) | AI-oriented quant research platform，覆盖数据、特征、模型、回测、风险、组合和工作流；提供 Alpha158/Alpha360、LightGBM、XGBoost、CatBoost、DoubleEnsemble 等研究组件。当前仓库有文档、示例、benchmark 和测试目录，最新可见 release 为 `v0.9.7`。 | 研究和机器学习核心：自定义 A 股 PIT 数据适配、Alpha158/Alpha360、LightGBM、DoubleEnsemble、Dataset、Feature Handler、Signal 和 Workflow。 | `requires-python >=3.8`，仓库含 Windows classifier，支持到 Python 3.12；Windows 仍需单独验证 native dependency。 | MIT；可纳入必选研究路径。官方 Yahoo CN 初始化数据仅作示例，本项目必须使用自己的 A 股数据质量和 PIT 校验。 | **Core** |
| [ricequant/rqalpha](https://github.com/ricequant/rqalpha) | 完整事件驱动回测/模拟框架；提供 `sys_accounts`、`sys_analyser`、`sys_risk`、`sys_scheduler`、`sys_simulation`、`sys_transaction_cost` 等 Mod 扩展点。仓库有测试目录；变更记录覆盖 Python 3.8、股息税、涨跌停和 pytest。 | 以 `EventBacktestProvider` 适配器接入，用于最终成交约束验证和纸面账户模拟；不修改其核心源码。 | 变更记录显示最低 Python 3.8；Windows 需本地验证。 | README 明确“仅限非商业使用”。如果未来需要商业分发，不能把它锁为唯一引擎；保留替换点。 | **Optional / Personal research** |
| [akfamily/akshare](https://github.com/akfamily/akshare) | Python 财经数据接口集合，提供 A 股股票列表、日线、指数、行业、基本面、估值、资金流、龙虎榜、交易日历等接口；仓库持续发布，接口有外部 endpoint 依赖。 | V1 默认免费行情/基本面 DataProvider；本项目负责重试、限流、缓存、schema、日期、缺失和异常校验。 | Python `>=3.9`；纯 Python/HTTP 适配，Windows 可用，但 endpoint 失败必须可降级。 | MIT；README 警示接口可能移除，数据只适合研究/参考；必须保留 `source`、`fetched_at` 和质量标记。 | **Core data adapter** |
| [duckdb/duckdb](https://duckdb.org/) / [duckdb-python](https://github.com/duckdb/duckdb-python) | 进程内数据库；Python API 可直接查询/写入 Parquet、CSV、JSON，适合本地研究数据湖。 | 元数据、manifest、质量检查、跨 Parquet 查询和 PIT 数据索引。 | Windows/Python wheel 路径成熟；先在 Python 3.12 验证。 | MIT；本地文件权限和并发写入仍由本项目负责。 | **Core storage** |
| [polakowo/vectorbt](https://github.com/polakowo/vectorbt) | 向量化回测、参数扫描、walk-forward、组合分析，适合快速研究。 | 快速参数和鲁棒性研究；候选方案必须再经过事件约束引擎和样本外门禁。 | Python API；Windows 先按 optional extra 验证。 | Apache 2.0 + Commons Clause；禁止销售价值主要来自该软件的产品/服务。不能成为未来商业路径的硬依赖。 | **Optional research accelerator** |
| [optuna/optuna](https://github.com/optuna/optuna) | 超参数优化、并行试验、可视化和 pruning；仓库有测试目录，MIT，支持 Python `>=3.9`。 | 统一优化器；目标函数固定为 OOS Sharpe、最大回撤、换手率、参数不稳定性的组合惩罚，禁止全历史调参。 | Python 3.12 可用；锁定稳定 4.x，不跟随 v5 RC。 | MIT；存储 study 时不得把 Token 或原始敏感数据写入 artifact。 | **Core optimizer** |
| [ranaroussi/quantstats](https://github.com/ranaroussi/quantstats) | 提供 stats、plots、reports、HTML tear sheet 和 Monte Carlo；README 要求 Python `>=3.10`。 | 周期收益、回撤、Sharpe、Sortino、Calmar、基准对比和报告渲染；交易级胜率、盈亏比、Profit Factor 等由本项目补充。 | Python 3.12 可用；图表生成需在 CI/本地分别验证。 | Apache-2.0；报告中保留策略版本、数据版本和引擎版本。 | **Core reporting** |
| [PyPortfolio/PyPortfolioOpt](https://github.com/PyPortfolio/PyPortfolioOpt) | 支持均值-方差、Black-Litterman、收缩、HRP 等组合优化，MIT，有 tests/examples。 | V1 先用简单仓位上限；V1.1 作为 HRP、最小方差、CVaR 和收缩估计的对照实现。 | Python API；optional，避免首版组合层过度复杂。 | MIT；输入必须是 PIT 允许的收益/协方差数据。 | **Optional V1.1** |
| [vnpy/vnpy](https://github.com/vnpy/vnpy) | Python 交易框架，MIT，执行和网关生态成熟；`vnpy.alpha` 还包含数据集、Alpha158、LightGBM 和研究/策略组件。 | 只定义未来 `ExecutionProvider` 和 Adapter 边界；V1 不安装网关、不连接券商、不启动交易服务。 | Windows 安装脚本和国内网关较多，但执行层复杂度高。 | MIT；真实资金操作有独立授权、审计和熔断要求。 | **Reference / Future adapter** |

## 正式技术栈

### 必选免费路径

- 运行时：Python 3.12，项目最小声明暂定 `>=3.10`，但 CI/开发标准固定 3.12；在 Qlib、AKShare、QuantStats 共同支持范围内运行。
- 数据接入：AKShare `AKShareDataProvider`；Tushare 只提供需要 Token 的可替换适配器，不参与免费默认路径。
- 本地数据湖：Parquet 分区文件 + DuckDB manifest/查询层，全部通过 `MarketDataStore` 访问。
- 研究/ML：Qlib；先实现自定义 A 股数据 Handler，再运行官方 unchanged Alpha158 + LightGBM baseline 和 DoubleEnsemble baseline。
- 优化：Optuna 稳定 4.x；所有 study 绑定数据版本、策略版本、模型版本和 OOS 区间。
- 报告：QuantStats + 自定义交易级指标计算。
- 工具：pandas、NumPy、PyArrow、PyYAML、Pydantic Settings、pytest、Ruff。

### 可选路径

- VectorBT：快速向量化研究、参数扫描和 walk-forward；不是最终交易约束的唯一来源。
- RQAlpha：事件驱动最终验证和纸面撮合；受非商业许可证限制，只在个人研究 profile 启用。
- PyPortfolioOpt：V1.1 组合权重对照。
- RQData / `ricequant-skills`：需要账号、许可证或 CLI 配置时才启用；先通过适配器复用 Skill 的报告工作流。
- vn.py：未来 ExecutionProvider 的参考实现，不进入 V1 依赖。

## 三个基准与统一实验条件

V1 至少比较以下三个 baseline，并使用完全相同的股票池、交易成本、停牌/涨跌停规则、再平衡频率、基准和 walk-forward 切分：

1. `RuleBasedMultiFactor`：本项目规则因子与 YAML 权重。
2. `QlibLightGBMAlpha158`：Qlib Alpha158 + LightGBM。
3. `QlibDoubleEnsembleAlpha158`：Qlib Alpha158 + DoubleEnsemble。

`Ensemble` 只能在基准结果、样本外稳定性和风险门禁均通过后生成。任何一个历史收益最高的模型都不能单独决定正式策略。

## 复用原则

1. 不复制 Qlib、RQAlpha、VectorBT、QuantStats 或 Skill 源码到 `src/`。
2. 只通过公开 API、配置、插件/Mod、DataHandler 和 Adapter 接入。
3. 本项目只实现 A 股数据字段映射、PIT 版本、股票池规则、因子、风险约束、统一信号和纸面交易记录。
4. 每个第三方依赖都记录版本约束、许可证、启用 profile 和可替换出口。
5. 需要商业化时，先重新审查 RQAlpha 和 VectorBT 的许可证，必要时替换对应 Adapter，不改动策略层契约。

## MT5 架构借鉴与账户导入边界

本项目只借鉴 MetaTrader 5 官方 Python 集成的架构思想：由一个明确的适配器边界提供终端或文件数据，连接状态、时间戳、质量和失败原因可审计；不复制 MetaQuotes 的代码、IPC 协议或私有实现。财信普通客户端没有本项目可使用的官方实时账户 API 时，采用官方导出文件收件箱、摘要复核、用户确认和独立持仓快照，禁止抓包、内存读取、OCR、模拟点击和外挂。

## 已知限制与应对

| 限制 | 应对 |
|---|---|
| AKShare endpoint 可能变化、限流或返回字段漂移 | schema 白名单、请求重试/退避、单 endpoint 缓存、质量报告、失败不覆盖旧数据；保留 Tushare/RQData adapter |
| Qlib 官方示例数据不是 A 股生产数据 | 只复用 Qlib 的 Handler/Model/Workflow；自己建立 A 股 PIT 数据集和质量门禁 |
| RQData Skill 依赖 CLI/Bash 和账号 | Skill 只作为可选研究工作流；Windows wrapper 统一调用本地 DataProvider |
| RQAlpha 非商业限制 | 个人研究 profile 下可选；EventBacktestProvider 必须可替换 |
| VectorBT Commons Clause | 不放入必选免费/商业路径；快速研究结果必须由独立事件引擎复核 |
| 财务数据公告时点缺失 | 存储 `announced_at`，特征只能读取 `announced_at <= as_of` 的版本；无法证明时点的数据不进入 OOS |
| 当前仓库已有旧的自研基础层草稿 | 本次重构删除旧契约和测试，保留本文件及新设计作为唯一实施依据 |
