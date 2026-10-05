"""Optional runtime settings: no secrets, remote URLs or implicit cloud fallback."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AgentRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    enabled: bool = False
    backend: Literal["LOCAL", "CLOUD"] = "CLOUD"
    local_model: str = Field(default="qwen3.5-local:9b-q4_k_m", min_length=1, max_length=128)
    cloud_model: Literal["deepseek-flash"] = "deepseek-flash"
    api_key_env: Literal["DEEPSEEK_API_KEY"] = "DEEPSEEK_API_KEY"
    payment_authorized: bool = False
    # A fee ceiling is not permission to start the experiment. Keep generation
    # off while the user reviews the non-generating connection check.
    generation_authorized: bool = False
    daily_budget_usd: str = "0"
    task_budget_usd: str = "0"
    experiment_budget_cny: str = "0"
    input_cny_per_million: Literal["2.00"] = "2.00"
    output_cny_per_million: Literal["8.00"] = "8.00"
    price_version_cny: Literal["deepseek-flash-2026-10-05-peak-cny"] = (
        "deepseek-flash-2026-10-05-peak-cny"
    )
    max_model_requests: int = Field(default=3, ge=1, le=3)
    max_tool_calls: int = Field(default=6, ge=1, le=6)
    deadline_seconds: int = Field(default=90, ge=5, le=90)
    max_context_bytes: int = Field(default=16384, ge=4096, le=32768)
    max_output_tokens: int = Field(default=512, ge=128, le=2048)
    local_context_tokens: int = Field(default=4096, ge=4096, le=8192)
    # Conservative peak, uncached USD rates checked against official docs.
    # Only this model is supported; a new model requires reviewed price bounds.
    price_version: Literal["deepseek-flash-2026-10-05-peak-usd"] = (
        "deepseek-flash-2026-10-05-peak-usd"
    )
    input_usd_per_million: Literal["0.30"] = "0.30"
    output_usd_per_million: Literal["1.20"] = "1.20"

    @field_validator("local_model")
    @classmethod
    def offline_model(cls, value: str) -> str:
        if not all(c.isalnum() or c in "._:-" for c in value) or "cloud" in value.lower():
            raise ValueError("本地模型名称无效；禁止云端模型别名")
        return value

    @field_validator("daily_budget_usd", "task_budget_usd")
    @classmethod
    def budget(cls, value: str) -> str:
        if len(value) > 16:
            raise ValueError("预算格式无效")
        try:
            amount = Decimal(value)
        except ArithmeticError as exc:
            raise ValueError("预算格式无效") from exc
        if not amount.is_finite() or amount < 0 or amount > 100:
            raise ValueError("预算须为 0 至 100 美元")
        return str(amount)

    @model_validator(mode="after")
    def authorized_cloud(self) -> AgentRuntimeConfig:
        try:
            trial = Decimal(self.experiment_budget_cny)
        except ArithmeticError as exc:
            raise ValueError("实验预算格式无效") from exc
        if (len(self.experiment_budget_cny) > 16 or not trial.is_finite()
            or trial < 0 or trial > 3):
            raise ValueError("本次实验累计限额须为 0 至 3 元")
        if Decimal(self.daily_budget_usd) > Decimal("0.10"):
            raise ValueError("本次授权每日费用上限不得超过 0.10 美元")
        if Decimal(self.task_budget_usd) > Decimal("0.02"):
            raise ValueError("本次授权每任务费用上限不得超过 0.02 美元")
        if self.enabled and self.backend == "CLOUD" and (
            not self.payment_authorized
            or Decimal(self.daily_budget_usd) <= 0
            or Decimal(self.task_budget_usd) <= 0
        ):
            raise ValueError("启用云端须明确授权每日及每任务费用上限")
        if self.enabled and self.backend == "CLOUD" and self.generation_authorized and trial <= 0:
            raise ValueError("开始本次实验前须设置有效的累计人民币限额")
        return self


QUESTIONS = {
    "selection": "解释为何入选以及反对因素；分数不是上涨概率。",
    "guidance": "解释指导价是否存在、适用条件和失效原因，不补造价格。",
    "freshness": "核对数据时间与有效性；过期、未核验和缺失必须明确说明。",
    "workflow": "查询工作流健康，解释当前异常与缺少的证据，不修改配置。",
}
