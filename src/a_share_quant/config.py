"""Environment-backed application settings with a paper-only safety guard."""

from __future__ import annotations

from pathlib import Path

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
        if self.provider.lower() not in {"akshare", "tushare"}:
            raise ValueError("provider must be akshare or tushare")
        return self

    @classmethod
    def load(cls, *, env_file: Path | None = None) -> Settings:
        kwargs = {"_env_file": str(env_file)} if env_file is not None else {}
        return cls(**kwargs)
