import dataclasses
import json
import re
from typing import Any

import pytest

from core import replies
from core.confirmation import confirmation_hash, shown_values
from core.instruction import pending_summary
from core.interpretation import TurnInterpretation
from core.merge import Note
from core.registry import TOOLS
from core.turn import TurnInput, TurnOutcome, filter_by_registry, handle_turn
from tests.interpretations import FIXED


def fixed(key: str) -> TurnInterpretation:
    return TurnInterpretation.model_validate(FIXED[key].interpretation)


def handle_fixed(pending: dict | None, key: str) -> TurnOutcome:
    """`handle_turn` on a fixed interpretation, with its own message (D4 rule 3)."""
    return handle_turn(pending, fixed(key), TurnInput(FIXED[key].message))


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
    summary = replies.confirmation_summary(shown_values(record["steps"]))
    return f"{summary}\n{record['last_question']}"


# 4
def test_unknown_tool_alone_gives_capabilities():
    outcome = handle_turn(
        None, interpretation("request_action", op("book_flight")), TurnInput("Reserva un vuelo")
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
        None, turn, TurnInput("Reserva un vuelo y crea un ticket: el servidor está caído")
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
    record = handle_turn(None, turn, TurnInput(message)).state_delta["pending"]

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
        outcome = handle_turn(pending, turn, TurnInput("Crea dos tickets: el servidor y la red"))
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
        outcome = handle_turn(None, turn, TurnInput("Sí, hazlo"))
        assert outcome.reply == replies.NO_PENDING_OPERATION, turn.act
        assert outcome.state_delta == {}

    for act in ("question", "other"):
        for operations in ([], [op("create_ticket", description="el servidor")]):
            outcome = handle_turn(
                None, interpretation(act, *operations), TurnInput("¿Y el servidor?")
            )
            assert outcome.route == "template", (act, operations)
            assert outcome.reply == replies.capabilities()
            assert outcome.state_delta == {}


# 9
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
        outcome = handle_turn(None, turn, TurnInput(message))
        assert outcome.route == route
        assert outcome.state_delta == {}


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
    record = handle_turn(None, turn, TurnInput("Una reunión con Ana")).state_delta["pending"]

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


# 16: what stays provisional with something pending (tasks 12 and 13). F2 with F1 pending is
# *Otra tool*, case F: nothing of the meeting is mixed into the ticket. ("A1", "G2") left in task
# 10a: a "yes" while collecting repeats the question (test_yes_while_collecting_...).
@pytest.mark.parametrize(("first", "key"), [("A1", "Q1"), ("F1", "F2")])
def test_anything_with_pending_is_provisional_and_keeps_state(first, key):
    pending = handle_fixed(None, first).state_delta["pending"]
    outcome = handle_fixed(pending, key)

    assert outcome.route == "pending_provisional"
    assert outcome.state_delta == {}


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
    outcome = handle_turn(None, meeting, TurnInput("Agenda una reunión con Ana"))
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
    outcome = handle_turn(None, ticket, TurnInput("Crea un ticket, el servidor está caído"))
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
    outcome = handle_turn(pending, _as("B2", act, certainty), TurnInput(FIXED["B2"].message))
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
    outcome = handle_turn(ticket, medium, TurnInput("Mejor media"))

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
        outcome = handle_turn(f1, _as("L2", act, "explicit"), TurnInput(FIXED["L2"].message))
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
    outcome = handle_turn(None, turn, TurnInput("Agenda una reunión con Ana"))
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
    outcome = handle_turn(pending, interpretation(act), TurnInput("Eso"))

    assert outcome.reply == pending["last_question"]
    assert outcome.state_delta == {}


# Review 08-1 ----------------------------------------------------------------------------------


# Should-fix 1 (autodecided AB): with something pending, an out-of-catalog request is not
# dropped in silence (D10); a turn with no operations at all keeps the author's answer (a).
def test_pending_and_unknown_tool_adds_the_d10_line():
    pending = a1_pending()
    flight = interpretation("request_action", op("book_flight"))

    outcome = handle_turn(pending, flight, TurnInput("Mejor reserva un vuelo"))

    assert outcome.reply == f"{replies.UNKNOWN_TOOL_LINE}\n{pending['last_question']}"
    assert outcome.state_delta == {}

    nothing = handle_turn(pending, interpretation("request_action"), TurnInput("Mejor eso"))
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
# awaiting confirmation (test_correction_while_awaiting); the clarification is task 13.
def test_same_tool_needs_a_collecting_record():
    clarifying = {
        "tools": ["send_notification", "schedule_meeting"],
        "status": "awaiting_clarification",
        "missing": [],
        "last_question": "¿Quieres enviar una notificación, programar una reunión, o ambas?",
    }  # like phase0's literal record: candidates, no `steps`
    outcome = handle_fixed(clarifying, "K2")
    assert outcome.route == "pending_provisional"
    assert outcome.state_delta == {}


# Brief 09a ------------------------------------------------------------------------------------

_TICKET_FIELDS = {spec.name: spec for spec in TOOLS["create_ticket"].fields}

# A meeting with every field (the date and the time have no rule until task 09b).
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
    outcome = handle_turn(pending, high, TurnInput("Alta"))
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
    outcome = handle_turn(complete, urgent, TurnInput("Mejor urgente"))
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
        None, _meeting(duration_minutes=minutes), TurnInput(_COMPLETE_MEETING_MESSAGE)
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


# 14 (finding 3 of backlog-06: a date or a time is never valid before task 09b)
def test_complete_meeting_waits_for_dates():
    outcome = handle_turn(None, _meeting(), TurnInput(_COMPLETE_MEETING_MESSAGE))
    record = outcome.state_delta["pending"]

    assert record["status"] == "collecting"
    assert record["missing"] == []
    assert record["invalid"] == []
    assert record["last_question"] is None
    assert outcome.reply.endswith(replies.DATE_TIME_NOT_CHECKED_YET)
    assert "confirmation_hash" not in record  # never awaits confirmation before task 09b

    again = handle_turn(record, _meeting(), TurnInput(_COMPLETE_MEETING_MESSAGE))
    assert again.state_delta == {}
    assert again.reply == replies.DATE_TIME_NOT_CHECKED_YET


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
    assert record["confirmation_hash"] == confirmation_hash(shown_values(record["steps"]))

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
        None, TurnInterpretation.model_validate(g1), TurnInput(FIXED["G1"].message)
    ).state_delta["pending"]
    assert pending["steps"][0]["args"]["recipient"] == recipient  # kept as said (D4 rule 7)

    outcome = handle_fixed(pending, "G2")

    assert calls["send_notification"] == [G1_NORMALIZED]
    assert calls["create_ticket"] == [] and calls["schedule_meeting"] == []
    assert outcome.state_delta == {"pending": None}
    assert outcome.route == "confirmed"
    assert outcome.reply.startswith("Notificación NTF-")  # the mock's message until task 10b


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
    outcome = handle_turn(pending, sms, TurnInput("Sí, pero por SMS"))
    record = outcome.state_delta["pending"]

    assert outcome.route == "confirm_merged"
    assert _lines(outcome.reply)[0] == "Cambié el canal: correo → SMS."
    assert record["status"] == "awaiting_confirmation"
    assert record["confirmation_hash"] != pending["confirmation_hash"]
    assert record["operation_id"] == pending["operation_id"]
    assert outcome.reply.endswith(confirmation_text(record))

    pending = handle_fixed(None, "H1").state_delta["pending"]
    urgent = confirm(op("create_ticket", priority="urgente"))
    outcome = handle_turn(pending, urgent, TurnInput("Sí, pero con prioridad urgente"))
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

    outcome = handle_turn(pending, turn, TurnInput(FIXED["G2"].message))

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

    outcome = handle_turn(pending, confirm(confirms_tool=confirms_tool), TurnInput("Sí"))

    if runs:
        assert calls["send_notification"] == [G1_NORMALIZED]
        assert outcome.state_delta == {"pending": None}
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
    assert outcome.reply == confirmation_text(pending)
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

    outcome = handle_turn(pending, push, TurnInput("Mejor por push"))
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
    outcome = handle_turn(pending, ticket, TurnInput(message))
    assert outcome.route == "confirm_other_tool"
    assert outcome.state_delta == {}
    assert outcome.reply == confirmation_text(pending)

    pizza = confirm(op("send_notification"), op("order_pizza"))
    outcome = handle_turn(pending, pizza, TurnInput("Sí, y pide una pizza"))
    assert outcome.state_delta == {}
    assert outcome.reply == f"{replies.UNKNOWN_TOOL_LINE}\n{confirmation_text(pending)}"

    collecting = a1_pending()
    meeting = confirm(op("schedule_meeting", attendees=["Juan"]))
    outcome = handle_turn(collecting, meeting, TurnInput("Sí, y agenda una reunión con Juan"))
    assert outcome.state_delta == {}
    assert outcome.reply == collecting["last_question"]
    assert nothing_ran(calls)


# 12 (note (ll)): the D10 line before the confirmation repeated by *Misma tool*
def test_unknown_tool_while_awaiting_adds_the_d10_line():
    pending = g1_pending()
    flight = interpretation("provide_info", op("book_flight"))

    outcome = handle_turn(pending, flight, TurnInput("Y reserva un vuelo"))

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
# Only a list field gives notes, and a meeting cannot await confirmation before task 09b, so the
# note is checked on a `collecting` meeting: without the note in the condition the turn would
# take `confirm_incomplete` and drop the note line.
def test_yes_with_a_note_never_counts(monkeypatch):
    calls = record_tools(monkeypatch)
    pending = handle_fixed(None, "F1").state_delta["pending"]  # a meeting with Juan
    turn = confirm(op("schedule_meeting", attendees_remove=["Ana"]))

    outcome = handle_turn(pending, turn, TurnInput("Sí, y quita a Ana"))

    attendees = TOOLS["schedule_meeting"].fields[4]
    assert outcome.route == "confirm_merged"
    assert outcome.state_delta == {}
    assert _lines(outcome.reply) == [
        replies.note_line(attendees, Note("attendees", "remove_absent", "Ana")),
        pending["last_question"],
    ]
    assert nothing_ran(calls)


# Review note 4 (autodecided AJ): a "yes" that repeats a stored value, or a name written in the
# message, changes nothing after normalization (D4 rule 5), so it counts as a yes.
@pytest.mark.parametrize(
    ("args", "message"),
    [({"channel": "correo"}, "Sí, por correo"), ({"recipient": "Carlos"}, "Sí, envíalo a Carlos")],
)
def test_yes_repeating_a_stored_value_counts(monkeypatch, args, message):
    calls = record_tools(monkeypatch)
    turn = confirm(op("send_notification", **args))
    outcome = handle_turn(g1_pending(), turn, TurnInput(message))

    assert outcome.route == "confirmed"
    assert calls["send_notification"] == [G1_NORMALIZED]
