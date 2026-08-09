"""Conservative A-share portfolio risk gates for advisory-only decisions."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any

import yaml

from a_share_quant.data.normalization import normalize_symbol

_WEIGHT = Decimal("0.0001")


def _decimal(value: Decimal | float | int | str, *, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{field} must be a decimal number") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field} must be finite")
    return parsed


@dataclass(frozen=True)
class PortfolioRiskPosition:
    symbol: str
    market_value: Decimal | float | int | str
    industry: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        value = _decimal(self.market_value, field="market_value")
        if value < 0:
            raise ValueError("market_value must be non-negative")
        if not str(self.industry).strip():
            raise ValueError("industry is required")
        object.__setattr__(self, "market_value", value)
        object.__setattr__(self, "industry", str(self.industry).strip())


@dataclass(frozen=True)
class PortfolioRiskSnapshot:
    equity: Decimal | float | int | str
    cash: Decimal | float | int | str
    drawdown: Decimal | float | int | str
    positions: tuple[PortfolioRiskPosition, ...]

    def __post_init__(self) -> None:
        equity = _decimal(self.equity, field="equity")
        cash = _decimal(self.cash, field="cash")
        drawdown = _decimal(self.drawdown, field="drawdown")
        if equity <= 0:
            raise ValueError("equity must be positive")
        if cash < 0:
            raise ValueError("cash must be non-negative")
        if not Decimal("0") <= drawdown < Decimal("1"):
            raise ValueError("drawdown must be between zero and one")
        if not all(isinstance(item, PortfolioRiskPosition) for item in self.positions):
            raise TypeError("positions must contain PortfolioRiskPosition records")
        object.__setattr__(self, "equity", equity)
        object.__setattr__(self, "cash", cash)
        object.__setattr__(self, "drawdown", drawdown)

    @property
    def gross_exposure(self) -> Decimal:
        value = sum((item.market_value for item in self.positions), Decimal("0")) / self.equity
        return value.quantize(_WEIGHT, rounding=ROUND_DOWN)

    def industry_exposure(self, industry: str) -> Decimal:
        value = sum(
            (item.market_value for item in self.positions if item.industry == industry),
            Decimal("0"),
        ) / self.equity
        return value.quantize(_WEIGHT, rounding=ROUND_DOWN)


@dataclass(frozen=True)
class RiskPolicy:
    normal_gross_cap: Decimal = Decimal("0.70")
    cautious_gross_cap: Decimal = Decimal("0.40")
    risk_off_gross_cap: Decimal = Decimal("0.20")
    initial_position_cap: Decimal = Decimal("0.04")
    absolute_single_name_cap: Decimal = Decimal("0.08")
    industry_cap: Decimal = Decimal("0.25")
    minimum_cash_reserve: Decimal = Decimal("0.30")
    planned_risk_per_position: Decimal = Decimal("0.0075")
    maximum_positions: int = 10
    minimum_liquidity_amount: Decimal = Decimal("10000000")
    maximum_forecast_uncertainty: Decimal = Decimal("0.35")
    drawdown_caution: Decimal = Decimal("0.06")
    drawdown_new_buy_freeze: Decimal = Decimal("0.08")
    drawdown_protection: Decimal = Decimal("0.12")
    lot_size: int = 100

    @classmethod
    def conservative(cls) -> RiskPolicy:
        return cls()

    @classmethod
    def from_yaml(cls, path: Path) -> RiskPolicy:
        with Path(path).open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        section = raw.get("advisory", {})
        if not isinstance(section, dict):
            raise ValueError("risk advisory configuration must be a mapping")
        allowed = set(cls.__dataclass_fields__)
        values: dict[str, Any] = {
            key: value for key, value in section.items() if key in allowed
        }
        for key in ("maximum_positions", "lot_size"):
            if key in values:
                values[key] = int(values[key])
        return cls(**values)

    def __post_init__(self) -> None:
        decimal_fields = (
            "normal_gross_cap",
            "cautious_gross_cap",
            "risk_off_gross_cap",
            "initial_position_cap",
            "absolute_single_name_cap",
            "industry_cap",
            "minimum_cash_reserve",
            "planned_risk_per_position",
            "maximum_forecast_uncertainty",
            "drawdown_caution",
            "drawdown_new_buy_freeze",
            "drawdown_protection",
        )
        for field in decimal_fields:
            value = _decimal(getattr(self, field), field=field)
            if not Decimal("0") <= value <= Decimal("1"):
                raise ValueError(f"{field} must be between zero and one")
            object.__setattr__(self, field, value)
        liquidity = _decimal(self.minimum_liquidity_amount, field="minimum_liquidity_amount")
        if liquidity <= 0:
            raise ValueError("minimum_liquidity_amount must be positive")
        object.__setattr__(self, "minimum_liquidity_amount", liquidity)
        if self.maximum_positions < 1 or self.lot_size < 1:
            raise ValueError("maximum_positions and lot_size must be positive")
        if not self.drawdown_caution <= self.drawdown_new_buy_freeze <= self.drawdown_protection:
            raise ValueError("drawdown thresholds must be ordered")

    def gross_cap_for(self, regime: str) -> Decimal:
        normalized = str(regime).strip().upper()
        if normalized == "NORMAL":
            return self.normal_gross_cap
        if normalized == "CAUTIOUS":
            return self.cautious_gross_cap
        if normalized == "RISK_OFF":
            return self.risk_off_gross_cap
        raise ValueError("regime must be NORMAL, CAUTIOUS, or RISK_OFF")


@dataclass(frozen=True)
class RiskAssessment:
    allowed: bool
    target_weight: Decimal
    suggested_quantity: int
    reason_codes: tuple[str, ...]


class RiskEngine:
    def __init__(self, policy: RiskPolicy) -> None:
        self.policy = policy

    def assess_new_position(
        self,
        *,
        portfolio: PortfolioRiskSnapshot,
        symbol: str,
        industry: str,
        price: Decimal | float | int | str,
        invalidation_price: Decimal | float | int | str,
        liquidity_amount: Decimal | float | int | str,
        regime: str,
    ) -> RiskAssessment:
        market_price = _decimal(price, field="price")
        invalidation = _decimal(invalidation_price, field="invalidation_price")
        liquidity = _decimal(liquidity_amount, field="liquidity_amount")
        if market_price <= 0 or invalidation <= 0 or invalidation >= market_price:
            return _blocked("INVALID_INVALIDATION")
        if liquidity < self.policy.minimum_liquidity_amount:
            return _blocked("LIQUIDITY_TOO_LOW")
        regime_name = str(regime).strip().upper()
        if regime_name == "RISK_OFF":
            return _blocked("RISK_OFF")
        gross_cap = self.policy.gross_cap_for(regime_name)
        if portfolio.drawdown >= self.policy.drawdown_protection:
            return _blocked("PROTECTION_MODE")
        if portfolio.drawdown >= self.policy.drawdown_new_buy_freeze:
            return _blocked("DRAWDOWN_NEW_BUY_FREEZE")
        if len(portfolio.positions) >= self.policy.maximum_positions:
            return _blocked("MAX_POSITIONS")
        gross_capacity = gross_cap - portfolio.gross_exposure
        cash_capacity = portfolio.cash / portfolio.equity - self.policy.minimum_cash_reserve
        industry_capacity = self.policy.industry_cap - portfolio.industry_exposure(industry)
        risk_distance = Decimal("1") - invalidation / market_price
        risk_sized_weight = self.policy.planned_risk_per_position / risk_distance
        target_weight = min(
            self.policy.initial_position_cap,
            self.policy.absolute_single_name_cap,
            gross_capacity,
            cash_capacity,
            industry_capacity,
            risk_sized_weight,
        )
        if target_weight <= 0:
            return _blocked("PORTFOLIO_CAPACITY_EXHAUSTED")
        raw_quantity = portfolio.equity * target_weight / market_price
        quantity = int(raw_quantity // self.policy.lot_size) * self.policy.lot_size
        if quantity < self.policy.lot_size:
            return _blocked("INSUFFICIENT_CAPACITY_FOR_LOT")
        actual_weight = (Decimal(quantity) * market_price / portfolio.equity).quantize(
            _WEIGHT,
            rounding=ROUND_DOWN,
        )
        reasons = ["ALL_RISK_GATES_PASSED"]
        if portfolio.drawdown >= self.policy.drawdown_caution:
            reasons.append("DRAWDOWN_CAUTION")
        return RiskAssessment(
            allowed=True,
            target_weight=actual_weight,
            suggested_quantity=quantity,
            reason_codes=tuple(reasons),
        )


def _blocked(reason: str) -> RiskAssessment:
    return RiskAssessment(
        allowed=False,
        target_weight=Decimal("0.0000"),
        suggested_quantity=0,
        reason_codes=(reason,),
    )
