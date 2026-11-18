"""The cases of TASK.md §4 (lines 288-451), one group of tests per case, in order A-H.

Every test runs the turns through the real ADK `Runner` and the turn node, with the scripted
fake model (`ScriptedLlm`) answering the fixed interpretations of `tests/interpretations.py`,
and checks each bullet of the case's "Comportamiento esperado" against the literal reply, the
state stored in the session and the calls to each mock tool. The node's clock is fixed at `NOW`
(Tuesday 2026-11-17, 09:00), so no result depends on the day the suite runs.

The last two tests tie `examples/inputs/` to the same fixed turns: each file holds, one per
line, the messages of its case, and runs through the CLI's own loop (`assistant.cli.chat`).
"""

import dataclasses
import io
import json
import logging
from pathlib import Path

import pytest
from google.adk.sessions import InMemorySessionService

from assistant.cli import chat
from core import replies
from core.executions import InMemoryExecutions, execution_key
from core.instruction import NOTHING_PENDING
from core.mock_tools import simulated
from core.registry import TOOLS
from tests.fake_llm import (
    ScriptedLlm,
    contents_text,
    event_text,
    reply_events,
    run_turn,
    system_text,
)
from tests.interpretations import FIXED
from tests.node_helpers import (
    USER,
    call_counts,
    conversation,
    fail_every_tool,
    freeze_clock,
    h1_values,
    make_runner,
    record_results,
    record_tools,
    turn_records,
)

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "inputs"

TICKET_QUESTION = "Para crear un ticket necesito el título, la prioridad y a quién se asigna."
CLARIFICATION_QUESTION = "¿Quieres enviar una notificación, programar una reunión, o ambas?"
G1_CONFIRMATION = (
    "Voy a enviar una notificación con estos datos:\n"
    "- A quién se envía: Carlos\n"
    "- El mensaje: el ticket quedó resuelto\n"
    "- El canal: correo\n"
    "¿Confirmas que quieres enviar una notificación? Responde sí o no."
)
H1_CONFIRMATION = (
    "Voy a crear un ticket con estos datos:\n"
    "- El título: Servidor caído\n"
    "- La descripción: el servidor de producción no responde\n"
    "- La prioridad: alta\n"
    "- A quién se asigna: Carlos\n"
    "¿Confirmas que quieres crear un ticket? Responde sí o no."
)
# The first reply to `G1` and to `H1`: each value noted, then the confirmation.
G1_REPLY = (
    "Anoté a quién se envía: Carlos.\n"
    "Anoté el mensaje: el ticket quedó resuelto.\n"
    "Anoté el canal: correo.\n"
    f"{G1_CONFIRMATION}"
)
H1_REPLY = (
    "Anoté el título: Servidor caído.\n"
    "Anoté la descripción: el servidor de producción no responde.\n"
    "Anoté la prioridad: alta.\n"
    "Anoté a quién se asigna: Carlos.\n"
    f"{H1_CONFIRMATION}"
)
REUSED_H1 = "Ya creé el ticket {} con estos mismos datos; no lo repetí."
UNCERTAIN_H1 = (
    "No pude confirmar si llegué a crear un ticket; no lo voy a reintentar para no duplicarlo."
)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    freeze_clock(monkeypatch)


def script_of(*keys: str) -> list[str]:
    return [json.dumps(FIXED[key].interpretation) for key in keys]


async def stored(sessions, session_id: str) -> dict:
    session = await sessions.get_session(app_name="assistant", user_id=USER, session_id=session_id)
    return session.state


async def reply_to(runner, session_id: str, message: str) -> str:
    [answer] = reply_events(await run_turn(runner, USER, session_id, message))
    return event_text(answer)


# Case A ---------------------------------------------------------------------------------------


# Case A (TASK.md lines 292-308): `create_ticket` is identified, the description extracted,
# title, priority and assignee asked for in one question, and the tool never runs. The model
# sees only the message and the "nothing pending" summary (D1.1).
# Task 15: the reply and the record are literals, no longer compared with `handle_turn`.
async def test_case_a_end_to_end_through_the_node(monkeypatch):
    fail_every_tool(monkeypatch)  # "No ejecutar la herramienta todavía"
    fake = ScriptedLlm(script=script_of("A1"))
    runner, sessions = make_runner(fake)

    events = await run_turn(runner, USER, "s-a", FIXED["A1"].message)

    answers = reply_events(events)
    assert len(answers) == 1
    assert event_text(answers[0]) == (
        f"Anoté la descripción: el servidor está caído.\n{TICKET_QUESTION}"
    )
    record = (await stored(sessions, "s-a"))["pending"]
    assert record["tools"] == ["create_ticket"]  # "Identificar create_ticket"
    # "Extraer la descripción"
    assert record["steps"] == [
        {"tool": "create_ticket", "args": {"description": "el servidor está caído"}}
    ]
    assert record["missing"] == ["title", "priority", "assignee"]  # "Detectar … faltantes"
    assert record["invalid"] == []
    assert record["status"] == "collecting"
    assert record["last_question"] == TICKET_QUESTION  # "Solicitar la información faltante"

    assert fake.calls == 1
    request = fake.requests[0]
    assert contents_text(request) == [FIXED["A1"].message]  # include_contents='none'
    assert NOTHING_PENDING in system_text(request)


# Case B ---------------------------------------------------------------------------------------


# Case B (TASK.md lines 311-335): in one session, until every field is in, then a "yes".
# The question after `B1` names the title too (F4, D17: the title is never derived), and the
# conversation ends with a confirmation and `B4` (D7.1). Task 15: each reply is a literal (no
# longer compared with `handle_turn`), and the record is checked after every turn.
async def test_case_b_through_the_node(monkeypatch):
    calls = record_tools(monkeypatch)
    results = record_results(monkeypatch)
    fake = ScriptedLlm(script=script_of("B1", "B2", "B3", "B4"))
    runner, sessions = make_runner(fake)
    description = "revisar el problema de autenticación"

    b1 = await reply_to(runner, "s-b", FIXED["B1"].message)
    assert b1 == f"Anoté la descripción: {description}.\n{TICKET_QUESTION}"
    first = (await stored(sessions, "s-b"))["pending"]
    assert first["tools"] == ["create_ticket"]

    b2 = await reply_to(runner, "s-b", FIXED["B2"].message)
    assert b2 == (
        "Anoté la prioridad: Alta.\n"
        "Anoté a quién se asigna: Carlos.\n"
        "Para crear un ticket necesito el título."
    )
    second = (await stored(sessions, "s-b"))["pending"]
    assert second["tools"] == ["create_ticket"]  # "Mantener la intención original"
    assert second["operation_id"] == first["operation_id"]
    assert second["steps"][0]["args"]["description"] == description  # "Conservar la descripción"
    assert second["missing"] == ["title"]  # "Incorporar los nuevos argumentos"

    b3 = await reply_to(runner, "s-b", FIXED["B3"].message)
    assert b3 == (
        "Anoté el título: Falla de autenticación.\n"
        "Voy a crear un ticket con estos datos:\n"
        "- El título: Falla de autenticación\n"
        f"- La descripción: {description}\n"
        "- La prioridad: alta\n"  # "Validar nuevamente": normalized and shown
        "- A quién se asigna: Carlos\n"
        "¿Confirmas que quieres crear un ticket? Responde sí o no."
    )
    record = (await stored(sessions, "s-b"))["pending"]
    assert record["tools"] == ["create_ticket"]
    assert record["steps"][0]["args"] == {
        "title": "Falla de autenticación",
        "description": description,
        "priority": "Alta",
        "assignee": "Carlos",
    }
    assert record["missing"] == [] and record["invalid"] == []
    assert record["status"] == "awaiting_confirmation"
    assert record["operation_id"] == first["operation_id"]
    assert b3.endswith(record["last_question"])
    assert all(not args for args in calls.values())  # nothing runs before the "yes"

    events = await run_turn(runner, USER, "s-b", FIXED["B4"].message)

    # "Ejecutar únicamente cuando todos los campos estén completos": once, normalized.
    assert calls["create_ticket"] == [
        {
            "title": "Falla de autenticación",
            "description": description,
            "priority": "high",
            "assignee": "Carlos",
        }
    ]
    assert call_counts(calls) == {"create_ticket": 1, "schedule_meeting": 0, "send_notification": 0}
    [result] = results["create_ticket"]
    reply = event_text(reply_events(events)[0])
    assert reply.startswith("Listo: creé el ticket ")
    assert reply == f"Listo: creé el ticket {result['ticket_id']}."
    state = await stored(sessions, "s-b")
    assert state["pending"] is None
    [entry] = state["history"]
    assert entry["operation_id"] == record["operation_id"]
    assert entry["status"] == "completed"
    assert entry["steps"][0]["result"] == result
    assert fake.calls == 4


# Case C ---------------------------------------------------------------------------------------


# Case C (a) (TASK.md lines 338-355): the ambiguous message stores a clarification and asks it,
# and nothing runs "solamente por asociación semántica" (line 352).
async def test_case_c_asks_for_clarification(monkeypatch):
    fail_every_tool(monkeypatch)
    fake = ScriptedLlm(script=script_of("C1"))
    runner, sessions = make_runner(fake)

    reply = await reply_to(runner, "s-ca", FIXED["C1"].message)

    assert reply == CLARIFICATION_QUESTION
    record = (await stored(sessions, "s-ca"))["pending"]
    assert record["status"] == "awaiting_clarification"
    assert [c["tool"] for c in record["candidates"]] == ["send_notification", "schedule_meeting"]
    assert fake.calls == 1


# Case C (b) (TASK.md lines 338-355, 282-284 and 436): the clarification is stored and asked;
# "solo avísale" starts the notification with what C1 said; "por correo" (`C2`, task 15; it was
# written inside this test) completes it; the "yes" runs only the notification. The log ties
# the first two turns to the same operation.
async def test_case_c_through_the_node(monkeypatch, caplog):
    calls = record_tools(monkeypatch)
    results = record_results(monkeypatch)
    caplog.set_level(logging.INFO, logger="assistant")
    keys = ("C1", "K2", "C2", "G2")
    fake = ScriptedLlm(script=script_of(*keys))
    runner, sessions = make_runner(fake)
    messages = [FIXED[key].message for key in keys]

    answers, lines, records = await conversation(runner, sessions, "s-cc", messages, caplog)

    assert fake.calls == 4
    clarification, notice, ready, closed = records
    assert answers[0] == CLARIFICATION_QUESTION
    assert clarification["status"] == "awaiting_clarification"
    assert [c["tool"] for c in clarification["candidates"]] == [
        "send_notification",
        "schedule_meeting",
    ]
    assert answers[1].split("\n")[:2] == [
        "Anoté a quién se envía: Carlos.",
        "Anoté el mensaje: mañana revisamos el servidor.",
    ]
    assert answers[1].endswith(notice["last_question"])
    assert notice["missing"] == ["channel"]
    assert ready["status"] == "awaiting_confirmation"
    assert answers[2].endswith(ready["last_question"])
    assert closed is None
    [result] = results["send_notification"]
    assert answers[3] == f"Listo: envié la notificación {result['notification_id']}."
    assert calls["send_notification"] == [
        {"recipient": "Carlos", "message": "mañana revisamos el servidor", "channel": "email"}
    ]
    assert calls["schedule_meeting"] == [] and calls["create_ticket"] == []

    operation = clarification["operation_id"]
    assert notice["operation_id"] == operation
    routes = ["route=R4 ", "route=clarification:regla_2 ", "route=same_tool ", "route=confirmed "]
    for line, route in zip(lines, routes, strict=True):
        assert route in line, line
    assert f"op={operation}" in lines[0] and f"op={operation}" in lines[1]


# Case C (c) (TASK.md line 348): "solo la reunión" starts the meeting with the date and the
# attendee C1 gave, asks for the rest and runs nothing.
async def test_case_c_only_the_meeting(monkeypatch):
    fail_every_tool(monkeypatch)
    fake = ScriptedLlm(script=script_of("C1", "K4"))
    runner, sessions = make_runner(fake)

    await reply_to(runner, "s-cm", FIXED["C1"].message)
    clarification = (await stored(sessions, "s-cm"))["pending"]
    reply = await reply_to(runner, "s-cm", FIXED["K4"].message)

    assert reply.startswith("Anoté la fecha: mañana.\nAnoté los asistentes: Carlos.\n")
    record = (await stored(sessions, "s-cm"))["pending"]
    assert record["tools"] == ["schedule_meeting"]
    assert record["operation_id"] == clarification["operation_id"]
    [step] = record["steps"]
    assert step["args"] == {"date": {"relative_days": 1}, "attendees": ["Carlos"]}
    assert record["missing"] == ["title", "time", "duration_minutes"]
    assert record["status"] == "collecting"
    assert reply.endswith(record["last_question"])
    assert fake.calls == 2


# Case C (d) ((A17); the third reading, TASK.md lines 344-350): "las dos" forms the plan, and
# nothing runs before its confirmation.
async def test_case_c_both_through_the_node(monkeypatch, caplog):
    calls = record_tools(monkeypatch)
    caplog.set_level(logging.INFO, logger="assistant")
    keys = ("C1", "K1")
    fake = ScriptedLlm(script=script_of(*keys))
    runner, sessions = make_runner(fake)

    messages = [FIXED[key].message for key in keys]
    answers, lines, records = await conversation(runner, sessions, "s-cb", messages, caplog)

    clarification, plan = records
    assert answers[1].split("\n")[:4] == [
        "Anoté a quién se envía: Carlos.",
        "Anoté el mensaje: mañana revisamos el servidor.",
        "Anoté la fecha: mañana.",
        "Anoté los asistentes: Carlos.",
    ]
    assert answers[1].endswith(plan["last_question"])
    assert plan["tools"] == ["send_notification", "schedule_meeting"]
    assert "candidates" not in plan
    assert plan["operation_id"] == clarification["operation_id"]
    assert "route=clarification:regla_2 depends= invalid=" in lines[1]
    assert f"op={clarification['operation_id']}" in lines[1]
    assert all(not args for args in calls.values())
    assert fake.calls == 2


# Case C (e) (TASK.md lines 134 and 353): a lone "Sí" to the clarification authorizes nothing:
# the question is repeated and the stored record does not change (D3 rule 5).
async def test_case_c_lone_yes_repeats_the_question(monkeypatch):
    fail_every_tool(monkeypatch)
    fake = ScriptedLlm(script=script_of("C1", "S1"))
    runner, sessions = make_runner(fake)

    await reply_to(runner, "s-cs", FIXED["C1"].message)
    clarification = (await stored(sessions, "s-cs"))["pending"]
    reply = await reply_to(runner, "s-cs", FIXED["S1"].message)

    assert reply == CLARIFICATION_QUESTION
    assert (await stored(sessions, "s-cs"))["pending"] == clarification
    assert fake.calls == 2


# Case C, "no" (D3 rule 1, TASK.md line 284): a "no" to the clarification discards it; a later
# "yes" finds nothing to run. Task 15: renamed from test_clarification_cancel_through_the_node.
async def test_case_c_no_through_the_node(monkeypatch):
    calls = record_tools(monkeypatch)
    fake = ScriptedLlm(script=script_of("C1", "S2", "G2"))
    runner, sessions = make_runner(fake)

    await run_turn(runner, USER, "s-cx", FIXED["C1"].message)
    clarification = (await stored(sessions, "s-cx"))["pending"]
    second = await run_turn(runner, USER, "s-cx", FIXED["S2"].message)

    state = await stored(sessions, "s-cx")
    assert state["pending"] is None
    [entry] = state["history"]
    assert entry == {
        "operation_id": clarification["operation_id"],
        "status": "cancelled",
        "steps": [{"tool": c["tool"], "args": c["args"]} for c in clarification["candidates"]],
    }
    assert len(entry["steps"]) == 2
    assert event_text(reply_events(second)[0]) == (
        "No inicié ninguna acción. Dime qué necesitas cuando quieras."
    )

    third = await run_turn(runner, USER, "s-cx", FIXED["G2"].message)

    assert event_text(reply_events(third)[0]) == replies.NO_PENDING_OPERATION
    assert all(not args for args in calls.values())
    assert fake.calls == 3


# Case D ---------------------------------------------------------------------------------------


# Case D (TASK.md lines 357-371 and 186-200): `schedule_meeting` identified, the invalid date
# and time detected and reported (the sentences of line 198, without its header: (A1), D56),
# a correction asked for, nothing run. Task 15: the reply is a literal (no longer compared with
# `handle_turn`), and the node's clock is fixed.
async def test_case_d_through_the_node(monkeypatch, caplog):
    fail_every_tool(monkeypatch)
    caplog.set_level(logging.INFO, logger="assistant")
    fake = ScriptedLlm(script=script_of("D1"))
    runner, sessions = make_runner(fake)

    events = await run_turn(runner, USER, "s-d", FIXED["D1"].message)

    assert event_text(reply_events(events)[0]) == (
        "Anoté la fecha: día 40, mes 11.\n"
        "Anoté la hora: 25:90.\n"
        "La fecha indicada no existe.\n"
        "La hora está fuera del formato válido.\n"
        "Para programar una reunión necesito el título, la fecha, la hora, la duración en "
        "minutos y los asistentes."
    )
    record = (await stored(sessions, "s-d"))["pending"]
    assert record["tools"] == ["schedule_meeting"]
    assert record["invalid"] == [
        {"tool": "schedule_meeting", "field": "date", "cause": "nonexistent"},
        {"tool": "schedule_meeting", "field": "time", "cause": "bad_format"},
    ]

    [line] = turn_records(caplog)
    before_changes = line.split(" changes=")[0]
    assert "invalid=date:nonexistent,time:bad_format" in before_changes
    # `op=` is a random uuid, whose hex digits may contain "40", so it is left out of the check.
    without_op = " ".join(part for part in before_changes.split() if not part.startswith("op="))
    for number in ("40", "25", "90"):
        assert number not in without_op, number


# Case D, the correction (TASK.md lines 180, 188 and 369): each value is replaced whole and
# validated again; the date and the time are no longer invalid, and nothing runs.
async def test_case_d_correction_is_validated_again(monkeypatch):
    fail_every_tool(monkeypatch)
    fake = ScriptedLlm(script=script_of("D1", "D2"))
    runner, sessions = make_runner(fake)

    await reply_to(runner, "s-dc", FIXED["D1"].message)
    reply = await reply_to(runner, "s-dc", FIXED["D2"].message)

    assert reply == (
        "Cambié la fecha: día 40, mes 11 → día 20, mes 11.\n"
        "Cambié la hora: 25:90 → 10:30.\n"
        "Para programar una reunión necesito el título, la duración en minutos y los asistentes."
    )
    record = (await stored(sessions, "s-dc"))["pending"]
    assert record["invalid"] == []
    assert record["missing"] == ["title", "duration_minutes", "attendees"]
    assert record["status"] == "collecting"
    assert fake.calls == 2


# Case E ---------------------------------------------------------------------------------------

E1_QUESTION = (
    "Para crear un ticket necesito el título y la descripción. "
    "Para enviar una notificación necesito el mensaje."
)
ID_LINK_LINE = "El mensaje llevará al final el número de ticket."


# Case E (TASK.md lines 374-392), task 14b's test 11: the plan's "yes" (`E3`) runs the ticket,
# then the notice with the ticket's number at the end of its message, and reports both.
# Task 15 ((A29)): the calls are counted by tool name, not in the registry's order.
async def test_case_e_through_the_node(monkeypatch, caplog):
    calls = record_tools(monkeypatch)
    results = record_results(monkeypatch)
    caplog.set_level(logging.INFO, logger="assistant")
    keys = ("E1", "E2", "E3")
    fake = ScriptedLlm(script=script_of(*keys))
    runner, sessions = make_runner(fake)

    messages = [FIXED[key].message for key in keys]
    answers, lines, records = await conversation(runner, sessions, "s-ce", messages, caplog)

    assert fake.calls == 3
    plan, ready, after = records
    assert answers[0].endswith(E1_QUESTION)
    assert plan["tools"] == ["create_ticket", "send_notification"]
    assert plan["steps"][1]["depends_on"] == "create_ticket"
    assert plan["missing"] == ["title", "description", "message"]
    assert ready["status"] == "awaiting_confirmation"
    assert ID_LINK_LINE in answers[1].split("\n")
    assert answers[1].endswith(ready["last_question"])

    [ticket], [notice] = results["create_ticket"], results["send_notification"]
    ticket_id, notice_id = ticket["ticket_id"], notice["notification_id"]
    assert answers[2] == (
        f"Listo: creé el ticket {ticket_id}.\nListo: envié la notificación {notice_id}."
    )
    # "Extraer sus argumentos" (TASK.md line 384): what each tool received, normalized.
    assert calls["create_ticket"] == [
        {
            "title": "Servidor caído",
            "description": "el servidor de producción no responde",
            "priority": "high",
            "assignee": "Carlos",
        }
    ]
    [notice_args] = calls["send_notification"]
    assert notice_args == {
        "recipient": "Carlos",
        "message": f"ya se registró la falla (ticket {ticket_id})",
        "channel": "email",
    }
    assert call_counts(calls) == {"create_ticket": 1, "schedule_meeting": 0, "send_notification": 1}
    assert after is None
    [entry] = (await stored(sessions, "s-ce"))["history"]
    assert entry["operation_id"] == plan["operation_id"] and entry["status"] == "completed"
    assert [(step["tool"], step["outcome"]) for step in entry["steps"]] == [
        ("create_ticket", "ran"),
        ("send_notification", "ran"),
    ]

    assert "route=R3 depends=send_notification<create_ticket invalid=" in lines[0]
    assert "route=same_tool " in lines[1] and "depends=" not in lines[1]
    ticket_key = execution_key("s-ce", "create_ticket", calls["create_ticket"][0])
    notice_key = execution_key("s-ce", "send_notification", notice_args)
    assert (
        f"route=confirmed results=create_ticket:{ticket_id}:ran,"
        f"send_notification:{notice_id}:ran idem={ticket_key},{notice_key} "
    ) in lines[2]
    assert "depends=" not in lines[2]
    for line in lines:
        assert f"op={plan['operation_id']}" in line


def failing(monkeypatch, name: str) -> None:
    """`name` answers a failure, as `MOCK_FAIL` makes it answer."""
    spec = TOOLS[name]
    failure = simulated(spec.contract, spec.result_id_field, "failure")
    monkeypatch.setitem(TOOLS, name, dataclasses.replace(spec, execute=failure))


# Case E, the second step fails (task 14b's test 12; D8.3; TASK.md line 270): the notice fails,
# as `MOCK_FAIL` makes it fail. Task 15: renamed from test_partial_failure_through_the_node,
# with the calls counted by tool name ((A29)).
async def test_case_e_second_step_fails_through_the_node(monkeypatch, caplog):
    failing(monkeypatch, "send_notification")
    calls = record_tools(monkeypatch)
    results = record_results(monkeypatch)
    caplog.set_level(logging.INFO, logger="assistant")
    keys = ("E1", "E2", "E3")
    fake = ScriptedLlm(script=script_of(*keys))
    runner, sessions = make_runner(fake)

    messages = [FIXED[key].message for key in keys]
    answers, lines, _ = await conversation(runner, sessions, "s-ce-fail", messages, caplog)

    [ticket] = results["create_ticket"]
    ticket_id = ticket["ticket_id"]
    assert answers[2] == (
        f"Listo: creé el ticket {ticket_id}.\n"
        "No pude enviar una notificación: la herramienta respondió con un error. "
        "Si me lo pides de nuevo, lo intento otra vez."
    )
    assert len(calls["send_notification"]) == 1
    assert call_counts(calls) == {"create_ticket": 1, "schedule_meeting": 0, "send_notification": 1}
    assert (
        f"route=confirmed results=create_ticket:{ticket_id}:ran,send_notification:-:failed idem="
    ) in lines[2]
    state = await stored(sessions, "s-ce-fail")
    [entry] = state["history"]
    assert entry["status"] == "failed"
    assert [(step["tool"], step["outcome"]) for step in entry["steps"]] == [
        ("create_ticket", "ran"),
        ("send_notification", "failed"),
    ]
    [notice_args] = calls["send_notification"]
    assert notice_args["message"] == f"ya se registró la falla (ticket {ticket_id})"
    assert state["pending"] is None


# Case E, the first step fails (TASK.md line 270: "si la primera falla, la segunda no debe
# ejecutarse"; D8.3, D89): the ticket fails, the notice is never tried and says so.
async def test_case_e_first_step_fails_through_the_node(monkeypatch, caplog):
    failing(monkeypatch, "create_ticket")
    calls = record_tools(monkeypatch)
    caplog.set_level(logging.INFO, logger="assistant")
    keys = ("E1", "E2", "E3")
    fake = ScriptedLlm(script=script_of(*keys))
    runner, sessions = make_runner(fake)

    messages = [FIXED[key].message for key in keys]
    answers, _, _ = await conversation(runner, sessions, "s-ce-fail1", messages, caplog)

    assert answers[2] == (
        "No pude crear un ticket: la herramienta respondió con un error. "
        "Si me lo pides de nuevo, lo intento otra vez.\n"
        "No intenté enviar una notificación porque un paso anterior no se completó."
    )
    assert call_counts(calls) == {"create_ticket": 1, "schedule_meeting": 0, "send_notification": 0}
    state = await stored(sessions, "s-ce-fail1")
    [entry] = state["history"]
    assert entry["status"] == "failed"
    assert [(step["tool"], step["outcome"]) for step in entry["steps"]] == [
        ("create_ticket", "failed"),
        ("send_notification", "skipped"),
    ]
    assert state["pending"] is None


# Case F ---------------------------------------------------------------------------------------


# Case F (TASK.md lines 395-417): the meeting is abandoned, the ticket starts from nothing,
# and the log ties the turn to the new ticket. `F2` as measured and its `provide_info` variant.
# Task 15: both replies are literals. After `F1` the question names the four missing fields,
# not only "¿Qué día y a qué hora?" (line 403): D17 and line 184, explained by task 20.
@pytest.mark.parametrize("act", ["request_action", "provide_info"])
async def test_case_f_through_the_node(monkeypatch, caplog, act):
    fail_every_tool(monkeypatch)  # "No ejecutar la reunión incompleta"
    caplog.set_level(logging.INFO, logger="assistant")
    f2 = {**FIXED["F2"].interpretation, "act": act}
    fake = ScriptedLlm(script=[json.dumps(FIXED["F1"].interpretation), json.dumps(f2)])
    runner, sessions = make_runner(fake)

    first = await reply_to(runner, "s-f", FIXED["F1"].message)
    assert first == (
        "Anoté los asistentes: Juan.\n"
        "Para programar una reunión necesito el título, la fecha, la hora y la duración en "
        "minutos."
    )
    meeting = (await stored(sessions, "s-f"))["pending"]
    caplog.clear()
    events = await run_turn(runner, USER, "s-f", FIXED["F2"].message)

    assert fake.calls == 2
    state = await stored(sessions, "s-f")
    ticket = state["pending"]
    assert ticket["tools"] == ["create_ticket"]  # "Iniciar el nuevo flujo"
    # "No mezclar los argumentos": nothing of the meeting, `attendees` included.
    assert ticket["steps"] == [
        {"tool": "create_ticket", "args": {"description": "revisar el servidor"}}
    ]
    [entry] = state["history"]
    assert entry["operation_id"] == meeting["operation_id"]
    assert entry["status"] == "abandoned"  # "Detectar el cambio de intención"
    assert entry["steps"] == [{"tool": "schedule_meeting", "args": {"attendees": ["Juan"]}}]
    reply = event_text(reply_events(events)[0])
    assert reply.startswith("No voy a programar una reunión; descarté lo que tenía pendiente.\n")
    assert reply.endswith(ticket["last_question"])
    assert reply == (
        "No voy a programar una reunión; descarté lo que tenía pendiente.\n"
        "Anoté la descripción: revisar el servidor.\n"
        f"{TICKET_QUESTION}"
    )
    [line] = turn_records(caplog)
    assert "route=other_tool:R2" in line
    assert f"op={ticket['operation_id']}" in line


# Case G ---------------------------------------------------------------------------------------

# A "yes" that names another tool than the pending one (own wording, task 15; D7.4).
YES_NAMING_A_TICKET = (
    "Sí, crea el ticket",
    {"act": "confirm", "operations": [], "confirms_tool": "create_ticket"},
)


# Case G (TASK.md lines 420-438): the "yes" runs only the pending tool, once, with the
# normalized values, and empties `pending`; the log ties both turns to the same operation.
# Task 15: after `G1` nothing is asked again ("No solicitar nuevamente información").
async def test_case_g_through_the_node(monkeypatch, caplog):
    calls = record_tools(monkeypatch)
    results = record_results(monkeypatch)
    caplog.set_level(logging.INFO, logger="assistant")
    fake = ScriptedLlm(script=script_of("G1", "G2"))
    runner, sessions = make_runner(fake)

    first = await run_turn(runner, USER, "s-g", FIXED["G1"].message)
    [g1_line] = turn_records(caplog)
    record = (await stored(sessions, "s-g"))["pending"]
    assert record["status"] == "awaiting_confirmation"
    assert record["missing"] == []
    first_reply = event_text(reply_events(first)[0])
    assert "necesito" not in first_reply
    assert first_reply == G1_REPLY
    assert first_reply.endswith(record["last_question"])
    assert all(not args for args in calls.values())
    operation = record["operation_id"]
    assert f"op={operation}" in g1_line and "route=R2" in g1_line

    caplog.clear()
    second = await run_turn(runner, USER, "s-g", FIXED["G2"].message)

    state = await stored(sessions, "s-g")
    assert state["pending"] is None
    # "Ejecutar únicamente la herramienta correspondiente"
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
    [entry] = state["history"]
    assert entry["operation_id"] == operation and entry["status"] == "completed"
    [g2_line] = turn_records(caplog)
    # Task 11 (decision G): each step with its outcome, then its idempotency key (a hash).
    key = execution_key("s-g", "send_notification", calls["send_notification"][0])
    assert f"route=confirmed results=send_notification:{result_id}:ran idem={key} " in g2_line
    assert f"op={operation}" in g2_line


# Case G, the "yes" is not a generic authorization (TASK.md lines 282-284, D7.4): a "yes" that
# names another tool repeats the confirmation and runs nothing; the next plain "yes" runs only
# the notification.
async def test_case_g_yes_naming_another_tool_runs_nothing(monkeypatch):
    calls = record_tools(monkeypatch)
    script = [json.dumps(FIXED["G1"].interpretation), json.dumps(YES_NAMING_A_TICKET[1])]
    fake = ScriptedLlm(script=[*script, json.dumps(FIXED["G2"].interpretation)])
    runner, sessions = make_runner(fake)

    await reply_to(runner, "s-gv", FIXED["G1"].message)
    record = (await stored(sessions, "s-gv"))["pending"]
    vetoed = await reply_to(runner, "s-gv", YES_NAMING_A_TICKET[0])

    assert vetoed == G1_CONFIRMATION
    assert (await stored(sessions, "s-gv"))["pending"] == record
    assert all(not args for args in calls.values())

    await reply_to(runner, "s-gv", FIXED["G2"].message)

    assert call_counts(calls) == {"create_ticket": 0, "schedule_meeting": 0, "send_notification": 1}
    assert fake.calls == 3


# Case G, nothing pending (TASK.md line 284): a "Sí, hazlo" in a new session runs nothing.
async def test_case_g_yes_with_nothing_pending(monkeypatch):
    fail_every_tool(monkeypatch)
    fake = ScriptedLlm(script=script_of("G2"))
    runner, _ = make_runner(fake)

    assert await reply_to(runner, "s-gn", FIXED["G2"].message) == (
        "No hay ninguna operación pendiente."
    )
    assert fake.calls == 1


# Case H ---------------------------------------------------------------------------------------


# Case H (a) (TASK.md line 447; D9 *Caso H*): the same request twice while it awaits
# confirmation changes nothing: the confirmation again, without any "Anoté…" line.
async def test_case_h_repeated_while_pending(monkeypatch):
    fail_every_tool(monkeypatch)
    fake = ScriptedLlm(script=script_of("H1", "H1"))
    runner, sessions = make_runner(fake)

    first = await reply_to(runner, "s-hp", FIXED["H1"].message)
    record = (await stored(sessions, "s-hp"))["pending"]
    second = await reply_to(runner, "s-hp", FIXED["H1"].message)

    assert first == H1_REPLY
    assert second == H1_CONFIRMATION
    assert "Anoté" not in second
    assert (await stored(sessions, "s-hp"))["pending"] == record  # same `operation_id`
    assert fake.calls == 2


# Case H (b) (TASK.md lines 441-449): the same request after it ran gives the stored ticket,
# and the key carries the session, so another session creates its own (D62).
async def test_case_h_through_the_node(monkeypatch, caplog):
    calls = record_tools(monkeypatch)
    results = record_results(monkeypatch)
    caplog.set_level(logging.INFO, logger="assistant")
    executions = InMemoryExecutions()
    keys = ("H1", "B4", "H1", "B4", "H1", "B4")
    fake = ScriptedLlm(script=script_of(*keys))
    runner, sessions = make_runner(fake, executions=executions)

    answers, lines = [], []
    for key in keys[:4]:
        caplog.clear()
        events = await run_turn(runner, USER, "s-h", FIXED[key].message)
        answers.append(event_text(reply_events(events)[0]))
        lines += turn_records(caplog)

    assert len(calls["create_ticket"]) == 1
    [result] = results["create_ticket"]
    ticket_id = result["ticket_id"]
    assert answers[1] == f"Listo: creé el ticket {ticket_id}."
    assert answers[3] == REUSED_H1.format(ticket_id)
    key = execution_key("s-h", "create_ticket", calls["create_ticket"][0])
    assert f"results=create_ticket:{ticket_id}:reused idem={key} " in lines[3]
    assert executions.snapshot() == {key: {"status": "done", "result": result}}
    state = await stored(sessions, "s-h")
    assert [entry["steps"][0]["outcome"] for entry in state["history"]] == ["ran", "reused"]

    for key in keys[4:]:
        await run_turn(runner, USER, "s-h2", FIXED[key].message)

    assert len(calls["create_ticket"]) == 2  # another session: another key
    assert len(executions.snapshot()) == 2
    assert fake.calls == 6


# Case H (c) (TASK.md line 443, "un reintento después de un timeout"): the model times out on
# the first try; nothing changes (D1.1, D23), so the retry starts from the same state.
async def test_case_h_retry_after_a_model_timeout(monkeypatch):
    fail_every_tool(monkeypatch)
    fake = ScriptedLlm(script=[TimeoutError("timed out"), json.dumps(FIXED["H1"].interpretation)])
    runner, sessions = make_runner(fake)

    failed = await reply_to(runner, "s-ht", FIXED["H1"].message)

    assert failed == (
        "El modelo no está disponible ahora; no cambié nada. Intenta de nuevo en un momento."
    )
    assert (await stored(sessions, "s-ht")).get("pending") is None

    retried = await reply_to(runner, "s-ht", FIXED["H1"].message)

    assert retried == H1_REPLY
    assert (await stored(sessions, "s-ht"))["pending"]["status"] == "awaiting_confirmation"
    assert fake.calls == 2


# Case H (d) (TASK.md line 443; D9 *Caso H, dos momentos*): the turn that ran was not saved, so
# the "yes" is resent on the record from before it (a second session service seeded with that
# record, the same execution registry): the stored ticket comes back and nothing runs again.
async def test_case_h_resent_yes_after_an_unsaved_turn(monkeypatch):
    calls = record_tools(monkeypatch)
    results = record_results(monkeypatch)
    executions = InMemoryExecutions()
    fake = ScriptedLlm(script=script_of("H1", "B4"))
    runner, sessions = make_runner(fake, executions=executions)

    await reply_to(runner, "s-hr", FIXED["H1"].message)
    before_yes = (await stored(sessions, "s-hr"))["pending"]
    await reply_to(runner, "s-hr", FIXED["B4"].message)
    assert len(calls["create_ticket"]) == 1
    [result] = results["create_ticket"]

    unsaved = InMemorySessionService()
    await unsaved.create_session(
        app_name="assistant", user_id=USER, session_id="s-hr", state={"pending": before_yes}
    )
    resent_fake = ScriptedLlm(script=script_of("B4"))
    resent_runner, _ = make_runner(resent_fake, sessions=unsaved, executions=executions)

    reply = await reply_to(resent_runner, "s-hr", FIXED["B4"].message)

    assert reply == REUSED_H1.format(result["ticket_id"])
    assert len(calls["create_ticket"]) == 1
    assert (await stored(unsaved, "s-hr"))["pending"] is None


# Case H (e) (TASK.md line 451): an execution that started and never ended (its key is
# `started`) cannot be known to have finished, so it is reported and never run, now or later.
# Explaining it is task 19's.
async def test_case_h_uncertain_execution_never_runs(monkeypatch):
    calls = record_tools(monkeypatch)
    executions = InMemoryExecutions()
    key = execution_key("s-hu", "create_ticket", h1_values())
    assert executions.reserve(key) is None  # a run that never ended
    fake = ScriptedLlm(script=script_of("H1", "B4", "H1", "B4"))
    runner, sessions = make_runner(fake, executions=executions)

    for _ in range(2):
        await reply_to(runner, "s-hu", FIXED["H1"].message)
        reply = await reply_to(runner, "s-hu", FIXED["B4"].message)
        assert reply == UNCERTAIN_H1

    assert calls["create_ticket"] == []
    state = await stored(sessions, "s-hu")
    assert state["pending"] is None
    assert [entry["status"] for entry in state["history"]] == ["uncertain", "uncertain"]
    assert executions.snapshot() == {key: {"status": "started"}}
    assert fake.calls == 4


# examples/inputs ------------------------------------------------------------------------------

# Each file of examples/inputs/ and the fixed turns whose messages it holds, in order (decision
# A of brief 15: one message per line, no comments, no blank lines; the CLI does not change).
EXAMPLE_INPUTS = {
    "caso_a.txt": ("A1",),
    "caso_b.txt": ("B1", "B2", "B3", "B4"),
    "caso_c.txt": ("C1", "K2", "C2", "G2"),
    "caso_d.txt": ("D1", "D2"),
    "caso_e.txt": ("E1", "E2", "E3"),
    "caso_f.txt": ("F1", "F2"),
    "caso_g.txt": ("G1", "G2"),
    "caso_h.txt": ("H1", "H1", "B4", "H1", "B4"),
}

# How many times each tool runs when the file goes through the CLI loop.
EXAMPLE_RUNS = {
    "caso_a.txt": {},
    "caso_b.txt": {"create_ticket": 1},
    "caso_c.txt": {"send_notification": 1},
    "caso_d.txt": {},
    "caso_e.txt": {"create_ticket": 1, "send_notification": 1},
    "caso_f.txt": {},
    "caso_g.txt": {"send_notification": 1},
    "caso_h.txt": {"create_ticket": 1},
}


def test_example_inputs_are_the_case_messages():
    assert sorted(path.name for path in EXAMPLES.iterdir()) == sorted(EXAMPLE_INPUTS)
    total = 0
    for name, keys in EXAMPLE_INPUTS.items():
        text = (EXAMPLES / name).read_text(encoding="utf-8")
        assert text.endswith("\n"), name
        lines = text[:-1].split("\n")
        assert all(line.strip() for line in lines), name  # no blank line
        assert not [line for line in lines if line.startswith("#")], name
        assert lines == [FIXED[key].message for key in keys], name
        total += len(lines)
    assert total == 23


@pytest.mark.parametrize("name", list(EXAMPLE_INPUTS))
async def test_example_inputs_run_through_the_cli_loop(monkeypatch, name):
    calls = record_tools(monkeypatch)
    keys = EXAMPLE_INPUTS[name]
    fake = ScriptedLlm(script=script_of(*keys))
    runner, _ = make_runner(fake, executions=InMemoryExecutions())
    out = io.StringIO()

    with (EXAMPLES / name).open(encoding="utf-8") as lines:
        await chat(runner, "cli", f"s-{name}", lines, out, echo=True)

    text = out.getvalue()
    assert fake.calls == len(keys)
    echoed = [line for line in text.splitlines() if line.startswith("> ")]
    assert echoed == [f"> {FIXED[key].message}" for key in keys]
    assert '"act"' not in text  # nothing from the interpreter
    expected = {tool: EXAMPLE_RUNS[name].get(tool, 0) for tool in TOOLS}
    assert call_counts(calls) == expected
