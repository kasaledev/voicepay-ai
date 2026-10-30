"""Safe conversation router.

The deterministic router is intentional for the MVP: every financial action
has an explicit, testable branch. Replace only `interpret_turn` with a
provider-backed LLM adapter later; keep payment decisions in business code.
"""
from __future__ import annotations

import re
import secrets

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Call, CallOutcome, Order, PaymentStatus
from app.services.audit import record_event
from app.services.payment import authorize_payment, prepare_payment


def order_number(text: str) -> str | None:
    match = re.search(r"(?:order\s*(?:number)?\s*#?\s*)?(\d{4,})", text.lower())
    return match.group(1) if match else None


async def append_turn(session: AsyncSession, call: Call, speaker: str, text: str) -> None:
    call.transcript = f"{call.transcript}\n{speaker}: {text}".strip()


async def handle_turn(session: AsyncSession, call: Call, text: str) -> tuple[str, str | None, int | None, PaymentStatus | None]:
    normalized = text.strip().lower()
    await append_turn(session, call, "Customer", text)

    if any(phrase in normalized for phrase in ("human", "representative", "agent please", "speak to someone")):
        call.outcome = CallOutcome.HUMAN_TRANSFER
        await record_event(session, "human_handoff_requested", call_id=call.id)
        reply = "Absolutely. I am transferring you to a customer-service representative now."
        await append_turn(session, call, "Agent", reply)
        return reply, "transfer_to_human", None, None

    if call.customer_id is None:
        reply = "I could not verify this number. For your security, I will connect you to a customer-service representative."
        call.outcome = CallOutcome.HUMAN_TRANSFER
        await record_event(session, "unknown_caller_handoff", call_id=call.id)
        await append_turn(session, call, "Agent", reply)
        return reply, "transfer_to_human", None, None

    number = order_number(normalized)
    if number:
        result = await session.execute(select(Order).where(Order.external_number == number))
        order = result.scalar_one_or_none()
        if order is None or order.customer_id != call.customer_id:
            reply = "I could not find an unpaid order with that number for your account. Please repeat the order number or ask for a representative."
        elif order.status.value != "UNPAID":
            reply = f"Order {number} has already been paid. Is there anything else I can help with?"
        else:
            call.selected_order_id = order.id
            reply = f"I found order {number}. The amount due is {int(order.amount):,} Tanzanian shillings. Would you like me to send a secure payment request to your phone?"
        await append_turn(session, call, "Agent", reply)
        return reply, "get_order", None, None

    if normalized in {"yes", "yes please", "confirm", "confirm payment", "pay now", "go ahead"}:
        if call.selected_order_id is None:
            reply = "Please tell me the order number first, so I can confirm the amount before any payment request is sent."
            await append_turn(session, call, "Agent", reply)
            return reply, "request_order_number", None, None
        order = await session.get(Order, call.selected_order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Selected order no longer exists")
        payment = await prepare_payment(session, call, order, idempotency_key=f"call-{call.id}-order-{order.id}")
        payment = await authorize_payment(session, call, payment)
        reply = "I have sent a secure payment request to your phone. Please approve it using your mobile-money app or provider prompt. Never share your PIN with me."
        await append_turn(session, call, "Agent", reply)
        return reply, "create_payment_request", payment.id, payment.status

    if "balance" in normalized:
        reply = "For account balances, I will verify the account and send the details through a secure channel. Please tell me your order number or ask for a representative."
        await append_turn(session, call, "Agent", reply)
        return reply, "secure_balance_lookup", None, None

    reply = "I can help you pay an order, check an order, or connect you to a person. Please say your order number, for example, order 48392."
    await append_turn(session, call, "Agent", reply)
    return reply, "clarify_intent", None, None
