from __future__ import annotations

import os
from dataclasses import dataclass


def csv_setting(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(item.strip() for item in os.getenv(name, default).split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    environment: str = os.getenv("VOICEPAY_ENV", "development").lower()
    database_url: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./voicepay.db")
    webhook_secret: str = os.getenv("WEBHOOK_SECRET", "dev-webhook-secret")
    max_single_payment_tzs: int = int(os.getenv("MAX_SINGLE_PAYMENT_TZS", "100000"))
    max_daily_payment_tzs: int = int(os.getenv("MAX_DAILY_PAYMENT_TZS", "500000"))
    allowed_origins: tuple[str, ...] = csv_setting("ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000")
    payment_provider: str = os.getenv("PAYMENT_PROVIDER", "mock")
    llm_provider: str = os.getenv("LLM_PROVIDER", "deterministic")
    llm_model: str = os.getenv("LLM_MODEL", "")

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def validate(self) -> None:
        if self.max_single_payment_tzs <= 0 or self.max_daily_payment_tzs <= 0:
            raise RuntimeError("Payment limits must be positive")
        if self.max_single_payment_tzs > self.max_daily_payment_tzs:
            raise RuntimeError("MAX_SINGLE_PAYMENT_TZS cannot exceed MAX_DAILY_PAYMENT_TZS")
        if self.is_production:
            if self.database_url.startswith("sqlite"):
                raise RuntimeError("Production requires PostgreSQL, not SQLite")
            if self.webhook_secret == "dev-webhook-secret" or len(self.webhook_secret) < 24:
                raise RuntimeError("Production requires a strong WEBHOOK_SECRET")
            if not self.allowed_origins:
                raise RuntimeError("Production requires ALLOWED_ORIGINS")


settings = Settings()
