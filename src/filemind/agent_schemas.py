"""Validated HTTP contracts for the first, QA-only agent mode."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

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

    conversation_id: UUID
    mode: Literal["qa"] = "qa"
    instruction: str = Field(min_length=1, max_length=4000)
    scope: SearchScope

    @field_validator("instruction")
    @classmethod
    def validate_instruction(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("instruction cannot be blank")
        return value


class RunIdPath(BaseModel):
    run_id: UUID
