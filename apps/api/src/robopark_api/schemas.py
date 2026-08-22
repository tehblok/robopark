from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class RegisterRequest(BaseModel):
    shared_password: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class ParkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    tag: str
    is_active: bool = True
    tracker_queue: str | None = None
    group_id: int | None = None
    chat_id: int | None = None
    feature_reports: bool = True
    feature_blockers: bool = True
    feature_sla_repair: bool = True
    feature_backlog_alerts: bool = True


class ParkCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    tag: str = Field(min_length=1, max_length=64)
    tracker_queue: str | None = Field(default=None, max_length=128)
    group_id: int | None = None
    chat_id: int | None = None
    feature_reports: bool = True
    feature_blockers: bool = True
    feature_sla_repair: bool = True
    feature_backlog_alerts: bool = True


class ParkUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    tag: str | None = Field(default=None, min_length=1, max_length=64)
    is_active: bool | None = None
    tracker_queue: str | None = Field(default=None, max_length=128)
    group_id: int | None = None
    chat_id: int | None = None
    feature_reports: bool | None = None
    feature_blockers: bool | None = None
    feature_sla_repair: bool | None = None
    feature_backlog_alerts: bool | None = None


class RegisterOut(BaseModel):
    id: int
    username: str
    role: str
    access_status: str
    parks: list[ParkOut]


class UserOut(BaseModel):
    id: int
    username: str
    role: str
    access_status: str
    parks: list[ParkOut]


class MechanicCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    park_id: int


class MechanicUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=1, max_length=128)
    park_id: int | None = None
    is_active: bool | None = None


class MechanicOut(BaseModel):
    id: int
    username: str
    is_active: bool
    created_at: datetime
    park: ParkOut


class BlockerOut(BaseModel):
    key: str
    summary: str
    status: str
    robot: str | None
    created_at: str | None
    hours_created: str | None
    url: str
    bucket: str


class MechanicTasksOut(BaseModel):
    park_tag: str
    status: str
    counts: dict[str, int]
    items: list[BlockerOut]


class RobotTicketsOut(BaseModel):
    query: str
    items: list[BlockerOut]


class EmergencySectionItem(BaseModel):
    id: str
    title: str


class EmergencyResolveRequest(BaseModel):
    robot_number: str = Field(min_length=1, max_length=64)


class EmergencyResolveOut(BaseModel):
    vin: str
    sections: list[EmergencySectionItem]


class EmergencyFieldOut(BaseModel):
    label: str
    lines: list[str]


class EmergencySectionOut(BaseModel):
    id: str
    title: str
    fields: list[EmergencyFieldOut]
