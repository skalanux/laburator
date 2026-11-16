"""Abstract job-search client contract."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from laburator.api.dtos import JobSearchFilters, JobSearchResult


class JobSearchClient(ABC):
    """Abstract interface for job-search providers.

    Implementations must map :class:`JobSearchFilters` to provider-specific
    requests and return normalized :class:`JobSearchResult` DTOs.
    """

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Stable identifier for this search source."""

    @abstractmethod
    async def search(self, filters: JobSearchFilters) -> JobSearchResult:
        """Search jobs using provider-agnostic filters."""

    @abstractmethod
    async def job_details(self, job_id: str) -> dict[str, Any]:
        """Fetch details for a specific job by its provider/job ID."""

    @abstractmethod
    async def close(self) -> None:
        """Release underlying resources."""
