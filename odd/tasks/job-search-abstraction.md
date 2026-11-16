# Job Search Abstraction (Etapa 1)

Objective: Refactor de la capa de búsqueda de empleo para soportar múltiples fuentes mediante interfaz abstracta, DTOs compartidos y adapter, manteniendo el comportamiento actual con OpenNinja (jsearch).

Branch: v2
Delivery strategy: ask-on-risk

## Specs

- S1. **DTOs compartidos (contrato cliente/adapter)**: definir DTOs para filtros de búsqueda y para resultados/entidades retornadas, usados por la interfaz y las implementaciones. Los filtros deben cubrir al menos: `query`, cantidad de páginas/resultados (`num_pages`), `country`, `date_posted`, `remote_only`, `employment_types`, `page` (compatibles con lo que soporta la búsqueda actual) y campo de fuente/identificación cuando aplique. Los resultados deben exponer jobs normalizados y metadatos de la búsqueda/respuesta de forma común.
- S2. **Cliente abstracto**: `JobSearchClient` pasa a ser una clase abstracta/interfaz que define los métodos necesarios (búsqueda y detalle de job, al menos) tipados con los DTOs de S1, además de ciclo de vida (`close`) y/o identificación de fuente según corresponda. No contiene lógica específica de proveedor.
- S3. **Implementación OpenNinja**: la implementación actual de jsearch se mueve/renombra a `OpenNinjaJobSearchClient` (o nombre equivalente claro) que implementa S2, mapea DTOs de filtros a parámetros de la API openwebninja/jsearch y parsea la respuesta a los DTOs/modelos comunes, preservando el comportamiento actual (endpoints, retries/backoff, timeout, headers, parseo de jobs).
- S4. **Adapter/capa intermedia**: existe una capa adapter/servicio de búsqueda que **recibe un `JobSearchClient` en su inicialización** y expone los comandos de búsqueda necesarios delegando al cliente, usando los DTOs de S1. Es el punto uniforme de uso para pipeline/CLI/web.
- S5. **Wiring y compatibilidad de comportamiento**: `pipeline`, `cli` y configuración quedan integrados con la nueva abstracción (adapter + cliente concreto OpenNinja por defecto/configurado) sin cambiar el comportamiento observable actual de `search/synth/run/list/generar-cv/config` para la fuente OpenNinja (misma API remota, cache, procesamiento y salidas). La clave de API actual (`JOB_SEARCH_API_KEY`) sigue funcionando para OpenNinja.
- S6. **Extensibilidad**: agregar una nueva fuente en el futuro debe requerir principalmente implementar un `JobSearchClient` concreto (y su mapeo DTO↔API) e instanciarlo, sin cambiar el contrato del adapter.

## Tasks

| ID | S# | Route | Status | Commit | Notes |
|---|---|---|---|---|---|
| T1 | S1,S2,S3,S4 | inline | done | 06af505 | DTOs (`JobSearchFilters`, `JobSearchResult`), ABC `JobSearchClient`, `OpenNinjaJobSearchClient`, `JobSearchAdapter` |
| T2 | S5 | inline | done | 06af505 | `JobSearchPipeline` usa adapter (default OpenNinja) preservando flujo fetch/cache/process |
| T3 | S1-S6 | inline | done | 06af505 | `py_compile` ok; import/wiring verificado con `.venv` (`JobSearchAdapter`, source `openninja`) |

## Log

L1. [2026-11-15] User request (verbatim):
> "Perfecto, ahora hacelo por etapas, haciendo commiss pero sin hacer push. Ademas quiero que revises lo de JobSearch porque me gustaria que hagas una capa intermedia con un adaptar, que tenga los comandos nacesarios como interfaz y al iniciar el servicio tengas que mandarlo un JobSearchClient sinedo esto una clase abstracta y el actual siendo el OpenNinjaJobSearchClient,  porque despues me gustaria tener varias fuentas de busquedas, como apify linkedind job search, remoteok, Remotive, We Work Remotely, jobify, o lo que sea y poder simplemente en la busqueda poner qde que fuente quiero y que todo sea igual (interfaz de adapttar con su client dentro, y que simplemenet instanciando al client de cada api, se pueda hacer las busquedas y parsear la información), el adaptater y el jobSeachClient tiene que definirse entre si no solo metodos sino DTOs de como pedir filtros y como retornar las busquedas optenidas."

Contexto: repo en branch `v2`, working tree con `?? proposal.txt` y `?? v2plan.md` (no tocar). Usuario aclaró: git limpio, sin tests (prototipo/uso personal), ejecutar Etapa 1 (abstracción JobSearch) como se propuso, etapas funcionales v2 después.

L2. [2026-11-15] Implementación Etapa 1: agregados `api/dtos.py`, `api/base.py`, `api/openninja.py`, `api/adapter.py`; `api/client.py` convertido a exports/compatibilidad; `api/__init__.py` exporta contrato/DTOs/adapter; `pipeline.py` inyecta/crea `JobSearchAdapter(OpenNinjaJobSearchClient)` y usa `search_jobs`. Verificación: `python3 -m py_compile` sobre archivos tocados y smoke test de import/wiring con `.venv` (OK). Risk: none.
