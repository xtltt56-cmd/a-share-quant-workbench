# A 股量化交易系统交接报告

日期：2026-08-12  
当前分支：`codex/phase1-data-foundation`  
合并提交：`de327c8 merge: complete price guidance and controlled evolution`；后续接线修复：`84dab17 fix: wire canonical daily closes into price guidance`

## 一、交付结论

本次方案2（顺序执行）已完成并合并到主工作区。系统现在具备：

1. 简体中文主工作台和本地人工投顾页面；
2. 官方日选候选的冻结价格指导计划；
3. 盘中只读行情叠加，不重写日选边界；
4. 持仓保护价、减仓区间和 T+1 可卖数量约束；
5. 成本后超额收益标签、时间走步验证和研究级挑战者元数据；
6. 独立概率校准、滚动共形区间和“未校准”降级状态；
7. 挑战者评估、人工批准、一次性令牌和可审计回滚；
8. 工作台拥有的研究任务检查点和安全退出接口；
9. 真实本地数据输入的纸面验收脚本与故障注入测试；
10. 双击快捷方式同时打开主工作台和本地人工投顾页。

系统仍然是纸面监控和人工执行系统。代码中没有券商下单能力，不会自动买卖。

## 二、已完成的主要修改

### 价格指导

- `src/a_share_quant/advisory/price_contracts.py`：冻结日选/持仓计划契约、指导等级、状态和边界校验。
- `src/a_share_quant/features/price_guidance.py`：ATR、均线、波动率、支撑位等只使用截止日及以前数据。
- `src/a_share_quant/advisory/price_engine.py`：保守买入区间、最高可接受价、失效价和研究级数量为 0。
- `src/a_share_quant/runtime/price_guidance.py`：按证券生成日选与持仓计划，历史不足或规则不支持时生成 `NO_RELIABLE_GUIDANCE`。
- 运行时兼容标准 BaoStock 日线湖的 `close` 字段：在没有显式 `raw_close` 时按未复权边界复制，保留已有显式原始收盘价；对应回归测试防止真实数据接入后整页误报“无可靠指导”。
- `src/a_share_quant/storage/price_guidance_store.py`：原子写入、摘要校验、未知字段拒绝、篡改/截断/未来日期 fail-closed。
- `src/a_share_quant/advisory/price_overlay.py`：盘中只读叠加，支持等待价格、价格过高、跌破失效价和数据不可靠状态。

### 预测与受控进化

- `src/a_share_quant/research/forecasting.py`：5/10/20 日成本后超额收益标签；交易行 shift；756/252/126/126 走步切分；Logistic/LightGBM 研究挑战者元数据。
- `src/a_share_quant/research/calibration.py`：isotonic 至少 1000 个独立样本；共形区间至少 500 个已成熟样本，最近 252 个到期日窗口；不足时不生成区间。
- `src/a_share_quant/advisory/contracts.py`：预测记录增加基准指数、最小边际、校准版本、区间和制品 SHA-256 字段；旧上下文格式显式兼容。
- `src/a_share_quant/research/evolution.py`：14 项晋级门、影子期、成本后表现、回撤、容量、Deflated Sharpe、PBO、市场状态、复现性和泄漏检查；自动晋级不存在。
- `src/a_share_quant/workbench/app.py`：模型治理中文区域及本地预览/确认/回滚 HTTP 路由；报告变更后返回 `REPORT_CHANGED`。

### 生命周期与界面

- `src/a_share_quant/runtime/research_jobs.py`：白名单研究任务、原子 JSON 检查点、损坏拒绝和安全停止。
- `scripts/start_quant_workbench.ps1` / `scripts/stop_quant_workbench.ps1`：不创建开机启动；停止时先请求受保护的 loopback 安全退出，再只清理身份匹配的工作台进程。
- `docs/PRICE_GUIDANCE_GUIDE_ZH.md`：简体中文操作说明、字段含义、研究数量为 0 的原因、人工执行和免责声明。
- `scripts/run_price_guidance_acceptance.py`：真实本地日线输入的纸面验收和故障关闭回放。

## 三、测试和质量证据

合并后的全量命令：

```powershell
D:\量化交易\.venv\Scripts\python.exe -m pytest -q
```

结果：`536 passed, 3 skipped`。

3 个跳过均为当前 Windows 账户没有创建符号链接权限（WinError 1314）的能力型测试，不是业务失败。测试过程中有 2 个第三方库警告（Pandas 弃用提示、Qlib 链式赋值提示），不影响退出码。

静态检查：

```powershell
D:\量化交易\.venv\Scripts\python.exe -m ruff check .
git diff --check
```

结果：均为 0 错误。

价格指导聚焦验收：`24 passed`（运行时、引擎、工作台和验收组合）；此前纸面验收记录为 `73 passed`。验收报告见 `reports/price_guidance_acceptance_2026-08-12.md`。

本机真实数据核对（2026-08-12）：`.runtime/signals/official-daily.json` 含 10 条 BaoStock 日选候选，`.runtime/advisory/price-guidance.json` 已能读取对应日线。当前保守规则对其中 1 条生成研究参考价格边界，另外 9 条因边界一致性或风险距离门槛不满足而明确返回 `NO_RELIABLE_GUIDANCE`；页面显示“暂无可靠指导价”，建议数量仍为 0。这是故障关闭行为，不是用估算值填充价格。盘中快照已能覆盖这些候选，但免费 AKShare 端点存在延迟和部分过期，数据质量为 `DEGRADED` 时不会显示为可执行信号。

## 四、当前发布状态

`config/production_readiness.yaml` 仍然故意保留以下阻断：

- 实时数据稳定性：免费端点存在限流、延迟和代理失败风险，不能把失败状态填成 READY；
- 正式样本外模型：当前挑战者仍为 `RESEARCH_ONLY`，需要至少 60 天影子运行、1000 个成熟样本、3 个以上样本外窗口，并通过人工批准；
- 财信 QMT 只读连接：本机普通财信客户端不是 QMT，项目没有检测到 `xtquant`/`userdata_mini`；
- 整体人工投顾发布门：只有经过核验的实时上下文和成熟预测记录才能解除，当前没有伪造这些输入。

因此系统可以投入“免费数据 + 纸面监控 + 人工执行”的试运行，不应描述为已验证的自动交易系统或保证准确的投资建议系统。

## 五、用户仍需手动完成的事项

1. 如果需要财信 QMT 账户数据，只能联系财信 95317/客户经理申请“迅投 QMT 量化服务和 XtQuant Python API 只读权限”，确认费用、资产/交易量门槛和行情权限。
2. 获批后从财信官网下载并安装“迅投 QMT”，登录 MiniQMT，再确认外部 Python 能发现 `xtquant` 和 `userdata_mini`。
3. 用户在财信客户端导出成交或持仓文件到 `.runtime\advisory\import-inbox`，在“本地人工投顾 → 券商导出文件导入”中扫描、生成预览、人工核对后确认。
4. 运行免费历史/实时数据前，确保本机网络允许 AKShare/BaoStock 请求；端点失败时页面应显示数据失败或过期，而不是人工填值。

## 六、建议的后续顺序

1. 先用 BaoStock/AKShare 累积真实日线和成熟结果，不降低校准门槛；
2. 连续记录实时数据质量、延迟、断点和缓存年龄；
3. 只有在满足影子期和成熟样本门槛后，运行模型评估并人工审查治理页面；
4. 需要账户核对时优先使用只读导出文件；QMT 授权可用后再接只读行情/资产/持仓适配器；
5. 任何正式模型晋级前保留旧冠军、模型制品摘要和可回滚记录。

## 七、重要限制

- 价格指导是风险约束和研究参考，不是保证盈利价、必涨价或自动止损指令；
- 研究级计划数量固定为 0，直到正式模型完成独立校准、样本外验证和人工批准；
- 数据源越多不等于预测越准，未经时间对齐、去重和质量门控的多源数据可能增加偏差；
- 普通财信客户端登录不能替代官方 QMT API；不建议 OCR、界面抓取或逆向客户端作为生产数据源；
- 所有交易决定仍由用户在券商端人工复核和执行。
