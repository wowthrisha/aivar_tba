"""FEATURE E (intelligence-v6) — plain-language explanation for a
non-technical reviewer. Mirrors app/llm.py's provider/cache pattern
(structured outputs via client.chat.completions.parse, the same
verified openai==2.49.0 behavior documented there) - a second provider
class rather than a variant of ConfidenceProvider, since this scores
nothing and returns prose, not a risk dimension.

E2 STRICT GROUNDING, non-negotiable:
  - Input is ONLY the stored structured record's listed fields
    (sub-scores, weights, composite, tier, floors_fired, the
    counterfactual explanation string, precedent) - deliberately NOT
    action_type/resource/params, both to stay literally within that
    field list and because those are agent-controlled, untrusted text
    (T-13 Finding 4a's lesson in app/llm.py applies here too).
  - The prompt forbids introducing any fact not in that input.
  - Cached by action_id (this class's own dict, same pattern as
    OpenAIConfidenceProvider._cache) - the explanation for a given
    decision must never change. This is an IN-PROCESS cache only: it
    does not survive a restart or a second worker process. A production
    fix would persist it (e.g. an additive rendered_plain_explanation
    column) - not done in this pass per S3's caution against live-DB
    schema changes for a shadow/presentation-only feature.
  - Fail soft: a failure returns degraded=True and NO text of its own -
    app/main.py's handler falls back to the action's existing
    rendered_explanation, per E2's literal instruction. This class never
    fails the request itself (every exception path returns a
    ExplanationResult, never raises).
"""

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Protocol

from openai import APITimeoutError, OpenAIError
from pydantic import BaseModel, ValidationError

logger = logging.getLogger("app")

TIMEOUT_SECONDS = 5.0


class ExplanationSchema(BaseModel):
    explanation: str


@dataclass(frozen=True)
class ExplanationResult:
    text: str | None
    degraded: bool
    reason: str | None = None


class ExplanationProvider(ABC):
    @abstractmethod
    async def explain(self, action_id: str, structured_record: dict[str, Any]) -> ExplanationResult: ...


class _ParseCapableClient(Protocol):
    chat: Any


class OpenAIExplanationProvider(ExplanationProvider):
    def __init__(self, client: _ParseCapableClient, model: str) -> None:
        self._client = client
        self._model = model  # PINNED, same OPENAI_MODEL as app/llm.py - not a second model string
        self._cache: dict[str, ExplanationResult] = {}

    async def explain(self, action_id: str, structured_record: dict[str, Any]) -> ExplanationResult:
        if action_id in self._cache:
            return self._cache[action_id]

        result = await self._call(structured_record)
        if not result.degraded:
            # Only a genuine success is cached, same rule as
            # OpenAIConfidenceProvider - a degraded result must never
            # "stick" and be replayed as if it were the real explanation.
            self._cache[action_id] = result
        return result

    async def _call(self, structured_record: dict[str, Any]) -> ExplanationResult:
        try:
            completion = await self._client.chat.completions.parse(
                model=self._model,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "You are writing a plain-English summary of an automated "
                            "risk-governance decision, for a reviewer with NO technical "
                            "background. You may use ONLY the facts given below inside "
                            "<structured_record> - never invent, assume, or infer any "
                            "fact that is not explicitly present in it. If a field is "
                            "null or absent, do not mention it or guess a value. Do not "
                            "add caveats, recommendations, or claims about what should "
                            "happen next - describe only what the record already says.\n"
                            "<structured_record>\n"
                            f"{json.dumps(structured_record, sort_keys=True, default=str)}\n"
                            "</structured_record>\n"
                            "Write 2-4 plain-English sentences."
                        ),
                    }
                ],
                response_format=ExplanationSchema,
                timeout=TIMEOUT_SECONDS,
            )
        except APITimeoutError:
            logger.warning("explanation_degraded=true reason=timeout")
            return ExplanationResult(text=None, degraded=True, reason="timeout")
        except ValidationError as exc:
            logger.warning(f"explanation_degraded=true reason=validation detail={exc}")
            return ExplanationResult(text=None, degraded=True, reason=f"validation: {exc}")
        except OpenAIError as exc:
            logger.warning(f"explanation_degraded=true reason=api_error detail={exc}")
            return ExplanationResult(text=None, degraded=True, reason=f"api_error: {exc}")

        message = completion.choices[0].message

        if message.refusal is not None:
            # TERMINAL, never retried - same rule as app/llm.py.
            logger.warning(f"explanation_degraded=true reason=refusal detail={message.refusal}")
            return ExplanationResult(text=None, degraded=True, reason=f"refusal: {message.refusal}")

        if message.parsed is None:
            logger.warning("explanation_degraded=true reason=unparsed_response")
            return ExplanationResult(text=None, degraded=True, reason="unparsed_response")

        return ExplanationResult(text=message.parsed.explanation, degraded=False)
