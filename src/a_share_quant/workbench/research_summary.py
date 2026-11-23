"""Bounded, integrity-checked view of the existing prospective evidence ledger."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from a_share_quant.market.trading_calendar import AShareTradingCalendar, CalendarUnavailableError
from a_share_quant.runtime.research_worker import due_predictions
from a_share_quant.storage.atomic_json import read_checked_json
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
    settlements = ledger.settlements()
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    due_ids = {row.id for row in due_predictions(ledger, now)}
    latest = max((row.prediction_at for row in predictions), default=None)
    latest_settlement = max((row.outcome_at for row in settlements), default=None)
    missing_sessions = []
    try:
        calendar = AShareTradingCalendar()
        session = calendar.latest_completed_session(now)
        sampled = {row.as_of for row in predictions}
        earliest = min(sampled, default=session)
        for _ in range(20):
            if session <= earliest:
                break
            if session not in sampled and now >= calendar.prediction_deadline(session):
                missing_sessions.append(session.isoformat())
            session = calendar.previous_session(session)
    except CalendarUnavailableError:
        pass
    reason_counts: dict[str, int] = {}
    try:
        summary_path = policy.authorize(".runtime/research/settlement-summary.json")
        body = read_checked_json(policy.revalidate(summary_path), maximum_bytes=32_768)
        for item in body["deferred"]:
            if item["prediction_id"] in due_ids and isinstance(item["reason"], str):
                reason = item["reason"]
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return {
        'status': 'OK',
        'prediction_count': len(predictions),
        'settled_count': len(settlements),
        'waiting_count': len(predictions) - len(settlements) - len(due_ids),
        'overdue_count': len(due_ids),
        'settlement_reasons': reason_counts,
        'missed_sessions': missing_sessions,
        'latest_settlement_at': latest_settlement.isoformat() if latest_settlement else None,
        'delayed_count': len({row.prediction_id for row, _ in ledger.pending()}),
        'latest_prediction_at': latest.isoformat() if latest else None,
        'model_count': len({(row.model_id, row.model_version) for row in predictions}),
        'notice_zh': '累计前瞻账本记录；未结算不等于预测成功，不同版本不可直接比较。',
    }
