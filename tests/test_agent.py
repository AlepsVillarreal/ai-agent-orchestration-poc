"""The turn node through the real ADK `Runner`, with a scripted fake model (D1.1).

These tests use `InMemorySessionService`; the Redis round trip is in test_session_redis.py.
"""

import dataclasses
import json
import logging
from datetime import datetime

import httpx
import pytest
from google.adk import Runner, Workflow
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.sessions import InMemorySessionService
from google.genai import types

import assistant.agent
from assistant.agent import build_root_agent, message_text
from assistant.interpreter import INTERPRETER_RETRY_OPTIONS, MODEL, build_interpreter
from core import replies
from core.confirmation import shown_values
from core.executions import InMemoryExecutions, execution_key
from core.instruction import NOTHING_PENDING
from core.interpretation import TurnInterpretation
from core.mock_tools import simulated
from core.registry import TOOLS
from core.turn import TurnInput, handle_turn
from tests.fake_llm import (
    ScriptedLlm,
    contents_text,
    event_text,
    quota_error,
    reply_events,
    run_turn,
    system_text,
)
from tests.interpretations import FIXED, NOW, SESSION

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


# The model returns a name that "Alta, asígnalo a Carlos" does not contain (D4 rule 3).
PEDRO_FOR_CARLOS = {
    "act": "provide_info",
    "operations": [
        {
            "tool": "create_ticket",
            "certainty": "explicit",
            "args": {"priority": "Alta", "assignee": "Pedro"},
        }
    ],
}


def fixed(key: str) -> TurnInterpretation:
    return TurnInterpretation.model_validate(FIXED[key].interpretation)


def handle_fixed(pending: dict | None, key: str):
    return handle_turn(pending, fixed(key), TurnInput(FIXED[key].message, NOW, SESSION))


def a1_record() -> dict:
    return handle_fixed(None, "A1").state_delta["pending"]


def without_id(record: dict) -> dict:
    """A record without its `operation_id`, a new uuid on every `handle_turn` call (task 10a)."""
    return {key: value for key, value in record.items() if key != "operation_id"}


def make_runner(fake, sessions=None, executions=None) -> tuple[Runner, InMemorySessionService]:
    sessions = sessions or InMemorySessionService()
    root_agent = build_root_agent(build_interpreter(fake), executions=executions)
    app = App(name="assistant", root_agent=root_agent)
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
    expected = handle_fixed(None, "A1")

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
    assert without_id(session.state["pending"]) == without_id(expected.state_delta["pending"])

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
            json.dumps(FIXED["B1"].interpretation),
            json.dumps(FIXED["B2"].interpretation),
            json.dumps(PEDRO_FOR_CARLOS),
        ]
    )
    runner, _ = make_runner(fake)
    lines = {}
    # (key, session, message): B1 and B2 share a session, so B2 merges into B1's record.
    turns = [
        ("A1", "s-log-A1", FIXED["A1"].message),
        ("C1", "s-log-C1", FIXED["C1"].message),
        ("flight", "s-log-flight", FLIGHT_AND_TICKET[0]),
        ("429", "s-log-429", "Necesito crear un ticket"),
        ("B1", "s-log-B", FIXED["B1"].message),
        ("B2", "s-log-B", FIXED["B2"].message),
        ("rejected", "s-log-rejected", FIXED["B2"].message),
    ]
    answers = {}
    for key, session_id, text in turns:
        caplog.clear()
        events = await run_turn(runner, USER, session_id, text)
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

    # D4 rules 3 and 8: the changes with their values and the rejected names with their key,
    # at the end of the line.
    assert "route=same_tool" in lines["B2"]
    assert (
        'changes=priority:fill:null->"Alta",assignee:fill:null->"Carlos" rejected=' in (lines["B2"])
    )
    assert lines["B2"].endswith("rejected=")
    assert "route=R2" in lines["rejected"]
    assert 'rejected=assignee:"Pedro"' in lines["rejected"]
    assert "changes=" not in lines["429"]  # a failed turn has nothing to report

    # No argument value and no message text before ` changes=`; `changes`, `rejected` and
    # `ambiguity` (the model's own free text, logged as D2 says) come last on purpose.
    for line in lines.values():
        without_values = line.split(" changes=")[0]
        for word in ("servidor", "Carlos", "Pedro", "Alta", "autenticación"):
            assert word not in without_values


def _no_tool_runs(monkeypatch) -> None:
    def fail(args):
        pytest.fail(f"a tool ran with {args}")

    for name, spec in list(TOOLS.items()):
        monkeypatch.setitem(TOOLS, name, dataclasses.replace(spec, execute=fail))


def record_tools(monkeypatch) -> dict[str, list[dict]]:
    """Wrap every tool's `execute` to record its arguments; the real mock still runs (D12)."""
    calls: dict[str, list[dict]] = {name: [] for name in TOOLS}
    for name, spec in list(TOOLS.items()):

        def recorder(args, name=name, real=spec.execute):
            calls[name].append(args)
            return real(args)

        monkeypatch.setitem(TOOLS, name, dataclasses.replace(spec, execute=recorder))
    return calls


def record_results(monkeypatch) -> dict[str, list[dict]]:
    """Wrap every tool's current `execute` to record what it returned (the mock's own ID)."""
    results: dict[str, list[dict]] = {name: [] for name in TOOLS}
    for name, spec in list(TOOLS.items()):

        def recorder(args, name=name, inner=spec.execute):
            results[name].append(inner(args))
            return results[name][-1]

        monkeypatch.setitem(TOOLS, name, dataclasses.replace(spec, execute=recorder))
    return results


# 13 (TASK.md lines 311-335): case B in one session, until every field is in, then a "yes".
# Task 10a: the records are compared without `operation_id` (a new uuid on every call), the
# complete ticket awaits confirmation, and the fourth turn runs `create_ticket` once.
# Task 10b: the fourth turn is case B's own "yes" (`B4`, not `G2`); its reply is the result
# line with the mock's ID, and the closed record goes to `history` (D6).
async def test_case_b_through_the_node(monkeypatch):
    calls = record_tools(monkeypatch)
    results = record_results(monkeypatch)
    keys = ("B1", "B2", "B3")
    script = [json.dumps(FIXED[key].interpretation) for key in (*keys, "B4")]
    fake = ScriptedLlm(script=script)
    runner, sessions = make_runner(fake)

    replies_seen, expected, pending = [], [], None
    for key in keys:
        replies_seen.append(
            event_text(reply_events(await run_turn(runner, USER, "s-b", FIXED[key].message))[0])
        )
        outcome = handle_fixed(pending, key)
        expected.append(outcome.reply)
        pending = outcome.state_delta.get("pending", pending)

    assert replies_seen == expected
    session = await sessions.get_session(app_name="assistant", user_id=USER, session_id="s-b")
    record = session.state["pending"]
    assert without_id(record) == without_id(pending)
    assert record["steps"][0]["args"] == {
        "title": "Falla de autenticación",
        "description": "revisar el problema de autenticación",
        "priority": "Alta",
        "assignee": "Carlos",
    }
    assert record["missing"] == []
    assert record["status"] == "awaiting_confirmation"
    assert replies_seen[-1].endswith(record["last_question"])
    assert all(not args for args in calls.values())  # nothing runs before the "yes"

    events = await run_turn(runner, USER, "s-b", FIXED["B4"].message)

    assert calls["create_ticket"] == [
        {
            "title": "Falla de autenticación",
            "description": "revisar el problema de autenticación",
            "priority": "high",
            "assignee": "Carlos",
        }
    ]
    assert calls["schedule_meeting"] == [] and calls["send_notification"] == []
    [result] = results["create_ticket"]
    reply = event_text(reply_events(events)[0])
    assert reply.startswith("Listo: creé el ticket ")
    assert reply == f"Listo: creé el ticket {result['ticket_id']}."
    session = await sessions.get_session(app_name="assistant", user_id=USER, session_id="s-b")
    assert session.state["pending"] is None
    [entry] = session.state["history"]
    assert entry["operation_id"] == record["operation_id"]
    assert entry["status"] == "completed"
    assert entry["steps"][0]["result"] == result
    assert fake.calls == 4


# 14: with test 13 (Carlos gets in), this shows the core receives the real message text: the
# same message rejects a name it does not contain.
async def test_node_passes_the_message_text():
    fake = ScriptedLlm(
        script=[json.dumps(FIXED["B1"].interpretation), json.dumps(PEDRO_FOR_CARLOS)]
    )
    runner, sessions = make_runner(fake)

    await run_turn(runner, USER, "s-pedro", FIXED["B1"].message)
    events = await run_turn(runner, USER, "s-pedro", FIXED["B2"].message)

    reply = event_text(reply_events(events)[0])
    assert replies.REJECTED_NAME_LINE in reply.split("\n")
    assert "Pedro" not in reply
    session = await sessions.get_session(app_name="assistant", user_id=USER, session_id="s-pedro")
    args = session.state["pending"]["steps"][0]["args"]
    assert args["priority"] == "Alta"
    assert "assignee" not in args
    assert "assignee" in session.state["pending"]["missing"]


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
        expected = handle_fixed(None, "A1")
        assert event_text(answers[0]) == expected.reply
        assert without_id(session.state["pending"]) == without_id(expected.state_delta["pending"])
        assert "route=R2" in line
        assert "error=" not in line  # same fields as a turn that was not retried
    else:
        assert event_text(answers[0]) == replies.MODEL_UNAVAILABLE
        assert not answers[0].actions.state_delta
        assert session.state["pending"] == record
        assert f"route=model_unavailable error={error}" in line


# Review 08-1, should-fix 1, through the node: the D10 line before the repeated question.
async def test_pending_and_unknown_tool_through_the_node():
    record = a1_record()
    flight = {
        "act": "request_action",
        "operations": [{"tool": "book_flight", "certainty": "explicit", "args": {}}],
    }
    fake = ScriptedLlm(script=[json.dumps(flight)])
    runner, sessions = make_runner(fake)
    await sessions.create_session(
        app_name="assistant", user_id=USER, session_id="s-flight", state={"pending": record}
    )

    events = await run_turn(runner, USER, "s-flight", "Mejor reserva un vuelo")

    [answer] = reply_events(events)
    assert event_text(answer) == f"{replies.UNKNOWN_TOOL_LINE}\n{record['last_question']}"
    assert not answer.actions.state_delta
    session = await sessions.get_session(app_name="assistant", user_id=USER, session_id="s-flight")
    assert session.state["pending"] == record


# Review note 5: parts are joined with a space, so two parts never glue two words (D4 rule 3).
def test_message_text_joins_text_parts():
    two = types.Content(role="user", parts=[types.Part(text="con"), types.Part(text="Ana")])
    assert message_text(two) == "con Ana"
    with_empty = types.Content(role="user", parts=[types.Part(text="Ana"), types.Part()])
    assert message_text(with_empty) == "Ana"
    assert message_text(types.Content(role="user")) == ""
    assert message_text({"text": "Ana"}) == ""


# Brief 09a ------------------------------------------------------------------------------------


# 16 (finding 5 of backlog-04): the validator runs in the node's turn; the log names the invalid
# field and its cause, never the value.
async def test_invalid_word_through_the_node(monkeypatch, caplog):
    _no_tool_runs(monkeypatch)
    caplog.set_level(logging.INFO, logger="assistant")
    fake = ScriptedLlm(script=[json.dumps(FIXED["X1"].interpretation)])
    runner, sessions = make_runner(fake)

    events = await run_turn(runner, USER, "s-x", FIXED["X1"].message)

    expected = handle_fixed(None, "X1")
    assert event_text(reply_events(events)[0]) == expected.reply
    session = await sessions.get_session(app_name="assistant", user_id=USER, session_id="s-x")
    record = session.state["pending"]
    assert without_id(record) == without_id(expected.state_delta["pending"])
    assert record["invalid"] == [
        {"tool": "create_ticket", "field": "priority", "cause": "not_allowed"}
    ]
    assert record["missing"] == ["title", "assignee"]
    assert fake.calls == 1

    [line] = _turn_records(caplog)
    before_changes = line.split(" changes=")[0]
    assert "invalid=priority:not_allowed" in before_changes
    assert "urgente" not in before_changes


# 17 became test 15 of brief 09b (`test_meeting_through_the_node`): a complete meeting now
# reaches confirmation, and a "yes" runs it.


# Brief 10a ------------------------------------------------------------------------------------


# 14 (case G, TASK.md lines 424-438): the "yes" runs only the pending tool, once, with the
# normalized values, and empties `pending`; the log ties both turns to the same operation.
async def test_case_g_through_the_node(monkeypatch, caplog):
    calls = record_tools(monkeypatch)
    results = record_results(monkeypatch)
    caplog.set_level(logging.INFO, logger="assistant")
    fake = ScriptedLlm(
        script=[json.dumps(FIXED["G1"].interpretation), json.dumps(FIXED["G2"].interpretation)]
    )
    runner, sessions = make_runner(fake)

    first = await run_turn(runner, USER, "s-g", FIXED["G1"].message)
    [g1_line] = _turn_records(caplog)
    session = await sessions.get_session(app_name="assistant", user_id=USER, session_id="s-g")
    record = session.state["pending"]
    assert record["status"] == "awaiting_confirmation"
    assert event_text(reply_events(first)[0]).endswith(record["last_question"])
    assert all(not args for args in calls.values())
    operation = record["operation_id"]
    assert f"op={operation}" in g1_line and "route=R2" in g1_line

    caplog.clear()
    second = await run_turn(runner, USER, "s-g", FIXED["G2"].message)

    session = await sessions.get_session(app_name="assistant", user_id=USER, session_id="s-g")
    assert session.state["pending"] is None
    assert calls["send_notification"] == [
        {"recipient": "Carlos", "message": "el ticket quedó resuelto", "channel": "email"}
    ]
    assert calls["create_ticket"] == [] and calls["schedule_meeting"] == []
    assert fake.calls == 2
    [answer] = reply_events(second)
    # Task 10b: the result line with the mock's ID, not the mock's `message`.
    [result] = results["send_notification"]
    result_id = result["notification_id"]
    assert event_text(answer) == f"Listo: envié la notificación {result_id}."
    [entry] = session.state["history"]
    assert entry["operation_id"] == operation and entry["status"] == "completed"
    [g2_line] = _turn_records(caplog)
    # Task 11 (decision G): each step with its outcome, then its idempotency key (a hash).
    key = execution_key("s-g", "send_notification", calls["send_notification"][0])
    assert f"route=confirmed results=send_notification:{result_id}:ran idem={key} " in g2_line
    assert f"op={operation}" in g2_line


# Brief 10b ------------------------------------------------------------------------------------


# 9 (D2, D6, D14): two executions in one session; the node passes the stored history (else the
# second would replace the first), and nothing of it reaches the model.
async def test_history_accumulates_and_is_never_read(monkeypatch):
    results = record_results(monkeypatch)
    keys = ("G1", "G2", "H1", "B4", "Q1")
    fake = ScriptedLlm(script=[json.dumps(FIXED[key].interpretation) for key in keys])
    runner, sessions = make_runner(fake)

    for key in keys:
        await run_turn(runner, USER, "s-hist", FIXED[key].message)

    session = await sessions.get_session(app_name="assistant", user_id=USER, session_id="s-hist")
    history = session.state["history"]
    assert [entry["steps"][0]["tool"] for entry in history] == [
        "send_notification",
        "create_ticket",
    ]
    assert history[0]["operation_id"] != history[1]["operation_id"]
    assert session.state["pending"] is None

    [notification], [ticket] = results["send_notification"], results["create_ticket"]
    request = fake.requests[-1]  # Q1, after both executions
    seen = system_text(request) + "\n".join(contents_text(request))
    assert NOTHING_PENDING in system_text(request)
    for text in (
        "Carlos",
        "resuelto",
        "Servidor caído",
        notification["notification_id"],
        ticket["ticket_id"],
    ):
        assert text not in seen, text
    assert fake.calls == 5


# Brief 09b ------------------------------------------------------------------------------------


# 14 (case D, TASK.md lines 361-371): through the `Runner`, with the node's own clock. Day 40 and
# 25:90 are invalid on any day, so the reply is the core's with the fixed `NOW`.
async def test_case_d_through_the_node(monkeypatch, caplog):
    _no_tool_runs(monkeypatch)
    caplog.set_level(logging.INFO, logger="assistant")
    fake = ScriptedLlm(script=[json.dumps(FIXED["D1"].interpretation)])
    runner, sessions = make_runner(fake)

    events = await run_turn(runner, USER, "s-d", FIXED["D1"].message)

    assert event_text(reply_events(events)[0]) == handle_fixed(None, "D1").reply
    session = await sessions.get_session(app_name="assistant", user_id=USER, session_id="s-d")
    assert session.state["pending"]["invalid"] == [
        {"tool": "schedule_meeting", "field": "date", "cause": "nonexistent"},
        {"tool": "schedule_meeting", "field": "time", "cause": "bad_format"},
    ]

    [line] = _turn_records(caplog)
    before_changes = line.split(" changes=")[0]
    assert "invalid=date:nonexistent,time:bad_format" in before_changes
    # `op=` is a random uuid, whose hex digits may contain "40", so it is left out of the check.
    without_op = " ".join(part for part in before_changes.split() if not part.startswith("op="))
    for number in ("40", "25", "90"):
        assert number not in without_op, number


# 15 (replaces test_complete_meeting_does_not_reach_confirmation): a year far ahead, so the
# node's real clock never makes the date past.
async def test_meeting_through_the_node(monkeypatch):
    calls = record_tools(monkeypatch)
    results = record_results(monkeypatch)
    message = (
        "Agenda la revisión del servidor el 20 de noviembre de 2099 a las 10:30, 30 minutos, "
        "con Juan"
    )
    meeting = {
        "act": "request_action",
        "operations": [
            {
                "tool": "schedule_meeting",
                "certainty": "explicit",
                "args": {
                    "title": "revisión del servidor",
                    "date": {"day": 20, "month": 11, "year": 2099},
                    "time": {"hour": 10, "minute": 30},
                    "duration_minutes": 30,
                    "attendees": ["Juan"],
                },
            }
        ],
    }
    fake = ScriptedLlm(script=[json.dumps(meeting), json.dumps(FIXED["G2"].interpretation)])
    runner, sessions = make_runner(fake)

    first = await run_turn(runner, USER, "s-meet", message)
    session = await sessions.get_session(app_name="assistant", user_id=USER, session_id="s-meet")
    record = session.state["pending"]
    assert record["status"] == "awaiting_confirmation"
    assert event_text(reply_events(first)[0]).endswith(record["last_question"])
    assert all(not args for args in calls.values())

    second = await run_turn(runner, USER, "s-meet", FIXED["G2"].message)

    [args] = calls["schedule_meeting"]
    assert (args["date"], args["time"]) == ("2099-11-20", "10:30")
    assert calls["create_ticket"] == [] and calls["send_notification"] == []
    [result] = results["schedule_meeting"]
    assert (
        event_text(reply_events(second)[0]) == f"Listo: programé la reunión {result['meeting_id']}."
    )
    session = await sessions.get_session(app_name="assistant", user_id=USER, session_id="s-meet")
    assert session.state["pending"] is None
    [entry] = session.state["history"]
    assert entry["operation_id"] == record["operation_id"]
    assert entry["steps"][0]["values"]["date"] == "2099-11-20"
    assert fake.calls == 2


# Brief 11 -------------------------------------------------------------------------------------


# 14 (case H, TASK.md lines 441-449): the node uses the registry it was built with, and the key
# carries the session.
async def test_case_h_through_the_node(monkeypatch, caplog):
    calls = record_tools(monkeypatch)
    results = record_results(monkeypatch)
    caplog.set_level(logging.INFO, logger="assistant")
    executions = InMemoryExecutions()
    keys = ("H1", "B4", "H1", "B4", "H1", "B4")
    fake = ScriptedLlm(script=[json.dumps(FIXED[key].interpretation) for key in keys])
    runner, sessions = make_runner(fake, executions=executions)

    answers, lines = [], []
    for key in keys[:4]:
        caplog.clear()
        events = await run_turn(runner, USER, "s-h", FIXED[key].message)
        answers.append(event_text(reply_events(events)[0]))
        lines += _turn_records(caplog)

    assert len(calls["create_ticket"]) == 1
    [result] = results["create_ticket"]
    ticket_id = result["ticket_id"]
    assert answers[1] == f"Listo: creé el ticket {ticket_id}."
    assert answers[3] == f"Ya creé el ticket {ticket_id} con estos mismos datos; no lo repetí."
    key = execution_key("s-h", "create_ticket", calls["create_ticket"][0])
    assert f"results=create_ticket:{ticket_id}:reused idem={key} " in lines[3]
    assert executions.snapshot() == {key: {"status": "done", "result": result}}
    session = await sessions.get_session(app_name="assistant", user_id=USER, session_id="s-h")
    assert [entry["steps"][0]["outcome"] for entry in session.state["history"]] == [
        "ran",
        "reused",
    ]

    for key in keys[4:]:
        await run_turn(runner, USER, "s-h2", FIXED[key].message)

    assert len(calls["create_ticket"]) == 2  # another session: another key
    assert len(executions.snapshot()) == 2
    assert fake.calls == 6


# 15 (note (A2)): the node hands the core the session id and its own clock.
async def test_node_passes_session_and_clock(monkeypatch):
    moment = datetime(2031, 5, 6, 7, 8)

    class FrozenClock:
        @staticmethod
        def now():
            return moment

    seen = []

    def spy(*args, **kwargs):
        seen.append((args, kwargs))
        return handle_turn(*args, **kwargs)

    monkeypatch.setattr(assistant.agent, "datetime", FrozenClock)
    monkeypatch.setattr(assistant.agent, "handle_turn", spy)
    executions = InMemoryExecutions()
    fake = ScriptedLlm(script=[json.dumps(FIXED["A1"].interpretation)])
    runner, _ = make_runner(fake, executions=executions)

    await run_turn(runner, USER, "s-spy", FIXED["A1"].message)

    [(args, kwargs)] = seen
    turn_input = args[2]
    assert turn_input == TurnInput(FIXED["A1"].message, moment, "s-spy")
    assert kwargs["executions"] is executions


# Review 11-1, should-fix 2 (D9.3 "se anota en el log"; D63): the log line of a step that
# failed and of one whose tool raised: `-` for the missing ID, its key, and the exception's type.
@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("failure", "results=create_ticket:-:failed idem={key} invalid="),
        (
            "timeout",
            "results=create_ticket:-:uncertain idem={key} tool_error=TimeoutError invalid=",
        ),
    ],
)
async def test_log_line_of_a_failed_and_an_uncertain_step(monkeypatch, caplog, mode, expected):
    spec = TOOLS["create_ticket"]
    failing = simulated(spec.contract, spec.result_id_field, mode)
    monkeypatch.setitem(TOOLS, "create_ticket", dataclasses.replace(spec, execute=failing))
    caplog.set_level(logging.INFO, logger="assistant")
    fake = ScriptedLlm(script=[json.dumps(FIXED[key].interpretation) for key in ("H1", "B4")])
    runner, _ = make_runner(fake)
    session_id = f"s-log-{mode}"

    await run_turn(runner, USER, session_id, FIXED["H1"].message)
    record = handle_fixed(None, "H1").state_delta["pending"]
    [step] = shown_values(record["steps"], NOW)
    caplog.clear()
    await run_turn(runner, USER, session_id, FIXED["B4"].message)

    [line] = _turn_records(caplog)
    key = execution_key(session_id, "create_ticket", step["normalized"])
    assert f"route=confirmed {expected.format(key=key)}" in line
    assert "None" not in line.split(" changes=")[0]


# Brief 12 -------------------------------------------------------------------------------------


# 12 (case F, TASK.md lines 395-417): the meeting is abandoned, the ticket starts from nothing,
# and the log ties the turn to the new ticket. `F2` as measured and its `provide_info` variant.
@pytest.mark.parametrize("act", ["request_action", "provide_info"])
async def test_case_f_through_the_node(monkeypatch, caplog, act):
    _no_tool_runs(monkeypatch)
    caplog.set_level(logging.INFO, logger="assistant")
    f2 = {**FIXED["F2"].interpretation, "act": act}
    fake = ScriptedLlm(script=[json.dumps(FIXED["F1"].interpretation), json.dumps(f2)])
    runner, sessions = make_runner(fake)

    await run_turn(runner, USER, "s-f", FIXED["F1"].message)
    session = await sessions.get_session(app_name="assistant", user_id=USER, session_id="s-f")
    meeting = session.state["pending"]
    caplog.clear()
    events = await run_turn(runner, USER, "s-f", FIXED["F2"].message)

    assert fake.calls == 2
    session = await sessions.get_session(app_name="assistant", user_id=USER, session_id="s-f")
    ticket = session.state["pending"]
    assert ticket["tools"] == ["create_ticket"]
    assert ticket["steps"] == [
        {"tool": "create_ticket", "args": {"description": "revisar el servidor"}}
    ]
    [entry] = session.state["history"]
    assert entry["operation_id"] == meeting["operation_id"]
    assert entry["status"] == "abandoned"
    assert entry["steps"] == [{"tool": "schedule_meeting", "args": {"attendees": ["Juan"]}}]
    reply = event_text(reply_events(events)[0])
    assert reply.startswith("No voy a programar una reunión; descarté lo que tenía pendiente.\n")
    assert reply.endswith(ticket["last_question"])
    [line] = _turn_records(caplog)
    assert "route=other_tool:R2" in line
    assert f"op={ticket['operation_id']}" in line


# 13 (D7.3, TASK.md line 284): a "cancel" discards the notification; a later "yes" finds nothing.
async def test_cancel_through_the_node(monkeypatch, caplog):
    calls = record_tools(monkeypatch)
    caplog.set_level(logging.INFO, logger="assistant")
    cancel = {"act": "cancel", "operations": []}
    script = [FIXED["G1"].interpretation, cancel, FIXED["G2"].interpretation]
    fake = ScriptedLlm(script=[json.dumps(item) for item in script])
    runner, sessions = make_runner(fake)

    await run_turn(runner, USER, "s-c", FIXED["G1"].message)
    session = await sessions.get_session(app_name="assistant", user_id=USER, session_id="s-c")
    notification = session.state["pending"]
    caplog.clear()
    second = await run_turn(runner, USER, "s-c", "Mejor cancela")

    session = await sessions.get_session(app_name="assistant", user_id=USER, session_id="s-c")
    assert session.state["pending"] is None
    [entry] = session.state["history"]
    assert entry["operation_id"] == notification["operation_id"]
    assert entry["status"] == "cancelled"
    assert entry["steps"] == notification["steps"]
    assert event_text(reply_events(second)[0]) == (
        "No voy a enviar una notificación; descarté lo que tenía pendiente."
    )
    [line] = _turn_records(caplog)
    assert "route=cancelled" in line
    assert f"op={notification['operation_id']}" in line

    third = await run_turn(runner, USER, "s-c", FIXED["G2"].message)

    assert event_text(reply_events(third)[0]) == replies.NO_PENDING_OPERATION
    assert calls["send_notification"] == []
    assert all(not args for args in calls.values())
    assert fake.calls == 3
