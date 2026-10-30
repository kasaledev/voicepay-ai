from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session, init_db
from app.models import AuditEvent, Call, CallOutcome, Customer, Order, OrderStatus, Payment, PaymentStatus
from app.schemas import CallReply, CallStart, CallTurn, CustomerOut, OrderOut, PaymentOut, WebhookPayload
from app.services.agent import append_turn, handle_turn
from app.services.audit import record_event
from app.services.payment import complete_payment, fail_payment

logger = logging.getLogger("voicepay")
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate()
    await init_db()
    yield


app = FastAPI(title="VoicePay AI", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins), allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-Signature", "X-Request-ID"])


@app.middleware("http")
async def request_trace(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    logger.info("request_id=%s method=%s path=%s status=%s duration=%.3f", request_id, request.method, request.url.path, response.status_code, time.perf_counter() - started)
    return response


@app.get("/", include_in_schema=False)
async def dashboard():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health/live")
async def liveness():
    return {"status": "ok"}


@app.post("/api/v1/demo/seed", response_model=CustomerOut)
async def seed_demo(session: AsyncSession = Depends(get_session)):
    found = await session.execute(select(Customer).where(Customer.phone == "+255712345678"))
    customer = found.scalar_one_or_none()
    if customer is not None:
        return customer
    customer = Customer(name="John Mwakalobo", phone="+255712345678")
    session.add(customer)
    await session.flush()
    session.add_all([
        Order(external_number="48392", customer_id=customer.id, amount=Decimal("27500")),
        Order(external_number="48393", customer_id=customer.id, amount=Decimal("18000")),
    ])
    await record_event(session, "demo_seeded", payload={"customer_id": customer.id})
    await session.commit()
    await session.refresh(customer)
    return customer


@app.get("/api/v1/customers", response_model=list[CustomerOut])
async def customers(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Customer).order_by(Customer.id.desc()))
    return list(result.scalars())


@app.get("/api/v1/orders", response_model=list[OrderOut])
async def orders(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Order).order_by(Order.created_at.desc()))
    return list(result.scalars())


@app.get("/api/v1/payments", response_model=list[PaymentOut])
async def payments(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Payment).order_by(Payment.created_at.desc()))
    return list(result.scalars())


@app.post("/api/v1/calls", response_model=CallReply)
async def start_call(payload: CallStart, session: AsyncSession = Depends(get_session)):
    caller = payload.caller_number.strip()
    result = await session.execute(select(Customer).where(Customer.phone == caller, Customer.status == "active"))
    customer = result.scalar_one_or_none()
    call = Call(caller_number=caller, customer_id=customer.id if customer else None)
    session.add(call)
    await session.flush()
    await record_event(session, "call_started", call_id=call.id, payload={"identified": customer is not None})
    if customer:
        reply = f"Hello {customer.name}. I can help you pay an order or check an order. Please say your order number."
    else:
        reply = "Hello. I could not verify this caller number. I will connect you to a customer-service representative for secure help."
        call.outcome = CallOutcome.HUMAN_TRANSFER
    await append_turn(session, call, "Agent", reply)
    await session.commit()
    return CallReply(call_id=call.id, reply=reply, action="customer_identified" if customer else "transfer_to_human")


@app.post("/api/v1/calls/{call_id}/turn", response_model=CallReply)
async def call_turn(call_id: int, payload: CallTurn, session: AsyncSession = Depends(get_session)):
    call = await session.get(Call, call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="Call not found")
    if call.outcome != CallOutcome.OPEN:
        raise HTTPException(status_code=409, detail="This call is no longer active")
    reply, action, payment_id, payment_status = await handle_turn(session, call, payload.text)
    await session.commit()
    return CallReply(call_id=call.id, reply=reply, action=action, payment_id=payment_id, payment_status=payment_status)


@app.post("/api/v1/payments/{payment_id}/demo-approve", response_model=PaymentOut)
async def demo_approve(payment_id: int, session: AsyncSession = Depends(get_session)):
    if settings.is_production:
        raise HTTPException(status_code=404, detail="Not found")
    payment = await session.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    await complete_payment(session, payment, provider_transaction_id="demo-approved")
    result = await session.execute(select(Call).where(Call.payment_id == payment.id))
    call = result.scalar_one_or_none()
    if call:
        call.outcome = CallOutcome.PAYMENT_SUCCESS
        await append_turn(session, call, "Agent", f"Your payment was successful. Your receipt reference is {payment.provider_reference}.")
    await session.commit()
    await session.refresh(payment)
    return payment


def valid_signature(raw_body: bytes, signature: str | None) -> bool:
    expected = hmac.new(settings.webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return bool(signature) and hmac.compare_digest(expected, signature)


@app.post("/api/v1/webhooks/payments")
async def payment_webhook(request: Request, x_signature: str | None = Header(default=None), session: AsyncSession = Depends(get_session)):
    raw_body = await request.body()
    if not valid_signature(raw_body, x_signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    try:
        payload = WebhookPayload.model_validate_json(raw_body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook payload") from exc
    result = await session.execute(select(Payment).where(Payment.provider_reference == payload.provider_reference))
    payment = result.scalar_one_or_none()
    if payment is None:
        raise HTTPException(status_code=404, detail="Unknown provider reference")
    if payload.status.lower() in {"success", "completed"}:
        await complete_payment(session, payment, payload.provider_transaction_id)
    else:
        await fail_payment(session, payment, payload.status)
    await session.commit()
    return {"accepted": True, "payment_id": payment.id, "status": payment.status}


@app.get("/api/v1/dashboard")
async def dashboard_metrics(session: AsyncSession = Depends(get_session)):
    calls = await session.scalar(select(func.count(Call.id))) or 0
    transfers = await session.scalar(select(func.count(Call.id)).where(Call.outcome == CallOutcome.HUMAN_TRANSFER)) or 0
    successful = await session.scalar(select(func.count(Payment.id)).where(Payment.status == PaymentStatus.SUCCESS)) or 0
    revenue = await session.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.status == PaymentStatus.SUCCESS)) or 0
    return {"calls": calls, "human_transfers": transfers, "payments_successful": successful, "revenue_tzs": float(revenue)}
