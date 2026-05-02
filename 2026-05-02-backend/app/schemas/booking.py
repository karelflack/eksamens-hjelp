from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class BookingCreate(BaseModel):
    listing_id: str
    scheduled_at: datetime
    duration_minutes: Optional[int] = Field(None, ge=15, le=480)
    withdrawal_rights_accepted: bool  # angrerettloven waiver — must be True


class BookingPublic(BaseModel):
    id: str
    listing_id: str
    student_id: str
    teacher_id: str
    status: str
    scheduled_at: str
    duration_minutes: int
    total_amount_ore: int
    platform_fee_ore: int
    payment_status: str
    payment_method: Optional[str] = None
    created_at: str
