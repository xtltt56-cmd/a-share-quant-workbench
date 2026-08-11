# A-Share Quant Framework-first

## Stage 2 progress (2026-08-08)

- [x] PIT feature store with separate trade date, report period, announcement date, effective date, and ingest time.
- [x] Default financial-data visibility is the next trading day; missing announcement dates are rejected.
- [x] Historical as-of universe filtering for listing age, delisting, ST, suspension, and historical liquidity.
- [x] Qlib 0.9.7 provider adapter and official Alpha158 dataset builder with `forward_excess_return_5d` labels.
- [x] Qlib LightGBM and Qlib DoubleEnsemble official model adapters with deterministic versioned predictions.
- [x] Rule-based factor baseline and unified versioned signal adapter.
- [x] Fixed OOS / Walk-Forward split utilities, A-share-aware fair evaluator, and reproducible experiment artifacts.
- [x] Fixed OOS / Walk-Forward runner writes window-level and aggregate artifacts.
- [x] report-renderer HTML smoke validated with an explicit synthetic-data warning.
- [ ] Real AKShare/local-data Stage 2 run and rendered baseline comparison report.

## Stage 3A progress (2026-08-09)

- [x] Approved adapter-first architecture and implementation plan.
- [x] Frozen the accepted Stage 2 baseline in
      `artifacts/baselines/STAGE2_BASELINE_MANIFEST.json` without modifying
      Stage 2 experiment directories.
- [x] Added shared `SignalFrame`, `PortfolioTarget`, and `ExecutionSpec`
      contracts with explicit after-close signal and T+1 execution semantics.
- [x] Added out-of-sample IC/Rank IC/ICIR, quantile, Top20, time-period,
      causal regime, volatility, stability, and model-correlation analysis.
- [x] Added structural promotion checks ending at `SIGNAL_VALIDATED`; `LIVE`
      remains forbidden.
- [x] Added TEST-isolation boundary for future ensemble/Optuna work.
- [x] Generated `reports/stage3_signal_quality_report.md` and its ignored JSON
      companion. The current report is explicitly labelled `fixture` data.
- [x] Stage 3A verification: 73 tests passed, Ruff passed, `pip check` passed,
      `compileall` passed, and coverage remained at 86%.

Stage 3A commands:

```powershell
\.venv\Scripts\python.exe scripts\freeze_stage2_baseline.py
\.venv\Scripts\python.exe scripts\run_stage3a_signal_quality.py
\.venv\Scripts\python.exe -m pytest -q
```

## Stage 3B progress (2026-08-09)

- [x] Added explicit `fixture` / `historical` / `paper` data-mode separation;
      fixture evidence can reach only `SIGNAL_VALIDATED_FIXTURE`.
- [x] Added the formal `PortfolioStrategy` boundary and four candidates:
      TopK equal-weight, score-weight, rank-weighted, and TopK dropout.
- [x] Added shared `RebalancePolicy`, unified raw/normalized turnover, and a
      versioned `BacktestResult` contract.
- [x] Added the optional VectorBT adapter with a non-blocking reference
      fallback. VectorBT is not installed in the current Windows environment.
- [x] Generated [Stage 3B fast-research report](reports/stage3_fast_research_report.md).
      It is explicitly marked `TEST / FIXTURE DATA - NOT INVESTMENT EVIDENCE`.
- [x] Historical dry run remains `NOT_RUN`: local candidate bars for
      `000002`/`000003`/`000004` and the `000300` benchmark are missing.
- [ ] Stage 3C is intentionally not started; RQAlpha, paper trading, brokers,
      and live order interfaces remain out of scope.

Stage 3B command:

```powershell
.\.venv\Scripts\python.exe scripts\run_stage3b_fast_research.py
```

The three frozen bundles are synthetic fixture artifacts from the accepted
Stage 2 smoke run. Their metrics are useful for deterministic contract and
pipeline validation only; they are not real-market investment evidence. VectorBT
and RQAlpha remain optional Stage 3B/3D adapters and no broker or live execution
provider is present.

## Stage 3RT progress (2026-08-09)

- [x] Approved and documented the independent Real-Time Quant Workbench
      design. It is intentionally separate from Stage 3C and remains
      monitoring/paper-signal only.
- [x] Added provider-neutral `RealTimeQuote`, `MinuteBar`, `MarketSnapshot`,
      capability, health, quality, and provider-switch contracts.
- [x] Added `RealTimeDataProvider`, capability discovery, ordered failover, and
      sanitized provider errors. The core layer has no provider SDK imports.
- [x] Added freshness assessment, timestamp-backwards/future checks, and a
      stale-data circuit breaker that blocks `READY` state inputs.
- [x] Added an in-memory `RealTimeStore` whose provisional bars are isolated
      from historical/PIT state until explicit EOD finalization.
- [x] Added `config/realtime.yaml` and RQData/Tushare credential placeholders
      to `.env.example`; no credential value is stored in Git.
- [x] Stage 3RT provider adapters, deterministic replay, explicit smoke test,
      production-symbol historical dry-run gate, and the local RealTimeStore
      boundary are implemented. The current network smoke remains a sanitized
      failure because the configured proxy blocked AKShare; this is not treated
      as market evidence.
- [x] Stage 3RT-C adds point-in-time intraday features, market breadth,
      WAIT/WATCH/READY/OVERHEATED/RISK/STALE_DATA monitor states, model
      frequency guards, trading-session-aware polling, bounded retries, gap
      tracking, and an ordered EOD finalization/PIT boundary.
- [x] Stage 3RT-D adds the loopback-only Dashboard/API, paper-only service,
      CLI, bounded Windows launcher/stop scripts, and Desktop shortcut creator.
      The verified local shortcut is
      `C:\Users\lenovo\Desktop\A股量化交易系统.lnk` and is not committed.
- [x] Stage 3RT acceptance report is in
      [`reports/stage3_realtime_workbench_report.md`](reports/stage3_realtime_workbench_report.md).
      The current provider smoke remains proxy-blocked; this is explicitly
      recorded as a data-source limitation, not investment evidence.
- [x] Stage 3RT regression: 175 tests passed, coverage 88%, Ruff/pip
      check/compileall/diff checks passed. No broker, account, or live order
      interface exists.

The Stage 3RT-A design is recorded in
[`docs/superpowers/specs/2026-08-09-stage3rt-realtime-workbench-design.md`](docs/superpowers/specs/2026-08-09-stage3rt-realtime-workbench-design.md)
and the execution plan is in
[`docs/superpowers/plans/2026-08-09-stage3rt-realtime-workbench.md`](docs/superpowers/plans/2026-08-09-stage3rt-realtime-workbench.md).

### Stage 3RT-B evidence

- [x] Added lazy AKShare, Tushare, RQData, and deterministic Replay adapters;
      optional SDKs are never imported by the core contracts.
- [x] Capability discovery reports authentication, permissions, frequency,
      and status; permission-denied providers are retained for reporting but
      are excluded from the failover chain.
- [x] Ran the explicit real-market smoke command. AKShare was the only
      configured-capable provider, but the current proxy blocked its request;
      the sanitized result is in
      [`reports/realtime_data_smoke_test.md`](reports/realtime_data_smoke_test.md).
- [x] Used the formal AKShare daily provider to write production-symbol bars
      for `000006`–`000009` into local Parquet. No fixture symbols were mapped
      into production signals.
- [x] Added CSI300 mapping fallback `000300 -> csi000300`; the current proxy
      also blocked that endpoint, so the benchmark remains missing.
- [x] Added a historical readiness report that remains `NOT_READY` because
      the benchmark and historical model artifacts are unavailable; fixture
      models are never presented as historical evidence.

### Stage 3RT-C evidence

- [x] Intraday features use canonical minute bars and only current/prior rows;
      appending a future bar cannot alter earlier feature values. Missing
      optional benchmark, industry, previous-close, and baseline-volume fields
      remain missing rather than being fabricated.
- [x] Market breadth reports up/down/flat, limit-up/limit-down, amount,
      breadth score, and a descriptive market temperature while excluding stale
      or incomplete observations.
- [x] The daily rule baseline is explicitly `DAILY` and rejects an intraday
      data-frequency invocation. Intraday states are monitor outputs only and
      never official model signals or orders.
- [x] The scheduler distinguishes pre-market, open, lunch break, closed, and
      non-trading sessions; polls only when open; retries with bounded
      backoff; and rejects future/backwards quote timestamps at the runtime
      boundary.
- [x] EOD finalization runs close confirmation, final daily load, reconciliation,
      PIT update, official daily signal generation, and report writing in order;
      any failure returns no official signals.
- [x] Stage 3RT-C regression: 167 tests passed, including the original suite
      and new intraday/runtime/frequency/EOD coverage. Full coverage and the
      final commit gate are recorded after the repository checks below.

### Stage 3RT-D evidence

- [x] Added `src/a_share_quant/workbench/app.py` and `service.py`. The HTTP
      server rejects non-loopback hosts, returns sanitized health/state JSON,
      and separates official daily candidates from descriptive intraday
      monitor rows.
- [x] Added `scripts/quant_cli.py` plus PowerShell start/stop/shortcut
      scripts. The launcher owns only its PID file, writes local logs, opens
      the browser at `127.0.0.1`, and has an offline mode for deterministic
      UI smoke tests.
- [x] Created and inspected the local Desktop `.lnk`; it targets the stable
      PowerShell launcher and never a temporary Python path. The `.lnk` is
      outside Git.
- [x] Dashboard/launcher security tests cover loopback binding, paper-only
      state, no raw provider error payload, no broker import, and no
      `0.0.0.0` listener.

### Stage 3RT-E E1 network evidence

- [x] Added the safe operator command
      .\.venv\Scripts\python.exe scripts\quant_cli.py realtime diagnose-network.
      It inspects environment, WinHTTP, WinINET/system proxy, a local proxy
      listener, DNS, HTTPS, official AKShare/Eastmoney endpoint reachability,
      requests, and aiohttp without printing proxy addresses, user names,
      passwords, tokens, raw exception payloads, or disabling TLS.
- [x] Added explicit security identifiers and vendor adapters. CSI300 is now
      represented as the non-tradable SSE index 000300 rather than an A-share
      equity; AKShare, Tushare, RQData, and Qlib formatting stays at provider
      boundaries.
- [x] The installed AKShare 1.18.83 matched the current PyPI stable release
      during this verification, so no untested upgrade or third-party proxy
      patch was applied.
- [x] The redacted live diagnostic found no environment or WinHTTP proxy,
      a listening local WinINET/system proxy, working DNS/HTTPS/full-market
      endpoint requests, but a ProxyError on AKShare's actual individual-quote
      long-field request. This is a narrow proxy/provider request-path failure,
      not evidence that AKShare, DNS, TLS, or all Python networking is broken.
      The default system-proxy policy remains in force; no silent bypass was
      enabled. See reports/network_diagnostics.md.
- [ ] The real-market gate remains STAGE_3RT_OFFLINE_VALIDATED. It cannot be
      promoted until an actual A-share trading session supplies valid, fresh,
      continuously updating real quotes, complete breadth, intraday features,
      real EOD finalization, and the remaining final-gate evidence. No broker,
      order, or execution interface is added.

### Stage 3RT-E E2 live-validation boundary

- [x] The workbench now requires a connected provider, schema-valid and fresh
      quotes, nondecreasing exchange timestamps, two distinct updates, and a
      closed circuit breaker before a non-replay source may display
      DataQuality=GOOD. A single successful request remains DEGRADED.
- [x] Provider telemetry is bounded and payload-free: it records connection
      and quote times, quote/error/fallback/stale counts, plus average and P95
      latency, but never exception text, credentials, URLs, or raw payloads.
- [x] Official daily candidates and RealtimeOverlay observations are stored
      separately. The overlay contains market facts and a monitor state only;
      it cannot change a Stage 2 score. READY remains monitoring-only.
- [x] The loopback dashboard now shows active source, public/professional or
      replay source class, backend quote timestamp, last update, data age,
      latency, quality, fallback count, and continuous-update status. API and
      browser requests use Cache-Control: no-store.
- [x] E2 regression: 197 tests passed with 89% coverage in the full suite.
      The live status remains STAGE_3RT_OFFLINE_VALIDATED; no broker,
      account, order, or execution interface has been added.

The Qlib adapter writes a project-owned local provider under the experiment/data path and keeps Qlib-specific imports inside `src/a_share_quant/integrations/qlib/`. It uses `kernels=1` by default on Windows to keep dataset construction deterministic and avoid uncontrolled worker spawning. Every comparison bundle records the fixed split, Walk-Forward windows, Git revision, dataset hash, configuration hash, library versions, feature/data/model versions, and seed under `experiments/<experiment_id>/`.

Stage 2 operator commands:

```powershell
# External calls require an explicit acknowledgement.
.\.venv\Scripts\python.exe scripts\update_data.py --provider akshare --start-date 2020-01-01 --end-date 2026-08-08 --limit 10 --network-smoke
.\.venv\Scripts\python.exe scripts\update_benchmark.py --symbol 000300 --start-date 2020-01-01 --end-date 2026-08-08 --network-smoke

# Refuses to proceed if the benchmark or a historical universe snapshot is missing.
.\.venv\Scripts\python.exe scripts\run_stage2_experiment.py --mode local

# Deterministic integration smoke only; never use its returns as investment evidence.
.\.venv\Scripts\python.exe scripts\run_stage2_experiment.py --mode fixture --fixture-periods 700
```

The current machine has only a four-row local stock sample and no local `000300` index snapshot. The live AKShare smoke request was attempted but the configured network proxy rejected both stock and index endpoints; therefore no synthetic fixture result is presented as a real-market run.

After a real local run, render the report with the installed renderer (use UTF-8 mode on this Windows console):

```powershell
.\.venv\Scripts\python.exe -X utf8 `
  'C:\Users\lenovo\.codex\skills\report-renderer\scripts\render_report.py' `
  'experiments\stage2_baseline\report\baseline_comparison.md' `
  'experiments\stage2_baseline\report\baseline_comparison.html'
```

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
.\.venv\Scripts\python.exe -m pip install -e ".[data-free]"      # BaoStock free adapter
```

`.env` 只保存本机配置和可选 Token，绝不提交 Git。AKShare/BaoStock 默认不需要 Token；Tushare/RQData 必须显式启用。密钥不会写入源码、测试夹具、日志、模型 artifact 或报告。

## 第一阶段命令

离线测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
```

使用 fake provider 验证本地写入不需要网络。需要真实 AKShare 网络检查时，显式运行：

```powershell
.\.venv\Scripts\python.exe scripts/update_data.py --provider akshare --limit 1 --start-date 2026-01-01 --end-date 2026-01-05 --network-smoke
.\.venv\Scripts\python.exe scripts/update_data.py --provider baostock --limit 1 --start-date 2026-01-01 --end-date 2026-01-05 --network-smoke
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
  data/providers/          AKShare/BaoStock/Tushare/RQData 适配器
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

## V1 验收状态（2026-08-10）

本地人工投顾链路、简体中文工作台、免费 BaoStock 历史数据适配器和 QMT 只读边界已完成。详细证据见 [A 股人工投顾 V1 验收报告](reports/a_share_advisory_v1_acceptance.md)。

系统仍严格保持纸面/人工执行：Tushare 接口权限、财富证券官方 QMT 权限、真实市场 EOD/PIT 数据和 60–120 个交易日纸面验证属于外部闸门，未满足前不会宣称预测准确或开放自动交易。

## 生产可用化执行计划

后续工作按 [生产可用化实施计划](docs/superpowers/plans/2026-08-11-a-share-production-readiness.md)
执行。当前验收矩阵位于 [config/production_readiness.yaml](config/production_readiness.yaml)，
其中 `BLOCKED` 和 `NOT_OBSERVED` 不会被汇总为可发布状态；系统只有在数据、样本外模型、指导、券商边界、界面和纸面安全门全部通过后，才可提升发布级别。
