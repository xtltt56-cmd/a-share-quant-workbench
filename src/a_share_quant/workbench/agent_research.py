"""Supplementary public research from existing local bars and announcement evidence."""

from __future__ import annotations

import math
import statistics
from datetime import date
from typing import Any

from a_share_quant.workbench.stock_history import load_history


def build_research(context: dict[str, Any]) -> dict[str, Any]:
    cutoff = min(value for value in (context.get("input_cutoff"),
                                    context.get("expected_session"),
                                    context["observed_at"][:10]) if value)
    metrics, observations, missing = {}, [], []
    source, version, bars = None, None, []
    try:
        history = load_history(context["symbol"], "252")
        source, version = history.get("source"), history.get("data_version")
        for row in history["bars"]:
            date.fromisoformat(row["date"])
            if (row["date"] <= cutoff and row["close"] > 0 and row["open"] > 0
                and 0 < row["low"] <= min(row["open"], row["close"])
                and row["high"] >= max(row["open"], row["close"])
                and row["volume"] >= 0):
                bars.append(row)
    except (OSError, ValueError, KeyError, TypeError):
        missing.append("本地历史日线无法读取或格式未通过核验。")
    if len(bars) < 21:
        missing.append("有效历史日线不足，无法计算补充趋势和风险指标。")
    else:
        closes = [row["close"] for row in bars]

        def metric(key, label, value, unit):
            if math.isfinite(value):
                metrics[key] = {"label": label, "value": round(value, 4), "unit": unit}

        change = (closes[-1] / closes[-21] - 1) * 100
        returns = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - 20, len(closes))]
        metric("change_20", "近20个交易日价格变化", change, "%")
        metric("volatility_20", "近20日历史波动（年化）", statistics.stdev(returns)
               * math.sqrt(252) * 100, "%")
        peak, drawdown = closes[-min(60, len(closes))], 0.0
        for close in closes[-60:]:
            peak = max(peak, close)
            drawdown = min(drawdown, close / peak - 1)
        metric("drawdown", "样本最近至多60日最大回撤", drawdown * 100, "%")
        gaps = [abs(bars[i]["open"] / closes[i - 1] - 1) * 100
                for i in range(len(bars) - 20, len(bars))]
        metric("largest_gap_20", "近20日最大开盘跳空幅度", max(gaps), "%")
        observations.append("短期历史价格变化为正。" if change > 0
                            else "短期历史价格未形成正向变化。")
        if len(bars) >= 61:
            medium = (closes[-1] / closes[-61] - 1) * 100
            metric("change_60", "近60个交易日价格变化", medium, "%")
            observations.append("短期与中期历史价格方向一致。" if (change > 0) == (medium > 0)
                                else "短期与中期历史价格方向存在分歧。")
        if len(bars) >= 25:
            old_volume = statistics.mean(row["volume"] for row in bars[-25:-5])
            if old_volume > 0:
                ratio = statistics.mean(row["volume"] for row in bars[-5:]) / old_volume
                metric("volume_ratio", "最近5日成交量 / 此前20日均量", ratio, "倍")
                observations.append("近期成交活跃度高于此前样本。" if ratio > 1
                                    else "近期成交活跃度未高于此前样本。")
    risk = context.get("event_risk") or {}
    announcements = [{key: event.get(key) for key in
                      ("title", "announced_at", "source", "source_url", "level")}
                     for event in (risk.get("events") or [])[:10]
                     if isinstance(event, dict)]
    if not announcements:
        missing.append("当前没有可引用的公告标题证据。")
    missing.extend(["公告证据仅包含标题和原文链接，尚未核验公告全文。",
                    "当前没有财报估值、行业对比和宏观事件的完整研究证据。"])
    return {"schema_version": 1, "tool": "get_stock_research", "symbol": context["symbol"],
            "name": context["name"], "input_cutoff": cutoff,
            "history_cutoff": bars[-1]["date"] if bars else None,
            "history_source": source, "price_version": version, "sample_count": len(bars),
            "metrics": metrics, "observations": observations, "announcements": announcements,
            "announcement_checked_at": risk.get("checked_at"),
            "missing_research": missing,
            "method_notice_zh": "复用已采集日线；价格变化不是总回报，除权除息可能影响指标。"
                                "历史样本只用于当前研究，不代表未来预测胜率。",
            "observed_at": context["observed_at"]}
