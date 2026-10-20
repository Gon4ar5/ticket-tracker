from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TicketStatus(str, Enum):
    BACKLOG = "Backlog"
    IN_PROGRESS = "In Progress"
    REVIEW = "Review"
    DONE = "Done"


class TicketPriority(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    URGENT = "Urgent"


class TicketCreate(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    description: str = ""
    acceptance_criteria: str = ""
    status: TicketStatus = TicketStatus.BACKLOG
    priority: TicketPriority = TicketPriority.MEDIUM
    spec_ref: str = Field(default="", max_length=500)
    branch_name: str = Field(default="", max_length=200)
    pull_request_url: str = Field(default="", max_length=500)

    @field_validator("title")
    @classmethod
    def strip_and_require_title(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 3:
            raise ValueError("Title must contain at least 3 non-space characters")
        return normalized


class TicketUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=160)
    description: str | None = None
    acceptance_criteria: str | None = None
    status: TicketStatus | None = None
    priority: TicketPriority | None = None
    spec_ref: str | None = Field(default=None, max_length=500)
    branch_name: str | None = Field(default=None, max_length=200)
    pull_request_url: str | None = Field(default=None, max_length=500)

    @field_validator("title")
    @classmethod
    def strip_and_require_title(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip()
        if len(normalized) < 3:
            raise ValueError("Title must contain at least 3 non-space characters")
        return normalized


class TicketRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    key: str
    title: str
    description: str
    acceptance_criteria: str
    status: TicketStatus
    priority: TicketPriority
    spec_ref: str
    branch_name: str
    pull_request_url: str
    created_at: datetime
    updated_at: datetime
