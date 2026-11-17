import dataclasses
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from core import mock_tools, replies
from core.confirmation import confirmation_hash, shown_values
from core.executions import InMemoryExecutions, execution_key, settled
from core.instruction import pending_summary
from core.interpretation import Act, Certainty, TurnInterpretation
from core.merge import Note
from core.mock_tools import simulated
from core.registry import TOOLS
from core.turn import TurnInput, TurnOutcome, filter_by_registry, handle_turn
from tests.case_c_judge import read_records
from tests.interpretations import FIXED, NOW, SESSION


def fixed(key: str) -> TurnInterpretation:
    return TurnInterpretation.model_validate(FIXED[key].interpretation)


def handle_fixed(pending: dict | None, key: str) -> TurnOutcome:
    """`handle_turn` on a fixed interpretation, with its own message (D4 rule 3)."""
    return handle_turn(pending, fixed(key), TurnInput(FIXED[key].message, NOW, SESSION))


def interpretation(act: str, *operations: dict[str, Any]) -> TurnInterpretation:
    return TurnInterpretation.model_validate({"act": act, "operations": list(operations)})


def op(tool: str, certainty: str = "explicit", **args: Any) -> dict[str, Any]:
    return {"tool": tool, "certainty": certainty, "args": args}


def a1_pending() -> dict:
    return handle_fixed(None, "A1").state_delta["pending"]


def run_conversation(*keys: str) -> tuple[dict | None, TurnOutcome]:
    """Fixed turns in a row, each applying its state delta. Returns the pending record and the
    last outcome."""
    pending, outcome = None, None
    for key in keys:
        outcome = handle_fixed(pending, key)
        pending = outcome.state_delta.get("pending", pending)
    return pending, outcome


def _all_values(value: Any) -> list[str]:
    """Every collected value as text, for "the question shows none of them" checks."""
    if isinstance(value, dict):
        return [text for item in value.values() for text in _all_values(item)]
    if isinstance(value, list):
        return [text for item in value for text in _all_values(item)]
    return [str(value)]


def confirmation_text(record: dict) -> str:
    """What a record awaiting confirmation shows: the normalized values, then the question."""
    summary = replies.confirmation_summary(shown_values(record["steps"], NOW))
    return f"{summary}\n{record['last_question']}"


# 4
def test_unknown_tool_alone_gives_capabilities():
    outcome = handle_turn(
        None,
        interpretation("request_action", op("book_flight")),
        TurnInput("Reserva un vuelo", NOW, SESSION),
    )

    assert outcome.route == "R1"
    assert all(tool.action_phrase in outcome.reply for tool in TOOLS.values())
    assert replies.UNKNOWN_TOOL_LINE not in outcome.reply
    assert outcome.state_delta == {}


# 5
def test_unknown_tool_next_to_a_known_one_adds_the_d10_line():
    turn = interpretation(
        "request_action",
        op("book_flight"),
        op("create_ticket", description="el servidor está caído"),
    )
    outcome = handle_turn(
        None,
        turn,
        TurnInput("Reserva un vuelo y crea un ticket: el servidor está caído", NOW, SESSION),
    )

    assert outcome.route == "R2"
    assert outcome.state_delta["pending"]["tools"] == ["create_ticket"]
    assert replies.UNKNOWN_TOOL_LINE in outcome.reply
    assert replies.UNKNOWN_TOOL_LINE not in outcome.state_delta["pending"]["last_question"]


# 6
def test_contract_filter_drops_args_of_other_tools():
    turn = interpretation(
        "request_action", op("create_ticket", description="el servidor", recipient="Carlos")
    )
    message = "Crea un ticket del servidor y avísale a Carlos"
    record = handle_turn(None, turn, TurnInput(message, NOW, SESSION)).state_delta["pending"]

    assert record["steps"][0]["args"] == {"description": "el servidor"}


def test_registry_filter_keeps_contract_keys_and_buckets():
    # Checked on the filter itself (review 03-1, finding 1): the merge applies the buckets
    # afterwards and never stores them, which would hide a broken filter.
    turn = interpretation(
        "request_action",
        op("schedule_meeting", attendees_add=["Ana"], recipient="Carlos"),
        op("create_ticket", "plausible", description="el servidor", attendees_add=["Ana"]),
    )
    meeting, ticket = filter_by_registry(turn)

    assert meeting["args"] == {"attendees_add": ["Ana"]}
    assert ticket["args"] == {"description": "el servidor"}


# 7
def test_two_operations_of_one_tool_follow_d11():
    turn = interpretation(
        "request_action",
        op("create_ticket", description="el servidor"),
        op("create_ticket", "plausible", description="la red"),
    )
    for pending in (None, a1_pending()):
        outcome = handle_turn(
            pending, turn, TurnInput("Crea dos tickets: el servidor y la red", NOW, SESSION)
        )
        assert outcome.route == "d11"
        assert outcome.reply == replies.ONE_PER_TOOL
        assert outcome.state_delta == {}


# 8
def test_nothing_pending_acts():
    no_pending = [
        fixed("G2"),
        interpretation("reject"),
        interpretation("cancel"),
        interpretation("confirm", op("create_ticket", description="el servidor")),
        interpretation("provide_info"),
    ]
    for turn in no_pending:
        outcome = handle_turn(None, turn, TurnInput("Sí, hazlo", NOW, SESSION))
        assert outcome.reply == replies.NO_PENDING_OPERATION, turn.act
        assert outcome.state_delta == {}

    for act in ("question", "other"):
        for operations in ([], [op("create_ticket", description="el servidor")]):
            outcome = handle_turn(
                None, interpretation(act, *operations), TurnInput("¿Y el servidor?", NOW, SESSION)
            )
            assert outcome.route == "template", (act, operations)
            assert outcome.reply == replies.capabilities()
            assert outcome.state_delta == {}


# 9. Task 13: R4 and R5 store a clarification (no `steps`, so no flow starts); R3 still creates
# nothing until task 14a. The clarification itself is in test_clarification.py.
def test_r3_r4_r5_do_not_start_anything_yet():
    only_plausible = interpretation(
        "request_action", op("schedule_meeting", "plausible", attendees=["Carlos"])
    )
    turns = (
        (fixed("E1"), FIXED["E1"].message, "R3"),
        (fixed("C1"), FIXED["C1"].message, "R4"),
        (only_plausible, "Algo con Carlos", "R5"),
    )
    for turn, message, route in turns:
        outcome = handle_turn(None, turn, TurnInput(message, NOW, SESSION))
        assert outcome.route == route
        if route == "R3":
            assert outcome.state_delta == {}
            continue
        assert list(outcome.state_delta) == ["pending"]
        record = outcome.state_delta["pending"]
        assert record["status"] == "awaiting_clarification"
        assert "steps" not in record


# 10
def test_case_a_creates_pending_and_asks_for_missing():
    outcome = handle_fixed(None, "A1")
    record = outcome.state_delta["pending"]

    assert outcome.route == "R2"
    assert list(outcome.state_delta) == ["pending"]
    assert record["status"] == "collecting"
    assert record["tools"] == ["create_ticket"]
    assert record["steps"] == [
        {"tool": "create_ticket", "args": {"description": "el servidor está caído"}}
    ]
    assert record["missing"] == ["title", "priority", "assignee"]

    reply = outcome.reply
    positions = [reply.index(label) for label in ("el título", "la prioridad", "a quién se asigna")]
    assert positions == sorted(positions)
    # The reply now says the description was noted (decision 1 of brief 08: R2 shows its fills),
    # so the description is checked where it must not be: `missing` and the question.
    assert "description" not in record["missing"]
    assert "la descripción" not in record["last_question"]
    assert reply.endswith(record["last_question"])


# 11 (task 09a adds X1: the invalid priority is asked for by its label only)
@pytest.mark.parametrize("key", ["A1", "B2", "F1", "X1"])
def test_missing_question_has_no_collected_value_and_no_options(key):
    record = handle_fixed(None, key).state_delta["pending"]
    question = record["last_question"].lower()

    for value in _all_values(record["steps"][0]["args"]):
        assert value.lower() not in question, value
    for option in ("alta", "media", "baja", "correo", "sms"):
        assert option not in question


# 12
def test_provide_info_with_operations_and_nothing_pending_starts_a_flow():
    outcome = handle_fixed(None, "B2")
    record = outcome.state_delta["pending"]

    assert outcome.route == "R2"
    assert record["steps"][0]["args"] == {"priority": "Alta", "assignee": "Carlos"}
    assert record["missing"] == ["title", "description"]
    # `{}` is "nothing pending" too (brief, point 1; review 03-1, note 6).
    assert handle_fixed({}, "B2").route == "R2"


# 13
def test_values_are_stored_as_said_without_validation():
    ticket = handle_fixed(None, "X1").state_delta["pending"]
    assert ticket["steps"][0]["args"]["priority"] == "urgente"
    assert "priority" not in ticket["missing"]

    meeting = handle_fixed(None, "D1").state_delta["pending"]
    args = meeting["steps"][0]["args"]
    assert args["date"] == {"day": 40, "month": 11}
    assert args["time"] == {"hour": 25, "minute": 90}
    # Date and time are present, so only the fields D1 does not mention are missing.
    assert meeting["missing"] == ["title", "duration_minutes", "attendees"]


def test_pending_args_leave_absent_values_out():
    turn = interpretation(
        "request_action",
        op(
            "schedule_meeting",
            title=" ",
            attendees=["", "Ana"],
            date={},
            duration_minutes=0,
        ),
    )
    record = handle_turn(None, turn, TurnInput("Una reunión con Ana", NOW, SESSION)).state_delta[
        "pending"
    ]

    assert record["steps"][0]["args"] == {"attendees": ["Ana"], "duration_minutes": 0}
    assert record["missing"] == ["title", "date", "time"]


# 14 became test 1 of brief 10a (`test_complete_request_awaits_confirmation`): a complete
# request now awaits confirmation instead of closing with the provisional template of D19.


# 15
def test_no_turn_executes_a_tool(monkeypatch):
    def fail(args):
        pytest.fail(f"a tool ran with {args}")

    for name, spec in list(TOOLS.items()):
        monkeypatch.setitem(TOOLS, name, dataclasses.replace(spec, execute=fail))

    pending = a1_pending()
    for key in FIXED:
        handle_fixed(None, key)
        handle_fixed(pending, key)
    run_conversation("B1", "B2", "B3")  # case B until complete


# 16, rewritten in task 12: with a `collecting` record nothing is provisional any more. Q1 is
# decision D of brief 12 (state unchanged) and F2 with F1 pending is *Otra tool*, case F. The
# clarification is read since task 13 (test_clarification.py). ("A1", "G2")
# left in task 10a: a "yes" while collecting repeats the question (test_yes_while_collecting_...).
@pytest.mark.parametrize(
    ("first", "key", "route", "keeps_state"),
    [("A1", "Q1", "pending_template", True), ("F1", "F2", "other_tool:R2", False)],
)
def test_pending_branches_are_no_longer_provisional(first, key, route, keeps_state):
    pending = handle_fixed(None, first).state_delta["pending"]
    outcome = handle_fixed(pending, key)

    assert outcome.route == route
    assert (outcome.state_delta == {}) == keeps_state


# 17
def test_pending_record_is_json_and_feeds_the_summary():
    record = a1_pending()

    json.dumps(record)
    assert record["tools"] == [step["tool"] for step in record["steps"]]

    summary = pending_summary(record)
    for text in ("create_ticket", "collecting", "title", "priority", "assignee"):
        assert text in summary
    assert record["last_question"] in summary
    assert "none" not in summary and "unknown" not in summary
    assert "servidor" not in summary


# Brief 08 ------------------------------------------------------------------------------------


def _lines(reply: str) -> list[str]:
    return reply.split("\n")


# 2
def test_r2_anchors_and_merges_a_new_flow():
    meeting = interpretation("request_action", op("schedule_meeting", attendees_add=["Ana"]))
    outcome = handle_turn(None, meeting, TurnInput("Agenda una reunión con Ana", NOW, SESSION))
    record = outcome.state_delta["pending"]

    assert record["steps"][0]["args"] == {"attendees": ["Ana"]}
    assert not any(
        key.endswith(("_add", "_remove")) for step in record["steps"] for key in step["args"]
    )  # D4 rule 10: buckets are applied, never stored (note (i))
    assert "attendees" not in record["missing"]

    ticket = interpretation(
        "request_action",
        op("create_ticket", description="el servidor está caído", assignee="Pedro"),
    )
    outcome = handle_turn(
        None, ticket, TurnInput("Crea un ticket, el servidor está caído", NOW, SESSION)
    )
    record = outcome.state_delta["pending"]

    assert outcome.route == "R2"
    assert record["steps"][0]["args"] == {"description": "el servidor está caído"}
    assert "assignee" in record["missing"]
    assert replies.REJECTED_NAME_LINE in _lines(outcome.reply)
    assert "Pedro" not in outcome.reply
    assert "Pedro" not in json.dumps(outcome.state_delta, ensure_ascii=False)
    assert [(r.key, r.name) for r in outcome.rejected] == [("assignee", "Pedro")]  # for the log


# 3
def test_filter_keeps_depends_on():
    ticket, notice = filter_by_registry(fixed("E1"))
    assert ticket["depends_on"] is None
    assert notice["depends_on"] == "create_ticket"

    (meeting,) = filter_by_registry(fixed("L4"))
    assert meeting["args"] == {"attendees_remove": ["Juan"], "attendees_add": ["Ana"]}
    assert meeting["depends_on"] is None


def _as(key: str, act: str, certainty: str) -> TurnInterpretation:
    """A fixed interpretation with its `act` and every operation's `certainty` replaced."""
    data = json.loads(json.dumps(FIXED[key].interpretation))
    data["act"] = act
    for operation in data["operations"]:
        operation["certainty"] = certainty
    return TurnInterpretation.model_validate(data)


# 4 (TASK.md lines 327-331; the `request_action` variant is finding 10 of 06-round-1)
@pytest.mark.parametrize("certainty", ["explicit", "plausible"])
@pytest.mark.parametrize("act", ["provide_info", "request_action"])
def test_same_tool_merges_and_asks_for_the_rest(act, certainty):
    pending, _ = run_conversation("B1")
    outcome = handle_turn(
        pending, _as("B2", act, certainty), TurnInput(FIXED["B2"].message, NOW, SESSION)
    )
    record = outcome.state_delta["pending"]
    args = record["steps"][0]["args"]

    assert outcome.route == "same_tool"
    assert args["description"] == "revisar el problema de autenticación"
    assert args["priority"] == "Alta"
    assert args["assignee"] == "Carlos"
    assert record["missing"] == ["title"]
    assert record["status"] == "collecting"

    lines = _lines(outcome.reply)
    assert lines == [
        replies.change_line(TOOLS["create_ticket"].fields[2], outcome.changes[0]),
        replies.change_line(TOOLS["create_ticket"].fields[3], outcome.changes[1]),
        record["last_question"],
    ]
    assert [(c.field, c.kind) for c in outcome.changes] == [
        ("priority", "fill"),
        ("assignee", "fill"),
    ]
    assert "Alta" in lines[0] and "Carlos" in lines[1]
    for value in _all_values(args):
        assert value.lower() not in record["last_question"].lower(), value


# 5
def test_case_b_until_complete(monkeypatch):
    def fail(args):
        pytest.fail(f"a tool ran with {args}")

    for name, spec in list(TOOLS.items()):
        monkeypatch.setitem(TOOLS, name, dataclasses.replace(spec, execute=fail))

    record, outcome = run_conversation("B1", "B2", "B3")

    assert outcome.route == "same_tool"
    assert record["steps"] == [
        {
            "tool": "create_ticket",
            "args": {
                "title": "Falla de autenticación",
                "description": "revisar el problema de autenticación",
                "priority": "Alta",
                "assignee": "Carlos",
            },
        }
    ]
    assert record["missing"] == []
    # Task 10a: the complete ticket awaits confirmation (test_case_b_reaches_confirmation).
    assert record["status"] == "awaiting_confirmation"
    assert outcome.reply.endswith(record["last_question"])
    assert "Falla de autenticación" in outcome.reply  # the fill of the title is shown


# 6
def test_changes_are_shown():
    ticket, _ = run_conversation("B1", "B2")
    medium = interpretation("provide_info", op("create_ticket", priority="media"))
    outcome = handle_turn(ticket, medium, TurnInput("Mejor media", NOW, SESSION))

    assert outcome.state_delta["pending"]["steps"][0]["args"]["priority"] == "media"
    assert [(c.field, c.kind, c.old, c.new) for c in outcome.changes] == [
        ("priority", "overwrite", "Alta", "media")
    ]
    assert "Alta" in outcome.reply and "media" in outcome.reply

    meeting, outcome = run_conversation("F1", "L1")
    assert meeting["steps"][0]["args"]["attendees"] == ["Juan", "Ana"]
    change_line = _lines(outcome.reply)[0]
    assert "Juan → Juan y Ana" in change_line  # the previous list, then the new one

    for act in ("provide_info", "request_action"):
        f1, _ = run_conversation("F1")
        outcome = handle_turn(
            f1, _as("L2", act, "explicit"), TurnInput(FIXED["L2"].message, NOW, SESSION)
        )
        record = outcome.state_delta["pending"]
        assert "attendees" not in record["steps"][0]["args"], act
        assert "attendees" in record["missing"], act
        assert [(c.field, c.kind) for c in outcome.changes] == [("attendees", "clear")]
        assert "Juan" in _lines(outcome.reply)[0], act  # the list it had before
        assert outcome.reply.endswith(record["last_question"])


# 7 (D9 *Caso H*; TASK.md line 447)
def test_repeated_request_changes_nothing():
    pending = a1_pending()
    outcome = handle_fixed(pending, "A1")
    assert outcome.route == "same_tool"
    assert outcome.state_delta == {}
    assert outcome.reply == pending["last_question"]
    assert outcome.changes == [] and outcome.rejected == []

    complete = handle_fixed(None, "H1").state_delta["pending"]
    outcome = handle_fixed(complete, "H1")
    assert outcome.state_delta == {}
    assert outcome.reply == confirmation_text(complete)  # task 10a: the confirmation again


# 8 (decision 1 of brief 08; the note is written in the present, so it is true here too)
def test_new_flow_note_is_true():
    turn = interpretation(
        "request_action", op("schedule_meeting", attendees=["Ana"], attendees_add=["ana"])
    )
    outcome = handle_turn(None, turn, TurnInput("Agenda una reunión con Ana", NOW, SESSION))
    record = outcome.state_delta["pending"]

    assert record["steps"][0]["args"] == {"attendees": ["Ana"]}
    attendees = TOOLS["schedule_meeting"].fields[4]
    assert replies.note_line(attendees, Note("attendees", "add_present", "ana")) in _lines(
        outcome.reply
    )
    assert outcome.reply.endswith(record["last_question"])


# 9 (decision 2 of brief 08)
@pytest.mark.parametrize("act", ["provide_info", "request_action"])
def test_pending_without_operations_repeats_the_question(act):
    pending = a1_pending()
    outcome = handle_turn(pending, interpretation(act), TurnInput("Eso", NOW, SESSION))

    assert outcome.reply == pending["last_question"]
    assert outcome.state_delta == {}


# Review 08-1 ----------------------------------------------------------------------------------


# Should-fix 1 (autodecided AB): with something pending, an out-of-catalog request is not
# dropped in silence (D10); a turn with no operations at all keeps the author's answer (a).
def test_pending_and_unknown_tool_adds_the_d10_line():
    pending = a1_pending()
    flight = interpretation("request_action", op("book_flight"))

    outcome = handle_turn(pending, flight, TurnInput("Mejor reserva un vuelo", NOW, SESSION))

    assert outcome.reply == f"{replies.UNKNOWN_TOOL_LINE}\n{pending['last_question']}"
    assert outcome.state_delta == {}

    nothing = handle_turn(
        pending, interpretation("request_action"), TurnInput("Mejor eso", NOW, SESSION)
    )
    assert nothing.reply == pending["last_question"]  # no D10 line: nothing was dropped
    assert nothing.state_delta == {}


# Should-fix 2: values are shown by their components, nothing converted or dropped.
_MEETING_FIELDS = {spec.name: spec for spec in TOOLS["schedule_meeting"].fields}


@pytest.mark.parametrize(
    ("field_name", "value", "shown"),
    [
        ("date", {"day": 40, "month": 11}, "día 40, mes 11"),
        ("date", {"day": 27, "month": 11, "year": 2026}, "día 27, mes 11, año 2026"),
        ("date", {"weekday": "viernes"}, "viernes"),
        ("date", {"relative_days": 0}, "hoy"),
        ("date", {"relative_days": 1}, "mañana"),
        ("date", {"relative_days": 2}, "pasado mañana"),
        ("date", {"relative_days": 5}, "5 días desde hoy"),
        ("time", {"hour": 25, "minute": 90}, "25:90"),
        ("time", {"hour": 9, "minute": 5}, "9:05"),
        ("time", {"hour": 10}, "las 10"),
        ("time", {"minute": 30}, "minuto 30"),
        ("duration_minutes", 30, "30"),
        ("attendees", ["Juan"], "Juan"),
        ("attendees", ["Juan", "Ana", "Luis"], "Juan, Ana y Luis"),
        ("title", "Revisión", "Revisión"),
    ],
)
def test_show_value_by_kind(field_name, value, shown):
    assert replies.show_value(_MEETING_FIELDS[field_name], value) == shown


# Review 09a-1, should-fix 1 (note (hh)): surrounding spaces are trimmed when a value is shown.
def test_show_value_trims_spaces():
    ticket = {spec.name: spec for spec in TOOLS["create_ticket"].fields}
    assert replies.show_value(ticket["assignee"], " Carlos ") == "Carlos"  # person
    assert replies.show_value(ticket["title"], "  Servidor caído ") == "Servidor caído"  # text
    assert replies.show_value(_MEETING_FIELDS["attendees"], [" Ana ", "Juan "]) == "Ana y Juan"


def test_case_d_reply_names_every_component():
    lines = _lines(handle_fixed(None, "D1").reply)
    assert "Anoté la fecha: día 40, mes 11." in lines
    assert "Anoté la hora: 25:90." in lines


# Review note 4: *Misma tool* applies only to a `collecting` record and, since task 10a, to one
# awaiting confirmation (test_correction_while_awaiting). Task 13: a clarification reads `K2` as
# its answer (rule 2, where `certainty` counts), never as *Misma tool*.
def test_same_tool_needs_a_collecting_record():
    clarifying = handle_fixed(None, "C1").state_delta["pending"]
    outcome = handle_fixed(clarifying, "K2")
    assert outcome.route == "clarification:regla_2"
    assert outcome.state_delta["pending"]["status"] == "collecting"


# Brief 09a ------------------------------------------------------------------------------------

_TICKET_FIELDS = {spec.name: spec for spec in TOOLS["create_ticket"].fields}

# A meeting with every field. Read against `NOW`, its date is 2026-11-20 (task 09b).
_COMPLETE_MEETING_MESSAGE = (
    "Agenda la revisión del servidor el 20 de noviembre a las 10:30, 30 minutos, con Juan y Ana"
)


def _meeting(**args: Any) -> TurnInterpretation:
    complete = {
        "title": "revisión del servidor",
        "date": {"day": 20, "month": 11},
        "time": {"hour": 10, "minute": 30},
        "duration_minutes": 30,
        "attendees": ["Juan", "Ana"],
    }
    return interpretation("request_action", op("schedule_meeting", **{**complete, **args}))


# 10 (TASK.md lines 186-200)
def test_invalid_and_missing_in_one_reply():
    outcome = handle_fixed(None, "X1")
    record = outcome.state_delta["pending"]

    assert record["invalid"] == [
        {"tool": "create_ticket", "field": "priority", "cause": "not_allowed"}
    ]
    assert record["missing"] == ["title", "assignee"]
    assert record["status"] == "collecting"
    assert record["steps"][0]["args"]["priority"] == "urgente"  # stored as said (D4 rule 7)
    assert outcome.invalid == record["invalid"]  # for the log

    lines = _lines(outcome.reply)
    fill = "Anoté la prioridad: urgente."
    invalid = replies.invalid_line(_TICKET_FIELDS["priority"], "not_allowed")
    assert "baja, media o alta" in invalid  # from `value_labels`, in the order of `allowed`
    assert lines.index(fill) < lines.index(invalid) < len(lines) - 1
    assert lines[-1] == record["last_question"]

    question = record["last_question"].lower()
    assert "la prioridad" in question
    for word in ("urgente", "alta", "media", "baja"):
        assert word not in question, word


# 11 (D4 rule 7 both ways)
def test_correction_clears_invalid():
    pending = handle_fixed(None, "X1").state_delta["pending"]
    high = interpretation("provide_info", op("create_ticket", priority="alta"))
    outcome = handle_turn(pending, high, TurnInput("Alta", NOW, SESSION))
    record = outcome.state_delta["pending"]

    assert [(c.field, c.kind, c.old, c.new) for c in outcome.changes] == [
        ("priority", "overwrite", "urgente", "alta")
    ]
    assert record["invalid"] == []
    assert record["missing"] == ["title", "assignee"]
    assert "la prioridad" not in record["last_question"]
    assert outcome.reply.endswith(record["last_question"])

    complete = handle_fixed(None, "H1").state_delta["pending"]
    assert complete["invalid"] == [] and complete["status"] == "awaiting_confirmation"
    urgent = interpretation("provide_info", op("create_ticket", priority="urgente"))
    outcome = handle_turn(complete, urgent, TurnInput("Mejor urgente", NOW, SESSION))
    record = outcome.state_delta["pending"]

    assert record["steps"][0]["args"]["priority"] == "urgente"  # a new invalid value replaces
    assert record["status"] == "collecting" and "confirmation_hash" not in record
    assert record["invalid"] == [
        {"tool": "create_ticket", "field": "priority", "cause": "not_allowed"}
    ]
    assert record["missing"] == []
    assert record["last_question"] is not None
    assert "la prioridad" in record["last_question"]
    assert outcome.reply.endswith(record["last_question"])


# 12 (D5.3)
@pytest.mark.parametrize("minutes", [0, 481])
def test_duration_out_of_range(minutes):
    outcome = handle_turn(
        None, _meeting(duration_minutes=minutes), TurnInput(_COMPLETE_MEETING_MESSAGE, NOW, SESSION)
    )
    record = outcome.state_delta["pending"]

    assert record["invalid"] == [
        {"tool": "schedule_meeting", "field": "duration_minutes", "cause": "out_of_range"}
    ]
    assert record["missing"] == []
    line = replies.invalid_line(_MEETING_FIELDS["duration_minutes"], "out_of_range")
    assert line in _lines(outcome.reply)
    assert "1" in line and "480" in line
    assert record["last_question"] == "Para programar una reunión necesito la duración en minutos."
    assert outcome.reply.endswith(record["last_question"])


# 13 (D9 *Caso H* with an invalid value; D42: no change, no state delta)
def test_repeated_invalid_request_changes_nothing():
    pending = handle_fixed(None, "X1").state_delta["pending"]
    outcome = handle_fixed(pending, "X1")

    assert outcome.route == "same_tool"
    assert outcome.state_delta == {}
    assert outcome.changes == []
    invalid = replies.invalid_line(_TICKET_FIELDS["priority"], "not_allowed")
    assert outcome.reply == f"{invalid}\n{pending['last_question']}"
    assert outcome.invalid == pending["invalid"]


# 14 became test 10 of brief 09b (`test_complete_meeting_awaits_confirmation`): a complete
# meeting now awaits confirmation instead of closing with the provisional template of 09a.


# Brief 10a ------------------------------------------------------------------------------------


def record_tools(monkeypatch) -> dict[str, list[dict]]:
    """Wrap every tool's `execute` to record its arguments; the real mock still runs, so the
    values it receives are checked against the contract (D12)."""
    calls: dict[str, list[dict]] = {name: [] for name in TOOLS}
    for name, spec in list(TOOLS.items()):

        def recorder(args, name=name, real=spec.execute):
            calls[name].append(args)
            return real(args)

        monkeypatch.setitem(TOOLS, name, dataclasses.replace(spec, execute=recorder))
    return calls


def nothing_ran(calls: dict[str, list[dict]]) -> bool:
    return all(not args for args in calls.values())


def g1_pending() -> dict:
    return handle_fixed(None, "G1").state_delta["pending"]


def confirm(*operations: dict[str, Any], confirms_tool: str | None = None) -> TurnInterpretation:
    return TurnInterpretation.model_validate(
        {"act": "confirm", "operations": list(operations), "confirms_tool": confirms_tool}
    )


G1_NORMALIZED = {"recipient": "Carlos", "message": "el ticket quedó resuelto", "channel": "email"}


# 1 (D7.1, D7.2, D43)
@pytest.mark.parametrize(
    ("key", "shown", "canonical"),
    [("G1", "- El canal: correo", "email"), ("H1", "- La prioridad: alta", "high")],
)
def test_complete_request_awaits_confirmation(monkeypatch, key, shown, canonical):
    calls = record_tools(monkeypatch)
    outcome = handle_fixed(None, key)
    record = outcome.state_delta["pending"]

    assert outcome.route == "R2"
    assert record["status"] == "awaiting_confirmation"
    assert record["missing"] == [] and record["invalid"] == []
    assert re.fullmatch(r"[0-9a-f]{32}", record["operation_id"])
    assert record["confirmation_hash"] == confirmation_hash(shown_values(record["steps"], NOW))

    lines = _lines(outcome.reply)
    assert shown in lines  # the Spanish label of the normalized value
    assert canonical not in outcome.reply
    assert outcome.reply.endswith(confirmation_text(record))
    assert lines[-1] == record["last_question"]

    question = record["last_question"].lower()
    for word in ("carlos", "resuelto", "correo", "alta", "baja", "media"):
        assert word not in question, word
    summary = pending_summary(record)
    assert "Status: awaiting_confirmation" in summary
    assert record["confirmation_hash"] not in summary
    assert nothing_ran(calls)


# 2 (TASK.md lines 311-335: case B ends with a confirmation, then a "yes")
def test_case_b_reaches_confirmation():
    b1 = handle_fixed(None, "B1").state_delta["pending"]
    b2 = handle_fixed(b1, "B2").state_delta["pending"]
    outcome = handle_fixed(b2, "B3")
    record = outcome.state_delta["pending"]

    assert b2["status"] == "collecting"
    assert record["status"] == "awaiting_confirmation"
    assert record["operation_id"] == b1["operation_id"]  # born in R2, kept by every merge
    assert "- La prioridad: alta" in _lines(outcome.reply)
    assert outcome.reply.endswith(confirmation_text(record))
    assert record["last_question"] == "¿Confirmas que quieres crear un ticket? Responde sí o no."


# 3 (case G, TASK.md lines 424-438; note (tt))
@pytest.mark.parametrize("recipient", ["Carlos", " Carlos "])
def test_yes_runs_the_pending_operation_once(monkeypatch, recipient):
    calls = record_tools(monkeypatch)
    g1 = json.loads(json.dumps(FIXED["G1"].interpretation))
    g1["operations"][0]["args"]["recipient"] = recipient
    pending = handle_turn(
        None, TurnInterpretation.model_validate(g1), TurnInput(FIXED["G1"].message, NOW, SESSION)
    ).state_delta["pending"]
    assert pending["steps"][0]["args"]["recipient"] == recipient  # kept as said (D4 rule 7)

    outcome = handle_fixed(pending, "G2")

    assert calls["send_notification"] == [G1_NORMALIZED]
    assert calls["create_ticket"] == [] and calls["schedule_meeting"] == []
    # Task 10b: the closed record also goes to `history` (D6), and the reply is the result line.
    assert outcome.state_delta.keys() == {"pending", "history"}
    assert outcome.state_delta["pending"] is None
    assert outcome.route == "confirmed"
    assert outcome.reply.startswith("Listo: envié la notificación ")


# 4 (D7.1, note (ss))
def test_yes_while_collecting_repeats_the_question(monkeypatch):
    calls = record_tools(monkeypatch)

    pending = a1_pending()
    outcome = handle_fixed(pending, "G2")
    assert outcome.state_delta == {}
    assert outcome.route == "confirm_incomplete"
    assert outcome.reply == pending["last_question"]

    pending = handle_fixed(None, "X1").state_delta["pending"]
    outcome = handle_fixed(pending, "G2")
    invalid = replies.invalid_line(_TICKET_FIELDS["priority"], "not_allowed")
    assert outcome.state_delta == {}
    assert outcome.reply == f"{invalid}\n{pending['last_question']}"
    assert nothing_ran(calls)


# 5 (what D4 requires of D7: a change never runs)
def test_yes_with_a_change_never_runs(monkeypatch):
    calls = record_tools(monkeypatch)

    pending = g1_pending()
    sms = confirm(op("send_notification", channel="SMS"))
    outcome = handle_turn(pending, sms, TurnInput("Sí, pero por SMS", NOW, SESSION))
    record = outcome.state_delta["pending"]

    assert outcome.route == "confirm_merged"
    assert _lines(outcome.reply)[0] == "Cambié el canal: correo → SMS."
    assert record["status"] == "awaiting_confirmation"
    assert record["confirmation_hash"] != pending["confirmation_hash"]
    assert record["operation_id"] == pending["operation_id"]
    assert outcome.reply.endswith(confirmation_text(record))

    pending = handle_fixed(None, "H1").state_delta["pending"]
    urgent = confirm(op("create_ticket", priority="urgente"))
    outcome = handle_turn(
        pending, urgent, TurnInput("Sí, pero con prioridad urgente", NOW, SESSION)
    )
    record = outcome.state_delta["pending"]

    assert record["status"] == "collecting"
    assert "confirmation_hash" not in record
    assert record["invalid"] == [
        {"tool": "create_ticket", "field": "priority", "cause": "not_allowed"}
    ]
    assert outcome.reply.endswith(record["last_question"])
    assert nothing_ran(calls)


# 6 (D38, notes (jj)/(qq)): a name the "yes" repeats but does not write is rejected
def test_yes_with_a_resent_name_is_rejected(monkeypatch):
    calls = record_tools(monkeypatch)
    pending = g1_pending()
    turn = confirm(op("send_notification", recipient="Carlos"))

    outcome = handle_turn(pending, turn, TurnInput(FIXED["G2"].message, NOW, SESSION))

    assert outcome.reply == f"{replies.REJECTED_NAME_LINE}\n{confirmation_text(pending)}"
    assert outcome.state_delta == {}
    assert [(r.key, r.name) for r in outcome.rejected] == [("recipient", "Carlos")]
    assert nothing_ran(calls)


# 7 (D7.4)
@pytest.mark.parametrize(
    ("confirms_tool", "runs"),
    [("create_ticket", False), ("send_notification", True), (None, True)],
)
def test_confirms_tool_veto(monkeypatch, confirms_tool, runs):
    calls = record_tools(monkeypatch)
    pending = g1_pending()

    outcome = handle_turn(
        pending, confirm(confirms_tool=confirms_tool), TurnInput("Sí", NOW, SESSION)
    )

    if runs:
        assert calls["send_notification"] == [G1_NORMALIZED]
        assert outcome.state_delta["pending"] is None  # and `history` gets the record (10b)
    else:
        assert nothing_ran(calls)
        assert outcome.state_delta == {}
        assert outcome.route == "confirm_vetoed"
        assert outcome.reply == confirmation_text(pending)


# 8 (D7.2)
def test_hash_mismatch_asks_again(monkeypatch):
    calls = record_tools(monkeypatch)
    pending = g1_pending()
    tampered = {**pending, "confirmation_hash": "0" * 64}

    outcome = handle_fixed(tampered, "G2")

    assert nothing_ran(calls)
    assert outcome.route == "confirm_hash_changed"
    # Task 09b, author's answer on note (ddd): the record is still valid, so a fixed line says
    # why the confirmation is shown again.
    assert outcome.reply == f"{replies.DATA_CHANGED_LINE}\n{confirmation_text(pending)}"
    assert outcome.state_delta == {"pending": pending}  # the recomputed hash is stored

    again = handle_fixed(outcome.state_delta["pending"], "G2")  # the next "yes" counts
    assert again.route == "confirmed"
    assert calls["send_notification"] == [G1_NORMALIZED]


# 9 (D9 *Caso H* while awaiting confirmation; TASK.md line 447)
def test_repeated_request_while_awaiting(monkeypatch):
    calls = record_tools(monkeypatch)
    pending = handle_fixed(None, "H1").state_delta["pending"]

    outcome = handle_fixed(pending, "H1")

    assert outcome.route == "same_tool"
    assert outcome.state_delta == {}
    assert outcome.reply == confirmation_text(pending)
    assert nothing_ran(calls)


# 10 (D3 *Misma tool* with `awaiting_confirmation`)
def test_correction_while_awaiting(monkeypatch):
    calls = record_tools(monkeypatch)
    pending = g1_pending()
    push = interpretation("provide_info", op("send_notification", channel="push"))

    outcome = handle_turn(pending, push, TurnInput("Mejor por push", NOW, SESSION))
    record = outcome.state_delta["pending"]

    assert outcome.route == "same_tool"
    assert record["status"] == "awaiting_confirmation"
    assert record["confirmation_hash"] != pending["confirmation_hash"]
    assert "- El canal: push" in _lines(outcome.reply)
    assert outcome.reply.endswith(confirmation_text(record))
    assert nothing_ran(calls)


# 11 (author's answer to brief 10a, option (a)): a "yes" with another tool runs nothing
def test_yes_with_another_tool_never_runs(monkeypatch):
    calls = record_tools(monkeypatch)
    pending = g1_pending()

    ticket = confirm(op("create_ticket", description="revisar el servidor"))
    message = "Sí, y crea un ticket para revisar el servidor"
    outcome = handle_turn(pending, ticket, TurnInput(message, NOW, SESSION))
    assert outcome.route == "confirm_other_tool"
    assert outcome.state_delta == {}
    assert outcome.reply == confirmation_text(pending)

    pizza = confirm(op("send_notification"), op("order_pizza"))
    outcome = handle_turn(pending, pizza, TurnInput("Sí, y pide una pizza", NOW, SESSION))
    assert outcome.state_delta == {}
    assert outcome.reply == f"{replies.UNKNOWN_TOOL_LINE}\n{confirmation_text(pending)}"

    collecting = a1_pending()
    meeting = confirm(op("schedule_meeting", attendees=["Juan"]))
    outcome = handle_turn(
        collecting, meeting, TurnInput("Sí, y agenda una reunión con Juan", NOW, SESSION)
    )
    assert outcome.state_delta == {}
    assert outcome.reply == collecting["last_question"]
    assert nothing_ran(calls)


# 12 (note (ll)): the D10 line before the confirmation repeated by *Misma tool*
def test_unknown_tool_while_awaiting_adds_the_d10_line():
    pending = g1_pending()
    flight = interpretation("provide_info", op("book_flight"))

    outcome = handle_turn(pending, flight, TurnInput("Y reserva un vuelo", NOW, SESSION))

    assert outcome.reply == f"{replies.UNKNOWN_TOOL_LINE}\n{confirmation_text(pending)}"
    assert outcome.state_delta == {}


# Review 10a-1 ---------------------------------------------------------------------------------


# Should-fix 1: a stored value that stopped being valid between the confirmation and the "yes"
# (a hand-edited or old record today; a date that expires from task 09b) sends the record back
# to `collecting`. The mock is never called with incomplete arguments.
def test_hash_mismatch_with_an_invalid_value_goes_back_to_collecting(monkeypatch):
    calls = record_tools(monkeypatch)
    pending = g1_pending()
    stale = json.loads(json.dumps(pending))
    stale["steps"][0]["args"]["channel"] = "fax"  # the hash still covers "correo"

    outcome = handle_fixed(stale, "G2")
    record = outcome.state_delta["pending"]

    assert outcome.route == "confirm_hash_changed"
    assert record["status"] == "collecting"
    assert "confirmation_hash" not in record
    assert record["invalid"] == [
        {"tool": "send_notification", "field": "channel", "cause": "not_allowed"}
    ]
    assert record["operation_id"] == pending["operation_id"]
    channel = TOOLS["send_notification"].fields[2]
    assert _lines(outcome.reply) == [
        replies.invalid_line(channel, "not_allowed"),
        record["last_question"],
    ]

    again = handle_fixed(record, "G2")  # a second "yes" repeats the question
    assert again.route == "confirm_incomplete"
    assert again.state_delta == {}
    assert nothing_ran(calls)


# Should-fix 2 (what D4 requires of D7): a `confirm` whose merge gives only a note never counts.
# Task 09b (note (ccc); D4 to D7): checked where it matters, on a meeting with Juan that awaits
# confirmation. Without the note in the condition, this "yes" would run the meeting.
def test_yes_with_a_note_never_counts(monkeypatch):
    calls = record_tools(monkeypatch)
    message = "Agenda la revisión del servidor el 20 de noviembre a las 10:30, 30 minutos, con Juan"
    with_juan = _meeting(attendees=["Juan"])
    pending = handle_turn(None, with_juan, TurnInput(message, NOW, SESSION)).state_delta["pending"]
    assert pending["status"] == "awaiting_confirmation"
    turn = confirm(op("schedule_meeting", attendees_remove=["Ana"]))

    outcome = handle_turn(pending, turn, TurnInput("Sí, y quita a Ana", NOW, SESSION))

    attendees = TOOLS["schedule_meeting"].fields[4]
    assert outcome.route == "confirm_merged"
    assert outcome.state_delta == {}
    assert outcome.reply == "\n".join(
        [
            replies.note_line(attendees, Note("attendees", "remove_absent", "Ana")),
            confirmation_text(pending),
        ]
    )
    assert nothing_ran(calls)

    yes = handle_fixed(pending, "G2")  # a plain "yes" afterwards runs the meeting once
    assert yes.route == "confirmed"
    [args] = calls["schedule_meeting"]
    assert (args["date"], args["time"]) == ("2026-11-20", "10:30")
    assert args["attendees"] == ["Juan"]


# Review note 4 (autodecided AJ): a "yes" that repeats a stored value, or a name written in the
# message, changes nothing after normalization (D4 rule 5), so it counts as a yes.
@pytest.mark.parametrize(
    ("args", "message"),
    [({"channel": "correo"}, "Sí, por correo"), ({"recipient": "Carlos"}, "Sí, envíalo a Carlos")],
)
def test_yes_repeating_a_stored_value_counts(monkeypatch, args, message):
    calls = record_tools(monkeypatch)
    turn = confirm(op("send_notification", **args))
    outcome = handle_turn(g1_pending(), turn, TurnInput(message, NOW, SESSION))

    assert outcome.route == "confirmed"
    assert calls["send_notification"] == [G1_NORMALIZED]


# Brief 10b ------------------------------------------------------------------------------------


def fixed_result(tool_name: str, result_id: str = "abc-123") -> dict[str, Any]:
    """A tool result as the mocks return it, with an ID and a `message` that must not be shown."""
    return {"success": True, TOOLS[tool_name].result_id_field: result_id, "message": "MOCK-MESSAGE"}


def tools_return_fixed_results(monkeypatch) -> dict[str, list[dict]]:
    """Every tool returns `fixed_result`; the arguments it got are recorded."""
    calls: dict[str, list[dict]] = {name: [] for name in TOOLS}
    for name, spec in list(TOOLS.items()):

        def double(args, name=name):
            calls[name].append(args)
            return fixed_result(name)

        monkeypatch.setitem(TOOLS, name, dataclasses.replace(spec, execute=double))
    return calls


def old_entries(count: int) -> list[dict]:
    return [{"operation_id": f"old-{i}", "status": "completed", "steps": []} for i in range(count)]


# 1 (decision 1, notes 4 and 5): no turn needed, so `schedule_meeting` is covered before 09b.
@pytest.mark.parametrize("name", list(TOOLS))
def test_result_line_per_tool(name):
    tool = TOOLS[name]
    # Task 11: the line gets the ID the core read once (criterion 12), not the whole result.
    line = replies.result_line(tool, "X-1")

    assert line == f"Listo: {tool.done_phrase} X-1."


# Review 10b-1, note 2: the phrase and the ID field come from the registry, never from tool
# names (TASK.md line 562): a tool outside the catalog with its own ID field.
def test_result_line_reads_the_registry_id_field():
    room = dataclasses.replace(
        TOOLS["create_ticket"],
        name="book_room",
        done_phrase="reservé la sala",
        result_id_field="room_id",
    )
    result = {"success": True, "room_id": "R-7", "ticket_id": "WRONG", "message": "otro texto"}

    # Task 11: the core reads the ID (`settled`) and the line writes the one it read.
    run = settled(InMemoryExecutions(), room.name, room.result_id_field, "key", {}, result)
    assert run.result_id == "R-7"
    assert replies.step_line(room, run.outcome, run.result_id) == "Listo: reservé la sala R-7."


# 2 (notes 4 and 5): the reply reads the ID the tool returned and never shows its `message` nor
# a canonical value.
@pytest.mark.parametrize("key", ["G1", "H1"])
def test_yes_reply_reads_the_result(monkeypatch, key):
    tools_return_fixed_results(monkeypatch)
    pending = handle_fixed(None, key).state_delta["pending"]
    tool = TOOLS[pending["tools"][0]]

    outcome = handle_fixed(pending, "G2")

    assert outcome.route == "confirmed"
    assert outcome.reply == f"Listo: {tool.done_phrase} abc-123."
    # Task 11 (decision G): each step tried also carries its outcome.
    assert [(r.tool, r.result_id, r.outcome) for r in outcome.results] == [
        (tool.name, "abc-123", "ran")
    ]
    for text in ("MOCK-MESSAGE", "email", "high"):
        assert text not in outcome.reply


# 3 (D6, decision 2): the entry keeps the normalized values the tool received, not `args`.
@pytest.mark.parametrize("recipient", ["Carlos", " Carlos "])
def test_confirmed_operation_goes_to_history(monkeypatch, recipient):
    calls = tools_return_fixed_results(monkeypatch)
    g1 = json.loads(json.dumps(FIXED["G1"].interpretation))
    g1["operations"][0]["args"]["recipient"] = recipient
    pending = handle_turn(
        None, TurnInterpretation.model_validate(g1), TurnInput(FIXED["G1"].message, NOW, SESSION)
    ).state_delta["pending"]

    outcome = handle_turn(
        pending, fixed("G2"), TurnInput(FIXED["G2"].message, NOW, SESSION), history=()
    )

    entry = {
        "operation_id": pending["operation_id"],
        "status": "completed",
        "steps": [
            {
                "tool": "send_notification",
                "values": G1_NORMALIZED,
                "outcome": "ran",  # task 11, decision A
                "result": fixed_result("send_notification"),
            }
        ],
    }
    assert outcome.state_delta == {"pending": None, "history": [entry]}
    assert calls["send_notification"] == [G1_NORMALIZED]
    json.dumps(outcome.state_delta)  # it goes to the session state as it is


# 4 (D6): the last ten, newest last, and the list received is not changed.
@pytest.mark.parametrize(("before", "after"), [(10, 10), (3, 4)])
def test_history_keeps_the_last_ten(monkeypatch, before, after):
    tools_return_fixed_results(monkeypatch)
    history = old_entries(before)
    received = json.loads(json.dumps(history))
    pending = g1_pending()

    outcome = handle_turn(
        pending, fixed("G2"), TurnInput(FIXED["G2"].message, NOW, SESSION), history=history
    )

    new = outcome.state_delta["history"]
    assert len(new) == after
    assert new[-1]["operation_id"] == pending["operation_id"]
    assert new[:-1] == received[before - after + 1 :]  # with 10, "old-0" is out
    assert history == received


# 5 (D6): only a closing writes `history`. No fixed turn runs a tool here; since task 12 a change
# of intention (`abandoned`) and a "no" (`cancelled`) close the pending record without running,
# and since task 13 so do rules 4 and 1 of a clarification (the record of `C1`).
def test_only_a_closing_writes_history():
    history = old_entries(2)
    outcomes = []
    clarifying = handle_fixed(None, "C1").state_delta["pending"]
    for pending in (None, a1_pending(), clarifying):
        outcomes += [
            handle_turn(
                pending, fixed(key), TurnInput(FIXED[key].message, NOW, SESSION), history=history
            )
            for key in FIXED
        ]
    pending = None
    for key in ("B1", "B2", "B3"):
        outcome = handle_turn(
            pending, fixed(key), TurnInput(FIXED[key].message, NOW, SESSION), history=history
        )
        pending = outcome.state_delta.get("pending", pending)
        outcomes.append(outcome)

    closings = {
        "cancelled": "cancelled",
        "other_tool": "abandoned",
        "clarification:regla_1": "cancelled",
        "clarification:regla_4": "abandoned",
    }
    for outcome in outcomes:
        assert outcome.route != "confirmed"
        assert outcome.results == []
        route = outcome.route
        status = closings.get(route.split(":")[0]) or closings.get(":".join(route.split(":")[:2]))
        if status is None:
            assert "history" not in outcome.state_delta, outcome.route
            continue
        new = outcome.state_delta["history"]
        assert new[:-1] == history and new[-1]["status"] == status, outcome.route
        # Only R3 creates nothing after a change of intention (task 14a).
        assert (outcome.state_delta["pending"] is None) == route.endswith(
            ("cancelled", "regla_1", ":R3")
        ), route
    assert {o.route for o in outcomes} >= {
        "cancelled",
        "other_tool:R2",
        "other_tool:R4",
        "clarification:regla_1",
        "clarification:regla_4:R2",
    }


# Brief 08b ------------------------------------------------------------------------------------

# The phase 0 run of task 06 where `L4` came split in two operations (D32; finding 8 of
# 06-round-1). Read as saved, so the evidence is the same output the code now handles.
PHASE0_RUN = (
    Path(__file__).resolve().parent.parent
    / "phase0"
    / "runs"
    / "20261117T073844Z-gemini-3.5-flash-lite.jsonl"
)


def measured_l4() -> tuple[TurnInterpretation, str]:
    """The model's raw `L4` output, validated as the interpreter would, and its message."""
    (record,) = [record for record in read_records(PHASE0_RUN) if record["id"] == "L4"]
    return TurnInterpretation.model_validate_json(record["raw"]), record["message"]


def f1_pending() -> dict:
    return handle_fixed(None, "F1").state_delta["pending"]


def h1_pending() -> dict:
    return handle_fixed(None, "H1").state_delta["pending"]


def with_dependency(operation: dict[str, Any], depends_on: str | None) -> dict[str, Any]:
    return {**operation, "depends_on": depends_on}


# 1 (D11.1, D35; the `request_action` variant is finding 10 of 06-round-1)
@pytest.mark.parametrize("certainty", ["explicit", "plausible"])
@pytest.mark.parametrize("act", ["request_action", "provide_info"])
def test_measured_l4_is_joined(act, certainty):
    measured, message = measured_l4()
    first, second = measured.operations
    assert len(measured.operations) == 2 and first.tool == second.tool == "schedule_meeting"
    turn = measured.model_copy(
        update={
            "act": Act(act),
            "operations": [first, second.model_copy(update={"certainty": Certainty(certainty)})],
        }
    )
    pending = f1_pending()

    outcome = handle_turn(pending, turn, TurnInput(message, NOW, SESSION))
    single = handle_fixed(pending, "L4")

    assert outcome.route == "same_tool"
    assert outcome.state_delta["pending"]["steps"][0]["args"]["attendees"] == ["Ana"]
    assert outcome.reply == single.reply
    assert outcome.state_delta == single.state_delta


# 2 (D11.1, pending (a): keys without absent values; a "" never overwrites)
def test_split_ops_ignore_absent_keys():
    turn = interpretation(
        "provide_info",
        op("schedule_meeting", title="Revisión", attendees_remove=["Juan"]),
        op("schedule_meeting", title="", attendees_add=["Ana"]),
    )
    message = "Ponle de título Revisión, quita a Juan y agrega a Ana"

    outcome = handle_turn(f1_pending(), turn, TurnInput(message, NOW, SESSION))
    args = outcome.state_delta["pending"]["steps"][0]["args"]

    assert outcome.route == "same_tool"
    assert args["title"] == "Revisión"
    assert args["attendees"] == ["Ana"]


# 3 (D11.1 with a record awaiting confirmation, D47)
def test_split_ops_while_awaiting_confirmation(monkeypatch):
    calls = record_tools(monkeypatch)
    pending = h1_pending()
    turn = interpretation(
        "provide_info",
        op("create_ticket", priority="media"),
        op("create_ticket", assignee="Ana"),
    )

    outcome = handle_turn(
        pending, turn, TurnInput("Mejor prioridad media y asígnalo a Ana", NOW, SESSION)
    )
    record = outcome.state_delta["pending"]
    args = record["steps"][0]["args"]

    assert outcome.route == "same_tool"
    assert record["status"] == "awaiting_confirmation"
    assert record["confirmation_hash"] != pending["confirmation_hash"]
    assert args["priority"] == "media"
    assert args["assignee"] == "Ana"
    assert outcome.reply.endswith(confirmation_text(record))
    assert nothing_ran(calls)


# 4 (D11.1: a repeated key; D35 and pending (b): different `depends_on`, None included)
@pytest.mark.parametrize(
    ("operations", "message"),
    [
        (
            [
                op("schedule_meeting", attendees_add=["Ana"]),
                op("schedule_meeting", attendees_add=["Luis"]),
            ],
            "Agrega a Ana y a Luis",
        ),
        (
            [
                with_dependency(op("schedule_meeting", attendees_remove=["Juan"]), None),
                with_dependency(op("schedule_meeting", attendees_add=["Ana"]), "create_ticket"),
            ],
            "Quita a Juan y agrega a Ana",
        ),
        (
            [
                with_dependency(
                    op("schedule_meeting", attendees_remove=["Juan"]), "send_notification"
                ),
                with_dependency(op("schedule_meeting", attendees_add=["Ana"]), "create_ticket"),
            ],
            "Quita a Juan y agrega a Ana",
        ),
    ],
    ids=["same_bucket", "none_and_a_dependency", "two_dependencies"],
)
def test_overlapping_split_ops_follow_d11(operations, message):
    outcome = handle_turn(
        f1_pending(), interpretation("provide_info", *operations), TurnInput(message, NOW, SESSION)
    )

    assert outcome.route == "d11"
    assert outcome.reply == replies.ONE_PER_TOOL
    assert outcome.state_delta == {}


_TWO_DISJOINT_TICKETS = interpretation(
    "request_action",
    op("create_ticket", title="Servidor caído"),
    op("create_ticket", priority="alta"),
)
_TWO_TICKETS_MESSAGE = "Crea un ticket titulado Servidor caído y otro de prioridad alta"


def _mixed_turn(certainty: str) -> TurnInterpretation:
    return interpretation(
        "request_action",
        op("schedule_meeting", attendees_remove=["Juan"]),
        op("schedule_meeting", attendees_add=["Ana"]),
        op("create_ticket", certainty, description="revisar el servidor"),
    )


# 5 (D11.1 only in *Misma tool*: "dos tickets pedidos no se vuelven uno"; should-fix 1 of
# 06-round-3). Joined outside the branch, (i) would give `other_tool:R3`, (ii) `R2` and
# (iv) `confirm_merged`.
@pytest.mark.parametrize(
    ("pending_key", "turn", "message"),
    [
        (
            "F1",
            _mixed_turn("explicit"),
            "Quita a Juan, agrega a Ana y crea un ticket para revisar el servidor",
        ),
        (
            "F1",
            _mixed_turn("plausible"),
            "Quita a Juan, agrega a Ana y crea un ticket para revisar el servidor",
        ),
        (None, _TWO_DISJOINT_TICKETS, _TWO_TICKETS_MESSAGE),
        ("F1", _TWO_DISJOINT_TICKETS, _TWO_TICKETS_MESSAGE),
        (
            "H1",
            confirm(op("create_ticket", priority="media"), op("create_ticket", assignee="Ana")),
            "Sí, con prioridad media y asígnalo a Ana",
        ),
    ],
    ids=["mixed_explicit", "mixed_plausible", "nothing_pending", "other_tool_pending", "confirm"],
)
def test_split_ops_outside_same_tool_follow_d11(monkeypatch, pending_key, turn, message):
    calls = record_tools(monkeypatch)
    pending = None
    if pending_key is not None:
        pending = handle_fixed(None, pending_key).state_delta["pending"]

    outcome = handle_turn(pending, turn, TurnInput(message, NOW, SESSION))

    assert outcome.route == "d11"
    assert outcome.reply == replies.ONE_PER_TOOL
    assert outcome.state_delta == {}
    assert nothing_ran(calls)


# Brief 09b ------------------------------------------------------------------------------------


# 8 (case D, TASK.md lines 186-200 and 361-371; notes (yy) and (ii))
def test_case_d_reports_date_and_time():
    outcome = handle_fixed(None, "D1")
    record = outcome.state_delta["pending"]

    assert record["status"] == "collecting"
    assert record["invalid"] == [
        {"tool": "schedule_meeting", "field": "date", "cause": "nonexistent"},
        {"tool": "schedule_meeting", "field": "time", "cause": "bad_format"},
    ]
    assert record["missing"] == ["title", "duration_minutes", "attendees"]
    assert outcome.invalid == record["invalid"]  # for the log

    lines = _lines(outcome.reply)
    date_line = next(i for i, line in enumerate(lines) if "La fecha indicada no existe" in line)
    time_line = next(
        i for i, line in enumerate(lines) if "La hora está fuera del formato válido" in line
    )
    assert (
        lines.index("Anoté la fecha: día 40, mes 11.")
        < lines.index("Anoté la hora: 25:90.")
        < date_line
        < time_line
        < len(lines) - 1
    )
    assert lines[-1] == record["last_question"]

    question = record["last_question"]
    assert "la fecha" in question and "la hora" in question
    for number in ("40", "25", "90"):
        assert number not in question, number


# 9 (D5.1: a weekday is its next occurrence; fixed `W1`, "el viernes a las 10")
def test_weekday_meeting():
    pending = handle_fixed(None, "W1").state_delta["pending"]
    assert pending["missing"] == ["title", "duration_minutes"]
    assert pending["invalid"] == []

    rest = interpretation(
        "provide_info", op("schedule_meeting", title="Planeación", duration_minutes=30)
    )
    outcome = handle_turn(pending, rest, TurnInput("Planeación, 30 minutos", NOW, SESSION))
    record = outcome.state_delta["pending"]

    assert record["status"] == "awaiting_confirmation"
    # Review 09b-2, note 4: after a merge with changes, the hash binds what was shown at `now`.
    assert record["confirmation_hash"] == confirmation_hash(shown_values(record["steps"], NOW))
    lines = _lines(outcome.reply)
    assert "- La fecha: 2026-11-20" in lines
    assert "- La hora: 10:00" in lines


# 10 (replaces test_complete_meeting_waits_for_dates of brief 09a)
def test_complete_meeting_awaits_confirmation(monkeypatch):
    calls = record_tools(monkeypatch)
    outcome = handle_turn(None, _meeting(), TurnInput(_COMPLETE_MEETING_MESSAGE, NOW, SESSION))
    record = outcome.state_delta["pending"]

    assert record["status"] == "awaiting_confirmation"
    assert record["confirmation_hash"] == confirmation_hash(shown_values(record["steps"], NOW))
    lines = _lines(outcome.reply)
    assert "- La fecha: 2026-11-20" in lines
    assert "- La hora: 10:30" in lines
    assert outcome.reply.endswith(confirmation_text(record))

    again = handle_turn(record, _meeting(), TurnInput(_COMPLETE_MEETING_MESSAGE, NOW, SESSION))
    assert again.route == "same_tool"
    assert again.state_delta == {}
    assert again.reply == confirmation_text(record)
    assert nothing_ran(calls)


_TOMORROW_MESSAGE = "Agenda la revisión del servidor mañana a las 10, 30 minutos, con Juan y Ana"
_TODAY_MESSAGE = "Agenda la revisión del servidor hoy a las 10, 30 minutos, con Juan y Ana"


# 12 (D7.2; note (ddd), author's answer: option (a))
def test_tomorrow_changes_the_hash_on_the_next_day(monkeypatch):
    calls = record_tools(monkeypatch)
    tomorrow = _meeting(date={"relative_days": 1}, time={"hour": 10})
    pending = handle_turn(None, tomorrow, TurnInput(_TOMORROW_MESSAGE, NOW, SESSION)).state_delta[
        "pending"
    ]
    assert pending["status"] == "awaiting_confirmation"

    next_day = datetime(2026, 11, 18, 8, 0)
    outcome = handle_turn(pending, fixed("G2"), TurnInput(FIXED["G2"].message, next_day, SESSION))
    record = outcome.state_delta["pending"]

    assert outcome.route == "confirm_hash_changed"
    assert record["status"] == "awaiting_confirmation"
    assert record["confirmation_hash"] != pending["confirmation_hash"]
    assert record["operation_id"] == pending["operation_id"]
    assert outcome.reply.startswith(f"{replies.DATA_CHANGED_LINE}\n")
    # Review 09b-2, note 3: the exact wording the author approved (decision A).
    assert replies.DATA_CHANGED_LINE == (
        "Los datos cambiaron desde que te los mostré; revísalos antes de confirmar."
    )
    assert "- La fecha: 2026-11-19" in _lines(outcome.reply)
    assert nothing_ran(calls)

    again = handle_turn(record, fixed("G2"), TurnInput(FIXED["G2"].message, next_day, SESSION))
    assert again.route == "confirmed"
    [args] = calls["schedule_meeting"]
    assert args["date"] == "2026-11-19"
    assert args["time"] == "10:00"


# 13 (decision F of brief 09b): a time that passed while the record waited
def test_time_that_passed_while_pending(monkeypatch):
    calls = record_tools(monkeypatch)
    today_at_10 = _meeting(date={"relative_days": 0}, time={"hour": 10})
    pending = handle_turn(None, today_at_10, TurnInput(_TODAY_MESSAGE, NOW, SESSION)).state_delta[
        "pending"
    ]
    assert pending["status"] == "awaiting_confirmation"
    later = datetime(2026, 11, 17, 10, 30)

    outcome = handle_turn(pending, today_at_10, TurnInput(_TODAY_MESSAGE, later, SESSION))
    record = outcome.state_delta["pending"]  # a KeyError here: the no-change branch kept {}

    assert outcome.route == "same_tool"
    assert outcome.changes == []
    assert record["status"] == "collecting"
    assert "confirmation_hash" not in record
    assert record["invalid"] == [{"tool": "schedule_meeting", "field": "time", "cause": "past"}]
    assert record["missing"] == []
    lines = _lines(outcome.reply)
    assert "La hora indicada ya pasó." in lines
    assert lines[-1] == record["last_question"]
    assert "la hora" in record["last_question"]

    yes = handle_turn(pending, fixed("G2"), TurnInput(FIXED["G2"].message, later, SESSION))
    assert yes.route == "confirm_hash_changed"
    assert yes.state_delta["pending"]["status"] == "collecting"
    assert not yes.reply.startswith(replies.DATA_CHANGED_LINE)  # the invalid line explains it
    assert nothing_ran(calls)


# Review 09b-1 ---------------------------------------------------------------------------------


# Should-fix 1 (autodecided AT): a "yes" that brings another tool (D45) or is vetoed by
# `confirms_tool` (D7.4) applies nothing of the message, but the record is assessed again against
# `now` as decision F does. "Hoy a las 10" confirmed at 09:00 and that "yes" at 10:30: the reply
# never shows a confirmation without its time line; it reports the time and asks for it.
@pytest.mark.parametrize(
    ("turn", "message", "route"),
    [
        (
            confirm(op("create_ticket", description="revisar el servidor")),
            "Sí, y crea un ticket para revisar el servidor",
            "confirm_other_tool",
        ),
        (confirm(confirms_tool="create_ticket"), "Sí, crea el ticket", "confirm_vetoed"),
    ],
)
def test_turned_away_yes_reassesses_against_now(monkeypatch, turn, message, route):
    calls = record_tools(monkeypatch)
    today_at_10 = _meeting(date={"relative_days": 0}, time={"hour": 10})
    pending = handle_turn(None, today_at_10, TurnInput(_TODAY_MESSAGE, NOW, SESSION)).state_delta[
        "pending"
    ]
    later = datetime(2026, 11, 17, 10, 30)

    outcome = handle_turn(pending, turn, TurnInput(message, later, SESSION))
    record = outcome.state_delta["pending"]

    assert outcome.route == route
    assert record["status"] == "collecting"
    assert "confirmation_hash" not in record
    assert record["invalid"] == [{"tool": "schedule_meeting", "field": "time", "cause": "past"}]
    assert record["steps"] == pending["steps"]  # nothing of the message got in
    lines = _lines(outcome.reply)
    assert "La hora indicada ya pasó." in lines
    assert lines[-1] == record["last_question"]
    assert not any(line.startswith("Voy a") for line in lines)  # no confirmation without time
    assert nothing_ran(calls)

    # At 09:30 nothing has expired: the state stays as it was (D45, D7.4).
    still = handle_turn(pending, turn, TurnInput(message, datetime(2026, 11, 17, 9, 30), SESSION))
    assert still.route == route
    assert still.state_delta == {}
    assert still.reply == confirmation_text(pending)


# Should-fix 2, item 6 (decision C): the wording of `mismatch` and of a past date in a reply.
@pytest.mark.parametrize(
    ("date", "line"),
    [
        (
            {"weekday": "jueves", "day": 27, "month": 11},
            "Las partes de la fecha que indicaste no dan el mismo día.",
        ),
        ({"day": 10, "month": 11, "year": 2026}, "La fecha indicada ya pasó."),
    ],
)
def test_date_invalid_lines_in_the_reply(date, line):
    outcome = handle_turn(
        None, _meeting(date=date), TurnInput(_COMPLETE_MEETING_MESSAGE, NOW, SESSION)
    )
    record = outcome.state_delta["pending"]

    assert record["status"] == "collecting"
    lines = _lines(outcome.reply)
    assert line in lines
    assert lines[-1] == record["last_question"] == "Para programar una reunión necesito la fecha."


# Brief 11 -------------------------------------------------------------------------------------


def counting(monkeypatch, name: str, execute=None) -> list[dict]:
    """Replace `name`'s `execute` by a double that records its arguments and then calls
    `execute` (the real mock by default)."""
    spec, calls = TOOLS[name], []
    inner = execute or spec.execute

    def double(args):
        calls.append(args)
        return inner(args)

    monkeypatch.setitem(TOOLS, name, dataclasses.replace(spec, execute=double))
    return calls


def turn_in(
    pending: dict | None,
    key: str,
    executions: InMemoryExecutions,
    session: str = SESSION,
    history: list[dict] | tuple = (),
) -> TurnOutcome:
    """`handle_turn` on a fixed interpretation with an explicit execution registry."""
    turn_input = TurnInput(FIXED[key].message, NOW, session)
    return handle_turn(pending, fixed(key), turn_input, history=history, executions=executions)


def h1_key(session: str = SESSION) -> str:
    """The idempotency key of `H1`'s values (D9.1)."""
    pending = handle_fixed(None, "H1").state_delta["pending"]
    [step] = shown_values(pending["steps"], NOW)
    return execution_key(session, "create_ticket", step["normalized"])


def failure_mock(name: str, mode: str):
    spec = TOOLS[name]
    return simulated(spec.contract, spec.result_id_field, mode)


REUSED_H1 = "Ya creé el ticket {} con estos mismos datos; no lo repetí."
FAILED_H1 = (
    "No pude crear un ticket: la herramienta respondió con un error. "
    "Si me lo pides de nuevo, lo intento otra vez."
)
UNCERTAIN_H1 = (
    "No pude confirmar si llegué a crear un ticket; no lo voy a reintentar para no duplicarlo."
)


# 3 (a) (TASK.md line 447; D9 *Caso H*, D40): repeated while pending, nothing runs or is reserved.
def test_case_h_repeated_while_pending(monkeypatch):
    calls = counting(monkeypatch, "create_ticket")
    executions = InMemoryExecutions()

    collecting = turn_in(None, "A1", executions).state_delta["pending"]
    again = turn_in(collecting, "A1", executions)
    assert again.state_delta == {}
    assert again.reply == collecting["last_question"]

    awaiting = turn_in(None, "H1", executions).state_delta["pending"]
    again = turn_in(awaiting, "H1", executions)
    assert again.state_delta == {}
    assert again.reply == confirmation_text(awaiting)

    assert calls == []
    assert executions.snapshot() == {}


# 4 (b) (TASK.md lines 443-449, D9.1): the same request after it ran asks again (limit of D9.1)
# and the "yes" gives the stored result without running.
def test_case_h_same_request_after_running(monkeypatch):
    calls = counting(monkeypatch, "create_ticket")
    executions = InMemoryExecutions()

    first_record = turn_in(None, "H1", executions).state_delta["pending"]
    first = turn_in(first_record, "B4", executions)
    assert len(calls) == 1
    ticket_id = first.state_delta["history"][0]["steps"][0]["result"]["ticket_id"]

    again = turn_in(None, "H1", executions)
    second_record = again.state_delta["pending"]
    assert second_record["status"] == "awaiting_confirmation"
    assert second_record["operation_id"] != first_record["operation_id"]
    assert again.reply.endswith(confirmation_text(second_record))

    second = turn_in(second_record, "B4", executions, history=first.state_delta["history"])

    assert len(calls) == 1
    assert second.reply == REUSED_H1.format(ticket_id)
    assert second.state_delta["pending"] is None
    entries = second.state_delta["history"]
    assert [entry["status"] for entry in entries] == ["completed", "completed"]
    assert [entry["steps"][0]["outcome"] for entry in entries] == ["ran", "reused"]
    assert entries[1]["steps"][0]["result"] == entries[0]["steps"][0]["result"]
    assert entries[1]["operation_id"] == second_record["operation_id"]


# 5 (c) (D9 *Caso H*): the turn that ran was not saved, so the same record gets a second "yes".
def test_resent_yes_after_unsaved_turn(monkeypatch):
    calls = counting(monkeypatch, "create_ticket")
    executions = InMemoryExecutions()
    record = turn_in(None, "H1", executions).state_delta["pending"]

    first = turn_in(record, "B4", executions)
    resent = turn_in(record, "B4", executions)

    assert len(calls) == 1
    [ran], [reused] = first.results, resent.results
    assert (ran.outcome, reused.outcome) == ("ran", "reused")
    assert reused.result_id == ran.result_id
    assert resent.reply == REUSED_H1.format(ran.result_id)


# 6 (d) (D9.2, TASK.md line 451): `started` without `done` is never run again.
def test_started_without_done_is_uncertain(monkeypatch):
    calls = counting(monkeypatch, "create_ticket")
    executions = InMemoryExecutions()
    assert executions.reserve(h1_key()) is None  # a run that never ended

    record = turn_in(None, "H1", executions).state_delta["pending"]
    outcome = turn_in(record, "B4", executions)

    assert calls == []
    assert outcome.reply == UNCERTAIN_H1
    assert "Listo" not in outcome.reply
    assert outcome.state_delta["pending"] is None
    [entry] = outcome.state_delta["history"]
    assert entry["status"] == "uncertain"
    assert entry["steps"][0]["outcome"] == "uncertain"
    assert entry["steps"][0]["result"] is None and "error" not in entry["steps"][0]
    assert executions.snapshot() == {h1_key(): {"status": "started"}}


def raise_runtime_error(args):
    raise RuntimeError("boom")


# 7 (D9.2; decision B): a tool that raises may have done its work: uncertain, never retried.
@pytest.mark.parametrize(
    ("make_execute", "error"),
    [
        (lambda: failure_mock("create_ticket", "timeout"), "TimeoutError"),
        (lambda: raise_runtime_error, "RuntimeError"),
    ],
    ids=["timeout", "runtime_error"],
)
def test_timeout_is_uncertain_and_never_retried(monkeypatch, make_execute, error):
    calls = counting(monkeypatch, "create_ticket", make_execute())
    executions = InMemoryExecutions()
    record = turn_in(None, "H1", executions).state_delta["pending"]

    outcome = turn_in(record, "B4", executions)  # the turn does not raise

    assert len(calls) == 1
    assert outcome.reply == UNCERTAIN_H1
    assert outcome.state_delta["pending"] is None  # note (hhh): the state is written
    [entry] = outcome.state_delta["history"]
    assert entry["status"] == "uncertain"
    assert entry["steps"][0]["error"] == error and entry["steps"][0]["result"] is None
    assert "boom" not in json.dumps(outcome.state_delta)  # the type, never the text
    [run] = outcome.results
    assert (run.outcome, run.error) == ("uncertain", error)
    assert executions.snapshot() == {h1_key(): {"status": "started"}}

    record = turn_in(None, "H1", executions).state_delta["pending"]
    again = turn_in(record, "B4", executions)
    assert len(calls) == 1
    assert again.reply == UNCERTAIN_H1


# 8 (e) (D9.3; decision F): a failure is reported, the key is released and asking again runs it.
def test_failed_result_releases_the_key(monkeypatch):
    calls = counting(monkeypatch, "create_ticket", failure_mock("create_ticket", "failure"))
    executions = InMemoryExecutions()
    record = turn_in(None, "H1", executions).state_delta["pending"]

    failed = turn_in(record, "B4", executions)

    assert len(calls) == 1
    assert failed.reply == FAILED_H1
    assert "Listo" not in failed.reply and mock_tools.FAILURE_MESSAGE not in failed.reply
    [entry] = failed.state_delta["history"]
    assert entry["status"] == "failed"
    assert entry["steps"][0]["outcome"] == "failed"
    assert entry["steps"][0]["result"] == {
        "success": False,
        "ticket_id": None,
        "message": mock_tools.FAILURE_MESSAGE,
    }
    assert executions.snapshot() == {}

    healthy = counting(monkeypatch, "create_ticket", mock_tools.create_ticket)
    record = turn_in(None, "H1", executions).state_delta["pending"]
    again = turn_in(record, "B4", executions, history=failed.state_delta["history"])

    assert len(healthy) == 1
    assert again.reply.startswith("Listo: creé el ticket ")
    assert [e["status"] for e in again.state_delta["history"]] == ["failed", "completed"]


# 9 (note (ggg), A3; decision B): only `success is True` with the ID is done; only
# `success is False` is failed; anything else is uncertain and never raises.
@pytest.mark.parametrize(
    ("result", "status", "kept"),
    [
        ({"success": True}, "uncertain", True),
        ({"success": True, "ticket_id": None}, "uncertain", True),
        ({"success": True, "ticket_id": ""}, "uncertain", True),  # review 11-1, note 3
        ({"success": True, "ticket_id": "  "}, "uncertain", True),
        # Review 11-1, should-fix 1: not JSON, so it can be neither stored nor kept (D50).
        ({"success": True, "ticket_id": "X", "at": datetime(2026, 1, 1)}, "uncertain", False),
        ({"message": "x"}, "uncertain", True),
        ({"success": "yes", "ticket_id": "X"}, "uncertain", True),
        ({"success": 1, "ticket_id": "X"}, "uncertain", True),
        ("TCK-1", "uncertain", False),
        (None, "uncertain", False),
        ({"success": False}, "failed", True),
    ],
)
def test_malformed_result_is_not_a_success(monkeypatch, result, status, kept):
    counting(monkeypatch, "create_ticket", lambda args: result)
    executions = InMemoryExecutions()
    record = turn_in(None, "H1", executions).state_delta["pending"]

    outcome = turn_in(record, "B4", executions)

    assert outcome.state_delta["pending"] is None
    [entry] = outcome.state_delta["history"]
    assert entry["status"] == status
    assert entry["steps"][0]["result"] == (result if kept else None)
    assert "Listo" not in outcome.reply
    assert outcome.reply == (FAILED_H1 if status == "failed" else UNCERTAIN_H1)
    json.dumps(outcome.state_delta)  # it goes to the session state as it is
    expected = {} if status == "failed" else {h1_key(): {"status": "started"}}
    assert executions.snapshot() == expected


# 10 (D9.1): the key carries the session, so another session runs its own.
def test_same_values_in_another_session_run_again(monkeypatch):
    calls = counting(monkeypatch, "create_ticket")
    executions = InMemoryExecutions()

    for session in (SESSION, "s-other"):
        record = turn_in(None, "H1", executions, session).state_delta["pending"]
        outcome = turn_in(record, "B4", executions, session)
        assert outcome.reply.startswith("Listo: creé el ticket ")

    assert len(calls) == 2
    assert set(executions.snapshot()) == {h1_key(SESSION), h1_key("s-other")}


def two_step_record() -> dict:
    """A plan awaiting confirmation, by hand (plans are task 14a): a ticket, then a notice."""
    ticket = handle_fixed(None, "H1").state_delta["pending"]["steps"][0]
    notice = g1_pending()["steps"][0]
    steps = [ticket, notice]
    return {
        "operation_id": "op-plan",
        "tools": ["create_ticket", "send_notification"],
        "steps": steps,
        "status": "awaiting_confirmation",
        "missing": [],
        "invalid": [],
        "last_question": replies.confirmation_question([TOOLS[s["tool"]] for s in steps]),
        "confirmation_hash": confirmation_hash(shown_values(steps, NOW)),
    }


# 11 (note (bbb) at the seam; TASK.md line 270, D8.1): a step that does not end done stops the
# steps after it.
@pytest.mark.parametrize("mode", ["failure", "timeout"])
def test_steps_after_one_that_does_not_complete_never_run(monkeypatch, mode):
    tickets = counting(monkeypatch, "create_ticket", failure_mock("create_ticket", mode))
    notices = counting(monkeypatch, "send_notification")
    executions = InMemoryExecutions()

    outcome = turn_in(two_step_record(), "G2", executions)

    assert outcome.route == "confirmed"
    assert len(tickets) == 1 and notices == []
    [entry] = outcome.state_delta["history"]
    status = "failed" if mode == "failure" else "uncertain"
    assert entry["status"] == status
    assert [(step["tool"], step["outcome"]) for step in entry["steps"]] == [
        ("create_ticket", status)
    ]
    assert outcome.reply == (FAILED_H1 if mode == "failure" else UNCERTAIN_H1)


class CountingResult(dict):
    """A result that counts how many times its ID is read, with `[]` or with `.get`."""

    reads = 0

    def __getitem__(self, key):
        if key == "room_id":
            self.reads += 1
        return super().__getitem__(key)

    def get(self, key, default=None):
        if key == "room_id":
            self.reads += 1
        return super().get(key, default)


# 12 (note (iii)): the ID comes from the registry's field, read once for the reply and the log
# (review 11-1, note 5: the presence check and the value are the same read).
def test_results_read_the_registry_id_field(monkeypatch):
    result = CountingResult(success=True, room_id="R-7", ticket_id="WRONG")
    spec = TOOLS["create_ticket"]
    room = dataclasses.replace(
        spec, done_phrase="reservé la sala", result_id_field="room_id", execute=lambda a: result
    )
    monkeypatch.setitem(TOOLS, "create_ticket", room)
    executions = InMemoryExecutions()
    record = turn_in(None, "H1", executions).state_delta["pending"]

    outcome = turn_in(record, "B4", executions)

    assert outcome.reply == "Listo: reservé la sala R-7."
    [run] = outcome.results
    assert (run.tool, run.result_id, run.outcome) == ("create_ticket", "R-7", "ran")
    assert result.reads == 1


# 13 (decision D): without `executions`, each call gets its own registry, so a "yes" runs once
# per call, as before task 11.
def test_without_executions_each_call_has_its_own_registry(monkeypatch):
    calls = counting(monkeypatch, "create_ticket")
    record = handle_fixed(None, "H1").state_delta["pending"]

    for _ in range(2):
        assert handle_fixed(record, "B4").reply.startswith("Listo: creé el ticket ")

    assert len(calls) == 2


# Brief 12 -------------------------------------------------------------------------------------

DISCARDED_MEETING = "No voy a programar una reunión; descarté lo que tenía pendiente."
DISCARDED_TICKET = "No voy a crear un ticket; descarté lo que tenía pendiente."

# `F2` as it came from the real model (`request_action`) and its `provide_info` variant, which
# *Otra tool* also takes (finding 10 of 06-round-1).
F2_ACTS = pytest.mark.parametrize("act", ["request_action", "provide_info"])


def f2_as(act: str) -> TurnInterpretation:
    return interpretation(act, op("create_ticket", description="revisar el servidor"))


def handle(pending: dict | None, turn: TurnInterpretation, message: str, **kwargs) -> TurnOutcome:
    return handle_turn(pending, turn, TurnInput(message, NOW, SESSION), **kwargs)


def closed(pending: dict, status: str) -> dict:
    """The history entry of a record closed without running (decision A of brief 12)."""
    return {"operation_id": pending["operation_id"], "status": status, "steps": pending["steps"]}


# 1 (case F, TASK.md lines 395-417; D3.1; author's answer B: option (a))
@F2_ACTS
def test_case_f_other_tool_abandons_and_starts_the_new_flow(monkeypatch, act):
    calls = record_tools(monkeypatch)
    meeting = f1_pending()

    outcome = handle(meeting, f2_as(act), FIXED["F2"].message)
    record = outcome.state_delta["pending"]

    assert outcome.route == "other_tool:R2"
    assert record["tools"] == ["create_ticket"]
    assert record["steps"] == [
        {"tool": "create_ticket", "args": {"description": "revisar el servidor"}}
    ]
    assert record["operation_id"] != meeting["operation_id"]
    for text in ("Juan", "attendees", "schedule_meeting"):
        assert text not in json.dumps(record), text
    assert outcome.state_delta["history"] == [closed(meeting, "abandoned")]

    lines = _lines(outcome.reply)
    assert lines == [
        DISCARDED_MEETING,
        "Anoté la descripción: revisar el servidor.",
        record["last_question"],
    ]
    for label in ("el título", "la prioridad", "a quién se asigna"):
        assert label in record["last_question"]
    assert nothing_ran(calls)


# 2 (TASK.md lines 282-284 and 436): after a change of intention, the "yes" runs only the new tool
def test_other_tool_while_awaiting_confirmation_runs_only_the_new_tool(monkeypatch):
    calls = record_tools(monkeypatch)
    ticket = h1_pending()
    assert ticket["status"] == "awaiting_confirmation"

    changed = handle_fixed(ticket, "G1")
    notification = changed.state_delta["pending"]
    history = changed.state_delta["history"]

    assert changed.route == "other_tool:R2"
    assert history == [closed(ticket, "abandoned")]
    assert notification["status"] == "awaiting_confirmation"
    assert notification["tools"] == ["send_notification"]
    assert _lines(changed.reply)[0] == DISCARDED_TICKET
    assert changed.reply.endswith(confirmation_text(notification))
    assert nothing_ran(calls)

    yes = handle(notification, fixed("G2"), FIXED["G2"].message, history=history)

    assert yes.route == "confirmed"
    assert calls["send_notification"] == [G1_NORMALIZED]
    assert calls["create_ticket"] == [] and calls["schedule_meeting"] == []
    assert yes.state_delta["pending"] is None
    assert [e["status"] for e in yes.state_delta["history"]] == ["abandoned", "completed"]
    assert yes.state_delta["history"][-1]["operation_id"] == notification["operation_id"]


# 3 (D3.1): a "no"/"cancel" that brings another tool is a change of intention, never `cancelled`
_NEW_TICKET = op("create_ticket", description="revisar el servidor")
_NEW_MEETING = op("schedule_meeting", attendees=["Juan"])


@pytest.mark.parametrize(
    ("first", "turn", "message", "route", "reply_end"),
    [
        (
            "F1",
            interpretation("reject", _NEW_TICKET),
            "No, mejor crea un ticket para revisar el servidor",
            "other_tool:R2",
            None,
        ),
        (
            "F1",
            interpretation("cancel", _NEW_TICKET),
            "Cancela eso, mejor crea un ticket para revisar el servidor",
            "other_tool:R2",
            None,
        ),
        (
            "A1",
            interpretation("request_action", _NEW_MEETING, op("create_ticket", title="Caída")),
            "Mejor agenda una reunión con Juan y crea un ticket titulado Caída",
            "other_tool:R3",
            replies.ONE_OPERATION_AT_A_TIME,
        ),
        # The `other_tool:R4` case left in task 13: R4 now stores a clarification with the new
        # candidates (test_change_of_intention_into_r4_keeps_the_new_candidates).
        # Review 12-1, should-fix 2 (D3.1): with `reject` or `cancel`, the operations of the
        # pending tool go to the table too, so two `explicit` are R3, not the new ticket alone.
        (
            "F1",
            interpretation("reject", op("schedule_meeting", attendees=["Ana"]), _NEW_TICKET),
            "No, la reunión con Ana no; mejor crea un ticket para revisar el servidor",
            "other_tool:R3",
            replies.ONE_OPERATION_AT_A_TIME,
        ),
        (
            "F1",
            interpretation("cancel", op("schedule_meeting", attendees=["Ana"]), _NEW_TICKET),
            "Cancela la reunión con Ana y crea un ticket para revisar el servidor",
            "other_tool:R3",
            replies.ONE_OPERATION_AT_A_TIME,
        ),
    ],
)
def test_reject_or_cancel_with_another_tool_is_a_change_of_intention(
    monkeypatch, first, turn, message, route, reply_end
):
    calls = record_tools(monkeypatch)
    pending = handle_fixed(None, first).state_delta["pending"]

    outcome = handle(pending, turn, message)

    assert outcome.route == route
    assert outcome.state_delta["history"] == [closed(pending, "abandoned")]
    discarded = DISCARDED_MEETING if first == "F1" else DISCARDED_TICKET
    assert _lines(outcome.reply)[0] == discarded
    if reply_end is None:  # R2: the new ticket, with nothing of the meeting
        record = outcome.state_delta["pending"]
        assert record["steps"] == [{"tool": "create_ticket", "args": _NEW_TICKET["args"]}]
    else:  # R3 creates nothing yet (task 14a): the pending record is gone all the same
        assert outcome.state_delta["pending"] is None
        assert outcome.reply == f"{discarded}\n{reply_end}"
    assert nothing_ran(calls)


# 4 (D3, RF-02 lines 128-134; decision E): a `plausible` never discards nor starts a flow
@pytest.mark.parametrize(
    ("turn", "message"),
    [
        (
            interpretation(
                "request_action", op("send_notification", "plausible", recipient="Carlos")
            ),
            "Quizá avísale a Carlos",
        ),
        (
            interpretation(
                "request_action",
                op("create_ticket", priority="alta"),
                op("send_notification", "plausible", recipient="Carlos"),
            ),
            "Prioridad alta, y quizá avísale a Carlos",
        ),
    ],
)
def test_only_plausible_of_another_tool_repeats_the_question(monkeypatch, turn, message):
    calls = record_tools(monkeypatch)
    pending = a1_pending()

    outcome = handle(pending, turn, message)

    assert outcome.route == "plausible_other_tool"
    assert outcome.state_delta == {}
    assert outcome.reply == pending["last_question"]
    assert "priority" not in pending["steps"][0]["args"]  # the data of the pending tool too
    assert nothing_ran(calls)


# 5 (D3 *Resto*, D20, TASK.md line 554; decision D)
@pytest.mark.parametrize("key", ["Q1", "K3"])
@pytest.mark.parametrize("first", ["A1", "H1"])
def test_question_or_other_with_pending_keeps_state(monkeypatch, first, key):
    calls = record_tools(monkeypatch)
    pending = handle_fixed(None, first).state_delta["pending"]

    outcome = handle_fixed(pending, key)

    assert outcome.route == "pending_template"
    assert outcome.state_delta == {}
    lines = _lines(outcome.reply)
    assert lines[0] == replies.capabilities(ask=False)
    assert lines[0] == (
        "Puedo ayudarte a crear un ticket, programar una reunión o enviar una notificación."
    )
    assert "¿Qué necesitas?" not in outcome.reply
    if first == "A1":
        assert lines[-1] == pending["last_question"]
    else:
        assert outcome.reply.endswith(confirmation_text(pending))
    assert nothing_ran(calls)


# Review 12-1, should-fix 1 (D3 *Resto*, D20, TASK.md line 554): a `question` or an `other` that
# brings operations applies none of them, neither data of the pending tool nor another tool.
@pytest.mark.parametrize("act", ["question", "other"])
@pytest.mark.parametrize(
    ("operation", "message"),
    [
        (op("create_ticket", priority="alta"), "¿La prioridad alta es la más urgente?"),
        (op("send_notification", recipient="Carlos"), "¿Puedes avisarle a Carlos por SMS?"),
    ],
)
def test_question_or_other_with_operations_keeps_state(monkeypatch, act, operation, message):
    calls = record_tools(monkeypatch)
    pending = a1_pending()

    outcome = handle(pending, interpretation(act, operation), message)

    assert outcome.route == "pending_template"
    assert outcome.state_delta == {}
    assert outcome.reply == f"{replies.capabilities(ask=False)}\n{pending['last_question']}"
    assert nothing_ran(calls)


# Review 12-1, should-fix 3 (D57; decision D): the other branches that repeat what is pending
# also assess it against `now`. "Hoy a las 10" confirmed at 09:00, then at 10:30 a question or
# a lone `plausible` of another tool: the reply reports the time, never the old confirmation.
@pytest.mark.parametrize(
    ("turn", "message", "route"),
    [
        (
            interpretation("request_action", op("send_notification", "plausible")),
            "Quizá avísale",
            "plausible_other_tool",
        ),
        (fixed("Q1"), FIXED["Q1"].message, "pending_template"),
    ],
)
def test_repeating_branches_reassess_against_now(monkeypatch, turn, message, route):
    calls = record_tools(monkeypatch)
    today_at_10 = _meeting(date={"relative_days": 0}, time={"hour": 10})
    pending = handle(None, today_at_10, _TODAY_MESSAGE).state_delta["pending"]
    assert pending["status"] == "awaiting_confirmation"

    later = handle_turn(pending, turn, TurnInput(message, datetime(2026, 11, 17, 10, 30), SESSION))
    record = later.state_delta["pending"]

    assert later.route == route
    assert record["status"] == "collecting"
    assert "confirmation_hash" not in record
    assert record["invalid"] == [{"tool": "schedule_meeting", "field": "time", "cause": "past"}]
    assert record["steps"] == pending["steps"]  # nothing of the message got in
    lines = _lines(later.reply)
    assert "La hora indicada ya pasó." in lines
    assert lines[-1] == record["last_question"]
    assert not any(line.startswith("Voy a") for line in lines)

    early = handle_turn(pending, turn, TurnInput(message, datetime(2026, 11, 17, 9, 30), SESSION))
    assert early.route == route
    assert early.state_delta == {}
    assert early.reply.endswith(confirmation_text(pending))
    assert nothing_ran(calls)


# 6 (D7.3, TASK.md line 284; decision C)
_PLAIN_NO = [
    (interpretation("cancel"), "Mejor cancela"),
    (interpretation("cancel", op("create_ticket", priority="alta")), "Cancela el ticket de alta"),
    (fixed("S2"), FIXED["S2"].message),
    (interpretation("reject", op("create_ticket")), "No, el ticket no"),
    # Review 12-2, should-fix 1 (decision F; AY): only arguments of a *pending* tool make a "no"
    # a correction. A lone `plausible` of another tool with arguments does not: the "no" discards.
    (
        interpretation("reject", op("send_notification", "plausible", recipient="Carlos")),
        "No, quizá avísale a Carlos",
    ),
    # Note (A11): a "no" whose arguments are all absent has no arguments (D69, "sin ausentes").
    (interpretation("reject", op("create_ticket", priority="", assignee=None)), "No"),
]


@pytest.mark.parametrize(("turn", "message"), _PLAIN_NO)
@pytest.mark.parametrize("first", ["A1", "H1"])
def test_cancel_or_plain_reject_discards_and_archives(monkeypatch, first, turn, message):
    calls = record_tools(monkeypatch)
    pending = handle_fixed(None, first).state_delta["pending"]

    outcome = handle(pending, turn, message)

    assert outcome.route == "cancelled"
    assert outcome.state_delta == {"pending": None, "history": [closed(pending, "cancelled")]}
    assert outcome.reply == DISCARDED_TICKET

    after = handle(None, fixed("G2"), FIXED["G2"].message)
    assert after.reply == replies.NO_PENDING_OPERATION
    assert after.state_delta == {}
    assert nothing_ran(calls)


def test_discarded_line_joins_actions_with_ni():
    tools = [TOOLS["create_ticket"], TOOLS["send_notification"]]
    assert replies.discarded_line(tools) == (
        "No voy a crear un ticket ni enviar una notificación; descarté lo que tenía pendiente."
    )


# 7 (D7.3, D4, TASK.md lines 145 and 557; decision F)
def test_reject_with_args_is_a_correction(monkeypatch):
    calls = record_tools(monkeypatch)
    ticket = h1_pending()
    to_ana = interpretation("reject", op("create_ticket", assignee="Ana"))

    outcome = handle(ticket, to_ana, "No, asígnalo a Ana")
    record = outcome.state_delta["pending"]

    assert outcome.route == "reject_merged"
    [change] = outcome.changes
    assert (change.kind, change.old, change.new) == ("overwrite", "Carlos", "Ana")
    assert _lines(outcome.reply)[0] == "Cambié a quién se asigna: Carlos → Ana."
    assert record["status"] == "awaiting_confirmation"
    assert record["confirmation_hash"] != ticket["confirmation_hash"]
    assert record["operation_id"] == ticket["operation_id"]
    assert outcome.reply.endswith(confirmation_text(record))
    assert nothing_ran(calls)

    yes = handle_fixed(record, "B4")
    assert yes.route == "confirmed"
    [args] = calls["create_ticket"]
    assert args["assignee"] == "Ana"

    # The stored value again: no change, nothing written, the confirmation repeated (D40, D57).
    same = handle(
        ticket, interpretation("reject", op("create_ticket", assignee="Carlos")), "No, a Carlos"
    )
    assert same.route == "reject_merged"
    assert same.state_delta == {}
    assert same.reply == confirmation_text(ticket)

    # A correction while collecting stays collecting.
    collecting = a1_pending()
    alta = handle(
        collecting, interpretation("reject", op("create_ticket", priority="alta")), "No, alta"
    )
    assert alta.route == "reject_merged"
    assert alta.state_delta["pending"]["status"] == "collecting"
    assert alta.state_delta["pending"]["steps"][0]["args"]["priority"] == "alta"
    assert "history" not in alta.state_delta
    assert len(calls["create_ticket"]) == 1  # only the "yes" above ran


# 8 (note (xxx), D57): a "yes" on a `collecting` record is assessed again against `now`
def test_confirm_incomplete_reassesses_against_now(monkeypatch):
    calls = record_tools(monkeypatch)
    untitled = interpretation(
        "request_action",
        op(
            "schedule_meeting",
            date={"relative_days": 0},
            time={"hour": 10},
            duration_minutes=30,
            attendees=["Juan", "Ana"],
        ),
    )
    message = "Agenda una reunión hoy a las 10, 30 minutos, con Juan y Ana"
    pending = handle(None, untitled, message).state_delta["pending"]
    assert pending["status"] == "collecting" and pending["invalid"] == []

    later = handle_turn(
        pending, fixed("G2"), TurnInput("Sí, hazlo", datetime(2026, 11, 17, 10, 30), SESSION)
    )
    record = later.state_delta["pending"]

    assert later.route == "confirm_incomplete"
    assert record["invalid"] == [{"tool": "schedule_meeting", "field": "time", "cause": "past"}]
    assert record["steps"] == pending["steps"]
    lines = _lines(later.reply)
    assert "La hora indicada ya pasó." in lines
    assert lines[-1] == record["last_question"]
    assert "el título" in record["last_question"] and "la hora" in record["last_question"]

    early = handle_turn(
        pending, fixed("G2"), TurnInput("Sí, hazlo", datetime(2026, 11, 17, 9, 30), SESSION)
    )
    assert early.route == "confirm_incomplete"
    assert early.state_delta == {}
    assert early.reply == pending["last_question"]
    assert nothing_ran(calls)


# 9 (note (ll), D41): the D10 line goes first in every new branch with something pending
_PIZZA = op("pedir_pizza", size="grande")


def _with_pizza(turn: TurnInterpretation) -> TurnInterpretation:
    data = turn.model_dump()
    data["operations"].append(_PIZZA)
    return TurnInterpretation.model_validate(data)


@pytest.mark.parametrize(
    ("first", "turn", "message", "route"),
    [
        ("F1", fixed("F2"), FIXED["F2"].message, "other_tool:R2"),
        ("A1", interpretation("cancel"), "Mejor cancela", "cancelled"),
        (
            "H1",
            interpretation("reject", op("create_ticket", assignee="Ana")),
            "No, asígnalo a Ana",
            "reject_merged",
        ),
        (
            "A1",
            interpretation("request_action", op("send_notification", "plausible")),
            "Quizá avísale",
            "plausible_other_tool",
        ),
        ("A1", fixed("Q1"), FIXED["Q1"].message, "pending_template"),
    ],
)
def test_d10_line_before_every_new_pending_branch(first, turn, message, route):
    pending = handle_fixed(None, first).state_delta["pending"]

    plain = handle(pending, turn, message)
    pizza = handle(pending, _with_pizza(turn), f"{message} y pide una pizza grande")

    assert plain.route == pizza.route == route
    assert pizza.reply == f"{replies.UNKNOWN_TOOL_LINE}\n{plain.reply}"

    def without_new_id(delta: dict) -> dict:
        record = delta.get("pending")
        if record and record.get("operation_id") != pending["operation_id"]:
            return {**delta, "pending": {k: v for k, v in record.items() if k != "operation_id"}}
        return delta

    assert without_new_id(pizza.state_delta) == without_new_id(plain.state_delta)


# 11 (D6): a closing without running also keeps only the last ten. Task 13 adds a clarification
# discarded by rule 1, archived with its candidates as `{tool, args}`.
@pytest.mark.parametrize(
    ("first", "turn", "message", "status"),
    [
        ("F1", f2_as("request_action"), FIXED["F2"].message, "abandoned"),
        ("F1", interpretation("cancel"), "Cancela", "cancelled"),
        ("C1", fixed("S2"), FIXED["S2"].message, "cancelled"),
    ],
)
def test_closing_without_running_keeps_the_last_ten(first, turn, message, status):
    history = old_entries(10)
    received = json.loads(json.dumps(history))
    pending = handle_fixed(None, first).state_delta["pending"]

    new = handle(pending, turn, message, history=history).state_delta["history"]

    assert len(new) == 10
    steps = pending.get("steps") or [
        {"tool": c["tool"], "args": c["args"]} for c in pending["candidates"]
    ]
    assert new[-1] == {"operation_id": pending["operation_id"], "status": status, "steps": steps}
    assert new[:-1] == received[1:]
    assert history == received
