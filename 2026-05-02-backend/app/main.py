"""FastAPI application entry point for Eksamens Hjelp."""

from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.config import settings
from app.routers import auth, bookings, conversations, listings, payments, reviews, users

sentry_sdk.init(
    dsn=settings.SENTRY_DSN,
    traces_sample_rate=0.1,
    environment=settings.ENVIRONMENT,
)

limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup: nothing needed — Supabase client is stateless
    yield
    # shutdown: nothing to clean up


app = FastAPI(
    title="Eksamens Hjelp API",
    version="1.0.0",
    docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
    redoc_url="/redoc" if settings.ENVIRONMENT != "production" else None,
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(users.router, prefix="/api/v1/users", tags=["users"])
app.include_router(listings.router, prefix="/api/v1/listings", tags=["listings"])
app.include_router(bookings.router, prefix="/api/v1/bookings", tags=["bookings"])
app.include_router(payments.router, prefix="/api/v1/payments", tags=["payments"])
app.include_router(conversations.router, prefix="/api/v1/conversations", tags=["conversations"])
app.include_router(reviews.router, prefix="/api/v1/reviews", tags=["reviews"])


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}
