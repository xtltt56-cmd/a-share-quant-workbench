"""Deterministic research fallback used when VectorBT is unavailable."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from a_share_quant.contracts.stage3 import ExecutionSpec, SignalFrame
from a_share_quant.strategies.contracts import PortfolioSpec, PortfolioStrategy

from .contracts import BacktestResult
from .turnover import compute_turnover


def _as_date(value: Any) -> date:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid date: {value!r}")
    return parsed.date()


def _prepare_bars(market_data: pd.DataFrame, period: Any | None) -> pd.DataFrame:
    if not isinstance(market_data, pd.DataFrame):
        raise TypeError("market_data must be a pandas DataFrame")
    required = {"date", "symbol", "close"}
    missing = sorted(required.difference(market_data.columns))
    if missing:
        raise ValueError(f"market_data is missing fields: {', '.join(missing)}")
    bars = market_data.copy()
    bars["date"] = bars["date"].map(_as_date)
    bars["symbol"] = bars["symbol"].astype(str)
    bars["close"] = pd.to_numeric(bars["close"], errors="coerce")
    bars = bars.loc[np.isfinite(bars["close"])].copy()
    if "open" not in bars:
        bars["open"] = bars["close"]
    for field in ("is_suspended", "is_limit_up", "is_limit_down"):
        if field not in bars:
            bars[field] = False
        bars[field] = bars[field].fillna(False).astype(bool)
    if period is not None:
        bars = bars.loc[
            (bars["date"] >= period.start_date) & (bars["date"] <= period.end_date)
        ]
    if bars.empty:
        raise ValueError("market_data is empty after period filters")
    return bars.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)


def _benchmark_returns(benchmark: pd.DataFrame | pd.Series | None) -> pd.Series:
    if benchmark is None:
        return pd.Series(dtype=float, name="benchmark_returns")
    if isinstance(benchmark, pd.Series):
        values = pd.to_numeric(benchmark, errors="coerce")
        values.index = [_as_date(value) for value in values.index]
        return values.pct_change().fillna(0.0).rename("benchmark_returns")
    if not isinstance(benchmark, pd.DataFrame) or "date" not in benchmark:
        raise ValueError("benchmark requires a date column and close/return values")
    current = benchmark.copy()
    current["date"] = current["date"].map(_as_date)
    value_column = (
        "close"
        if "close" in current
        else "benchmark_equity"
        if "benchmark_equity" in current
        else "return"
    )
    if value_column not in current:
        raise ValueError("benchmark requires close, benchmark_equity, or return")
    values = pd.to_numeric(current[value_column], errors="coerce")
    result = pd.Series(values.to_numpy(), index=current["date"], name="benchmark_returns")
    if value_column == "return":
        return result.fillna(0.0)
    return result.pct_change().fillna(0.0)


def _metrics(
    nav: pd.Series,
    returns: pd.Series,
    benchmark: pd.Series,
    turnover: pd.DataFrame,
    transaction_cost: pd.Series,
    positions: pd.DataFrame,
) -> dict[str, float | int]:
    if nav.empty:
        return {"total_return": 0.0, "cagr": 0.0, "max_drawdown": 0.0}
    total_return = float(nav.iloc[-1] / max(nav.iloc[0], 1e-12) - 1.0)
    days = max((nav.index[-1] - nav.index[0]).days, 1)
    cagr = (1 + total_return) ** (365.25 / days) - 1 if total_return > -1 else -1.0
    std = float(returns.std(ddof=0))
    volatility = std * math.sqrt(252)
    sharpe = float(returns.mean() / std * math.sqrt(252)) if std > 0 else 0.0
    downside = float(returns.where(returns < 0, 0.0).std(ddof=0) * math.sqrt(252))
    sortino = float(returns.mean() / downside) if downside > 0 else 0.0
    drawdown = nav / nav.cummax() - 1.0
    max_drawdown = float(drawdown.min())
    calmar = float(cagr / abs(max_drawdown)) if max_drawdown < 0 else 0.0
    benchmark_total = float(benchmark.add(1.0).prod() - 1.0) if not benchmark.empty else 0.0
    if positions.empty:
        concentration = 0.0
    else:
        concentration = float(
            positions.groupby("date")["weight"]
            .apply(lambda values: (values**2).sum())
            .mean()
        )
    normalized_turnover = (
        float(turnover["normalized_turnover"].mean()) if not turnover.empty else 0.0
    )
    raw_turnover = float(turnover["raw_turnover"].mean()) if not turnover.empty else 0.0
    return {
        "total_return": total_return,
        "cagr": float(cagr),
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_drawdown,
        "calmar": calmar,
        "volatility": volatility,
        "turnover": normalized_turnover,
        "raw_turnover": raw_turnover,
        "normalized_turnover": normalized_turnover,
        "estimated_transaction_cost": float(transaction_cost.sum()),
        "number_rebalances": int(len(turnover)),
        "average_holding_period": float(len(nav) / max(len(turnover), 1)),
        "concentration": concentration,
        "benchmark_excess_return": total_return - benchmark_total,
    }


class ReferenceFastResearchEngine:
    """Small transparent fallback with explicit approximation warnings."""

    engine = "reference-fast"
    engine_version = "stage3b-reference-v1"

    def run(
        self,
        signals: SignalFrame,
        strategy: PortfolioStrategy,
        portfolio_spec: PortfolioSpec,
        execution_spec: ExecutionSpec | None = None,
        market_data: pd.DataFrame | None = None,
        benchmark: pd.DataFrame | pd.Series | None = None,
        period: Any | None = None,
        current_positions: dict[str, float] | None = None,
    ) -> BacktestResult:
        if not isinstance(signals, SignalFrame):
            raise TypeError("signals must be a SignalFrame")
        if period is not None and period.data_mode != signals.data_mode:
            raise ValueError("period and signals must use the same data_mode")
        bars = _prepare_bars(market_data, period)
        dates = sorted(bars["date"].unique())
        by_date = {current_date: group for current_date, group in bars.groupby("date", sort=True)}
        close = bars.pivot_table(index="date", columns="symbol", values="close", aggfunc="last")
        signal_frame = signals.to_frame()
        signal_frame["date"] = signal_frame["date"].map(_as_date)
        signal_groups = signal_frame.groupby("date", sort=True)
        events: dict[date, tuple[date, ExecutionSpec, dict[str, float]]] = {}
        last_rebalance: date | None = None
        held = dict(current_positions or {})
        warnings: list[str] = [
            "Fast research is an approximation; final A-share execution validation "
            "requires an event engine.",
        ]
        for signal_date, group in signal_groups:
            if period is not None and not (period.start_date <= signal_date <= period.end_date):
                continue
            if not portfolio_spec.rebalance_policy.should_rebalance(signal_date, last_rebalance):
                continue
            intended = _as_date(group["intended_execution_date"].iloc[0])
            if intended <= signal_date:
                raise ValueError("execution date must be after signal date")
            if execution_spec is None:
                raise ValueError("execution_spec is required for fast research")
            current_execution = replace(
                execution_spec,
                signal_date=signal_date,
                execution_date=intended,
            )
            group_signal = SignalFrame.from_frame(group, data_mode=signals.data_mode)
            targets = strategy.generate_targets(
                group_signal,
                portfolio_spec,
                current_execution,
                current_positions=held,
            )
            events[intended] = (
                signal_date,
                current_execution,
                {target.symbol: float(target.target_weight) for target in targets},
            )
            held = events[intended][2].copy()
            last_rebalance = signal_date

        nav_values: list[float] = []
        returns_values: list[float] = []
        nav = 1.0
        positions_rows: list[dict[str, object]] = []
        order_rows: list[dict[str, object]] = []
        trade_rows: list[dict[str, object]] = []
        turnover_rows: list[dict[str, object]] = []
        cost_rows: list[float] = []
        previous_close: dict[str, float] = {}
        held = dict(current_positions or {})
        for current_date in dates:
            daily_cost = 0.0
            if current_date in events:
                signal_date, current_execution, target_weights = events[current_date]
                effective = dict(target_weights)
                current_bars = by_date[current_date].set_index("symbol")
                for symbol in set(held).union(target_weights):
                    previous_weight = float(held.get(symbol, 0.0))
                    target_weight = float(target_weights.get(symbol, 0.0))
                    delta = target_weight - previous_weight
                    if abs(delta) <= 1e-12:
                        continue
                    blocked = False
                    rejection_reason = ""
                    bar = current_bars.loc[symbol] if symbol in current_bars.index else None
                    if bar is None:
                        blocked = True
                        rejection_reason = "missing_bar"
                    elif current_execution.suspension_rule and bool(bar["is_suspended"]):
                        blocked = True
                        rejection_reason = "suspended"
                    elif current_execution.limit_rule and delta > 0 and bool(bar["is_limit_up"]):
                        blocked = True
                        rejection_reason = "limit_up_buy_locked"
                    elif current_execution.limit_rule and delta < 0 and bool(bar["is_limit_down"]):
                        blocked = True
                        rejection_reason = "limit_down_sell_locked"
                    if blocked:
                        effective[symbol] = previous_weight
                        warnings.append(f"{current_date} {symbol}: {rejection_reason}")
                    buy = max(delta, 0.0) if not blocked else 0.0
                    sell = max(-delta, 0.0) if not blocked else 0.0
                    price = float(bar["open"]) if bar is not None else float("nan")
                    cost = buy * (current_execution.commission + current_execution.slippage)
                    cost += sell * (
                        current_execution.commission
                        + current_execution.slippage
                        + current_execution.tax
                    )
                    daily_cost += cost
                    order = {
                        "date": current_date,
                        "signal_date": signal_date,
                        "execution_date": current_execution.execution_date,
                        "symbol": symbol,
                        "side": "BUY" if delta > 0 else "SELL",
                        "previous_weight": previous_weight,
                        "target_weight": target_weight,
                        "filled": not blocked,
                        "rejection_reason": rejection_reason,
                        "execution_price": price,
                        "estimated_cost": cost,
                    }
                    order_rows.append(order)
                    if not blocked:
                        trade_rows.append(order.copy())
                breakdown = compute_turnover(held, effective)
                turnover_rows.append(
                    {
                        "date": current_date,
                        "raw_turnover": breakdown.raw,
                        "normalized_turnover": breakdown.normalized,
                    }
                )
                held = {symbol: weight for symbol, weight in effective.items() if weight > 1e-12}
            for symbol, weight in held.items():
                positions_rows.append({"date": current_date, "symbol": symbol, "weight": weight})
            day_returns: list[float] = []
            for symbol, weight in held.items():
                if symbol not in close.columns or pd.isna(close.loc[current_date, symbol]):
                    continue
                current_price = float(close.loc[current_date, symbol])
                old_price = previous_close.get(symbol)
                if old_price is not None and old_price > 0:
                    day_returns.append(weight * (current_price / old_price - 1.0))
            gross_return = float(sum(day_returns))
            nav *= max(0.0, 1.0 - daily_cost) * (1.0 + gross_return)
            nav_values.append(nav)
            returns_values.append(gross_return - daily_cost)
            cost_rows.append(daily_cost)
            for symbol in close.columns:
                value = close.loc[current_date, symbol]
                if pd.notna(value):
                    previous_close[symbol] = float(value)

        index = pd.Index(dates, name="date")
        nav_series = pd.Series(nav_values, index=index, name="nav")
        returns_series = pd.Series(returns_values, index=index, name="returns")
        benchmark_series = _benchmark_returns(benchmark).reindex(index).fillna(0.0)
        turnover = pd.DataFrame(turnover_rows)
        transaction_cost = pd.Series(cost_rows, index=index, name="transaction_cost")
        positions = pd.DataFrame(positions_rows)
        orders = pd.DataFrame(order_rows)
        trades = pd.DataFrame(trade_rows)
        metrics = _metrics(
            nav_series,
            returns_series,
            benchmark_series,
            turnover,
            transaction_cost,
            positions,
        )
        nav_frame = pd.DataFrame({"date": dates, "nav": nav_values, "equity": nav_values})
        limitations = (
            "Vectorized/approximate execution does not model exact A-share lot fills "
            "or queue priority.",
            "T+1, limit, suspension, and rejection behavior must be revalidated by "
            "the future event engine.",
        )
        assumptions = (
            "Signal generated after the T close is eligible no earlier than T+1.",
            "Research returns use available daily close data after the execution date.",
            "Transaction costs are estimated from weight turnover and ExecutionSpec rates.",
        )
        return BacktestResult(
            nav=nav_frame,
            returns=returns_series,
            benchmark_returns=benchmark_series,
            positions=positions,
            orders=orders,
            trades=trades,
            turnover=turnover,
            transaction_cost=transaction_cost,
            metrics=metrics,
            data_mode=signals.data_mode,
            engine=self.engine,
            engine_version=self.engine_version,
            warnings=tuple(dict.fromkeys(warnings)),
            limitations=limitations,
            assumptions=assumptions,
        )
