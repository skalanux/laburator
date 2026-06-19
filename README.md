# Laburator

CLI job-search assistant powered by LangGraph. Fetches job listings via API, processes them through an LLM, and generates tailored CVs, cover letters, and interview questions for each position.

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

### API Keys

Laburator requires two API keys. Both go in the `.env` file.

#### OpenCode Zen (LLM)

Used to generate CVs, cover letters, analysis, etc.

1. Go to **https://opencode.ai/zen** and sign up.
2. Once logged in, create an API key from the dashboard.
3. Copy it into `.env` as `MODEL_API_KEY`.

The endpoint and model are preconfigured in `.env.example`:

```
MODEL_API_KEY=sk-...
MODEL_API_ENDPOINT=https://opencode.ai/zen/v1
MODEL_NAME=deepseek-v4-flash-free
```

#### jsearch API (job search)

Used to fetch live job listings.

1. Go to **https://openwebninja.com** and sign up.
2. Subscribe to the **jsearch API** plan (free tier: 200 requests/month).
3. Copy your API key into `.env` as `JOB_SEARCH_API_KEY`:

```
JOB_SEARCH_API_KEY=ak_...
```

### CV

Create your own `cv.md` at the project root with your personal experience, skills, and education. This is used by the LLM to tailor CVs, cover letters, and interview questions for each job.

A sample is provided as reference:

```bash
cp cv.md.example cv.md   # then edit it with your own data
```

The CV supports any markdown content — sections like Summary, Experience, Education, Skills, and Languages are all parsed and passed as context to every skill.

### LLM Wiki (optional)

If you have reference knowledge — technical notes, company background, industry research — you can place `.md` files in `~/Proyectos/llmwiki/wiki/` (or the path configured as `LLMWIKI_DIR` in `.env`). These files are loaded and injected into every skill prompt as supplementary context, giving the LLM more relevant background to work with.

### Skills (custom prompts)

Skills are **markdown files** containing the system prompt for each LLM call. Built-in skills ship with the package:

| File | Generates |
|------|-----------|
| `jobsynthesis.md` | Job analysis |
| `createcv.md` | Tailored CV |
| `presentationletter.md` | Cover letter |
| `interviewquestions.md` | Interview Q&A |

To customise a skill, create `~/.config/laburator/skills/<name>.md` with the same filename — it will take precedence over the built-in. For example:

```bash
mkdir -p ~/.config/laburator/skills
# Override the CV prompt
cp src/laburator/skills/createcv.md ~/.config/laburator/skills/
# Now edit ~/.config/laburator/skills/createcv.md to your liking
```

Every skill call receives the job listing (JSON), your CV, and the wiki knowledge base as context automatically.

## Output

Results are saved under `~/.local/share/laburator/output/`, organized by date:

```
~/.local/share/laburator/output/
├── cache/                          # cached job listings (JSON)
│   └── python-developer/
│       └── jobs.json
└── 2026-06-19/                     # one folder per run date
    ├── ait-python-developer/       # one folder per job
    │   ├── job.md                  #   job analysis (skills, summary)
    │   ├── cv.md                   #   tailored CV
    │   ├── presentation.md         #   cover letter
    │   └── interview.md            #   interview questions
    ├── randstad-usa-senior-full-stack-python-developer/
    │   └── ...
    └── ...
```

Files appear in real time as each skill completes — you don't have to wait for the full pipeline to finish.

## Usage

```bash
# Search for jobs (fetch-only, no LLM calls)
laburator search "python developer" --pages 1 --remote

# Run the full pipeline: fetch + LLM analysis + output files
laburator synth "python developer" --pages 1 --remote

# List cached searches
laburator list

# Run a single skill on a cached job
laburator run <job-id> --skill createcv

# Show configuration
laburator config
```

## Verification

Run `laburator config` to confirm both keys are loaded (they'll be partially masked in the output).
