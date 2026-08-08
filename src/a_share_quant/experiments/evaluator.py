"""A-share-aware fair portfolio evaluation for baseline comparison."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from a_share_quant.data.normalization import normalize_symbol

from .metrics import compute_comparison_metrics


@dataclass(frozen=True)
class EvaluationConfig:
    horizon_days: int = 5
    signal_delay_days: int = 1
    max_positions: int = 10
    max_single_position: float = 0.15
    max_gross_exposure: float = 0.60
    commission_rate: float = 0.0003
    stamp_duty_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    slippage_bps: float = 5.0

    @classmethod
    def from_yaml(cls, path: Path) -> EvaluationConfig:
        with Path(path).open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        config = raw.get("backtest", {})
        costs = config.get("transaction_costs", {})
        return cls(
            horizon_days=5,
            signal_delay_days=int(config.get("signal_to_execution_delay_days", 1)),
            max_positions=10,
            max_single_position=0.15,
            max_gross_exposure=0.60,
            commission_rate=float(costs.get("commission_rate", 0.0003)),
            stamp_duty_rate=float(costs.get("stamp_duty_rate", 0.0005)),
            transfer_fee_rate=float(costs.get("transfer_fee_rate", 0.00001)),
            slippage_bps=float(costs.get("slippage_bps", 5)),
        )


@dataclass(frozen=True)
class FairEvaluationResult:
    predictions: pd.DataFrame
    equity: pd.DataFrame
    trades: pd.DataFrame
    metrics: dict[str, Any]


class FairPortfolioEvaluator:
    """Evaluate every strategy under one delayed, costed, constrained portfolio."""

    def __init__(self, config: EvaluationConfig) -> None:
        if config.horizon_days < 1 or config.signal_delay_days < 1:
            raise ValueError("horizon and signal delay must be positive")
        self.config = config

    def evaluate(
        self,
        predictions: pd.DataFrame,
        daily_bars: pd.DataFrame,
        *,
        benchmark: str,
    ) -> FairEvaluationResult:
        bars = _prepare_bars(daily_bars)
        benchmark_symbol = normalize_symbol(benchmark)
        if benchmark_symbol not in set(bars["symbol"]):
            raise ValueError(f"benchmark is missing from daily bars: {benchmark_symbol}")
        enriched = self._add_forward_labels(predictions.copy(), bars, benchmark_symbol)
        equity, trades = self._simulate(enriched, bars, benchmark_symbol)
        top_k = min(self.config.max_positions, max(enriched["symbol"].nunique(), 1))
        metrics = compute_comparison_metrics(enriched, equity, top_k=top_k)
        return FairEvaluationResult(enriched, equity, trades, metrics)

    def _add_forward_labels(
        self,
        predictions: pd.DataFrame,
        bars: pd.DataFrame,
        benchmark: str,
    ) -> pd.DataFrame:
        required = {"signal_date", "symbol", "raw_score"}
        missing = sorted(required.difference(predictions.columns))
        if missing:
            raise ValueError(f"predictions are missing columns: {', '.join(missing)}")
        result = predictions.copy()
        result["signal_date"] = pd.to_datetime(result["signal_date"]).dt.date
        result["symbol"] = result["symbol"].map(normalize_symbol)
        close = {
            symbol: group.set_index("date")["close"].sort_index()
            for symbol, group in bars.groupby("symbol", sort=False)
        }
        returns = []
        excess = []
        for row in result.itertuples(index=False):
            stock_return = _forward_return(
                close.get(row.symbol),
                row.signal_date,
                self.config.horizon_days,
            )
            benchmark_return = _forward_return(
                close[benchmark],
                row.signal_date,
                self.config.horizon_days,
            )
            returns.append(stock_return)
            excess.append(
                stock_return - benchmark_return
                if np.isfinite(stock_return) and np.isfinite(benchmark_return)
                else np.nan
            )
        result["forward_return"] = returns
        result["forward_excess_return"] = excess
        return result

    def _simulate(
        self,
        predictions: pd.DataFrame,
        bars: pd.DataFrame,
        benchmark: str,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        calendar = sorted(bars["date"].unique())
        bar_lookup = {(row.symbol, row.date): row._asdict() for row in bars.itertuples(index=False)}
        events: dict[date, pd.DataFrame] = {}
        for signal_date, group in predictions.groupby("signal_date", sort=True):
            execution_date = _delayed_date(calendar, signal_date, self.config.signal_delay_days)
            if execution_date is not None:
                events[execution_date] = group.sort_values(
                    "raw_score", ascending=False, kind="stable"
                )

        cash = 1.0
        positions: dict[str, float] = {}
        entry_dates: dict[str, date] = {}
        equity_rows: list[dict[str, Any]] = []
        trade_rows: list[dict[str, Any]] = []
        benchmark_equity = 1.0
        previous_benchmark_close: float | None = None
        slippage = self.config.slippage_bps / 10_000

        for current_date in calendar:
            trade_notional = 0.0
            if current_date in events:
                target = _target_weights(events[current_date], self.config)
                account_at_open = _mark_to_market(
                    cash, positions, current_date, bar_lookup, use_open=True
                )

                for symbol in list(positions):
                    if symbol in target:
                        continue
                    row = bar_lookup.get((symbol, current_date))
                    if row is None or not _can_sell(row) or entry_dates[symbol] >= current_date:
                        continue
                    proceeds, fees = _sell_position(
                        positions.pop(symbol),
                        row["open"],
                        slippage,
                        self.config,
                    )
                    cash += proceeds - fees
                    notional = proceeds / max(1 - slippage, 1e-12)
                    trade_notional += notional
                    trade_rows.append(
                        _trade_row(current_date, "SELL", symbol, notional, 0.0, fees)
                    )
                    entry_dates.pop(symbol, None)

                for symbol, weight in target.items():
                    if symbol in positions:
                        continue
                    row = bar_lookup.get((symbol, current_date))
                    if row is None or not _can_buy(row):
                        continue
                    desired = account_at_open * weight
                    quantity, spent, fees = _buy_position(
                        desired,
                        cash,
                        row["open"],
                        slippage,
                        self.config,
                    )
                    if quantity <= 0:
                        continue
                    positions[symbol] = quantity
                    entry_dates[symbol] = current_date
                    cash -= spent + fees
                    trade_notional += spent
                    trade_rows.append(
                        _trade_row(current_date, "BUY", symbol, spent, weight, fees)
                    )

            current_equity = _mark_to_market(
                cash, positions, current_date, bar_lookup, use_open=False
            )
            benchmark_row = bar_lookup[(benchmark, current_date)]
            benchmark_close = float(benchmark_row["close"])
            if previous_benchmark_close is not None and previous_benchmark_close > 0:
                benchmark_equity *= benchmark_close / previous_benchmark_close
            previous_benchmark_close = benchmark_close
            equity_rows.append(
                {
                    "date": current_date,
                    "equity": current_equity,
                    "benchmark_equity": benchmark_equity,
                    "turnover": trade_notional / max(current_equity, 1e-12),
                    "position_count": len(positions),
                    "gross_exposure": _gross_exposure(
                        positions, current_date, bar_lookup, current_equity
                    ),
                    "cash": cash,
                }
            )
        trades = pd.DataFrame(
            trade_rows,
            columns=[
                "execution_date",
                "side",
                "symbol",
                "notional",
                "target_weight",
                "fees",
            ],
        )
        return pd.DataFrame(equity_rows), trades


def _prepare_bars(bars: pd.DataFrame) -> pd.DataFrame:
    required = {"symbol", "date", "open", "close"}
    missing = sorted(required.difference(bars.columns))
    if missing:
        raise ValueError(f"daily bars are missing columns: {', '.join(missing)}")
    result = bars.copy()
    result["symbol"] = result["symbol"].map(normalize_symbol)
    result["date"] = pd.to_datetime(result["date"]).dt.date
    for column in ("open", "close"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result["is_suspended"] = _bool_column(result, "is_suspended")
    result["is_limit_up"] = _bool_column(result, "is_limit_up")
    result["is_limit_down"] = _bool_column(result, "is_limit_down")
    if result[["open", "close"]].isna().any().any():
        raise ValueError("daily bars contain invalid prices")
    return result.sort_values(["symbol", "date"], kind="stable").drop_duplicates(
        ["symbol", "date"], keep="last"
    )


def _bool_column(frame: pd.DataFrame, name: str) -> pd.Series:
    if name not in frame:
        return pd.Series(False, index=frame.index, dtype="bool")
    if frame[name].dtype == bool:
        return frame[name].fillna(False).astype(bool)
    return frame[name].astype(str).str.lower().isin({"1", "true", "yes", "y"})


def _target_weights(group: pd.DataFrame, config: EvaluationConfig) -> dict[str, float]:
    selected = group.drop_duplicates("symbol", keep="first").head(config.max_positions)
    if selected.empty:
        return {}
    weight = min(config.max_single_position, config.max_gross_exposure / len(selected))
    return {str(symbol): weight for symbol in selected["symbol"]}


def _forward_return(close: pd.Series | None, current: date, horizon: int) -> float:
    if close is None or current not in close.index:
        return float("nan")
    position = close.index.get_loc(current)
    if isinstance(position, slice) or position + horizon >= len(close):
        return float("nan")
    start, end = float(close.iloc[position]), float(close.iloc[position + horizon])
    return end / start - 1 if start > 0 else float("nan")


def _delayed_date(calendar: list[date], current: date, delay: int) -> date | None:
    index = bisect_right(calendar, current)
    target = index + delay - 1
    return calendar[target] if target < len(calendar) else None


def _can_buy(row: dict[str, Any]) -> bool:
    return not row["is_suspended"] and not row["is_limit_up"]


def _can_sell(row: dict[str, Any]) -> bool:
    return not row["is_suspended"] and not row["is_limit_down"]


def _buy_position(
    desired: float,
    cash: float,
    price: float,
    slippage: float,
    config: EvaluationConfig,
) -> tuple[float, float, float]:
    execution_price = price * (1 + slippage)
    cost_rate = 1 + config.commission_rate + config.transfer_fee_rate
    spent = min(desired, cash / cost_rate)
    fees = spent * (cost_rate - 1)
    return spent / execution_price, spent, fees


def _sell_position(
    quantity: float,
    price: float,
    slippage: float,
    config: EvaluationConfig,
) -> tuple[float, float]:
    execution_price = price * (1 - slippage)
    proceeds = quantity * execution_price
    fees = proceeds * (config.commission_rate + config.stamp_duty_rate + config.transfer_fee_rate)
    return proceeds, fees


def _mark_to_market(
    cash: float,
    positions: dict[str, float],
    current_date: date,
    lookup: dict[tuple[str, date], dict[str, Any]],
    *,
    use_open: bool,
) -> float:
    price_field = "open" if use_open else "close"
    value = cash
    for symbol, quantity in positions.items():
        row = lookup.get((symbol, current_date))
        if row is not None:
            value += quantity * row[price_field]
    return value


def _gross_exposure(
    positions: dict[str, float],
    current_date: date,
    lookup: dict[tuple[str, date], dict[str, Any]],
    equity: float,
) -> float:
    if equity <= 0:
        return 0.0
    return sum(
        quantity * lookup[(symbol, current_date)]["close"]
        for symbol, quantity in positions.items()
        if (symbol, current_date) in lookup
    ) / equity


def _trade_row(
    execution_date: date,
    side: str,
    symbol: str,
    notional: float,
    target_weight: float,
    fees: float,
) -> dict[str, Any]:
    return {
        "execution_date": execution_date,
        "side": side,
        "symbol": symbol,
        "notional": notional,
        "target_weight": target_weight,
        "fees": fees,
    }
