# v2 UI Polish & Config

Objective: mejorar UX/config de v2 (enrich/force en web, feedback, visibilidad de salidas existentes) sin usar LLM automáticamente.

Branch: v2
Delivery strategy: ask-on-risk
Test policy: no tests (prototipo). Verificación con `py_compile`.

## Specs

- S1. Exponer en la web enriquecimiento on-demand y `force` al generar desde índice (botones/flags claros), usando endpoints existentes.
- S2. Más feedback en la UI al generar/enriquecer (procesando/éxito/salteado/error-rate limit).
- S3. Agregar `JOB_SEARCH_SOURCE` en `.env.example`.
- S4. Visibilidad de salidas ya generadas por job+skill (hoy) en la UI para evitar reprocesos accidentales.

## Tasks

| ID | S# | Route | Status | Commit | Notes |
|---|---|---|---|---|---|
| T1 | S1-S4 | inline | done | 1f0aa48 | template + backend `_existing_outputs` + `.env.example` |

## Log

L1. [2026-11-16] User request (verbatim):
> "si, hace esas 4 y despues arma un plan para sumar otros proveedores de job search client gratuitos o con buen limite gratis para usar teniendo en cuenta que no quiero gastar plata en busuqedas"

L2. [2026-11-16] T1 (S1-S4): web “Buscar propuestas” muestra salidas existentes por job/skill (hoy), botón “Enriquecer” (on-demand, sin LLM), checkbox “Forzar regenerar” y estados de feedback (procesando, reutilizado sin tokens, generado, error/rate limit). Backend anota `_existing_outputs` y `/api/discovery/generate` devuelve `skipped`/`existing_outputs`. `.env.example` incluye `JOB_SEARCH_SOURCE=openninja`. Verificación: `py_compile src/laburator/web/app.py` OK. Risk: none.