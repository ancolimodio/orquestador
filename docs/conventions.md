# Convenciones

## Código

- Python 3.12+, tipado estricto (`mypy --strict`) y formateado con `ruff format`.
- Async-first: todo I/O usa `asyncio`. Nada de `time.sleep`, `requests` ni I/O síncrono dentro de corrutinas; para librerías síncronas, `asyncio.to_thread`.
- Concurrencia estructurada con `asyncio.TaskGroup`; concurrencia acotada con `asyncio.Semaphore`.
- Timeout en toda llamada externa (`asyncio.timeout`) y reintentos con backoff exponencial solo para errores recuperables.
- Contratos entre componentes con Pydantic v2 y `extra="forbid"`.
- Excepciones de dominio en `errors.py`; nunca `except Exception: pass`.
- Nombres de código en inglés; mensajes al usuario y documentación en español.

## Tests

- Sin llamadas de red: el modelo se simula con `ScriptedLLM` y HTTP con `httpx.MockTransport`.
- Cada guardrail tiene su test de caso permitido y caso bloqueado.
- Los secretos falsos se construyen en runtime (`"AKIA" + "A" * 16`) para que ningún escáner los detecte en el repo.
- Cobertura mínima del 80%, verificada en CI.

## Commits y PRs

- Commits en formato [Conventional Commits](https://www.conventionalcommits.org/es/): `feat:`, `fix:`, `docs:`, `test:`, `refactor:`.
- Un PR por cambio lógico, con descripción de qué cambia, por qué y cómo probarlo.
- Todo PR pasa el CI completo antes de revisión.

## Decisiones vigentes

| Decisión | Motivo |
|---|---|
| Los agentes devuelven archivos completos, no diffs | Los diffs generados por modelos fallan seguido al aplicarse; el archivo completo es verificable |
| El Tester es un agente separado del Implementer | Evita que un agente escriba tests a medida de su propio código |
| Borrar archivos requiere a un humano | Es la acción con peor relación entre riesgo y beneficio |
| El Gate A usa búsqueda textual de símbolos | Es simple y agnóstico del lenguaje; el análisis por AST está en el roadmap |
