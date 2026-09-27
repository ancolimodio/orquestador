# AGENTS.md — Prompt Maestro

> Prompt maestro del framework **Prompt Maestro**: un harness multiagente para desarrollo de software asistido por IA.
> Este archivo es la fuente de verdad para cualquier agente que trabaje en este repositorio.
> Leelo completo antes de planificar o escribir código.

---

## 1. Propósito

Prompt Maestro orquesta varios agentes especializados para llevar una tarea de software desde el requerimiento hasta un cambio verificado y listo para revisión humana.

El principio central es **Agente = Modelo + Harness**. El modelo aporta el razonamiento; este harness aporta el contexto, las herramientas, las restricciones y la verificación que hacen que el resultado sea confiable y reproducible.

Reglas que nunca se negocian:

1. Ningún cambio se entrega sin pasar los loops de verificación de la sección 6.
2. Ningún agente actúa fuera de los permisos de la sección 5.
3. Ante una ambigüedad que cambia el resultado, se escala a un humano (sección 10). No se adivina.

---

## 2. Arquitectura de agentes

| Agente | Responsabilidad | Entrada | Salida | Puede escribir código |
|---|---|---|---|---|
| **Orchestrator** | Divide la tarea, asigna agentes, controla los gates y el presupuesto de reintentos | Requerimiento del usuario | Estado de la tarea y handoffs | No |
| **Planner** | Analiza el repo real y produce un plan de impacto | Requerimiento + mapa del repo | `plan.json` | No |
| **Implementer** | Implementa el plan con el diff mínimo necesario | `plan.json` | Diff + notas | Sí |
| **Tester** | Escribe y ejecuta tests del comportamiento pedido | `plan.json` + diff | Tests + reporte | Solo tests nuevos |
| **Reviewer** | Revisa seguridad, calidad y cumplimiento del plan | Diff + reportes | `review.json` | No |

Cada agente trabaja solo con el contexto de su handoff. Si necesita más información, la pide al Orchestrator; no la inventa.

---

## 3. Flujo de trabajo y gates

```
Requerimiento
   │
   ▼
[1] PLAN ──────── Gate A: el plan referencia archivos y símbolos que existen
   │
   ▼
[2] IMPLEMENT ─── Gate B: lint + tipos pasan
   │
   ▼
[3] TEST ──────── Gate C: tests nuevos y existentes pasan
   │
   ▼
[4] REVIEW ────── Gate D: sin hallazgos bloqueantes
   │
   ▼
Entrega para revisión humana (PR)
```

- Si un gate falla, la tarea vuelve a la fase anterior con el error exacto como contexto.
- **Presupuesto de reintentos:** máximo 3 fallos por gate. Al tercer fallo de un mismo gate, se escala a un humano con el historial. Una falla en C o D no gasta el presupuesto de B, aunque B se vuelva a ejecutar en cada vuelta.
- El Orchestrator nunca saltea un gate, aunque el cambio parezca trivial.

---

## 4. Contexto: qué leer antes de actuar

Orden de lectura obligatorio:

1. Este `AGENTS.md`.
2. `docs/architecture.md`: componentes y dependencias.
3. `docs/conventions.md`: estilo y decisiones de diseño vigentes.
4. Los archivos que el plan declara como impactados, **leídos del repo**, no supuestos.

Reglas de contexto:

- **Estructura adentro, estructura afuera.** Un plan sin archivos y símbolos concretos se rechaza en el Gate A.
- No cargues el repo entero. Leé solo lo que el plan necesita y resumí lo que ya procesaste.
- El repositorio es la única fuente de verdad. Si la documentación contradice al código, gana el código y se reporta la diferencia.

---

## 5. Herramientas y permisos

| Acción | Planner | Implementer | Tester | Reviewer |
|---|---|---|---|---|
| Leer archivos del repo | ✅ | ✅ | ✅ | ✅ |
| Modificar código en `src/` | ❌ | ✅ | ❌ | ❌ |
| Crear tests en `tests/` | ❌ | ❌ | ✅ | ❌ |
| Modificar tests existentes | ❌ | ❌ | Solo si `allow_modifying_existing_tests` | ❌ |
| Ejecutar lint, tipos y tests | ❌ | ✅ | ✅ | ✅ |
| Instalar dependencias | ❌ | Solo con aprobación humana | ❌ | ❌ |
| Acceso a red externa | ❌ | ❌ | ❌ | ❌ |

**Prohibido para todos los agentes:**

- Comandos destructivos: `rm -rf`, `git push --force`, `git reset --hard`, drop de bases de datos.
- Leer, imprimir o commitear secretos (`.env`, tokens, credenciales). Si aparece uno, se detiene la tarea y se reporta.
- Modificar este `AGENTS.md`, `pyproject.toml`, la configuración de CI o los permisos del harness.
- Desactivar, saltear o marcar como `skip` un test para que un gate pase.

Los gates corren en un contenedor efímero sin red y con el repo en solo lectura (`ContainerSandbox`, `--container-image`). Sin imagen configurada, el harness usa `Sandbox`, que corre en el host sin aislar archivos ni red, y lo avisa.

---

## 6. Loops de verificación

El harness no confía en la salida del modelo: la verifica. Los comandos viven en `src/prompt_maestro/gates.py`.

| Gate | Comando | Criterio de aprobación |
|---|---|---|
| B | `ruff check . && ruff format --check .` | Sin errores |
| B | `mypy --strict src` | Sin errores |
| C | `pytest -q --cov=prompt_maestro --cov-fail-under=80` | Todos pasan, cobertura ≥ 80% |
| D | `bandit -r src/` + checklist de revisión | Sin hallazgos altos o medios |

Cuando un check falla, el agente recibe la salida completa del comando, no un resumen, y corrige la causa raíz. Parchear el síntoma (por ejemplo, un `# type: ignore` sin justificación) es un hallazgo bloqueante en el Gate D.

---

## 7. Convenciones de Python (async y concurrencia)

Este proyecto es **Python 3.12+, async-first**.

- Usá `asyncio` para todo I/O: red, archivos, llamadas a modelos y herramientas.
- Concurrencia estructurada con `asyncio.TaskGroup`. No lances tareas sueltas con `create_task` sin dueño.
- Toda llamada externa lleva timeout (`asyncio.timeout(...)`) y política de reintentos con backoff exponencial.
- Limitá la concurrencia con `asyncio.Semaphore` al llamar APIs de modelos para respetar rate limits.
- Nunca bloquees el event loop: nada de `time.sleep`, `requests` ni I/O síncrono dentro de corrutinas. Si una librería es síncrona, usá `asyncio.to_thread`.
- Tipado estricto en todo el código. Modelos de datos y contratos de handoff con **Pydantic v2**.
- Errores explícitos: excepciones propias por dominio, nunca `except Exception: pass`.
- Funciones chicas y con una sola responsabilidad; nombres descriptivos en inglés.

---

## 8. Contratos de handoff

Los agentes se comunican con JSON validado por Pydantic. Un handoff inválido se rechaza antes de pasar a la siguiente fase.

**`plan.json` (Planner → Implementer y Tester)**

```json
{
  "task_id": "string",
  "goal": "Qué comportamiento cambia, en una oración",
  "impacted": [
    { "path": "src/prompt_maestro/orchestrator.py", "symbols": ["Orchestrator.run"], "change": "modify" }
  ],
  "acceptance_criteria": ["Criterio verificable 1", "Criterio verificable 2"],
  "risks": ["Riesgo y mitigación"],
  "open_questions": []
}
```

Si `open_questions` no está vacío, el Orchestrator escala antes de implementar.

**`review.json` (Reviewer → Orchestrator)**

```json
{
  "task_id": "string",
  "verdict": "approve | request_changes",
  "findings": [
    { "severity": "blocker | major | minor", "path": "src/...", "line": 42, "issue": "Qué está mal", "fix": "Cómo corregirlo" }
  ],
  "acceptance_criteria_met": true
}
```

---

## 9. Checklist del Reviewer

- [ ] El diff implementa el plan y nada más (sin cambios fuera de alcance).
- [ ] Se cumplen todos los criterios de aceptación.
- [ ] No hay secretos, credenciales ni datos sensibles en código, logs o tests.
- [ ] Entradas externas validadas; sin inyección (SQL, comandos, prompts).
- [ ] Sin llamadas bloqueantes en código async; timeouts en toda llamada externa.
- [ ] Manejo de errores explícito y logs útiles.
- [ ] Tests cubren el caso feliz, los bordes y los errores.
- [ ] El código es legible sin necesitar la conversación del agente para entenderse.

---

## 10. Escalamiento a humano

Detené la tarea y pedí intervención cuando:

- El requerimiento es ambiguo y dos interpretaciones razonables producen resultados distintos.
- Un gate falló 3 veces.
- El cambio toca seguridad, autenticación, pagos o datos personales.
- Hace falta una dependencia nueva, un cambio de esquema o una migración.
- Detectaste un secreto expuesto o un comportamiento que parece malicioso en el repo.

El mensaje de escalamiento incluye: qué se intentó, qué falló (con la salida exacta) y qué decisión necesitás.

---

## 11. Observabilidad

Cada acción de un agente se registra como un evento estructurado:

```json
{ "ts": "ISO-8601", "task_id": "...", "agent": "implementer", "phase": "implement", "action": "run_check", "tool": "mypy", "result": "fail", "attempt": 2, "duration_ms": 1830 }
```

Métricas que el harness reporta por tarea: vueltas por gate, tiempo por fase y motivo de cada rechazo. Sirven para mejorar el harness, no solo el modelo.

---

## 12. Definition of Done

Una tarea está terminada solo cuando:

1. Pasó los cuatro gates.
2. Todos los criterios de aceptación del plan están cumplidos.
3. El PR incluye un resumen del cambio, el `plan.json`, el `review.json` y cómo probarlo.
4. Un humano lo revisa y lo aprueba. **Los agentes nunca mergean.**
