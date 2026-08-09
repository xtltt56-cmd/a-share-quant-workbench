from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from a_share_quant.analysis.fast_research_report import write_fast_research_report
from a_share_quant.backtest.fast import ResearchPeriod, TimePeriod
from a_share_quant.contracts import (
    ExecutionSpec,
    PortfolioTarget,
    SignalFrame,
    TimeSemantics,
    next_trading_date,
    validate_execution_date,
)
from a_share_quant.strategies import (
    EveryNDays,
    PortfolioSpec,
    RebalancePolicy,
    TopKEqualWeight,
    build_candidate_strategies,
    candidate_strategy_factories,
)


def _execution() -> ExecutionSpec:
    return ExecutionSpec(
        signal_date=date(2026, 8, 7),
        execution_date=date(2026, 8, 10),
        execution_price_rule="open",
        slippage=0.0005,
        commission=0.0003,
        tax=0.0005,
        t_plus_one=True,
        limit_rule=True,
        suspension_rule=True,
        minimum_order_size=100,
        cash_constraint=True,
    )


def _signals() -> SignalFrame:
    return SignalFrame.from_predictions(
        pd.DataFrame(
            {
                "signal_date": [date(2026, 8, 7)],
                "symbol": ["000001"],
                "raw_score": [1.0],
                "strategy_id": ["lgbm"],
                "strategy_version": ["v1"],
                "model_version": ["m1"],
                "feature_version": ["f1"],
            }
        ),
        experiment_id="fixture-exp",
        trading_dates=[date(2026, 8, 7), date(2026, 8, 10)],
        data_mode="fixture",
    )


def _bars() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [date(2026, 8, 7), date(2026, 8, 10)],
            "symbol": ["000001", "000001"],
            "open": [10.0, 10.2],
            "close": [10.1, 10.3],
            "is_suspended": [False, False],
            "is_limit_up": [False, False],
            "is_limit_down": [False, False],
        }
    )


def test_contract_lazy_exports_and_time_aliases() -> None:
    assert TimePeriod is ResearchPeriod
    semantics = TimeSemantics(
        datetime(2026, 8, 7, 7, tzinfo=timezone.utc),
        datetime(2026, 8, 7, 8, tzinfo=timezone.utc),
        datetime(2026, 8, 7, 9, tzinfo=timezone.utc),
        datetime(2026, 8, 7, 10, tzinfo=timezone.utc),
        datetime(2026, 8, 10, 1, tzinfo=timezone.utc),
    )
    assert semantics.validate()
    assert next_trading_date(date(2026, 8, 7), [date(2026, 8, 7), date(2026, 8, 10)]) == date(
        2026, 8, 10
    )
    validate_execution_date(date(2026, 8, 7), date(2026, 8, 10), t_plus_one=True)
    assert PortfolioTarget(
        date(2026, 8, 10), "000001", 0.1, "s:v1", "test"
    ).symbol == "000001"


def test_strategy_contract_validation_and_registry() -> None:
    assert set(candidate_strategy_factories()) == {
        "topk_equal_weight",
        "topk_score_weight",
        "rank_weighted",
        "topk_dropout",
    }
    assert set(build_candidate_strategies()) == set(candidate_strategy_factories())
    assert EveryNDays(5).frequency == "every_n_days"
    assert RebalancePolicy.weekly().interval_days == 7
    assert PortfolioSpec(target_gross_exposure=0.6).gross_exposure == 0.6
    with pytest.raises(ValueError):
        RebalancePolicy("monthly")
    with pytest.raises(ValueError):
        EveryNDays(0)
    with pytest.raises(ValueError):
        PortfolioSpec(target_gross_exposure=0.8, cash_buffer=0.3)
    with pytest.raises(TypeError):
        TopKEqualWeight().generate_targets("bad", PortfolioSpec(), _execution())


def test_reference_engine_handles_status_flags_and_benchmark() -> None:
    from a_share_quant.backtest.fast import FastResearchEngine

    bars = _bars()
    bars.loc[1, "is_limit_up"] = True
    benchmark = pd.DataFrame(
        {
            "date": bars["date"],
            "close": [100.0, 101.0],
        }
    )
    result = FastResearchEngine(prefer_vectorbt=False).run(
        _signals(),
        TopKEqualWeight(),
        PortfolioSpec(top_k=1),
        _execution(),
        bars,
        benchmark,
    )

    assert any("limit_up_buy_locked" in warning for warning in result.warnings)
    assert result.metrics["benchmark_excess_return"] is not None


def test_reference_engine_accepts_minimal_bars_and_series_benchmark() -> None:
    from a_share_quant.backtest.fast import FastResearchEngine

    bars = _bars().drop(
        columns=["open", "is_suspended", "is_limit_up", "is_limit_down"]
    )
    benchmark = pd.Series(
        [100.0, 101.0], index=[date(2026, 8, 7), date(2026, 8, 10)]
    )
    result = FastResearchEngine(prefer_vectorbt=False).run(
        signals=_signals(),
        strategy=TopKEqualWeight(),
        portfolio_spec=PortfolioSpec(top_k=1),
        execution_spec=_execution(),
        market_data=bars,
        benchmark=benchmark,
        period=ResearchPeriod(date(2026, 8, 7), date(2026, 8, 10), data_mode="fixture"),
    )

    assert len(result.nav) == 2


def test_fast_research_validation_errors() -> None:
    from a_share_quant.backtest.reference import ReferenceFastResearchEngine

    with pytest.raises(ValueError):
        ResearchPeriod(date(2026, 8, 10), date(2026, 8, 7))
    with pytest.raises(ValueError):
        ResearchPeriod("not-a-date", "2026-08-10")
    with pytest.raises(ValueError):
        ReferenceFastResearchEngine().run(
            _signals(),
            TopKEqualWeight(),
            PortfolioSpec(),
            _execution(),
            pd.DataFrame({"date": [date(2026, 8, 7)], "symbol": ["000001"]}),
        )


def test_fast_report_writer_covers_comparison_sections(tmp_path) -> None:
    from a_share_quant.analysis.fast_research_report import _json_safe

    metadata = {
        "data_mode": "fixture",
        "equal_weight_vs_score_weight": {"lgbm": {"delta": np.float64(0.1)}},
        "reasonable_zones": {"top_k": "fixture", "rebalance": "fixture"},
    }
    row = {
        "model": "lgbm",
        "strategy": "equal",
        "top_k": 10,
        "rebalance_days": 5,
        "cagr": 0.1,
        "sharpe": 1.0,
        "sortino": 1.2,
        "max_drawdown": -0.1,
        "calmar": 1.0,
        "volatility": 0.2,
        "turnover": 0.1,
        "raw_turnover": 0.1,
        "normalized_turnover": 0.2,
        "estimated_transaction_cost": 0.01,
        "total_return": 0.1,
        "selection_status": "CANDIDATE",
    }
    payload = {
        "metadata": metadata,
        "candidate_strategies": [row],
        "topk_comparison": [row],
        "rebalance_comparison": [row],
        "turnover_comparison": [row],
        "cost_comparison": [row],
        "drawdown_comparison": [row],
        "parameter_surface": [row],
        "model_strategy_matrix": [row],
        "equal_rank_ensemble": [row],
        "historical_dry_run": {"status": "NOT_RUN", "missing": ["000300"]},
        "limitations": ["fixture"],
    }
    markdown, _ = write_fast_research_report(
        payload,
        markdown_path=tmp_path / "report.md",
        json_path=tmp_path / "report.json",
    )

    assert "EqualWeight vs ScoreWeight" in markdown.read_text(encoding="utf-8")
    assert _json_safe(np.float64(1.0)) == 1.0
