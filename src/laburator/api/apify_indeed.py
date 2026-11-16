"""Apify Indeed Jobs search provider.

Cost: Apify is a paid platform but provides free credits for new/eligible accounts
    (user indicated ~$5 USD free credit available). After credits, usage is billed
    per actor run/compute and dataset operations according to Apify pricing and the
    selected actor's pricing. Monitor spend in the Apify console.
Auth: Requires `APIFY_API_TOKEN` (personal API token). Set
    `JOB_SEARCH_SOURCE=apify_indeed`.
Actor: Uses an Apify actor to scrape/search Indeed jobs (default:
    `valig/indeed-jobs-scraper`, configurable via `APIFY_INDEED_ACTOR_ID`).
    Different actors have different input schemas, speed, reliability, and cost.
Rate limits/quotas:
    - Limited by your Apify plan, available credits, and actor concurrency/limits.
    - Each search triggers an actor run (and may take time depending on limit).
    - Keep `num_pages`/`limit` low to control cost and runtime.
Coverage/limitations:
    - Depends on Indeed availability/country support and the chosen actor's behavior;
      scraping can be brittle, return partial data, or break when Indeed/actor changes.
    - Use must comply with Indeed and Apify terms; scraping may be restricted.
    - Fields (description, apply link, salary, remote flag) vary by actor/listing and
      are mapped best-effort. Generic filters like `remote_only` and
      `employment_types` are not first-class in this actor's schema and are applied
      best-effort or ignored.
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


class ApifyIndeedJobSearchClient(JobSearchClient):
    """Search Indeed jobs via an Apify actor (uses run-sync dataset items)."""

    source_name = "apify_indeed"

    def __init__(self, api_token: str, actor_id: str = "valig/indeed-jobs-scraper") -> None:
        self._api_token = api_token
        self._actor_id = (actor_id or "valig/indeed-jobs-scraper").replace("/", "~")
        self._client: httpx.AsyncClient | None = None

    @staticmethod
    def _map_date_posted(date_posted: str) -> str:
        """Map generic date filter to Indeed actor's `datePosted` (days) when possible."""
        v = (date_posted or "all").strip().lower()
        mapping = {
            "day": "1",
            "24h": "1",
            "past-24h": "1",
            "past 24 hours": "1",
            "3days": "3",
            "3-days": "3",
            "past-3-days": "3",
            "week": "7",
            "past-week": "7",
            "past week": "7",
            "14days": "14",
            "past-14-days": "14",
            "month": "30",
            "past-month": "30",
            "past month": "30",
            "all": "",
            "any": "",
        }
        return mapping.get(v, "")

    @staticmethod
    def _map_location(filters: JobSearchFilters) -> str:
        """Best-effort location string. Prefer explicit hint in query not possible; use country/blank."""
        # Generic filters don't carry a separate city/location field; leave blank to broaden
        # or rely on actor defaults. Country is passed separately.
        return ""

    async def search(self, filters: JobSearchFilters) -> JobSearchResult:
        client = self._get_client()
        pages = max(1, int(filters.num_pages or 1))
        limit = min(100, max(1, pages) * 25)
        country = (filters.country or "us").strip().lower() or "us"
        title = (filters.query or "").strip()
        date_posted = self._map_date_posted(filters.date_posted)

        # Input aligned to valig/indeed-jobs-scraper schema.
        run_input: dict[str, Any] = {
            "country": country,
            "title": title,
            "location": self._map_location(filters),
            "limit": limit,
        }
        if date_posted:
            run_input["datePosted"] = date_posted

        url = f"/acts/{self._actor_id}/run-sync-get-dataset-items"
        params = {"token": self._api_token, "format": "json", "clean": "true"}
        try:
            resp = await client.post(url, params=params, json=run_input, timeout=180.0)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.error("Apify Indeed search failed (actor=%s): %s", self._actor_id, exc)
            raise

        items = data if isinstance(data, list) else []
        jobs: list[JobData] = []
        q = title.lower()
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            job_id = _first_str(item.get("id"), item.get("jobKey"), item.get("job_key"), item.get("url"), item.get("link"))
            if not job_id:
                job_id = f"apify-indeed-{idx}"
            job_title = _first_str(item.get("title"), item.get("positionName"), item.get("jobTitle"))
            company = _first_str(item.get("company"), item.get("companyName"), item.get("employer"))
            location = _first_str(item.get("location"), item.get("formattedLocation"), item.get("jobLocation"))
            description = _first_str(item.get("description"), item.get("jobDescription"), item.get("descriptionText"), item.get("snippet"))
            apply_link = _first_str(item.get("url"), item.get("link"), item.get("jobUrl"), item.get("applyUrl"))
            posted = _first_str(item.get("datePosted"), item.get("postedAt"), item.get("publishedAt"), item.get("age"))
            salary = _first_str(item.get("salary"), item.get("salaryText"), item.get("compensation"))
            emp = _first_str(item.get("jobType"), item.get("employmentType"), item.get("contractType"))
            remote = _boolish(item.get("isRemote"))
            if remote is None and isinstance(item.get("remote"), (bool, str)):
                remote = _boolish(item.get("remote"))
            if remote is None and location:
                remote = "remote" in str(location).lower()

            # best-effort generic filters not native to schema
            if filters.remote_only and not remote:
                continue
            if filters.employment_types and emp:
                wanted = {t.strip().lower().replace("_", "-") for t in filters.employment_types.split(",") if t.strip()}
                if wanted and str(emp).lower().replace("_", "-") not in wanted:
                    continue

            jobs.append(JobData(
                job_id=str(job_id),
                job_title=job_title,
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
            raw={"count": len(jobs), "actor_id": self._actor_id, "country": country},
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
                base_url=APIFY_BASE,
                timeout=30.0,
                headers={"Accept": "application/json"},
            )
        return self._client
