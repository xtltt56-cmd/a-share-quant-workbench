"""Environment-backed application settings with a paper-only safety guard."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    environment: str = "development"
    provider: str = "akshare"
    data_dir: Path = Path("data")
    database_path: Path = Path("quant.duckdb")
    log_level: str = "INFO"
    request_timeout_seconds: int = 30
    request_retry_count: int = 3
    request_delay_seconds: float = 0.25
    execution_mode: str = "paper"
    allow_live: bool = False
    tushare_token: SecretStr | None = Field(default=None, validation_alias="TUSHARE_TOKEN")
    rqdata_config_path: Path | None = None

    model_config = SettingsConfigDict(
        env_prefix="A_SHARE_QUANT_",
        extra="ignore",
        case_sensitive=False,
    )

    @model_validator(mode="after")
    def enforce_paper_only(self) -> Settings:
        if self.execution_mode.lower() != "paper" or self.allow_live:
            raise ValueError("V1 execution mode must remain paper and allow_live must be false")
        if self.provider.lower() not in {"akshare", "baostock", "tushare"}:
            raise ValueError("provider must be akshare, baostock, or tushare")
        return self

    @classmethod
    def load(cls, *, env_file: Path | None = None) -> Settings:
        if env_file is None:
            return cls()

        # An explicitly supplied file is a testable/operator-selected snapshot.
        # Pass its values as constructor arguments so a process-wide secret (for
        # example TUSHARE_TOKEN) cannot silently contaminate an isolated run.
        values = dotenv_values(env_file)
        prefix = str(cls.model_config.get("env_prefix", ""))
        explicit: dict[str, str] = {}
        for field_name, field_info in cls.model_fields.items():
            alias = field_info.validation_alias
            env_name = str(alias) if isinstance(alias, str) else f"{prefix}{field_name.upper()}"
            value = values.get(env_name)
            if value is not None:
                explicit[field_name] = value
        explicit["_env_file"] = str(env_file)
        return cls(**explicit)


@dataclass(frozen=True)
class PITConfig:
    announcement_day_policy: str = "next_trading_day"
    allow_same_day_announcement: bool = False
    unknown_announcement_date: str = "reject"


@dataclass(frozen=True)
class SplitConfig:
    train_ratio: float = 0.6
    validation_ratio: float = 0.2
    test_ratio: float = 0.2

    def __post_init__(self) -> None:
        total = self.train_ratio + self.validation_ratio + self.test_ratio
        if any(value <= 0 for value in (self.train_ratio, self.validation_ratio, self.test_ratio)):
            raise ValueError("time split ratios must be positive")
        if abs(total - 1.0) > 1e-9:
            raise ValueError("time split ratios must sum to 1")


@dataclass(frozen=True)
class LabelConfig:
    name: str = "forward_excess_return_5d"
    horizon_days: int = 5

    def __post_init__(self) -> None:
        if self.name != "forward_excess_return_5d":
            raise ValueError("stage 2 supports only forward_excess_return_5d")
        if self.horizon_days != 5:
            raise ValueError("stage 2 fixes the label horizon at 5 trading days")


@dataclass(frozen=True)
class ComparisonConfig:
    benchmark: str = "000300"
    seed: int = 42
    max_positions: int = 10
    max_single_position: float = 0.15
    rebalance_frequency: str = "weekly"
    split: SplitConfig = SplitConfig()


@dataclass(frozen=True)
class Stage2Config:
    pit: PITConfig
    label: LabelConfig
    comparison: ComparisonConfig

    @classmethod
    def load(cls, *, config_dir: Path) -> Stage2Config:
        pit_data = _read_yaml_section(config_dir / "pit.yaml", "pit")
        qlib_data = _read_yaml_section(config_dir / "qlib.yaml", "qlib")
        experiment_data = _read_yaml_section(config_dir / "experiments.yaml", "comparison")
        split_data = experiment_data.pop("split", {})

        return cls(
            pit=PITConfig(
                announcement_day_policy=pit_data.get(
                    "announcement_day_policy", "next_trading_day"
                ),
                allow_same_day_announcement=bool(
                    pit_data.get("allow_same_day_announcement", False)
                ),
                unknown_announcement_date=pit_data.get("unknown_announcement_date", "reject"),
            ),
            label=LabelConfig(
                name=qlib_data.get("label", "forward_excess_return_5d"),
                horizon_days=int(qlib_data.get("label_horizon_days", 5)),
            ),
            comparison=ComparisonConfig(
                benchmark=str(experiment_data.get("benchmark", "000300")),
                seed=int(experiment_data.get("seed", 42)),
                max_positions=int(experiment_data.get("max_positions", 10)),
                max_single_position=float(experiment_data.get("max_single_position", 0.15)),
                rebalance_frequency=str(experiment_data.get("rebalance_frequency", "weekly")),
                split=SplitConfig(
                    train_ratio=float(split_data.get("train_ratio", 0.6)),
                    validation_ratio=float(split_data.get("validation_ratio", 0.2)),
                    test_ratio=float(split_data.get("test_ratio", 0.2)),
                ),
            ),
        )


def _read_yaml_section(path: Path, section: str) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    value = raw.get(section, {})
    if not isinstance(value, dict):
        raise ValueError(f"configuration section must be a mapping: {section}")
    return dict(value)
