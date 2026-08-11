"""Generate and persist the paper-only official daily candidate ranking."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from a_share_quant.research.daily_candidates import (
    generate_from_data_root,
    load_name_map,
)
from a_share_quant.storage.official_signal_store import OfficialSignalStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--data-root", type=Path, default=None)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".runtime/signals/official-daily.json"),
        help="durable JSON artifact loaded by the workbench",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("reports/official_daily_generation.md"),
    )
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()
    data_root = (args.data_root or repo_root / "data").resolve()
    output = _inside(repo_root, args.output)
    report = _inside(repo_root, args.report)
    signals = generate_from_data_root(
        data_root,
        top_k=args.top_k,
        name_map=load_name_map(data_root),
        now=datetime.now(timezone.utc),
    )
    OfficialSignalStore(path=output).put_signals(signals)
    write_generation_report(
        report,
        signals,
        data_root=data_root,
        output=output,
        repo_root=repo_root,
    )
    print(f"wrote {output}")
    print(f"wrote {report}")
    print(f"status=SUCCESS candidates={len(signals)} signal_date={signals[0].signal_date}")
    return 0


def _inside(root: Path, value: Path) -> Path:
    candidate = (value if value.is_absolute() else root / value).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"path must stay inside repo root: {value}")
    return candidate


def write_generation_report(
    path: Path,
    signals: tuple[object, ...],
    *,
    data_root: Path,
    output: Path,
    repo_root: Path | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    first = signals[0]
    lines = [
        "# 官方日选生成记录",
        "",
        (
            "> 这是基于本地 BaoStock 真实历史日线的固定权重横截面排序；"
            "它不是经过校准的收益预测，全部建议仍需人工复核。"
        ),
        "",
        f"- 状态：**成功**（{len(signals)} 条）",
        f"- 信号日期：`{first.signal_date}`",
        "- 数据源：`BaoStock`（免费公开历史日线）",
        "- 策略版本：`initial-free-data-v1`",
        "- 模型版本：`rule-ranking-v1`（非机器学习收益预测）",
        "- 估值因子：禁用（当前没有可验证的 PIT 基本面估值数据）",
        f"- 本地数据根目录：`{_display_path(data_root, repo_root)}`",
        f"- 持久化文件：`{_display_path(output, repo_root)}`",
        "",
        "## 候选",
        "",
        "| 排名 | 代码 | 名称 | 分数 | 收盘价 | 20日平均成交额 | 失效价 |",
        "|---:|---|---|---:|---:|---:|---:|",
    ]
    for signal in signals:
        lines.append(
            f"| {signal.rank} | {signal.symbol} | {signal.name} | "
            f"{signal.normalized_score:.2f} | {signal.reference_price:.4f} | "
            f"{signal.average_amount:.0f} | {signal.invalidation_price:.4f} |"
        )
    lines.extend(
        [
            "",
            "## 使用边界",
            "",
            "- 仅表示研究候选与观察顺序，不代表买入承诺或准确率保证。",
            "- 盘中行情缺失、未来时间戳、数据质量不是 GOOD 时，系统不得生成 READY。",
            "- 页面显示的所有成交仍由用户在财信证券客户端人工完成并登记。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _display_path(path: Path, repo_root: Path | None) -> str:
    if repo_root is None:
        return str(path)
    try:
        return str(path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
