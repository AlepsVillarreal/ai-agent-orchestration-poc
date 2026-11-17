"""Task 13: the clarification of D3 (R4 and R5 ask; the answer is read with five rules).

Pure core, no ADK: fixed interpretations, the saved phase 0 outputs and `record_tools`, which
counts the tool calls (none here, except after the "yes" that the criteria name).
"""

import dataclasses
import json
from pathlib import Path

import pytest

from core import replies
from core.clarification import new_record
from core.instruction import pending_summary
from core.interpretation import TurnInterpretation
from core.registry import TOOLS
from core.turn import TurnOutcome
from phase0.scenarios import SCENARIOS
from tests.case_c_judge import read_records
from tests.interpretations import FIXED
from tests.test_turn import (
    confirm,
    confirmation_text,
    fixed,
    handle,
    handle_fixed,
    interpretation,
    nothing_ran,
    op,
    record_tools,
)

RUNS = Path(__file__).resolve().parent.parent / "phase0" / "runs"
RUN_06 = RUNS / "20261117T073844Z-gemini-3.5-flash-lite.jsonl"
RUN_10C_1 = RUNS / "20261117T164006Z-gemini-3.5-flash-lite.jsonl"
RUN_10C_2 = RUNS / "20261117T164255Z-gemini-3.5-flash-lite.jsonl"

TWO_QUESTION = "¿Quieres enviar una notificación, programar una reunión, o ambas?"
DISCARDED_BOTH = (
    "No voy a enviar una notificación ni programar una reunión; descarté lo que tenía pendiente."
)
INHERITED = ["Anoté a quién se envía: Carlos.", "Anoté el mensaje: mañana revisamos el servidor."]
C1_CANDIDATES = [
    {
        "tool": "send_notification",
        "args": {"recipient": "Carlos", "message": "mañana revisamos el servidor"},
        "depends_on": None,
    },
    {
        "tool": "schedule_meeting",
        "args": {"attendees": ["Carlos"], "date": {"relative_days": 1}},
        "depends_on": None,
    },
]


def with_act(key: str, act: str) -> TurnInterpretation:
    return TurnInterpretation.model_validate({**FIXED[key].interpretation, "act": act})


def c1_record() -> dict:
    """The clarification `handle_turn` stores for the fixed `C1` ("the record of `C1`")."""
    return handle_fixed(None, "C1").state_delta["pending"]


# R5 with one candidate, the meeting (criteria 4, rule 3 and rule 5).
R5_MEETING = interpretation(
    "request_action", op("schedule_meeting", "plausible", attendees=["Carlos"])
)
R5_MEETING_MESSAGE = "Algo con Carlos"


def r5_meeting_record() -> dict:
    return handle(None, R5_MEETING, R5_MEETING_MESSAGE).state_delta["pending"]


def measured(path: Path, key: str) -> tuple[TurnInterpretation, str]:
    """A saved output of the phase 0 harness, validated as the judge does, and its message."""
    [record] = [record for record in read_records(path) if record["id"] == key]
    return TurnInterpretation.model_validate(record["interpretation"]), record["message"]


def candidates_as_steps(record: dict) -> list[dict]:
    return [{"tool": c["tool"], "args": c["args"]} for c in record["candidates"]]


def closed_clarification(record: dict, status: str) -> dict:
    return {
        "operation_id": record["operation_id"],
        "status": status,
        "steps": candidates_as_steps(record),
    }


def without_id(delta: dict) -> dict:
    record = delta.get("pending")
    if not record:
        return delta
    return {**delta, "pending": {k: v for k, v in record.items() if k != "operation_id"}}


# 1 (D3 *Pregunta* and *Estado*, D4 rule 4; TASK.md lines 137 and 338-355)
def test_r4_and_r5_store_a_clarification_and_ask(monkeypatch):
    calls = record_tools(monkeypatch)

    outcome = handle_fixed(None, "C1")
    record = outcome.state_delta["pending"]

    assert outcome.route == "R4"
    assert list(outcome.state_delta) == ["pending"]
    assert len(record["operation_id"]) == 32 and int(record["operation_id"], 16) >= 0
    assert record == {
        "operation_id": record["operation_id"],
        "status": "awaiting_clarification",
        "tools": ["send_notification", "schedule_meeting"],
        "candidates": C1_CANDIDATES,
        "missing": [],
        "last_question": TWO_QUESTION,
    }
    assert outcome.reply == TWO_QUESTION

    # The `plausible` first in the interpreter's list: the `explicit` is still listed first.
    plausible_first = interpretation(
        "request_action", *reversed(FIXED["C1"].interpretation["operations"])
    )
    swapped = handle(None, plausible_first, FIXED["C1"].message).state_delta["pending"]
    assert swapped["candidates"] == C1_CANDIDATES
    assert swapped["tools"] == ["send_notification", "schedule_meeting"]

    one = handle(None, R5_MEETING, R5_MEETING_MESSAGE)
    assert one.route == "R5"
    assert one.reply == "¿Quieres programar una reunión? Responde sí o no."
    assert one.state_delta["pending"]["candidates"] == [
        {"tool": "schedule_meeting", "args": {"attendees": ["Carlos"]}, "depends_on": None}
    ]

    two_plausible = interpretation(
        "request_action",
        op("send_notification", "plausible", recipient="Carlos"),
        op("schedule_meeting", "plausible", attendees=["Carlos"]),
    )
    two = handle(None, two_plausible, "Algo con Carlos")
    assert two.route == "R5"
    assert two.reply == TWO_QUESTION

    # D4 rule 9: a candidate's name not written in the message is dropped, and said so.
    pedro = interpretation(
        "request_action",
        op("send_notification", recipient="Carlos", message="mañana revisamos el servidor"),
        op("schedule_meeting", "plausible", attendees=["Pedro"], date={"relative_days": 1}),
    )
    rejected = handle(None, pedro, FIXED["C1"].message)
    meeting = rejected.state_delta["pending"]["candidates"][1]
    assert meeting["args"] == {"date": {"relative_days": 1}}
    assert "Pedro" not in json.dumps(rejected.state_delta)
    assert rejected.reply == f"{replies.REJECTED_NAME_LINE}\n{TWO_QUESTION}"
    assert nothing_ran(calls)


# 2 (D3 *Pregunta*)
def test_clarification_question_by_count():
    ticket, meeting, notice = (
        TOOLS["create_ticket"],
        TOOLS["schedule_meeting"],
        TOOLS["send_notification"],
    )
    fourth = dataclasses.replace(ticket, name="book_room", action_phrase="reservar una sala")

    assert replies.clarification_question([meeting]) == (
        "¿Quieres programar una reunión? Responde sí o no."
    )
    assert replies.clarification_question([notice, meeting]) == TWO_QUESTION
    assert replies.clarification_question([ticket, meeting, notice]) == (
        "¿Qué necesitas: crear un ticket, programar una reunión o enviar una notificación? "
        "Puedes elegir varias."
    )
    assert replies.clarification_question([ticket, meeting, notice, fourth]) is None


def test_four_candidates_answer_the_capabilities(monkeypatch):
    # Unreachable with three tools (D11 leaves one operation per tool); a fourth tool shows it.
    fourth = dataclasses.replace(TOOLS["create_ticket"], name="book_room")
    monkeypatch.setitem(TOOLS, "book_room", fourth)
    operations = [
        {"tool": name, "certainty": "plausible", "depends_on": None, "args": {}} for name in TOOLS
    ]
    record, changes, rejected = new_record(operations, "Algo")
    assert (record, changes, rejected) == (None, [], [])


# 3 (D14; note (e)): the measured K1-S3 saw the same summary the core now produces.
def test_clarification_summary_matches_the_measured_one():
    by_id = {scenario.id: scenario for scenario in SCENARIOS}
    assert pending_summary(c1_record()) == pending_summary(by_id["K1"].pending)


# 4 (D3 *Lectura*, D3.1, D3.2; note (nnn)): the route of each rule, and nothing runs.
RULE_CASES: dict[str, tuple[str, TurnInterpretation, str, str]] = {
    # rule 1
    "S2": ("C1", fixed("S2"), FIXED["S2"].message, "clarification:regla_1"),
    "cancel": ("C1", interpretation("cancel"), "Mejor cancela", "clarification:regla_1"),
    "cancel the notice": (
        "C1",
        interpretation("cancel", op("send_notification")),
        "Cancela el aviso",
        "clarification:regla_1",
    ),
    # rule 2
    "K1": ("C1", fixed("K1"), FIXED["K1"].message, "clarification:regla_2"),
    "K1 measured": (
        "C1",
        with_act("K1", "request_action"),
        FIXED["K1"].message,
        "clarification:regla_2",
    ),
    "K2": ("C1", fixed("K2"), FIXED["K2"].message, "clarification:regla_2"),
    "K2 request": (
        "C1",
        with_act("K2", "request_action"),
        FIXED["K2"].message,
        "clarification:regla_2",
    ),
    "confirm one explicit": (
        "C1",
        confirm(op("send_notification")),
        "Sí, avísale",
        "clarification:regla_2",
    ),
    # rule 3
    "S1 one candidate": ("R5", fixed("S1"), FIXED["S1"].message, "clarification:regla_3"),
    "confirm naming it": (
        "R5",
        confirm(confirms_tool="schedule_meeting"),
        "Sí, la reunión",
        "clarification:regla_3",
    ),
    # rule 4
    **{
        f"ticket {act}": (
            "C1",
            interpretation(act, op("create_ticket", description="revisar el servidor")),
            "Mejor crea un ticket para revisar el servidor",
            "clarification:regla_4:R2",
        )
        for act in ("request_action", "provide_info", "reject", "cancel")
    },
    # rule 5
    "S1 two candidates": ("C1", fixed("S1"), FIXED["S1"].message, "clarification:regla_5"),
    "K3": ("C1", fixed("K3"), FIXED["K3"].message, "clarification:regla_5"),
    "Q1": ("C1", fixed("Q1"), FIXED["Q1"].message, "clarification:regla_5"),
    "both plausible": (
        "C1",
        interpretation(
            "provide_info",
            op("send_notification", "plausible"),
            op("schedule_meeting", "plausible"),
        ),
        "Lo que sea",
        "clarification:regla_5",
    ),
    "S3 round 2": (
        "C1",
        measured(RUN_10C_2, "S3")[0],
        FIXED["C1"].message,
        "clarification:regla_5",
    ),
    "confirm both explicit": (
        "C1",
        confirm(op("send_notification"), op("schedule_meeting")),
        "Sí, las dos",
        "clarification:regla_5",
    ),
    "confirm naming another tool": (
        "R5",
        confirm(confirms_tool="create_ticket"),
        "Sí, el ticket",
        "clarification:regla_5",
    ),
}


@pytest.mark.parametrize("case", list(RULE_CASES))
def test_clarification_answer_rules(monkeypatch, case):
    calls = record_tools(monkeypatch)
    first, turn, message, route = RULE_CASES[case]
    pending = c1_record() if first == "C1" else r5_meeting_record()

    outcome = handle(pending, turn, message)

    assert outcome.route == route
    assert outcome.results == []
    assert nothing_ran(calls)


# 5 (D3 rule 1, D6; TASK.md line 284)
@pytest.mark.parametrize(
    ("turn", "message"), [(fixed("S2"), FIXED["S2"].message), (interpretation("cancel"), "Cancela")]
)
def test_rule_1_discards_the_clarification(monkeypatch, turn, message):
    calls = record_tools(monkeypatch)
    pending = c1_record()

    outcome = handle(pending, turn, message)

    assert outcome.state_delta == {
        "pending": None,
        "history": [
            {
                "operation_id": pending["operation_id"],
                "status": "cancelled",
                "steps": [{"tool": c["tool"], "args": c["args"]} for c in C1_CANDIDATES],
            }
        ],
    }
    assert outcome.reply == "No inicié ninguna acción. Dime qué necesitas cuando quieras."
    assert outcome.reply == replies.NO_ACTION_STARTED

    after = handle_fixed(None, "G2")
    assert after.reply == replies.NO_PENDING_OPERATION
    assert after.state_delta == {}
    assert nothing_ran(calls)


# 6 (D3 rule 2, D4 rules 4 and 9; TASK.md lines 145, 184 and 338-355)
@pytest.mark.parametrize("act", ["provide_info", "request_action"])
def test_rule_2_starts_the_chosen_flow_with_what_was_said(monkeypatch, act):
    calls = record_tools(monkeypatch)
    pending = c1_record()

    outcome = handle(pending, with_act("K2", act), FIXED["K2"].message)
    record = outcome.state_delta["pending"]

    assert outcome.route == "clarification:regla_2"
    assert list(outcome.state_delta) == ["pending"]  # `history` does not change
    assert record["status"] == "collecting"
    assert record["tools"] == ["send_notification"]
    assert record["steps"] == [
        {
            "tool": "send_notification",
            "args": {"recipient": "Carlos", "message": "mañana revisamos el servidor"},
        }
    ]
    assert record["operation_id"] == pending["operation_id"]
    assert "candidates" not in record
    assert record["missing"] == ["channel"]
    assert "attendees" not in json.dumps(record)
    assert outcome.reply.split("\n") == [*INHERITED, record["last_question"]]
    assert "el canal" in record["last_question"]
    assert nothing_ran(calls)

    # With the channel in the same answer: complete, so it awaits the "yes"; nothing runs yet.
    by_mail = interpretation("provide_info", op("send_notification", channel="correo"))
    complete = handle(pending, by_mail, "Solo avísale, por correo")
    ready = complete.state_delta["pending"]
    assert complete.route == "clarification:regla_2"
    assert ready["status"] == "awaiting_confirmation"
    assert complete.reply.split("\n")[:3] == [*INHERITED, "Anoté el canal: correo."]
    assert complete.reply.endswith(confirmation_text(ready))
    for shown in ("- A quién se envía: Carlos", "- El mensaje: mañana revisamos el servidor"):
        assert shown in complete.reply
    assert "- El canal: correo" in complete.reply
    assert nothing_ran(calls)

    yes = handle(ready, fixed("G2"), FIXED["G2"].message)
    assert yes.route == "confirmed"
    assert calls["send_notification"] == [
        {"recipient": "Carlos", "message": "mañana revisamos el servidor", "channel": "email"}
    ]
    assert calls["schedule_meeting"] == [] and calls["create_ticket"] == []

    # Another recipient in the answer: the inherited line, then the change (D4 limitation: the
    # stored message stays).
    to_pedro = interpretation("provide_info", op("send_notification", recipient="Pedro"))
    pedro = handle(pending, to_pedro, "Avísale a Pedro")
    assert pedro.reply.split("\n")[:3] == [*INHERITED, "Cambié a quién se envía: Carlos → Pedro."]
    assert pedro.state_delta["pending"]["steps"][0]["args"] == {
        "recipient": "Pedro",
        "message": "mañana revisamos el servidor",
    }


# 7 (D3 rule 3, D7.1; TASK.md lines 276-284)
def test_rule_3_one_candidate_yes_never_runs(monkeypatch):
    calls = record_tools(monkeypatch)
    maybe_notice = interpretation(
        "request_action",
        op(
            "send_notification",
            "plausible",
            recipient="Carlos",
            message="mañana revisamos el servidor",
            channel="correo",
        ),
    )
    asked = handle(
        None, maybe_notice, "Quizá avísale a Carlos por correo que mañana revisamos el servidor"
    )
    pending = asked.state_delta["pending"]
    assert asked.route == "R5"
    assert asked.reply == "¿Quieres enviar una notificación? Responde sí o no."

    outcome = handle_fixed(pending, "S1")
    record = outcome.state_delta["pending"]

    assert outcome.route == "clarification:regla_3"
    assert record["status"] == "awaiting_confirmation"
    assert record["operation_id"] == pending["operation_id"]
    assert outcome.reply.split("\n")[:3] == [*INHERITED, "Anoté el canal: correo."]
    assert outcome.reply.endswith(confirmation_text(record))
    assert nothing_ran(calls)

    yes = handle_fixed(record, "G2")
    assert yes.route == "confirmed"
    assert len(calls["send_notification"]) == 1
    assert calls["schedule_meeting"] == [] and calls["create_ticket"] == []


# 8. Task 14a ((A17), D76 *Pendiente*, D3 rule 2, D4 rule 9; TASK.md lines 344-350): "both"
# forms the plan, in the candidates' order, with what each candidate already had. It was
# `test_rule_2_with_two_tools_waits_for_the_plan` (the provisional template of D76).
C1_PLAN_STEPS = [
    {"tool": c["tool"], "args": c["args"]} for c in C1_CANDIDATES
]  # no step depends on another: no `depends_on` (decision B of brief 14a)
C1_INHERITED = [
    *INHERITED,
    "Anoté la fecha: mañana.",
    "Anoté los asistentes: Carlos.",
]
C1_PLAN_QUESTION = (
    "Para enviar una notificación necesito el canal. "
    "Para programar una reunión necesito el título, la hora y la duración en minutos."
)


@pytest.mark.parametrize(
    ("turn", "message"),
    [
        (with_act("K1", "provide_info"), FIXED["K1"].message),
        (with_act("K1", "request_action"), FIXED["K1"].message),
        measured(RUN_06, "K1"),
    ],
    ids=["K1", "K1_request_action", "K1_measured"],
)
def test_rule_2_with_two_tools_forms_the_plan(monkeypatch, turn, message):
    calls = record_tools(monkeypatch)
    pending = c1_record()

    outcome = handle(pending, turn, message)
    record = outcome.state_delta["pending"]

    assert outcome.route == "clarification:regla_2"
    assert list(outcome.state_delta) == ["pending"]
    assert record == {
        "operation_id": pending["operation_id"],
        "status": "collecting",
        "tools": ["send_notification", "schedule_meeting"],
        "steps": C1_PLAN_STEPS,
        "missing": ["channel", "title", "time", "duration_minutes"],
        "invalid": [],
        "last_question": C1_PLAN_QUESTION,
    }
    assert outcome.reply.split("\n") == [*C1_INHERITED, C1_PLAN_QUESTION]
    assert outcome.depends == []
    assert nothing_ran(calls)


def _with_candidates(record: dict, depends_on: dict[str, str | None]) -> dict:
    """The record of `C1` with each candidate's `depends_on` replaced."""
    candidates = [{**c, "depends_on": depends_on[c["tool"]]} for c in record["candidates"]]
    return {**record, "candidates": candidates}


# The `depends_on` the candidates brought between them is kept (D3: "D8 la valida"); one that
# names a candidate not chosen, or a tool that is not a candidate, is ignored.
@pytest.mark.parametrize(
    ("depends_on", "tools", "effective", "log"),
    [
        (
            {"send_notification": "schedule_meeting", "schedule_meeting": None},
            ["schedule_meeting", "send_notification"],
            [None, "schedule_meeting"],
            ["send_notification<schedule_meeting"],
        ),
        (
            {"send_notification": "create_ticket", "schedule_meeting": None},
            ["send_notification", "schedule_meeting"],
            [None, None],
            ["send_notification<create_ticket:ignored"],
        ),
    ],
    ids=["kept", "names_no_candidate"],
)
def test_rule_2_plan_keeps_the_candidates_dependency(
    monkeypatch, depends_on, tools, effective, log
):
    calls = record_tools(monkeypatch)
    pending = _with_candidates(c1_record(), depends_on)

    outcome = handle(pending, fixed("K1"), FIXED["K1"].message)
    record = outcome.state_delta["pending"]

    assert outcome.route == "clarification:regla_2"
    assert record["tools"] == tools
    assert [step.get("depends_on") for step in record["steps"]] == effective
    assert outcome.depends == log
    # D4 rule 9: the inherited lines follow the steps' order.
    meeting = ["Anoté la fecha: mañana.", "Anoté los asistentes: Carlos."]
    expected = [*meeting, *INHERITED] if tools[0] == "schedule_meeting" else C1_INHERITED
    assert outcome.reply.split("\n")[:4] == expected
    assert nothing_ran(calls)


# Review 14a-1, note 5: a `depends_on` that names a candidate the answer did not choose is
# ignored and logged (three candidates, two chosen); nothing is inferred between these two.
def test_rule_2_plan_ignores_a_dependency_on_a_candidate_not_chosen(monkeypatch):
    calls = record_tools(monkeypatch)
    pending = _with_candidates(
        c1_record(), {"send_notification": "create_ticket", "schedule_meeting": None}
    )
    pending["candidates"].append({"tool": "create_ticket", "args": {}, "depends_on": None})
    pending["tools"] = [*pending["tools"], "create_ticket"]

    outcome = handle(pending, fixed("K1"), FIXED["K1"].message)
    record = outcome.state_delta["pending"]

    assert outcome.route == "clarification:regla_2"
    assert record["tools"] == ["send_notification", "schedule_meeting"]
    assert all("depends_on" not in step for step in record["steps"])
    assert outcome.depends == ["send_notification<create_ticket:ignored"]
    assert nothing_ran(calls)


# Decision E of brief 14a, the author's answer (a): a plan born from a clarification infers the
# dependency the registry declares, like any plan (D8.4 over D3 rule 2's "no añade ninguna").
def test_rule_2_plan_infers_the_declared_dependency(monkeypatch):
    calls = record_tools(monkeypatch)
    maybe = interpretation(
        "request_action",
        op("create_ticket", assignee="Carlos"),
        op("send_notification", "plausible", recipient="Carlos"),
    )
    asked = handle(None, maybe, "Crea un ticket para Carlos y quizá avísale a Carlos")
    pending = asked.state_delta["pending"]
    assert asked.route == "R4"
    both = interpretation("provide_info", op("create_ticket"), op("send_notification"))

    outcome = handle(pending, both, "Las dos")
    record = outcome.state_delta["pending"]

    assert outcome.route == "clarification:regla_2"
    assert record["steps"] == [
        {"tool": "create_ticket", "args": {"assignee": "Carlos"}},
        {
            "tool": "send_notification",
            "args": {"recipient": "Carlos"},
            "depends_on": "create_ticket",
        },
    ]
    assert outcome.depends == ["send_notification<create_ticket:inferred"]
    assert outcome.reply.split("\n")[:2] == [
        "Anoté a quién se asigna: Carlos.",
        "Anoté a quién se envía: Carlos.",
    ]
    assert nothing_ran(calls)


# 9 (D3 rule 4, D3.1, D64, D65; TASK.md lines 395-417)
@pytest.mark.parametrize("act", ["request_action", "provide_info", "reject", "cancel"])
def test_rule_4_is_a_change_of_intention(monkeypatch, act):
    calls = record_tools(monkeypatch)
    pending = c1_record()
    ticket = interpretation(act, op("create_ticket", description="revisar el servidor"))

    outcome = handle(pending, ticket, "Mejor crea un ticket para revisar el servidor")
    record = outcome.state_delta["pending"]

    assert outcome.route == "clarification:regla_4:R2"
    assert outcome.state_delta["history"] == [closed_clarification(pending, "abandoned")]
    assert outcome.reply.split("\n")[0] == DISCARDED_BOTH
    assert record["steps"] == [
        {"tool": "create_ticket", "args": {"description": "revisar el servidor"}}
    ]
    assert record["operation_id"] != pending["operation_id"]
    assert "Carlos" not in json.dumps(record)
    assert nothing_ran(calls)


# Task 14a (D71 *Pendiente*, AW 1): a rule 4 that falls in R3 now stores the plan of the turn's
# operations, with nothing of the clarification (D3.1). It was `..._empties_pending`.
def test_rule_4_into_r3_stores_the_plan(monkeypatch):
    calls = record_tools(monkeypatch)
    pending = c1_record()
    both = interpretation("request_action", op("create_ticket"), op("send_notification"))

    outcome = handle(pending, both, "Mejor crea un ticket y avísale")
    record = outcome.state_delta["pending"]

    assert outcome.route == "clarification:regla_4:R3"
    assert outcome.state_delta["history"] == [closed_clarification(pending, "abandoned")]
    assert record["steps"] == [
        {"tool": "create_ticket", "args": {}},
        {"tool": "send_notification", "args": {}, "depends_on": "create_ticket"},
    ]
    assert record["operation_id"] != pending["operation_id"]
    assert "Carlos" not in json.dumps(record)
    assert outcome.reply == f"{DISCARDED_BOTH}\n{record['last_question']}"
    assert outcome.depends == ["send_notification<create_ticket:inferred"]
    assert nothing_ran(calls)


# 10 (D3.2): an unclear answer repeats the same question, and keeps doing so.
@pytest.mark.parametrize(
    ("turn", "message"),
    [
        (fixed("S1"), FIXED["S1"].message),
        (fixed("K3"), FIXED["K3"].message),
        (fixed("Q1"), FIXED["Q1"].message),
        measured(RUN_10C_2, "S3"),
    ],
    ids=["S1", "K3", "Q1", "S3_round_2"],
)
def test_rule_5_repeats_the_question(monkeypatch, turn, message):
    calls = record_tools(monkeypatch)
    pending = c1_record()

    for _ in range(2):  # no discard on the second try
        outcome = handle(pending, turn, message)
        assert outcome.route == "clarification:regla_5"
        assert outcome.reply == pending["last_question"]
        assert outcome.state_delta == {}
    assert nothing_ran(calls)


# 11 (D2 *Sondas*, D52; note (ooo)): the saved outputs of the real model through the core.
MEASURED = [
    (RUN_06, "K1", "clarification:regla_2"),
    (RUN_06, "K2", "clarification:regla_2"),
    (RUN_06, "K3", "clarification:regla_5"),
    (RUN_06, "S1", "clarification:regla_5"),
    (RUN_06, "S2", "clarification:regla_1"),
    (RUN_06, "S3", "clarification:regla_2"),  # D52's failure as measured: the notice alone
    (RUN_10C_1, "S3", "clarification:regla_2"),
    (RUN_10C_2, "S3", "clarification:regla_5"),
]


@pytest.mark.parametrize(
    ("path", "key", "route"), MEASURED, ids=[f"{p.name[:16]}-{k}" for p, k, _ in MEASURED]
)
def test_measured_answers_through_the_core(monkeypatch, path, key, route):
    calls = record_tools(monkeypatch)
    turn, message = measured(path, key)

    outcome = handle(c1_record(), turn, message)

    assert outcome.route == route
    if key == "K1":  # two tools: the plan of task 14a, in the candidates' order
        record = outcome.state_delta["pending"]
        assert record["tools"] == ["send_notification", "schedule_meeting"]
        assert record["steps"] == C1_PLAN_STEPS
    assert nothing_ran(calls)


# 12 (D2 *Solo para logs*): `ambiguity_reason` changes no decision.
def _with_reason(turn: TurnInterpretation, reason: str | None) -> TurnInterpretation:
    return turn.model_copy(update={"ambiguity_reason": reason})


REASONS = [None, FIXED["C1"].interpretation["ambiguity_reason"], "Clearly a meeting with Carlos."]


@pytest.mark.parametrize(
    ("first", "turn", "message"),
    [
        (None, fixed("C1"), FIXED["C1"].message),
        ("C1", fixed("K2"), FIXED["K2"].message),
        ("C1", *measured(RUN_10C_2, "S3")),
    ],
    ids=["C1", "K2", "S3_round_2"],
)
def test_ambiguity_reason_changes_no_decision(first, turn, message):
    pending = c1_record() if first else None
    outcomes: list[TurnOutcome] = [
        handle(pending, _with_reason(turn, reason), message) for reason in REASONS
    ]

    assert len({o.route for o in outcomes}) == 1
    assert len({o.reply for o in outcomes}) == 1
    deltas = [json.dumps(without_id(o.state_delta), sort_keys=True) for o in outcomes]
    assert len(set(deltas)) == 1


# 13 (D41, D54 (c))
def test_clarification_and_the_d10_and_d11_lines():
    pending = c1_record()
    plain = handle_fixed(pending, "K2")
    pizza_turn = interpretation(
        "provide_info", op("send_notification"), op("pedir_pizza", size="grande")
    )
    pizza = handle(pending, pizza_turn, "Solo avísale y pide una pizza grande")

    assert pizza.reply.split("\n")[0] == replies.UNKNOWN_TOOL_LINE
    assert pizza.reply == f"{replies.UNKNOWN_TOOL_LINE}\n{plain.reply}"
    assert pizza.state_delta == plain.state_delta

    split = interpretation(
        "provide_info",
        op("send_notification", recipient="Carlos"),
        op("send_notification", channel="correo"),
    )
    outcome = handle(pending, split, "Avísale a Carlos, por correo")
    assert outcome.route == "d11"
    assert outcome.state_delta == {}


# 14 (AW 1 for R4; D71 *Pendiente*): a change of intention into R4 now stores a clarification
# with the candidates of the turn, and nothing of what was abandoned.
def test_change_of_intention_into_r4_keeps_the_new_candidates(monkeypatch):
    calls = record_tools(monkeypatch)
    ticket = handle_fixed(None, "A1").state_delta["pending"]
    turn = interpretation(
        "request_action",
        op("schedule_meeting", attendees=["Juan"]),
        op("create_ticket", "plausible", title="Caída"),
    )

    outcome = handle(
        ticket, turn, "Mejor agenda una reunión con Juan y quizá un ticket titulado Caída"
    )
    record = outcome.state_delta["pending"]

    assert outcome.route == "other_tool:R4"
    assert outcome.state_delta["history"] == [
        {"operation_id": ticket["operation_id"], "status": "abandoned", "steps": ticket["steps"]}
    ]
    assert record["status"] == "awaiting_clarification"
    assert record["candidates"] == [
        {"tool": "schedule_meeting", "args": {"attendees": ["Juan"]}, "depends_on": None},
        {"tool": "create_ticket", "args": {"title": "Caída"}, "depends_on": None},
    ]
    assert "el servidor está caído" not in json.dumps(record)
    assert outcome.reply == (
        "No voy a crear un ticket; descarté lo que tenía pendiente.\n"
        "¿Quieres programar una reunión, crear un ticket, o ambas?"
    )
    assert nothing_ran(calls)
