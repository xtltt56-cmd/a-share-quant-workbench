"""Non-promotional historical engineering screening.

Historical data is useful for checking data plumbing, leakage, cost handling and
model availability.  It is deliberately *not* a model competition: a good
historical score cannot rank a candidate, issue a provisional status, or alter
the production champion.  Future pre-registered observations are the only
evidence that can enter the later governance workflow.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from a_share_quant.backtest.costs import AshareCostModel

NON_PROMOTIONAL_ENGINEERING = "NON_PROMOTIONAL_ENGINEERING"
_LABEL_COLUMNS = (
    "forward_excess_return_5",
    "excess_return_5",
    "forward_excess_return",
    "research_return",
    "forward_return_5",
    "forward_return",
    "return",
    "label",
)
_REALIZED_LABEL_COLUMNS = frozenset(
    {
        *_LABEL_COLUMNS,
        "_label",
        "label",
        "excess_return",
        "forward_excess_return",
        "forward_return",
    }
)
_DATE_COLUMNS = (
    "trading_date",
    "session_date",
    "date",
    "signal_date",
    "available_at",
)


@dataclass(frozen=True, slots=True)
class ScreeningConfig:
    """Frozen historical split and research-cost policy."""

    train_sessions: int = 756
    validation_sessions: int = 252
    embargo_sessions: int = 126
    test_sessions: int = 126
    minimum_oos_windows: int = 3
    round_trip_cost: float = 0.0012
    top_k: int = 10
    capacity_limit: float = 1.0
    evidence_mode: str = NON_PROMOTIONAL_ENGINEERING

    def __post_init__(self) -> None:
        for name in (
            "train_sessions",
            "validation_sessions",
            "embargo_sessions",
            "test_sessions",
            "minimum_oos_windows",
            "top_k",
        ):
            value = int(getattr(self, name))
            if value <= 0:
                raise ValueError(f"{name} must be positive")
            object.__setattr__(self, name, value)
        if self.minimum_oos_windows < 3:
            raise ValueError("minimum_oos_windows must be at least 3")
        for name in ("round_trip_cost", "capacity_limit"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
            object.__setattr__(self, name, value)
        if str(self.evidence_mode).strip() != NON_PROMOTIONAL_ENGINEERING:
            raise ValueError("historical screening evidence_mode is fixed and non-promotional")
        object.__setattr__(self, "evidence_mode", NON_PROMOTIONAL_ENGINEERING)

    @classmethod
    def from_yaml(cls, path: str | Path) -> ScreeningConfig:
        """Load only the historical validation keys from the project config."""

        import yaml

        with Path(path).open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        validation = raw.get("validation", {})
        return cls(
            train_sessions=int(validation.get("train_sessions", 756)),
            validation_sessions=int(validation.get("validation_sessions", 252)),
            embargo_sessions=int(validation.get("embargo_sessions", 126)),
            test_sessions=int(validation.get("test_sessions", 126)),
            minimum_oos_windows=int(validation.get("minimum_oos_windows", 3)),
            evidence_mode=str(
                validation.get("evidence_mode", NON_PROMOTIONAL_ENGINEERING)
            ),
        )


@dataclass(frozen=True, slots=True)
class HistoricalCandidate:
    """A model/algorithm adapter to be attempted by the screen.

    ``scorer`` is intentionally optional and is not serialized into evidence;
    callers should prefer a named ``score_column`` or ``model_family`` so a
    future prospective registration can reproduce the implementation.
    """

    candidate_id: str
    model_family: str = "rule-baseline"
    parameters: Mapping[str, Any] = field(default_factory=dict)
    score_column: str | None = None
    scorer: Callable[..., Any] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        candidate_id = str(self.candidate_id).strip()
        family = str(self.model_family).strip().lower()
        if not candidate_id:
            raise ValueError("candidate_id is required")
        if not family:
            raise ValueError("model_family is required")
        if self.parameters is None:
            parameters: dict[str, Any] = {}
        elif isinstance(self.parameters, Mapping):
            parameters = dict(self.parameters)
        else:
            raise TypeError("parameters must be a mapping")
        object.__setattr__(self, "candidate_id", candidate_id)
        object.__setattr__(self, "model_family", family)
        object.__setattr__(self, "parameters", parameters)
        if self.score_column is not None:
            value = str(self.score_column).strip()
            object.__setattr__(self, "score_column", value or None)

    def canonical_parameters(self) -> dict[str, Any]:
        return _json_safe(self.parameters)


@dataclass(frozen=True, slots=True)
class ScreeningFold:
    index: int
    train_start: date
    train_end: date
    validation_start: date
    validation_end: date
    embargo_start: date
    embargo_end: date
    test_start: date
    test_end: date
    train_sessions: int
    validation_sessions: int
    embargo_sessions: int
    test_sessions: int


@dataclass(frozen=True, slots=True)
class WindowMetrics:
    fold_index: int
    gross_excess_return: float
    net_cost_return: float
    rank_ic: float
    brier: float
    ece: float
    turnover: float
    estimated_cost: float
    max_drawdown: float
    capacity: float
    capacity_ok: bool
    regime: str
    pbo: float
    deflated_sharpe: float
    cpcv_paths: int
    leakage_flags: tuple[str, ...]
    reproducible: bool

    @property
    def excess_return(self) -> float:
        return self.gross_excess_return


@dataclass(frozen=True, slots=True)
class ScreeningTrial:
    candidate_id: str
    model_family: str
    canonical_parameters: Mapping[str, Any]
    random_seed: int
    status: str
    governance_state: str
    evidence_mode: str
    validation_score: float
    fold_metrics: tuple[WindowMetrics, ...]
    leakage_flags: tuple[str, ...]
    reproducible: bool
    pbo: float
    deflated_sharpe: float
    cpcv: Mapping[str, Any]
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    @property
    def metrics(self) -> tuple[WindowMetrics, ...]:
        return self.fold_metrics


@dataclass(frozen=True, slots=True)
class HistoricalScreeningResult:
    snapshot_id: str
    random_seed: int
    config: ScreeningConfig
    folds: tuple[ScreeningFold, ...]
    trials: tuple[ScreeningTrial, ...]
    status: str
    evidence_mode: str = NON_PROMOTIONAL_ENGINEERING
    can_rank_models: bool = False
    can_issue_provisional: bool = False
    can_issue_approval_token: bool = False
    champion_id: str | None = None
    canonical_digest: str = ""

    def __post_init__(self) -> None:
        if self.evidence_mode != NON_PROMOTIONAL_ENGINEERING:
            raise ValueError("historical screening result must be non-promotional")
        if self.can_rank_models or self.can_issue_provisional or self.can_issue_approval_token:
            raise ValueError("historical screening cannot issue governance decisions")
        if self.champion_id is not None:
            raise ValueError("historical screening cannot set a champion")
        if not self.canonical_digest:
            object.__setattr__(self, "canonical_digest", _digest(self._canonical_payload()))

    @property
    def trial_count(self) -> int:
        return len(self.trials)

    def _canonical_payload(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "random_seed": self.random_seed,
            "config": _json_safe(asdict(self.config)),
            "folds": _json_safe([asdict(item) for item in self.folds]),
            "trials": _json_safe([_trial_dict(item) for item in self.trials]),
            "status": self.status,
            "evidence_mode": self.evidence_mode,
            "can_rank_models": False,
            "can_issue_provisional": False,
            "can_issue_approval_token": False,
            "champion_id": None,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._canonical_payload(), "canonical_digest": self.canonical_digest}


class HistoricalEngineeringScreen:
    """Run deterministic, cost-aware historical engineering checks."""

    def __init__(
        self,
        config: ScreeningConfig | None = None,
        *,
        cost_model: AshareCostModel | None = None,
    ) -> None:
        self.config = config or ScreeningConfig()
        self.cost_model = cost_model or AshareCostModel()

    def run(
        self,
        snapshot: Any,
        candidates: Iterable[HistoricalCandidate | Mapping[str, Any] | str],
        *,
        seed: int = 20260814,
    ) -> HistoricalScreeningResult:
        seed = int(seed)
        if seed < 0:
            raise ValueError("seed must be non-negative")
        snapshot_id = _snapshot_id(snapshot)
        features = _snapshot_frame(snapshot, "features")
        labels = _snapshot_frame(snapshot, "labels", required=False)
        cutoff = _snapshot_cutoff(snapshot, features)
        normalized_features, normalized_labels, leakage = _prepare_inputs(
            features, labels, cutoff
        )
        dates = sorted(normalized_features["_session_date"].dropna().unique().tolist())
        folds = _make_folds(dates, self.config)
        if len(folds) < self.config.minimum_oos_windows:
            raise ValueError(
                f"历史数据不足以生成至少{self.config.minimum_oos_windows}个样本外窗口"
            )
        normalized_candidates = tuple(_coerce_candidate(item) for item in candidates)
        if not normalized_candidates:
            raise ValueError("至少需要一个历史筛查候选")
        candidate_ids = [candidate.candidate_id for candidate in normalized_candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate_id must be unique")

        trials = tuple(
            self._run_trial(
                candidate,
                normalized_features,
                normalized_labels,
                folds,
                seed=seed,
                base_leakage=leakage,
            )
            for candidate in normalized_candidates
        )
        status = (
            "ENGINEERING_BLOCKED"
            if any(
                trial.status in {"ENGINEERING_BLOCKED", "UNAVAILABLE_DEPENDENCY"}
                for trial in trials
            )
            else "ENGINEERING_SCREENED"
        )
        return HistoricalScreeningResult(
            snapshot_id=snapshot_id,
            random_seed=seed,
            config=self.config,
            folds=folds,
            trials=trials,
            status=status,
        )

    def _run_trial(
        self,
        candidate: HistoricalCandidate,
        features: pd.DataFrame,
        labels: pd.DataFrame,
        folds: tuple[ScreeningFold, ...],
        *,
        seed: int,
        base_leakage: tuple[str, ...],
    ) -> ScreeningTrial:
        family = candidate.model_family
        if base_leakage:
            return _blocked_trial(candidate, seed, base_leakage)
        if family in {"lightgbm", "qlib", "qlib-double-ensemble"} and not _optional_available(
            family
        ):
            return ScreeningTrial(
                candidate_id=candidate.candidate_id,
                model_family=family,
                canonical_parameters=candidate.canonical_parameters(),
                random_seed=seed,
                status="UNAVAILABLE_DEPENDENCY",
                governance_state="NON_PROMOTIONAL_ENGINEERING",
                evidence_mode=NON_PROMOTIONAL_ENGINEERING,
                validation_score=float("nan"),
                fold_metrics=(),
                leakage_flags=base_leakage,
                reproducible=True,
                pbo=float("nan"),
                deflated_sharpe=float("nan"),
                cpcv={"paths": 0, "splits": 0, "status": "UNAVAILABLE_DEPENDENCY"},
            )

        fold_metrics: list[WindowMetrics] = []
        validation_scores: list[float] = []
        trial_flags: list[str] = []
        try:
            merged = _merge_features_labels(features, labels, candidate)
            if merged.empty:
                return _blocked_trial(
                    candidate, seed, ("missing_or_invalid_labels",)
                )
            for fold in folds:
                train = merged.loc[
                    merged["_session_date"].between(
                        fold.train_start, fold.train_end, inclusive="both"
                    )
                ].copy()
                train = _mature_training_labels(train, fold.train_end)
                validation = merged.loc[
                    merged["_session_date"].between(
                        fold.validation_start, fold.validation_end, inclusive="both"
                    )
                ].copy()
                test = merged.loc[
                    merged["_session_date"].between(
                        fold.test_start, fold.test_end, inclusive="both"
                    )
                ].copy()
                train_score, validation_score = self._scores(
                    candidate, train, validation, seed=seed + fold.index
                )
                validation_scores.append(validation_score)
                test_score, _ = self._scores(
                    candidate, train, test, seed=seed + fold.index
                )
                fold_metrics.append(
                    _window_metrics(
                        fold,
                        test,
                        test_score,
                        config=self.config,
                        cost_model=self.cost_model,
                        pbo=0.0,
                        deflated_sharpe=0.0,
                        cpcv_paths=len(folds),
                    )
                )
        except Exception as exc:
            trial_flags.append(_safe_error_code(exc))
            return _blocked_trial(candidate, seed, tuple(sorted(set(trial_flags))))

        pbo = _pbo(validation_scores)
        dsr = _deflated_sharpe([item.net_cost_return for item in fold_metrics], len(folds))
        fold_metrics = [
            WindowMetrics(
                **{
                    **asdict(item),
                    "pbo": pbo,
                    "deflated_sharpe": dsr,
                }
            )
            for item in fold_metrics
        ]
        trial_flags.extend(
            flag
            for item in fold_metrics
            for flag in item.leakage_flags
            if flag not in trial_flags and not flag.endswith("_for_cost")
        )
        status = "ENGINEERING_BLOCKED" if trial_flags else "ENGINEERING_SCREENED"
        return ScreeningTrial(
            candidate_id=candidate.candidate_id,
            model_family=family,
            canonical_parameters=candidate.canonical_parameters(),
            random_seed=seed,
            status=status,
            governance_state=(
                "ENGINEERING_BLOCKED"
                if status == "ENGINEERING_BLOCKED"
                else NON_PROMOTIONAL_ENGINEERING
            ),
            evidence_mode=NON_PROMOTIONAL_ENGINEERING,
            validation_score=_finite_or_nan(float(np.nanmean(validation_scores))),
            fold_metrics=tuple(fold_metrics),
            leakage_flags=tuple(sorted(set(trial_flags))),
            reproducible=True,
            pbo=pbo,
            deflated_sharpe=dsr,
            cpcv={
                "paths": len(folds),
                "splits": len(folds),
                "embargo_sessions": self.config.embargo_sessions,
                "status": "DIAGNOSTIC_ONLY",
            },
            diagnostics={
                "dropped_nonfinite_label_count": int(
                    labels.attrs.get("dropped_nonfinite_label_count", 0)
                )
            },
        )

    def _scores(
        self,
        candidate: HistoricalCandidate,
        train: pd.DataFrame,
        target: pd.DataFrame,
        *,
        seed: int,
    ) -> tuple[pd.Series, float]:
        if target.empty:
            raise ValueError("evaluation window has no rows")
        label = train["_label"].astype(float)
        if label.isna().all() or label.nunique(dropna=True) < 1:
            raise ValueError("training labels are unavailable")
        feature_columns = _feature_columns(train, candidate)
        x_train = train[feature_columns].apply(pd.to_numeric, errors="coerce")
        x_target = target[feature_columns].apply(pd.to_numeric, errors="coerce")
        if x_train.isna().any().any() or x_target.isna().any().any():
            raise ValueError("feature contains non-numeric or missing values")
        means = x_train.mean()
        scales = x_train.std(ddof=0).replace(0, 1.0).fillna(1.0)
        x_train = (x_train - means) / scales
        x_target = (x_target - means) / scales

        if candidate.scorer is not None:
            values = _call_scorer(
                candidate.scorer,
                _without_realized_labels(target),
                _without_realized_labels(train),
            )
            repeat_values = _call_scorer(
                candidate.scorer,
                _without_realized_labels(target),
                _without_realized_labels(train),
            )
            scores = pd.Series(values, index=target.index, dtype="float64")
            repeat_scores = pd.Series(
                repeat_values, index=target.index, dtype="float64"
            )
            if not np.array_equal(
                scores.to_numpy(), repeat_scores.to_numpy(), equal_nan=True
            ):
                raise ValueError("reproducibility failure")
        elif candidate.model_family in {"rule-baseline", "rule", "momentum"}:
            direction = float(candidate.parameters.get("direction", 1.0))
            scores = x_target.iloc[:, 0].astype(float) * direction
        elif candidate.model_family in {"sklearn-logistic", "logistic", "portable-logistic"}:
            try:
                from sklearn.linear_model import LogisticRegression
            except ImportError as exc:
                raise ImportError("sklearn dependency is unavailable") from exc
            model = LogisticRegression(random_state=seed, max_iter=1000)
            model.fit(x_train, (label > 0).astype(int))
            scores = pd.Series(model.predict_proba(x_target)[:, 1], index=target.index)
        elif candidate.model_family in {"lightgbm", "qlib", "qlib-double-ensemble"}:
            # Availability is checked before entering this path.  Keep the
            # adapters intentionally small; installing large optional packages
            # is never part of an engineering screen.
            raise RuntimeError("optional adapter is not enabled in this lightweight screen")
        else:
            raise ValueError(f"unsupported model family: {candidate.model_family}")

        if len(scores) != len(target) or not np.isfinite(scores.to_numpy(dtype=float)).all():
            raise ValueError("candidate produced invalid scores")
        # The caller evaluates validation and test independently.  A target
        # score has no effect on test selection; this value is only a diagnostic
        # carried from the validation branch.
        validation_score = _score_summary(target, scores, self.config.top_k)
        return scores, validation_score


def screen(
    config: ScreeningConfig | None = None,
    *,
    cost_model: AshareCostModel | None = None,
) -> HistoricalEngineeringScreen:
    """Convenience factory used by CLI and research notebooks."""

    return HistoricalEngineeringScreen(config, cost_model=cost_model)


def _snapshot_frame(snapshot: Any, name: str, *, required: bool = True) -> pd.DataFrame:
    value = getattr(snapshot, name, None)
    if value is None:
        if required:
            raise ValueError(f"snapshot.{name} is required")
        return pd.DataFrame()
    if callable(value):
        value = value()
    if not isinstance(value, pd.DataFrame):
        raise TypeError(f"snapshot.{name} must be a pandas DataFrame")
    if value.empty and required:
        raise ValueError(f"snapshot.{name} cannot be empty")
    return value.copy(deep=True)


def _snapshot_id(snapshot: Any) -> str:
    value = getattr(snapshot, "canonical_sha256", None) or getattr(snapshot, "snapshot_id", None)
    if value is not None and str(value).strip():
        return str(value).strip()
    features = _snapshot_frame(snapshot, "features")
    labels = _snapshot_frame(snapshot, "labels", required=False)
    return _digest({"features": _frame_payload(features), "labels": _frame_payload(labels)})


def _snapshot_cutoff(snapshot: Any, features: pd.DataFrame) -> date:
    value = getattr(snapshot, "signal_cutoff", None) or getattr(snapshot, "signal_date", None)
    if value is not None:
        return _as_date(value)
    column = _first_date_column(features)
    return max(_as_date(item) for item in features[column].dropna())


def _prepare_inputs(
    features: pd.DataFrame,
    labels: pd.DataFrame,
    cutoff: date,
) -> tuple[pd.DataFrame, pd.DataFrame, tuple[str, ...]]:
    if "symbol" not in features.columns:
        raise ValueError("features requires symbol")
    feature_date = _first_date_column(features)
    prepared = features.copy(deep=True)
    prepared["symbol"] = prepared["symbol"].astype(str).str.zfill(6)
    prepared["_session_date"] = pd.to_datetime(prepared[feature_date], errors="coerce").dt.date
    leakage: list[str] = []
    if prepared["_session_date"].isna().any():
        leakage.append("invalid_feature_date")
    if "available_at" in prepared.columns:
        available = pd.to_datetime(prepared["available_at"], errors="coerce").dt.date
        if available.isna().any() or (available > prepared["_session_date"]).fillna(False).any():
            leakage.append("future_feature_available_at")
        if (available > cutoff).fillna(False).any():
            leakage.append("future_feature_cutoff")
    if prepared.duplicated(["symbol", "_session_date"]).any():
        leakage.append("duplicate_feature_session")
    label_frame = labels.copy(deep=True)
    if label_frame.empty:
        label_frame = pd.DataFrame(columns=["symbol", "_session_date", "_label"])
    else:
        if "symbol" not in label_frame.columns:
            raise ValueError("labels requires symbol")
        label_date = _first_date_column(label_frame)
        label_frame["symbol"] = label_frame["symbol"].astype(str).str.zfill(6)
        label_frame["_session_date"] = pd.to_datetime(
            label_frame[label_date], errors="coerce"
        ).dt.date
        label_column = next(
            (name for name in _LABEL_COLUMNS if name in label_frame.columns), None
        )
        if label_column is None:
            raise ValueError("labels requires a forward return label")
        label_frame["_label"] = pd.to_numeric(
            label_frame[label_column], errors="coerce"
        )
        finite_labels = np.isfinite(label_frame["_label"])
        label_frame.attrs["dropped_nonfinite_label_count"] = int((~finite_labels).sum())
        # Tail NaNs are an expected property of forward labels.  They do not
        # participate in screening and must not make maturity validation fail.
        label_frame = label_frame.loc[finite_labels].copy()
        maturity_column = next(
            (
                name
                for name in ("label_available_at", "maturity_date")
                if name in label_frame.columns
            ),
            None,
        )
        label_frame["_maturity_error"] = False
        session_dates = sorted(prepared["_session_date"].dropna().unique().tolist())
        session_index = {value: index for index, value in enumerate(session_dates)}
        if maturity_column is not None:
            label_frame["_maturity_date"] = pd.to_datetime(
                label_frame[maturity_column], errors="coerce"
            ).dt.date
            label_frame["_maturity_error"] = (
                label_frame["_maturity_date"].isna()
                | (label_frame["_maturity_date"] <= label_frame["_session_date"])
            )
        elif "horizon_days" in label_frame.columns:
            label_frame["_maturity_date"] = None
            for row_index, row in label_frame.iterrows():
                raw_horizon = pd.to_numeric(
                    pd.Series([row["horizon_days"]]), errors="coerce"
                ).iloc[0]
                valid_horizon = (
                    pd.notna(raw_horizon)
                    and float(raw_horizon).is_integer()
                    and int(raw_horizon) in {5, 10, 20}
                )
                try:
                    date_index = session_index[row["_session_date"]]
                except KeyError:
                    date_index = -1
                maturity_index = date_index + int(raw_horizon) if valid_horizon else -1
                if maturity_index < 0 or maturity_index >= len(session_dates):
                    label_frame.at[row_index, "_maturity_error"] = True
                else:
                    label_frame.at[row_index, "_maturity_date"] = session_dates[
                        maturity_index
                    ]
        else:
            label_frame["_maturity_date"] = None
            label_frame["_maturity_error"] = True
        if label_frame["_maturity_error"].astype(bool).any():
            leakage.append("invalid_label_maturity")
    return prepared, label_frame, tuple(sorted(set(leakage)))


def _merge_features_labels(
    features: pd.DataFrame,
    labels: pd.DataFrame,
    candidate: HistoricalCandidate,
) -> pd.DataFrame:
    feature_columns = _feature_columns(features, candidate)
    # Execution cost estimation needs an unadjusted price even when a
    # candidate explicitly selects only one score column.  Keep ``close`` in
    # the merged evaluation frame as audit data; _scores still uses only the
    # candidate's declared feature columns, so this cannot silently change a
    # model's input schema.
    merged_columns = list(feature_columns)
    if "close" in features.columns and "close" not in merged_columns:
        merged_columns.append("close")
    left = features[["symbol", "_session_date", *merged_columns]].copy()
    if labels.empty:
        return pd.DataFrame()
    right = labels[
        ["symbol", "_session_date", "_label", "_maturity_date", "_maturity_error"]
    ].copy()
    merged = left.merge(right, on=["symbol", "_session_date"], how="inner", validate="one_to_one")
    merged["_label"] = pd.to_numeric(merged["_label"], errors="coerce")
    merged = merged.loc[np.isfinite(merged["_label"])].copy()
    return merged.sort_values(["_session_date", "symbol"], kind="stable").reset_index(drop=True)


def _mature_training_labels(frame: pd.DataFrame, train_end: date) -> pd.DataFrame:
    """Keep only outcomes known by the end of the training window."""

    maturity = pd.to_datetime(frame["_maturity_date"], errors="coerce").dt.date
    if frame["_maturity_error"].astype(bool).any() or maturity.isna().any():
        raise ValueError("unknown label maturity at training boundary")
    return frame.loc[maturity.le(train_end)].copy()


def _feature_columns(frame: pd.DataFrame, candidate: HistoricalCandidate) -> list[str]:
    if candidate.score_column is not None:
        if candidate.score_column not in frame.columns:
            raise KeyError(f"missing score column: {candidate.score_column}")
        if _is_realized_label_column(candidate.score_column):
            raise ValueError("score column cannot be a realized label")
        return [candidate.score_column]
    blocked = {
        "symbol",
        "date",
        "signal_date",
        "available_at",
        "_session_date",
        "_label",
        *_REALIZED_LABEL_COLUMNS,
    }
    columns = [
        str(column)
        for column in frame.columns
        if str(column) not in blocked
        and not _is_realized_label_column(str(column))
        and not str(column).startswith("forward_")
        and not str(column).startswith("label")
    ]
    numeric = [column for column in sorted(columns) if pd.api.types.is_numeric_dtype(frame[column])]
    if not numeric:
        raise ValueError("features contain no numeric feature columns")
    return numeric


def _is_realized_label_column(column: str) -> bool:
    normalized = str(column).strip().lower()
    return normalized in _REALIZED_LABEL_COLUMNS or normalized.startswith(
        ("forward_", "excess_return", "research_return", "future_return", "target_", "label_")
    )


def _without_realized_labels(frame: pd.DataFrame) -> pd.DataFrame:
    """Return scorer-visible data without any realized outcome columns."""

    columns = [
        column for column in frame.columns if not _is_realized_label_column(str(column))
    ]
    return frame.loc[:, columns].copy(deep=True)


def _make_folds(dates: Sequence[date], config: ScreeningConfig) -> tuple[ScreeningFold, ...]:
    ordered = sorted({_as_date(item) for item in dates})
    span = (
        config.train_sessions
        + config.validation_sessions
        + config.embargo_sessions
        + config.test_sessions
    )
    folds: list[ScreeningFold] = []
    start = 0
    while start + span <= len(ordered):
        train_end = start + config.train_sessions
        validation_end = train_end + config.validation_sessions
        embargo_end = validation_end + config.embargo_sessions
        folds.append(
            ScreeningFold(
                index=len(folds),
                train_start=ordered[start],
                train_end=ordered[train_end - 1],
                validation_start=ordered[train_end],
                validation_end=ordered[validation_end - 1],
                embargo_start=ordered[validation_end],
                embargo_end=ordered[embargo_end - 1],
                test_start=ordered[embargo_end],
                test_end=ordered[start + span - 1],
                train_sessions=config.train_sessions,
                validation_sessions=config.validation_sessions,
                embargo_sessions=config.embargo_sessions,
                test_sessions=config.test_sessions,
            )
        )
        start += config.test_sessions
    return tuple(folds)


def _window_metrics(
    fold: ScreeningFold,
    frame: pd.DataFrame,
    scores: pd.Series,
    *,
    config: ScreeningConfig,
    cost_model: AshareCostModel,
    pbo: float,
    deflated_sharpe: float,
    cpcv_paths: int,
) -> WindowMetrics:
    if frame.empty:
        raise ValueError("test window has no rows")
    working = frame.copy()
    working["_score"] = pd.to_numeric(scores, errors="coerce").to_numpy()
    working = working.loc[np.isfinite(working["_score"])].copy()
    if working.empty:
        raise ValueError("test window scores are empty")
    top_k = max(1, min(config.top_k, len(working)))
    selected = working.sort_values(
        ["_session_date", "_score", "symbol"], ascending=[True, False, True]
    )
    selected = selected.groupby("_session_date", sort=True, group_keys=False).head(top_k)
    gross = float(selected["_label"].mean())
    # Use the same conservative A-share cost object as the execution research
    # path.  The configured round-trip assumption is the floor; fees can only
    # increase the net cost.
    cost_rate, cost_flags = _selected_cost_rate(
        selected, config=config, cost_model=cost_model
    )
    net = gross - cost_rate
    selected["_net_return"] = selected["_label"] - selected["_cost_rate"]
    daily = selected.groupby("_session_date", sort=True)["_net_return"].mean()
    equity = (1.0 + daily.fillna(0.0)).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    max_drawdown = float(drawdown.min()) if not drawdown.empty else 0.0
    rank_ic = _correlation(working["_score"], working["_label"])
    probabilities = 1.0 / (1.0 + np.exp(-np.clip(working["_score"].to_numpy(), -40, 40)))
    observed = (working["_label"].to_numpy() > 0).astype(float)
    brier = float(np.mean((probabilities - observed) ** 2))
    ece = _ece(probabilities, observed)
    turnover = _turnover(selected)
    regime = "BULL" if float(working["_label"].mean()) > 0 else "BEAR_OR_FLAT"
    capacity = float(
        min(1.0, len(selected) / max(1, config.top_k * len(working["_session_date"].unique())))
    )
    return WindowMetrics(
        fold_index=fold.index,
        gross_excess_return=gross,
        net_cost_return=net,
        rank_ic=rank_ic,
        brier=brier,
        ece=ece,
        turnover=turnover,
        estimated_cost=cost_rate,
        max_drawdown=max_drawdown,
        capacity=capacity,
        capacity_ok=capacity >= config.capacity_limit,
        regime=regime,
        pbo=pbo,
        deflated_sharpe=deflated_sharpe,
        cpcv_paths=cpcv_paths,
        leakage_flags=cost_flags,
        reproducible=True,
    )


def _selected_cost_rate(
    selected: pd.DataFrame,
    *,
    config: ScreeningConfig,
    cost_model: AshareCostModel,
) -> tuple[float, tuple[str, ...]]:
    price_column = next(
        (column for column in ("price", "close") if column in selected), None
    )
    quantity_column = next(
        (column for column in ("quantity", "shares", "position_size") if column in selected),
        None,
    )
    if price_column is None:
        raise ValueError("cost data unavailable: missing unadjusted price or close")
    rates: list[float] = []
    for _, row in selected.iterrows():
        price = pd.to_numeric(pd.Series([row[price_column]]), errors="coerce").iloc[0]
        quantity = row[quantity_column] if quantity_column is not None else cost_model.lot_size
        quantity = pd.to_numeric(pd.Series([quantity]), errors="coerce").iloc[0]
        if (
            not pd.notna(price)
            or not pd.notna(quantity)
            or float(price) <= 0
            or float(quantity) <= 0
        ):
            raise ValueError("cost data unavailable: invalid unadjusted price or quantity")
        quantity = max(cost_model.lot_size, cost_model.fillable_quantity(float(quantity)))
        buy = cost_model.estimate(side="BUY", price=float(price), quantity=quantity).total
        sell = cost_model.estimate(side="SELL", price=float(price), quantity=quantity).total
        rates.append(max(float(config.round_trip_cost), (buy + sell) / (float(price) * quantity)))
    selected["_cost_rate"] = rates
    return float(np.mean(rates)), ()


def _score_summary(frame: pd.DataFrame, scores: pd.Series, top_k: int) -> float:
    if frame.empty:
        return float("nan")
    ranked = frame.copy()
    ranked["_score"] = scores.to_numpy()
    return float(
        ranked.sort_values(
            ["_session_date", "_score", "symbol"],
            ascending=[True, False, True],
            kind="stable",
        )
        .groupby("_session_date", sort=True, group_keys=False)
        .head(max(1, top_k))["_label"]
        .mean()
    )


def _top_mean(frame: pd.DataFrame, scores: pd.Series) -> float:
    if frame.empty or scores.empty:
        return float("nan")
    return _score_summary(frame, scores, 10)


def _turnover(selected: pd.DataFrame) -> float:
    groups = [
        set(group["symbol"].astype(str))
        for _, group in selected.groupby("_session_date", sort=True)
    ]
    if len(groups) < 2:
        return 0.0
    return float(
        np.mean(
            [
                1.0 - len(left & right) / max(len(left | right), 1)
                for left, right in zip(groups, groups[1:])
            ]
        )
    )


def _correlation(left: pd.Series, right: pd.Series) -> float:
    value = left.corr(right, method="spearman")
    return float(value) if pd.notna(value) and math.isfinite(float(value)) else 0.0


def _ece(probabilities: np.ndarray, observed: np.ndarray, bins: int = 10) -> float:
    if len(probabilities) == 0:
        return 0.0
    total = 0.0
    for lower, upper in zip(
        np.linspace(0, 1, bins, endpoint=False), np.linspace(0, 1, bins + 1)[1:]
    ):
        mask = (probabilities >= lower) & (
            probabilities <= upper if upper == 1 else probabilities < upper
        )
        if mask.any():
            total += float(mask.mean()) * abs(
                float(probabilities[mask].mean() - observed[mask].mean())
            )
    return total


def _pbo(values: Sequence[float]) -> float:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    if len(finite) < 2:
        return 0.5
    median = float(np.median(finite))
    return float(sum(value <= median for value in finite) / len(finite))


def _deflated_sharpe(values: Sequence[float], trials: int) -> float:
    finite = np.asarray([value for value in values if math.isfinite(float(value))], dtype="float64")
    if len(finite) < 2:
        return 0.0
    std = float(finite.std(ddof=1))
    if std == 0:
        return 0.0
    return float(
        finite.mean() / std * math.sqrt(252)
        - math.sqrt(max(0.0, 2 * math.log(max(1, trials))))
    )


def _optional_available(family: str) -> bool:
    module = "lightgbm" if family == "lightgbm" else "qlib"
    # ``find_spec`` avoids importing optional packages (some of them emit
    # warnings or initialise global state merely on import).  Their adapters
    # remain disabled until a version-pinned, prospective-safe implementation
    # is registered, even when the package happens to be installed.
    importlib.util.find_spec(module)
    return False  # adapters remain opt-in until their versions are frozen


def _coerce_candidate(value: HistoricalCandidate | Mapping[str, Any] | str) -> HistoricalCandidate:
    if isinstance(value, HistoricalCandidate):
        return value
    if isinstance(value, str):
        return HistoricalCandidate(value)
    if isinstance(value, Mapping):
        payload = dict(value)
        return HistoricalCandidate(
            candidate_id=str(payload.pop("candidate_id", payload.pop("id", ""))),
            model_family=str(payload.pop("model_family", payload.pop("family", "rule-baseline"))),
            parameters=payload.pop("parameters", {}),
            score_column=payload.pop("score_column", None),
            scorer=payload.pop("scorer", None),
        )
    raise TypeError("candidate must be HistoricalCandidate, mapping, or string")


def _call_scorer(
    scorer: Callable[..., Any], target: pd.DataFrame, train: pd.DataFrame
) -> Any:
    """Call a user-supplied scorer without making its signature a dependency.

    Research adapters in the project use either ``(target, train)`` or just
    ``(target)``.  Restricting this helper to those two forms keeps historical
    screening deterministic and avoids catching errors raised *inside* a
    scorer as signature errors.
    """

    try:
        import inspect

        parameters = inspect.signature(scorer).parameters
    except (TypeError, ValueError):
        parameters = None
    if parameters is not None and len(parameters) == 1:
        return scorer(target)
    return scorer(target, train)


def _blocked_trial(
    candidate: HistoricalCandidate,
    seed: int,
    flags: tuple[str, ...],
) -> ScreeningTrial:
    return ScreeningTrial(
            candidate_id=candidate.candidate_id,
            model_family=candidate.model_family,
            canonical_parameters=candidate.canonical_parameters(),
        random_seed=seed,
        status="ENGINEERING_BLOCKED",
        governance_state="ENGINEERING_BLOCKED",
        evidence_mode=NON_PROMOTIONAL_ENGINEERING,
        validation_score=float("nan"),
        fold_metrics=(),
        leakage_flags=tuple(sorted(set(flags))),
        reproducible="reproducibility_failure" not in flags,
        pbo=float("nan"),
        deflated_sharpe=float("nan"),
        cpcv={"paths": 0, "splits": 0, "status": "ENGINEERING_BLOCKED"},
    )


def _trial_dict(trial: ScreeningTrial) -> dict[str, Any]:
    return {
            "candidate_id": trial.candidate_id,
            "model_family": trial.model_family,
            "canonical_parameters": _json_safe(trial.canonical_parameters),
        "random_seed": trial.random_seed,
        "status": trial.status,
        "governance_state": trial.governance_state,
        "evidence_mode": trial.evidence_mode,
        "validation_score": _json_safe(trial.validation_score),
        "fold_metrics": [_json_safe(asdict(item)) for item in trial.fold_metrics],
        "leakage_flags": list(trial.leakage_flags),
        "reproducible": trial.reproducible,
        "pbo": _json_safe(trial.pbo),
        "deflated_sharpe": _json_safe(trial.deflated_sharpe),
            "cpcv": _json_safe(trial.cpcv),
        "diagnostics": _json_safe(trial.diagnostics),
    }


def _first_date_column(frame: pd.DataFrame) -> str:
    for column in _DATE_COLUMNS:
        if column in frame.columns:
            return column
    raise ValueError("research frame requires a date column")


def _as_date(value: Any) -> date:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid date: {value!r}")
    return parsed.date()


def _safe_error_code(exc: BaseException) -> str:
    text = str(exc).lower()
    if "reproducibility" in text:
        return "reproducibility_failure"
    if "cost data unavailable" in text:
        return "cost_data_unavailable"
    if "unknown label maturity" in text:
        return "missing_input"
    if "future" in text:
        return "future_feature"
    if "missing" in text:
        return "missing_input"
    if isinstance(exc, ImportError):
        return "optional_dependency_unavailable"
    return "candidate_execution_error"


def _finite_or_nan(value: float) -> float:
    return value if math.isfinite(value) else float("nan")


def _frame_payload(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "columns": [str(column) for column in frame.columns],
        "rows": _json_safe(frame.astype(object).where(pd.notna(frame), None).to_dict("records")),
    }


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, (date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)


def _digest(value: Any) -> str:
    encoded = json.dumps(
        _json_safe(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "HistoricalCandidate",
    "HistoricalEngineeringScreen",
    "HistoricalScreeningResult",
    "NON_PROMOTIONAL_ENGINEERING",
    "ScreeningConfig",
    "ScreeningFold",
    "ScreeningTrial",
    "WindowMetrics",
    "screen",
]
