import json

import httpx
import pytest

from prompt_maestro.errors import LLMError
from prompt_maestro.llm import AnthropicLLM, ScriptedLLM


def _ok(text: str) -> httpx.Response:
    return httpx.Response(200, json={"content": [{"type": "text", "text": text}]})


async def test_anthropic_client_sends_expected_request() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok('{"ok": true}')

    llm = AnthropicLLM(api_key="test", model="m", transport=httpx.MockTransport(handler))
    text = await llm.complete(role="planner", system="sys", prompt="hola")
    await llm.aclose()

    assert text == '{"ok": true}'
    body = json.loads(seen[0].content)
    assert body["model"] == "m" and body["system"] == "sys"
    assert seen[0].headers["x-api-key"] == "test"


async def test_anthropic_client_retries_overloaded(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter([httpx.Response(529, text="overloaded"), _ok("listo")])

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("prompt_maestro.llm.asyncio.sleep", no_sleep)
    llm = AnthropicLLM(api_key="k", transport=httpx.MockTransport(lambda _: next(responses)))
    assert await llm.complete(role="r", system="s", prompt="p") == "listo"
    await llm.aclose()


async def test_anthropic_client_does_not_retry_client_errors() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, text="bad request")

    llm = AnthropicLLM(api_key="k", transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError, match="HTTP 400"):
        await llm.complete(role="r", system="s", prompt="p")
    await llm.aclose()
    assert calls == 1


async def test_anthropic_client_fails_after_transport_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_sleep(_: float) -> None:
        return None

    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin red")

    monkeypatch.setattr("prompt_maestro.llm.asyncio.sleep", no_sleep)
    llm = AnthropicLLM(api_key="k", max_retries=2, transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError, match="ConnectError"):
        await llm.complete(role="r", system="s", prompt="p")
    await llm.aclose()


async def test_scripted_llm_without_responses_fails() -> None:
    with pytest.raises(LLMError):
        await ScriptedLLM({}).complete(role="planner", system="", prompt="")
