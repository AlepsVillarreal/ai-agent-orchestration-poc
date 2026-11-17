"""The turn node through the real ADK `Runner`, with a scripted fake model (D1.1).

These tests use `InMemorySessionService`; the Redis round trip is in test_session_redis.py.
"""

import dataclasses
import json
import logging

import httpx
import pytest
from google.adk import Runner, Workflow
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.sessions import InMemorySessionService
from google.genai import types

import assistant.agent
from assistant.agent import build_root_agent
from assistant.interpreter import INTERPRETER_RETRY_OPTIONS, MODEL, build_interpreter
from core import replies
from core.instruction import NOTHING_PENDING
from core.interpretation import TurnInterpretation
from core.registry import TOOLS
from core.turn import handle_turn
from tests.fake_llm import (
    ScriptedLlm,
    contents_text,
    event_text,
    quota_error,
    reply_events,
    run_turn,
    system_text,
)
from tests.interpretations import FIXED

USER = "u1"

# An unknown tool next to a known one (D10): the core drops it, the log keeps it.
FLIGHT_AND_TICKET = (
    "Reserva un vuelo y crea un ticket porque el servidor está caído",
    {
        "act": "request_action",
        "operations": [
            {"tool": "book_flight", "certainty": "explicit", "args": {}},
            {
                "tool": "create_ticket",
                "certainty": "explicit",
                "args": {"description": "el servidor está caído"},
            },
        ],
    },
)


def fixed(key: str) -> TurnInterpretation:
    return TurnInterpretation.model_validate(FIXED[key].interpretation)


def a1_record() -> dict:
    return handle_turn(None, fixed("A1")).state_delta["pending"]


def make_runner(fake, sessions=None) -> tuple[Runner, InMemorySessionService]:
    sessions = sessions or InMemorySessionService()
    app = App(name="assistant", root_agent=build_root_agent(build_interpreter(fake)))
    return Runner(app=app, session_service=sessions, auto_create_session=True), sessions


# 1
def test_app_is_where_adk_web_looks():
    app = assistant.agent.app

    assert isinstance(app, App)
    assert app.name == "assistant"
    assert app.root_agent is assistant.agent.root_agent
    assert isinstance(assistant.agent.root_agent, Workflow)
    assert app.resumability_config is None


# 2
async def test_case_a_end_to_end_through_the_node(monkeypatch):
    expected = handle_turn(None, fixed("A1"))

    def fail(args):
        pytest.fail(f"a tool ran with {args}")

    for name, spec in list(TOOLS.items()):
        monkeypatch.setitem(TOOLS, name, dataclasses.replace(spec, execute=fail))

    fake = ScriptedLlm(script=[json.dumps(FIXED["A1"].interpretation)])
    runner, sessions = make_runner(fake)

    events = await run_turn(runner, USER, "s-a", FIXED["A1"].message)

    answers = reply_events(events)
    assert len(answers) == 1
    assert event_text(answers[0]) == expected.reply
    session = await sessions.get_session(app_name="assistant", user_id=USER, session_id="s-a")
    assert session.state["pending"] == expected.state_delta["pending"]

    assert fake.calls == 1
    request = fake.requests[0]
    assert contents_text(request) == [FIXED["A1"].message]  # include_contents='none'
    assert NOTHING_PENDING in system_text(request)


# 3
@pytest.mark.parametrize(
    ("item", "expected"),
    [
        pytest.param('{"act": 123, ', replies.NOT_UNDERSTOOD, id="broken_json"),
        pytest.param('{"act": "dance"}', replies.NOT_UNDERSTOOD, id="outside_schema"),
        pytest.param("", replies.NOT_UNDERSTOOD, id="empty"),
        pytest.param(quota_error(), replies.MODEL_UNAVAILABLE, id="429"),
        pytest.param(TimeoutError("timed out"), replies.MODEL_UNAVAILABLE, id="timeout"),
    ],
)
async def test_failed_turn_keeps_state(item, expected):
    record = a1_record()
    fake = ScriptedLlm(script=[item])
    runner, sessions = make_runner(fake)
    await sessions.create_session(
        app_name="assistant", user_id=USER, session_id="s-f", state={"pending": record}
    )

    events = await run_turn(runner, USER, "s-f", "lo que sea")

    answers = reply_events(events)
    assert len(answers) == 1
    assert event_text(answers[0]) == expected
    assert not answers[0].actions.state_delta
    session = await sessions.get_session(app_name="assistant", user_id=USER, session_id="s-f")
    assert session.state["pending"] == record
    assert fake.calls == 1  # the node does not retry (D1.1)


def _turn_records(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "assistant.turn"]


# 4 and 5
async def test_one_log_line_per_turn(caplog):
    caplog.set_level(logging.INFO, logger="assistant")
    fake = ScriptedLlm(
        script=[
            json.dumps(FIXED["A1"].interpretation),
            json.dumps(FIXED["C1"].interpretation),
            json.dumps(FLIGHT_AND_TICKET[1]),
            quota_error(),
        ]
    )
    runner, _ = make_runner(fake)
    lines = {}
    turns = {
        "A1": FIXED["A1"].message,
        "C1": FIXED["C1"].message,
        "flight": FLIGHT_AND_TICKET[0],
        "429": "Necesito crear un ticket",
    }
    answers = {}
    for key, text in turns.items():
        caplog.clear()
        events = await run_turn(runner, USER, f"s-log-{key}", text)
        answers[key] = event_text(reply_events(events)[0])
        records = _turn_records(caplog)
        assert len(records) == 1, (key, records)
        lines[key] = records[0]

    assert "session=s-log-A1" in lines["A1"]
    assert "act=request_action" in lines["A1"]
    assert "ops=create_ticket:explicit" in lines["A1"]
    assert "route=R2" in lines["A1"]

    assert "route=R4" in lines["C1"]
    assert FIXED["C1"].interpretation["ambiguity_reason"] in lines["C1"]

    # 5: the unknown tool stays visible in the log (before the filter, D10), and the route
    # comes from the core even though the reply starts with the D10 line.
    assert "ops=book_flight:explicit,create_ticket:explicit" in lines["flight"]
    assert "route=R2" in lines["flight"]
    assert answers["flight"].startswith(replies.UNKNOWN_TOOL_LINE)

    assert "route=model_unavailable" in lines["429"]
    assert "error=ClientError" in lines["429"]
    assert "act= ops= " in lines["429"]

    # No argument value and no message text. `ambiguity` is the model's own free text, logged
    # as D2 says, so it is left out of this check.
    for line in lines.values():
        without_ambiguity = line.split(" ambiguity=")[0]
        assert "servidor" not in without_ambiguity
        assert "Carlos" not in without_ambiguity


# Note (o) of the task 04 review: no retry setting, and the turn node reruns on resume (D1.1).
def test_no_retry_config_and_rerun_on_resume():
    root = assistant.agent.root_agent
    turn_node = root.edges[0][1]

    assert root.retry_config is None
    assert turn_node.retry_config is None
    assert turn_node.rerun_on_resume is True


def _status(code: int, status: str):
    def respond() -> httpx.Response:
        body = {"error": {"code": code, "status": status, "message": "simulated"}}
        return httpx.Response(code, json=body)

    return respond


def _a1_ok() -> httpx.Response:
    text = json.dumps(FIXED["A1"].interpretation)
    candidate = {"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}
    return httpx.Response(200, json={"candidates": [candidate]})


def _connect_error() -> httpx.ConnectError:
    return httpx.ConnectError("connection refused")


_503 = _status(503, "UNAVAILABLE")


# D1.2, end to end without network: a real `Gemini` with the production attempts and codes
# (only the waits are zeroed; test_interpreter_retry_options checks the real ones), over an
# `httpx.MockTransport`, through the `Runner` and the turn node. Each item of `script` answers
# one HTTP request: it returns a response, or an exception that the transport raises.
# `error` is the type the turn log names; None means the turn succeeds and "" means any type.
# The 429 uses "": the real `Gemini` re-raises its `ClientError` as a private ADK subclass
# (google/adk/models/google_llm.py:386-391), whose name this test does not depend on.
@pytest.mark.parametrize(
    ("script", "error"),
    [
        pytest.param([_503, _a1_ok], None, id="503_then_ok"),
        pytest.param([_503, _503], "ServerError", id="503_twice"),
        pytest.param([_status(429, "RESOURCE_EXHAUSTED")], "", id="429"),
        pytest.param([_status(500, "INTERNAL")], "ServerError", id="500"),
        pytest.param([_connect_error, _connect_error], "ConnectError", id="connect_error_twice"),
    ],
)
async def test_http_retry_through_the_turn_node(script, error, caplog):
    caplog.set_level(logging.INFO, logger="assistant")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if len(requests) >= len(script):
            pytest.fail(
                f"HTTP request {len(requests) + 1}, but the script has {len(script)} items",
                pytrace=False,
            )
        result = script[len(requests)]()
        requests.append(request)
        if isinstance(result, BaseException):
            raise result
        return result

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = Gemini(
        model=MODEL,
        retry_options=INTERPRETER_RETRY_OPTIONS.model_copy(
            update={"initial_delay": 0.0, "jitter": 0.0}
        ),
        client_kwargs={
            "api_key": "test-key",
            "vertexai": False,
            "http_options": types.HttpOptions(httpx_async_client=http_client),
        },
    )
    record = None if error is None else a1_record()
    try:
        runner, sessions = make_runner(model)
        if record is not None:
            await sessions.create_session(
                app_name="assistant", user_id=USER, session_id="s-h", state={"pending": record}
            )
        events = await run_turn(runner, USER, "s-h", FIXED["A1"].message)
    finally:
        await http_client.aclose()

    assert len(requests) == len(script)
    answers = reply_events(events)
    assert len(answers) == 1
    session = await sessions.get_session(app_name="assistant", user_id=USER, session_id="s-h")
    [line] = _turn_records(caplog)  # one line per turn, retried or not

    if error is None:
        expected = handle_turn(None, fixed("A1"))
        assert event_text(answers[0]) == expected.reply
        assert session.state["pending"] == expected.state_delta["pending"]
        assert "route=R2" in line
        assert "error=" not in line  # same fields as a turn that was not retried
    else:
        assert event_text(answers[0]) == replies.MODEL_UNAVAILABLE
        assert not answers[0].actions.state_delta
        assert session.state["pending"] == record
        assert f"route=model_unavailable error={error}" in line
