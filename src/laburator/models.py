"""Pydantic models for job-search data structures."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class JobData(BaseModel):
    """All fields returned by the jsearch API for a single job listing."""

    job_id: Optional[str] = None
    job_title: Optional[str] = None
    employer_name: Optional[str] = None
    employer_logo: Optional[str] = None
    employer_website: Optional[str] = None
    job_publisher: Optional[str] = None
    job_employment_type: Optional[str] = None
    job_employment_types: Optional[list[str]] = None
    job_apply_link: Optional[str] = None
    job_apply_is_direct: Optional[bool] = None
    apply_options: Optional[list[dict[str, Any]]] = None
    job_description: Optional[str] = None
    job_is_remote: Optional[bool] = None
    job_posted_at: Optional[str] = None
    job_posted_at_timestamp: Optional[int] = None
    job_posted_at_datetime_utc: Optional[str] = None
    job_location: Optional[str] = None
    job_city: Optional[str] = None
    job_state: Optional[str] = None
    job_country: Optional[str] = None
    job_latitude: Optional[float] = None
    job_longitude: Optional[float] = None
    job_benefits: Optional[list[str]] = None
    job_google_link: Optional[str] = None
    job_salary: Optional[str] = None
    job_min_salary: Optional[float] = None
    job_max_salary: Optional[float] = None
    job_salary_period: Optional[str] = None
    job_highlights: Optional[dict[str, Any]] = None
    job_onet_soc: Optional[str] = None
    job_onet_job_zone: Optional[str] = None
    employer_reviews: Optional[list[dict[str, Any]]] = None


class SearchParameters(BaseModel):
    """Parameters used in the search request."""

    query: str = ""
    num_pages: int = 1
    country: str = "us"
    date_posted: str = "all"
    remote_jobs_only: bool = False
    employment_types: str = ""
    page: int = 1


class SearchResponse(BaseModel):
    """Response from the jsearch API search endpoint."""

    status: str = ""
    request_id: str = ""
    parameters: Optional[SearchParameters] = None
    data: list[JobData] = Field(default_factory=list)


class SkillOutput(BaseModel):
    """Output from a single skill execution."""

    skill_name: str = ""
    file_path: str = ""
    content: str = ""
