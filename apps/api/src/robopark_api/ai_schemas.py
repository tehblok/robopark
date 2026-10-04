from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfigUpdate(Input):
    revision: int = Field(ge=1)
    enabled: bool | None = None
    learning_enabled: bool | None = None


class PromptUpdate(Input):
    revision: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=8000)


class DocumentIn(Input):
    title: str = Field(min_length=1, max_length=250)
    content: str = Field(min_length=1, max_length=100000)
    kind: Literal["manual", "chat", "ticket", "note"] = "note"
    source_ref: str = Field(default="", max_length=400)
    park_id: int | None = None
    state: Literal["active", "candidate"] = "candidate"


class DocumentUpdate(Input):
    revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=250)
    content: str | None = Field(default=None, min_length=1, max_length=100000)
    state: Literal["active", "candidate", "rejected"] | None = None


class ImportIn(Input):
    documents: list[DocumentIn] = Field(min_length=1, max_length=100)
    park_id: int | None = None
    activate_manuals: bool = False
    activate_unverified: bool = False


class ConversationIn(Input):
    title: str = Field(default="Новый разговор", min_length=1, max_length=200)
    park_id: int
    issue_key: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*-\d+$", max_length=128)


class MessageIn(Input):
    content: str = Field(min_length=1, max_length=6000)
    idempotency_key: str = Field(min_length=8, max_length=128)


class ConnectorIn(Input):
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=9, max_length=1000)
    method: Literal["GET", "POST", "PUT", "PATCH"] = "PATCH"
    token: str = Field(default="", max_length=8000)
    enabled: bool = False


class ConnectorUpdate(Input):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    url: str | None = Field(default=None, min_length=9, max_length=1000)
    method: Literal["GET", "POST", "PUT", "PATCH"] | None = None
    token: str | None = Field(default=None, max_length=8000)
    enabled: bool | None = None


class ScriptIn(Input):
    name: str = Field(min_length=1, max_length=120)
    source: str = Field(min_length=1, max_length=32000)


class ScriptUpdate(Input):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    source: str | None = Field(default=None, min_length=1, max_length=32000)
    enabled: bool | None = None


class TestIn(Input):
    input: Any = Field(default_factory=dict)


FilterTerm = Annotated[str, Field(min_length=1, max_length=200)]


class Filters(Input):
    component_ids: list[FilterTerm] = Field(default_factory=list, max_length=50)
    defect_codes: list[FilterTerm] = Field(default_factory=list, max_length=50)
    keywords: list[FilterTerm] = Field(default_factory=list, max_length=20)


class Action(Input):
    connector_id: str | None = Field(default=None, max_length=36)
    script_id: str | None = Field(default=None, max_length=36)
    body: Any = Field(default_factory=dict)


class AutomationIn(Input):
    name: str = Field(min_length=1, max_length=120)
    park_id: int
    filters: Filters = Field(default_factory=Filters)
    action: Action


class AutomationUpdate(Input):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    filters: Filters | None = None
    action: Action | None = None
    enabled: bool | None = None


class PreviewIn(Input):
    event: dict = Field(default_factory=dict)


class RuntimeIn(Input):
    action: Literal["install", "enable", "disable", "remove_model"]


class DraftIn(Input):
    kind: Literal["script", "automation"]
    instruction: str = Field(min_length=1, max_length=6000)
    park_id: int


class MaintenanceIn(Input):
    kind: Literal["history", "failed_jobs"]
    before_days: int = Field(default=30, ge=1, le=3650)
