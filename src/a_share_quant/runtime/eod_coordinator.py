"""Workbench-scoped end-of-day refresh coordinator."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from a_share_quant.market.trading_calendar import AShareTradingCalendar, CalendarUnavailableError
from a_share_quant.storage.atomic_json import read_checked_json, write_checked_json


@dataclass(frozen=True)
class EODRefreshResult:
    success: bool
    status: str
    notice_zh: str
    input_summary: dict[str, object] | None = None


class EODCoordinator:
    def __init__(
        self,
        *,
        refresh: Callable[[date], EODRefreshResult | None],
        clock: Callable[[], datetime] | None = None,
        calendar: AShareTradingCalendar | None = None,
        close_time: time = time(15, 30),
        retry_base_seconds: float = 0,
        retry_max_seconds: float = 1800,
        maximum_attempts: int = 8,
        checkpoint_path: Path | None = None,
        completion_is_valid: Callable[[date], bool] | None = None,
    ) -> None:
        self.refresh = refresh
        self.clock = clock or (lambda: datetime.now(ZoneInfo("Asia/Shanghai")))
        self.calendar = calendar or AShareTradingCalendar()
        self.close_time = close_time
        if (
            not math.isfinite(retry_base_seconds) or not math.isfinite(retry_max_seconds)
            or retry_base_seconds < 0 or retry_max_seconds < retry_base_seconds
            or maximum_attempts < 1
        ):
            raise ValueError("invalid EOD retry limits")
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.maximum_attempts = maximum_attempts
        self._completed: set[date] = set()
        self.last_error: str | None = None
        self._pending_target: date | None = None
        self._attempts = 0
        self._next_retry: datetime | None = None
        self._last_attempt: datetime | None = None
        self._last_success: datetime | None = None
        self._last_success_day: date | None = None
        self._status = "NOT_RUN"
        self._notice_zh = "等待日线刷新。"
        self._error_code: str | None = None
        self._blocked_target: date | None = None
        self._lock = threading.RLock()
        self._run_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._request_revision = 0
        self._input_summary: dict[str, object] | None = None
        self.checkpoint_path = checkpoint_path
        self.completion_is_valid = completion_is_valid
        if checkpoint_path is not None and checkpoint_path.exists():
            self._restore()

    @property
    def stop_event(self) -> threading.Event:
        return self._stop

    def request_refresh(self) -> None:
        """Invalidate a completed session after an actual account-universe change."""
        current = self._now()
        target = self.calendar.latest_completed_session(current, close_time=self.close_time)
        with self._lock:
            if self._error_code == "CHECKPOINT_REJECTED":
                raise ValueError("rejected checkpoint cannot be overwritten")
            paused = self._status == "BLOCKED" and self._error_code in {
                "PERMISSION_DENIED", "CHECKPOINT_WRITE_FAILED",
            }
            self._request_revision += 1
            self._completed.discard(target)
            self._pending_target = target
            if not paused:
                self._attempts = 0
            self._next_retry = None
            if paused:
                # An account update is not operator approval to retry a denied
                # workflow, even if the target trading session has changed.
                self._notice_zh = "持仓范围已更新；日线任务仍暂停，修复原因并核验后再重试。"
            else:
                self._status = "PENDING"
                self._notice_zh = "持仓范围改变，等待补齐必要日线输入和持仓计划。"
            self._persist()

    def run_due(self) -> bool:
        if self._stop.is_set():
            return False
        try:
            current = self._now()
            day = current.date()
            target = (
                day
                if self.calendar.is_session(day) and current.time() >= self.close_time
                else self._pending_target
            )
        except CalendarUnavailableError:
            return self._calendar_failure()
        return self._run_target(target, current) if target is not None else False

    def retry_after_review(self) -> dict[str, object]:
        """Explicit local operator action; it does not bypass invalid checkpoints."""
        with self._lock:
            if self._error_code == "CHECKPOINT_REJECTED":
                raise ValueError("检查点校验失败，请先核验文件；不能直接覆盖该证据。")
            if self._stop.is_set():
                raise RuntimeError("系统正在退出，不能启动刷新。")
            if self._run_lock.locked():
                raise RuntimeError("刷新正在运行，请等待完成。")
            self._blocked_target = None
            self._status = "PENDING"
            self.last_error = self._error_code = None
            self.request_refresh()
            return self.snapshot()

    def run_initial(self) -> bool:
        """Refresh the latest completed session without delaying HTTP startup."""

        if self._stop.is_set():
            return False
        try:
            current = self._now()
            day = current.date()
            target = (
                day
                if self.calendar.is_session(day) and current.time() >= self.close_time
                else self.calendar.previous_session(day)
            )
        except CalendarUnavailableError:
            return self._calendar_failure()
        return self._run_target(target, current)

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("EOD clock must be timezone-aware")
        return value.astimezone(ZoneInfo("Asia/Shanghai"))

    def _calendar_failure(self) -> bool:
        with self._lock:
            self.last_error = self._error_code = "CALENDAR_UNAVAILABLE"
            self._status = "BLOCKED"
            self._notice_zh = "缺少该年份的正式交易日历，日线任务暂停；不会猜测交易日。"
            try:
                self._persist()
            except (OSError, ValueError):
                self._error_code = "CHECKPOINT_WRITE_FAILED"
        return False

    def _run_target(self, target: date, current: datetime) -> bool:
        if not self._run_lock.acquire(blocking=False):
            return False
        try:
            with self._lock:
                if (
                    target in self._completed
                    or target == self._blocked_target
                    or self._status == "BLOCKED"
                    and self._error_code in {"PERMISSION_DENIED", "CHECKPOINT_WRITE_FAILED"}
                    or self._stop.is_set()
                ):
                    return False
                if target != self._pending_target:
                    self._pending_target = target
                    self._attempts = 0
                    self._next_retry = None
                if (
                    self._attempts >= self.maximum_attempts
                    or self._next_retry is not None
                    and current < self._next_retry
                ):
                    return False
                self._attempts += 1
                self._last_attempt = current
                self._status = "RUNNING"
                self._notice_zh = f"正在刷新 {target.isoformat()} 的日线与候选。"
                revision = self._request_revision
                attempt_number = self._attempts
                # Write intent BEFORE network or durable publication. A killed process
                # therefore resumes as pending, never as a fabricated success.
                self._persist()
            try:
                result = self.refresh(target)
                # None is retained for legacy injected callbacks; production is explicit.
                if result is not None and not isinstance(result, EODRefreshResult):
                    raise TypeError("invalid EOD refresh result")
                if result is not None:
                    with self._lock:
                        self._input_summary = deepcopy(result.input_summary)
                if result is not None and not result.success:
                    with self._lock:
                        self.last_error = self._error_code = result.status
                        self._notice_zh = result.notice_zh
                    return self._record_failure(self._now(), revision, attempt_number)
            except Exception as exc:
                with self._lock:
                    self.last_error = str(exc)[:500]
                    self._error_code = (
                        "PERMISSION_DENIED" if isinstance(exc, PermissionError)
                        else type(exc).__name__
                    )
                    if isinstance(exc, PermissionError):
                        self._blocked_target = target
                        self._status = "BLOCKED"
                        self._next_retry = None
                        self._notice_zh = (
                            "文件权限不足，日线任务暂停；修复权限并核验后解除任务阻塞，"
                            "不会自动扩大权限。"
                        )
                        self._persist()
                        return False
                    self._notice_zh = "日线刷新失败，保留上次结果并按限额重试。"
                return self._record_failure(self._now(), revision, attempt_number)
            with self._lock:
                self.last_error = self._error_code = None
                if revision == self._request_revision:
                    self._completed.add(target)
                    self._pending_target = None
                self._next_retry = None
                self._last_success = self._now()
                self._last_success_day = target
                self._status = "SUCCESS" if self._pending_target is None else "PENDING"
                self._notice_zh = result.notice_zh if result is not None else "日线刷新完成。"
                self._persist()
            return True
        except (OSError, ValueError) as exc:
            with self._lock:
                self._completed.discard(target)
                self._pending_target = target
                self._status = "BLOCKED"
                self._blocked_target = target
                self._error_code = "CHECKPOINT_WRITE_FAILED"
                self.last_error = type(exc).__name__
                self._notice_zh = "任务状态无法可靠保存，已暂停；不会把未保存的任务标为完成。"
            return False
        finally:
            self._run_lock.release()

    def _record_failure(self, current: datetime, revision: int, attempt_number: int) -> bool:
        with self._lock:
            if revision != self._request_revision:
                self._status = "PENDING"
                self._persist()
                return False
            exhausted = self._attempts >= self.maximum_attempts
            self._status = "RETRY_EXHAUSTED" if exhausted else "FAILED"
            delay = min(self.retry_max_seconds, self.retry_base_seconds * 2 ** (attempt_number - 1))
            self._next_retry = None if exhausted else current + timedelta(seconds=delay)
            self._persist()
        return False

    def _persist(self) -> None:
        if self.checkpoint_path is None:
            return
        body = {
            "format_version": 1, **self.snapshot(),
            "completed": [day.isoformat() for day in sorted(self._completed)[-64:]],
            "blocked_target": (
                self._blocked_target.isoformat() if self._blocked_target else None
            ),
        }
        write_checked_json(self.checkpoint_path, body, maximum_bytes=262144)

    def _restore(self) -> None:
        assert self.checkpoint_path is not None
        try:
            body = read_checked_json(self.checkpoint_path, maximum_bytes=262144)
            if body["format_version"] != 1 or not isinstance(body["attempts"], int):
                raise ValueError("unsupported checkpoint")
            completed = {date.fromisoformat(value) for value in body["completed"]}
            if len(completed) > 64 or not 0 <= body["attempts"] <= self.maximum_attempts:
                raise ValueError("invalid checkpoint attempts")
            if self.completion_is_valid is not None:
                completed = {day for day in completed if self.completion_is_valid(day)}
            self._completed = completed
            self._pending_target = _date_or_none(body["target_session"])
            self._blocked_target = _date_or_none(body["blocked_target"])
            self._attempts = body["attempts"]
            self._next_retry = _datetime_or_none(body["next_retry_at"])
            self._last_attempt = _datetime_or_none(body["last_attempt_at"])
            self._last_success = _datetime_or_none(body["last_success_at"])
            self._last_success_day = _date_or_none(body["last_success_session"])
            if self._last_attempt is not None and self._now() < self._last_attempt:
                raise ValueError("checkpoint clock is ahead of current time")
            self._status = body["status"]
            self._notice_zh = body["notice_zh"]
            self._error_code = body["error_code"]
            self._input_summary = body.get("input_summary")
            if self._status == "RUNNING":
                self._status = "PENDING"
                self._notice_zh = "上次运行中断，等待按原目标和重试限额恢复。"
        except (OSError, ValueError, TypeError, KeyError):
            # Do not overwrite the rejected file; operator can inspect/recover it.
            self._status = "BLOCKED"
            self._notice_zh = "任务检查点校验失败，已暂停；需核验该文件后恢复。"
            self._error_code = "CHECKPOINT_REJECTED"
            self._stop.set()

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "status": self._status,
                "notice_zh": self._notice_zh,
                "target_session": self._pending_target.isoformat()
                if self._pending_target
                else None,
                "attempts": self._attempts,
                "maximum_attempts": self.maximum_attempts,
                "last_attempt_at": self._last_attempt.isoformat() if self._last_attempt else None,
                "last_success_at": self._last_success.isoformat() if self._last_success else None,
                "last_success_session": (
                    self._last_success_day.isoformat() if self._last_success_day else None
                ),
                "next_retry_at": self._next_retry.isoformat() if self._next_retry else None,
                "error_code": self._error_code,
                "input_summary": deepcopy(self._input_summary),
            }

    def start(self, *, interval_seconds: float = 60.0) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        if self._error_code == "CHECKPOINT_REJECTED":
            self._stop.set()
            return

        def worker() -> None:
            self.run_initial()
            while not self._stop.wait(interval_seconds):
                self.run_due()

        self._thread = threading.Thread(target=worker, name="quant-eod-refresh", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        if self._thread is not None and not self._thread.is_alive():
            self._thread = None


def _date_or_none(value: str | None) -> date | None:
    return date.fromisoformat(value) if value is not None else None


def _datetime_or_none(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("checkpoint timestamps must be aware")
    return parsed


__all__ = ["EODCoordinator", "EODRefreshResult"]
