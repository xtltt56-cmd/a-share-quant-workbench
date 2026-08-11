# 财信导出文件自动导入桥 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立安全的券商导出文件收件箱，使成交和持仓 CSV/XLSX/XLS 可自动发现、预览并由用户确认导入，同时保持公开行情自动化和永久禁止自动下单的边界。

**Architecture:** 新的账户导入适配层负责固定目录扫描、文件完整性校验、常见中文列名识别和持仓快照持久化；成交明细复用现有追加式哈希链账本。浏览器只能提交后端生成的文件标识和一次性确认令牌，不能提供本地路径；确认时必须重新校验文件摘要。持仓快照与本机成交账本分开显示，绝不把快照伪造成历史成交。

**Tech Stack:** Python 3.12、标准库 HTTP/JSON/CSV/hashlib/tempfile、pandas、openpyxl、xlrd、pytest、Ruff、PowerShell。

---

## Locked file structure

- `src/a_share_quant/account/importer.py`: 只负责读取受支持表格和把显式映射的成交行规范化为 `FillEvent`；新增 CSV 编码兼容及可复用的原始表读取结果。
- `src/a_share_quant/account/import_inbox.py`: 只负责收件箱边界、扫描、文件标识、列名检测、成交/持仓预览和确认前摘要复核。
- `src/a_share_quant/account/snapshot_store.py`: 只负责外部持仓快照值对象、严格校验、摘要封装和原子持久化。
- `src/a_share_quant/account/service.py`: 保持账本写入职责；新增“确认一个已验证的 `ImportPreview`”入口，供收件箱工作流复用。
- `src/a_share_quant/workbench/advisory_service.py`: 组合收件箱、账本服务和快照存储，管理一次性确认令牌。
- `src/a_share_quant/workbench/app.py`: 增加本地 HTTP 路由和简体中文操作界面，不解析券商文件。
- `scripts/quant_cli.py`: 仅负责解析固定收件箱和快照路径并注入服务。
- `scripts/start_quant_workbench.ps1`: 创建默认收件箱并把路径传给 CLI。
- `docs/ACCOUNT_IMPORT_GUIDE_ZH.md`: 面向非金融/技术用户的唯一操作手册。

## Locked public contracts

```python
class AccountImportKind(str, Enum):
    FILLS = "FILLS"
    POSITIONS = "POSITIONS"


@dataclass(frozen=True)
class InboxFile:
    file_id: str
    file_name: str
    size_bytes: int
    modified_ns: int


@dataclass(frozen=True)
class ImportedPosition:
    symbol: str
    name: str
    total_quantity: int
    available_quantity: int
    frozen_quantity: int
    average_cost: Decimal


@dataclass(frozen=True)
class ImportedAccountSnapshot:
    snapshot_id: str
    source_sha256: str
    source_name: str
    as_of: date
    imported_at: datetime
    cash: Decimal | None
    positions: tuple[ImportedPosition, ...]


@dataclass(frozen=True)
class AccountFilePreview:
    preview_id: str
    file_id: str
    source_sha256: str
    source_name: str
    kind: AccountImportKind
    detected_mapping: Mapping[str, str]
    fill_preview: ImportPreview | None
    positions: tuple[ImportedPosition, ...]
    cash: Decimal | None
    as_of: date
    rejected_rows: tuple[ImportIssue, ...]
    warnings: tuple[str, ...]
```

`AccountFilePreview` 的互斥条件固定为：`FILLS` 必须有 `fill_preview` 且 `positions` 为空；`POSITIONS` 必须没有 `fill_preview` 且至少有一条有效持仓。所有时间戳写入 UTC；交易/快照日期使用明确的 A 股本地日期。

`AccountSnapshotStore` 的构造参数固定为 `path: Path` 与可注入时钟 `now: Callable[[], datetime]`；公开方法固定为 `save(snapshot: ImportedAccountSnapshot) -> None` 和 `load() -> ImportedAccountSnapshot | None`。默认时钟返回当前 UTC 时间，测试注入固定 UTC 时间。

---

### Task 1: 扩展表格读取并建立安全收件箱

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/a_share_quant/account/importer.py`
- Create: `src/a_share_quant/account/import_inbox.py`
- Create: `tests/test_account_import_inbox.py`
- Modify: `tests/test_account_importer.py`

- [ ] **Step 1: 添加依赖声明测试和表格读取 RED 测试**

在 `tests/test_account_importer.py` 增加以下独立用例：

```python
def test_csv_file_accepts_gb18030_from_chinese_broker(tmp_path) -> None:
    source = tmp_path / "成交明细.csv"
    source.write_bytes(
        "证券名称,证券代码,成交数量,成交价格\n平安银行,000001,100,10.25\n".encode(
            "gb18030"
        )
    )
    rows, raw = read_broker_file(source)
    assert raw == source.read_bytes()
    assert rows[0]["证券代码"] == "000001"


def test_excel_read_failure_is_sanitized(tmp_path) -> None:
    source = tmp_path / "成交明细.xlsx"
    source.write_bytes(b"not-an-excel-workbook")
    with pytest.raises(ValueError, match="Excel import could not be read"):
        read_broker_file(source)
```

在 `tests/test_account_import_inbox.py` 写入这些具体行为测试：

```python
def test_scan_returns_only_supported_regular_direct_children(tmp_path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "成交.csv").write_text("证券代码,成交数量,成交价格\n000001,100,10\n", encoding="utf-8")
    (inbox / "说明.txt").write_text("ignore", encoding="utf-8")
    (inbox / "nested").mkdir()
    (inbox / "nested" / "隐藏.csv").write_text("x", encoding="utf-8")
    files = AccountImportInbox(inbox).scan()
    assert [item.file_name for item in files] == ["成交.csv"]


def test_scan_rejects_link_or_reparse_entry_when_supported_by_platform(tmp_path) -> None:
    inbox = tmp_path / "inbox"
    outside = tmp_path / "outside.csv"
    inbox.mkdir()
    outside.write_text("证券代码,成交数量,成交价格\n000001,100,10\n", encoding="utf-8")
    link = inbox / "linked.csv"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"link creation unavailable: {exc}")
    assert AccountImportInbox(inbox).scan() == ()


def test_file_id_cannot_be_used_after_file_content_changes(tmp_path) -> None:
    source = _write_fill_csv(tmp_path, price="10")
    inbox = AccountImportInbox(source.parent)
    discovered = inbox.scan()[0]
    preview = inbox.preview(discovered.file_id, default_trade_date=date(2026, 8, 11))
    source.write_text(
        "证券名称,证券代码,成交数量,成交价格\n平安银行,000001,100,11\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="file changed after preview"):
        inbox.verify_unchanged(preview)


def test_unknown_file_identifier_never_reads_an_arbitrary_path(tmp_path) -> None:
    inbox = AccountImportInbox(tmp_path)
    with pytest.raises(ValueError, match="unknown account import file"):
        inbox.preview("../../secrets.txt", default_trade_date=date(2026, 8, 11))
```

- [ ] **Step 2: 运行 RED 并记录正确失败原因**

Run: `.venv/Scripts/python.exe -m pytest tests/test_account_import_inbox.py tests/test_account_importer.py -q`

Expected: 新收件箱测试在导入 `a_share_quant.account.import_inbox` 时失败；GB18030 测试因现有读取器只接受 UTF-8 而失败。不得在看到这两个预期失败前修改生产代码。

- [ ] **Step 3: 最小实现表格读取和扫描边界**

在 `pyproject.toml` 的主依赖中加入：

```toml
"openpyxl>=3.1,<4",
"xlrd>=2,<3",
```

把 CSV 解码改成按固定顺序尝试且不静默替换字符：

```python
def _decode_csv(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("CSV encoding must be UTF-8 or GB18030")
```

在 `import_inbox.py` 实现：构造时 `Path(root).resolve(strict=False)`；最多返回 100 个文件；扩展名只允许 `.csv/.xlsx/.xls`；单文件上限沿用 25 MiB；解析后的表格最多 20,000 行、100 列；仅枚举根目录直接子项；拒绝 `is_symlink()`、`is_junction()` 和 Windows `FILE_ATTRIBUTE_REPARSE_POINT`；对每个文件读取前后比较 `(size, mtime_ns)`，然后以原始字节 SHA-256、文件名和状态生成不可逆 `file_id`。扫描缓存只保存 `file_id -> 已验证绝对路径与状态`，外部调用者不能传路径。

- [ ] **Step 4: 运行扫描与读取 GREEN**

Run: `.venv/Scripts/python.exe -m pytest tests/test_account_import_inbox.py tests/test_account_importer.py -q`

Expected: 本阶段所有测试通过；若 Windows 令牌无链接权限，只允许链接能力测试显示一条明确 skip。

- [ ] **Step 5: Commit Task 1**

```powershell
git add pyproject.toml src/a_share_quant/account/importer.py src/a_share_quant/account/import_inbox.py tests/test_account_importer.py tests/test_account_import_inbox.py
git commit -m "feat: add safe account import inbox"
```

---

### Task 2: 自动识别成交明细和持仓快照

**Files:**
- Modify: `src/a_share_quant/account/import_inbox.py`
- Modify: `src/a_share_quant/account/importer.py`
- Modify: `tests/test_account_import_inbox.py`

- [ ] **Step 1: 写入成交自动识别 RED 测试**

```python
def test_detects_common_chinese_fill_columns(tmp_path) -> None:
    source = tmp_path / "成交明细.csv"
    source.write_text(
        "证券名称,证券代码,买卖标志,成交数量,成交价格,成交日期\n"
        "平安银行,000001,买入,100,10.25,2026-08-11\n",
        encoding="utf-8",
    )
    inbox = AccountImportInbox(tmp_path)
    file = inbox.scan()[0]
    preview = inbox.preview(file.file_id, default_trade_date=date(2026, 8, 11))
    assert preview.kind is AccountImportKind.FILLS
    assert preview.detected_mapping == {
        "name": "证券名称",
        "symbol": "证券代码",
        "side": "买卖标志",
        "quantity": "成交数量",
        "price": "成交价格",
        "trade_date": "成交日期",
    }
    assert preview.fill_preview is not None
    assert preview.fill_preview.candidate_events[0].side is TradeSide.BUY
```

再加单独用例验证 `证券买入/B/BUY/买入` 和 `证券卖出/S/SELL/卖出`，未知方向必须拒绝该行而不能默认为买入；只有整份文件没有方向列时才能产生 `MISSING_SIDE_DEFAULTED_TO_BUY` 警告。

- [ ] **Step 2: 写入持仓自动识别 RED 测试**

```python
def test_detects_position_snapshot_without_creating_fill_events(tmp_path) -> None:
    source = tmp_path / "持仓.csv"
    source.write_text(
        "证券代码,证券名称,证券数量,可用数量,冻结数量,成本价,可用资金,日期\n"
        "000001,平安银行,300,200,100,10.1234,88000.50,2026-08-11\n",
        encoding="utf-8",
    )
    inbox = AccountImportInbox(tmp_path)
    file = inbox.scan()[0]
    preview = inbox.preview(file.file_id, default_trade_date=date(2026, 8, 11))
    assert preview.kind is AccountImportKind.POSITIONS
    assert preview.fill_preview is None
    assert preview.cash == Decimal("88000.50")
    assert preview.positions[0].total_quantity == 300
    assert preview.positions[0].available_quantity == 200
    assert preview.positions[0].frozen_quantity == 100
```

再加测试：负数、非整数数量、`available > total`、`frozen != total - available`、非有限成本价、同一代码重复行、同列出现不一致的现金/日期都进入拒绝或使预览失败；零持仓行可忽略但不得成为负持仓。

- [ ] **Step 3: 运行 RED**

Run: `.venv/Scripts/python.exe -m pytest tests/test_account_import_inbox.py -q`

Expected: 列检测、方向规范化和 `ImportedPosition` 尚不存在导致新增测试失败。

- [ ] **Step 4: 实现固定别名字典和无歧义检测**

实现以下别名，比较前只去除首尾空格，不做模糊包含匹配：

```python
ALIASES = {
    "name": ("证券名称", "证券简称", "股票名称", "名称", "name"),
    "symbol": ("证券代码", "股票代码", "代码", "symbol", "code"),
    "fill_quantity": ("成交数量", "发生数量", "成交股数", "quantity"),
    "fill_price": ("成交价格", "成交均价", "成交价", "price"),
    "side": ("买卖标志", "买卖方向", "操作", "业务名称", "side"),
    "trade_date": ("成交日期", "发生日期", "交易日期", "trade_date"),
    "position_quantity": ("证券数量", "股票余额", "持仓数量", "股份余额"),
    "available_quantity": ("可用数量", "可卖数量", "股份可用"),
    "frozen_quantity": ("冻结数量", "冻结股份"),
    "average_cost": ("成本价", "摊薄成本价", "参考成本价", "average_cost"),
    "cash": ("可用资金", "资金余额", "cash"),
    "as_of": ("日期", "数据日期", "持仓日期", "as_of"),
}
```

检测优先规则固定为：满足持仓的 `symbol + position_quantity + average_cost` 时识别为 `POSITIONS`；否则满足成交的 `symbol + fill_quantity + fill_price` 时识别为 `FILLS`；两种都不满足时返回包含“已发现列名”和“缺失语义字段”的安全错误，不输出单元格数据。两个原始列同时命中同一语义字段时视为歧义并失败关闭。

- [ ] **Step 5: 运行 GREEN 并提交**

Run: `.venv/Scripts/python.exe -m pytest tests/test_account_import_inbox.py tests/test_account_importer.py -q`

Expected: 全部通过。

```powershell
git add src/a_share_quant/account/import_inbox.py src/a_share_quant/account/importer.py tests/test_account_import_inbox.py
git commit -m "feat: detect broker export schemas"
```

---

### Task 3: 持久化独立的券商持仓快照

**Files:**
- Create: `src/a_share_quant/account/snapshot_store.py`
- Create: `tests/test_account_snapshot_store.py`

- [ ] **Step 1: 写入值对象和往返 RED 测试**

```python
def test_snapshot_store_round_trips_canonical_snapshot(tmp_path) -> None:
    path = tmp_path / "imported-account-snapshot.json"
    snapshot = ImportedAccountSnapshot(
        snapshot_id="snapshot-1",
        source_sha256="a" * 64,
        source_name="持仓.csv",
        as_of=date(2026, 8, 11),
        imported_at=datetime(2026, 8, 11, 3, tzinfo=timezone.utc),
        cash=Decimal("88000.50"),
        positions=(
            ImportedPosition(
                symbol="000001",
                name="平安银行",
                total_quantity=300,
                available_quantity=200,
                frozen_quantity=100,
                average_cost=Decimal("10.1234"),
            ),
        ),
    )
    store = AccountSnapshotStore(path)
    store.save(snapshot)
    assert store.load() == snapshot
```

- [ ] **Step 2: 写入损坏与原子性 RED 测试**

分别测试：修改 payload 后摘要不匹配；缺少/增加顶层键；重复证券代码；未来 `imported_at` 超过 5 分钟；文件超过 5 MiB；目标为符号链接/结点；`os.fsync` 或 `os.replace` 注入失败后旧快照保持不变且临时文件被清理。

- [ ] **Step 3: 运行 RED**

Run: `.venv/Scripts/python.exe -m pytest tests/test_account_snapshot_store.py -q`

Expected: `a_share_quant.account.snapshot_store` 不存在而失败。

- [ ] **Step 4: 实现严格值对象与摘要封装**

外层 JSON 只能包含：

```json
{"format_version":1,"payload":{},"payload_sha256":"64 lowercase hex characters"}
```

`payload_sha256` 是 UTF-8 编码、`ensure_ascii=False`、`sort_keys=True`、紧凑分隔符的 payload SHA-256。`save()` 在同目录创建命名临时文件，写入和目录创建后检查父链不是链接/结点，`flush()`、`os.fsync()`、再次检查目标边界、`os.replace()`；异常时删除临时文件并把底层路径细节转换为 `ValueError("account snapshot cannot be written")`。`load()` 只接受普通文件，先做大小限制，再校验完整键集、版本、摘要和所有字段。

- [ ] **Step 5: 运行 GREEN 并提交**

Run: `.venv/Scripts/python.exe -m pytest tests/test_account_snapshot_store.py -q`

Expected: 全部通过；只有真实链接能力测试可 skip。

```powershell
git add src/a_share_quant/account/snapshot_store.py tests/test_account_snapshot_store.py
git commit -m "feat: persist imported account snapshots"
```

---

### Task 4: 在账户服务中复用经验证的成交预览

**Files:**
- Modify: `src/a_share_quant/account/service.py`
- Modify: `tests/test_account_importer.py`

- [ ] **Step 1: 写入外部预览确认 RED 测试**

```python
def test_confirm_validated_preview_uses_same_idempotent_ledger_path(tmp_path) -> None:
    ledger = AccountLedger(initial_cash=Decimal("100000"))
    service = AccountEntryService(ledger=ledger, store=JsonlLedgerStore(tmp_path / "ledger.jsonl"))
    preview = preview_broker_rows(
        rows=[{"名称": "平安银行", "代码": "000001", "数量": "100", "价格": "10"}],
        mapping={"name": "名称", "symbol": "代码", "quantity": "数量", "price": "价格"},
        default_trade_date=date(2026, 8, 11),
        source_bytes=b"validated-file",
    )
    first = service.confirm_validated_preview(preview)
    second = service.confirm_validated_preview(preview)
    assert first[0].idempotent is False
    assert second[0].idempotent is True
    assert len(service.store.load_fills()) == 1
```

- [ ] **Step 2: 运行 RED**

Run: `.venv/Scripts/python.exe -m pytest tests/test_account_importer.py -q`

Expected: `confirm_validated_preview` 不存在。

- [ ] **Step 3: 提取现有确认实现**

新增：

```python
def confirm_validated_preview(self, preview: ImportPreview) -> tuple[LedgerReceipt, ...]:
    if not isinstance(preview, ImportPreview):
        raise TypeError("preview must be an ImportPreview")
    return self._confirm(preview)
```

现有 `confirm_preview(preview_id)` 只负责从 `_previews` 取值并调用同一个 `_confirm()`。不得增加跳过 prospective ledger 验证或直接批量写文件的第二条路径。

- [ ] **Step 4: 运行 GREEN 并提交**

Run: `.venv/Scripts/python.exe -m pytest tests/test_account_importer.py tests/test_account_ledger.py tests/test_account_store.py -q`

Expected: 全部通过。

```powershell
git add src/a_share_quant/account/service.py tests/test_account_importer.py
git commit -m "refactor: reuse validated account previews"
```

---

### Task 5: 组合一次性预览和确认工作流

**Files:**
- Modify: `src/a_share_quant/workbench/advisory_service.py`
- Create: `tests/test_account_import_workflow.py`
- Modify: `tests/test_advisory_service.py`

- [ ] **Step 1: 写入未确认不落盘 RED 测试**

```python
def test_fill_file_preview_does_not_write_before_confirmation(tmp_path) -> None:
    service, inbox_file, ledger_path, snapshot_path = _service_with_fill_export(tmp_path)
    listed = service.list_account_imports()
    assert listed["files"][0]["file_id"] == inbox_file.file_id
    preview = service.preview_account_import(inbox_file.file_id)
    assert preview["kind"] == "FILLS"
    assert not ledger_path.exists()
    assert not snapshot_path.exists()
```

- [ ] **Step 2: 写入两种确认和篡改 RED 测试**

```python
def test_fill_confirmation_is_one_time_and_file_import_is_idempotent(tmp_path) -> None:
    service, inbox_file, ledger_path, unused = _service_with_fill_export(tmp_path)
    token = service.preview_account_import(inbox_file.file_id)["confirmation_token"]
    result = service.confirm_account_import(token)
    assert result["kind"] == "FILLS"
    assert result["recorded_rows"] == 1
    with pytest.raises(ValueError, match="unknown or expired"):
        service.confirm_account_import(token)
    second_token = service.preview_account_import(service.list_account_imports()["files"][0]["file_id"])[
        "confirmation_token"
    ]
    assert service.confirm_account_import(second_token)["recorded_rows"] == 0
    assert len(JsonlLedgerStore(ledger_path).load_fills()) == 1


def test_position_confirmation_writes_snapshot_but_no_fill(tmp_path) -> None:
    service, inbox_file, ledger_path, snapshot_path = _service_with_position_export(tmp_path)
    token = service.preview_account_import(inbox_file.file_id)["confirmation_token"]
    result = service.confirm_account_import(token)
    assert result["kind"] == "POSITIONS"
    assert result["position_rows"] == 1
    assert not ledger_path.exists()
    assert AccountSnapshotStore(snapshot_path).load() is not None


def test_changed_source_invalidates_confirmation_token(tmp_path) -> None:
    service, inbox_file, ledger_path, snapshot_path = _service_with_fill_export(tmp_path)
    token = service.preview_account_import(inbox_file.file_id)["confirmation_token"]
    (tmp_path / "inbox" / inbox_file.file_name).write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="file changed after preview"):
        service.confirm_account_import(token)
    assert not ledger_path.exists()
    assert not snapshot_path.exists()
```

- [ ] **Step 3: 运行 RED**

Run: `.venv/Scripts/python.exe -m pytest tests/test_account_import_workflow.py -q`

Expected: `AdvisoryWorkbenchService` 没有收件箱参数和导入方法。

- [ ] **Step 4: 实现服务组合**

构造函数新增两个必须成对提供的可选依赖：

```python
account_import_inbox: AccountImportInbox | None = None,
account_snapshot_store: AccountSnapshotStore | None = None,
```

服务内部创建一个共享当前 `_ledger`/`_ledger_store` 的 `AccountEntryService`，并保存：

```python
@dataclass(frozen=True)
class _AccountImportConfirmation:
    confirmation_token: str
    preview: AccountFilePreview
```

所有 `list_account_imports()`、`preview_account_import(file_id)`、`confirm_account_import(token)` 在现有 `_manual_buy_lock` 下运行。预览响应只返回文件名、类型、映射、有效/拒绝行数、最多 100 条经过字段白名单处理的预览行、警告和令牌，不返回绝对路径。确认一开始就从字典 `pop` 令牌，因此失败后也必须重新预览；然后调用 `verify_unchanged()`。成交走 `confirm_validated_preview()`；持仓构造确定性 `snapshot_id = sha256(source_sha256 + as_of + canonical positions)` 并保存快照。

- [ ] **Step 5: 扩展 holdings 且保持向后兼容**

保留现有顶层 `cash/positions` 作为本机账本，新增：

```python
{
    "local_ledger": {
        "cash": "89994.90",
        "positions": [],
        "notice_zh": "本机账本来自已确认的人工成交或成交明细导入。",
    },
    "imported_account_snapshot": {
        "source_name": "持仓.csv",
        "source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "as_of": "2026-08-11",
        "cash": "88000.50",
        "positions": [],
        "notice_zh": "券商持仓快照独立展示，未伪造成历史成交。",
    },
}
```

没有快照时 `imported_account_snapshot` 为 `None`。`managed_local_files()` 增加 `imported-account-snapshot.json`，但不把它加入需要与账本共同恢复的 `account-state` 一致性组；它是独立数据源。

- [ ] **Step 6: 运行 GREEN 并提交**

Run: `.venv/Scripts/python.exe -m pytest tests/test_account_import_workflow.py tests/test_advisory_service.py tests/test_backup_restore.py -q`

Expected: 全部通过。

```powershell
git add src/a_share_quant/workbench/advisory_service.py tests/test_account_import_workflow.py tests/test_advisory_service.py
git commit -m "feat: add confirmed account import workflow"
```

---

### Task 6: 增加受保护的本地 HTTP 接口

**Files:**
- Modify: `src/a_share_quant/workbench/app.py`
- Create: `tests/test_account_import_http.py`
- Modify: `tests/test_workbench_app.py`

- [ ] **Step 1: 写入路由防护 RED 测试**

```python
def test_import_list_is_read_only_and_preview_rejects_paths(running_advisory_server) -> None:
    base = running_advisory_server
    with urlopen(f"{base}/api/advisory/imports", timeout=3) as response:
        payload = json.loads(response.read().decode("utf-8"))
    assert "files" in payload
    request = Request(
        f"{base}/api/advisory/import-preview",
        method="POST",
        headers={"Content-Type": "application/json", "X-Quant-Workbench-Request": "manual-advisory"},
        data=json.dumps({"path": "C:/secret.txt"}).encode("utf-8"),
    )
    with pytest.raises(HTTPError) as error:
        urlopen(request, timeout=3)
    assert error.value.code == 400
```

分别测试：缺请求头返回 403；非 JSON 返回 415；额外键/缺键返回 400；超长 body 返回 400；未知令牌返回净化后的 400；响应包含 `manual_execution_required: true`；任何错误均不泄露绝对路径或 Python 异常文本。

- [ ] **Step 2: 写入成功流程 RED 测试**

通过真实临时收件箱和 `AdvisoryWorkbenchService` 启动 `create_server(port=0)`，调用 list → preview(`file_id`) → confirm(`confirmation_token`)；断言确认后 holdings 可见数据，POST body 从未出现路径。

- [ ] **Step 3: 运行 RED**

Run: `.venv/Scripts/python.exe -m pytest tests/test_account_import_http.py tests/test_workbench_app.py -q`

Expected: 三个新路由不存在而返回 404。

- [ ] **Step 4: 添加精确路由**

```text
GET  /api/advisory/imports
POST /api/advisory/import-preview   body: {"file_id":"64 lowercase hex"}
POST /api/advisory/import-confirm   body: {"confirmation_token":"account-import-7f4d49f45f0e4f18aefdd4fa6f89a231"}
```

GET 通过 `_write_advisory_response`；POST 必须复用 `_manual_request_payload()`，并对 `set(payload)` 做完全匹配。不得加入接收 `path`、`directory`、账号或密码的接口。

- [ ] **Step 5: 运行 GREEN 并提交**

Run: `.venv/Scripts/python.exe -m pytest tests/test_account_import_http.py tests/test_workbench_app.py -q`

Expected: 全部通过。

```powershell
git add src/a_share_quant/workbench/app.py tests/test_account_import_http.py tests/test_workbench_app.py
git commit -m "feat: expose guarded account import routes"
```

---

### Task 7: 增加完整简体中文导入界面

**Files:**
- Modify: `src/a_share_quant/workbench/app.py`
- Modify: `tests/test_workbench_app.py`
- Modify: `tests/test_account_import_http.py`

- [ ] **Step 1: 写入 HTML 文案和安全渲染 RED 测试**

断言 `/advisory` 包含：`券商导出文件导入`、`扫描导出文件`、`生成预览`、`确认导入`、`只读取固定收件箱`、`不会登录或控制券商客户端`、`系统不会提交委托`。断言页面不包含英文操作标题 `Broker Import`、`Scan Files`、`Confirm Import`。用文件名 `<img src=x onerror=alert(1)>.csv` 的假服务响应验证页面 JavaScript 只通过 `esc()` 插入该值。

- [ ] **Step 2: 运行 RED**

Run: `.venv/Scripts/python.exe -m pytest tests/test_workbench_app.py tests/test_account_import_http.py -q`

Expected: 新卡片和 JavaScript 函数不存在。

- [ ] **Step 3: 实现中文卡片**

页面元素固定为：文件下拉框 `import-file`、扫描按钮、预览按钮、只读预览区域、确认按钮、消息区域。加载页面时仅调用 GET 列表；用户点击预览后才读取文件；用户点击确认后显示写入行数并重新加载持仓。确认按钮在没有有效令牌时 disabled，完成或失败后立即清空令牌。预览表最多显示 100 行并对所有值调用 `esc()`。

持仓卡片分为“本机成交账本”和“券商导入持仓快照”，不存在快照时显示“尚未确认导入券商持仓文件”，避免用户误认为本机账本就是券商真实持仓。

- [ ] **Step 4: 运行 GREEN 并提交**

Run: `.venv/Scripts/python.exe -m pytest tests/test_workbench_app.py tests/test_account_import_http.py -q`

Expected: 全部通过。

```powershell
git add src/a_share_quant/workbench/app.py tests/test_workbench_app.py tests/test_account_import_http.py
git commit -m "feat: add Chinese account import interface"
```

---

### Task 8: 接入 CLI、桌面快捷方式和备份清单

**Files:**
- Modify: `scripts/quant_cli.py`
- Modify: `scripts/start_quant_workbench.ps1`
- Modify: `.gitignore`
- Modify: `tests/test_launcher_security.py`
- Create: `tests/test_account_import_cli.py`

- [ ] **Step 1: 写入 CLI RED 测试**

使用 `importlib` 加载 `scripts/quant_cli.py`，断言 `workbench` 子命令接受 `--account-import-dir` 和 `--account-snapshot-path`；绝对路径仅能由本机命令行操作者提供，HTTP 无对应参数。通过 monkeypatch `run_server` 捕获服务，断言两个路径被注入 `AdvisoryWorkbenchService`。

- [ ] **Step 2: 写入启动器 RED 测试**

在 `tests/test_launcher_security.py` 断言：默认目录是 `.runtime/advisory/import-inbox`；PowerShell 使用 `New-Item -ItemType Directory -Force` 创建目录；参数数组包含 `--account-import-dir` 和 `--account-snapshot-path`；启动器仍只打开 `127.0.0.1` 两个页面；全文不含 `order_stock`、`order_send`、`place_order`、`0.0.0.0`、账号或密码参数。

- [ ] **Step 3: 运行 RED**

Run: `.venv/Scripts/python.exe -m pytest tests/test_account_import_cli.py tests/test_launcher_security.py -q`

Expected: CLI 参数和启动器路径不存在。

- [ ] **Step 4: 实现组合根**

CLI 新增：

```python
workbench.add_argument(
    "--account-import-dir",
    type=Path,
    default=Path(".runtime/advisory/import-inbox"),
)
workbench.add_argument(
    "--account-snapshot-path",
    type=Path,
    default=Path(".runtime/advisory/imported-account-snapshot.json"),
)
```

相对路径用现有 `_inside(repo_root, path)` 约束在仓库；显式绝对收件箱仅由 CLI 操作者配置，并在 `AccountImportInbox` 内再次解析和验证。快照路径必须在仓库 `.runtime` 内，避免备份管理范围漂移。

PowerShell 新增可选 `$AccountImportDirectory`，默认设置为上述目录并创建；快照路径保持内部固定，不要求用户填写。参数名使用 `account` 而不引入自动交易或券商连接概念。

- [ ] **Step 5: 运行 GREEN 并提交**

Run: `.venv/Scripts/python.exe -m pytest tests/test_account_import_cli.py tests/test_launcher_security.py tests/test_backup_restore.py -q`

Expected: 全部通过。

```powershell
git add scripts/quant_cli.py scripts/start_quant_workbench.ps1 .gitignore tests/test_account_import_cli.py tests/test_launcher_security.py
git commit -m "feat: wire desktop account import inbox"
```

---

### Task 9: 操作手册、验收脚本和真实格式适配入口

**Files:**
- Create: `docs/ACCOUNT_IMPORT_GUIDE_ZH.md`
- Modify: `README.md`
- Modify: `docs/OPEN_SOURCE_EVALUATION.md`
- Create: `scripts/run_account_import_acceptance.py`
- Create: `tests/test_account_import_acceptance.py`

- [ ] **Step 1: 写入离线端到端 RED 测试**

验收脚本接收 `--workspace` 临时目录，只生成不含真实账户信息的两份夹具：一份 GB18030 成交 CSV、一份 XLSX 持仓文件。脚本依次执行扫描、成交预览、确认、重复导入幂等、持仓预览、确认、重建服务、读取持仓；最后打印 JSON：

```json
{
  "status":"PASS",
  "fills_recorded":1,
  "duplicate_fills_recorded":0,
  "positions_loaded":1,
  "manual_execution_required":true,
  "order_capability_present":false
}
```

测试调用脚本 `main()`，断言完整结果且扫描源代码没有 `order_send/order_stock/place_order/submit_order` 可调用引用。

- [ ] **Step 2: 运行 RED**

Run: `.venv/Scripts/python.exe -m pytest tests/test_account_import_acceptance.py -q`

Expected: 验收脚本不存在。

- [ ] **Step 3: 编写脚本和中文手册**

手册必须逐屏写清：

1. 双击桌面“A股量化交易系统”。
2. 在财信客户端使用官方“导出/另存为”功能导出成交明细或持仓；不安装插件、不提供密码。
3. 保存到 `D:\量化交易\.runtime\advisory\import-inbox`。
4. 在“本地人工投顾”页扫描、预览、核对、确认。
5. 解释本机账本与券商快照为何分开，以及出现差异时以券商正式记录为准。
6. 列出支持列名、编码、文件大小、重复文件、文件修改、无法识别列名的排查方式。
7. 明确第一次真实适配时，用户唯一需要提供的是一份已经删除姓名、账号、股东号等敏感列的导出样例；系统只需表头和一两行脱敏结构。

`OPEN_SOURCE_EVALUATION.md` 记录只借鉴 MT5 官方终端桥的适配器边界，不复制 MetaQuotes 代码或协议；本实现使用本项目原创代码和公开库。

- [ ] **Step 4: 运行 GREEN 并提交**

Run: `.venv/Scripts/python.exe -m pytest tests/test_account_import_acceptance.py -q`

Expected: 通过并输出上述语义一致的 PASS JSON。

```powershell
git add docs/ACCOUNT_IMPORT_GUIDE_ZH.md README.md docs/OPEN_SOURCE_EVALUATION.md scripts/run_account_import_acceptance.py tests/test_account_import_acceptance.py
git commit -m "docs: add account import operator workflow"
```

---

### Task 10: 全量回归、实际桌面冒烟和交接报告

**Files:**
- Modify only if a failing regression has a new failing test first
- Create: `reports/ACCOUNT_IMPORT_HANDOVER_2026-08-11.md`

- [ ] **Step 1: 运行账户/工作台相关回归**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_account_import_inbox.py tests/test_account_snapshot_store.py tests/test_account_import_workflow.py tests/test_account_import_http.py tests/test_account_import_cli.py tests/test_account_import_acceptance.py tests/test_account_importer.py tests/test_account_ledger.py tests/test_account_store.py tests/test_advisory_service.py tests/test_workbench_app.py tests/test_launcher_security.py tests/test_backup_restore.py -q
```

Expected: 零失败；只允许测试明确报告的 Windows 链接权限 skip。

- [ ] **Step 2: 运行全量自动测试和静态检查**

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .
git diff --check
```

Expected: pytest 零失败；Ruff 输出 `All checks passed!`；`git diff --check` 无输出且退出码 0。

- [ ] **Step 3: 执行离线验收脚本**

```powershell
.venv\Scripts\python.exe scripts\run_account_import_acceptance.py --workspace .runtime\acceptance\account-import
```

Expected: `status=PASS`、成交首次 1 条、重复 0 条、持仓 1 条、永久人工执行、无下单能力。

- [ ] **Step 4: 执行本地 HTTP 冒烟**

在空闲回环端口启动工作台离线模式，把夹具复制到临时收件箱，通过真实 HTTP 完成 list → preview → confirm → holdings；停止并重启服务后验证账本和快照仍可加载。检查标准输出/错误日志不包含文件单元格原文、绝对收件箱路径、令牌或 Python traceback。

- [ ] **Step 5: 检查永久安全边界**

```powershell
git grep -n -E "order_send|order_stock|place_order|cancel_order|submit_order" -- src scripts
```

Expected: 只允许 `QmtReadOnlyAdapter.submit_order` 的永久拒绝方法和测试/验收中的禁止词检查；不得出现可达的交易 SDK 调用。

- [ ] **Step 6: 生成交接报告**

报告记录：提交列表、修改文件、RED→GREEN 证据、完整命令输出摘要、支持格式/列名、默认路径、真实财信格式仍需验证的字段、数据口径限制、剩余风险、恢复/备份范围、以及用户唯一手动步骤。不得写“预测准确”或“券商实时持仓已连接”；应写“经用户确认的导出快照”。

- [ ] **Step 7: 最终提交**

```powershell
git add reports/ACCOUNT_IMPORT_HANDOVER_2026-08-11.md
git commit -m "docs: hand over account import bridge"
git status --short
```

Expected: 报告提交成功，`git status --short` 无输出。

---

## Requirement-to-task traceability

| Requirement | Implemented by | Verified by |
|---|---|---|
| 公开行情继续自动获取 | 不改变现有实时 provider | 全量回归与桌面冒烟 |
| 固定目录自动发现文件 | Task 1, Task 8 | inbox 与 launcher tests |
| 财信常见中文格式识别 | Task 2 | schema detection tests |
| CSV/XLSX/XLS | Task 1 | importer/inbox tests |
| 未确认不得写入 | Task 5 | workflow tests |
| 成交幂等写入哈希账本 | Task 4, Task 5 | importer/workflow tests |
| 持仓快照不伪造成交 | Task 3, Task 5 | snapshot/workflow tests |
| 文件修改后预览失效 | Task 1, Task 5 | digest-change tests |
| 浏览器不能读取任意路径 | Task 1, Task 6 | unknown-ID/HTTP tests |
| 简体中文、容易上手 | Task 7, Task 9 | HTML tests 与操作手册 |
| 桌面快捷方式自动接入 | Task 8 | launcher tests |
| 无逆向、无自动下单 | 所有任务的 locked boundary | grep、安全回归、验收脚本 |
| 完整交接报告 | Task 10 | 报告内容清单 |

## Known limitation accepted by design

系统无法在没有财信官方 API 授权的情况下实时读取财信账户。文件收件箱把重复操作缩减为“在财信客户端执行一次官方导出并在网页确认”，但仍不是实时账户连接。第一次拿到真实财信导出文件后，若列名不在固定别名字典中，只允许增加明确别名和对应脱敏回归夹具；禁止改成模糊列名猜测、OCR、抓包或客户端逆向。
