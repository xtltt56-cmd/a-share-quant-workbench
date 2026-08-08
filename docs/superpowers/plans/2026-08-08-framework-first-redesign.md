# Framework-first A 股量化系统实施计划

> 本计划依据 `docs/OPEN_SOURCE_EVALUATION.md` 和 `docs/superpowers/specs/2026-08-08-framework-first-redesign-design.md` 执行。每个阶段都必须：先写测试/验收条件，运行测试，修复问题，更新 README/进度，然后提交一个独立 Git commit。

## Stage 0：开源评估与架构冻结（本次已执行）

- [x] 检查旧仓库和未提交的从零构建草稿。
- [x] 评估 RiceQuant Skills、Qlib、RQAlpha、AKShare、DuckDB、VectorBT、Optuna、QuantStats、PyPortfolioOpt、vn.py 的 API、Python/Windows、测试、活跃度和许可证。
- [x] 固化核心/可选/未来 Adapter 决策。
- [x] 写入 `OPEN_SOURCE_EVALUATION.md`、`DEPENDENCIES.md`、设计文档和本计划。

## Stage 1：仓库骨架和最小数据路径（已完成）

### 1. 骨架

- [x] 用 framework-first README 替换旧路线说明。
- [x] 重置 `pyproject.toml` 为核心依赖、研究依赖、可选引擎和开发依赖分组。
- [x] 建立 `contracts/`、`providers/`、`storage/`、`features/`、`backtest/`、`risk/`、`paper/` 和 `observability/` 目录；通用回测/指标仍由后续框架接入。
- [x] 清理旧的自研 normalizer/pipeline/store 测试，改成围绕 Adapter 契约的测试。

### 2. DataProvider 与 canonical schema

- [x] 先写离线契约测试：字段、类型、日期、股票代码、重复键、OHLC 范围、源版本和错误分类。
- [x] 实现 `DataProvider` Protocol、`AKShareDataProvider` lazy import 和 Tushare placeholder。
- [x] 加入 bounded retry/backoff、rate limit、增量本地缓存和外部异常脱敏。
- [x] 只把显式 `--network-smoke` 运行视为 AKShare 网络检查；网络失败不影响离线 CI。

### 3. DuckDB + Parquet

- [x] 先写增量/去重/manifest 测试。
- [x] 实现按 dataset/date/symbol 语义的 Parquet 写入、DuckDB manifest、schema version 和质量状态。
- [x] 确保重复更新幂等，失败写入不覆盖旧分区，临时文件能清理。
- [x] 保存股票池快照与数据版本，为后续 survivorship/PIT 测试提供输入。

### 4. Stage 1 验收

- [x] 无 Token、无外网时核心依赖可安装，离线测试全部通过。
- [x] fake provider 能完成股票列表和日线数据写入，第二次运行只请求缺失日期。
- [x] DuckDB 查询和 Parquet 结果在重复运行后稳定一致。
- [x] 安全扫描确认 `.env`、数据库、Parquet、日志、报告和模型不被 Git 跟踪。
- [x] 运行 `security-threat-model`，输出仓库根目录 `a-share-quant-threat-model.md`。
- [x] 更新 README 和进度；待本阶段最终验证后提交 `feat: redesign around open-source quant frameworks`。

## Stage 2：Qlib 数据桥与三个基准

- [ ] 将 canonical PIT 数据导出为 Qlib DataHandler 可读格式。
- [ ] 先运行官方 Alpha158 + LightGBM baseline unchanged，再替换为 A 股数据。
- [ ] 实现 `QlibLightGBMAlpha158` 和 `QlibDoubleEnsembleAlpha158` registry。
- [ ] 规则多因子策略保留为同一数据/成本/股票池下的基准。
- [ ] 对三个 baseline 运行相同 rolling walk-forward，输出 OOS 结果和数据/模型版本。

## Stage 3：VectorBT 研究加速与 Optuna

- [ ] VectorBT 作为 optional profile，仅用于快速参数/组合扫描。
- [ ] Optuna objective 使用 OOS Sharpe、回撤、换手和参数不稳定性惩罚。
- [ ] 任何优化不得读取最终 test 窗口；记录 study、代码版本、数据版本和随机种子。
- [ ] 将快速研究候选转换为统一 signal，再送入事件验证。

## Stage 4：RQAlpha 事件验证与交易约束

- [ ] 以 Adapter/Mod 配置接入 RQAlpha，不修改其 core。
- [ ] 验证 T+1、涨跌停、停牌、手续费、印花税、滑点和无法成交事件。
- [ ] 对比 VectorBT 和 RQAlpha 的收益/成交/持仓差异；差异必须可解释。
- [ ] RQAlpha 仅在个人研究 profile 启用；保留未来替代引擎出口。

## Stage 5：组合、风控、报告和纸面交易

- [ ] 实现 YAML 驱动的仓位上限、最大持仓数、总仓位、固定止损、ATR/趋势止损和最大组合回撤。
- [ ] 通过 QuantStats 和自定义交易级指标生成每日候选 Top20、Top5 解释及回测报告。
- [ ] 纸面交易记录每次模拟成交、仓位、盈亏、回撤、信号/模型/特征/数据版本。
- [ ] 只允许通过 OOS + 风控 + 人工批准门禁进入纸面交易；永远不进入实盘。

## Stage 6：后续扩展

- [ ] RQData/RiceQuant Skills adapter 和 Windows wrapper。
- [ ] PyPortfolioOpt 的 HRP/最小方差/CVaR 对照。
- [ ] vn.py `ExecutionProvider` 只做技术预研；真实券商接口需要独立安全评审和用户明确授权。
