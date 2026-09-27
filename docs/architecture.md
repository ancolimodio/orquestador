# Arquitectura

Prompt Maestro separa **quién decide** (los agentes, apoyados en un modelo) de **quién controla** (el harness, código determinista). Ninguna decisión del modelo se ejecuta sin pasar antes por una verificación en código.

## Componentes

| Componente | Módulo | Responsabilidad | Depende de |
|---|---|---|---|
| Orchestrator | `orchestrator.py` | Conduce la tarea por los gates A→D, controla el presupuesto y decide cuándo escalar | Agentes, Workspace, GateRunner, EventLog |
| Agentes | `agents/` | Un rol cada uno: planificar, implementar, testear, revisar | LLMClient, prompts, models |
| Contratos | `models.py` | Esquemas Pydantic de cada handoff y de los resultados | — |
| Workspace | `workspace.py` | Lectura y escritura confinada al repo; valida el plan (Gate A) | guardrails |
| Sandbox | `sandbox.py` | Ejecuta comandos con timeout, salida acotada y entorno mínimo | guardrails |
| GateRunner | `gates.py` | Agrupa checks por gate y los corre en paralelo | Sandbox |
| Guardrails | `guardrails.py` | Reglas deterministas: comandos, secretos, rutas, áreas sensibles | — |
| LLMClient | `llm.py` | Protocol del modelo + cliente async de Anthropic | httpx |
| EventLog | `observability.py` | Eventos estructurados y métricas por tarea | — |

## Flujo de una tarea

1. El Orchestrator carga `AGENTS.md` y lo inyecta en el system prompt de los cuatro agentes.
2. **Planner → Gate A.** El plan se valida contra el repo: los archivos a modificar existen, los que se crean no existen y los símbolos aparecen en el código. Si falla, el Planner recibe la lista exacta de errores.
3. **Reglas de escalamiento.** Antes de escribir una línea, el harness escala si hay preguntas abiertas, borrados de archivos o áreas sensibles (auth, pagos, credenciales, datos personales).
4. **Implementer → Gate B.** El código se escribe solo en `src/`, pasando por los guardrails. Luego corren `ruff` y `mypy --strict` en paralelo.
5. **Tester → Gate C.** Un agente distinto escribe los tests en `tests/` y corre `pytest`. Separar autor y tester evita que el mismo agente "acomode" los tests a su propio código.
6. **Reviewer → Gate D.** `bandit` corre primero y su resultado se le pasa al Reviewer, que revisa el diff contra el plan. La tarea se aprueba solo si ambos pasan y no hay hallazgos `blocker` o `major`.
7. Cualquier falla en B, C o D vuelve al Implementer con el feedback completo; las fallas de C y D también le llegan al Tester, porque el error puede estar en el test. Cada gate tiene un presupuesto de 3 fallos propios; al agotarse, la tarea se escala con el historial.

## Modelo de seguridad

El harness asume que el modelo **puede equivocarse o ser manipulado** (por ejemplo, con instrucciones escondidas en el código que lee). Por eso los controles no dependen de su obediencia:

- **Confinamiento de rutas:** toda ruta se resuelve y se verifica que quede dentro del repo.
- **Permisos por rol:** el Implementer solo escribe en `src/`, el Tester solo crea tests nuevos en `tests/` (sin `skip` ni `xfail`), y nadie modifica `AGENTS.md`, `pyproject.toml` ni el CI. Los permisos se validan sobre la ruta resuelta, así que `..` no sirve para salir del alcance.
- **Secretos:** se bloquea la lectura de `.env`, claves y certificados, y se escanea todo contenido antes de escribirlo.
- **Comandos:** una lista de patrones prohibidos (borrados recursivos, `push --force`, red, instalación de dependencias) se verifica antes de ejecutar.
- **Entorno:** los procesos hijos heredan solo variables de una allowlist, así que las API keys nunca llegan a los comandos de verificación.

El Sandbox es una segunda barrera, no la única: en producción, cada tarea debería correr en un contenedor efímero sin red.

## Extensibilidad

- **Otro modelo:** implementá `LLMClient.complete(role, system, prompt)`.
- **Otros checks:** pasá un mapa propio de `CheckSpec` a `SandboxGateRunner` (por ejemplo, `npm test` para un repo de TypeScript).
- **Otra política:** `OrchestratorConfig` controla reintentos, prefijos de escritura y escalamiento de áreas sensibles.
