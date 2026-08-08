# A-Share Quant Framework-first

## Stage 2 progress (2026-08-08)

- [x] PIT feature store with separate trade date, report period, announcement date, effective date, and ingest time.
- [x] Default financial-data visibility is the next trading day; missing announcement dates are rejected.
- [x] Historical as-of universe filtering for listing age, delisting, ST, suspension, and historical liquidity.
- [x] Qlib 0.9.7 provider adapter and official Alpha158 dataset builder with `forward_excess_return_5d` labels.
- [x] Qlib LightGBM and Qlib DoubleEnsemble official model adapters with deterministic versioned predictions.
- [ ] Rule-based factor baseline, fair OOS comparison, and paper signals.

The Qlib adapter writes a project-owned local provider under the experiment/data path and keeps Qlib-specific imports inside `src/a_share_quant/integrations/qlib/`. It uses `kernels=1` by default on Windows to keep dataset construction deterministic and avoid uncontrolled worker spawning.

研究优先、默认不下单的 A 股量化研究与纸面交易系统。项目的核心原则是复用成熟开源框架，只实现 A 股数据适配、PIT 数据边界、股票池、因子/模型注册、风险约束、统一信号和纸面交易记录。

```text
AKShare -> DataProvider -> DuckDB + Parquet -> PIT Feature Store / Qlib
                                      -> Rule factors / LightGBM / DoubleEnsemble
                                      -> Unified Signal -> VectorBT quick research
                                      -> RQAlpha event validation (optional)
                                      -> Walk-forward/OOS -> Risk -> Paper trading
```

V1 固定目标：A 股日频数据 → 数据清洗与本地存储 → 股票池过滤 → 多因子评分 → 每日候选股 → 回测 → 风控 → 每日报告 → 纸面交易监控。暂不接入任何真实券商下单接口、账号、交易网关或自动实盘。

## 当前状态

- [x] Git 仓库和项目目录初始化
- [x] 开源框架、Skill、Python/Windows 和许可证评估
- [x] Framework-first 架构、依赖清单和分阶段计划
- [x] AKShare → DataProvider → DuckDB/Parquet 第一阶段数据路径
- [ ] Qlib Alpha158/LightGBM/DoubleEnsemble 三个统一 baseline
- [ ] VectorBT 快速研究与 RQAlpha 事件验证适配
- [ ] Walk-Forward、风控、QuantStats 报告和纸面交易

正式选型见 [docs/OPEN_SOURCE_EVALUATION.md](docs/OPEN_SOURCE_EVALUATION.md)，依赖和许可证见 [DEPENDENCIES.md](DEPENDENCIES.md)，设计见 [framework-first redesign](docs/superpowers/specs/2026-08-08-framework-first-redesign-design.md)，实施步骤见 [implementation plan](docs/superpowers/plans/2026-08-08-framework-first-redesign.md)。

第一阶段实测：AKShare 股票池快照写入 5,539 行；日线通过主端点失败后的 Tencent fallback 写入 `000001` 的 4 行（2026-08-04 至 2026-08-07），DuckDB manifest 为 `valid`；重复运行同一区间为 `updated=0, skipped=1`。本地数据不提交 Git。

## 运行环境

- Windows 10/11
- Python 3.12（项目声明 `>=3.10`；当前开发和 CI 标准为 3.12）
- Git

## 安装

```powershell
Set-Location 'D:\量化交易'
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

可选研究/引擎依赖按 profile 安装：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[research]"       # Qlib/Optuna/QuantStats
.\.venv\Scripts\python.exe -m pip install -e ".[fast-research]"  # VectorBT
.\.venv\Scripts\python.exe -m pip install -e ".[event-backtest]" # RQAlpha，个人研究
.\.venv\Scripts\python.exe -m pip install -e ".[portfolio]"      # PyPortfolioOpt
.\.venv\Scripts\python.exe -m pip install -e ".[data-extra]"     # Tushare adapter
```

`.env` 只保存本机配置和可选 Token，绝不提交 Git。AKShare 默认不需要 Token；Tushare/RQData 必须显式启用。密钥不会写入源码、测试夹具、日志、模型 artifact 或报告。

## 第一阶段命令

离线测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
```

使用 fake provider 验证本地写入不需要网络。需要真实 AKShare 网络检查时，显式运行：

```powershell
.\.venv\Scripts\python.exe scripts/update_data.py --provider akshare --limit 1 --start-date 2026-01-01 --end-date 2026-01-05 --network-smoke
```

网络失败、接口限流或字段变化都只应被记录为数据源失败，不得被当作交易信号或回测成功。

## 目录

```text
config/                    YAML 配置，因子权重和成本不写死在代码
data/                      本地 Parquet/DuckDB 状态，默认被 Git 忽略
docs/                      选型、设计、计划和威胁模型
scripts/                   操作入口，不承载策略逻辑
src/a_share_quant/
  contracts/               DataProvider、Signal、Backtest、Execution 契约
  data/providers/          AKShare/Tushare/RQData 适配器
  storage/                 DuckDB + Parquet Data Lake
  features/                未来规则因子和 Qlib bridge
  backtest/                VectorBT/RQAlpha adapters
  risk/                     未来组合风险约束
  paper/                    未来纸面交易
  observability/            日志、版本和质量记录
tests/                     离线契约、数据质量和回归测试
reports/                   生成报告，默认被 Git 忽略
```

## 数据与研究安全边界

- 所有外部数据都先通过字段、类型、日期、范围、重复键和质量校验。
- 财务/估值数据必须记录 `announced_at`，特征只能读取信号时点已经公布的版本。
- 股票池按历史 `as_of` 快照保存，禁止当前完整股票列表回填历史，防止幸存者偏差。
- Qlib 使用本项目的 A 股 PIT 数据桥，不把官方示例数据当作生产数据。
- VectorBT 只用于快速研究；最终交易约束需要独立事件验证。RQAlpha 在个人研究 profile 中可选，受其非商业许可证限制。
- 生命周期严格为 `Research → Backtest → Robustness → WalkForward → PaperTrading → Approved`，不存在 `Research → Live` 路径。
- V1 没有真实执行层。未来只能通过拒绝真实下单的 `NoopExecutionProvider` 和独立人工批准边界扩展。

## 开源复用边界

不复制 Qlib、RQAlpha、VectorBT、QuantStats、vn.py 或 RiceQuant Skills 源码；只使用公开 API、配置、DataHandler、Mod 和 Adapter。许可证/用途/可替换出口维护在 [DEPENDENCIES.md](DEPENDENCIES.md) 和 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

RiceQuant 的 `idea-generation` 和 `report-renderer` Skill 已安装到本机用户 Skill 目录；RQData/Bash 依赖的适配边界见 [docs/RICEQUANT_SKILLS_ADAPTER.md](docs/RICEQUANT_SKILLS_ADAPTER.md)。
