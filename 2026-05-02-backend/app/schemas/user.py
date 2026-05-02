from typing import Optional
from pydantic import BaseModel, Field


class UserProfilePublic(BaseModel):
    id: str
    full_name: str
    avatar_url: Optional[str] = None
    bio: Optional[str] = None
    location: Optional[str] = None
    is_teacher: bool
    bankid_verified: bool
    avg_rating: Optional[float] = None
    review_count: int


class UserProfileUpdate(BaseModel):
    full_name: Optional[str] = Field(None, min_length=2, max_length=100)
    bio: Optional[str] = Field(None, max_length=1000)
    location: Optional[str] = Field(None, max_length=100)
    avatar_url: Optional[str] = None
    vipps_msisdn: Optional[str] = None   # E.164 phone number for Vipps
