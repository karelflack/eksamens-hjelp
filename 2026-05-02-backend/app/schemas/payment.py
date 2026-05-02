from pydantic import BaseModel, Field


class VippsInitiateRequest(BaseModel):
    booking_id: str
    phone_number: str = Field(description="Norwegian mobile number (8 digits, no country code)")


class StripeInitiateRequest(BaseModel):
    booking_id: str
