# A 股量化系统 Framework-first 重设计

状态：已批准实施的架构基线

日期：2026-08-08

## 1. 目标与边界

项目 V1 仍然是：A 股日频数据 → 数据清洗与本地存储 → 股票池过滤 → 多因子评分/模型信号 → 每日候选 → 回测 → 风控 → 报告 → 纸面交易监控。

本次重设计停止从零实现通用数据、回测、指标、优化和报告基础设施。成熟开源框架负责通用能力；本项目只实现：

- A 股数据源字段和服务适配；
- PIT（point-in-time）数据版本和数据质量门禁；
- A 股股票池排除规则；
- 规则因子、Qlib Handler/Model 配置和策略注册；
- 统一信号/组合/风控契约；
- A 股交易约束配置和纸面交易记录；
- 外部框架的 Adapter、版本审计和安全边界。

V1 明确不包含真实券商下单、账户登录、交易网关、网络监听、自动实盘、真实资金操作和互联网实时舆情自动交易。

## 2. 总体架构

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ Data sources: AKShare (V1) | Tushare/RQData (future optional)                │
└─────────────────────────────┬────────────────────────────────────────────────┘
                              v
                     DataProvider contract
                              v
               Schema / date / quality / PIT gate
                              v
                    DuckDB + Parquet Data Lake
                              v
             Feature Store + Qlib DataHandler bridge
                              v
        ┌─────────────────────┴────────────────────┐
        │ Rule factors | Qlib Alpha158/360 + models │
        └─────────────────────┬────────────────────┘
                              v
                  Unified Signal Schema (versioned)
                              v
                 Ranking / Ensemble / Portfolio plan
                      ┌───────┴────────┐
                      v                v
             VectorBT quick research  Risk engine
                 (optional)             │
                      │                 v
                      └──────────> RQAlpha event validation
                                      (optional/personal)
                                               v
                                  Walk-forward / OOS gate
                                               v
                                  Report + PaperTrading
                                               v
                           Future ExecutionProvider only
```

所有箭头跨越模块边界时使用稳定契约；策略不能直接 import AKShare、DuckDB connection 或任何券商 SDK。

## 3. 组件与职责

### 3.1 DataProvider

接口按业务能力定义，而不是按 AKShare 函数名定义：

- `list_instruments(as_of)`：代码、名称、交易所、上市日期、ST/退市风险状态；
- `daily_bars(symbols, start, end, adjusted)`：OHLCV、成交额、换手率、复权信息；
- `indices(indexes, start, end)`：沪深 300 等基准；
- `fundamentals(symbols, as_of)`：财报期、公告日、质量字段；
- `valuation(symbols, as_of)`：估值字段和公告/采集时点；
- `money_flow(symbols, start, end)`、`sectors(...)`、`trading_calendar(...)`；
- `suspension(...)`、`corporate_actions(...)`、可交易状态和涨跌停参考字段。

AKShare adapter 只负责请求和源字段映射。重试、限流、缓存、schema 校验、错误分类和写入由应用层/存储层负责。Tushare/RQData 复用同一契约。

### 3.2 数据湖与 Feature Store

- 原始返回保存在被 Git 忽略的 Parquet 分区，带 `source`、`fetched_at`、请求参数摘要和 schema 版本。
- DuckDB 保存 manifest、数据质量结果、交易日历、PIT 索引和跨分区查询视图。
- 规范数据必须有 `symbol`、`date`、`as_of`/`announced_at`（适用时）、`source`、`data_version`。
- 以 `(dataset, symbol, date, as_of)` 去重；更新不能覆盖更早版本的财报或更早的采集记录。
- Qlib 通过自定义 DataHandler/导出桥读取本项目的规范数据，不直接读取 AKShare 中文列名。

### 3.3 策略注册

V1 注册以下策略 ID：

- `rule_multifactor`：资金流、趋势/动量、相对强弱、成交量/换手、质量、估值、波动/风险；
- `qlib_lightgbm_alpha158`：Qlib Alpha158 + LightGBM；
- `qlib_double_ensemble_alpha158`：Qlib Alpha158 + DoubleEnsemble；
- `ensemble`：只有经过统一 OOS/风险门禁的 baseline 才可组合。

所有策略输出同一 `SignalRecord`：

```text
strategy_id, strategy_version, model_version, signal_date,
symbol, score_0_100, rank, factor_scores, feature_snapshot_id,
data_version, reason_codes, risk_flags, valid_until
```

因子权重和阈值放 YAML；模型、特征、数据和参数分别有版本号。每个分数都必须能够追溯到 PIT 数据切片。

### 3.4 股票池与因子

在因子计算前按当日可知信息过滤：ST、`*ST`、退市整理/退市风险、新股观察期、停牌、无效价格、低成交额/低换手、无法交易证券和数据质量异常。股票池快照按 `as_of` 保存，禁止使用今天的完整股票名单回填历史。

规则因子在横截面内统一 winsorize/标准化，再映射到 0–100。若当日样本不足或因子质量失败，返回不可用状态而不是伪造中性分数。Qlib 特征也必须通过相同的 PIT 和缺失检查。

### 3.5 回测与风控

- VectorBT 只做快速参数扫描和研究级 walk-forward，交易信号、价格和成本来自同一个规范数据快照。
- RQAlpha adapter 负责最终事件撮合验证（个人研究 profile）；不修改 RQAlpha core。
- `BacktestProvider` 契约保留未来可替换事件引擎。V1 不因为 RQAlpha 许可证限制而把它写入核心策略代码。
- 交易约束包括 T+1、涨跌停、停牌、佣金、印花税、过户费（如适用）、滑点和无法成交；无法成交必须是显式事件。
- 风控包括单股最大仓位、最大持仓数、总仓位、固定止损、ATR/趋势止损和最大组合回撤。
- 统一输出累计收益、年化收益、最大回撤、Sharpe、Sortino、Calmar、胜率、盈亏比、Profit Factor、换手率、最大连续亏损和相对沪深 300 的表现。

### 3.6 纸面交易与未来执行层

纸面交易只接收通过 OOS 和风控门禁的信号，记录：

```text
strategy_id, strategy_version, model_version,
signal_date, execution_date, symbol, score,
entry_price, exit_price, position, pnl, drawdown,
reason_codes, features_snapshot_id, data_version
```

未来 `ExecutionProvider` 只接受经过人工批准的 `PaperOrder`，默认实现是拒绝真实下单的 `NoopExecutionProvider`。vn.py 或券商 SDK 不得绕过这个边界。

## 4. 防未来函数与样本外规则

1. 行情特征在 `signal_date` 只能使用该日收盘前/收盘时定义允许的数据；默认在下一交易日可成交。
2. 财务/估值字段必须以 `announced_at <= signal_date` 过滤；报告期本身不是可用时间。
3. 股票池和指数基准使用当日快照，禁止用当前成分股回填历史（survivorship bias）。
4. 参数搜索只能使用训练窗口和验证窗口；测试窗口在试验结束前不可读。
5. 使用 rolling walk-forward：train → validation → OOS test，测试窗口滚动向前；所有窗口、数据版本和参数写入实验元数据。
6. 研究、回测、鲁棒性、Walk-Forward、纸面交易和批准是单向状态机；没有 `Research → Live` 路径。

## 5. 版本和许可证边界

- `strategy_version`、`model_version`、`feature_version`、`data_version`、`backtest_engine_version` 必须进入每份报告和每笔纸面交易记录。
- 核心免费路径只依赖许可证已审查且可用于当前目标的组件。
- RQAlpha 的非商业限制和 VectorBT 的 Commons Clause 记录在 [DEPENDENCIES.md](../../../DEPENDENCIES.md)；未来需要商业化时更换 Adapter，不修改核心契约。
- 不复制第三方源码；第三方版本和来源统一记录在 [THIRD_PARTY_NOTICES.md](../../../THIRD_PARTY_NOTICES.md)。

## 6. 本次第一阶段的验收边界

本阶段只完成：

1. 仓库、目录、README、配置、`.env.example`、Git 忽略和依赖声明；
2. `DataProvider`、canonical daily bars/instrument schema 和 `MarketDataStore` 契约；
3. AKShare adapter 的离线 fake 测试和显式网络 smoke test；
4. DuckDB manifest + Parquet 增量去重/质量校验；
5. 安全威胁模型和最小测试集。

Qlib/VectorBT/RQAlpha 的正式基准在后续阶段接入，不能用“已安装”代替基准结果或样本外证据。
