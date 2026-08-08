# 第二阶段：PIT 数据桥与 Qlib Baselines 设计

状态：依据用户第二阶段要求冻结，执行中

日期：2026-08-08

## 1. 目标与非目标

本阶段建立可审计的 point-in-time（PIT）数据路径，并在相同数据、股票池、label、时间切分和成本元数据下运行三个 baseline：

1. `RuleBasedMultiFactor`；
2. `QlibAlpha158LightGBM`；
3. `QlibAlpha158DoubleEnsemble`。

三个结果都必须转换为同一个 `SignalRecord`，完成一次固定样本外比较和至少一个 rolling Walk-Forward demo。暂不接入 VectorBT、RQAlpha、vn.py、深度学习、强化学习、大规模参数搜索或任何真实交易接口。

## 2. 关键选择

### 2.1 PIT 先于 Qlib

Qlib 不能决定 A 股数据何时可见。PIT 规则先在本项目 `PITFeatureStore` 中计算，再把经过审核的数据桥接给 Qlib。核心策略只依赖项目自己的 `FeatureFrame`、`SignalProvider` 和版本化 metadata，不 import Qlib 内部对象。

### 2.2 时间字段和可见性

所有财务/估值记录至少包含：

```text
symbol
report_period       财报所属期间，例如 2025-12-31
announcement_date   首次公开日期
effective_date      可用于交易信号的日期
ingest_time         本地采集时间，只用于审计，不决定可见性
feature_name
feature_value
source
data_version
```

市场数据使用 `trade_date`。`report_period` 不能作为可见性字段，也不能据此直接 `forward fill`。

默认公告规则写入 `config/pit.yaml`：

```yaml
pit:
  announcement_day_policy: next_trading_day
  allow_same_day_announcement: false
  unknown_announcement_date: reject
```

在默认规则下，收盘后公告的数据从下一个交易日生效。若显式启用 `allow_same_day_announcement`，只允许在数据源提供可靠公告时刻且策略明确允许时使用公告日；两种语义都必须有测试。`ingest_time` 永远不能让未来记录提前可见。

查询逻辑为：

```text
get_features_asof(symbol, asof_date)
  -> 只保留 effective_date <= asof_date
  -> 每个 symbol/feature_name 选择 effective_date 不晚于 asof 的最新版本
  -> 同版本冲突选择明确的数据版本，不按本地采集时间把未来修订提前覆盖
  -> 没有历史可用版本则返回缺失，不伪造中性值
```

这允许公告之后按“已公开信息”使用历史财务数据，但禁止在公告之前看到它。

### 2.3 历史化股票池

`tradable_universe(asof_date)` 使用历史 instrument 状态，而不是当前股票列表。每个状态记录至少包含：

```text
symbol, listed_date, delisted_date,
st_effective_date, st_end_date,
suspended_effective_date, suspended_end_date,
is_delisting_risk, exchange, data_version
```

在 `asof_date` 同时满足已上市、未退市、非 ST/退市风险、非停牌、价格和成交量有效、流动性门槛通过的股票才可进入历史样本。若某个状态没有可靠生效日期，历史回测拒绝使用该状态，而不是用今天状态回填过去。

### 2.4 三个 baseline 的统一条件

固定比较配置包含：

- 同一 historical universe；
- 同一 trade-date range 和 time-based train/validation/test split；
- 同一主要 label：`forward_excess_return_5d`；
- 同一 benchmark、rebalance frequency、交易成本、最大持仓数和单股上限；
- 同一 random seed 和版本 metadata；
- 测试区间不可参与模型/权重调参。

第一版 Rule baseline 使用固定 YAML 权重，不由历史收益反推。规则因子为 Momentum、Trend、Relative Strength、Volume、Turnover、Volatility、Quality、Value；Money Flow 在数据不足时标为 optional，不阻塞核心 baseline。

Qlib baseline 首先使用官方 Alpha158/LightGBM/DoubleEnsemble 逻辑，项目只提供 A 股 PIT 数据桥、label、时间切分和适配器。不得为提升测试收益修改官方 baseline 逻辑。

## 3. 模块边界

```text
canonical market/fundamental frames
             |
             v
src/a_share_quant/features/
  pit_store.py       -> FeatureFrame as-of query
  universe.py        -> historical tradable universe
  labels.py          -> forward excess return, no leakage
  rule_factors.py    -> fixed RuleBasedMultiFactor
             |
             v
src/a_share_quant/integrations/qlib/
  provider.py        -> lazy qlib availability boundary
  dataset_builder.py -> Qlib-compatible dataset artifact
  calendar_adapter.py
  instrument_adapter.py
  feature_adapter.py
  label_adapter.py
  model_runner.py    -> official-style Alpha158 models only
             |
             v
src/a_share_quant/signals/
  schema.py          -> SignalRecord / SignalProvider
             |
             v
src/a_share_quant/experiments/
  metadata.py, runner.py, walk_forward.py, reports.py
```

`integrations/qlib` 只能依赖 `features`, `contracts` 和 Qlib；`features`、`signals` 和 `experiments` 不得依赖 Qlib。未来替换 Qlib 时只替换 integration adapter。

## 4. Label、切分和 Walk-Forward

主要 label 定义为：

```text
asset_return_5d  = close[t+5] / close[t] - 1
benchmark_return = benchmark_close[t+5] / benchmark_close[t] - 1
forward_excess_return_5d = asset_return_5d - benchmark_return
```

label 的未来价格只能出现在训练目标，不能出现在 `FeatureFrame` 的输入列。最后五个可用交易日因缺少未来标签而被排除，并记录排除原因。

固定 split 根据可用交易日自动确定，不硬编码当前数据尚未覆盖的年份：

```text
ordered trading dates -> train -> validation -> test
```

`WalkForwardRunner` 输入 `train_window`、`validation_window`、`test_window` 和 `step`，每个窗口独立生成 dataset、model artifact、predictions、metrics 和 portfolio input，最后只拼接每个窗口的 OOS test 段。不得拼接窗口内最优结果冒充 OOS。

## 5. 统一 Signal Schema

三个 baseline 都实现 `SignalProvider`：

```python
class SignalRecord:
    signal_date: date
    symbol: str
    strategy_id: str
    strategy_version: str
    raw_score: float
    normalized_score: float
    rank: int
    confidence: float | None
    model_version: str
    feature_version: str
    data_version: str
    experiment_id: str
```

`normalized_score` 为横截面 0–100 分数；规则模型的 `raw_score` 是规则分数，Qlib 模型的 `raw_score` 是预测值。所有输出按 `signal_date, rank, symbol` 排序，保存为 `predictions.parquet` 和统一信号 Parquet。

## 6. 版本、缓存和实验

每次实验生成唯一 `experiment_id`，保存到被 Git 忽略的 `experiments/<experiment_id>/`：

```text
config.yaml
metadata.json
metrics.json
predictions.parquet
equity.parquet
model/
report/
```

metadata 至少包含 `git_commit`、`dataset_hash`、`feature_version`、`config_hash`、library versions、train/validation/test 起止日期和 random seed。Alpha158 cache key 为：

```text
feature_version + symbol + trade_date + dataset_hash
```

底层数据 hash 未变化时复用缓存；hash 变化时生成新版本，不覆盖旧实验。

## 7. 报告和质量门槛

报告输入是统一 comparison model，不让每个模型自行定义指标。第一版 Markdown 报告包含数据覆盖、PIT 规则、label、切分、参数、IC、Rank IC、ICIR、Top-K Return、Excess Return、CAGR、Sharpe、Sortino、最大回撤、换手、胜率、年度稳定性、失败案例和风险说明，再由已安装的 `report-renderer` 生成 HTML；没有 Markdown 输入时不调用 renderer。

阶段完成必须满足：

1. PIT synthetic tests 全部通过，明确公告日边界且无未来泄漏；
2. historical universe tests 证明退市股票不会从历史样本消失；
3. Qlib Alpha158 → LightGBM → predictions 真实跑通；
4. Alpha158 → DoubleEnsemble → predictions 真实跑通；
5. Rule baseline 输出同一 Signal Schema；
6. 固定 OOS comparison 和至少一个 Walk-Forward demo 可重现；
7. 现有测试继续通过，coverage 不低于 85%，目标 88%+；
8. 不安装或调用任何真实交易执行组件。
