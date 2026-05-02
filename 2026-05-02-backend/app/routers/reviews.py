"""Review and rating submission (post-session only, one per booking)."""

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status

from app.deps import UserClientDep, UserIdDep
from app.schemas.review import ReviewCreate, ReviewPublic

router = APIRouter()


@router.post("", response_model=ReviewPublic, status_code=status.HTTP_201_CREATED)
async def submit_review(body: ReviewCreate, user_id: UserIdDep, db: UserClientDep):
    """
    Submit a review after a completed session.
    Constraints:
    - Booking must be in 'completed' status
    - Reviewer must be the student of the booking
    - One review per booking (unique constraint on booking_id)
    """
    booking_result = (
        db.table("bookings")
        .select("id,student_id,teacher_id,listing_id,status")
        .eq("id", body.booking_id)
        .single()
        .execute()
    )
    if not booking_result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

    booking = booking_result.data
    if booking["status"] != "completed":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Can only review completed sessions")
    if booking["student_id"] != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the student can review this session")

    review_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    result = db.table("reviews").insert(
        {
            "id": review_id,
            "booking_id": body.booking_id,
            "reviewer_id": user_id,
            "reviewee_id": booking["teacher_id"],
            "listing_id": booking["listing_id"],
            "rating": body.rating,
            "comment": body.comment,
            "is_public": body.is_public,
            "created_at": now,
        }
    ).execute()

    # avg_rating and review_count on users are updated by a Postgres trigger (see migration)
    return result.data[0]


@router.get("")
async def list_my_reviews_given(user_id: UserIdDep, db: UserClientDep):
    """Reviews the authenticated user has submitted."""
    result = (
        db.table("reviews")
        .select("*,users!reviewee_id(full_name,avatar_url),skill_listings!listing_id(title)")
        .eq("reviewer_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    return result.data or []


@router.get("/received")
async def list_my_reviews_received(user_id: UserIdDep, db: UserClientDep):
    """Reviews the authenticated user has received as a teacher."""
    result = (
        db.table("reviews")
        .select("*,users!reviewer_id(full_name,avatar_url),skill_listings!listing_id(title)")
        .eq("reviewee_id", user_id)
        .eq("is_public", True)
        .order("created_at", desc=True)
        .execute()
    )
    return result.data or []
