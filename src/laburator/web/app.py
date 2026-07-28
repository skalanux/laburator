"""Web interface for Laburator.

Start with:
    uvicorn laburator.web.app:app --reload
"""

import asyncio
import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import markdown
from fastapi import FastAPI, Form, HTTPException, Query, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
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

    # Save output
    run_date = date.today().isoformat()
    filename = SKILL_FILENAMES.get(skill_name, skill_name) + ".md"
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
    html = HTML_TEMPLATE
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
    for f in sorted(out_dir.rglob("**/manual/*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
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
        response.headers["Content-Disposition"] = f'attachment; filename="{resolved_path.name}"'
    else:
        response.headers["Content-Disposition"] = f'inline; filename="{resolved_path.name}"'
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


@app.get("/api/render/{filepath:path}", response_class=HTMLResponse)
async def render_pdf(filepath: str):
    """Render a markdown file as HTML for print/PDF view."""
    resolved_path = (config.resolved_output_dir / filepath).resolve()
    if not resolved_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    base = config.resolved_output_dir.resolve()
    if not str(resolved_path).startswith(str(base)):
        raise HTTPException(status_code=403, detail="Access denied")

    md_content = resolved_path.read_text(encoding="utf-8")
    html_body = markdown.markdown(md_content, extensions=["fenced_code", "tables"])

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Laburator - PDF View</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: "Segoe UI", Roboto, Helvetica, Arial, sans-serif; font-size: 13pt; line-height: 1.6; color: #222; padding: 2cm; max-width: 900px; margin: 0 auto; }}
  h1, h2, h3, h4 {{ color: #111; margin-top: 1.2em; margin-bottom: 0.5em; }}
  h1 {{ font-size: 22pt; border-bottom: 2px solid #333; padding-bottom: 8px; }}
  h2 {{ font-size: 18pt; border-bottom: 1px solid #ccc; padding-bottom: 4px; }}
  p {{ margin-bottom: 0.8em; }}
  ul, ol {{ margin-left: 1.5em; margin-bottom: 0.8em; }}
  li {{ margin-bottom: 0.3em; }}
  pre {{ background: #f5f5f5; border: 1px solid #ddd; border-radius: 4px; padding: 12px; overflow-x: auto; font-size: 11pt; }}
  code {{ background: #f0f0f0; border-radius: 3px; padding: 1px 4px; font-size: 11pt; }}
  blockquote {{ border-left: 4px solid #ccc; margin: 1em 0; padding: 0.5em 1em; color: #555; background: #fafafa; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
  th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
  th {{ background: #f5f5f5; font-weight: 600; }}
  .no-print {{ display: none; }}
  @media print {{
    body {{ padding: 0; }}
    @page {{ margin: 2cm; }}
  }}
  .print-btn {{ position: fixed; top: 20px; right: 20px; background: #2563eb; color: white; border: none; padding: 12px 24px; border-radius: 6px; font-size: 16px; cursor: pointer; font-weight: 600; z-index: 1000; }}
  .print-btn:hover {{ background: #1d4ed8; }}
  @media print {{ .print-btn {{ display: none; }} }}
</style>
</head>
<body>
<button class="print-btn" onclick="window.print()">🖨️ Generar PDF</button>
{html_body}
<script>setTimeout(() => window.print(), 500);</script>
</body>
</html>"""


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Laburator - Generador Rápido</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #333; max-width: 1200px; margin: 0 auto; padding: 20px; background: #f5f5f5; }
        h1, h2, h3 { margin: 0.5em 0; color: #1a1a1a; }
        .card { background: white; border-radius: 8px; padding: 24px; margin-bottom: 24px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
        label { display: block; margin: 12px 0 4px; font-weight: 600; }
        textarea, select { width: 100%; padding: 12px; border: 1px solid #ddd; border-radius: 6px; font-size: 14px; font-family: inherit; }
        textarea { min-height: 300px; resize: vertical; }
        button { background: #2563eb; color: white; border: none; padding: 12px 24px; border-radius: 6px; font-size: 16px; cursor: pointer; font-weight: 600; transition: background 0.2s; }
        button:hover { background: #1d4ed8; }
        button:disabled { background: #9ca3af; cursor: not-allowed; }
        .output-editor { font-family: "SF Mono", "Fira Code", "Consolas", monospace; background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 6px; padding: 20px; margin-top: 16px; min-height: 300px; max-height: 600px; font-size: 14px; line-height: 1.5; }
        .action-bar { margin-top: 12px; display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
        .loading { color: #6b7280; font-style: italic; }
        .success-msg { color: #16a34a; font-size: 13px; margin-left: 8px; }
        .badge { display: inline-block; background: #e5e7eb; color: #374151; border-radius: 4px; padding: 2px 8px; font-size: 12px; margin-left: 8px; }
        .btn-save { background: #16a34a; color: white; border: none; padding: 10px 20px; border-radius: 6px; font-size: 14px; cursor: pointer; font-weight: 600; }
        .btn-save:hover { background: #15803d; }
        .btn-pdf { background: #dc2626; color: white; border: none; padding: 10px 20px; border-radius: 6px; font-size: 14px; cursor: pointer; font-weight: 600; }
        .btn-pdf:hover { background: #b91c1c; }
        .file-list { list-style: none; }
        .file-item { display: flex; justify-content: space-between; align-items: center; padding: 12px; border: 1px solid #e5e7eb; border-radius: 6px; margin-bottom: 8px; background: white; }
        .file-item a { color: #2563eb; text-decoration: none; font-weight: 600; }
        .file-item a:hover { text-decoration: underline; }
        .btn-secondary { background: #6b7280; color: white; border: none; padding: 8px 16px; border-radius: 4px; font-size: 14px; cursor: pointer; margin-left: 8px; }
        .btn-secondary:hover { background: #4b5563; }
        .path { font-size: 12px; color: #6b7280; background: #f3f4f6; padding: 8px; border-radius: 4px; word-break: break-all; margin-bottom: 16px; font-family: monospace; }
    </style>
</head>
<body>
    <h1>🛠️ Laburator — Entradas Manuales</h1>
    <p>Pega un <strong>job description</strong> y aplica una skill para generar contenido.</p>

    <div class="card">
        <h2>Generar</h2>
        <label for="jobDescription">Job Description (todo el texto)</label>
        <textarea id="jobDescription" placeholder="Pega aquí el completo job description..."></textarea>

        <label for="skill">Skill a aplicar</label>
        <select id="skill">
            <option value="jobsynthesis">📋 Análisis de puesto</option>
            <option value="generarcv">📄 CV personalizado con análisis ats</option>
            <option value="presentationletter">✉️ Carta de presentación</option>
            <option value="interviewquestions">❓ Preguntas de entrevista</option>
        </select>

        <button id="generateBtn" onclick="generate()">Generar</button>
        <div id="loading" class="loading" style="display:none; margin-top: 12px;">Generando...</div>
        <div id="resultArea" style="display:none;">
            <label for="result">Resultado <span id="fileBadge" class="badge"></span></label>
            <textarea id="result" class="output-editor" placeholder="El contenido generado aparecerá aquí..."></textarea>
            <div class="action-bar">
                <button id="saveBtn" class="btn-save" onclick="saveFile()">💾 Guardar</button>
                <button id="pdfBtn" class="btn-pdf" onclick="openPdf()">📄 PDF</button>
                <a id="downloadLink" class="btn-secondary" target="_blank">Ver archivo</a>
                <button onclick="copyPath()" class="btn-secondary">Copiar ruta</button>
                <span id="saveStatus" class="success-msg" style="display:none;"></span>
                <div id="copied" style="display:none; margin-left: 8px; color: #16a34a; font-size: 13px;">¡Copiado!</div>
            </div>
        </div>
    </div>

    <div class="card">
        <h2>Directorio de salida</h2>
        <div class="path" id="outputDirPath"></div>
        <ul class="file-list" id="fileList"></ul>
    </div>

    <script>
        let currentRelPath = null;

        document.getElementById('outputDirPath').textContent = "{{ OUTPUT_DIR }}";

        async function generate() {
            const jobDesc = document.getElementById('jobDescription').value.trim();
            const skill = document.getElementById('skill').value;

            if (!jobDesc) {
                alert("Por favor, pega un job description.");
                return;
            }

            const btn = document.getElementById('generateBtn');
            const loading = document.getElementById('loading');
            const resultArea = document.getElementById('resultArea');
            const result = document.getElementById('result');

            btn.disabled = true;
            loading.style.display = 'block';
            resultArea.style.display = 'none';

            try {
                const res = await fetch('/generate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ job_description: jobDesc, skill: skill })
                });

                if (!res.ok) {
                    const err = await res.text();
                    throw new Error('Error: ' + err);
                }

                const data = await res.json();
                currentRelPath = data.rel_path;

                result.value = data.content;
                resultArea.style.display = 'block';

                document.getElementById('fileBadge').textContent = data.filename;

                const link = document.getElementById('downloadLink');
                link.href = '/api/file/' + encodeURIComponent(data.rel_path);

                clearStatus();
            } catch (e) {
                alert('Error: ' + e.message);
                console.error(e);
            } finally {
                btn.disabled = false;
                loading.style.display = 'none';
            }
        }

        async function saveFile() {
            if (!currentRelPath) return;
            const content = document.getElementById('result').value;
            const status = document.getElementById('saveStatus');

            try {
                const res = await fetch('/api/file/' + encodeURIComponent(currentRelPath), {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content: content })
                });
                if (!res.ok) {
                    const err = await res.text();
                    throw new Error(err);
                }
                status.textContent = '✅ Guardado!';
                status.style.display = 'inline';
                setTimeout(() => { status.style.display = 'none'; }, 3000);
            } catch (e) {
                alert('Error al guardar: ' + e.message);
            }
        }

        function openPdf() {
            if (!currentRelPath) return;
            window.open('/api/render/' + encodeURIComponent(currentRelPath), '_blank');
        }

        async function refreshFiles() {
            const res = await fetch('/api/outputs?limit=50');
            const data = await res.json();
            const list = document.getElementById('fileList');
            list.innerHTML = '';

            if (!data.files || data.files.length === 0) {
                list.innerHTML = '<li style="color:#6b7280; padding:12px;">No hay archivos generados aún.</li>';
                return;
            }

            data.files.forEach(file => {
                const li = document.createElement('li');
                li.className = 'file-item';
                const relPath = file.rel_path.split('/').pop();
                const dateStr = new Date(file.modified * 1000).toLocaleDateString();
                li.innerHTML = `
                    <div>
                        <strong>${relPath}</strong> • ${dateStr} • ${file.size} bytes
                    </div>
                    <div>
                        <a href="/api/file/${encodeURIComponent(file.rel_path)}" target="_blank">Ver</a>
                        <a href="/api/file/${encodeURIComponent(file.rel_path)}?download=true" class="btn-secondary">↓</a>
                        <a href="/api/render/${encodeURIComponent(file.rel_path)}" class="btn-secondary" target="_blank">📄</a>
                    </div>
                `;
                list.appendChild(li);
            });
        }

        function copyPath() {
            const path = document.getElementById('outputDirPath').textContent;
            navigator.clipboard.writeText(path).then(() => {
                document.getElementById('copied').style.display = 'inline';
                setTimeout(() => {
                    document.getElementById('copied').style.display = 'none';
                }, 2000);
            });
        }

        function clearStatus() {
            document.getElementById('saveStatus').style.display = 'none';
        }

        window.addEventListener('load', () => {
            refreshFiles();
            setInterval(refreshFiles, 5000);
        });
    </script>
</body>
</html>
"""
