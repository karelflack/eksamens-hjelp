from typing import Optional
from pydantic import BaseModel, Field


class ConversationCreate(BaseModel):
    participant_ids: list[str] = Field(min_length=1)  # one other participant (exactly 2 total after adding caller)
    booking_id: Optional[str] = None


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
