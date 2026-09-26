"""Clientes de modelo. El harness depende de un Protocol, no de un proveedor."""

import asyncio
from collections import deque
from collections.abc import Mapping, Sequence
from typing import Any, Protocol, cast

import httpx

from prompt_maestro.errors import LLMError

RETRYABLE_STATUS: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504, 529})


class LLMClient(Protocol):
    async def complete(self, *, role: str, system: str, prompt: str) -> str: ...


class ScriptedLLM:
    """Respuestas predefinidas por rol. Útil para tests, demos y evals reproducibles."""

    def __init__(self, responses: Mapping[str, Sequence[str]]) -> None:
        self._responses = {role: deque(items) for role, items in responses.items()}
        self.calls: list[tuple[str, str]] = []

    async def complete(self, *, role: str, system: str, prompt: str) -> str:
        self.calls.append((role, prompt))
        queue = self._responses.get(role)
        if not queue:
            raise LLMError(f"ScriptedLLM sin respuestas para el rol '{role}'.")
        return queue.popleft()


class AnthropicLLM:
    """Cliente async de la API de Anthropic con timeout, reintentos y rate limiting."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "claude-sonnet-5",
        max_tokens: int = 8192,
        timeout_s: float = 120.0,
        max_retries: int = 3,
        max_concurrency: int = 4,
        base_url: str = "https://api.anthropic.com",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._client = httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
            timeout=timeout_s,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )

    async def complete(self, *, role: str, system: str, prompt: str) -> str:
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        last_error = "sin detalle"
        async with self._semaphore:
            for attempt in range(1, self.max_retries + 1):
                try:
                    async with asyncio.timeout(self.timeout_s):
                        response = await self._client.post("/v1/messages", json=payload)
                except (httpx.TransportError, TimeoutError) as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                else:
                    if response.status_code == 200:
                        return self._extract_text(response.json())
                    last_error = f"HTTP {response.status_code}: {response.text[:300]}"
                    if response.status_code not in RETRYABLE_STATUS:
                        break
                if attempt < self.max_retries:
                    await asyncio.sleep(0.5 * 2 ** (attempt - 1))
        raise LLMError(f"Fallo del modelo para el rol '{role}': {last_error}")

    @staticmethod
    def _extract_text(data: Any) -> str:
        blocks = cast(list[dict[str, Any]], data.get("content", []))
        return "".join(str(b.get("text", "")) for b in blocks if b.get("type") == "text")

    async def aclose(self) -> None:
        await self._client.aclose()
