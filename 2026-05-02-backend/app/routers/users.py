"""User profile routes and GDPR data subject rights endpoints."""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from fastapi.responses import JSONResponse

from app.deps import AdminClientDep, UserClientDep, UserIdDep
from app.schemas.user import UserProfilePublic, UserProfileUpdate
from app.services.email import send_account_deletion_confirmation
from app.services.gdpr import build_data_export, queue_personal_data_purge

router = APIRouter()


@router.get("/me")
async def get_own_profile(user_id: UserIdDep, db: UserClientDep):
    """Return the authenticated user's full profile."""
    result = db.table("users").select("*").eq("id", user_id).single().execute()
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return result.data


@router.patch("/me")
async def update_own_profile(body: UserProfileUpdate, user_id: UserIdDep, db: UserClientDep):
    """Update own profile. RLS enforces that only the owner can write."""
    update_data = body.model_dump(exclude_unset=True)
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = db.table("users").update(update_data).eq("id", user_id).execute()
    return result.data[0] if result.data else {}


@router.get("/{user_id}", response_model=UserProfilePublic)
async def get_user_profile(user_id: str, db: UserClientDep):
    """Return a public user profile. RLS ensures only public fields are returned."""
    result = (
        db.table("users")
        .select("id,full_name,avatar_url,bio,location,is_teacher,bankid_verified,avg_rating,review_count,created_at")
        .eq("id", user_id)
        .single()
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return result.data


# ── GDPR data subject rights ──────────────────────────────────────────────────

@router.get("/me/data-export")
async def download_my_data(user_id: UserIdDep, admin_db: AdminClientDep):
    """
    GDPR Art. 20 — Data portability. Returns all personal data held about the user.
    Must be fulfilled within 30 days; this endpoint makes it instant.
    """
    user_check = admin_db.table("users").select("id").eq("id", user_id).single().execute()
    if not user_check.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    export = await build_data_export(user_id, admin_db)
    return JSONResponse(
        content=export,
        headers={"Content-Disposition": f'attachment; filename="my-data-{user_id}.json"'},
    )


@router.delete("/me")
async def delete_my_account(
    user_id: UserIdDep,
    background_tasks: BackgroundTasks,
    admin_db: AdminClientDep,
):
    """
    GDPR Art. 17 — Right to erasure ("Slett min konto").

    Process:
    1. Deactivate user profile immediately (soft-delete).
    2. Queue personal data purge (runs within 30 days per GDPR requirement).
    3. Retain only legally required financial records (5 years — Bokføringsloven).
    4. Send deletion confirmation email.
    5. Disable Supabase Auth account.
    """
    # 1. Soft-deactivate profile
    admin_db.table("users").update(
        {
            "is_deleted": True,
            "deleted_at": datetime.now(timezone.utc).isoformat(),
            "full_name": "[deleted]",
            "avatar_url": None,
            "bio": None,
            "location": None,
            "email": f"deleted-{user_id}@anonymised.local",
        }
    ).eq("id", user_id).execute()

    # 2. Get email before we anonymise it (needed to send confirmation)
    auth_user = admin_db.auth.admin.get_user_by_id(user_id)
    original_email = auth_user.user.email if auth_user.user else None

    # 3. Queue 30-day purge job (financial records excepted per Bokføringsloven)
    background_tasks.add_task(queue_personal_data_purge, user_id, admin_db)

    # 4. Send confirmation email
    if original_email:
        background_tasks.add_task(send_account_deletion_confirmation, original_email)

    # 5. Disable Supabase Auth login
    admin_db.auth.admin.update_user_by_id(user_id, {"ban_duration": "876000h"})  # ~100 years

    return {"deleted": True, "message": "Your account has been deactivated. Personal data will be purged within 30 days. Financial records are retained for 5 years as required by Norwegian law (Bokføringsloven)."}


@router.patch("/me/become-teacher")
async def become_teacher(user_id: UserIdDep, db: UserClientDep):
    """Promote the authenticated user to teacher role (no BankID required at this stage)."""
    db.table("users").update({"is_teacher": True, "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", user_id).execute()
    return {"is_teacher": True}


@router.patch("/me/instructor-tax-info")
async def update_instructor_tax_info(
    foedselsnummer: str,
    bank_account_no: str,
    legal_name: str,
    user_id: UserIdDep,
    admin_db: AdminClientDep,
):
    """
    Collect DAC7 / Skatteetaten reporting data at instructor onboarding.
    fødselsnummer stored encrypted (column uses pgcrypto in the DB migration).
    Lawful basis: Legal Obligation (GDPR Art. 6(1)(c)) for DAC7 tax reporting.
    """
    # Validate format: Norwegian fødselsnummer is 11 digits
    cleaned = foedselsnummer.replace(" ", "")
    if not cleaned.isdigit() or len(cleaned) != 11:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid fødselsnummer format (must be 11 digits)")

    admin_db.table("instructor_tax_info").upsert(
        {
            "user_id": user_id,
            # stored via pgcrypto encrypt() in DB trigger; plain text never persisted
            "foedselsnummer_encrypted": cleaned,
            "bank_account_no": bank_account_no,
            "legal_name": legal_name,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    ).execute()

    return {"tax_info_saved": True}
