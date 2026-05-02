"""Payment service: Vipps token cache, payment reconciliation, refunds, payouts."""

import asyncio
from datetime import datetime, timezone
from typing import Optional

import httpx
import stripe

from app.config import settings

stripe.api_key = settings.STRIPE_SECRET_KEY

# Simple in-memory token cache for Vipps access token (expires in ~24h)
_vipps_token_cache: dict = {"token": None, "expires_at": 0}


async def get_vipps_access_token() -> str:
    """Fetch and cache the Vipps OAuth access token."""
    import time
    if _vipps_token_cache["token"] and time.time() < _vipps_token_cache["expires_at"] - 60:
        return _vipps_token_cache["token"]

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.VIPPS_BASE_URL}/accesstoken/get",
            headers={
                "client_id": settings.VIPPS_CLIENT_ID,
                "client_secret": settings.VIPPS_CLIENT_SECRET,
                "Ocp-Apim-Subscription-Key": settings.VIPPS_SUBSCRIPTION_KEY,
            },
        )
    resp.raise_for_status()
    data = resp.json()
    _vipps_token_cache["token"] = data["access_token"]
    _vipps_token_cache["expires_at"] = time.time() + int(data.get("expires_in", 3600))
    return _vipps_token_cache["token"]


async def handle_vipps_payment_captured(order_id: str, details: dict, admin_db) -> None:
    """Called from webhook handler when Vipps confirms CAPTURED status."""
    booking_result = admin_db.table("bookings").select("*").eq("vipps_order_id", order_id).single().execute()
    if not booking_result.data:
        return

    booking = booking_result.data
    now = datetime.now(timezone.utc).isoformat()

    admin_db.table("bookings").update(
        {"payment_status": "paid", "status": "confirmed", "updated_at": now}
    ).eq("id", booking["id"]).execute()

    admin_db.table("payments").insert(
        {
            "booking_id": booking["id"],
            "payer_id": booking["student_id"],
            "payee_id": booking["teacher_id"],
            "amount_gross": booking["total_amount_ore"],
            "platform_fee": booking["platform_fee_ore"],
            "amount_net": booking["total_amount_ore"] - booking["platform_fee_ore"],
            "currency": "NOK",
            "payment_method": "vipps",
            "external_id": order_id,
            "status": "succeeded",
            "paid_at": now,
            "created_at": now,
        }
    ).execute()


async def handle_stripe_payment_succeeded(payment_intent: dict, admin_db) -> None:
    """Called from Stripe webhook when payment_intent.succeeded fires."""
    booking_id = payment_intent.get("metadata", {}).get("booking_id")
    if not booking_id:
        return

    booking_result = admin_db.table("bookings").select("*").eq("id", booking_id).single().execute()
    if not booking_result.data:
        return

    booking = booking_result.data
    now = datetime.now(timezone.utc).isoformat()

    admin_db.table("bookings").update(
        {"payment_status": "paid", "status": "confirmed", "updated_at": now}
    ).eq("id", booking_id).execute()

    admin_db.table("payments").insert(
        {
            "booking_id": booking_id,
            "payer_id": booking["student_id"],
            "payee_id": booking["teacher_id"],
            "amount_gross": payment_intent["amount"],
            "platform_fee": payment_intent.get("application_fee_amount", booking["platform_fee_ore"]),
            "amount_net": payment_intent["amount"] - payment_intent.get("application_fee_amount", booking["platform_fee_ore"]),
            "currency": "NOK",
            "payment_method": "stripe",
            "external_id": payment_intent["id"],
            "status": "succeeded",
            "paid_at": now,
            "created_at": now,
        }
    ).execute()


async def refund_booking(booking: dict, admin_db) -> None:
    """Issue a refund for a paid booking. Used when teacher cancels."""
    if booking.get("payment_method") == "stripe" and booking.get("stripe_payment_intent_id"):
        stripe.Refund.create(payment_intent=booking["stripe_payment_intent_id"])

    elif booking.get("payment_method") == "vipps" and booking.get("vipps_order_id"):
        access_token = await get_vipps_access_token()
        async with httpx.AsyncClient() as client:
            await client.put(
                f"{settings.VIPPS_BASE_URL}/ecomm/v2/payments/{booking['vipps_order_id']}/refund",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Ocp-Apim-Subscription-Key": settings.VIPPS_SUBSCRIPTION_KEY,
                    "Merchant-Serial-Number": settings.VIPPS_MERCHANT_SERIAL_NUMBER,
                },
                json={
                    "merchantInfo": {"merchantSerialNumber": settings.VIPPS_MERCHANT_SERIAL_NUMBER},
                    "transaction": {
                        "amount": booking["total_amount_ore"],
                        "transactionText": "Refund — teacher cancelled",
                    },
                },
            )

    if admin_db:
        admin_db.table("bookings").update(
            {"payment_status": "refunded", "updated_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", booking["id"]).execute()
