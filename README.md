# Laburator

CLI job-search assistant powered by LangGraph. Fetches job listings from a configurable source, lets you curate proposals, and (only when you explicitly ask) generates tailored outputs (analysis, CV, cover letter, interview Q&A) with an LLM.

## Installation

### From PyPI (recommended)

```bash
# With pip
pip install laburator

# With uv
uv pip install laburator

# Or install as a standalone tool (uv only, isolates dependencies)
uv tool install laburator

# Verify it works
laburator --help
```

### From source (development)

```bash
git clone <repo-url>
cd laburator

# With pip
pip install -e .

# With uv
uv pip install -e .

# Verify it works
laburator --help
```

## Setup

Copy `.env.example` to `.env` and fill in your API keys:

```bash
cp .env.example .env
```

### API Keys & config

#### LLM (for on-demand generation)

Used only when you explicitly run a generation command/action (see “Token control”).

1. Go to **https://opencode.ai/zen** and sign up / create an API key.
2. Copy it into `.env` as `MODEL_API_KEY`.

Example:

```
MODEL_API_KEY=sk-...
MODEL_API_ENDPOINT=https://opencode.ai/zen/v1
MODEL_NAME=deepseek-v4-flash-free
```

#### Job search source

Job search uses a pluggable source (default: `openninja` / jsearch). Configure via env/config:

```
JOB_SEARCH_SOURCE=openninja
JOB_SEARCH_API_KEY=ak_...
```

- `JOB_SEARCH_SOURCE` selects the provider (currently `openninja` is implemented).
- The corresponding provider credentials must be set (e.g. `JOB_SEARCH_API_KEY` for `openninja`).

### CV

Create your own `cv.md` at the project root with your personal experience, skills, and education. It is used as context when you ask to generate tailored outputs.

```bash
cp cv.md.example cv.md   # then edit it with your own data
```

### LLM Wiki (optional)

If you have reference `.md` notes, place them in the configured `LLMWIKI_DIR` (default `~/Proyectos/llmwiki/wiki/`). They are injected as context for generation skills.

### Skills (custom prompts)

Skills are markdown files containing the system prompt for each LLM call. Built-ins ship with the package (e.g. `jobsynthesis`, `createcv`, `presentationletter`, `interviewquestions`, `generarcv`, `generarcvtex`).

To customise, add `~/.config/laburator/skills/<name>.md` with the same filename — it takes precedence over the built-in.

## Token control (important)

- **No automatic AI analysis/generation.** LLM calls happen **only on explicit request** and for the **specific action/skill you select**.
- Searching, listing, deciding, and enriching job details do **not** call the LLM.
- To avoid wasting tokens, existing outputs are **skipped by default** (no reprocessing). Regenerate only when you explicitly ask with `--force` / `--force-process` / `force: true`.

## v2 flow

1. **Search** proposals (fetch + cache/index) — no LLM.
2. **Curate**: review and mark each job as `interested` / `rejected` / `pending` (stored in the index).
3. **Enrich on demand** (optional): fetch full `job_details` for a specific job and merge into the index — no LLM.
4. **Generate on demand** (LLM): run selected skills for selected jobs only (single job or batch of `interested`), skipping existing outputs unless forced.

## CLI usage

```bash
# Configuration (keys masked)
laburator config

# Search and index/cache jobs (fetch-only, no LLM)
laburator search "python developer" \
  --pages 1 --remote --country us \
  --date-posted all --employment-types "" --page 1 \
  --source openninja

# List indexed searches/jobs and legacy cache
laburator list

# Curate a job by job_id or indexed job_key
laburator decide <job_id|job_key> interested
laburator decide <job_id|job_key> rejected
laburator decide <job_id|job_key> pending --notes "optional"

# Enrich a job with full details on demand (no LLM)
laburator enrich <job_id|job_key> --source openninja

# Run a single skill on a specific job (job_id or job_key) — LLM, on demand
laburator run <job_id|job_key> --skill createcv

# Generate tailored CV (md + tex) for a specific proposal — LLM, on demand
laburator generar-cv <job_id|job_key>

# Batch: process ONLY interested jobs, ONLY selected skills — LLM, on demand
# (repeat --skill; existing outputs are skipped unless --force)
laburator process-interested --skill createcv --skill presentationletter
laburator process-interested --skill jobsynthesis --force

# Full pipeline fetch + generate for a query — LLM, on demand (runs configured skills)
# skips existing outputs unless --force-process; re-fetch with --refresh
laburator synth "python developer" --pages 1 --remote --force-process
```

Notes:
- `--source` / `JOB_SEARCH_SOURCE` select the search provider (effective in CLI/pipeline/web).
- `process-interested` and `synth` skip existing outputs by default to avoid token waste; use `--force` / `--force-process` to regenerate.

## Web UI

Start the web server:

```bash
laburator web            # http://localhost:8080
laburator web --port 3000
```

The web UI includes:
- **Buscar propuestas** — search by source/filters, review results, mark `interested` / `rejected` / `pending`, enrich job details on demand, and trigger generation for a selected job+skill **only when you ask** (skips existing output unless forced).
- **Generar** — generate content from a manual job description for a selected skill (on demand).
- **Mi CV** — view/edit `cv.md`.
- **Directorio de salida** — browse generated outputs, view/download/export to PDF.

## Output

Results are saved under `~/.local/share/laburator/output/` (configurable):

```
~/.local/share/laburator/output/
├── index/                          # v2 search index and decisions
│   ├── searches/*.json
│   └── decisions.json
├── cache/                          # legacy-compatible cached listings
│   └── <query-slug>/jobs.json
└── <YYYY-MM-DD>/                   # outputs per run date (on-demand generation)
    └── <company-job>/              # per job
        ├── job.md
        ├── <skill>.md              # generated outputs
        └── ...
```

Generation skips existing outputs by default; regeneration happens only with explicit force.

## Verification

Run `laburator config` to confirm configured source/model/keys (keys partially masked) and paths.
