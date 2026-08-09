from datetime import date, datetime, timedelta, timezone

import pytest

from a_share_quant.data.provenance import CanonicalKey, DataEvidence, EvidenceLedger


def test_evidence_ledger_rejects_future_effective_time_and_duplicate_canonical_key() -> None:
    now = datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc)
    key = CanonicalKey(dataset="daily_bars", symbol="000001", effective_date=date(2026, 8, 8))
    ledger = EvidenceLedger()

    with pytest.raises(ValueError, match="effective_at"):
        ledger.append(
            DataEvidence(
                provider="akshare",
                canonical_key=key,
                fetched_at=now,
                effective_at=now + timedelta(seconds=1),
                content_sha256="a" * 64,
            )
        )

    evidence = DataEvidence(
        provider="akshare",
        canonical_key=key,
        fetched_at=now,
        effective_at=now - timedelta(minutes=1),
        content_sha256="a" * 64,
    )
    ledger.append(evidence)

    with pytest.raises(ValueError, match="duplicate canonical key"):
        ledger.append(evidence)
