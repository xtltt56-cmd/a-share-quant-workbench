from pathlib import Path

import numpy as np
import pandas as pd

from a_share_quant.integrations.qlib.provider import QlibProviderAdapter


def test_provider_adapter_writes_qlib_calendar_instruments_and_feature_bins(tmp_path: Path) -> None:
    bars = pd.DataFrame(
        [
            {
                "symbol": "000001",
                "date": "2026-01-02",
                "open": 10.0,
                "high": 11.0,
                "low": 9.8,
                "close": 10.5,
                "volume": 1000,
                "amount": 10500,
            },
            {
                "symbol": "000001",
                "date": "2026-01-05",
                "open": 10.5,
                "high": 11.2,
                "low": 10.4,
                "close": 11.0,
                "volume": 1100,
                "amount": 11800,
            },
        ]
    )

    artifact = QlibProviderAdapter(tmp_path).build(bars)

    assert artifact.root == tmp_path
    assert (tmp_path / "calendars" / "day.txt").read_text(encoding="utf-8").splitlines() == [
        "2026-01-02",
        "2026-01-05",
    ]
    assert (tmp_path / "instruments" / "all.txt").read_text(encoding="utf-8").startswith(
        "000001\t2026-01-02\t2026-01-05"
    )
    close_bin = np.fromfile(tmp_path / "features" / "000001" / "close.day.bin", dtype="<f4")
    assert close_bin.tolist() == [0.0, 10.5, 11.0]
    assert artifact.dataset_hash

