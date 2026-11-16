"""Apify LinkedIn Jobs search provider.

Cost: Apify is a paid platform but provides free credits for new/eligible accounts
    (user indicated ~$5 USD free credit available). After credits, usage is billed
    per actor run/compute and dataset operations according to Apify pricing and the
    selected actor's pricing. Monitor spend in the Apify console.
Auth: Requires `APIFY_API_TOKEN` (personal API token). Set
    `JOB_SEARCH_SOURCE=apify_linkedin`.
Actor: Uses an Apify actor to scrape/search LinkedIn jobs (default:
    `bebity/linkedin-jobs-scraper`, configurable via `APIFY_LINKEDIN_ACTOR_ID`).
    Different actors have different input schemas, speed, reliability, and cost.
Rate limits/quotas:
    - Limited by your Apify plan, available credits, and actor concurrency/limits.
    - Each search triggers an actor run (and may take time).
    - Run options are set to control spend/runtime: timeout 120s, memory 1024 MB,
      maxTotalChargeUsd 1. Keep `num_pages`/result counts low to control cost.
Coverage/limitations:
    - Depends on LinkedIn availability and the chosen actor's behavior; scraping
      can be brittle, return partial data, or break when LinkedIn/actor changes.
    - Use must comply with LinkedIn and Apify terms; scraping may be restricted in
      some contexts.
    - Fields like description, apply link, salary, and remote flag vary by actor
      and listing; mapping is best-effort.
    - Not a guaranteed/stable official API. Test with small queries first.
Verify current actor input/docs and pricing in Apify before relying on it.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from laburator.api.base import JobSearchClient
from laburator.api.dtos import JobSearchFilters, JobSearchResult
from laburator.models import JobData

logger = logging.getLogger(__name__)

APIFY_BASE = "https://api.apify.com/v2"


def _first_str(*vals: Any) -> str | None:
    for v in vals:
        if v is None:
            continue
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)):
            return str(v)
    return None


def _boolish(val: Any) -> bool | None:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        v = val.lower().strip()
        if v in {"true", "1", "yes", "remote"}:
            return True
        if v in {"false", "0", "no", "on-site", "onsite"}:
            return False
    return None


class ApifyLinkedInJobSearchClient(JobSearchClient):
    """Search LinkedIn jobs via an Apify actor (uses run-sync dataset items)."""

    source_name = "apify_linkedin"

    def __init__(self, api_token: str, actor_id: str = "bebity/linkedin-jobs-scraper") -> None:
        self._api_token = api_token
        # Apify actor id in path form owner~name or owner/name; normalize for URL
        self._actor_id = (actor_id or "bebity/linkedin-jobs-scraper").replace("/", "~")
        self._client: httpx.AsyncClient | None = None

    async def search(self, filters: JobSearchFilters) -> JobSearchResult:
        client = self._get_client()
        # Input aligned to requested LinkedIn Jobs actor schema.
        query = (filters.query or "").strip()
        pages = max(1, int(filters.num_pages or 1))
        rows = min(50, pages * 20)

        run_input: dict[str, Any] = {
            "companyProfile": True,
            "contractTypes": ["F", "P", "C", "T"],
            "easyApply": False,
            "enrichCompany": False,
            "locations": ["Argentina", "LATAM"],
            "publishedAt": "r86400",
            "rows": rows,
            "titles": [query] if query else [],
            "under10Applicants": False,
            "workTypes": ["2", "3"],
        }

        # Use run-sync-get-dataset-items to wait for results in one call.
        # Run options: timeout 120s, memory 1 GB, max total charge $1.
        url = f"/acts/{self._actor_id}/run-sync-get-dataset-items"
        params = {
            "token": self._api_token,
            "format": "json",
            "clean": "true",
            "timeout": 120,
            "memory": 1024,
            "maxTotalChargeUsd": 1,
        }
        try:
            resp = await client.post(url, params=params, json=run_input, timeout=150.0)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.error("Apify LinkedIn search failed (actor=%s): %s", self._actor_id, exc)
            raise

        items = data if isinstance(data, list) else []
        jobs: list[JobData] = []
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            job_id = _first_str(item.get("id"), item.get("jobId"), item.get("job_id"), item.get("urn"), item.get("url"), item.get("link"))
            if not job_id:
                job_id = f"apify-{idx}"
            title = _first_str(item.get("title"), item.get("jobTitle"), item.get("positionName"))
            company = _first_str(item.get("companyName"), item.get("company"), item.get("employer"), item.get("company_name"))
            location = _first_str(item.get("location"), item.get("jobLocation"), item.get("formattedLocation"), item.get("place"))
            description = _first_str(item.get("description"), item.get("jobDescription"), item.get("descriptionText"), item.get("text"))
            apply_link = _first_str(item.get("applyUrl"), item.get("applyLink"), item.get("url"), item.get("link"), item.get("jobUrl"))
            posted = _first_str(item.get("postedAt"), item.get("postedTime"), item.get("publishedAt"), item.get("postedDate"))
            remote = _boolish(item.get("remote"))
            if remote is None and isinstance(item.get("workplaceType"), str):
                remote = "remote" in item["workplaceType"].lower()
            if remote is None and location:
                remote = "remote" in location.lower()
            salary = _first_str(item.get("salary"), item.get("salaryText"), item.get("compensation"))
            emp = _first_str(item.get("employmentType"), item.get("jobType"), item.get("contractType"))

            jobs.append(JobData(
                job_id=str(job_id),
                job_title=title,
                employer_name=company,
                job_description=description,
                job_location=location,
                job_is_remote=remote,
                job_apply_link=apply_link,
                job_google_link=apply_link,
                job_posted_at=posted,
                job_salary=salary,
                job_employment_type=emp,
            ))

        return JobSearchResult(
            status="OK",
            request_id="",
            source=self.source_name,
            parameters=filters.model_copy(update={"source": self.source_name}),
            data=jobs,
            raw={"count": len(jobs), "actor_id": self._actor_id},
        )

    async def job_details(self, job_id: str) -> dict[str, Any]:
        # Detail enrichment depends on actor; return minimal merge (search payload is primary).
        return {"job_id": job_id, "source": self.source_name}

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=APIFY_BASE,
                timeout=30.0,
                headers={"Accept": "application/json"},
            )
        return self._client
