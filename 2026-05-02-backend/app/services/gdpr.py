"""GDPR data subject rights: data export (Art. 20) and account purge (Art. 17)."""

import asyncio
from datetime import datetime, timezone


async def build_data_export(user_id: str, admin_db) -> dict:
    """
    GDPR Art. 20 — Data portability export.
    Returns all personal data held about the user as structured JSON.
    """
    profile = admin_db.table("users").select("*").eq("id", user_id).single().execute().data or {}
    bookings = admin_db.table("bookings").select("*").or_(f"student_id.eq.{user_id},teacher_id.eq.{user_id}").execute().data or []
    payments = admin_db.table("payments").select("*").or_(f"payer_id.eq.{user_id},payee_id.eq.{user_id}").execute().data or []
    reviews = admin_db.table("reviews").select("*").eq("reviewer_id", user_id).execute().data or []
    consents = admin_db.table("consent_records").select("*").eq("user_id", user_id).execute().data or []
    messages = admin_db.table("messages").select("*").eq("sender_id", user_id).execute().data or []

    # Strip sensitive internal fields before export
    for p in [profile]:
        p.pop("criipto_subject_id", None)

    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "profile": profile,
        "bookings": bookings,
        "payments": payments,
        "reviews_given": reviews,
        "consent_records": consents,
        "messages_sent": messages,
    }


async def queue_personal_data_purge(user_id: str, admin_db) -> None:
    """
    GDPR Art. 17 — Right to erasure cascade.

    Immediately:
    - Anonymise profile (already done in the endpoint before this is called)
    - Delete messages and conversations where user is sole participant

    Retained (legal obligation — Bokføringsloven 5 years):
    - payments rows (amount_gross, platform_fee, external_id, paid_at)
    - bookings rows (anonymised: student_id/teacher_id replaced with a tombstone UUID)

    This function is designed to be idempotent so it can be re-run if it fails.
    In production, schedule this as an ARQ job with a 30-day delay for the full purge.
    """
    # Delete messages sent by this user
    admin_db.table("messages").delete().eq("sender_id", user_id).execute()

    # Delete reviews (not legally required to retain)
    admin_db.table("reviews").delete().eq("reviewer_id", user_id).execute()

    # Anonymise bookings: replace user id references with tombstone — retain for financial records
    TOMBSTONE = "00000000-0000-0000-0000-000000000000"
    admin_db.table("bookings").update({"student_id": TOMBSTONE}).eq("student_id", user_id).execute()
    admin_db.table("bookings").update({"teacher_id": TOMBSTONE}).eq("teacher_id", user_id).execute()

    # Delete consent records older than 1 year post-withdrawal (per magnus's retention table)
    # (In practice, implement a scheduled cleanup job)
    admin_db.table("consent_records").delete().eq("user_id", user_id).not_.is_("withdrawn_at", None).execute()

    # Delete instructor tax info — retained 5 years by separate policy, handled by admin process
    # (Do not delete here — tax law overrides erasure for this data)

    # Log the purge for audit trail
    admin_db.table("gdpr_purge_log").insert(
        {
            "user_id": user_id,
            "purged_at": datetime.now(timezone.utc).isoformat(),
            "purge_type": "account_deletion",
        }
    ).execute()
