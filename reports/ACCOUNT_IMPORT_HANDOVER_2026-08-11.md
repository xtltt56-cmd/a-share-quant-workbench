# 账户导入桥交接报告

日期：2026-08-11
分支：`codex/phase1-data-foundation`
范围：公开行情保持自动获取；财信账户数据通过官方导出文件进入本地；所有交易继续人工执行。

## 交付结果

已完成一个不依赖 QMT 授权、不读取财信客户端私有协议的账户导入桥：

1. 扫描固定目录 `D:\量化交易\.runtime\advisory\import-inbox`，只接受直接子目录中的 CSV/XLSX/XLS 文件。
2. 支持 UTF-8、UTF-8 BOM、GB18030 CSV，以及标准 XLSX/XLS 和财信/同花顺常见的“GB18030 制表符文本但扩展名为 `.xls`”格式。
3. 自动识别成交明细与持仓快照；常见中文字段包括证券代码、证券名称、成交数量、成交价格、买卖标志、成交日期、股票余额、可用余额、冻结数量、成本价、可用资金和日期。
4. 预览阶段只读取和校验，不写入任何账本或快照；确认阶段需要一次性确认令牌。
5. 成交明细使用已有追加式哈希链账本，重复文件导入幂等；持仓快照独立保存，不伪造成历史成交，不改变本机账本现金。
6. 文件在预览后被修改、路径变成链接/结点、摘要不一致或格式不明确时失败关闭。
7. 工作台新增中文“券商导出文件导入”卡片和三个本地接口：

   - `GET /api/advisory/imports`
   - `POST /api/advisory/import-preview`，只接受 `file_id`
   - `POST /api/advisory/import-confirm`，只接受一次性 `confirmation_token`

8. 桌面快捷方式启动时自动创建收件箱，并同时打开“官方日选 + 盘中监控”和“本地人工投顾”。
9. 新增中文手册：[ACCOUNT_IMPORT_GUIDE_ZH.md](../docs/ACCOUNT_IMPORT_GUIDE_ZH.md)。

## 你的真实样例

你提供的 `D:\table.xls` 已保留原文件，并复制到：

`D:\量化交易\.runtime\advisory\import-inbox\table.xls`

检测结论：它不是标准二进制 Excel，而是 GB18030 编码的制表符文本，使用 `.xls` 扩展名。系统已针对该格式增加兼容和回归测试。实际预览识别出 2 条持仓记录，代码为 `002007`、`603883`；其中证券代码以 `="002007"` 形式导出，系统会安全清理外层公式文本后再校验代码。

原始文件未被删除或覆盖。由于收件箱属于本机运行时目录，不会进入 Git。

## 关键提交

| 提交 | 内容 |
|---|---|
| `8b91525` | 安全收件箱、文件边界、GB18030 CSV |
| `f102ebe` | 成交/持仓模式识别、买卖方向和真实 `.xls` 文本适配 |
| `1235c14` | 完整性校验和原子持仓快照存储 |
| `7db2eac` | 复用已验证成交预览确认路径 |
| `b2d8ff0` | 一次性预览/确认工作流和来源分离 |
| `4b297be` | 本地 HTTP 路由和请求边界 |
| `337a4d7` | 简体中文导入页面 |
| `d1a618f` | CLI、PowerShell 启动器和收件箱路径 |
| `3dfba4a` | 操作手册、离线验收脚本和开源边界说明 |

设计和执行计划仍记录在：

- [账户导入桥设计规格](../docs/superpowers/specs/2026-08-11-account-import-bridge-design.md)
- [账户导入桥实施计划](../docs/superpowers/plans/2026-08-11-account-import-bridge.md)

## 验证证据

### 相关联合回归

命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_account_import_inbox.py tests/test_account_snapshot_store.py tests/test_account_import_workflow.py tests/test_account_import_http.py tests/test_account_import_cli.py tests/test_account_import_acceptance.py tests/test_account_importer.py tests/test_account_ledger.py tests/test_account_store.py tests/test_advisory_service.py tests/test_workbench_app.py tests/test_launcher_security.py tests/test_backup_restore.py -q
```

结果：`113 passed, 3 skipped`。3 个 skip 都是当前 Windows 令牌无权创建真实文件符号链接，属于能力条件 skip，不是业务失败。

### 全量回归

命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

结果：`449 passed, 3 skipped, 2 warnings`。警告来自既有 pandas/QLib 依赖，不是本次导入代码；没有测试失败。

### 静态检查

```powershell
.\.venv\Scripts\python.exe -m ruff check .
git diff --check
```

结果：Ruff `All checks passed!`；差异检查退出码 0、无输出。

### 离线端到端验收

```powershell
.\.venv\Scripts\python.exe scripts\run_account_import_acceptance.py --workspace .runtime\acceptance\account-import-final
```

输出语义：

```json
{
  "status": "PASS",
  "fills_recorded": 1,
  "duplicate_fills_recorded": 0,
  "positions_loaded": 1,
  "manual_execution_required": true,
  "order_capability_present": false
}
```

## 现在的使用流程

1. 双击桌面的“A股量化交易系统”。
2. 在财信客户端用官方导出功能导出成交或持仓文件。
3. 将文件放入 `D:\量化交易\.runtime\advisory\import-inbox`。
4. 打开“本地人工投顾”，点击“扫描导出文件”。
5. 选择文件，点击“生成预览”，核对证券代码、数量、价格和日期。
6. 确认无误后点击“确认导入”。

如果出现无法识别列名，保留原文件并提供删除姓名、资金账号、股东号等敏感信息后的表头和一两行样例；只增加明确映射，不使用猜测、OCR、抓包或逆向。

## 仍需人工完成的事项

- 每次需要更新账户数据时，仍需在财信客户端执行一次官方导出并把文件放入收件箱；普通财信客户端没有可供本项目使用的实时账户 API。
- 第一次真实操作时，你需要确认导出文件中列名和数据口径；系统不会替你猜测“余额”“可用”“冻结”等含义。
- 若希望 QMT 只读实时账户查询，仍需向财信申请正式 QMT/XtQuant 权限；本导入桥不绕过授权门槛。

## 明确限制

- 公开行情、日选和盘中监控与账户导入是两个数据来源；账户导入不会制造预测信号。
- 持仓快照不会自动改写历史成交，因此本机账本与券商快照可能暂时不同；正式成交和结算记录优先。
- 系统没有下单、撤单、程序化报单或自动交易能力。
- 系统不能保证预测准确、收益或避免损失；“确认导入”只表示确认数据进入本机记录。
