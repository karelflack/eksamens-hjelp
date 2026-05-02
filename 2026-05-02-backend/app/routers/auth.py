"""Authentication routes: register, login, BankID verification, GDPR consent."""

from datetime import datetime, timezone
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address
from supabase import create_client

from app.config import settings
from app.deps import UserClientDep, UserIdDep, get_supabase_admin_client
from app.schemas.auth import (
    BankIDCallbackRequest,
    ConsentWithdrawRequest,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    RegisterResponse,
)
from app.services.email import send_welcome_email

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def register(request: Request, body: RegisterRequest):
    admin_db = get_supabase_admin_client()
    """
    Register a new user. Enforces:
    - Age gate: user must declare age >= 15 (GDPR Art.8 / Personopplysningsloven §5)
    - GDPR consent: must accept privacy policy and terms
    - Withdrawal rights notice recorded
    """
    if not body.age_confirmed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="You must confirm you are 15 or older to create an account.",
        )
    if not body.privacy_accepted or not body.terms_accepted:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="You must accept the Privacy Policy and Terms of Service.",
        )

    # Create the auth user in Supabase Auth
    auth_response = admin_db.auth.admin.create_user(
        {
            "email": body.email,
            "password": body.password,
            "email_confirm": False,  # send confirmation email via Supabase
            "user_metadata": {"full_name": body.full_name},
        }
    )
    if not auth_response.user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Registration failed")

    user_id = auth_response.user.id
    now = datetime.now(timezone.utc).isoformat()

    # Insert user profile row (triggers RLS; admin client used for initial row creation)
    admin_db.table("users").insert(
        {
            "id": user_id,
            "email": body.email,
            "full_name": body.full_name,
            "is_teacher": False,
            "is_student": True,
        }
    ).execute()

    # Record GDPR consent
    admin_db.table("consent_records").insert(
        {
            "user_id": user_id,
            "consent_type": "registration",
            "privacy_policy_version": body.privacy_policy_version,
            "terms_version": body.terms_version,
            "accepted_at": now,
            "ip_address": request.client.host if request.client else None,
        }
    ).execute()

    await send_welcome_email(body.email, body.full_name)

    return RegisterResponse(user_id=user_id, email=body.email)


@router.post("/login", response_model=LoginResponse)
@limiter.limit("20/minute")
async def login(request: Request, body: LoginRequest):
    """Email/password login. Returns Supabase JWT."""
    client = create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)
    response = client.auth.sign_in_with_password({"email": body.email, "password": body.password})
    if not response.session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return LoginResponse(
        access_token=response.session.access_token,
        refresh_token=response.session.refresh_token,
        expires_in=response.session.expires_in,
    )


@router.post("/bankid/initiate")
@limiter.limit("10/minute")
async def bankid_initiate(request: Request, user_id: UserIdDep):
    """
    Begin optional BankID verification for a teacher. Returns Criipto OIDC redirect URL.
    BankID is NOT required at registration — only for the "Become a Verified Teacher" upsell.
    """
    state = user_id  # embed user_id in state so callback can look up the right user
    redirect_uri = f"{settings.VIPPS_CALLBACK_BASE_URL}/api/v1/auth/bankid/callback"
    # Criipto OIDC — no-bid = Norwegian BankID on mobile
    auth_url = (
        f"https://{settings.CRIIPTO_DOMAIN}/oauth2/authorize"
        f"?client_id={settings.CRIIPTO_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&scope=openid+ssn"   # ssn = Norwegian fødselsnummer (not stored; only verified)
        f"&acr_values=urn:grn:authn:no:bankid:substantial"
        f"&state={state}"
    )
    return {"redirect_url": auth_url}


@router.post("/bankid/callback")
async def bankid_callback(body: BankIDCallbackRequest):
    admin_db = get_supabase_admin_client()
    """
    Handle Criipto OIDC callback after BankID verification.
    GDPR constraint: do NOT store the fødselsnummer — only store verified=true and the Criipto subject ID.
    """
    user_id = body.state

    # Exchange code for ID token at Criipto's token endpoint
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            f"https://{settings.CRIIPTO_DOMAIN}/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "code": body.code,
                "redirect_uri": f"{settings.VIPPS_CALLBACK_BASE_URL}/api/v1/auth/bankid/callback",
                "client_id": settings.CRIIPTO_CLIENT_ID,
                "client_secret": settings.CRIIPTO_CLIENT_SECRET,
            },
        )
    if token_resp.status_code != 200:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="BankID token exchange failed")

    # Decode the ID token — in production use python-jose to verify signature
    import base64, json as _json
    id_token = token_resp.json().get("id_token", "")
    parts = id_token.split(".")
    if len(parts) < 2:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Invalid BankID token")
    claims = _json.loads(base64.b64decode(parts[1] + "=="))

    criipto_subject = claims.get("sub")  # store this for de-duplication, NOT the SSN
    # ssn is in claims["ssn"] — we intentionally discard it per GDPR
    if not criipto_subject:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="BankID subject missing")

    admin_db.table("users").update(
        {
            "bankid_verified": True,
            "criipto_subject_id": criipto_subject,
            "is_teacher": True,  # bankid verification also promotes to teacher
        }
    ).eq("id", user_id).execute()

    return {"verified": True}


@router.post("/consent/withdraw")
async def withdraw_consent(body: ConsentWithdrawRequest, user_id: UserIdDep):
    admin_db = get_supabase_admin_client()
    """Record consent withdrawal for a specific consent type (e.g. marketing, analytics)."""
    admin_db.table("consent_records").insert(
        {
            "user_id": user_id,
            "consent_type": body.consent_type,
            "withdrawn_at": datetime.now(timezone.utc).isoformat(),
            "privacy_policy_version": None,
            "terms_version": None,
            "accepted_at": None,
        }
    ).execute()
    return {"withdrawn": True}


@router.post("/refresh")
@limiter.limit("30/minute")
async def refresh_token(request: Request, refresh_token: str):
    """Exchange a refresh token for a new access token."""
    client = create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)
    response = client.auth.refresh_session(refresh_token)
    if not response.session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    return {
        "access_token": response.session.access_token,
        "refresh_token": response.session.refresh_token,
        "expires_in": response.session.expires_in,
    }
