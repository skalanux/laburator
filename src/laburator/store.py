"""Lightweight JSON index for searches, jobs, and user decisions."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from laburator.api.dtos import JobSearchFilters, JobSearchResult
from laburator.models import JobData


DECISION_PENDING = "pending"
DECISION_INTERESTED = "interested"
DECISION_REJECTED = "rejected"
VALID_DECISIONS = {DECISION_PENDING, DECISION_INTERESTED, DECISION_REJECTED}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(path)


def _slugify(text: str) -> str:
    text = (text or "").lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text)
    return text[:80].rstrip("-") or "search"


def job_key(job: dict[str, Any] | JobData, source: str = "") -> str:
    """Return a stable key for a job across index operations."""
    if isinstance(job, JobData):
        data = job.model_dump()
    else:
        data = job
    src = source or data.get("_source") or ""
    job_id = data.get("job_id")
    if job_id:
        return f"{src}:{job_id}" if src else str(job_id)
    basis = "|".join(
        [
            str(data.get("job_title") or ""),
            str(data.get("employer_name") or ""),
            str(data.get("job_apply_link") or data.get("job_google_link") or ""),
            str((data.get("job_description") or "")[:200]),
        ]
    )
    digest = hashlib.sha1(basis.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"{src}:hash:{digest}" if src else f"hash:{digest}"


class JobIndexStore:
    """File-based index for search runs and job decisions."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.index_dir = output_dir / "index"
        self.searches_dir = self.index_dir / "searches"
        self.decisions_path = self.index_dir / "decisions.json"

    # ── Searches ────────────────────────────────────────────────────────

    def save_search(self, result: JobSearchResult, filters: JobSearchFilters | None = None) -> dict[str, Any]:
        """Persist a search run and return the saved record metadata."""
        search_id = uuid.uuid4().hex[:12]
        params = (filters or result.parameters or JobSearchFilters()).model_dump()
        source = result.source or params.get("source") or "unknown"
        jobs: list[dict[str, Any]] = []
        for job in result.data:
            item = job.model_dump()
            item["_job_key"] = job_key(item, source)
            item["_source"] = source
            item.setdefault("job_id", item["_job_key"])
            jobs.append(item)

        record = {
            "id": search_id,
            "created_at": _now_iso(),
            "source": source,
            "query": params.get("query", ""),
            "parameters": params,
            "status": result.status,
            "request_id": result.request_id,
            "count": len(jobs),
            "jobs": jobs,
        }
        _atomic_write_json(self.searches_dir / f"{search_id}.json", record)

        # Backward-compatible cache for CLI/tools expecting cache/<slug>/jobs.json
        query = params.get("query") or "search"
        cache_dir = self.output_dir / "cache" / _slugify(query)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_jobs = [{k: v for k, v in j.items() if not k.startswith("_")} for j in jobs]
        _atomic_write_json(cache_dir / "jobs.json", cache_jobs)

        return {k: v for k, v in record.items() if k != "jobs"}

    def list_searches(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.searches_dir.exists():
            return []
        records: list[dict[str, Any]] = []
        for path in self.searches_dir.glob("*.json"):
            data = _safe_read_json(path, None)
            if not isinstance(data, dict):
                continue
            records.append({k: v for k, v in data.items() if k != "jobs"})
        records.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return records[:limit]

    def get_search(self, search_id: str) -> dict[str, Any] | None:
        data = _safe_read_json(self.searches_dir / f"{search_id}.json", None)
        return data if isinstance(data, dict) else None

    # ── Decisions ───────────────────────────────────────────────────────

    def _load_decisions(self) -> dict[str, dict[str, Any]]:
        data = _safe_read_json(self.decisions_path, {})
        return data if isinstance(data, dict) else {}

    def get_decisions(self) -> dict[str, dict[str, Any]]:
        return self._load_decisions()

    def get_decision(self, key: str) -> str:
        entry = self._load_decisions().get(key, {})
        if isinstance(entry, dict):
            val = entry.get("decision", DECISION_PENDING)
            return val if val in VALID_DECISIONS else DECISION_PENDING
        return DECISION_PENDING

    def set_decision(self, key: str, decision: str, notes: str = "") -> dict[str, Any]:
        if decision not in VALID_DECISIONS:
            raise ValueError(f"Invalid decision '{decision}'")
        decisions = self._load_decisions()
        entry = decisions.get(key, {}) if isinstance(decisions.get(key), dict) else {}
        entry.update({"decision": decision, "updated_at": _now_iso()})
        if notes:
            entry["notes"] = notes
        elif "notes" in entry and notes == "":
            pass
        decisions[key] = entry
        _atomic_write_json(self.decisions_path, decisions)
        return {"job_key": key, **entry}

    # ── Jobs ────────────────────────────────────────────────────────────

    def _iter_search_records(self) -> list[dict[str, Any]]:
        if not self.searches_dir.exists():
            return []
        records = []
        for path in self.searches_dir.glob("*.json"):
            data = _safe_read_json(path, None)
            if isinstance(data, dict):
                records.append(data)
        records.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return records

    def list_jobs(
        self,
        search_id: str | None = None,
        decision: str | None = None,
        q: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        decisions = self._load_decisions()
        if search_id:
            rec = self.get_search(search_id)
            records = [rec] if rec else []
        else:
            records = self._iter_search_records()

        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        ql = (q or "").lower().strip()
        for rec in records:
            source = rec.get("source", "")
            for job in rec.get("jobs", []) or []:
                if not isinstance(job, dict):
                    continue
                key = job.get("_job_key") or job_key(job, source)
                if key in seen:
                    continue
                seen.add(key)
                dec_entry = decisions.get(key, {})
                dec = dec_entry.get("decision", DECISION_PENDING) if isinstance(dec_entry, dict) else DECISION_PENDING
                if decision and dec != decision:
                    continue
                item = {k: v for k, v in job.items() if not k.startswith("_")}
                item["_job_key"] = key
                item["_source"] = source
                item["_decision"] = dec
                item["_search_id"] = rec.get("id")
                item["_search_query"] = rec.get("query")
                item["_search_created_at"] = rec.get("created_at")
                if ql:
                    hay = " ".join(
                        str(item.get(f) or "")
                        for f in ("job_title", "employer_name", "job_description", "job_location")
                    ).lower()
                    if ql not in hay:
                        continue
                out.append(item)
                if len(out) >= limit:
                    return out
        return out

    def get_job(self, job_key_value: str) -> dict[str, Any] | None:
        decisions = self._load_decisions()
        for rec in self._iter_search_records():
            source = rec.get("source", "")
            for job in rec.get("jobs", []) or []:
                if not isinstance(job, dict):
                    continue
                key = job.get("_job_key") or job_key(job, source)
                if key != job_key_value:
                    # also allow raw job_id match
                    if str(job.get("job_id") or "") != job_key_value:
                        continue
                    key = job_key_value
                dec_entry = decisions.get(job_key_value, {}) or decisions.get(key, {})
                dec = dec_entry.get("decision", DECISION_PENDING) if isinstance(dec_entry, dict) else DECISION_PENDING
                item = {k: v for k, v in job.items() if not k.startswith("_")}
                item["_job_key"] = key
                item["_source"] = source
                item["_decision"] = dec
                item["_search_id"] = rec.get("id")
                item["_search_query"] = rec.get("query")
                return item
        return None

    def merge_job_details(self, job_key_value: str, details: dict[str, Any]) -> dict[str, Any] | None:
        """Merge fetched job details into the stored job record and persist. Returns updated job or None."""
        if not isinstance(details, dict) or not details:
            return self.get_job(job_key_value)
        for rec in self._iter_search_records():
            source = rec.get("source", "")
            search_id = rec.get("id", "")
            jobs = rec.get("jobs", []) or []
            changed = False
            for idx, job in enumerate(jobs):
                if not isinstance(job, dict):
                    continue
                key = job.get("_job_key") or job_key(job, source)
                matched = key == job_key_value or str(job.get("job_id") or "") == job_key_value
                if not matched:
                    continue
                merged = dict(job)
                for k, v in details.items():
                    if v is None or v == "" or v == [] or v == {}:
                        continue
                    # prefer richer incoming description/details; overwrite when meaningful
                    if k in ("job_description", "description", "job_highlights", "qualifications", "responsibilities", "benefits"):
                        merged[k] = v
                    else:
                        merged.setdefault(k, v)
                # keep meta keys stable
                merged["_job_key"] = merged.get("_job_key") or key
                merged["_source"] = merged.get("_source") or source
                jobs[idx] = merged
                rec["jobs"] = jobs
                path = self.searches_dir / f"{search_id}.json"
                _atomic_write_json(path, rec)
                changed = True
                break
            if changed:
                return self.get_job(job_key_value)
        return None
