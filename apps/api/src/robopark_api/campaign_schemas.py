from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CampaignKind = Literal["service_company", "wrapping"]


class CampaignCreateIn(BaseModel):
    kind: CampaignKind
    name: str = Field(min_length=1, max_length=128)
    tracker_tag: str = Field(min_length=1, max_length=128)
    park_ids: list[int] = Field(min_length=1, max_length=100)
    starts_on: date
    due_on: date


class CampaignUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    tracker_tag: str | None = Field(default=None, min_length=1, max_length=128)
    park_ids: list[int] | None = Field(default=None, min_length=1, max_length=100)
    starts_on: date | None = None
    due_on: date | None = None
    is_active: bool | None = None


class CampaignTicketOut(BaseModel):
    key: str
    summary: str
    status: str
    park_id: int
    park_name: str
    robot: str | None
    url: str
    completed_at: datetime | None = None
    completed_by: int | None = None
    comment: str | None = None
    report_id: int | None = None
    review_status: str | None = None
    tracker_transition: str | None = None


class CampaignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: CampaignKind
    name: str
    tracker_tag: str
    starts_on: date
    due_on: date
    is_active: bool
    park_ids: list[int]
    park_names: list[str]
    total_count: int
    completed_count: int
    pending_review_count: int
    remaining_count: int
    percent_complete: int
    overdue: bool


class CampaignDetailOut(CampaignOut):
    open_tickets: list[CampaignTicketOut]
    closed_tickets: list[CampaignTicketOut]


class CampaignSubmissionOut(BaseModel):
    id: int
    issue_key: str
    report_id: int
    review_status: str
    tracker_transition: str | None
    completed_at: datetime
