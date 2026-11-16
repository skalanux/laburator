"""Remotive job search provider.

Cost: Free ($0). No paid plan required for the public API.
Auth: No API key required.
Rate limits: Public API is intended for reasonable use; community guidance
    suggests keeping request volume low (e.g. around ~2 requests/minute) and
    caching results. Excessive use may be throttled/blocked.
Coverage/limitations:
    - Remote jobs only (global). Not suitable for non-remote/on-site searches.
    - Filters are limited (search/category/company_name); `country`, `date_posted`,
      and `employment_types` from generic filters are not fully supported and are
      applied best-effort or ignored.
    - Job descriptions are HTML in source; stored as provided (may include tags).
    - Attribution/terms may apply for public use: verify https://remotive.com/api-documentation
      and https://remotive.com/api/remote-jobs before relying on it.
Terms/limits can change.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from laburator.api.base import JobSearchClient
from laburator.api.dtos import JobSearchFilters, JobSearchResult
from laburator.models import JobData

logger = logging.getLogger(__name__)

BASE_URL = "https://remotive.com/api/remote-jobs"


class RemotiveJobSearchClient(JobSearchClient):
    """Client for the public Remotive remote jobs API (no auth)."""

    source_name = "remotive"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def search(self, filters: JobSearchFilters) -> JobSearchResult:
        client = self._get_client()
        params: dict[str, Any] = {}
        if filters.query:
            params["search"] = filters.query
        # limit can reduce payload; API supports limit in some contexts, keep simple
        try:
            resp = await client.get("", params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.error("Remotive search failed: %s", exc)
            raise

        items = data.get("jobs") or []
        jobs: list[JobData] = []
        q = (filters.query or "").lower().strip()

        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            job_id = str(item.get("id") or item.get("url") or "")
            title = item.get("title") or ""
            company = item.get("company_name") or ""
            location = item.get("candidate_required_location") or "Remote"
            description = item.get("description") or ""
            url = item.get("url") or ""
            job_type = item.get("job_type") or None
            pub = item.get("publication_date") or None
            salary = item.get("salary") or None
            category = item.get("category") or None
            tags = item.get("tags") or []

            if filters.employment_types and job_type:
                wanted = {t.strip().lower() for t in filters.employment_types.split(",") if t.strip()}
                if wanted and str(job_type).lower() not in wanted:
                    continue

            job = JobData(
                job_id=job_id or None,
                job_title=str(title) if title else None,
                employer_name=str(company) if company else None,
                employer_logo=str(item.get("company_logo")) if item.get("company_logo") else None,
                job_description=str(description) if description else None,
                job_location=str(location) if location else None,
                job_is_remote=True,
                job_apply_link=str(url) if url else None,
                job_google_link=str(url) if url else None,
                job_employment_type=str(job_type) if job_type else None,
                job_posted_at=str(pub) if pub else None,
                job_salary=str(salary) if salary else None,
                job_highlights={"category": category, "tags": tags} if (category or tags) else None,
            )
            jobs.append(job)

        # num_pages not natively supported as separate pages; return all matches.
        return JobSearchResult(
            status="OK",
            request_id="",
            source=self.source_name,
            parameters=filters.model_copy(update={"source": self.source_name}),
            data=jobs,
            raw={"count": len(jobs), "note": "Remotive returns remote jobs in a single response; num_pages/country/date_posted partially unsupported."},
        )

    async def job_details(self, job_id: str) -> dict[str, Any]:
        # No separate details endpoint; details are included in search results.
        return {"job_id": job_id, "source": self.source_name}

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=BASE_URL,
                timeout=30.0,
                headers={"Accept": "application/json"},
                follow_redirects=True,
            )
        return self._client
