"""T-09 — LLM adapter, tested with a mocked client (no real API calls).

SDK behavior asserted here (refusal field, parsed field, strict/
additionalProperties auto-set from a Pydantic response_format,
APITimeoutError hierarchy) was verified by introspecting the actual
installed openai==2.49.0 source before writing app/llm.py or these tests.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from openai import APITimeoutError
from pydantic import ValidationError

from app.llm import ConfidenceSchema, OpenAIConfidenceProvider


def _fake_completion(parsed=None, refusal=None):
    message = SimpleNamespace(parsed=parsed, refusal=refusal)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def _fake_client(parse_mock: AsyncMock) -> SimpleNamespace:
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse_mock)))


async def test_happy_path():
    parse = AsyncMock(
        return_value=_fake_completion(
            parsed=ConfidenceSchema(self_reported_confidence=0.8, reasoning="looks routine")
        )
    )
    provider = OpenAIConfidenceProvider(_fake_client(parse), model="gpt-5.6-luna")

    result = await provider.get_confidence("delete", "users/42", {"id": 42})

    assert result.confidence == 0.8
    assert result.degraded is False
    parse.assert_awaited_once()


async def test_refusal_is_terminal_and_not_retried():
    parse = AsyncMock(
        return_value=_fake_completion(parsed=None, refusal="I can't help with that request.")
    )
    provider = OpenAIConfidenceProvider(_fake_client(parse), model="gpt-5.6-luna")

    result = await provider.get_confidence("delete", "users/42", {"id": 42})

    assert result.confidence == 0.0
    assert result.degraded is True
    assert "refusal" in result.reason
    parse.assert_awaited_once()  # terminal - never retried


async def test_timeout_fails_closed():
    err = APITimeoutError(request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))
    parse = AsyncMock(side_effect=err)
    provider = OpenAIConfidenceProvider(_fake_client(parse), model="gpt-5.6-luna")

    result = await provider.get_confidence("delete", "users/42", {"id": 42})

    assert result.confidence == 0.0
    assert result.degraded is True
    assert "timeout" in result.reason


async def test_out_of_range_value_is_rejected():
    # Structured outputs constrain STRUCTURE, not VALUES: simulate exactly
    # what the SDK raises internally when our field_validator rejects an
    # in-range-typed-but-out-of-range number during response parsing.
    captured_error: ValidationError | None = None
    try:
        ConfidenceSchema.model_validate({"self_reported_confidence": 7.4, "reasoning": "x"})
        raise AssertionError("expected ValidationError")
    except ValidationError as exc:
        captured_error = exc

    parse = AsyncMock(side_effect=captured_error)
    provider = OpenAIConfidenceProvider(_fake_client(parse), model="gpt-5.6-luna")

    result = await provider.get_confidence("delete", "users/42", {"id": 42})

    assert result.confidence == 0.0
    assert result.degraded is True
    assert "range" in result.reason or "validation" in result.reason.lower()


async def test_caches_by_action_type_resource_and_canonical_params():
    parse = AsyncMock(
        return_value=_fake_completion(
            parsed=ConfidenceSchema(self_reported_confidence=0.6, reasoning="cached test")
        )
    )
    provider = OpenAIConfidenceProvider(_fake_client(parse), model="gpt-5.6-luna")

    await provider.get_confidence("delete", "users/42", {"id": 42})
    await provider.get_confidence("delete", "users/42", {"id": 42})

    parse.assert_awaited_once()  # second call served from cache


async def test_degraded_result_is_not_cached():
    """A failed/degraded call must never be replayed on a later identical
    call - only genuine successes are cached."""
    err = APITimeoutError(request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))
    success = _fake_completion(
        parsed=ConfidenceSchema(self_reported_confidence=0.7, reasoning="recovered")
    )
    parse = AsyncMock(side_effect=[err, success])
    provider = OpenAIConfidenceProvider(_fake_client(parse), model="gpt-5.6-luna")

    first = await provider.get_confidence("delete", "users/42", {"id": 42})
    assert first.degraded is True

    second = await provider.get_confidence("delete", "users/42", {"id": 42})
    assert second.degraded is False
    assert second.confidence == 0.7
    assert parse.await_count == 2  # the second call genuinely retried, not served from cache
