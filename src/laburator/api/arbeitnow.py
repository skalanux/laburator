"""Arbeitnow job search provider.

Cost: Free ($0). No paid plan required for the public job board API.
Auth: No API key required.
Rate limits: No officially published hard quota for the public endpoint; use
    responsibly and avoid aggressive polling (treat as a low-volume personal-use
    API). Throttling/blocking is possible if abused.
Coverage/limitations:
    - Focus on tech/IT and many remote-friendly roles, stronger for EU/global
      than US-local coverage.
    - Limited server-side filters (mainly search/remote/page-like behavior may be
      constrained by what the endpoint exposes); client-side filtering is applied
      where needed.
    - Fields (salary, employment type, posted date) may be missing or coarse.
    - No dedicated job-details endpoint in the public API; `job_details` returns
      the best-known indexed record shape / empty merge.
Terms/limits can change: verify https://www.arbeitnow.com/api/job-board-api before
relying on it.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from laburator.api.base import JobSearchClient
from laburator.api.dtos import JobSearchFilters, JobSearchResult
from laburator.models import JobData

logger = logging.getLogger(__name__)

BASE_URL = "https://www.arbeitnow.com/api/job-board-api"


def _as_bool_remote(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"true", "1", "yes", "remote"}
    return False


class ArbeitnowJobSearchClient(JobSearchClient):
    """Client for the public Arbeitnow job board API (no auth)."""

    source_name = "arbeitnow"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def search(self, filters: JobSearchFilters) -> JobSearchResult:
        client = self._get_client()
        jobs: list[JobData] = []
        q = (filters.query or "").lower().strip()
        pages = max(1, int(filters.num_pages or 1))
        seen_ids: set[str] = set()

        for page in range(1, pages + 1):
            params: dict[str, Any] = {"page": page}
            if filters.query:
                params["search"] = filters.query
            try:
                resp = await client.get("", params=params)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.error("Arbeitnow search failed (page %s): %s", page, exc)
                raise

            items = data.get("data") or []
            if not isinstance(items, list) or not items:
                break

            for item in items:
                if not isinstance(item, dict):
                    continue
                slug = str(item.get("slug") or item.get("url") or "")
                job_id = slug or str(item.get("id") or item.get("created_at") or len(jobs))
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)

                title = item.get("title") or ""
                company = item.get("company_name") or ""
                location = item.get("location") or ""
                remote = _as_bool_remote(item.get("remote"))
                tags = item.get("tags") or []
                job_types = item.get("job_types") or []
                description = item.get("description") or ""
                url = item.get("url") or ""

                # client-side filters best-effort
                if filters.remote_only and not remote:
                    continue
                if q:
                    hay = " ".join([str(title), str(company), str(location), str(description), " ".join(map(str, tags))]).lower()
                    # search param usually applied, but keep best-effort
                    if q not in hay and not all(tok in hay for tok in q.split()[:3] if tok):
                        # allow if API already matched via search param
                        pass

                emp_type = None
                if isinstance(job_types, list) and job_types:
                    emp_type = str(job_types[0])
                elif isinstance(job_types, str):
                    emp_type = job_types

                job = JobData(
                    job_id=job_id,
                    job_title=str(title) if title else None,
                    employer_name=str(company) if company else None,
                    job_description=str(description) if description else None,
                    job_location=str(location) if location else None,
                    job_is_remote=bool(remote),
                    job_apply_link=str(url) if url else None,
                    job_google_link=str(url) if url else None,
                    job_employment_type=emp_type,
                    job_employment_types=[str(x) for x in job_types] if isinstance(job_types, list) else ([job_types] if job_types else None),
                    job_posted_at=str(item.get("created_at")) if item.get("created_at") else None,
                    job_highlights={"tags": tags} if tags else None,
                )
                jobs.append(job)

            # stop early if no more pages indicated
            links = data.get("links") or {}
            if isinstance(links, dict) and not links.get("next"):
                # some responses use links.next; if absent, still respect requested pages
                pass

        return JobSearchResult(
            status="OK",
            request_id="",
            source=self.source_name,
            parameters=filters.model_copy(update={"source": self.source_name}),
            data=jobs,
            raw={"count": len(jobs)},
        )

    async def job_details(self, job_id: str) -> dict[str, Any]:
        # Public endpoint does not expose per-job details; return minimal merge.
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
