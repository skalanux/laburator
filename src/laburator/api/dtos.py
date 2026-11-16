"""Shared DTOs for job-search providers (filters and results)."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from laburator.models import JobData


class JobSearchFilters(BaseModel):
    """Provider-agnostic search filters passed from adapter to client."""

    query: str = ""
    num_pages: int = 1
    country: str = "us"
    date_posted: str = "all"
    remote_only: bool = False
    employment_types: str = ""
    page: int = 1
    source: Optional[str] = None


class JobSearchResult(BaseModel):
    """Provider-agnostic search result returned by clients/adapters."""

    status: str = ""
    request_id: str = ""
    source: str = ""
    parameters: Optional[JobSearchFilters] = None
    data: list[JobData] = Field(default_factory=list)
    raw: Optional[dict[str, Any]] = None
