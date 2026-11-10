"""Validated HTTP contracts for assistant runs, plans, and approvals."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from filemind.search_schemas import SearchScope


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="新的问答", min_length=1, max_length=200)
    scope: SearchScope

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title cannot be blank")
        return value


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID | None = None
    mode: Literal["qa", "organize", "application_pack"] = "qa"
    instruction: str = Field(min_length=1, max_length=4000)
    scope: SearchScope

    @field_validator("instruction")
    @classmethod
    def validate_instruction(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("instruction cannot be blank")
        return value

    @model_validator(mode="after")
    def validate_mode(self) -> "RunCreate":
        if self.mode == "qa" and self.conversation_id is None:
            raise ValueError("qa mode requires a conversation")
        if self.mode in {"organize", "application_pack"} and self.scope.all_documents:
            raise ValueError("this mode requires a finite scope")
        return self


class ApprovalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: UUID
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["approve", "reject"]
    selected_action_ids: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("selected_action_ids")
    @classmethod
    def unique_action_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(not item or len(item) > 64 for item in value):
            raise ValueError("action ids must be unique and non-empty")
        return value

    @model_validator(mode="after")
    def validate_decision(self) -> "ApprovalCreate":
        if self.decision == "approve" and not self.selected_action_ids:
            raise ValueError("approval must include at least one action")
        if self.decision == "reject" and self.selected_action_ids:
            raise ValueError("rejection cannot include actions")
        return self


class RunIdPath(BaseModel):
    run_id: UUID
