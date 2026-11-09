"""Strict request schemas for folders, tags and document metadata."""

import re
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def clean_segment(value: str, maximum: int) -> str:
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > maximum
        or normalized in {".", ".."}
        or "/" in normalized
        or "\\" in normalized
        or _CONTROL.search(normalized)
    ):
        raise ValueError("invalid name")
    return normalized


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FolderCreate(RequestModel):
    name: str = Field(min_length=1, max_length=100)
    parent_id: UUID | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return clean_segment(value, 100)


class FolderPatch(RequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    parent_id: UUID | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        return clean_segment(value, 100) if value is not None else None

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        return self


class TagCreate(RequestModel):
    name: str = Field(min_length=1, max_length=40)
    color: str = "#6c7a70"

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return clean_segment(value, 40)

    @field_validator("color")
    @classmethod
    def validate_color(cls, value: str) -> str:
        if not _COLOR.fullmatch(value):
            raise ValueError("color must be #RRGGBB")
        return value.lower()


class TagPatch(RequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    color: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        return clean_segment(value, 40) if value is not None else None

    @field_validator("color")
    @classmethod
    def validate_color(cls, value: str | None) -> str | None:
        if value is not None and not _COLOR.fullmatch(value):
            raise ValueError("color must be #RRGGBB")
        return value.lower() if value is not None else None

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        return self


class DocumentPatch(RequestModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    folder_id: UUID | None = None
    tag_ids: list[UUID] | None = Field(default=None, max_length=50)
    favorite: bool | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or _CONTROL.search(normalized):
            raise ValueError("invalid title")
        return normalized

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        if self.tag_ids is not None and len(self.tag_ids) != len(set(self.tag_ids)):
            raise ValueError("tag ids must be unique")
        return self


class PermanentDelete(RequestModel):
    confirm_title: str = Field(min_length=1, max_length=255)
