"""Booking creation, management, and cancellation with GDPR withdrawal rights notice."""

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status

from app.config import settings
from app.deps import AdminClientDep, UserClientDep, UserIdDep, get_supabase_admin_client
from app.schemas.booking import BookingCreate, BookingPublic
from app.services.payments import refund_booking

router = APIRouter()


def _calculate_fees(total_ore: int) -> dict:
    """Calculate platform fee and teacher net in øre. Commission: 15%, MVA: 25% on fee."""
    platform_fee_ore = round(total_ore * settings.PLATFORM_COMMISSION_RATE)
    mva_ore = round(platform_fee_ore * settings.MVA_RATE)
    teacher_net_ore = total_ore - platform_fee_ore
    return {
        "platform_fee_ore": platform_fee_ore,
        "mva_ore": mva_ore,
        "teacher_net_ore": teacher_net_ore,
    }


@router.post("", response_model=BookingPublic, status_code=status.HTTP_201_CREATED)
async def create_booking(body: BookingCreate, user_id: UserIdDep, db: UserClientDep):
    """
    Create a booking. Requires:
    - withdrawal_rights_accepted: consumer acknowledges 14-day right of withdrawal
      ends when session is completed (angrerettloven / Consumer Cancellation Rights Act).
    """
    if not body.withdrawal_rights_accepted:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "You must accept the withdrawal rights notice. "
                "By confirming this booking, you acknowledge that your 14-day right of withdrawal "
                "ends once the session is completed."
            ),
        )

    # Fetch listing to compute price
    listing_result = db.table("skill_listings").select("*").eq("id", body.listing_id).eq("is_active", True).single().execute()
    if not listing_result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found or inactive")

    listing = listing_result.data
    teacher_id = listing["teacher_id"]

    if teacher_id == user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot book your own listing")

    duration_minutes = body.duration_minutes or listing["session_duration_minutes"]
    price_per_hour_ore = listing["price_per_hour_ore"]
    total_amount_ore = round(price_per_hour_ore * duration_minutes / 60)
    fees = _calculate_fees(total_amount_ore)

    booking_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    booking_data = {
        "id": booking_id,
        "listing_id": body.listing_id,
        "student_id": user_id,
        "teacher_id": teacher_id,
        "status": "pending",
        "scheduled_at": body.scheduled_at.isoformat(),
        "duration_minutes": duration_minutes,
        "total_amount_ore": total_amount_ore,
        "platform_fee_ore": fees["platform_fee_ore"],
        "payment_status": "unpaid",
        "withdrawal_rights_accepted": True,
        "withdrawal_rights_accepted_at": now,
        "created_at": now,
        "updated_at": now,
    }

    try:
        result = db.table("bookings").insert(booking_data).execute()
    except Exception as exc:
        msg = str(exc).lower()
        if "duplicate" in msg or "unique" in msg:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Booking conflict: time slot already taken")
        raise
    return result.data[0] if isinstance(result.data, list) else result.data


@router.get("/{booking_id}", response_model=BookingPublic)
async def get_booking(booking_id: str, db: UserClientDep):
    """RLS ensures only the student or teacher of this booking can read it."""
    result = db.table("bookings").select("*").eq("id", booking_id).single().execute()
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
    return result.data


@router.get("")
async def list_my_bookings(user_id: UserIdDep, db: UserClientDep):
    """Return bookings where the user is either student or teacher."""
    result = (
        db.table("bookings")
        .select("*")
        .or_(f"student_id.eq.{user_id},teacher_id.eq.{user_id}")
        .order("scheduled_at", desc=True)
        .execute()
    )
    return result.data or []


@router.patch("/{booking_id}/confirm")
async def confirm_booking(booking_id: str, user_id: UserIdDep, db: UserClientDep):
    """Teacher confirms a pending booking."""
    booking = _get_booking_or_404(booking_id, db)
    if booking["teacher_id"] != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the teacher can confirm this booking")
    if booking["status"] != "pending":
        if booking["status"] == "confirmed":
            # Idempotent re-confirm: only succeed if the teacher's account is
            # verified active (guards against stale/orphaned booking rows).
            user_result = db.table("users").select("is_teacher").eq("id", user_id).single().execute()
            if user_result.data and user_result.data.get("is_teacher"):
                return booking
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Booking is already {booking['status']}")
    result = db.table("bookings").update({"status": "confirmed", "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", booking_id).execute()
    return result.data[0] if isinstance(result.data, list) else result.data


@router.patch("/{booking_id}/cancel")
async def cancel_booking(booking_id: str, reason: str, user_id: UserIdDep, db: UserClientDep, admin_db: AdminClientDep):
    """Either party may cancel. If teacher cancels, auto-refund is triggered."""
    booking = _get_booking_or_404(booking_id, db)
    is_teacher = booking["teacher_id"] == user_id
    is_student = booking["student_id"] == user_id
    if not is_teacher and not is_student:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your booking")
    if booking["status"] in ("cancelled", "completed"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Booking already {booking['status']}")

    update = {
        "status": "cancelled",
        "cancellation_reason": reason,
        "cancelled_by": "teacher" if is_teacher else "student",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    db.table("bookings").update(update).eq("id", booking_id).execute()

    # Teacher-initiated cancellation = automatic refund
    if is_teacher and booking["payment_status"] == "paid":
        await refund_booking(booking, admin_db=admin_db)

    return {"cancelled": True}


@router.patch("/{booking_id}/complete")
async def complete_booking(booking_id: str, user_id: UserIdDep, db: UserClientDep):
    """Teacher marks the session as completed. Triggers payout release."""
    booking = _get_booking_or_404(booking_id, db)
    if booking["teacher_id"] != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the teacher can mark a booking as completed")
    if booking["status"] != "confirmed":
        if booking["status"] == "completed":
            return {"completed": True}  # idempotent
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Booking must be confirmed before completing")

    db.table("bookings").update({"status": "completed", "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", booking_id).execute()
    # Payout release is handled by ARQ worker job (triggered via ARQ queue)
    return {"completed": True}


def _get_booking_or_404(booking_id: str, db) -> dict:
    result = db.table("bookings").select("*").eq("id", booking_id).single().execute()
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
    return result.data
