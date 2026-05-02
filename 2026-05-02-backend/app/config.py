"""Environment-based configuration. All secrets come from Railway env vars — never hardcoded."""

from typing import List

from pydantic import AnyHttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ENVIRONMENT: str = "development"

    # Supabase
    SUPABASE_URL: str
    SUPABASE_ANON_KEY: str
    SUPABASE_SERVICE_ROLE_KEY: str  # only for admin ops; never exposed to users
    SUPABASE_JWT_SECRET: str        # to verify Supabase-issued JWTs

    # Stripe
    STRIPE_SECRET_KEY: str
    STRIPE_WEBHOOK_SECRET: str
    STRIPE_CONNECT_CLIENT_ID: str

    # Vipps
    VIPPS_CLIENT_ID: str
    VIPPS_CLIENT_SECRET: str
    VIPPS_SUBSCRIPTION_KEY: str
    VIPPS_MERCHANT_SERIAL_NUMBER: str
    VIPPS_BASE_URL: str = "https://apitest.vipps.no"  # switch to api.vipps.no in prod
    VIPPS_CALLBACK_BASE_URL: str   # public URL that Vipps can POST to

    # Criipto / BankID
    CRIIPTO_CLIENT_ID: str
    CRIIPTO_CLIENT_SECRET: str
    CRIIPTO_DOMAIN: str            # e.g. "your-tenant.criipto.id"

    # Resend (email)
    RESEND_API_KEY: str
    EMAIL_FROM: str = "noreply@eksamensh jelp.no"

    # Redis / ARQ
    REDIS_URL: str                  # Upstash Redis URL (rediss://)

    # Sentry
    SENTRY_DSN: str = ""

    # App
    FRONTEND_URL: str = "https://eksamenshjelp.no"
    PLATFORM_COMMISSION_RATE: float = 0.15  # 15%
    MVA_RATE: float = 0.25                  # 25% Norwegian VAT on platform fee

    # CORS — comma-separated list in env, e.g. "https://eksamenshjelp.no,https://www.eksamenshjelp.no"
    CORS_ORIGINS_RAW: str = "http://localhost:5173"

    @property
    def CORS_ORIGINS(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS_RAW.split(",")]


settings = Settings()
