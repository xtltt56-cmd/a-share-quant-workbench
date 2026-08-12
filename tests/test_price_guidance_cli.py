from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from a_share_quant.runtime.price_guidance import main


def test_price_guidance_generate_command_writes_auditable_artifact(tmp_path, monkeypatch) -> None:
    data_root = tmp_path / "data"
    bars_dir = data_root / "lake" / "daily_bars"
    bars_dir.mkdir(parents=True)
    rows = []
    for index in range(260):
        day = date(2025, 8, 1) + timedelta(days=index)
        value = 10 + index * 0.01
        rows.append(
            {
                "symbol": "000001",
                "date": day,
                "open": value,
                "high": value + 0.2,
                "low": value - 0.2,
                "close": value,
                "raw_close": value,
                "volume": 100000,
                "amount": 20000000,
                "source": "baostock",
            }
        )
    pd.DataFrame(rows).to_parquet(bars_dir / "000001.parquet", index=False)
    output = tmp_path / "guidance.json"
    rc = main(
        [
            "generate",
            "--data-root",
            str(data_root),
            "--output",
            str(output),
            "--calculation-date",
            "2026-04-15",
            "--valid-for",
            "2026-04-16",
            "--symbols",
            "000001",
        ]
    )
    assert rc == 0
    assert output.exists()
