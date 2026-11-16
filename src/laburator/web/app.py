"""Web interface for Laburator.

Start with:
    uvicorn laburator.web.app:app --reload
"""

import logging
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import markdown
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from weasyprint import HTML

from laburator.api import JobSearchAdapter, JobSearchFilters, create_search_adapter
from laburator.config import LaburatorConfig
from laburator.llm.service import LLMService
from laburator.skills import (
    SKILL_FILENAMES,
    SKILL_NAMES,
    build_proposal_messages,
    build_user_messages,
    load_skill,
    response_format,
)
from laburator.store import VALID_DECISIONS, JobIndexStore

_STOPWORDS = {
    "para", "con", "los", "las", "del", "una", "uno", "por", "que", "como", "más", "mas",
    "the", "and", "for", "with", "you", "your", "our", "are", "will", "this", "that",
    "from", "have", "has", "not", "but", "all", "can", "job", "work", "role", "team",
    "experience", "years", "year", "skills", "ability", "looking", "required", "plus",
}


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-zA-ZáéíóúüñÁÉÍÓÚÜÑ0-9+#.]{3,}", (text or "").lower())
    return {w for w in words if w not in _STOPWORDS}


def _load_cv_text() -> str:
    try:
        path = config.resolved_cv_path
        if path.exists():
            return path.read_text(encoding="utf-8")
    except Exception:
        pass
    return ""


def _score_job(job: dict[str, Any], cv_tokens: set[str]) -> int:
    if not cv_tokens:
        return 0
    title = _tokenize(str(job.get("job_title") or ""))
    employer = _tokenize(str(job.get("employer_name") or ""))
    desc = _tokenize(str(job.get("job_description") or "")[:4000])
    loc = _tokenize(str(job.get("job_location") or ""))
    score = 0
    score += len(title & cv_tokens) * 4
    score += len(employer & cv_tokens) * 2
    score += len(desc & cv_tokens)
    score += len(loc & cv_tokens)
    if job.get("job_is_remote"):
        remote_hints = {"remote", "remoto", "distributed", "anywhere"}
        if cv_tokens & remote_hints:
            score += 2
    return score


def _norm_text(s: str) -> str:
    s = (s or "").lower().strip()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _norm_link(s: str) -> str:
    s = (s or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"^https?://", "", s)
    s = s.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    return s


def _dupe_signature(job: dict[str, Any]) -> str:
    title = _norm_text(str(job.get("job_title") or ""))
    emp = _norm_text(str(job.get("employer_name") or ""))
    link = _norm_link(str(job.get("job_apply_link") or job.get("job_google_link") or ""))
    # if no link, include coarse location to reduce false positives
    loc = _norm_text(str(job.get("job_location") or "")) if not link else ""
    if not title and not emp and not link:
        return ""
    return "|".join([title[:80], emp[:60], link[:120], loc[:40]])


def _annotate_dupes(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[int]] = {}
    sigs: list[str] = []
    for idx, job in enumerate(jobs):
        sig = _dupe_signature(job)
        sigs.append(sig)
        if sig:
            groups.setdefault(sig, []).append(idx)
    out = []
    for idx, job in enumerate(jobs):
        item = dict(job)
        sig = sigs[idx]
        peers = groups.get(sig, []) if sig else []
        if len(peers) > 1:
            item["_dupe_group_size"] = len(peers)
            item["_is_dupe"] = peers[0] != idx
            # list other sources/keys briefly
            others = []
            for j in peers:
                if j == idx:
                    continue
                o = jobs[j]
                others.append(str(o.get("_source") or "?"))
            item["_dupe_sources"] = sorted(set(others))
        else:
            item["_dupe_group_size"] = 1
            item["_is_dupe"] = False
            item["_dupe_sources"] = []
        out.append(item)
    return out


def _rank_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cv_tokens = _tokenize(_load_cv_text())
    ranked = []
    for job in jobs:
        item = dict(job)
        item["_score"] = _score_job(item, cv_tokens)
        item = _annotate_job_outputs(item)
        ranked.append(item)
    ranked.sort(key=lambda j: (j.get("_score", 0), str(j.get("_search_created_at") or "")), reverse=True)
    ranked = _annotate_dupes(ranked)
    return ranked

config = LaburatorConfig()
logger = logging.getLogger(__name__)
store = JobIndexStore(config.resolved_output_dir)


class GenerateRequest(BaseModel):
    job_description: str
    skill: str
    link: str = ""


class SaveContent(BaseModel):
    content: str


class SearchRequest(BaseModel):
    query: str
    num_pages: int = 1
    country: str = "us"
    remote_only: bool = False
    date_posted: str = "all"
    employment_types: str = ""
    page: int = 1
    source: str = ""  # defaults to config.job_search_source when empty


class DecisionRequest(BaseModel):
    job_key: str
    decision: str
    notes: str = ""


class DiscoveryGenerateRequest(BaseModel):
    job_key: str
    skill: str
    link: str = ""
    force: bool = False


class DiscoveryEnrichRequest(BaseModel):
    job_key: str
    source: str = ""  # defaults to job's source or config


def _get_search_adapter(source: str) -> JobSearchAdapter:
    try:
        return create_search_adapter(source or getattr(config, "job_search_source", "openninja"), config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text)
    return text[:80].rstrip("-")


def _discovery_folder_name(job_data: dict[str, Any], job_key_value: str = "") -> str:
    company = _slugify(job_data.get("employer_name") or "unknown")
    title = _slugify(job_data.get("job_title") or "job")
    folder_name = f"{company}-{title}"[:80].rstrip("-")
    if not folder_name:
        folder_name = _slugify(job_key_value) or "job"
    return folder_name


def _find_existing_output_path(job_data: dict[str, Any], job_key_value: str, skill_name: str) -> Path | None:
    """Find an existing output for job+skill across all run-date folders (latest first)."""
    folder = _discovery_folder_name(job_data, job_key_value)
    filename = SKILL_FILENAMES.get(skill_name, skill_name) + ".md"
    root = config.resolved_output_dir
    if not root.is_dir():
        return None
    candidates: list[Path] = []
    for date_dir in root.iterdir():
        if not date_dir.is_dir():
            continue
        # skip index/cache dirs
        if date_dir.name in {"index", "cache"}:
            continue
        p = date_dir / folder / filename
        if p.exists():
            candidates.append(p)
    if not candidates:
        return None
    # prefer most recent run-date dir name, then mtime
    candidates.sort(key=lambda p: (p.parent.parent.name, p.stat().st_mtime), reverse=True)
    return candidates[0]


def _existing_outputs_for_job(job_data: dict[str, Any], job_key_value: str = "") -> list[str]:
    """Return skill names with existing output across any run date (no LLM)."""
    out: list[str] = []
    for skill in SKILL_NAMES:
        if _find_existing_output_path(job_data, job_key_value, skill) is not None:
            out.append(skill)
    return out


def _annotate_job_outputs(job: dict[str, Any]) -> dict[str, Any]:
    item = dict(job)
    key = item.get("_job_key") or item.get("job_id") or ""
    try:
        item["_existing_outputs"] = _existing_outputs_for_job(item, str(key))
    except Exception:
        item["_existing_outputs"] = []
    return item


def _read_context(cv_path: Path, wiki_path: Path) -> tuple[str, str]:
    cv_context = ""
    if cv_path.exists():
        try:
            cv_context = cv_path.read_text(encoding="utf-8")
        except Exception:
            pass
    wiki_context = ""
    if wiki_path.is_dir():
        parts: list[str] = []
        for md_file in sorted(wiki_path.glob("*.md")):
            try:
                content = md_file.read_text(encoding="utf-8")
                parts.append(f"## {md_file.stem}\n\n{content}")
            except Exception:
                continue
        wiki_context = "\n\n---\n\n".join(parts)
    return cv_context, wiki_context


def _save_skill_output(
    run_date: str,
    folder_name: str,
    skill_name: str,
    result: str,
    job_md_content: str | None = None,
) -> Path:
    filename = SKILL_FILENAMES.get(skill_name, skill_name) + ".md"
    out_dir = config.resolved_output_dir / run_date / folder_name
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / filename
    try:
        out_path.write_text(result, encoding="utf-8")
    except Exception as exc:
        raise RuntimeError(f"Failed to save file: {exc}")
    if job_md_content:
        try:
            (out_dir / "job.md").write_text(job_md_content, encoding="utf-8")
        except Exception:
            pass
    return out_path


async def _run_skill_for_job(job_data: dict[str, Any], skill_name: str) -> str:
    cv_context, wiki_context = _read_context(config.resolved_cv_path, config.resolved_llmwiki_dir)
    system_prompt = load_skill(skill_name)
    user_messages = build_user_messages(skill_name, job_data, cv_context, wiki_context)
    fmt = response_format(skill_name)
    llm = LLMService(config)
    try:
        return await llm.generate(
            system_prompt=system_prompt,
            user_messages=user_messages,
            response_format=fmt,
        )
    finally:
        await llm.close()


async def generate_skill(
    job_description: str,
    skill_name: str,
    cv_path: Path,
    wiki_path: Path,
    link: str = "",
) -> tuple[str, Path]:
    """Generate a skill output from a manual job description."""
    if skill_name not in SKILL_NAMES:
        raise ValueError(f"Unknown skill. Available: {SKILL_NAMES}")

    # Load CV context
    cv_context = ""
    if cv_path.exists():
        try:
            cv_context = cv_path.read_text(encoding="utf-8")
        except Exception:
            pass

    # Load wiki context (optional)
    wiki_context = ""
    if wiki_path.is_dir():
        parts: list[str] = []
        for md_file in sorted(wiki_path.glob("*.md")):
            try:
                content = md_file.read_text(encoding="utf-8")
                parts.append(f"## {md_file.stem}\n\n{content}")
            except Exception:
                continue
        wiki_context = "\n\n---\n\n".join(parts)

    job_data: dict[str, Any] = {
        "job_id": f"manual-{os.getpid()}-{int(datetime.now().timestamp())}",
        "job_description": job_description,
        "employer_name": "Manual Entry",
        "job_title": "Custom Job Description",
        "job_location": "",
        "job_is_remote": False,
    }
    result = await _run_skill_for_job(job_data, skill_name)

    # Load skill prompt
    try:
        system_prompt = load_skill(skill_name)
    except FileNotFoundError:
        raise FileNotFoundError(f"Skill '{skill_name}' not found")

    # Build messages
    if skill_name == "generarcv":
        user_messages = build_proposal_messages(
            proposal=job_description,
            tips="",
            cv_context=cv_context,
            llmwiki_context=wiki_context,
        )
    else:
        user_messages = build_user_messages(
            skill_name, job_data, cv_context, wiki_context
        )

    # Call LLM
    fmt = response_format(skill_name)
    llm = LLMService(config)
    try:
        result = await llm.generate(
            system_prompt=system_prompt,
            user_messages=user_messages,
            response_format=fmt,
        )
    finally:
        await llm.close()

    # Save output — determine folder name
    run_date = date.today().isoformat()
    now = datetime.now()
    if link and link.strip():
        folder_name = _slugify(link.strip())
    else:
        timestamp = now.strftime("%H%M%S")
        desc_slug = _slugify(job_description[:60])[:40]
        folder_name = f"{run_date}-{timestamp}-{desc_slug}"

    job_md_parts = ["# Job Description\n"]
    if link and link.strip():
        job_md_parts.append(f"\n**Link:** {link.strip()}\n")
    job_md_parts.append(f"\n{job_description}")
    out_path = _save_skill_output(run_date, folder_name, skill_name, result, "".join(job_md_parts))
    return result, out_path


app = FastAPI(title="Laburator Web UI", version="0.1.0")

assets_dir = Path(__file__).parent / "assets"
if assets_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main HTML page."""
    html = _load_template()
    html = html.replace("{{ OUTPUT_DIR }}", str(config.resolved_output_dir))
    return html


@app.post("/generate")
async def generate(request: GenerateRequest):
    """Generate a skill output from a manual job description."""
    logger.info("Generating skill output with skill '%s'...", request.skill)
    try:
        result, filepath = await generate_skill(
            request.job_description,
            request.skill,
            config.resolved_cv_path,
            config.resolved_llmwiki_dir,
            link=request.link,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    rel_path = filepath.relative_to(config.resolved_output_dir)
    return JSONResponse(
        {
            "content": result,
            "filepath": str(filepath),
            "rel_path": str(rel_path),
            "filename": filepath.name,
            "date": filepath.parent.parent.name,
        }
    )


@app.get("/api/outputs")
async def list_outputs(
    page: int = Query(1, ge=1),
    page_size: int = Query(15, ge=1, le=50),
):
    """List generated folders (one per job), paginated by most recent."""
    out_dir = config.resolved_output_dir
    folders: dict[str, dict[str, Any]] = {}

    for f in sorted(
        out_dir.rglob("*/*/*.md"), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        if not f.is_file():
            continue
        parent = f.parent
        key = str(parent.relative_to(out_dir))
        if key not in folders:
            folders[key] = {
                "folder_path": key,
                "name": parent.name,
                "date": parent.parent.name,
                "modified": f.stat().st_mtime,
                "has_job_md": False,
                "link": "",
                "skills": [],
            }
        if f.name == "job.md":
            folders[key]["has_job_md"] = True
            try:
                content = f.read_text(encoding="utf-8")
                folders[key]["job_md_content"] = content
                # Extract link from first line that looks like one
                for line in content.splitlines():
                    if line.startswith("**Link:**"):
                        folders[key]["link"] = line.replace("**Link:**", "").strip()
                        break
            except Exception:
                pass
        else:
            rel = str(f.relative_to(out_dir))
            folders[key]["skills"].append(
                {
                    "filename": f.name,
                    "rel_path": rel,
                    "size": f.stat().st_size,
                }
            )
        # Keep the most recent modification time
        folders[key]["modified"] = max(folders[key]["modified"], f.stat().st_mtime)

    all_folders = sorted(folders.values(), key=lambda x: x["modified"], reverse=True)
    total = len(all_folders)
    total_pages = max(1, (total + page_size - 1) // page_size)
    start = (page - 1) * page_size
    page_folders = all_folders[start : start + page_size]

    return JSONResponse(
        {
            "folders": page_folders,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        }
    )


@app.get("/api/file/{filepath:path}")
async def serve_file(filepath: str, download: bool = Query(False, alias="download")):
    """Serve a file from the output directory with path safety."""
    # Resolve relative to output_dir to prevent path traversal
    resolved_path = (config.resolved_output_dir / filepath).resolve()
    if not resolved_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    base = config.resolved_output_dir.resolve()
    if not str(resolved_path).startswith(str(base)):
        raise HTTPException(status_code=403, detail="Access denied")

    response = FileResponse(path=resolved_path, filename=resolved_path.name)
    if download:
        response.headers["Content-Disposition"] = (
            f'attachment; filename="{resolved_path.name}"'
        )
    else:
        response.headers["Content-Disposition"] = (
            f'inline; filename="{resolved_path.name}"'
        )
    return response


@app.put("/api/file/{filepath:path}")
async def save_file(filepath: str, body: SaveContent):
    """Save edited content to a file in the output directory."""
    resolved_path = (config.resolved_output_dir / filepath).resolve()
    base = config.resolved_output_dir.resolve()
    if not str(resolved_path).startswith(str(base)):
        raise HTTPException(status_code=403, detail="Access denied")
    try:
        resolved_path.write_text(body.content, encoding="utf-8")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return JSONResponse({"status": "ok"})


@app.get("/api/cv")
async def get_cv(download: bool = Query(False, alias="download")):
    """Read the CV from the configured path, optionally as a file download."""
    cv_path = config.resolved_cv_path
    if download:
        if not cv_path.exists():
            raise HTTPException(status_code=404, detail="CV file not found")
        response = FileResponse(path=cv_path, filename=cv_path.name)
        response.headers["Content-Disposition"] = (
            f'attachment; filename="{cv_path.name}"'
        )
        return response
    content = ""
    exists = cv_path.exists()
    if exists:
        try:
            content = cv_path.read_text(encoding="utf-8")
        except Exception:
            pass
    return JSONResponse(
        {
            "content": content,
            "path": str(cv_path),
            "exists": exists,
        }
    )


@app.put("/api/cv")
async def save_cv(body: SaveContent):
    """Save CV content to the configured path."""
    cv_path = config.resolved_cv_path
    try:
        cv_path.parent.mkdir(parents=True, exist_ok=True)
        cv_path.write_text(body.content, encoding="utf-8")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return JSONResponse({"status": "ok", "path": str(cv_path)})


PDF_STYLES = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: "Segoe UI", Roboto, Helvetica, Arial, sans-serif; font-size: 11pt; line-height: 1.5; color: #222; padding: 0; max-width: 100%; }
h1, h2, h3, h4 { color: #111; margin-top: 1em; margin-bottom: 0.4em; }
h1 { font-size: 18pt; border-bottom: 2px solid #333; padding-bottom: 6px; }
h2 { font-size: 15pt; border-bottom: 1px solid #ccc; padding-bottom: 3px; }
h3 { font-size: 13pt; }
p { margin-bottom: 0.6em; }
ul, ol { margin-left: 1.3em; margin-bottom: 0.6em; }
li { margin-bottom: 0.2em; }
pre { background: #f5f5f5; border: 1px solid #ddd; border-radius: 3px; padding: 8px; font-size: 10pt; overflow-x: auto; page-break-inside: avoid; }
code { background: #f0f0f0; border-radius: 2px; padding: 1px 3px; font-size: 10pt; }
pre code { background: none; padding: 0; }
blockquote { border-left: 3px solid #ccc; margin: 0.8em 0; padding: 0.3em 0.8em; color: #555; background: #fafafa; }
table { border-collapse: collapse; width: 100%; margin: 0.8em 0; page-break-inside: avoid; }
th, td { border: 1px solid #ddd; padding: 6px; text-align: left; }
th { background: #f5f5f5; font-weight: 600; }
img { max-width: 100%; height: auto; }
hr { border: none; border-top: 1px solid #ddd; margin: 1em 0; }
@page { margin: 2cm; }
"""


def _markdown_to_pdf_bytes(md_content: str) -> bytes:
    """Convert markdown text to PDF bytes using weasyprint."""
    html_body = markdown.markdown(md_content, extensions=["fenced_code", "tables"])
    html_doc = (
        "<!DOCTYPE html><html lang='es'><head><meta charset='UTF-8'>"
        f"<style>{PDF_STYLES}</style></head><body>{html_body}</body></html>"
    )
    return HTML(string=html_doc).write_pdf()


@app.get("/api/pdf/{filepath:path}")
async def get_pdf(filepath: str):
    """Generate a PDF from a saved markdown file and return as download."""
    resolved_path = (config.resolved_output_dir / filepath).resolve()
    if not resolved_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    base = config.resolved_output_dir.resolve()
    if not str(resolved_path).startswith(str(base)):
        raise HTTPException(status_code=403, detail="Access denied")

    md_content = resolved_path.read_text(encoding="utf-8")
    try:
        pdf_bytes = _markdown_to_pdf_bytes(md_content)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {exc}")

    pdf_name = resolved_path.stem + ".pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{pdf_name}"',
        },
    )


class PdfConvertBody(BaseModel):
    content: str
    filename: str = "document"


@app.post("/api/pdf/convert")
async def convert_pdf(body: PdfConvertBody):
    """Convert raw markdown text to PDF and return as download."""
    try:
        pdf_bytes = _markdown_to_pdf_bytes(body.content)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {exc}")

    pdf_name = body.filename.rstrip(".md") + ".pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{pdf_name}"',
        },
    )


# ── Discovery / Search endpoints ────────────────────────────────────────


@app.post("/api/search")
async def api_search(request: SearchRequest):
    adapter = _get_search_adapter(request.source)
    filters = JobSearchFilters(
        query=request.query,
        num_pages=request.num_pages,
        country=request.country,
        date_posted=request.date_posted,
        remote_only=request.remote_only,
        employment_types=request.employment_types,
        page=request.page,
        source=adapter.source_name,
    )
    try:
        result = await adapter.search(filters)
        saved = store.save_search(result, filters)
        jobs = _rank_jobs(store.list_jobs(search_id=saved["id"], limit=500))
        return JSONResponse(
            {
                "search": saved,
                "jobs": jobs,
                "count": len(jobs),
                "source": adapter.source_name,
            }
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Search failed")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        try:
            await adapter.close()
        except Exception:
            pass


@app.get("/api/searches")
async def api_list_searches(limit: int = Query(50, ge=1, le=200)):
    return JSONResponse({"searches": store.list_searches(limit=limit)})


@app.get("/api/searches/{search_id}")
async def api_get_search(search_id: str):
    rec = store.get_search(search_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Search not found")
    jobs = _rank_jobs(store.list_jobs(search_id=search_id, limit=1000))
    meta = {k: v for k, v in rec.items() if k != "jobs"}
    return JSONResponse({"search": meta, "jobs": jobs, "count": len(jobs)})


@app.get("/api/discovery/jobs")
async def api_list_discovery_jobs(
    search_id: str | None = Query(None),
    decision: str | None = Query(None),
    q: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    if decision and decision not in VALID_DECISIONS:
        raise HTTPException(status_code=400, detail="Invalid decision filter")
    jobs = _rank_jobs(store.list_jobs(search_id=search_id, decision=decision, q=q, limit=limit))
    return JSONResponse({"jobs": jobs, "count": len(jobs)})


@app.post("/api/discovery/decision")
async def api_set_decision(request: DecisionRequest):
    try:
        entry = store.set_decision(request.job_key, request.decision, request.notes)
        return JSONResponse({"ok": True, "entry": entry})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/discovery/job/{job_key:path}")
async def api_get_discovery_job(job_key: str):
    job = store.get_job(job_key)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    cv_tokens = _tokenize(_load_cv_text())
    job = dict(job)
    job["_score"] = _score_job(job, cv_tokens)
    return JSONResponse({"job": job})


@app.post("/api/discovery/generate")
async def api_discovery_generate(request: DiscoveryGenerateRequest):
    if request.skill not in SKILL_NAMES:
        raise HTTPException(status_code=400, detail=f"Unknown skill. Available: {SKILL_NAMES}")
    job = store.get_job(request.job_key)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    job_data = {k: v for k, v in job.items() if not k.startswith("_")}
    run_date = date.today().isoformat()
    folder_name = _discovery_folder_name(job_data, request.job_key)
    filename = SKILL_FILENAMES.get(request.skill, request.skill) + ".md"
    out_path = config.resolved_output_dir / run_date / folder_name / filename

    if not request.force:
        existing = _find_existing_output_path(job_data, request.job_key, request.skill)
        if existing is not None:
            try:
                result = existing.read_text(encoding="utf-8")
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"Failed to read existing output: {exc}")
            rel_existing = existing.relative_to(config.resolved_output_dir)
            return JSONResponse(
                {
                    "content": result,
                    "filepath": str(existing),
                    "rel_path": str(rel_existing),
                    "filename": existing.name,
                    "date": existing.parent.parent.name,
                    "job_key": request.job_key,
                    "skipped": True,
                    "existing_outputs": _existing_outputs_for_job(job_data, request.job_key),
                }
            )

    try:
        result = await _run_skill_for_job(job_data, request.skill)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.exception("Discovery generation failed")
        raise HTTPException(status_code=500, detail=str(exc))

    link = request.link or job_data.get("job_apply_link") or job_data.get("job_google_link") or ""
    job_md_parts = ["# Job Description\n"]
    if link:
        job_md_parts.append(f"\n**Link:** {link}\n")
    job_md_parts.append(f"\n**Title:** {job_data.get('job_title','')}\n")
    job_md_parts.append(f"**Employer:** {job_data.get('employer_name','')}\n")
    job_md_parts.append(f"**Location:** {job_data.get('job_location','')}\n\n")
    job_md_parts.append(job_data.get("job_description") or "")

    try:
        out_path = _save_skill_output(run_date, folder_name, request.skill, result, "".join(job_md_parts))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    rel_path = out_path.relative_to(config.resolved_output_dir)
    return JSONResponse(
        {
            "content": result,
            "filepath": str(out_path),
            "rel_path": str(rel_path),
            "filename": out_path.name,
            "date": run_date,
            "job_key": request.job_key,
            "skipped": False,
            "existing_outputs": _existing_outputs_for_job(job_data, request.job_key),
        }
    )


@app.post("/api/discovery/enrich")
async def api_discovery_enrich(request: DiscoveryEnrichRequest):
    """Fetch full job details on demand and merge into the index (no LLM)."""
    job = store.get_job(request.job_key)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    key = job.get("_job_key") or request.job_key
    src = request.source or job.get("_source") or getattr(config, "job_search_source", "openninja")
    provider_job_id = str(job.get("job_id") or request.job_key)
    if ":" in provider_job_id and provider_job_id.startswith(src + ":"):
        provider_job_id = provider_job_id.split(":", 1)[1]

    try:
        adapter = create_search_adapter(src, config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        details = await adapter.job_details(provider_job_id)
        if not isinstance(details, dict):
            details = {"raw_details": details}
        updated = store.merge_job_details(key, details) or {}
        cv_tokens = _tokenize(_load_cv_text())
        if updated:
            updated = dict(updated)
            updated["_score"] = _score_job(updated, cv_tokens)
        return JSONResponse({"ok": True, "job_key": key, "job": updated})
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Discovery enrich failed")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        try:
            await adapter.close()
        except Exception:
            pass


TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _load_template() -> str:
    """Read the HTML template from the templates directory."""
    tpl_path = TEMPLATE_DIR / "index.html"
    if not tpl_path.exists():
        raise RuntimeError(f"Template not found: {tpl_path}")
    return tpl_path.read_text(encoding="utf-8")
