from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEvent


async def record_event(
    session: AsyncSession,
    event_type: str,
    *,
    call_id: int | None = None,
    payment_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Persist only operational metadata—never PINs, OTPs or raw card details."""
    session.add(AuditEvent(event_type=event_type, call_id=call_id, payment_id=payment_id, payload=json.dumps(payload or {})))
