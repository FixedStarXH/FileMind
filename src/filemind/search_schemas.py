"""Search request models."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SearchScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_ids: list[UUID] = Field(default_factory=list, max_length=100)
    folder_ids: list[UUID] = Field(default_factory=list, max_length=100)
    tag_ids: list[UUID] = Field(default_factory=list, max_length=100)
    inbox: bool = False
    all_documents: bool = False

    @model_validator(mode="after")
    def validate_explicit_scope(self) -> "SearchScope":
        if self.all_documents:
            if self.document_ids or self.folder_ids or self.tag_ids or self.inbox:
                raise ValueError("all_documents cannot be combined with other selectors")
        elif not (self.document_ids or self.folder_ids or self.tag_ids or self.inbox):
            raise ValueError("search scope must be explicit")
        return self


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    mode: Literal["keyword", "semantic", "hybrid"] = "hybrid"
    scope: SearchScope
    limit: int = Field(default=10, ge=1, le=50)
