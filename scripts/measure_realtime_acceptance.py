"""Measure one complete A-share session from the running loopback workbench."""

from __future__ import annotations

import argparse
import json
import time
from datetime import date, datetime, timedelta
from datetime import time as market_time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener
from zoneinfo import ZoneInfo

from a_share_quant.market.trading_calendar import AShareTradingCalendar
from a_share_quant.storage.atomic_json import write_checked_json
from a_share_quant.storage.project_storage import ProjectStoragePolicy

SHANGHAI = ZoneInfo("Asia/Shanghai")
INTERVAL = timedelta(seconds=15)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=date.fromisoformat, required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not AShareTradingCalendar().is_session(args.session):
        parser.error("expected a valid port and verified trading session")
    policy = ProjectStoragePolicy(Path(__file__).resolve().parents[1])
    output = policy.authorize(f".runtime/acceptance/realtime-{args.session}.json")
    if output.exists():
        parser.error("an acceptance result already exists for this session")
    opening = datetime.combine(args.session, market_time(9, 30), SHANGHAI)
    if datetime.now(SHANGHAI) >= opening + INTERVAL:
        parser.error("start before or within the first 15 seconds of market open")
    route = f"http://127.0.0.1:{args.port}/api/workflow/health"
    http = build_opener(ProxyHandler({}))
    points = []
    for start, stop in ((market_time(9, 30), market_time(11, 30)),
                        (market_time(13), market_time(15))):
        slot = datetime.combine(args.session, start, SHANGHAI)
        finish = datetime.combine(args.session, stop, SHANGHAI)
        while slot < finish:
            wait = (slot - datetime.now(SHANGHAI)).total_seconds()
            if wait > 0:
                time.sleep(wait)
            point = {"at": slot.isoformat(), "status": "NOT_OBSERVED", "passed": False}
            try:
                if datetime.now(SHANGHAI) < slot + INTERVAL:
                    with http.open(route, timeout=5) as response:
                        stage = json.loads(response.read(512 * 1024))["stages"]["realtime"]
                    required, fresh = stage["priority_required"], stage["priority_fresh"]
                    point.update(
                        status=stage["status"], required=required, fresh=fresh,
                        market_request_ms=stage.get("full_market_request_ms"),
                        priority_request_ms=stage.get("priority_request_ms"),
                        market_quote_age_seconds=stage.get("data_age_seconds"),
                        passed=(stage["session"] == "OPEN" and required > 0
                                and fresh * 100 >= required * 95),
                    )
            except (OSError, ValueError, KeyError, TypeError):
                point["status"] = "WORKBENCH_UNAVAILABLE"
            points.append(point)
            slot += INTERVAL
    passed = sum(point["passed"] for point in points)
    result = {"session": args.session.isoformat(), "passed": passed, "expected": len(points),
              "target_met": passed * 100 >= len(points) * 95, "samples": points}
    write_checked_json(policy.revalidate(output), result, maximum_bytes=512 * 1024)
    print(f"{passed}/{len(points)} eligible samples, target_met={result['target_met']}")
    return 0 if result["target_met"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
