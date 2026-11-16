# Plan: Proveedores gratuitos / free-tier para Job Search

Objetivo: sumar más proveedores de búsqueda de empleo **sin gastar plata**, reutilizando la abstracción actual (`JobSearchClient` + `JobSearchFilters`/`JobSearchResult` + factory en `src/laburator/api/__init__.py`).

> Nota: límites, requisitos de registro y términos pueden cambiar. Verificar en la doc oficial de cada proveedor antes de implementar/habilitar. Priorizar opciones sin tarjeta y con cuota gratuita suficiente para uso personal.

## Estado actual

- Fuente implementada: `openninja` (`OpenNinjaJobSearchClient`, jsearch en OpenWebNinja).
- Selección: `JOB_SEARCH_SOURCE` en config y `--source` en CLI/web; `create_search_adapter` construye el adapter y valida credenciales.
- Contrato: implementar `source_name`, `search(filters) -> JobSearchResult`, `job_details(job_id) -> dict`, `close()`.

## Candidatos (prioridad sugerida para “$0”)

| Proveedor | Tipo | Registro / costo | Cuota/limitaciones típicas (verificar) | Cobertura | Notas de integración |
|---|---|---|---|---|---|
| **Arbeitnow** (`arbeitnow`) | API pública de jobs | Sin API key (según doc pública) | Rate limit moderado; datos de empleos tech/remotos mayormente EU/global | Tech/remote | Muy simple (GET JSON). Mapear `title`, `company_name`, `location`, `remote`, `description`, `url/slug`, `tags`. `job_details` puede resolverse desde el mismo payload/cache si no hay endpoint dedicado. Bueno para arrancar sin credenciales. |
| **Remotive** (`remotive`) | API pública remota | Sin API key | Límite de requests razonable; orientado a remoto | Remote | Endpoint público de jobs remotos con búsqueda/categoría. Mapear campos remotos/empresa/URL/descripción. Respetar atribución/términos si aplican. |
| **The Muse** (`themuse`) | API pública | Registro para app id/key (free) | Cuota free (requests/día) suficiente para uso personal | General (empresas grandes) | Soporta búsqueda y paginación; campos ricos (company, locations, levels). `job_details` via endpoint de job. Requiere `THE_MUSE_API_KEY` (o app id según doc vigente). |
| **Adzuna** (`adzuna`) | API comercial con free tier | Registro app_id/app_key (free) | Cuota mensual free para desarrolladores (verificar país/endpoints) | Amplia por país | Buena cobertura y filtros por país/salario; requiere `ADZUNA_APP_ID` y `ADZUNA_APP_KEY`. Mapear país (`country` ya existe en filtros), página, resultados y descripción. |
| **USAJobs** (`usajobs`) | API gubernamental | API key gratuita | Uso gratuito para búsquedas públicas (verificar límites) | Empleos gobierno EE.UU. | Muy útil si se busca en `country=us` y roles públicos; requiere `USAJOBS_API_KEY` + `User-Agent` con email según términos. Campos/estructura distintos (PositionTitle, OrganizationName, etc.). |
| **Jooble** (`jooble`) | Agregador con free tier | API key | Requests limitados en plan free (verificar) | Amplia | JSON POST simple; puede complementar cobertura. Evaluar calidad/duplicados vs fuentes ya indexadas. |

**Descartados o de menor prioridad para “sin plata”:** proveedores que exigen suscripción paga, tarjeta obligatoria, o cuotas free demasiado bajas/inestables para el flujo (re-evaluar si cambian términos). JSearch/OpenWebNinja se mantiene como `openninja` (ya soportado).

## Diseño de integración (patrón existente)

Para cada proveedor `X`:

1. **Cliente** `src/laburator/api/<x>.py`
   - Clase `XJobSearchClient(JobSearchClient)`.
   - `source_name = "x"`.
   - `search(filters: JobSearchFilters)`: traducir `query`, `country`, `num_pages`/`page`, `remote_only`, `date_posted`, `employment_types` a parámetros soportados (ignorar/ajustar los no soportados, documentar mapeo).
   - Normalizar respuesta a `JobSearchResult` con `JobData` (mínimo: `job_id`, `job_title`, `employer_name`, `job_description`, `job_location`, `job_is_remote`, `job_apply_link`/`job_google_link`, `job_posted_at` cuando exista).
   - `job_details(job_id)`: endpoint de detalle si existe; si no, devolver el registro conocido/mergeable.
   - Manejo de rate limit/errores con mensajes claros (alineado a T4) y sin reintentos que quemen cuota.

2. **Config/credenciales** `src/laburator/config.py` y `.env.example`
   - Agregar claves necesarias (`*_API_KEY`, `*_APP_ID`, etc.) como opcionales; solo requeridas cuando `JOB_SEARCH_SOURCE=x`.
   - No romper defaults.

3. **Factory** `src/laburator/api/__init__.py`
   - Registrar `x` en `create_search_adapter` con validación de credenciales específicas.
   - Exportar el cliente.

4. **UI/CLI**
   - Agregar opción en `web/templates/index.html` (`#searchSource`) y aceptar `--source x` (ya soportado).
   - Actualizar `README` (tabla de fuentes, variables, límites conocidos) con nota de verificación de términos.

5. **Calidad de datos / dedupe (opcional, recomendado al sumar 2+ fuentes)**
   - `job_key` ya es estable por `source:job_id` (o hash); mantener `source` correcto para evitar colisiones.
   - Considerar deduplicación suave en UI (mismo título+empresa+link) solo como ayuda visual, sin perder registros.

## Orden de implementación recomendado

1. **Arbeitnow** o **Remotive**: sin credenciales, bajo riesgo, valida el flujo multi-fuente rápido.
2. **The Muse**: free con key, buena estructura y `job_details`.
3. **Adzuna**: amplia cobertura por país, requiere id+key y mapeo de país/paginación.
4. **USAJobs** (si aplica al perfil/usos en EE.UU.): key free pero con requisitos de headers/términos.

## Criterios de aceptación por proveedor

- `search` devuelve `JobSearchResult` normalizado y se indexa en `output_dir/index/searches` con `source` correcto.
- `list`/`decide`/`enrich`/`run` funcionan por `job_key` del nuevo source.
- `enrich` usa `job_details` del source correcto (sin LLM).
- Sin llamadas LLM automáticas; sin reprocesos salvo `--force`.
- Errores de cuota/auth informativos (no silenciosos).
- README y `.env.example` actualizados con variables y nota de “verificar límites/terminos”.

## Riesgos y mitigaciones

- **Cambios de cuota/términos**: documentar “verificar antes de usar”, no hardcodear supuestos como contrato.
- **Campos faltantes**: rellenar con valores seguros/vacíos en `JobData`; no inventar datos.
- **Rate limits**: respetar límites, backoff moderado solo cuando sea seguro, y mensajes claros al usuario.
- **Sesgo de cobertura** (remoto/tech/país): ofrecer múltiples fuentes seleccionables y dejar claro el alcance de cada una.

## Trabajo fuera de alcance (para no gastar recursos)

- Scraping de sitios sin API oficial (riesgo legal/técnico y mantenimiento alto).
- Enriquecimiento o ranking con LLM automático (prohibido por la restricción de tokens; seguir siendo on-demand y seleccionado).
- Suscripciones o proxies pagos.
