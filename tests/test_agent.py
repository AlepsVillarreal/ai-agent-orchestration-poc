"""The turn node through the real ADK `Runner`, with a scripted fake model (D1.1).

These tests use `InMemorySessionService`; the Redis round trip is in test_session_redis.py.
The node's mechanics stay here (log, HTTP retries, session and clock, history, cancel); the
cases of TASK.md §4 moved to tests/test_cases.py in task 15, and the helpers both files share
to tests/node_helpers.py.
"""

import dataclasses
import json
import logging
from datetime import datetime

import httpx
import pytest
from google.adk import Workflow
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

import assistant.agent
from assistant.agent import message_text
from assistant.interpreter import INTERPRETER_RETRY_OPTIONS, MODEL
from core import replies
from core.confirmation import shown_values
from core.executions import InMemoryExecutions, execution_key
from core.instruction import NOTHING_PENDING
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
from tests.interpretations import FIXED, NOW
from tests.node_helpers import (
    USER,
    conversation,
    fail_every_tool,
    freeze_clock,
    handle_fixed,
    make_runner,
    record_results,
    record_tools,
    turn_records,
    without_id,
)

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


def a1_record() -> dict:
    return handle_fixed(None, "A1").state_delta["pending"]


# 1
def test_app_is_where_adk_web_looks():
    app = assistant.agent.app

    assert isinstance(app, App)
    assert app.name == "assistant"
    assert app.root_agent is assistant.agent.root_agent
    assert isinstance(assistant.agent.root_agent, Workflow)
    assert app.resumability_config is None


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
        records = turn_records(caplog)
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
    [line] = turn_records(caplog)  # one line per turn, retried or not

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
    fail_every_tool(monkeypatch)
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

    [line] = turn_records(caplog)
    before_changes = line.split(" changes=")[0]
    assert "invalid=priority:not_allowed" in before_changes
    assert "urgente" not in before_changes


# 17 became test 15 of brief 09b (`test_meeting_through_the_node`): a complete meeting now
# reaches confirmation, and a "yes" runs it.


# Brief 10a ------------------------------------------------------------------------------------


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


# 15 (note (A2)): the node hands the core the session id and its own clock.
async def test_node_passes_session_and_clock(monkeypatch):
    moment = datetime(2031, 5, 6, 7, 8)
    seen = []

    def spy(*args, **kwargs):
        seen.append((args, kwargs))
        return handle_turn(*args, **kwargs)

    freeze_clock(monkeypatch, moment)
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

    [line] = turn_records(caplog)
    key = execution_key(session_id, "create_ticket", step["normalized"])
    assert f"route=confirmed {expected.format(key=key)}" in line
    assert "None" not in line.split(" changes=")[0]


# Brief 12 -------------------------------------------------------------------------------------


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
    [line] = turn_records(caplog)
    assert "route=cancelled" in line
    assert f"op={notification['operation_id']}" in line

    third = await run_turn(runner, USER, "s-c", FIXED["G2"].message)

    assert event_text(reply_events(third)[0]) == replies.NO_PENDING_OPERATION
    assert calls["send_notification"] == []
    assert all(not args for args in calls.values())
    assert fake.calls == 3


# Brief 13 -------------------------------------------------------------------------------------


# Brief 14a ------------------------------------------------------------------------------------


# 16 (D8.4: "el log anota que la dependencia se infirió")
async def test_inferred_dependency_through_the_node(monkeypatch, caplog):
    calls = record_tools(monkeypatch)
    caplog.set_level(logging.INFO, logger="assistant")
    without = json.loads(json.dumps(FIXED["E1"].interpretation))
    del without["operations"][1]["depends_on"]
    fake = ScriptedLlm(script=[json.dumps(FIXED["E1"].interpretation), json.dumps(without)])
    runner, sessions = make_runner(fake)

    message = [FIXED["E1"].message]
    _, [reported], [given] = await conversation(runner, sessions, "s-e-1", message, caplog)
    _, [inferred], [plan] = await conversation(runner, sessions, "s-e-2", message, caplog)

    assert without_id(plan) == without_id(given)
    assert "depends=send_notification<create_ticket:inferred " in inferred
    assert "depends=send_notification<create_ticket " in reported
    assert all(not args for args in calls.values())


# Round 2 of brief 14a (the author's amendment to D3.1): "Cancela la reunión y crea un ticket"
# with a meeting pending archives the meeting and starts only the ticket's flow.
async def test_cancel_and_another_tool_through_the_node(monkeypatch, caplog):
    calls = record_tools(monkeypatch)
    caplog.set_level(logging.INFO, logger="assistant")
    cancel_and_ticket = {
        "act": "cancel",
        "operations": [
            {"tool": "schedule_meeting", "certainty": "explicit", "args": {}},
            {
                "tool": "create_ticket",
                "certainty": "explicit",
                "args": {"description": "revisar el servidor"},
            },
        ],
    }
    script = [FIXED["F1"].interpretation, cancel_and_ticket]
    fake = ScriptedLlm(script=[json.dumps(item) for item in script])
    runner, sessions = make_runner(fake)
    messages = [
        FIXED["F1"].message,
        "Cancela la reunión y crea un ticket para revisar el servidor",
    ]

    answers, lines, records = await conversation(runner, sessions, "s-cx2", messages, caplog)

    meeting, ticket = records
    assert ticket["tools"] == ["create_ticket"]
    assert ticket["steps"] == [
        {"tool": "create_ticket", "args": {"description": "revisar el servidor"}}
    ]
    session = await sessions.get_session(app_name="assistant", user_id=USER, session_id="s-cx2")
    [entry] = session.state["history"]
    assert (entry["operation_id"], entry["status"]) == (meeting["operation_id"], "abandoned")
    assert answers[1].split("\n")[0] == (
        "No voy a programar una reunión; descarté lo que tenía pendiente."
    )
    assert answers[1].endswith(ticket["last_question"])
    assert "route=other_tool:R2 " in lines[1] and "depends=" not in lines[1]
    assert all(not args for args in calls.values())
    assert fake.calls == 2
