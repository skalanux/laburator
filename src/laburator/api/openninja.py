"""OpenNinja (jsearch) job-search client implementation."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from laburator.api.base import JobSearchClient
from laburator.api.dtos import JobSearchFilters, JobSearchResult
from laburator.models import JobData

logger = logging.getLogger(__name__)

BASE_URL = "https://api.openwebninja.com/jsearch"
MAX_RETRIES = 3


class OpenNinjaJobSearchClient(JobSearchClient):
    """Async HTTP client for the OpenNinja jsearch job-search API."""

    source_name = "openninja"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._client: httpx.AsyncClient | None = None

    @staticmethod
    def _normalize_employment_types(raw: str) -> str:
        """Map generic employment type tokens to JSearch/OpenNinja expected values."""
        if not raw:
            return ""
        mapping = {
            "fulltime": "FULLTIME",
            "full-time": "FULLTIME",
            "full_time": "FULLTIME",
            "ft": "FULLTIME",
            "parttime": "PARTTIME",
            "part-time": "PARTTIME",
            "part_time": "PARTTIME",
            "pt": "PARTTIME",
            "contract": "CONTRACTOR",
            "contractor": "CONTRACTOR",
            "intern": "INTERN",
            "internship": "INTERN",
        }
        out: list[str] = []
        for part in raw.replace("|", ",").split(","):
            p = part.strip().lower()
            if not p or p in {"remote", "on-site", "onsite", "hybrid"}:
                # handled by remote_jobs_only/location filters, not employment_types
                continue
            mapped = mapping.get(p)
            if mapped and mapped not in out:
                out.append(mapped)
        return ",".join(out)

    async def search(self, filters: JobSearchFilters) -> JobSearchResult:
        client = self._get_client()
        params: dict[str, Any] = {
            "query": filters.query,
            "num_pages": filters.num_pages,
            "country": filters.country,
            "date_posted": filters.date_posted,
            "remote_jobs_only": str(filters.remote_only).lower(),
        }
        employment_types = self._normalize_employment_types(filters.employment_types or "")
        if employment_types:
            params["employment_types"] = employment_types
        if filters.page > 1:
            params["page"] = filters.page

        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES):
            try:
                resp = await client.get("/api/v2/search", params=params)
                resp.raise_for_status()
                data = resp.json()
                return self._parse_search_result(data, filters)

            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                body = (exc.response.text or "").strip()
                if status in (401, 403):
                    logger.error("Job search API auth error: HTTP %d — %s", status, body[:500])
                    raise RuntimeError(f"OpenNinja auth error HTTP {status}: {body[:500]}") from exc
                if status == 429 or status >= 500:
                    last_error = exc
                    if attempt < MAX_RETRIES - 1:
                        wait = 2**attempt
                        logger.warning(
                            "Job search API error (attempt %d/%d): HTTP %d. "
                            "Retrying in %ds...",
                            attempt + 1,
                            MAX_RETRIES,
                            status,
                            wait,
                        )
                        await asyncio.sleep(wait)
                    continue
                logger.error(
                    "Job search API error: HTTP %d — %s",
                    status,
                    body[:1000],
                )
                raise RuntimeError(f"OpenNinja HTTP {status}: {body[:1000]}") from exc

            except Exception as exc:
                last_error = exc
                if attempt < MAX_RETRIES - 1:
                    wait = 2**attempt
                    logger.warning(
                        "Job search request failed (attempt %d/%d): %s. "
                        "Retrying in %ds...",
                        attempt + 1,
                        MAX_RETRIES,
                        exc,
                        wait,
                    )
                    await asyncio.sleep(wait)
                continue

        raise RuntimeError(
            f"Job search failed after {MAX_RETRIES} attempts. "
            f"Last error: {last_error}"
        )

    async def job_details(self, job_id: str) -> dict[str, Any]:
        client = self._get_client()
        try:
            resp = await client.get("/api/v2/job-details", params={"job_id": job_id})
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Job details API error: HTTP %d — %s",
                exc.response.status_code,
                exc.response.text,
            )
            raise
        except Exception as exc:
            logger.error("Job details request failed: %s", exc)
            raise

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=BASE_URL,
                timeout=30.0,
                headers={
                    "x-api-key": self._api_key,
                    "Content-Type": "application/json",
                },
                follow_redirects=True,
            )
        return self._client

    def _parse_search_result(
        self, data: dict[str, Any], filters: JobSearchFilters
    ) -> JobSearchResult:
        jobs_data = data.get("data", [])
        if isinstance(jobs_data, dict):
            jobs_list = jobs_data.get("jobs", [])
        elif isinstance(jobs_data, list):
            jobs_list = jobs_data
        else:
            jobs_list = []

        jobs = [JobData(**job) for job in jobs_list]

        return JobSearchResult(
            status=data.get("status", ""),
            request_id=data.get("request_id", ""),
            source=self.source_name,
            parameters=filters.model_copy(update={"source": self.source_name}),
            data=jobs,
            raw=data,
        )
