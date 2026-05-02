"""Skill listing CRUD with category filtering, search, and price ordering."""

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, status

from app.deps import TeacherIdDep, UserClientDep, UserIdDep
from app.schemas.listing import ListingCreate, ListingPublic, ListingUpdate

router = APIRouter()


@router.get("", response_model=list[ListingPublic])
async def browse_listings(
    db: UserClientDep,
    category: Optional[str] = Query(None),
    delivery_mode: Optional[str] = Query(None),
    min_price_ore: Optional[int] = Query(None, ge=0),
    max_price_ore: Optional[int] = Query(None, ge=0),
    location: Optional[str] = Query(None),
    min_rating: Optional[float] = Query(None, ge=1.0, le=5.0),
    q: Optional[str] = Query(None, description="Full-text search on title and description"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Browse active listings. All filters are optional and combinable."""
    query = (
        db.table("skill_listings")
        .select(
            "id,teacher_id,title,description,category,tags,price_per_hour_ore,"
            "currency,delivery_mode,location,session_duration_minutes,max_students,"
            "cover_image_url,created_at,"
            "users!teacher_id(full_name,avatar_url,avg_rating,review_count,bankid_verified)"
        )
        .eq("is_active", True)
    )

    if category:
        query = query.eq("category", category)
    if delivery_mode:
        query = query.eq("delivery_mode", delivery_mode)
    if min_price_ore is not None:
        query = query.gte("price_per_hour_ore", min_price_ore)
    if max_price_ore is not None:
        query = query.lte("price_per_hour_ore", max_price_ore)
    if location:
        query = query.ilike("location", f"%{location}%")
    if q:
        if "\x00" in q:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid query parameter")
        # Postgres full-text search via Supabase textSearch helper
        query = query.text_search("fts", q, type="websearch")

    result = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
    return result.data or []


@router.get("/{listing_id}", response_model=ListingPublic)
async def get_listing(listing_id: str, db: UserClientDep):
    result = (
        db.table("skill_listings")
        .select(
            "*,users!teacher_id(full_name,avatar_url,avg_rating,review_count,bankid_verified)"
        )
        .eq("id", listing_id)
        .eq("is_active", True)
        .single()
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found")
    return result.data


@router.post("", response_model=ListingPublic, status_code=status.HTTP_201_CREATED)
async def create_listing(body: ListingCreate, teacher_id: TeacherIdDep, db: UserClientDep):
    """Teachers only. Price must be in øre (integers)."""
    listing_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    data = {
        "id": listing_id,
        "teacher_id": teacher_id,
        **body.model_dump(),
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    result = db.table("skill_listings").insert(data).execute()
    if not result.data:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Failed to create listing")
    return result.data[0] if isinstance(result.data, list) else result.data


@router.patch("/{listing_id}", response_model=ListingPublic)
async def update_listing(listing_id: str, body: ListingUpdate, teacher_id: TeacherIdDep, db: UserClientDep):
    """Teachers may only update their own listings (RLS enforces this)."""
    update_data = body.model_dump(exclude_unset=True)
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = db.table("skill_listings").update(update_data).eq("id", listing_id).eq("teacher_id", teacher_id).execute()
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found or not owned by you")
    return result.data[0]


@router.delete("/{listing_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_listing(listing_id: str, teacher_id: TeacherIdDep, db: UserClientDep):
    """Soft-delete: sets is_active=false. Existing bookings are unaffected."""
    result = db.table("skill_listings").update({"is_active": False}).eq("id", listing_id).eq("teacher_id", teacher_id).execute()
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found or not owned by you")


@router.get("/{listing_id}/reviews")
async def get_listing_reviews(listing_id: str, db: UserClientDep, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    result = (
        db.table("reviews")
        .select("*,users!reviewer_id(full_name,avatar_url)")
        .eq("listing_id", listing_id)
        .eq("is_public", True)
        .order("created_at", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    return result.data or []
