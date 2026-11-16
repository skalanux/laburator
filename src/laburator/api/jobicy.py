"""Jobicy job search provider.

Cost: Free ($0). Public remote jobs feed/API intended for broad access.
Auth: No API key required for the public feed endpoint.
Rate limits: No strict published quota, but fair-use applies; avoid high-frequency
    polling and cache results when possible.
Coverage/limitations:
    - Remote jobs focus. Coverage varies by industry/region and is not exhaustive.
    - Filters supported by the feed are limited (e.g. count/tag/geo/industry
      depending on API version); generic filters like `date_posted`,
      `employment_types`, and precise `country` are only best-effort.
    - Response schema/fields may evolve; some fields (salary, detailed location)
      can be missing.
Terms/limits can change: verify https://jobicy.com/jobs-rss-feed and the public
API docs before relying on it.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from laburator.api.base import JobSearchClient
from laburator.api.dtos import JobSearchFilters, JobSearchResult
from laburator.models import JobData

logger = logging.getLogger(__name__)

BASE_URL = "https://jobicy.com/api/v2/remote-jobs"


class JobicyJobSearchClient(JobSearchClient):
    """Client for the public Jobicy remote jobs API (no auth)."""

    source_name = "jobicy"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def search(self, filters: JobSearchFilters) -> JobSearchResult:
        client = self._get_client()
        # Jobicy supports count/geo/industry/tag; map query best-effort via tag/geo not exact
        count = min(50, max(1, int(filters.num_pages or 1)) * 20)
        params: dict[str, Any] = {"count": count}
        if filters.query:
            # tag param is a single keyword; use first token as best-effort hint
            params["tag"] = filters.query.split()[0]
        try:
            resp = await client.get("", params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.error("Jobicy search failed: %s", exc)
            raise

        items = data.get("jobs") or data.get("data") or []
        q = (filters.query or "").lower().strip()
        jobs: list[JobData] = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            job_id = str(item.get("id") or item.get("url") or item.get("jobSlug") or "")
            title = item.get("jobTitle") or item.get("title") or ""
            company = item.get("companyName") or item.get("company_name") or ""
            geo = item.get("jobGeo") or item.get("location") or "Remote"
            description = item.get("jobDescription") or item.get("description") or item.get("jobExcerpt") or ""
            url = item.get("url") or item.get("jobUrl") or ""
            job_type = item.get("jobType") or item.get("job_type") or None
            if isinstance(job_type, list):
                job_type_val = str(job_type[0]) if job_type else None
                job_types = [str(x) for x in job_type]
            else:
                job_type_val = str(job_type) if job_type else None
                job_types = [job_type_val] if job_type_val else None

            pub = item.get("pubDate") or item.get("published_at") or item.get("jobPostedAt") or None
            salary_min = item.get("annualSalaryMin")
            salary_max = item.get("annualSalaryMax")
            currency = item.get("salaryCurrency") or ""
            salary = None
            if salary_min or salary_max:
                salary = f"{salary_min or ''}-{salary_max or ''} {currency}".strip(" -")

            if q:
                hay = " ".join([str(title), str(company), str(geo), str(description)]).lower()
                if q not in hay and not any(tok in hay for tok in q.split()[:3] if len(tok) > 2):
                    continue

            if filters.employment_types and job_type_val:
                wanted = {t.strip().lower() for t in filters.employment_types.split(",") if t.strip()}
                if wanted and job_type_val.lower() not in wanted:
                    continue

            industry = item.get("jobIndustry") or item.get("industry")
            level = item.get("jobLevel")
            job = JobData(
                job_id=job_id or None,
                job_title=str(title) if title else None,
                employer_name=str(company) if company else None,
                employer_logo=str(item.get("companyLogo")) if item.get("companyLogo") else None,
                job_description=str(description) if description else None,
                job_location=str(geo) if geo else None,
                job_is_remote=True,
                job_apply_link=str(url) if url else None,
                job_google_link=str(url) if url else None,
                job_employment_type=job_type_val,
                job_employment_types=job_types,
                job_posted_at=str(pub) if pub else None,
                job_salary=salary,
                job_min_salary=float(salary_min) if salary_min not in (None, "") else None,
                job_max_salary=float(salary_max) if salary_max not in (None, "") else None,
                job_highlights={"industry": industry, "level": level} if (industry or level) else None,
            )
            jobs.append(job)

        return JobSearchResult(
            status="OK",
            request_id="",
            source=self.source_name,
            parameters=filters.model_copy(update={"source": self.source_name}),
            data=jobs,
            raw={"count": len(jobs), "note": "Jobicy is remote-focused; some generic filters are best-effort."},
        )

    async def job_details(self, job_id: str) -> dict[str, Any]:
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
