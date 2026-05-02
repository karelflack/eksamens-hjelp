"""Payment routes: Vipps eComm v2 and Stripe Connect initiation + webhook handling."""

import hashlib
import hmac
import json
import sys
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import stripe
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status

from app.config import settings
from app.deps import AdminClientDep, UserClientDep, UserIdDep
from app.schemas.payment import StripeInitiateRequest, VippsInitiateRequest
from app.services.payments import (
    get_vipps_access_token,
    handle_stripe_payment_succeeded,
    handle_vipps_payment_captured,
)

router = APIRouter()
stripe.api_key = settings.STRIPE_SECRET_KEY


# ── Vipps ─────────────────────────────────────────────────────────────────────

@router.post("/vipps/initiate")
async def initiate_vipps_payment(body: VippsInitiateRequest, user_id: UserIdDep, db: UserClientDep, admin_db: AdminClientDep):
    """Initiate a Vipps eComm v2 payment for a booking."""
    booking = _get_booking_for_payment(body.booking_id, user_id, db)

    order_id = f"EH-{str(uuid4())[:8].upper()}"
    access_token = await get_vipps_access_token()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.VIPPS_BASE_URL}/ecomm/v2/payments",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Ocp-Apim-Subscription-Key": settings.VIPPS_SUBSCRIPTION_KEY,
                "Merchant-Serial-Number": settings.VIPPS_MERCHANT_SERIAL_NUMBER,
                "Content-Type": "application/json",
            },
            json={
                "customerInfo": {"mobileNumber": body.phone_number},
                "merchantInfo": {
                    "merchantSerialNumber": settings.VIPPS_MERCHANT_SERIAL_NUMBER,
                    "callbackPrefix": settings.VIPPS_CALLBACK_BASE_URL,
                    "fallBack": f"{settings.FRONTEND_URL}/bookings/{body.booking_id}",
                    "authToken": _generate_vipps_auth_token(order_id),
                },
                "transaction": {
                    "orderId": order_id,
                    "amount": booking["total_amount_ore"],  # already in øre
                    "transactionText": f"Eksamensh jelp — {booking['listing_id'][:8]}",
                },
            },
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Vipps error: {resp.text}")

    vipps_data = resp.json()
    # Store the orderId so we can verify the callback
    admin_db.table("bookings").update(
        {"vipps_order_id": order_id, "payment_method": "vipps"}
    ).eq("id", body.booking_id).execute()

    return {"redirect_url": vipps_data["url"], "order_id": order_id}


@router.post("/vipps/callback")
async def vipps_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str = Header(None),
):
    """
    Receive Vipps payment callback. Security: verify by calling Vipps details API
    (never trust the callback payload alone — Vipps docs recommend this approach).
    """
    body = await request.json()
    order_id = body.get("orderId")
    if not order_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing orderId")

    admin_db = sys.modules["app.deps"].get_supabase_admin_client()

    # Verify with Vipps API
    access_token = await get_vipps_access_token()
    async with httpx.AsyncClient() as client:
        details_resp = await client.get(
            f"{settings.VIPPS_BASE_URL}/ecomm/v2/payments/{order_id}/details",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Ocp-Apim-Subscription-Key": settings.VIPPS_SUBSCRIPTION_KEY,
                "Merchant-Serial-Number": settings.VIPPS_MERCHANT_SERIAL_NUMBER,
            },
        )
    if details_resp.status_code != 200:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Could not verify Vipps order")

    details = details_resp.json()
    transaction_info = details.get("transactionInfo", {})
    transaction_status = transaction_info.get("status")

    if transaction_status == "CAPTURED":
        background_tasks.add_task(handle_vipps_payment_captured, order_id, details, admin_db)

    return {"received": True}


# ── Stripe ────────────────────────────────────────────────────────────────────

@router.post("/stripe/initiate")
async def initiate_stripe_payment(body: StripeInitiateRequest, user_id: UserIdDep, db: UserClientDep, admin_db: AdminClientDep):
    """Create a Stripe Payment Intent for a booking. SCA (3DS) handled by Stripe."""
    booking = _get_booking_for_payment(body.booking_id, user_id, db)

    # Fetch teacher's Stripe Connect account id
    teacher_row = admin_db.table("users").select("stripe_account_id").eq("id", booking["teacher_id"]).single().execute()
    stripe_account_id = teacher_row.data.get("stripe_account_id") if teacher_row.data else None

    platform_fee_ore = booking["platform_fee_ore"]

    # Create a Payment Intent with application fee (split payments via Connect)
    intent = stripe.PaymentIntent.create(
        amount=booking["total_amount_ore"],
        currency="nok",
        application_fee_amount=platform_fee_ore,
        transfer_data={"destination": stripe_account_id} if stripe_account_id else None,
        metadata={
            "booking_id": body.booking_id,
            "user_id": user_id,
        },
        automatic_payment_methods={"enabled": True},
    )

    admin_db.table("bookings").update(
        {"stripe_payment_intent_id": intent.id, "payment_method": "stripe"}
    ).eq("id", body.booking_id).execute()

    return {"client_secret": intent.client_secret, "payment_intent_id": intent.id}


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Stripe webhook handler. Signature verified using stripe.construct_event()
    with the webhook signing secret — never trust raw payloads.
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, settings.STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Stripe signature")

    admin_db = sys.modules["app.deps"].get_supabase_admin_client()

    if event["type"] == "payment_intent.succeeded":
        payment_intent = event["data"]["object"]
        background_tasks.add_task(handle_stripe_payment_succeeded, payment_intent, admin_db)

    elif event["type"] == "account.updated":
        # Stripe Connect — instructor KYC completed
        account = event["data"]["object"]
        if account.get("payouts_enabled"):
            admin_db.table("users").update({"stripe_payouts_enabled": True}).eq("stripe_account_id", account["id"]).execute()

    return {"received": True}


@router.get("/stripe/connect/onboard")
async def stripe_connect_onboard(user_id: UserIdDep, admin_db: AdminClientDep):
    """Generate a Stripe Connect Express onboarding link for the teacher."""
    user_row = admin_db.table("users").select("stripe_account_id,email").eq("id", user_id).single().execute()
    user_data = user_row.data or {}

    # Create account if not already existing
    stripe_account_id = user_data.get("stripe_account_id")
    if not stripe_account_id:
        account = stripe.Account.create(
            type="express",
            country="NO",
            email=user_data.get("email"),
            capabilities={"card_payments": {"requested": True}, "transfers": {"requested": True}},
        )
        stripe_account_id = account.id
        admin_db.table("users").update({"stripe_account_id": stripe_account_id}).eq("id", user_id).execute()

    link = stripe.AccountLink.create(
        account=stripe_account_id,
        refresh_url=f"{settings.FRONTEND_URL}/dashboard/payout-setup?retry=true",
        return_url=f"{settings.FRONTEND_URL}/dashboard/payout-setup?success=true",
        type="account_onboarding",
    )
    return {"onboarding_url": link.url}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_booking_for_payment(booking_id: str, user_id: str, db) -> dict:
    result = db.table("bookings").select("*").eq("id", booking_id).eq("student_id", user_id).single().execute()
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found or not yours")
    if result.data["payment_status"] == "paid":
        # Idempotent re-initiation is only allowed when a payment intent already
        # exists (meaning this booking went through payment flow before), so the
        # caller can safely create a new intent for retry/3DS completion.
        if not result.data.get("stripe_payment_intent_id"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Booking already paid")
    return result.data


def _generate_vipps_auth_token(order_id: str) -> str:
    """Generate an auth token for the Vipps callback — included in callback header for basic validation."""
    return hmac.new(
        settings.VIPPS_CLIENT_SECRET.encode(),
        order_id.encode(),
        hashlib.sha256,
    ).hexdigest()
