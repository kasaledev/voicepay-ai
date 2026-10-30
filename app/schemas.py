from __future__ import annotations

from pydantic import BaseModel, Field

from app.models import CallOutcome, OrderStatus, PaymentStatus


class CallStart(BaseModel):
    caller_number: str = Field(min_length=8, max_length=30)


class CallTurn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class CallReply(BaseModel):
    call_id: int
    reply: str
    action: str | None = None
    payment_id: int | None = None
    payment_status: PaymentStatus | None = None


class WebhookPayload(BaseModel):
    provider_reference: str = Field(min_length=5, max_length=120)
    status: str
    provider_transaction_id: str | None = Field(default=None, max_length=120)


class CustomerOut(BaseModel):
    id: int
    name: str
    phone: str
    status: str
    model_config = {"from_attributes": True}


class OrderOut(BaseModel):
    id: int
    external_number: str
    amount: float
    currency: str
    status: OrderStatus
    customer_id: int
    model_config = {"from_attributes": True}


class PaymentOut(BaseModel):
    id: int
    order_id: int
    amount: float
    currency: str
    provider: str
    provider_reference: str | None
    status: PaymentStatus
    model_config = {"from_attributes": True}
