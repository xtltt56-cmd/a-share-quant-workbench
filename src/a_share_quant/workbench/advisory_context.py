"""Strict JSON loading for a locally produced advisory context artifact."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

from a_share_quant.advisory.contracts import ForecastRecord
from a_share_quant.advisory.engine import AdvisoryContext
from a_share_quant.advisory.risk import PortfolioRiskPosition, PortfolioRiskSnapshot
from a_share_quant.data.normalization import normalize_symbol


def load_advisory_context(path: str | Path) -> AdvisoryContext:
    """Load one model-produced context without accepting extra fields.

    The artifact is intentionally explicit: it contains the forecast, portfolio
    risk snapshot, and the market gates required by :class:`AdvisoryEngine`.
    Missing or malformed artifacts fail closed and never create a fallback signal.
    """

    artifact_path = Path(path)
    if artifact_path.is_symlink() or not artifact_path.is_file():
        raise ValueError("context artifact is not a regular file")
    try:
        raw = artifact_path.read_bytes()
        if not raw or len(raw) > 1_048_576:
            raise ValueError("context artifact has invalid size")
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("context artifact cannot be read") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("context artifact must be an object")
    _require_keys(
        payload,
        {
            "forecast",
            "portfolio",
            "data_quality",
            "tradeable",
            "market_regime",
            "market_price",
            "invalidation_price",
            "industry",
            "liquidity_amount",
            "event_risk",
        },
        "context artifact",
    )
    forecast_payload = _mapping(payload["forecast"], "forecast")
    forecast_fields = {
            "forecast_id",
            "symbol",
            "generated_at",
            "data_cutoff",
            "reference_price",
            "model_version",
            "data_version",
            "feature_version",
            "horizon_days",
            "maturity_date",
            "predicted_return",
            "predicted_probability",
            "predicted_rank",
            "uncertainty",
            "proposed_state",
            "report_id",
            "data_mode",
        }
    optional_forecast_fields = {
        "benchmark_symbol",
        "minimum_edge",
        "calibration_version",
        "interval_lower",
        "interval_upper",
        "interval_status",
        "artifact_sha256",
    }
    _require_keys(
        forecast_payload,
        forecast_fields | optional_forecast_fields,
        "forecast",
        allow_legacy=forecast_fields,
    )
    for field, default in {
        "benchmark_symbol": "",
        "minimum_edge": "0",
        "calibration_version": "",
        "interval_lower": None,
        "interval_upper": None,
        "interval_status": "UNCALIBRATED",
        "artifact_sha256": "",
    }.items():
        forecast_payload.setdefault(field, default)
    forecast = ForecastRecord(
        **{
            **forecast_payload,
            "generated_at": _datetime(forecast_payload["generated_at"], "generated_at"),
            "data_cutoff": _datetime(forecast_payload["data_cutoff"], "data_cutoff"),
            "maturity_date": _date(forecast_payload["maturity_date"], "maturity_date"),
        }
    )

    portfolio_payload = _mapping(payload["portfolio"], "portfolio")
    _require_keys(portfolio_payload, {"equity", "cash", "drawdown", "positions"}, "portfolio")
    positions_raw = portfolio_payload["positions"]
    if not isinstance(positions_raw, list):
        raise ValueError("portfolio.positions must be a list")
    positions: list[PortfolioRiskPosition] = []
    for item in positions_raw:
        position = _mapping(item, "portfolio position")
        _require_keys(position, {"symbol", "market_value", "industry"}, "portfolio position")
        positions.append(PortfolioRiskPosition(**position))
    portfolio = PortfolioRiskSnapshot(
        equity=portfolio_payload["equity"],
        cash=portfolio_payload["cash"],
        drawdown=portfolio_payload["drawdown"],
        positions=tuple(positions),
    )
    if not isinstance(payload["tradeable"], bool) or not isinstance(payload["event_risk"], bool):
        raise ValueError("tradeable and event_risk must be boolean")
    return AdvisoryContext(
        forecast=forecast,
        portfolio=portfolio,
        data_quality=payload["data_quality"],
        tradeable=payload["tradeable"],
        market_regime=payload["market_regime"],
        market_price=payload["market_price"],
        invalidation_price=payload["invalidation_price"],
        industry=payload["industry"],
        liquidity_amount=payload["liquidity_amount"],
        event_risk=payload["event_risk"],
        holding_quantity=payload.get("holding_quantity", 0),
        available_quantity=payload.get("available_quantity", 0),
    )


def load_instrument_map(path: str | Path) -> dict[str, str]:
    """Load a strict ``{symbol: Chinese name}`` catalog for ledger validation."""

    catalog_path = Path(path)
    if catalog_path.is_symlink() or not catalog_path.is_file():
        raise ValueError("instrument map is not a regular file")
    try:
        raw = catalog_path.read_bytes()
        if not raw or len(raw) > 1_048_576:
            raise ValueError("instrument map has invalid size")
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("instrument map cannot be read") from exc
    if not isinstance(payload, Mapping) or not payload:
        raise ValueError("instrument map must be a non-empty object")
    result: dict[str, str] = {}
    for symbol, name in payload.items():
        normalized = normalize_symbol(symbol)
        clean_name = str(name).strip()
        if not clean_name or normalized in result:
            raise ValueError("instrument map contains an invalid or duplicate entry")
        result[normalized] = clean_name
    return result


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _require_keys(
    payload: Mapping[str, Any],
    required: set[str],
    label: str,
    *,
    allow_legacy: set[str] | None = None,
) -> None:
    accepted = [required]
    if allow_legacy is not None:
        accepted.append(allow_legacy)
    if set(payload) not in accepted:
        raise ValueError(f"{label} context artifact fields are invalid")


def _datetime(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO datetime")
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO datetime") from exc


def _date(value: Any, label: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO date") from exc
