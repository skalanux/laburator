# v2 Remaining Steps

Objective: completar etapas restantes de `v2plan.md` con foco en uso explícito y bajo consumo de tokens: selección real de fuente, procesamiento batch de interesados (solo a pedido y con skills seleccionadas), enriquecimiento bajo demanda, evitar reprocesos, robustez/progreso y documentación.

Branch: v2
Delivery strategy: ask-on-risk
Test policy: no tests (prototipo). Verificación con `py_compile` y smoke checks CLI/app.

## Specs

- S1. **Selección real de fuente en config/factory del pipeline**: fuente configurable (`job_search_source` en config, default `openninja`) y factory única para construir adapter/client por fuente con validación de credenciales; pipeline, web y CLI deben usarla (CLI `--source` efectivo en pipeline). Sin romper OpenNinja.
- S2. **Procesamiento batch de jobs interesados en CLI (solo a pedido, skills seleccionadas)**: comando explícito para procesar únicamente jobs con decisión `interested` del índice, ejecutando solo las skills indicadas por el usuario (`--skill` repetible/requerido), con `--force` opcional. Ningún análisis/generación LLM automático.
- S3. **Enriquecimiento con `job_details` bajo demanda**: comando/endpoint explícitos para traer y guardar detalles completos del job en el índice (merge en job almacenado). Sin enriquecimiento automático.
- S4. **Evitar reprocesos**: si ya existe salida para job+skill, omitir por defecto (con mensaje) y permitir regenerar solo con `--force`/`force`; aplica a pipeline/batch y endpoints de generación desde job indexado. No ejecutar LLM innecesario.
- S5. **Progreso/robustez**: feedback de progreso en operaciones batch/CLI, mensajes claros ante rate limits/errores, y lecturas tolerantes de índice/cache existentes.
- S6. **Documentación**: actualizar README con flujo v2 (búsqueda/curación/generación explícita), comandos, flags y notas de control de tokens (LLM solo a pedido y por selección).

## Tasks

| ID | S# | Route | Status | Commit | Notes |
|---|---|---|---|---|---|
| T1 | S1 | inline | done | cbac4b8 | `job_search_source`, factory `create_search_adapter`, wiring pipeline/web/CLI; `--source` efectivo |
| T2 | S2,S4 | inline | done | d2de915 | Batch `interested` explícito + skip/force reprocesos (CLI/pipeline/web) |
| T3 | S3 | inline | done | d6da35c | `enrich` CLI y `/api/discovery/enrich` (on-demand, merge en índice, sin LLM) |
| T4 | S5 | inline | done | 6ac3fc2 | Progreso/errores claros (rate limit/timeout) y lecturas de índice tolerantes |
| T5 | S6 | inline | done | c23ae43 | README actualizado: flujo v2, comandos/flags, pestaña web y control de tokens |

## Log

L1. [2026-11-15] User request (verbatim):
> "Quiero que hagas todas, teniendo en cuenta que tengo pocos tokens, por lo que el analisis con IA tiene que ser unicamente a pedido mio y seleccionando que hacer, no se pueden desperfdiciar token de mas"

Contexto: `odd/tasks/v2-job-discovery.md` y `odd/tasks/v2-cli-index-parity.md` completados (branch v2). Continuar con pasos restantes de `v2plan.md` respetando: sin análisis/generación LLM automática, solo bajo demanda y con selección explícita. Sin push, sin tests.

L2. [2026-11-15] T1 (S1): agregado `job_search_source` en config y `create_search_adapter` (factory por fuente, valida credenciales; OpenNinja). Pipeline selecciona adapter por fuente efectiva (`_adapter_for_source`), web usa factory y default de config, CLI muestra fuente en `config`. `--source` en CLI/pipeline aplicado en fetch. Verificación: `py_compile` OK. Commit cbac4b8. Risk: none.

L3. [2026-11-15] T2 (S2,S4): CLI `process-interested` procesa solo jobs `interested` con `--skill` repetible/requerido y `--force` opcional (on-demand, sin LLM automático). Pipeline `process_jobs_node` y `synth` (`--force-process`) omiten salidas existentes por defecto. Web `/api/discovery/generate` acepta `force` y devuelve salida existente sin llamar al LLM salvo `force=true`. Verificación: `py_compile`, `python -m laburator.cli --help` y `process-interested --help` OK. Commit d2de915. Risk: none.

L4. [2026-11-15] T3 (S3): agregado `JobIndexStore.merge_job_details` y enriquecimiento explícito bajo demanda (sin LLM): comando CLI `enrich <job_id|job_key>` y endpoint `POST /api/discovery/enrich` que traen `job_details` del proveedor y guardan/mergean en el índice. Verificación: `py_compile` y `enrich --help` OK. Commit d6da35c. Risk: none.

L5. [2026-11-15] T4 (S5): mensajes de error más claros en CLI/pipeline para rate limits/quotas y timeouts (`_friendly_error`, manejo en batch y enrich) manteniendo progreso en operaciones batch; lecturas de índice/cache tolerantes via `_safe_read_json`. Verificación: `py_compile` OK. Commit 6ac3fc2. Risk: none.

L6. [2026-11-15] T5 (S6): actualizado `README.md` con flujo v2 (buscar/curar/enriquecer/generar bajo demanda), comandos y flags (`search`, `decide`, `enrich`, `process-interested`, `synth --force-process`), pestaña web “Buscar propuestas” y nota explícita de control de tokens (LLM solo a pedido y con selección, sin reprocesos salvo `--force`). Verificación: revisión de contenido. Risk: none.
