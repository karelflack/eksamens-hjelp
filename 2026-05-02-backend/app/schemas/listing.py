from typing import Literal, Optional
from pydantic import BaseModel, Field

VALID_CATEGORIES = {"language", "music", "fitness", "cooking", "programming", "other"}
VALID_DELIVERY_MODES = {"online", "in_person", "both"}


class ListingCreate(BaseModel):
    title: str = Field(min_length=5, max_length=120)
    description: str = Field(min_length=20, max_length=5000)
    category: str
    tags: list[str] = Field(default_factory=list, max_length=10)
    price_per_hour_ore: int = Field(gt=0, description="Price in øre (NOK × 100)")
    delivery_mode: str
    location: Optional[str] = None
    session_duration_minutes: int = Field(default=60, ge=15, le=480)
    max_students: int = Field(default=1, ge=1, le=20)
    cover_image_url: Optional[str] = None

    def model_post_init(self, __context):
        if self.category not in VALID_CATEGORIES:
            raise ValueError(f"category must be one of {VALID_CATEGORIES}")
        if self.delivery_mode not in VALID_DELIVERY_MODES:
            raise ValueError(f"delivery_mode must be one of {VALID_DELIVERY_MODES}")
        if self.delivery_mode in ("in_person", "both") and not self.location:
            raise ValueError("location is required for in_person or both delivery modes")


class ListingUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=5, max_length=120)
    description: Optional[str] = Field(None, min_length=20, max_length=5000)
    tags: Optional[list[str]] = None
    price_per_hour_ore: Optional[int] = Field(None, gt=0)
    delivery_mode: Optional[str] = None
    location: Optional[str] = None
    session_duration_minutes: Optional[int] = Field(None, ge=15, le=480)
    max_students: Optional[int] = Field(None, ge=1, le=20)
    cover_image_url: Optional[str] = None
    is_active: Optional[bool] = None


class ListingPublic(BaseModel):
    id: str
    teacher_id: str
    title: str
    description: str
    category: str
    tags: list[str]
    price_per_hour_ore: int
    currency: str
    delivery_mode: str
    location: Optional[str]
    session_duration_minutes: int
    max_students: int
    cover_image_url: Optional[str]
    created_at: str
