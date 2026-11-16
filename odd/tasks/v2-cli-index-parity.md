# v2 CLI & Index Parity

Objective: integrar el índice de búsquedas/decisiones (store) con la CLI y exponer filtros/fuentes de forma coherente, manteniendo compatibilidad con cache legacy.

Branch: v2
Delivery strategy: ask-on-risk

## Specs

- S1. **Lectura unificada de jobs (CLI)**: `list` y `run` (y búsqueda interna de jobs) deben poder encontrar jobs desde `output_dir/index/searches` además del cache legacy `cache/*/jobs.json`, sin romper el comportamiento actual.
- S2. **Búsquedas CLI persistidas en índice + filtros**: `search` (y `synth` en modo fetch) debe guardar resultados también en el índice (`JobIndexStore`) y aceptar/exponer filtros soportados (`date_posted`, `employment_types`, `page`/páginas, `remote_only`, `country`, `source` cuando aplique) pasando por la abstracción de búsqueda.
- S3. **Curación/generación desde índice en CLI**: comandos para marcar decisión por job (`interested|rejected|pending`) y para generar/ejecutar skills sobre un job indexado por `job_key` (compatibilidad con `job_id` legacy). Mantener salida en estructura existente.

## Tasks

| ID | S# | Route | Status | Commit | Notes |
|---|---|---|---|---|---|
| T1 | S1 | inline | done | 77fe192 | `list` muestra índice+cache legacy; `run`/búsqueda resuelven por `_job_key` o `job_id` en índice y cache |
| T2 | S2 | inline | done | 7877a3d | `search`/`synth` aceptan filtros extra y pipeline guarda resultados en índice además del cache legacy |
| T3 | S3 | inline | done | 7877a3d | `decide` marca decisión en índice; `run` ejecuta skills por `job_key`/`job_id` indexado (T1) |

## Log

L1. [2026-11-15] User request (verbatim):
> "si, segui"

Contexto: T1–T5 de `odd/tasks/v2-job-discovery.md` completados (commits hasta 7e70276, branch v2). Continuar con etapas restantes de `v2plan.md` (selección de fuente en config/CLI, paridad CLI, robustez). Sin push, sin tests. Se prioriza paridad CLI + integración con índice.

L2. [2026-11-15] T1 (S1): `cli.py` usa `JobIndexStore` para listar búsquedas/jobs indexados y resolver jobs por `job_key` o `job_id` (`_find_job`) con fallback a cache legacy; `run` acepta key/index, `list` muestra índice y cache legacy. Verificación: `py_compile` OK. Commit 77fe192. Risk: none.

L3. [2026-11-15] T2 (S2): pipeline guarda `JobSearchResult`/filtros y persiste en `JobIndexStore` además del cache legacy; `fetch_jobs_node` pasa `date_posted`, `employment_types`, `page`, `source`. CLI `search` y `synth` exponen `--date-posted`, `--employment-types`, `--page`, `--source`. Verificación: `py_compile` y `python -m laburator.cli --help` OK. Commit 7877a3d. (Fuente configurable en CLI pero pipeline usa adapter OpenNinja por defecto.) Risk: none.

L4. [2026-11-15] T3 (S3): agregado comando `decide <job_id|job_key> <pending|interested|rejected>` para curación en índice; `run` ya ejecuta skills sobre jobs indexados por key/id (compatibilidad legacy). Verificación: `py_compile` y `python -m laburator.cli decide --help` OK. Commit 7877a3d (incluido junto con T2 por race de git/amend). Risk: none.
