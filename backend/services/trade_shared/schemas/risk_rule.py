"""
Risk Rule Schemas
"""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.services.live_trading.services.risk_rule_types import (
    validate_rule_parameters,
)


class RiskRuleBase(BaseModel):
    """Base risk rule schema"""

    rule_name: str = Field(..., min_length=1, max_length=100)
    rule_type: str = Field(..., min_length=1, max_length=50)
    description: str | None = Field(None, max_length=500)
    parameters: dict[str, Any] = Field(default_factory=dict)
    applies_to_all: bool = True
    user_ids: list[int] | None = None
    priority: int = Field(0, ge=0, le=100)

    @field_validator("user_ids")
    @classmethod
    def _normalize_user_ids(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        return [int(item) for item in value]


class RiskRuleCreate(RiskRuleBase):
    """Create risk rule schema"""

    is_active: bool = True

    @model_validator(mode="after")
    def _validate_parameters(self) -> "RiskRuleCreate":
        self.parameters = validate_rule_parameters(self.rule_type, self.parameters)
        if not self.applies_to_all and not self.user_ids:
            raise ValueError("user_ids is required when applies_to_all is false")
        return self


class RiskRuleUpdate(BaseModel):
    """Update risk rule schema"""

    rule_name: str | None = Field(None, min_length=1, max_length=100)
    rule_type: str | None = Field(None, min_length=1, max_length=50)
    description: str | None = Field(None, max_length=500)
    is_active: bool | None = None
    parameters: dict[str, Any] | None = None
    applies_to_all: bool | None = None
    user_ids: list[int] | None = None
    priority: int | None = Field(None, ge=0, le=100)

    @model_validator(mode="after")
    def _validate_parameters(self) -> "RiskRuleUpdate":
        if self.rule_type is not None and self.parameters is not None:
            self.parameters = validate_rule_parameters(self.rule_type, self.parameters)
        if self.applies_to_all is False and not self.user_ids:
            raise ValueError("user_ids is required when applies_to_all is false")
        return self


class RiskRuleResponse(RiskRuleBase):
    """Risk rule response schema"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class RiskEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rule_id: int | None = None
    rule_type: str
    tenant_id: str
    user_id: int
    trade_date: date
    symbol: str
    action: str
    status: str
    trigger_price: float | None = None
    cost_price: float | None = None
    pnl_pct: float | None = None
    quantity: float | None = None
    order_ids: list[str] | None = None
    message: str | None = None
    created_at: datetime


class RiskDryRunRequest(BaseModel):
    user_id: int
    tenant_id: str = "default"
    market: str = "CN"
