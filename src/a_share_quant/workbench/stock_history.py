"""Bounded read-only chart data from the configured local market lake."""

import re
from pathlib import Path

import pandas as pd

from a_share_quant.config import Settings


def load_history(symbol: str, limit: str = "120", *, data_root: Path | None = None) -> dict:
    if not re.fullmatch(r"[0-9]{6}", symbol) or limit not in {"60", "120", "252", "600"}:
        raise ValueError("invalid symbol or range")
    root = ((data_root or Settings.load().data_dir) / "lake" / "daily_bars").resolve()
    path = root / f"{symbol}.parquet"
    if path.is_symlink() or path.resolve().parent != root:
        raise ValueError("invalid local file")
    if not path.exists():
        return {"symbol": symbol, "bars": [], "notice_zh": "尚未收集该股票的本地日线"}
    frame = pd.read_parquet(path)
    frame = frame.sort_values("date").drop_duplicates("date", keep="last").tail(int(limit))
    rows = []
    for row in frame.itertuples(index=False):
        values = [float(getattr(row, key)) for key in ("open", "close", "low", "high", "volume")]
        if not all(pd.notna(value) and abs(value) != float("inf") for value in values):
            continue
        rows.append(
            {
                "date": str(row.date)[:10],
                **dict(zip(("open", "close", "low", "high", "volume"), values, strict=True)),
            }
        )
    return {
        "symbol": symbol,
        "bars": rows,
        "source": str(frame.source.iloc[-1]) if len(frame) else "",
        "notice_zh": "本地历史日线；价格口径以数据版本为准，非实时分时行情",
        "data_version": (
            str(frame.data_version.iloc[-1]) if len(frame) and "data_version" in frame else "未知"
        ),
    }
