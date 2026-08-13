"""Causal, auditable daily candidate ranking from local real market bars.

This module deliberately produces a ranking, not a calibrated return forecast.
It requires a real historical source and a sufficiently broad common-date
cross-section before any row can be exposed as an official daily candidate.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from a_share_quant.data.normalization import normalize_symbol
from a_share_quant.experiments.pipeline import build_rule_features
from a_share_quant.features.rule_factors import RuleFactorEngine
from a_share_quant.features.universe import HistoricalUniverse
from a_share_quant.signals.realtime import OfficialModelSignal

DEFAULT_STRATEGY_VERSION = "initial-free-data-v1"
DEFAULT_MODEL_VERSION = "rule-ranking-v1"
DEFAULT_FEATURE_VERSION = "rule-features-v1-no-valuation"
DEFAULT_MIN_HISTORY = 252
DEFAULT_MIN_CROSS_SECTION = 30
DEFAULT_MIN_AVERAGE_AMOUNT = 10_000_000.0
DEFAULT_TOP_K = 10
_DISALLOWED_SOURCES = frozenset({"fixture", "replay", "synthetic", "test", "test-data"})
_REQUIRED_COLUMNS = frozenset(
    {"symbol", "date", "open", "high", "low", "close", "volume", "amount", "source"}
)


class DailyDataStaleError(ValueError):
    """Raised when local daily bars are behind the latest expected session."""

    def __init__(self, available: date, expected: date) -> None:
        self.available = available
        self.expected = expected
        super().__init__(
            f"日线数据截止 {available.isoformat()}，预计至少需要 {expected.isoformat()}"
        )


def generate_official_signals(
    daily_bars: pd.DataFrame,
    *,
    benchmark: str = "000300",
    as_of: date | str | None = None,
    top_k: int = DEFAULT_TOP_K,
    min_history: int = DEFAULT_MIN_HISTORY,
    min_cross_section: int = DEFAULT_MIN_CROSS_SECTION,
    min_average_amount: float = DEFAULT_MIN_AVERAGE_AMOUNT,
    engine: RuleFactorEngine | None = None,
    name_map: Mapping[str, str] | None = None,
    instruments: pd.DataFrame | None = None,
    now: datetime | None = None,
    require_fresh: bool = False,
    max_business_day_lag: int = 1,
) -> tuple[OfficialModelSignal, ...]:
    """Generate the latest causal daily ranking from canonical bars.

    The complete eligible cross-section must have at least ``min_cross_section``
    symbols.  Liquidity and trend gates are then applied to the output rows;
    a temporarily weak market can therefore produce fewer than ``top_k`` rows,
    but never a fabricated fallback.
    """

    _validate_parameters(top_k, min_history, min_cross_section, min_average_amount)
    frame = _prepare_bars(daily_bars)
    benchmark_symbol = normalize_symbol(benchmark)
    if benchmark_symbol not in set(frame["symbol"]):
        raise ValueError(f"benchmark is missing from daily bars: {benchmark_symbol}")

    requested_as_of = _parse_date(as_of) if as_of is not None else None
    eligibility_date = requested_as_of or max(frame["date"])
    if instruments is not None:
        tradable = HistoricalUniverse(
            instruments,
            exclude_new_days=60,
            min_average_amount=None,
            min_average_turnover_pct=None,
        ).tradable_universe(eligibility_date)
        allowed = set(tradable["symbol"].astype(str)) | {benchmark_symbol}
        frame = frame.loc[frame["symbol"].isin(allowed)].copy()
    latest_by_symbol = frame.groupby("symbol", sort=True)["date"].max()
    common_as_of = min(latest_by_symbol.tolist())
    if requested_as_of is not None:
        if requested_as_of > common_as_of:
            raise ValueError("requested as_of is later than available common history")
        common_as_of = requested_as_of
    if require_fresh:
        validate_daily_data_freshness(
            common_as_of,
            now=now or datetime.now(ZoneInfo("Asia/Shanghai")),
            max_business_day_lag=max_business_day_lag,
        )

    prepared_groups: list[pd.DataFrame] = []
    eligible_symbols: list[str] = []
    for symbol, group in frame.groupby("symbol", sort=True):
        bounded = group.loc[group["date"] <= common_as_of].copy()
        if len(bounded) < min_history:
            continue
        eligible_symbols.append(symbol)
        prepared_groups.append(bounded.tail(max(min_history, 600)))
    if benchmark_symbol not in eligible_symbols:
        raise ValueError("benchmark does not have enough common historical bars")
    stock_symbols = [symbol for symbol in eligible_symbols if symbol != benchmark_symbol]
    if len(stock_symbols) < min_cross_section:
        raise ValueError(
            f"eligible cross-section has {len(stock_symbols)} symbols; "
            f"at least {min_cross_section} is required"
        )

    bounded_frame = pd.concat(prepared_groups, ignore_index=True)
    features = build_rule_features(bounded_frame, benchmark=benchmark_symbol)
    latest = features.loc[features["date"].eq(common_as_of)].copy()
    latest = _add_candidate_gates(latest, bounded_frame)
    scoring_frame = latest.drop(columns=["valuation"], errors="ignore")
    scorer = engine or _default_engine()
    scored = scorer.score(scoring_frame)
    scored["full_rank"] = scored["rule_score"].rank(method="first", ascending=False).astype(int)
    candidates = scored.loc[
        scored["amount20"].ge(min_average_amount) & scored["close"].gt(scored["ma60"])
    ].sort_values(["rule_score", "symbol"], ascending=[False, True], kind="stable")
    if candidates.empty:
        raise ValueError("no candidate passed the liquidity and trend gates")
    candidates = candidates.head(top_k).reset_index(drop=True)

    generated_at = now or datetime.now(timezone.utc)
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    names = {normalize_symbol(key): str(value).strip() for key, value in (name_map or {}).items()}
    result: list[OfficialModelSignal] = []
    for rank, row in enumerate(candidates.itertuples(index=False), start=1):
        symbol = normalize_symbol(str(row.symbol))
        result.append(
            OfficialModelSignal(
                signal_date=common_as_of,
                symbol=symbol,
                name=names.get(symbol, symbol),
                normalized_score=float(row.rule_score),
                strategy_version=DEFAULT_STRATEGY_VERSION,
                model_version=DEFAULT_MODEL_VERSION,
                feature_version=DEFAULT_FEATURE_VERSION,
                data_mode="historical",
                source="baostock",
                data_cutoff=common_as_of,
                generated_at=generated_at,
                rank=rank,
                reasons=(
                    "收盘价位于60日均线上方",
                    "20日平均成交额通过流动性门槛",
                    "固定权重横截面因子排序（未启用估值因子）",
                ),
                reference_price=float(row.close),
                average_amount=float(row.amount20),
                invalidation_price=round(float(row.close) * 0.93, 4),
            )
        )
    return tuple(result)


def generate_from_data_root(
    data_root: str | Path,
    *,
    benchmark: str = "000300",
    **kwargs: object,
) -> tuple[OfficialModelSignal, ...]:
    """Load only canonical local Parquet bars and generate the ranking."""

    root = Path(data_root).resolve()
    paths = sorted((root / "lake" / "daily_bars").glob("*.parquet"))
    if not paths:
        raise FileNotFoundError(f"no daily bars under {root / 'lake' / 'daily_bars'}")
    frames = [pd.read_parquet(path) for path in paths]
    frame = pd.concat(frames, ignore_index=True)
    instrument_paths = sorted((root / "lake" / "instruments").glob("*.parquet"))
    instruments = pd.read_parquet(instrument_paths[-1]) if instrument_paths else None
    return generate_official_signals(
        frame,
        benchmark=benchmark,
        instruments=instruments,
        **kwargs,
    )


def validate_daily_data_freshness(
    available_cutoff: date,
    *,
    now: datetime,
    max_business_day_lag: int = 1,
) -> None:
    """Reject a local cutoff that is too far behind the latest expected day.

    The free-data path has no guaranteed exchange-holiday calendar, so this
    uses weekdays conservatively and leaves a one-business-day grace period.
    """

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if max_business_day_lag < 0:
        raise ValueError("max_business_day_lag must be non-negative")
    local = now.astimezone(ZoneInfo("Asia/Shanghai"))
    candidate = local.date()
    if local.timetz().replace(tzinfo=None) < time(15, 0):
        candidate -= timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    lag = _business_day_distance(available_cutoff, candidate)
    if lag > max_business_day_lag:
        raise DailyDataStaleError(available_cutoff, candidate)


def _business_day_distance(available: date, expected: date) -> int:
    if available >= expected:
        return 0
    current = available
    distance = 0
    while current < expected:
        current += timedelta(days=1)
        if current.weekday() < 5:
            distance += 1
    return distance


def load_name_map(data_root: str | Path) -> dict[str, str]:
    root = Path(data_root).resolve()
    paths = sorted((root / "lake" / "instruments").glob("*.parquet"))
    if not paths:
        return {}
    frame = pd.read_parquet(paths[-1])
    if not {"symbol", "name"}.issubset(frame.columns):
        return {}
    return {
        normalize_symbol(str(row.symbol)): str(row.name).strip()
        for row in frame.itertuples(index=False)
        if str(row.name).strip()
    }


def _prepare_bars(frame: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError("daily bars must be a non-empty DataFrame")
    missing = _REQUIRED_COLUMNS.difference(frame.columns)
    if missing:
        raise ValueError(f"daily bars missing columns: {', '.join(sorted(missing))}")
    result = frame.copy()
    result["symbol"] = result["symbol"].map(normalize_symbol)
    result["date"] = pd.to_datetime(result["date"], errors="coerce").dt.date
    if result["date"].isna().any():
        raise ValueError("daily bars contain invalid dates")
    sources = result["source"].astype(str).str.strip().str.casefold()
    if sources.isin(_DISALLOWED_SOURCES).any():
        raise ValueError("fixture or non-market daily bars cannot generate official signals")
    result = result.loc[sources.eq("baostock")].copy()
    if result.empty:
        raise ValueError("initial free-data candidates require BaoStock daily bars")
    for field in ("open", "high", "low", "close", "volume", "amount"):
        result[field] = pd.to_numeric(result[field], errors="coerce")
    if result[["open", "high", "low", "close", "volume", "amount"]].isna().any().any():
        raise ValueError("daily bars contain invalid numeric values")
    return result.sort_values(["symbol", "date"], kind="stable").drop_duplicates(
        ["symbol", "date"], keep="last"
    )


def _add_candidate_gates(latest: pd.DataFrame, bars: pd.DataFrame) -> pd.DataFrame:
    metrics = bars.sort_values(["symbol", "date"], kind="stable").copy()
    metrics["amount20"] = metrics.groupby("symbol")["amount"].transform(
        lambda values: values.rolling(20, min_periods=20).mean()
    )
    metrics["ma60"] = metrics.groupby("symbol")["close"].transform(
        lambda values: values.rolling(60, min_periods=60).mean()
    )
    current = metrics.loc[metrics["date"].eq(latest["date"].max()), ["symbol", "amount20", "ma60"]]
    return latest.merge(current, on="symbol", how="left", validate="one_to_one")


def _default_engine() -> RuleFactorEngine:
    strategy_path = Path(__file__).resolve().parents[3] / "config" / "strategy.yaml"
    return RuleFactorEngine.from_yaml(strategy_path)


def _parse_date(value: date | str) -> date:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid date: {value!r}")
    return parsed.date()


def _validate_parameters(
    top_k: int,
    min_history: int,
    min_cross_section: int,
    min_average_amount: float,
) -> None:
    if top_k < 1:
        raise ValueError("top_k must be positive")
    if min_history < 60:
        raise ValueError("min_history must be at least 60 trading days")
    if min_cross_section < 2:
        raise ValueError("min_cross_section must be at least 2")
    if min_average_amount < 0:
        raise ValueError("min_average_amount cannot be negative")


__all__ = [
    "DailyDataStaleError",
    "generate_from_data_root",
    "generate_official_signals",
    "load_name_map",
    "validate_daily_data_freshness",
]
