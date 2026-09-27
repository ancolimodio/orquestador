# Changelog

## [Sin publicar]

### Agregado
- `prompt-maestro.toml`: el harness sirve para otros stacks. Declara stack y framework de tests (para los prompts), globs de código y de tests (los tests pueden vivir junto al código), archivos protegidos, valores públicos que el detector de secretos ignora y los checks de cada gate.
- El detector de tests debilitados reconoce `skip`, `only`, `todo`, `xit` y `fit` de Jest/Vitest.
- El mapa del repo que ven los agentes respeta `.gitignore` y ya no recorre `node_modules`.
- `ContainerSandbox`: gates en contenedores efímeros sin red, con el repo en solo lectura (`--container-image`).
- Guardrail contra tests debilitados (`skip`, `skipif`, `xfail`) y contra sobrescribir tests existentes.
- `OpenAICompatibleLLM` y `--provider anthropic|gemini|openai` en el CLI: Gemini, OpenAI u Ollama además de Anthropic.
- Los clientes HTTP respetan `Retry-After` ante un 429, para planes con límites por minuto.
- Cada corrida del CLI guarda `report.json`, `events.jsonl` y, si falla, `error.txt` en su propia carpeta (`.prompt-maestro/runs/`).
- `EventLog.phase_durations()`: tiempo total por fase. Es la primera feature que escribieron los propios agentes del harness.

### Cambiado
- `OrchestratorConfig` reemplaza `code_prefixes`, `test_prefixes` y `rules_file` por `profile: ProjectProfile`, y `Workspace.write` recibe una `WritePolicy` en lugar de `allowed_prefixes`.

### Corregido
- El Reviewer recibía los archivos completos en lugar de un diff: en una corrida real, una propiedad basura inyectada en un archivo de 717 líneas pasó la revisión. Ahora recibe el diff unificado contra el repo previo a la tarea.
- El Gate A rechazaba planes correctos que declaraban métodos nuevos: el plan ahora distingue `symbols` de `new_symbols`.
- Un agente que escribía fuera de su carpeta mataba la tarea y dejaba archivos a medio escribir: el ChangeSet se valida completo antes de escribir y el error de alcance vuelve como feedback.
- Los permisos de escritura se podían saltear con `..` (por ejemplo, `src/../AGENTS.md`).
- El presupuesto de reintentos contaba vueltas del loop en lugar de fallos, y escalaba culpando al gate equivocado.
- El Tester recibía errores de lint como si fueran fallas de tests.
- `read_many` propagaba un `ExceptionGroup` en lugar del error de dominio; los archivos no UTF-8 rompían la tarea.
- Falsos positivos de áreas sensibles ("author", "tokenizer") y de símbolos del Gate A (`add` en `address`).
- En Windows, el Sandbox no pasaba `SYSTEMROOT` y los gates en Python fallaban con WinError 10106.
- La documentación de gates, presupuesto y aislamiento no coincidía con el código.

## [0.1.0] - 2026-09-26

### Agregado
- Orchestrator con gates A→D y presupuesto de reintentos por gate.
- Agentes Planner, Implementer, Tester y Reviewer con contratos Pydantic v2.
- Guardrails de comandos, secretos, rutas y áreas sensibles.
- Sandbox async con timeout, salida acotada y entorno mínimo.
- Cliente async de Anthropic con reintentos y rate limiting.
- Eventos estructurados en JSONL y métricas de rechazos.
- Demo end-to-end sin API key y CI con lint, tipos, tests, seguridad y demo.
