"""Job-search API client for the jsearch API (openwebninja.com)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from laburator.models import JobData, SearchParameters, SearchResponse

logger = logging.getLogger(__name__)

BASE_URL = "https://api.openwebninja.com/jsearch"
MAX_RETRIES = 3


class JobSearchClient:
    """Async HTTP client for the jsearch job-search API.

    Provides ``search_v2`` and ``job_details`` methods that return parsed
    Pydantic models. Uses async httpx so it doesn't block the event loop.

    Usage::

        client = JobSearchClient(api_key="...")
        results = await client.search_v2("python developer", num_pages=2)
    """

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._client: httpx.AsyncClient | None = None

    # ── Public API ──────────────────────────────────────────────────────

    async def search_v2(
        self,
        query: str,
        num_pages: int = 1,
        country: str = "us",
        date_posted: str = "all",
        remote_jobs_only: bool = False,
        employment_types: str = "",
        page: int = 1,
    ) -> SearchResponse:
        """Search for jobs using the jsearch ``/api/v2/search`` endpoint.

        Args:
            query: Job title or keywords to search for.
            num_pages: Number of result pages to fetch (1-10).
            country: Two-letter country code (e.g. ``"us"``, ``"ar"``).
            date_posted: ``"all"``, ``"today"``, ``"3days"``, ``"week"``,
                ``"month"``.
            remote_jobs_only: Only return remote jobs when ``True``.
            employment_types: Comma-separated types (``"fulltime,parttime"``).
            page: Starting page number (1-based).

        Returns:
            A ``SearchResponse`` with the parsed results.
        """
        client = self._get_client()
        params: dict[str, Any] = {
            "query": query,
            "num_pages": num_pages,
            "country": country,
            "date_posted": date_posted,
            "remote_jobs_only": str(remote_jobs_only).lower(),
        }
        if employment_types:
            params["employment_types"] = employment_types
        if page > 1:
            params["page"] = page

        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES):
            try:
                resp = await client.get("/api/v2/search", params=params)
                resp.raise_for_status()
                data = resp.json()
                return self._parse_search_response(
                    data, query, num_pages, country, date_posted,
                    remote_jobs_only, employment_types, page,
                )

            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in (401, 403):
                    logger.error(
                        "Job search API auth error: HTTP %d", status,
                    )
                    raise
                if status == 429 or status >= 500:
                    last_error = exc
                    if attempt < MAX_RETRIES - 1:
                        wait = 2**attempt
                        logger.warning(
                            "Job search API error (attempt %d/%d): HTTP %d. "
                            "Retrying in %ds...",
                            attempt + 1, MAX_RETRIES, status, wait,
                        )
                        await asyncio.sleep(wait)
                    continue
                logger.error(
                    "Job search API error: HTTP %d — %s",
                    status, exc.response.text,
                )
                raise

            except Exception as exc:
                last_error = exc
                if attempt < MAX_RETRIES - 1:
                    wait = 2**attempt
                    logger.warning(
                        "Job search request failed (attempt %d/%d): %s. "
                        "Retrying in %ds...",
                        attempt + 1, MAX_RETRIES, exc, wait,
                    )
                    await asyncio.sleep(wait)
                continue

        raise RuntimeError(
            f"Job search failed after {MAX_RETRIES} attempts. "
            f"Last error: {last_error}"
        )

    async def job_details(self, job_id: str) -> dict[str, Any]:
        """Fetch details for a specific job by its ID.

        Args:
            job_id: The unique job identifier.

        Returns:
            A dictionary with the job details.
        """
        client = self._get_client()
        try:
            resp = await client.get("/api/v2/job-details", params={"job_id": job_id})
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Job details API error: HTTP %d — %s",
                exc.response.status_code, exc.response.text,
            )
            raise
        except Exception as exc:
            logger.error("Job details request failed: %s", exc)
            raise

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ── Internal helpers ────────────────────────────────────────────────

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=BASE_URL,
                timeout=30.0,
                headers={
                    "x-api-key": self._api_key,
                    "Content-Type": "application/json",
                },
            )
        return self._client

    def _parse_search_response(
        self,
        data: dict[str, Any],
        query: str,
        num_pages: int,
        country: str,
        date_posted: str,
        remote_jobs_only: bool,
        employment_types: str,
        page: int,
    ) -> SearchResponse:
        params = SearchParameters(
            query=query,
            num_pages=num_pages,
            country=country,
            date_posted=date_posted,
            remote_jobs_only=remote_jobs_only,
            employment_types=employment_types,
            page=page,
        )
        jobs_data = data.get("data", [])
        # The API may return the job list directly or nested
        if isinstance(jobs_data, dict):
            jobs_list = jobs_data.get("jobs", [])
        elif isinstance(jobs_data, list):
            jobs_list = jobs_data
        else:
            jobs_list = []

        jobs = [JobData(**job) for job in jobs_list]

        return SearchResponse(
            status=data.get("status", ""),
            request_id=data.get("request_id", ""),
            parameters=params,
            data=jobs,
        )
