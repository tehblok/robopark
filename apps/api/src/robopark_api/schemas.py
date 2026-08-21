from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class RegisterRequest(BaseModel):
    shared_password: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class RegisterOut(BaseModel):
    id: int
    username: str
    role: str
    access_status: str


class UserOut(BaseModel):
    id: int
    username: str
    role: str
