"""FEATURE E — plain-language explanation provider, tested with a mocked
client (no real API calls), same pattern as tests/test_llm.py.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from openai import APITimeoutError

from app.explain import ExplanationSchema, OpenAIExplanationProvider

_RECORD = {"tier": "CONFIRM", "composite": 0.42, "floors_fired": []}


def _fake_completion(parsed=None, refusal=None):
    message = SimpleNamespace(parsed=parsed, refusal=refusal)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def _fake_client(parse_mock: AsyncMock) -> SimpleNamespace:
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse_mock)))


async def test_happy_path_returns_text():
    parse = AsyncMock(
        return_value=_fake_completion(parsed=ExplanationSchema(explanation="This is a routine change."))
    )
    provider = OpenAIExplanationProvider(_fake_client(parse), model="gpt-5.6-luna")

    result = await provider.explain("action-1", _RECORD)

    assert result.text == "This is a routine change."
    assert result.degraded is False
    parse.assert_awaited_once()


async def test_refusal_is_terminal_degraded_with_no_text():
    parse = AsyncMock(return_value=_fake_completion(parsed=None, refusal="cannot help"))
    provider = OpenAIExplanationProvider(_fake_client(parse), model="gpt-5.6-luna")

    result = await provider.explain("action-1", _RECORD)

    assert result.text is None
    assert result.degraded is True
    assert "refusal" in result.reason
    parse.assert_awaited_once()  # terminal, never retried


async def test_timeout_is_degraded_with_no_text():
    err = APITimeoutError(request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))
    parse = AsyncMock(side_effect=err)
    provider = OpenAIExplanationProvider(_fake_client(parse), model="gpt-5.6-luna")

    result = await provider.explain("action-1", _RECORD)

    assert result.degraded is True
    assert result.text is None


async def test_caching_returns_identical_text_and_calls_llm_only_once():
    parse = AsyncMock(
        return_value=_fake_completion(parsed=ExplanationSchema(explanation="Stable summary."))
    )
    provider = OpenAIExplanationProvider(_fake_client(parse), model="gpt-5.6-luna")

    first = await provider.explain("action-1", _RECORD)
    second = await provider.explain("action-1", {"tier": "DIFFERENT_INPUT_IGNORED"})

    assert first.text == second.text == "Stable summary."
    parse.assert_awaited_once()  # second call served from cache, no second LLM call


async def test_degraded_result_is_never_cached():
    parse = AsyncMock(
        side_effect=[
            _fake_completion(parsed=None, refusal="nope"),
            _fake_completion(parsed=ExplanationSchema(explanation="Recovered.")),
        ]
    )
    provider = OpenAIExplanationProvider(_fake_client(parse), model="gpt-5.6-luna")

    first = await provider.explain("action-1", _RECORD)
    assert first.degraded is True

    second = await provider.explain("action-1", _RECORD)
    assert second.degraded is False
    assert second.text == "Recovered."
    assert parse.await_count == 2  # degraded result was not cached, retried on next call
