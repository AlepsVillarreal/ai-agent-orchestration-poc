import json
from collections.abc import AsyncGenerator
from types import SimpleNamespace

from google.adk import Runner
from google.adk.models import BaseLlm, Gemini, LlmRequest, LlmResponse
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import Field

from assistant.interpreter import (
    INTERPRETER_RETRY_OPTIONS,
    MODEL,
    build_interpreter,
    interpreter,
    interpreter_instruction,
)
from core.instruction import NOTHING_PENDING, build_instruction, pending_summary
from core.interpretation import TurnInterpretation
from tests.interpretations import FIXED

PENDING = {
    "tools": ["create_ticket"],
    "status": "collecting",
    "missing": ["title", "priority", "assignee"],
    "last_question": "¿Qué título, prioridad y responsable tendrá?",
    "args": {"description": "Servidor caído", "assignee": "Carlos"},
}


class FakeLlm(BaseLlm):
    """Stands in for Gemini: records every request and answers with a fixed JSON."""

    reply: str
    requests: list[LlmRequest] = Field(default_factory=list)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.requests.append(llm_request)
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=self.reply)]))


def _system_text(request: LlmRequest) -> str:
    instruction = request.config.system_instruction
    if isinstance(instruction, str):
        return instruction
    return "".join(part.text or "" for part in instruction.parts)


def _texts(request: LlmRequest) -> list[str]:
    return [part.text for content in request.contents for part in content.parts if part.text]


# 18
def test_interpreter_configuration():
    assert interpreter.name == "interpreter"
    # D1.2: the live agent carries the 503 retry in its model, not in generate_content_config.
    assert isinstance(interpreter.model, Gemini)
    assert interpreter.model.model == MODEL == "gemini-3.5-flash-lite"
    assert interpreter.model.retry_options is INTERPRETER_RETRY_OPTIONS
    assert interpreter.output_schema is TurnInterpretation
    assert interpreter.include_contents == "none"
    assert interpreter.output_key is None
    assert interpreter.tools == []
    assert interpreter.sub_agents == []
    assert interpreter.generate_content_config is None
    assert callable(interpreter.instruction)


# D1.2: two attempts, only on a 503 (a 429 is spent quota), the SDK's exp_base and jitter.
def test_interpreter_retry_options():
    options = INTERPRETER_RETRY_OPTIONS
    assert options.attempts == 2
    assert options.http_status_codes == [503]
    assert options.initial_delay == 1.0
    assert options.max_delay == 5.0
    assert options.exp_base is None
    assert options.jitter is None


# 19
def test_instruction_provider_reads_pending_from_state():
    with_pending = interpreter_instruction(SimpleNamespace(state={"pending": PENDING}))
    without = interpreter_instruction(SimpleNamespace(state={}))

    assert with_pending.startswith(build_instruction())
    assert with_pending.endswith(pending_summary(PENDING))
    assert without.startswith(build_instruction())
    assert without.endswith(NOTHING_PENDING)


# 20
async def test_interpreter_request_with_fake_model():
    fake = FakeLlm(model="fake", reply=json.dumps(FIXED["A1"].interpretation))
    agent = build_interpreter(fake)
    sessions = InMemorySessionService()
    session = await sessions.create_session(
        app_name="test", user_id="u1", state={"pending": PENDING}
    )
    runner = Runner(app_name="test", agent=agent, session_service=sessions)

    messages = ["primer mensaje", "segundo mensaje"]
    events = []
    for text in messages:
        async for event in runner.run_async(
            user_id="u1",
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text=text)]),
        ):
            events.append(event)

    assert len(fake.requests) == 2
    for request, text in zip(fake.requests, messages, strict=True):
        system = _system_text(request)
        assert build_instruction() in system
        assert pending_summary(PENDING) in system
        assert "Carlos" not in system and "Servidor caído" not in system
        # include_contents='none': only this turn's message, also on the second turn.
        assert _texts(request) == [text]
        assert request.config.response_schema is TurnInterpretation

    interpreter_events = [event for event in events if event.author == "interpreter"]
    assert interpreter_events
    assert all(not event.actions.state_delta for event in interpreter_events)
