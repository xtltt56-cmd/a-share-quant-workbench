from datetime import date, datetime, timezone

import pytest

from a_share_quant.research.prospective_competition import ProspectivePrediction
from a_share_quant.storage.project_storage import ProjectStoragePolicy
from a_share_quant.storage.prospective_ledger_store import (
    LedgerIntegrityError,
    ProspectiveLedgerStore,
)
from a_share_quant.workbench.research_summary import research_summary


def test_summary_distinguishes_overdue_from_settled_and_preserves_evidence(tmp_path):
    policy = ProjectStoragePolicy(tmp_path)
    store = ProspectiveLedgerStore(policy=policy)
    store.append_prediction(ProspectivePrediction(
        model_id='model', model_version='v1', config_hash='cfg', training_snapshot_hash='data',
        symbol='600001', name='测试股票', prediction_at=datetime(2020, 1, 2, tzinfo=timezone.utc),
        as_of=date(2020, 1, 2), horizon=5, score=0.7, probability=0.6,
        guidance_price_bands={'buy': (10, 11)}, maturity_date=date(2020, 1, 10),
    ))
    before = store.path.read_bytes()
    result = research_summary(tmp_path)
    assert result['prediction_count'] == result['overdue_count'] == 1
    assert result['settled_count'] == result['waiting_count'] == 0
    assert store.path.read_bytes() == before


def test_summary_never_repairs_damaged_tail(tmp_path):
    store = ProspectiveLedgerStore(policy=ProjectStoragePolicy(tmp_path))
    store.path.write_bytes(b'{broken')
    with pytest.raises(LedgerIntegrityError):
        research_summary(tmp_path)
    assert store.path.read_bytes() == b'{broken'


def test_summary_missing_does_not_create_ledger(tmp_path):
    assert research_summary(tmp_path)['status'] == 'MISSING'
    assert not (tmp_path / '.runtime').exists()
