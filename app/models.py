from __future__ import annotations

import datetime as dt
import enum
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class OrderStatus(str, enum.Enum):
    UNPAID = "UNPAID"
    PAID = "PAID"
    CANCELLED = "CANCELLED"


class PaymentStatus(str, enum.Enum):
    CREATED = "CREATED"
    CUSTOMER_IDENTIFIED = "CUSTOMER_IDENTIFIED"
    PAYMENT_PREPARED = "PAYMENT_PREPARED"
    AMOUNT_CONFIRMED = "AMOUNT_CONFIRMED"
    CUSTOMER_AUTHORIZED = "CUSTOMER_AUTHORIZED"
    PAYMENT_REQUEST_SENT = "PAYMENT_REQUEST_SENT"
    WAITING_FOR_PROVIDER = "WAITING_FOR_PROVIDER"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class CallOutcome(str, enum.Enum):
    OPEN = "OPEN"
    PAYMENT_SUCCESS = "PAYMENT_SUCCESS"
    HUMAN_TRANSFER = "HUMAN_TRANSFER"
    ENDED = "ENDED"


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    phone: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(30), default="active")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc))
    orders: Mapped[list["Order"]] = relationship(back_populates="customer")


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    external_number: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3), default="TZS")
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), default=OrderStatus.UNPAID)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc))
    customer: Mapped[Customer] = relationship(back_populates="orders")
    payments: Mapped[list["Payment"]] = relationship(back_populates="order")
    __table_args__ = (CheckConstraint("amount > 0", name="ck_orders_positive_amount"),)


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3), default="TZS")
    provider: Mapped[str] = mapped_column(String(50))
    provider_reference: Mapped[str | None] = mapped_column(String(120), unique=True, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255))
    status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus), default=PaymentStatus.CREATED)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc))
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), onupdate=lambda: dt.datetime.now(dt.timezone.utc))
    order: Mapped[Order] = relationship(back_populates="payments")
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_payments_positive_amount"),
        UniqueConstraint("customer_id", "idempotency_key", name="uq_payment_customer_idempotency"),
    )


class Call(Base):
    __tablename__ = "calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    caller_number: Mapped[str] = mapped_column(String(30), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    selected_order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id"), nullable=True)
    transcript: Mapped[str] = mapped_column(Text, default="")
    outcome: Mapped[CallOutcome] = mapped_column(Enum(CallOutcome), default=CallOutcome.OPEN)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc))
    ended_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    call_id: Mapped[int | None] = mapped_column(ForeignKey("calls.id"), nullable=True)
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id"), nullable=True)
    payload: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc))
