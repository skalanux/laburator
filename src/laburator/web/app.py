"""Web interface for Laburator.

Start with:
    uvicorn laburator.web.app:app --reload
"""

import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import markdown
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from weasyprint import HTML
from pydantic import BaseModel

from laburator.config import LaburatorConfig
from laburator.llm.service import LLMService
from laburator.skills import (
    SKILL_FILENAMES,
    SKILL_NAMES,
    build_user_messages,
    load_skill,
    response_format,
)


config = LaburatorConfig()


class GenerateRequest(BaseModel):
    job_description: str
    skill: str


class SaveContent(BaseModel):
    content: str


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text)
    return text[:80].rstrip("-")


async def generate_skill(
    job_description: str,
    skill_name: str,
    cv_path: Path,
    wiki_path: Path,
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

    # Build minimal job_data from pasted description
    job_data: dict[str, Any] = {
        "job_id": f"manual-{os.getpid()}-{int(datetime.now().timestamp())}",
        "job_description": job_description,
        "employer_name": "Manual Entry",
        "job_title": "Custom Job Description",
        "job_location": "",
        "job_is_remote": False,
    }

    # Load skill prompt
    try:
        system_prompt = load_skill(skill_name)
    except FileNotFoundError:
        raise FileNotFoundError(f"Skill '{skill_name}' not found")

    # Build messages
    user_messages = build_user_messages(skill_name, job_data, cv_context, wiki_context)

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

    # Save output with unique filename
    run_date = date.today().isoformat()
    now = datetime.now()
    timestamp = now.strftime("%H%M%S")
    base = SKILL_FILENAMES.get(skill_name, skill_name)
    # Short slug from the beginning of the job description (40 chars max)
    desc_slug = _slugify(job_description[:60])[:40]
    filename = f"{base}_{desc_slug}_{timestamp}.md"
    out_dir = config.resolved_output_dir / run_date / "manual"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / filename
    try:
        out_path.write_text(result, encoding="utf-8")
    except Exception as exc:
        raise RuntimeError(f"Failed to save file: {exc}")

    return result, out_path


app = FastAPI(title="Laburator Web UI", version="0.1.0")


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main HTML page."""
    html = _load_template()
    html = html.replace("{{ OUTPUT_DIR }}", str(config.resolved_output_dir))
    return html


@app.post("/generate")
async def generate(request: GenerateRequest):
    """Generate a skill output from a manual job description."""
    try:
        result, filepath = await generate_skill(
            request.job_description,
            request.skill,
            config.resolved_cv_path,
            config.resolved_llmwiki_dir,
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
async def list_outputs(limit: int = Query(20, ge=1, le=100)):
    """List recently generated output files (manual entries)."""
    out_dir = config.resolved_output_dir
    files: list[dict[str, Any]] = []
    # Search for all .md files in any /manual/ subdirectory
    for f in sorted(
        out_dir.rglob("**/manual/*.md"), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        if f.is_file():
            rel = str(f.relative_to(config.resolved_output_dir))
            files.append(
                {
                    "filename": f.name,
                    "rel_path": rel,
                    "full_path": str(f),
                    "size": f.stat().st_size,
                    "modified": f.stat().st_mtime,
                }
            )
            if len(files) >= limit:
                break

    return JSONResponse({"files": files})


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
async def get_cv():
    """Read the CV from the configured path."""
    cv_path = config.resolved_cv_path
    content = ""
    exists = cv_path.exists()
    if exists:
        try:
            content = cv_path.read_text(encoding="utf-8")
        except Exception:
            pass
    return JSONResponse({
        "content": content,
        "path": str(cv_path),
        "exists": exists,
    })


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
    return Response(content=pdf_bytes, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{pdf_name}"',
    })


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
    return Response(content=pdf_bytes, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{pdf_name}"',
    })


TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

def _load_template() -> str:
    """Read the HTML template from the templates directory."""
    tpl_path = TEMPLATE_DIR / "index.html"
    if not tpl_path.exists():
        raise RuntimeError(f"Template not found: {tpl_path}")
    return tpl_path.read_text(encoding="utf-8")
