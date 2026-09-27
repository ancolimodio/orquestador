# Changelog

## [Sin publicar]

### Agregado
- `ContainerSandbox`: gates en contenedores efímeros sin red, con el repo en solo lectura (`--container-image`).
- Guardrail contra tests debilitados (`skip`, `skipif`, `xfail`) y contra sobrescribir tests existentes.

### Corregido
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
