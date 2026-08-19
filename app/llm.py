"""LLM confidence provider. Scores exactly ONE dimension: confidence.

Structured Outputs via client.chat.completions.parse(response_format=
<Pydantic model>). Verified against the actual installed openai==2.49.0
source before writing this (not guessed, per E-3):
  - passing a Pydantic BaseModel as response_format auto-sets strict=True
    and additionalProperties=False on every object
    (openai/lib/_pydantic.py, openai/lib/_parsing/_completions.py:286)
  - message.refusal: str | None, message.parsed: Model | None
    (openai.types.chat.ParsedChatCompletionMessage)
  - refusal short-circuits parsing: maybe_parse_content() skips parsing
    entirely when message.refusal is set, so parsed stays None
  - a field_validator on the response_format model runs during the SDK's
    own internal parsing, so an out-of-range value raises
    pydantic.ValidationError out of the .parse() call itself
  - APITimeoutError, LengthFinishReasonError, ContentFilterFinishReasonError
    are all subclasses of openai.OpenAIError
"""

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Protocol

from openai import APITimeoutError, OpenAIError
from pydantic import BaseModel, ValidationError, field_validator

TIMEOUT_SECONDS = 3.0


class ConfidenceSchema(BaseModel):
    """The structured-output contract. Every field required (strict mode)."""

    self_reported_confidence: float
    reasoning: str

    @field_validator("self_reported_confidence")
    @classmethod
    def _range_check(cls, value: float) -> float:
        # Structured outputs constrain STRUCTURE, not VALUES: a number
        # field can still return e.g. 7.4 when the contract says 0-1.
        if not (0.0 <= value <= 1.0):
            raise ValueError(f"self_reported_confidence out of range: {value}")
        return value


@dataclass(frozen=True)
class ConfidenceResult:
    confidence: float
    degraded: bool
    reason: str | None


class ConfidenceProvider(ABC):
    @abstractmethod
    async def get_confidence(
        self, action_type: str, resource: str, params: dict[str, Any]
    ) -> ConfidenceResult: ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Cheap reachability probe for /readyz. Never raises."""
        ...


class _ParseCapableClient(Protocol):
    chat: Any  # structurally: client.chat.completions.parse(...) is awaitable
    models: Any  # structurally: client.models.retrieve(model) is awaitable


def _cache_key(action_type: str, resource: str, params: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"action_type": action_type, "resource": resource, "params": params},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class OpenAIConfidenceProvider(ConfidenceProvider):
    def __init__(self, client: _ParseCapableClient, model: str) -> None:
        self._client = client
        self._model = model  # PINNED: caller passes the exact OPENAI_MODEL string
        self._cache: dict[str, ConfidenceResult] = {}

    async def health_check(self) -> bool:
        try:
            await self._client.models.retrieve(self._model)
            return True
        except Exception:
            return False

    async def get_confidence(
        self, action_type: str, resource: str, params: dict[str, Any]
    ) -> ConfidenceResult:
        key = _cache_key(action_type, resource, params)
        if key in self._cache:
            return self._cache[key]

        result = await self._call(action_type, resource, params)
        self._cache[key] = result
        return result

    async def _call(
        self, action_type: str, resource: str, params: dict[str, Any]
    ) -> ConfidenceResult:
        try:
            completion = await self._client.chat.completions.parse(
                model=self._model,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Rate your confidence (0.0-1.0) that this proposed "
                            f"action is safe and well-specified.\naction_type: "
                            f"{action_type}\nresource: {resource}\nparams: {params}"
                        ),
                    }
                ],
                response_format=ConfidenceSchema,
                timeout=TIMEOUT_SECONDS,
            )
        except APITimeoutError:
            return ConfidenceResult(confidence=0.0, degraded=True, reason="timeout")
        except ValidationError as exc:
            # structural parse succeeded but a value was out of range
            return ConfidenceResult(confidence=0.0, degraded=True, reason=f"out_of_range_validation: {exc}")
        except OpenAIError as exc:
            # FAIL CLOSED: any other API error, including refusal-adjacent
            # finish reasons (length, content_filter) - never fail open.
            return ConfidenceResult(confidence=0.0, degraded=True, reason=f"api_error: {exc}")

        message = completion.choices[0].message

        if message.refusal is not None:
            # TERMINAL. Never retried - a retry loop that ignores refusals
            # loops forever.
            return ConfidenceResult(confidence=0.0, degraded=True, reason=f"refusal: {message.refusal}")

        if message.parsed is None:
            return ConfidenceResult(confidence=0.0, degraded=True, reason="unparsed_response")

        return ConfidenceResult(
            confidence=message.parsed.self_reported_confidence,
            degraded=False,
            reason=None,
        )
