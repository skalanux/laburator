"""Intermediate adapter that exposes unified job-search commands."""
from __future__ import annotations

from typing import Any

from laburator.api.base import JobSearchClient
from laburator.api.dtos import JobSearchFilters, JobSearchResult


class JobSearchAdapter:
    """Adapter layer that delegates search commands to a JobSearchClient.

    The service receives a concrete client at initialization and provides a
    stable, provider-agnostic interface for callers (pipeline, CLI, web).
    """

    def __init__(self, client: JobSearchClient) -> None:
        self._client = client

    @property
    def client(self) -> JobSearchClient:
        """Return the underlying search client."""
        return self._client

    @property
    def source_name(self) -> str:
        """Return the active source identifier."""
        return self._client.source_name

    async def search(self, filters: JobSearchFilters) -> JobSearchResult:
        """Run a search using the provided DTO filters."""
        return await self._client.search(filters)

    async def search_jobs(
        self,
        query: str,
        num_pages: int = 1,
        country: str = "us",
        date_posted: str = "all",
        remote_only: bool = False,
        employment_types: str = "",
        page: int = 1,
        source: str | None = None,
    ) -> JobSearchResult:
        """Convenience command to build filters and run a search."""
        filters = JobSearchFilters(
            query=query,
            num_pages=num_pages,
            country=country,
            date_posted=date_posted,
            remote_only=remote_only,
            employment_types=employment_types,
            page=page,
            source=source or self.source_name,
        )
        return await self.search(filters)

    async def job_details(self, job_id: str) -> dict[str, Any]:
        """Fetch provider-specific job details by ID."""
        return await self._client.job_details(job_id)

    async def close(self) -> None:
        """Close the underlying client resources."""
        await self._client.close()
