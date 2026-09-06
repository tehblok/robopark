from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    remember_me: bool = False


class RegisterRequest(BaseModel):
    shared_password: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    role_slug: str = Field(default="operator", min_length=2, max_length=32)


class ParkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    tag: str
    is_active: bool = True
    tracker_queue: str | None = None
    tracker_priority: str | None = None
    tracker_type: str | None = None
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
    tracker_priority: str | None = Field(default=None, max_length=64)
    tracker_type: str | None = Field(default=None, max_length=64)
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
    tracker_priority: str | None = Field(default=None, max_length=64)
    tracker_type: str | None = Field(default=None, max_length=64)
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
    tracker_login: str | None = None
    must_change_password: bool = False
    screenshot_guard: bool = False
    permissions: list[str] = Field(default_factory=list)
    parks: list[ParkOut]


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


class MechanicCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    park_id: int


class MechanicUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=1, max_length=128)
    park_id: int | None = None
    is_active: bool | None = None
    tracker_login: str | None = Field(default=None, max_length=128)
    must_change_password: bool | None = None


class MechanicOut(BaseModel):
    id: int
    username: str
    is_active: bool
    created_at: datetime
    tracker_login: str | None = None
    must_change_password: bool = False
    park: ParkOut


class TrackerPersonOut(BaseModel):
    display: str
    login: str = ""


class TrackerAttachmentOut(BaseModel):
    id: str
    name: str
    size: int | None = None
    url: str | None = None
    mimetype: str | None = None


class BlockerOut(BaseModel):
    key: str
    summary: str
    status: str
    status_key: str | None = None
    robot: str | None
    created_at: str | None
    hours_created: str | None
    url: str
    bucket: str
    priority: str | None = None
    assignee: TrackerPersonOut | None = None


class MechanicTasksOut(BaseModel):
    park_tag: str
    status: str
    counts: dict[str, int]
    items: list[BlockerOut]


class OperatorBlockersOut(BaseModel):
    park_id: int
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


DiagnosticMatchKind = Literal["exact", "regex"]
DiagnosticSeverity = Literal["info", "warning", "critical"]
DiagnosticView = Literal["top", "front", "rear", "left", "right", "isometric"]
DiagnosticIndicator = Literal["point", "outline", "zone"]


class DiagnosticEvent(BaseModel):
    id: str
    rule_id: int | None = None
    source_path: str = Field(
        description="Dotted display path; dots/backslashes in keys are escaped."
    )
    source_segments: list[str | int] = Field(
        default_factory=list,
        description=(
            "Authoritative JSON source path: string keys and integer indexes. "
            "For a residual atomic event, identifies the original object; raw_value is its "
            "unclassified projection."
        ),
    )
    raw_value: JsonValue
    title: str
    description: str
    severity: DiagnosticSeverity
    sort_order: int = 0
    part: str | None = None
    view: DiagnosticView | None = None
    x: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    y: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    indicator: DiagnosticIndicator | None = None


class EmergencySnapshotOut(BaseModel):
    vin: str
    short_number: str
    observed_at: datetime
    online: bool | None = None
    speed: float | None = None
    charge_percent: float | None = None
    battery1_percent: float | None = None
    battery2_percent: float | None = None
    disk_percent: float | None = None
    mode: str | None = None
    icp_label: str | None = None
    icp_ok: bool | None = None
    lte_label: str | None = None
    lte_ok: bool | None = None
    connection: Literal["lte", "wire"] | None = None
    error_banner: str | None = None
    diagnostic_events: list[DiagnosticEvent] = Field(default_factory=list)
    lat: float | None = None
    lon: float | None = None
    heading_deg: float | None = None
    wheels_fault: list[str] = []


EmergencyViewerRole = Literal["mechanic", "operator", "admin", "royal", "driver"]


class EmergencyFieldAdminOut(BaseModel):
    id: int
    path: str
    label: str
    sort_order: int


class EmergencyFieldCreate(BaseModel):
    path: str = Field(min_length=1, max_length=256)
    label: str = Field(min_length=1, max_length=128)


def _reject_explicit_nulls(data: Any, fields: tuple[str, ...]) -> Any:
    if isinstance(data, dict):
        for key in fields:
            if key in data and data[key] is None:
                raise ValueError(f"{key} must not be null")
    return data


class DiagnosticRuleCreate(BaseModel):
    source_path: str = Field(min_length=1, max_length=256)
    match_kind: DiagnosticMatchKind
    pattern: str = Field(min_length=1, max_length=512)
    example: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1)
    severity: DiagnosticSeverity
    part: str = Field(min_length=1, max_length=128)
    preferred_view: DiagnosticView
    x: float = Field(ge=0, le=1, allow_inf_nan=False)
    y: float = Field(ge=0, le=1, allow_inf_nan=False)
    indicator: DiagnosticIndicator
    is_enabled: bool = True
    sort_order: int = 0


class DiagnosticRuleUpdate(BaseModel):
    source_path: str | None = Field(default=None, min_length=1, max_length=256)
    match_kind: DiagnosticMatchKind | None = None
    pattern: str | None = Field(default=None, min_length=1, max_length=512)
    example: str | None = Field(default=None, min_length=1)
    title: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = Field(default=None, min_length=1)
    severity: DiagnosticSeverity | None = None
    part: str | None = Field(default=None, min_length=1, max_length=128)
    preferred_view: DiagnosticView | None = None
    x: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    y: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    indicator: DiagnosticIndicator | None = None
    is_enabled: bool | None = None
    sort_order: int | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_nulls(cls, data: Any) -> Any:
        return _reject_explicit_nulls(
            data,
            (
                "source_path",
                "match_kind",
                "pattern",
                "example",
                "title",
                "description",
                "severity",
                "part",
                "preferred_view",
                "x",
                "y",
                "indicator",
                "is_enabled",
                "sort_order",
            ),
        )


class DiagnosticRuleOut(DiagnosticRuleCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int


class EmergencyFieldUpdate(BaseModel):
    path: str | None = Field(default=None, min_length=1, max_length=256)
    label: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_nulls(cls, data: Any) -> Any:
        return _reject_explicit_nulls(data, ("path", "label"))


class EmergencySectionAdminOut(BaseModel):
    id: str
    title: str
    sort_order: int
    is_enabled: bool
    formatter: str | None
    meta: dict[str, Any] | None
    roles: list[str]
    fields: list[EmergencyFieldAdminOut]


class EmergencySectionCreate(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=128)
    is_enabled: bool = True
    formatter: str | None = Field(default=None, max_length=64)
    meta: dict[str, Any] | None = None
    roles: list[EmergencyViewerRole] = Field(default_factory=list)
    fields: list[EmergencyFieldCreate] = Field(default_factory=list)


class EmergencySectionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=128)
    is_enabled: bool | None = None
    formatter: str | None = Field(default=None, max_length=64)
    meta: dict[str, Any] | None = None
    roles: list[EmergencyViewerRole] | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_nulls(cls, data: Any) -> Any:
        return _reject_explicit_nulls(data, ("title", "is_enabled"))


class EmergencySectionsReorder(BaseModel):
    ids: list[str]


class ParkMetricsOut(BaseModel):
    park_id: int
    park_name: str
    park_tag: str
    metrics: dict[str, int]


class SkippedParkOut(BaseModel):
    park_id: int
    park_name: str
    reason: str


class NowReportOut(BaseModel):
    generated_at: str
    scope: str
    totals: dict[str, int]
    parks: list[ParkMetricsOut]
    skipped_parks: list[SkippedParkOut]


class TrackerIssueOut(BaseModel):
    key: str
    summary: str
    status: str
    status_key: str | None = None
    queue: str | None = None
    robot: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    hours_created: str | None = None
    url: str
    tags: list[str] = Field(default_factory=list)
    priority: str | None = None
    type: str | None = None
    assignee: TrackerPersonOut | None = None


class TrackerIssueCapabilitiesOut(BaseModel):
    comment: bool
    assign: bool
    unassign: bool
    transition: bool
    close: bool
    attach: bool


class TrackerIssueDetailOut(TrackerIssueOut):
    resolution: str | None = None
    description: str | None = None
    reporter: TrackerPersonOut | None = None
    components: list[str] = Field(default_factory=list)
    attachments: list[TrackerAttachmentOut] = Field(default_factory=list)
    capabilities: TrackerIssueCapabilitiesOut


class TrackerUserOut(BaseModel):
    login: str
    display: str
    source: str = "park"


class TrackerCommentOut(BaseModel):
    id: str
    text: str
    author: str | None = None
    author_login: str | None = None
    created_at: str | None = None
    attachments: list[TrackerAttachmentOut] = Field(default_factory=list)


class TrackerTransitionOut(BaseModel):
    id: str
    display: str


class TrackerIssuesOut(BaseModel):
    items: list[TrackerIssueOut]
    total: int = 0
    limit: int = 0
    offset: int = 0
    has_more: bool = False


class TrackerActionOut(BaseModel):
    key: str
    action: str
    status: str
    actor: str
    performed_at: str


class TrackerCommentIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class TrackerAssignIn(BaseModel):
    assignee: str = Field(min_length=1, max_length=128)


class TrackerTransitionIn(BaseModel):
    transition: str = Field(min_length=1, max_length=128)
    resolution: str | None = Field(default=None, max_length=128)


class TrackerPolicySettingsOut(BaseModel):
    operator_show_untagged: bool
    operator_show_raw: bool
    operator_show_firmware_profile: bool
    mechanic_can_write: bool


class TrackerPolicySettingsIn(BaseModel):
    operator_show_untagged: bool | None = None
    operator_show_raw: bool | None = None
    operator_show_firmware_profile: bool | None = None
    mechanic_can_write: bool | None = None


class ScreenshotGuardSettingsOut(BaseModel):
    operator: bool
    mechanic: bool
    admin: bool
    royal: bool
    driver: bool


class ScreenshotGuardSettingsIn(BaseModel):
    operator: bool | None = None
    mechanic: bool | None = None
    admin: bool | None = None
    royal: bool | None = None
    driver: bool | None = None


EmergencyCookieStatus = Literal["unchecked", "valid", "invalid", "unavailable"]


class EmergencyCookieUpdate(BaseModel):
    cookie: str = Field(min_length=1)
    robot_number: str = Field(min_length=1, max_length=64)


class EmergencyCookieCheck(BaseModel):
    robot_number: str | None = Field(default=None, min_length=1, max_length=64)


class IntegrationSettingsOut(BaseModel):
    tracker_token_masked: str | None
    tracker_token_updated_at: str | None
    tracker_token_encrypted: bool = False
    emergency_cookie_masked: str | None
    emergency_cookie_updated_at: str | None
    emergency_cookie_encrypted: bool = False
    emergency_cookie_valid: bool | None
    emergency_cookie_status: EmergencyCookieStatus = "unchecked"
    emergency_cookie_checked_at: str | None = None
    emergency_cookie_checked_robot: str | None = None


class DashboardMovingItemOut(BaseModel):
    key: str
    summary: str


class DashboardSummaryOut(BaseModel):
    park_id: int
    generated_at: datetime
    arrived: int
    done: int
    queued: int
    in_transit: int
    moving: list[DashboardMovingItemOut]


class DashboardHistoryPointOut(BaseModel):
    bucket_start: datetime
    arrived_count: int
    departed_count: int


class DashboardHistoryOut(BaseModel):
    park_id: int
    points: list[DashboardHistoryPointOut]


ReportKindManual = Literal["ticket_question", "mechanic_problem"]


class ReportAttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    filename: str
    content_type: str
    size_bytes: int


class ReportCreateIn(BaseModel):
    kind: ReportKindManual
    park_id: int
    title: str = Field(min_length=1, max_length=256)
    body: str = ""
    tracker_key: str | None = Field(default=None, max_length=128)
    tracker_url: str | None = Field(default=None, max_length=512)


class ReportReturnIn(BaseModel):
    comment: str


class ReportEscalateIn(BaseModel):
    comment: str


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    status: str
    park_id: int | None
    author_user_id: int
    target_role: str
    tracker_key: str | None
    tracker_url: str | None
    title: str
    body: str
    parent_report_id: int | None
    return_comment: str | None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    attachments: list[ReportAttachmentOut] = Field(default_factory=list)


class ReportBadgeOut(BaseModel):
    count: int


class AuditEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    actor_user_id: int | None
    actor_username: str | None
    actor_role: str | None
    park_id: int | None
    target_type: str | None
    target_id: str | None
    outcome: str
    detail: str | None
    client_ip: str | None
    created_at: datetime


class AuditPageOut(BaseModel):
    items: list[AuditEntryOut]
    total: int
    limit: int
    offset: int
    has_more: bool
