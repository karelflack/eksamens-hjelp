"""ARQ background job worker. Runs alongside the FastAPI app on Railway."""

import csv
import io
from datetime import datetime, timezone

from arq import create_pool
from arq.connections import RedisSettings

from app.config import settings
from app.deps import get_supabase_admin_client
from app.services.payments import refund_booking
from app.services.email import send_payout_notification


async def release_payout(ctx, booking_id: str) -> None:
    """
    Trigger Stripe payout to teacher after booking is marked completed.
    Stripe Connect handles the actual bank transfer.
    """
    import stripe
    stripe.api_key = settings.STRIPE_SECRET_KEY

    admin_db = get_supabase_admin_client()
    booking = admin_db.table("bookings").select("*").eq("id", booking_id).single().execute().data
    if not booking or booking["status"] != "completed":
        return

    teacher = admin_db.table("users").select("stripe_account_id,email").eq("id", booking["teacher_id"]).single().execute().data
    if not teacher or not teacher.get("stripe_account_id"):
        return  # teacher hasn't connected Stripe — queue for manual payout

    # Stripe Connect transfer (from platform to teacher)
    amount_net = booking["total_amount_ore"] - booking["platform_fee_ore"]
    transfer = stripe.Transfer.create(
        amount=amount_net,
        currency="nok",
        destination=teacher["stripe_account_id"],
        metadata={"booking_id": booking_id},
    )
    admin_db.table("payments").update(
        {"payout_transfer_id": transfer.id, "payout_initiated_at": datetime.now(timezone.utc).isoformat()}
    ).eq("booking_id", booking_id).execute()

    if teacher.get("email"):
        await send_payout_notification(teacher["email"], amount_net / 100)


async def generate_dac7_report(ctx, year: int) -> str:
    """
    Generate the annual Skatteetaten DAC7 report for the given calendar year.
    Reports instructors with >30 transactions OR gross fees > EUR 2,000.
    Must be filed by 31 January of the following year.

    Returns CSV string — platform operator downloads this and submits via Skatteetaten API.
    """
    admin_db = get_supabase_admin_client()

    # Aggregate earnings per instructor for the year
    # Uses service role — no RLS bypass concern for admin reporting job
    payments = (
        admin_db.table("payments")
        .select("payee_id,amount_gross,created_at")
        .gte("created_at", f"{year}-01-01T00:00:00Z")
        .lt("created_at", f"{year + 1}-01-01T00:00:00Z")
        .eq("status", "succeeded")
        .execute()
        .data or []
    )

    # Aggregate by instructor
    earnings: dict[str, dict] = {}
    for p in payments:
        uid = p["payee_id"]
        if uid not in earnings:
            earnings[uid] = {"transaction_count": 0, "gross_ore": 0}
        earnings[uid]["transaction_count"] += 1
        earnings[uid]["gross_ore"] += p["amount_gross"]

    # EUR 2,000 ~ NOK 23,000 at ~11.5 NOK/EUR; stored as 2,300,000 øre
    EUR_2000_IN_ORE = 2_300_000
    reportable = {uid: data for uid, data in earnings.items() if data["transaction_count"] > 30 or data["gross_ore"] >= EUR_2000_IN_ORE}

    if not reportable:
        return ""

    # Fetch instructor tax info (fødselsnummer stored encrypted — admin proc to decrypt for report)
    tax_infos = (
        admin_db.table("instructor_tax_info")
        .select("user_id,legal_name,foedselsnummer_encrypted,bank_account_no")
        .in_("user_id", list(reportable.keys()))
        .execute()
        .data or []
    )
    tax_by_uid = {t["user_id"]: t for t in tax_infos}

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["legal_name", "foedselsnummer", "bank_account", "transaction_count", "gross_nok", "report_year"])

    for uid, data in reportable.items():
        tax = tax_by_uid.get(uid, {})
        writer.writerow([
            tax.get("legal_name", ""),
            tax.get("foedselsnummer_encrypted", "MISSING"),  # decrypt before actual submission
            tax.get("bank_account_no", ""),
            data["transaction_count"],
            round(data["gross_ore"] / 100, 2),
            year,
        ])

    return output.getvalue()


class WorkerSettings:
    functions = [release_payout, generate_dac7_report]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    job_timeout = 300
    max_jobs = 10
