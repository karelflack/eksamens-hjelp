"""Shared FastAPI dependencies: auth verification and Supabase client factory."""

import sys
from typing import Annotated

import httpx
import jwt
from fastapi import Depends, Header, HTTPException, status
from supabase import Client, create_client

from app.config import settings


def get_supabase_user_client(authorization: Annotated[str | None, Header()] = None) -> Client:
    """Return a Supabase client scoped to the requesting user's JWT so RLS applies."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ")
    client = create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)
    # Inject the user JWT so PostgREST executes as auth.uid() = the token's sub
    client.postgrest.auth(token)
    return client


def get_supabase_admin_client() -> Client:
    """Service-role client for admin-only operations. Never call from user-facing routes."""
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)


# Proxy wrappers — delegates through the module namespace at call time so
# unittest.mock.patch("app.deps.get_supabase_*") works with FastAPI's DI,
# which captures function objects directly in Depends() at import time.

def _proxy_user_client(authorization: Annotated[str | None, Header()] = None) -> Client:
    return sys.modules[__name__].get_supabase_user_client(authorization)


def _proxy_admin_client() -> Client:
    return sys.modules[__name__].get_supabase_admin_client()


def verify_supabase_jwt(authorization: Annotated[str | None, Header()] = None) -> dict:
    """Decode and verify a Supabase-issued JWT. Returns the payload dict."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    token = authorization.removeprefix("Bearer ")
    try:
        payload = jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {exc}")


def get_current_user_id(payload: Annotated[dict, Depends(verify_supabase_jwt)]) -> str:
    """Extract the user's UUID from the verified JWT payload."""
    uid = payload.get("sub")
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has no sub claim")
    return uid


def require_teacher(
    user_id: Annotated[str, Depends(get_current_user_id)],
    db: Annotated[Client, Depends(_proxy_user_client)],
) -> str:
    """Require the authenticated user to have is_teacher=true."""
    result = db.table("users").select("is_teacher").eq("id", user_id).single().execute()
    if not result.data or not result.data.get("is_teacher"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Teacher role required")
    return user_id


def require_admin(payload: Annotated[dict, Depends(verify_supabase_jwt)]) -> dict:
    """Require the user to have the 'admin' app_metadata role set by Supabase dashboard."""
    role = (payload.get("app_metadata") or {}).get("role")
    if role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return payload


UserIdDep = Annotated[str, Depends(get_current_user_id)]
UserClientDep = Annotated[Client, Depends(_proxy_user_client)]
AdminClientDep = Annotated[Client, Depends(_proxy_admin_client)]
TeacherIdDep = Annotated[str, Depends(require_teacher)]
