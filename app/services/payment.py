from __future__ import annotations

import datetime as dt
import secrets
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Call, Order, OrderStatus, Payment, PaymentStatus
from app.services.audit import record_event

ACTIVE_PAYMENT_STATES = {
    PaymentStatus.CREATED,
    PaymentStatus.CUSTOMER_IDENTIFIED,
    PaymentStatus.PAYMENT_PREPARED,
    PaymentStatus.AMOUNT_CONFIRMED,
    PaymentStatus.CUSTOMER_AUTHORIZED,
    PaymentStatus.PAYMENT_REQUEST_SENT,
    PaymentStatus.WAITING_FOR_PROVIDER,
}


def receipt_number() -> str:
    return secrets.token_hex(3).upper()


async def _daily_total(session: AsyncSession, customer_id: int) -> Decimal:
    start = dt.datetime.combine(dt.date.today(), dt.time.min, tzinfo=dt.timezone.utc)
    result = await session.execute(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.customer_id == customer_id,
            Payment.status.in_([PaymentStatus.PAYMENT_REQUEST_SENT, PaymentStatus.WAITING_FOR_PROVIDER, PaymentStatus.SUCCESS]),
            Payment.created_at >= start,
        )
    )
    return Decimal(result.scalar_one())


async def prepare_payment(session: AsyncSession, call: Call, order: Order, idempotency_key: str) -> Payment:
    if call.customer_id != order.customer_id:
        raise HTTPException(status_code=403, detail="This order does not belong to the verified caller")
    if order.status != OrderStatus.UNPAID:
        raise HTTPException(status_code=409, detail="This order is not available for payment")
    if order.amount > settings.max_single_payment_tzs:
        raise HTTPException(status_code=403, detail="The amount exceeds the single-payment limit and requires a human agent")

    existing = await session.execute(
        select(Payment).where(Payment.customer_id == order.customer_id, Payment.idempotency_key == idempotency_key)
    )
    payment = existing.scalar_one_or_none()
    if payment is not None:
        return payment

    active = await session.execute(
        select(Payment).where(Payment.order_id == order.id, Payment.status.in_(ACTIVE_PAYMENT_STATES))
    )
    if active.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="A payment for this order is already in progress")

    if await _daily_total(session, order.customer_id) + order.amount > settings.max_daily_payment_tzs:
        raise HTTPException(status_code=403, detail="The daily payment limit is reached and requires a human agent")

    payment = Payment(
        customer_id=order.customer_id,
        order_id=order.id,
        amount=order.amount,
        currency=order.currency,
        provider=settings.payment_provider,
        idempotency_key=idempotency_key,
        status=PaymentStatus.PAYMENT_PREPARED,
    )
    session.add(payment)
    await session.flush()
    call.payment_id = payment.id
    await record_event(session, "payment_prepared", call_id=call.id, payment_id=payment.id, payload={"order": order.external_number, "amount": str(order.amount)})
    return payment


async def authorize_payment(session: AsyncSession, call: Call, payment: Payment) -> Payment:
    if payment.status != PaymentStatus.PAYMENT_PREPARED:
        raise HTTPException(status_code=409, detail="Payment is not ready for customer authorization")
    payment.status = PaymentStatus.AMOUNT_CONFIRMED
    # This is the final pre-provider state. A live provider must perform the
    # customer's secure authorization; we never request a spoken payment PIN.
    payment.status = PaymentStatus.CUSTOMER_AUTHORIZED
    payment.provider_reference = f"VP-{receipt_number()}"
    payment.status = PaymentStatus.PAYMENT_REQUEST_SENT
    payment.status = PaymentStatus.WAITING_FOR_PROVIDER
    await record_event(session, "payment_request_sent", call_id=call.id, payment_id=payment.id, payload={"provider_reference": payment.provider_reference})
    return payment


async def complete_payment(session: AsyncSession, payment: Payment, provider_transaction_id: str | None = None) -> Payment:
    if payment.status == PaymentStatus.SUCCESS:
        return payment
    if payment.status != PaymentStatus.WAITING_FOR_PROVIDER:
        raise HTTPException(status_code=409, detail="Payment cannot be completed from its current state")
    payment.status = PaymentStatus.SUCCESS
    order = await session.get(Order, payment.order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    order.status = OrderStatus.PAID
    await record_event(session, "payment_success", payment_id=payment.id, payload={"provider_transaction_id": provider_transaction_id, "receipt": payment.provider_reference})
    return payment


async def fail_payment(session: AsyncSession, payment: Payment, reason: str) -> Payment:
    if payment.status in (PaymentStatus.SUCCESS, PaymentStatus.CANCELLED):
        raise HTTPException(status_code=409, detail="Completed or cancelled payments cannot be failed")
    payment.status = PaymentStatus.FAILED
    await record_event(session, "payment_failed", payment_id=payment.id, payload={"reason": reason})
    return payment
