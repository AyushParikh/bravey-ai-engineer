from datetime import datetime

from pydantic import BaseModel


class PlanPublic(BaseModel):
    slug: str
    name: str
    description: str | None = None
    agent_runs_limit: int
    price_cents: int

    model_config = {"from_attributes": True}


class BillingStatusResponse(BaseModel):
    plan_slug: str
    plan_name: str
    agent_runs_limit: int
    agent_runs_used: int
    subscription_status: str
    cancel_at_period_end: bool
    current_period_end: datetime | None = None


class CheckoutRequest(BaseModel):
    plan_slug: str
    success_url: str | None = None
    cancel_url: str | None = None


class CheckoutResponse(BaseModel):
    checkout_url: str


class PortalRequest(BaseModel):
    return_url: str | None = None


class PortalResponse(BaseModel):
    portal_url: str
