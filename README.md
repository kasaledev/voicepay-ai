# VoicePay AI MVP

An interactive, safe-by-design MVP for a Tanzanian customer-service voice agent that can look up orders and initiate a payment request. It deliberately prevents the AI from directly performing financial actions.

## What works now

- Caller-number lookup and a demo voice conversation flow
- Order lookup by order number (`48392` in the demo)
- Explicit spoken confirmation before a payment request is sent
- Guarded payment state machine and transaction limits
- Customer-scoped idempotency key and active-payment duplicate protection
- Mock provider approval for development only
- HMAC-verified payment webhook for real provider callbacks
- Call transcript, payment audit events and human-handoff outcome
- Operator dashboard available at `/`

## Run locally

```bash
python -m venv .venv
# PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`, click **Load demo workspace**, then **Start call**. Send `order 48392`, then `yes`, then use **Simulate provider approval**.

## Production integration boundaries

This project implements the business-safe core and deliberately contains adapters rather than live Telnyx, STT, TTS, LLM and mobile-money credentials. Use the following integration points:

- Telnyx/SIP webhooks and media streams should call `POST /api/v1/calls` and the turn handler after STT.
- Replace `app/services/agent.py` intent routing with a tool-calling LLM adapter; retain the payment service as the authority that validates and executes actions.
- Connect Africa's Talking Payments or a licensed Tanzanian payment provider behind `app/services/payment.py` and call `POST /api/v1/webhooks/payments` using the HMAC signature scheme.
- Add OTP/PIN or provider secure confirmation for sensitive actions. Never collect a mobile-money PIN through speech or logs.

## Production checklist

Set `VOICEPAY_ENV=production`, PostgreSQL `DATABASE_URL`, a 24+ character `WEBHOOK_SECRET`, exact `ALLOWED_ORIGINS`, TLS/reverse proxy, database migrations, secrets manager, rate limiting, user/tenant authorization, live provider webhook verification and monitoring before real customer or financial use.
