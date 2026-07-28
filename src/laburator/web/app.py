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

    return JSONResponse(
        {
            "content": result,
            "filepath": str(filepath),
            "filename": filepath.name,
            "date": filepath.parent.parent.name,
        }
    )


@app.get("/api/outputs")
async def list_outputs(limit: int = Query(20, ge=1, le=100)):
    """List recently generated output files (manual entries)."""
    out_dir = config.resolved_output_dir / "manual"
    if not out_dir.exists():
        return JSONResponse({"files": []})

    files: list[dict[str, Any]] = []
    for f in sorted(out_dir.rglob("*"), key=lambda p: p.stat().st_mtime, reverse=True):
        if f.is_file() and f.suffix == ".md":
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


@app.get("/api/file/{filepath:path}", response_class=FileResponse)
async def serve_file(filepath: str, download: bool = Query(False)):
    """Serve a file from the output directory with path safety."""
    # Resolve relative to output_dir to prevent path traversal
    resolved_path = (config.resolved_output_dir / filepath).resolve()
    if not resolved_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    base = config.resolved_output_dir.resolve()
    if not str(resolved_path).startswith(str(base)):
        raise HTTPException(status_code=403, detail="Access denied")

    return FileResponse(
        resolved_path, as_attachment=download, filename=resolved_path.name
    )


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
        .output { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 6px; padding: 20px; margin-top: 16px; min-height: 100px; max-height: 500px; overflow-y: auto; white-space: pre-wrap; font-family: inherit; }
        .loading { color: #6b7280; font-style: italic; }
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
        <div id="result" class="output" style="display:none;"></div>
        <div id="fileLink" style="display:none; margin-top: 12px;">
            <a id="downloadLink" class="btn-secondary" target="_blank">Ver/Descargar archivo</a>
            <button onclick="copyPath()" class="btn-secondary">Copiar ruta</button>
            <div id="copied" style="display:none; margin-top: 4px; color: green; font-size: 13px;">¡Copiado!</div>
        </div>
    </div>

    <div class="card">
        <h2>Directorio de salida</h2>
        <div class="path" id="outputDirPath"></div>
        <ul class="file-list" id="fileList"></ul>
    </div>

    <script>
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
            const result = document.getElementById('result');
            const fileLink = document.getElementById('fileLink');

            btn.disabled = true;
            loading.style.display = 'block';
            result.style.display = 'none';
            fileLink.style.display = 'none';

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
                result.textContent = data.content;
                result.style.display = 'block';
                
                const link = document.getElementById('downloadLink');
                link.href = '/api/file/' + encodeURIComponent(data.rel_path) + '?download=true';
                fileLink.style.display = 'block';
                document.getElementById('copied').style.display = 'none';
            } catch (e) {
                alert('Error: ' + e.message);
                console.error(e);
            } finally {
                btn.disabled = false;
                loading.style.display = 'none';
            }
        }

        async function refreshFiles() {
            const res = await fetch('/api/outputs?limit=30');
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
                    </div>
                `;
                list.appendChild(li);
            });
        }

        function copyPath() {
            const path = document.getElementById('outputDirPath').textContent;
            navigator.clipboard.writeText(path).then(() => {
                document.getElementById('copied').style.display = 'block';
                setTimeout(() => {
                    document.getElementById('copied').style.display = 'none';
                }, 2000);
            });
        }

        window.addEventListener('load', () => {
            refreshFiles();
            setInterval(refreshFiles, 5000);
        });
    </script>
</body>
</html>
"""
