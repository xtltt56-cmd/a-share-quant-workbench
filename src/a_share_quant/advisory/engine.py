"""Ordered, explainable advisory-state engine with no execution capability."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .contracts import AdvisoryState, ForecastRecord
from .risk import PortfolioRiskSnapshot, RiskEngine, RiskPolicy


def _decimal(value: Decimal | float | int | str, *, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{field} must be a decimal number") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field} must be finite")
    return parsed


@dataclass(frozen=True)
class AdvisoryContext:
    forecast: ForecastRecord
    portfolio: PortfolioRiskSnapshot
    data_quality: str
    tradeable: bool
    market_regime: str
    market_price: Decimal | float | int | str
    invalidation_price: Decimal | float | int | str
    industry: str
    liquidity_amount: Decimal | float | int | str
    event_risk: bool
    holding_quantity: int = 0
    available_quantity: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.forecast, ForecastRecord):
            raise TypeError("forecast must be a ForecastRecord")
        if not isinstance(self.portfolio, PortfolioRiskSnapshot):
            raise TypeError("portfolio must be a PortfolioRiskSnapshot")
        if not str(self.data_quality).strip():
            raise ValueError("data_quality is required")
        object.__setattr__(self, "data_quality", str(self.data_quality).strip().upper())
        object.__setattr__(self, "market_regime", str(self.market_regime).strip().upper())
        if not str(self.industry).strip():
            raise ValueError("industry is required")
        object.__setattr__(self, "industry", str(self.industry).strip())
        for field in ("market_price", "invalidation_price", "liquidity_amount"):
            value = _decimal(getattr(self, field), field=field)
            if value <= 0:
                raise ValueError(f"{field} must be positive")
            object.__setattr__(self, field, value)
        if self.holding_quantity < 0 or self.available_quantity < 0:
            raise ValueError("holding quantities must be non-negative")
        if self.available_quantity > self.holding_quantity:
            raise ValueError("available_quantity cannot exceed holding_quantity")


@dataclass(frozen=True)
class AdvisoryDecision:
    state: AdvisoryState
    action_zh: str
    reason_codes: tuple[str, ...]
    target_weight: Decimal
    suggested_quantity: int
    maximum_acceptable_price: Decimal | None
    invalidation_price: Decimal | None
    evidence_cutoff: str
    valid_until: date
    confidence: Decimal
    cancel_conditions: tuple[str, ...]
    explanation_zh: str
    manual_execution_required: bool = True


class AdvisoryEngine:
    def __init__(self, policy: RiskPolicy) -> None:
        self.policy = policy
        self.risk_engine = RiskEngine(policy)

    def evaluate(self, context: AdvisoryContext) -> AdvisoryDecision:
        if context.data_quality != "GOOD":
            return self._blocked(context, "DATA_QUALITY_NOT_GOOD")
        if not context.tradeable:
            return self._blocked(context, "NOT_TRADABLE")
        if context.event_risk:
            return self._watch(context, "EVENT_RISK")
        if context.forecast.uncertainty > self.policy.maximum_forecast_uncertainty:
            return self._insufficient(context, "FORECAST_UNCERTAINTY_TOO_HIGH")
        if context.holding_quantity > 0:
            return self._held_position_decision(context)
        if (
            context.forecast.predicted_return <= 0
            or context.forecast.predicted_probability < Decimal("0.55")
        ):
            return self._watch(context, "FORECAST_EDGE_NOT_MATERIAL")
        risk = self.risk_engine.assess_new_position(
            portfolio=context.portfolio,
            symbol=context.forecast.symbol,
            industry=context.industry,
            price=context.market_price,
            invalidation_price=context.invalidation_price,
            liquidity_amount=context.liquidity_amount,
            regime=context.market_regime,
        )
        if not risk.allowed:
            return self._blocked(context, *risk.reason_codes)
        maximum_price = (context.market_price * Decimal("1.01")).quantize(
            Decimal("0.0001"),
            rounding=ROUND_HALF_UP,
        )
        confidence = self._confidence(context.forecast)
        return AdvisoryDecision(
            state=AdvisoryState.BUY_CANDIDATE,
            action_zh="候选买入",
            reason_codes=(*risk.reason_codes, "FORMAL_FORECAST_AND_PORTFOLIO_GATES_PASSED"),
            target_weight=risk.target_weight,
            suggested_quantity=risk.suggested_quantity,
            maximum_acceptable_price=maximum_price,
            invalidation_price=context.invalidation_price,
            evidence_cutoff=context.forecast.data_cutoff.isoformat(),
            valid_until=context.forecast.maturity_date,
            confidence=confidence,
            cancel_conditions=(
                "数据质量不再为 GOOD",
                "市场进入风险关闭状态",
                "价格高于最大可接受价格",
                "价格跌破失效价",
                "出现重大事件风险",
            ),
            explanation_zh=(
                f"模型在 {context.forecast.horizon_days} 个交易日窗口给出正向预期，"
                f"建议仓位上限为 {risk.target_weight:.2%}；价格跌破"
                f"{context.invalidation_price} 即视为失效。"
            ),
        )

    def _held_position_decision(self, context: AdvisoryContext) -> AdvisoryDecision:
        if context.forecast.predicted_return <= 0:
            if context.available_quantity > 0:
                return self._decision(
                    context,
                    state=AdvisoryState.REDUCE,
                    action_zh="减仓",
                    reasons=("FORECAST_EDGE_NEGATIVE",),
                    explanation="预测优势消失且可卖数量可用，建议按风险计划减仓。",
                )
            return self._watch(context, "T_PLUS_ONE_SELL_NOT_AVAILABLE")
        return self._decision(
            context,
            state=AdvisoryState.HOLD,
            action_zh="持有观察",
            reasons=("EXISTING_POSITION_REMAINS_VALID",),
            explanation="当前持仓仍通过数据、模型和风险门槛，继续观察失效条件。",
        )

    def _blocked(self, context: AdvisoryContext, *reasons: str) -> AdvisoryDecision:
        return self._decision(
            context,
            state=AdvisoryState.BLOCKED,
            action_zh="暂不操作",
            reasons=reasons,
            explanation="关键数据或风险门槛未通过，系统不生成买入建议。",
        )

    def _watch(self, context: AdvisoryContext, *reasons: str) -> AdvisoryDecision:
        return self._decision(
            context,
            state=AdvisoryState.WATCH,
            action_zh="观察",
            reasons=reasons,
            explanation="证据不足以支持新的仓位，保持观察并等待条件改善。",
        )

    def _insufficient(self, context: AdvisoryContext, *reasons: str) -> AdvisoryDecision:
        return self._decision(
            context,
            state=AdvisoryState.INSUFFICIENT_DATA,
            action_zh="数据不足",
            reasons=reasons,
            explanation="预测不确定性过高，系统拒绝将其转化为交易建议。",
        )

    def _decision(
        self,
        context: AdvisoryContext,
        *,
        state: AdvisoryState,
        action_zh: str,
        reasons: tuple[str, ...],
        explanation: str,
    ) -> AdvisoryDecision:
        return AdvisoryDecision(
            state=state,
            action_zh=action_zh,
            reason_codes=reasons,
            target_weight=Decimal("0.0000"),
            suggested_quantity=0,
            maximum_acceptable_price=None,
            invalidation_price=None,
            evidence_cutoff=context.forecast.data_cutoff.isoformat(),
            valid_until=context.forecast.maturity_date,
            confidence=self._confidence(context.forecast),
            cancel_conditions=("数据质量改变", "风险状态改变", "模型证据失效"),
            explanation_zh=explanation,
        )

    @staticmethod
    def _confidence(forecast: ForecastRecord) -> Decimal:
        return (forecast.predicted_probability * (Decimal("1") - forecast.uncertainty)).quantize(
            Decimal("0.0001"),
            rounding=ROUND_HALF_UP,
        )
