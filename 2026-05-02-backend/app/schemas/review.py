from typing import Optional
from pydantic import BaseModel, Field


class ReviewCreate(BaseModel):
    booking_id: str
    rating: int = Field(ge=1, le=5)
    comment: Optional[str] = Field(None, max_length=2000)
    is_public: bool = True


class ReviewPublic(BaseModel):
    id: str
    booking_id: str
    reviewer_id: str
    reviewee_id: str
    listing_id: str
    rating: int
    comment: Optional[str]
    is_public: bool
    created_at: str
