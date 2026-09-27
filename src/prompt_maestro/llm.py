"""Clientes de modelo. El harness depende de un Protocol, no de un proveedor."""

import asyncio
from collections import deque
from collections.abc import Mapping, Sequence
from typing import Any, Protocol, cast

import httpx

from prompt_maestro.errors import LLMError

RETRYABLE_STATUS: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504, 529})
MAX_RETRY_AFTER_S = 60.0


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


def _retry_after(response: httpx.Response) -> float | None:
    """Segundos pedidos por el proveedor en `Retry-After`, acotados; None si no hay."""
    try:
        return min(float(response.headers["retry-after"]), MAX_RETRY_AFTER_S)
    except (KeyError, ValueError):
        return None


class HttpLLM:
    """Base de los clientes HTTP: timeout, reintentos con backoff y rate limiting.

    Cada proveedor define la ruta, el cuerpo del pedido y cómo extraer el texto.
    """

    path: str

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        headers: Mapping[str, str],
        max_tokens: int,
        timeout_s: float = 120.0,
        max_retries: int = 3,
        backoff_base_s: float = 0.5,
        max_concurrency: int = 4,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._client = httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
            timeout=timeout_s,
            headers={**headers, "content-type": "application/json"},
        )

    def _payload(self, system: str, prompt: str) -> dict[str, Any]:
        raise NotImplementedError

    def _extract_text(self, data: Any) -> str:
        raise NotImplementedError

    async def complete(self, *, role: str, system: str, prompt: str) -> str:
        payload = self._payload(system, prompt)
        last_error = "sin detalle"
        async with self._semaphore:
            for attempt in range(1, self.max_retries + 1):
                delay = self.backoff_base_s * 2 ** (attempt - 1)
                try:
                    async with asyncio.timeout(self.timeout_s):
                        response = await self._client.post(self.path, json=payload)
                except (httpx.TransportError, TimeoutError) as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                else:
                    if response.status_code == 200:
                        return self._extract_text(response.json())
                    last_error = f"HTTP {response.status_code}: {response.text[:300]}"
                    if response.status_code not in RETRYABLE_STATUS:
                        break
                    # Con límites por minuto, el backoff corto no alcanza: se respeta lo
                    # que pide el proveedor.
                    delay = _retry_after(response) or delay
                if attempt < self.max_retries:
                    await asyncio.sleep(delay)
        raise LLMError(f"Fallo del modelo para el rol '{role}': {last_error}")

    async def aclose(self) -> None:
        await self._client.aclose()


class AnthropicLLM(HttpLLM):
    """Cliente async de la API de Anthropic (Messages API)."""

    path = "/v1/messages"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "claude-sonnet-5",
        max_tokens: int = 8192,
        base_url: str = "https://api.anthropic.com",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            model=model,
            base_url=base_url,
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            max_tokens=max_tokens,
            **kwargs,
        )

    def _payload(self, system: str, prompt: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }

    def _extract_text(self, data: Any) -> str:
        blocks = cast(list[dict[str, Any]], data.get("content", []))
        return "".join(str(b.get("text", "")) for b in blocks if b.get("type") == "text")


class OpenAICompatibleLLM(HttpLLM):
    """Cliente para APIs compatibles con Chat Completions de OpenAI.

    Sirve para OpenAI, el endpoint compatible de Gemini y servidores locales como Ollama.
    """

    path = "chat/completions"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        max_tokens: int = 16_000,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            model=model,
            base_url=base_url,
            headers={"authorization": f"Bearer {api_key}"},
            max_tokens=max_tokens,
            **kwargs,
        )

    def _payload(self, system: str, prompt: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }

    def _extract_text(self, data: Any) -> str:
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Respuesta sin `choices[0].message.content`: {exc!r}") from exc
        return content if isinstance(content, str) else ""
