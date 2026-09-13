"""Bounded, integrity-checked view of the existing prospective evidence ledger."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from a_share_quant.storage.project_storage import ProjectStoragePolicy
from a_share_quant.storage.prospective_ledger_store import ProspectiveLedgerStore


def research_summary(root: Path | None = None) -> dict:
    policy = ProjectStoragePolicy(root or Path(__file__).resolve().parents[3])
    path = policy.authorize('.runtime/research/prospective/predictions.jsonl')
    if not path.exists():
        return {'status': 'MISSING', 'notice_zh': '尚未建立前瞻预测账本'}
    if path.stat().st_size > 16 * 1024 * 1024:
        return {'status': 'UNAVAILABLE', 'notice_zh': '预测账本超出在线读取上限，需生成汇总后查看'}
    ledger = ProspectiveLedgerStore(path, policy=policy, recover_incomplete_tail=False)
    predictions = ledger.predictions()
    settled = {row.prediction_id for row in ledger.settlements()}
    today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
    waiting = [row for row in predictions if row.id not in settled]
    overdue = sum(row.maturity_date is not None and row.maturity_date <= today for row in waiting)
    latest = max((row.prediction_at for row in predictions), default=None)
    return {
        'status': 'OK',
        'prediction_count': len(predictions),
        'settled_count': len(settled),
        'waiting_count': len(waiting) - overdue,
        'overdue_count': overdue,
        'delayed_count': len({row.prediction_id for row, _ in ledger.pending()}),
        'latest_prediction_at': latest.isoformat() if latest else None,
        'model_count': len({(row.model_id, row.model_version) for row in predictions}),
        'notice_zh': '累计前瞻账本记录；未结算不等于预测成功，不同版本不可直接比较。',
    }
