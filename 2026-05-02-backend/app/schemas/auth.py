from pydantic import BaseModel, EmailStr, Field, field_validator


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=2, max_length=100)
    age_confirmed: bool                # must be True — user declares they are 15+
    privacy_accepted: bool
    terms_accepted: bool
    privacy_policy_version: str = "1.0"
    terms_version: str = "1.0"

    @field_validator("age_confirmed", "privacy_accepted", "terms_accepted")
    @classmethod
    def must_be_true(cls, v: bool, info) -> bool:
        if not v:
            raise ValueError(f"{info.field_name} must be accepted")
        return v


class RegisterResponse(BaseModel):
    user_id: str
    email: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int


class BankIDCallbackRequest(BaseModel):
    code: str
    state: str   # contains user_id


class ConsentWithdrawRequest(BaseModel):
    consent_type: str   # e.g. "marketing", "analytics"
