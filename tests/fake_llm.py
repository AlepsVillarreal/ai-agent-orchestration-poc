"""A scripted stand-in for Gemini, run through the real `Runner` (no API key, no network).

Each model call consumes the next item of `script`: a string is returned as the model's text,
an exception is raised. Every `LlmRequest` is recorded so tests can check what the model saw.
Running past the end of the script fails the test: D1.1 allows one model call per turn.
"""

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import Field


class ScriptedLlm(BaseLlm):
    model: str = "fake"
    script: list[Any] = Field(default_factory=list)  # str (model text) or Exception (raised)
    requests: list[LlmRequest] = Field(default_factory=list)

    @property
    def calls(self) -> int:
        return len(self.requests)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        if self.calls >= len(self.script):
            pytest.fail(
                f"the model was called {self.calls + 1} times but the script has "
                f"{len(self.script)} items (one call per turn, D1.1)",
                pytrace=False,
            )
        item = self.script[self.calls]
        self.requests.append(llm_request)
        if isinstance(item, BaseException):
            raise item
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=item)]))


def quota_error() -> genai_errors.ClientError:
    """A simulated 429 from the Gemini API."""
    return genai_errors.ClientError(
        429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota"}}
    )


def system_text(request: LlmRequest) -> str:
    instruction = request.config.system_instruction
    if isinstance(instruction, str):
        return instruction
    return "".join(part.text or "" for part in instruction.parts)


def contents_text(request: LlmRequest) -> list[str]:
    return [part.text for content in request.contents for part in content.parts if part.text]


async def run_turn(runner, user_id: str, session_id: str, text: str) -> list:
    """Send one user message through the `Runner` and return every event of the turn."""
    message = types.Content(role="user", parts=[types.Part(text=text)])
    return [
        event
        async for event in runner.run_async(
            user_id=user_id, session_id=session_id, new_message=message
        )
    ]


def event_text(event) -> str:
    if not event.content or not event.content.parts:
        return ""
    return "".join(part.text or "" for part in event.content.parts)


def reply_events(events: list) -> list:
    """What the CLI prints: events with text whose author is not the interpreter (D1.1)."""
    return [event for event in events if event_text(event) and event.author != "interpreter"]
