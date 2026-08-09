# 依赖与许可证清单

更新时间：2026-08-08

本清单是项目的依赖入口。版本采用“稳定大版本上限 + 定期锁定”的策略：`pyproject.toml` 声明兼容范围，环境锁定文件和 CI 在安装时记录实际版本。禁止直接追踪 `main`、release candidate 或未审查的 Git URL。

## 运行时与必选依赖

| 依赖 | 包/来源 | 版本策略 | 许可证 | 用途 | Windows/验证备注 |
|---|---|---|---|---|---|
| Python | [python.org](https://www.python.org/) | 开发/CI 固定 3.12；项目声明 `>=3.10` | PSF | 运行时 | 首版以 Windows 3.12 wheel 为准，暂不承诺 3.13 |
| pandas | `pandas` | `>=2.2,<3` | BSD-3-Clause | 表格契约、时间序列 | 离线单元测试必需 |
| NumPy | `numpy` | 与 pandas/Qlib 兼容 | BSD-3-Clause | 数值计算 | 由核心/研究依赖共同约束 |
| PyArrow | `pyarrow` | `>=16,<24` | Apache-2.0 | Parquet I/O | Windows wheel；写入前做 schema 检查 |
| DuckDB | `duckdb` | `>=1.0,<2` | MIT | 本地查询、manifest、质量索引 | 只允许项目内数据目录；不暴露网络服务 |
| AKShare | `akshare` | `>=1.18,<2` | MIT | 免费 A 股数据适配器 | endpoint 不稳定；通过 lazy import 和 adapter 处理 |
| Qlib | `pyqlib` | 锁定稳定版本；首版验证 `0.9.x` | MIT | Alpha158/Alpha360、模型、Workflow、研究回测 | Python 3.12/Windows 需要单独 smoke test；不使用官方 Yahoo 数据作为生产数据 |
| Optuna | `optuna` | 稳定 4.x；不锁 v5 RC | MIT | OOS 参数优化和 pruning | study 存储不含密钥/原始敏感数据 |
| QuantStats | `quantstats` | `>=0.0.8,<1` | Apache-2.0 | 指标、基准对比、tear sheet | 报告输出到被忽略目录 |
| PyYAML | `PyYAML` | `>=6,<7` | MIT | 配置读取 | 只读取项目配置，禁止从数据文件执行 YAML |
| pydantic-settings | `pydantic-settings` | `>=2.4,<3` | MIT | 环境变量配置 | Secret 类型只在内存中使用 |
| python-dotenv | `python-dotenv` | `>=1,<2` | BSD-3-Clause | `.env` 本地加载 | `.env` 必须被 Git 忽略 |

## 开发与测试依赖

| 依赖 | 版本策略 | 许可证 | 用途 |
|---|---|---|---|
| pytest | `>=8,<9` | MIT | 单元、契约、回归测试 |
| pytest-cov | `>=5,<8` | MIT | 覆盖率 |
| Ruff | `>=0.6,<1` | MIT | lint/format 校验 |

## 可选依赖和服务

| 依赖/服务 | 作用 | 许可证/商业边界 | 启用条件 |
|---|---|---|---|
| Tushare | 付费/Token 数据源备用适配器 | 以其服务条款和账号权限为准；Token 只能来自 `.env` 或安全存储 | `provider=tushare` 且显式配置 Token |
| RQData / `rqdata` CLI | RiceQuant 高质量数据和 RiceQuant Skills 的数据入口 | 账号/许可证与服务条款约束；不作为免费 V1 依赖 | 用户显式配置并通过 provider adapter 启用 |
| [RQAlpha](https://github.com/ricequant/rqalpha) | 事件驱动回测/模拟 | 项目 README 标注仅限非商业使用 | 个人研究 profile；商业路径保持关闭 |
| [VectorBT](https://github.com/polakowo/vectorbt) | 向量化研究/参数扫描 | Apache-2.0 + Commons Clause | `research-fast` profile；最终结果需独立事件引擎复核 |
| [PyPortfolioOpt](https://github.com/PyPortfolio/PyPortfolioOpt) | HRP/最小方差/CVaR 对照 | MIT | V1.1 组合层实验 |
| [vn.py](https://github.com/vnpy/vnpy) | 未来执行适配参考 | MIT；真实券商与账户权限另行审查 | V1 不安装、不连接、不启动 |

### Stage 3B verification note

VectorBT remains an optional `fast-research` dependency (`vectorbt>=0.28,<1`).
It was not installed during the Stage 3B run on Windows/Python 3.12; the
adapter reported the absence and used the project-owned reference fallback.
The core package does not import VectorBT directly. Any future installation
must be followed by `pip check`, the full test suite, and a Windows smoke test;
the Commons Clause license boundary remains recorded above.

## 依赖管理规则

1. 核心安装必须可在无 Token、无券商、无 RQData、无外网历史数据的环境中完成，并能运行离线测试。
2. 生产/研究代码通过本项目的 `DataProvider`、`FeatureStore`、`BacktestProvider` 和 `ExecutionProvider` 接口访问第三方能力。
3. 不复制第三方源代码；只保存版本、许可证、来源链接和适配说明。若未来必须复制文件，先在 `THIRD_PARTY_NOTICES.md` 登记来源、版本和许可证文本。
4. Git 中不得提交 `.env`、Token、账号、数据库、Parquet、模型、Optuna study artifact、运行日志或含敏感信息的 HTML 报告。
5. 升级依赖时先跑全套测试、许可证扫描、Windows 安装 smoke test 和数据契约测试，再更新本文件和锁定版本。
