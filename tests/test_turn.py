import dataclasses
import json
from typing import Any

import pytest

from core import replies
from core.instruction import pending_summary
from core.interpretation import TurnInterpretation
from core.registry import TOOLS
from core.turn import _filter_by_registry, handle_turn
from tests.interpretations import FIXED


def fixed(key: str) -> TurnInterpretation:
    return TurnInterpretation.model_validate(FIXED[key].interpretation)


def interpretation(act: str, *operations: dict[str, Any]) -> TurnInterpretation:
    return TurnInterpretation.model_validate({"act": act, "operations": list(operations)})


def op(tool: str, certainty: str = "explicit", **args: Any) -> dict[str, Any]:
    return {"tool": tool, "certainty": certainty, "args": args}


def a1_pending() -> dict:
    return handle_turn(None, fixed("A1")).state_delta["pending"]


def _all_values(value: Any) -> list[str]:
    """Every collected value as text, for "the question shows none of them" checks."""
    if isinstance(value, dict):
        return [text for item in value.values() for text in _all_values(item)]
    if isinstance(value, list):
        return [text for item in value for text in _all_values(item)]
    return [str(value)]


# 4
def test_unknown_tool_alone_gives_capabilities():
    outcome = handle_turn(None, interpretation("request_action", op("book_flight")))

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
    outcome = handle_turn(None, turn)

    assert outcome.route == "R2"
    assert outcome.state_delta["pending"]["tools"] == ["create_ticket"]
    assert replies.UNKNOWN_TOOL_LINE in outcome.reply
    assert replies.UNKNOWN_TOOL_LINE not in outcome.state_delta["pending"]["last_question"]


# 6
def test_contract_filter_drops_args_of_other_tools():
    turn = interpretation(
        "request_action", op("create_ticket", description="el servidor", recipient="Carlos")
    )
    record = handle_turn(None, turn).state_delta["pending"]

    assert record["steps"][0]["args"] == {"description": "el servidor"}


def test_registry_filter_keeps_contract_keys_and_buckets():
    # Checked on the filter itself: R2 drops buckets again before storing, which would hide
    # a broken filter (review 03-1, finding 1). Task 08 uses this filter without R2.
    turn = interpretation(
        "request_action",
        op("schedule_meeting", attendees_add=["Ana"], recipient="Carlos"),
        op("create_ticket", "plausible", description="el servidor", attendees_add=["Ana"]),
    )
    meeting, ticket = _filter_by_registry(turn)

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
        outcome = handle_turn(pending, turn)
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
        outcome = handle_turn(None, turn)
        assert outcome.reply == replies.NO_PENDING_OPERATION, turn.act
        assert outcome.state_delta == {}

    for act in ("question", "other"):
        for operations in ([], [op("create_ticket", description="el servidor")]):
            outcome = handle_turn(None, interpretation(act, *operations))
            assert outcome.route == "template", (act, operations)
            assert outcome.reply == replies.capabilities()
            assert outcome.state_delta == {}


# 9
def test_r3_r4_r5_do_not_start_anything_yet():
    only_plausible = interpretation(
        "request_action", op("schedule_meeting", "plausible", attendees=["Carlos"])
    )
    for turn, route in ((fixed("E1"), "R3"), (fixed("C1"), "R4"), (only_plausible, "R5")):
        outcome = handle_turn(None, turn)
        assert outcome.route == route
        assert outcome.state_delta == {}


# 10
def test_case_a_creates_pending_and_asks_for_missing():
    outcome = handle_turn(None, fixed("A1"))
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
    assert "la descripción" not in reply
    assert record["last_question"] == reply


# 11
@pytest.mark.parametrize("key", ["A1", "B2", "F1"])
def test_missing_question_has_no_collected_value_and_no_options(key):
    record = handle_turn(None, fixed(key)).state_delta["pending"]
    question = record["last_question"].lower()

    for value in _all_values(record["steps"][0]["args"]):
        assert value.lower() not in question, value
    for option in ("alta", "media", "baja", "correo", "sms"):
        assert option not in question


# 12
def test_provide_info_with_operations_and_nothing_pending_starts_a_flow():
    outcome = handle_turn(None, fixed("B2"))
    record = outcome.state_delta["pending"]

    assert outcome.route == "R2"
    assert record["steps"][0]["args"] == {"priority": "Alta", "assignee": "Carlos"}
    assert record["missing"] == ["title", "description"]
    # `{}` is "nothing pending" too (brief, point 1; review 03-1, note 6).
    assert handle_turn({}, fixed("B2")).route == "R2"


# 13
def test_values_are_stored_as_said_without_validation():
    ticket = handle_turn(None, fixed("X1")).state_delta["pending"]
    assert ticket["steps"][0]["args"]["priority"] == "urgente"
    assert "priority" not in ticket["missing"]

    meeting = handle_turn(None, fixed("D1")).state_delta["pending"]
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
    record = handle_turn(None, turn).state_delta["pending"]

    assert record["steps"][0]["args"] == {"attendees": ["Ana"], "duration_minutes": 0}
    assert record["missing"] == ["title", "date", "time"]


# 14
@pytest.mark.parametrize("key", ["G1", "H1"])
def test_complete_request_creates_pending_but_does_not_execute(key):
    outcome = handle_turn(None, fixed(key))
    record = outcome.state_delta["pending"]

    assert outcome.route == "R2"
    assert record["missing"] == []
    assert record["status"] == "collecting"
    assert outcome.reply == replies.READY_NOT_AVAILABLE_YET
    assert record["last_question"] is None
    assert "Last question to the user: none" in pending_summary(record)


# 15
def test_no_turn_executes_a_tool(monkeypatch):
    def fail(args):
        pytest.fail(f"a tool ran with {args}")

    for name, spec in list(TOOLS.items()):
        monkeypatch.setitem(TOOLS, name, dataclasses.replace(spec, execute=fail))

    pending = a1_pending()
    for key in FIXED:
        handle_turn(None, fixed(key))
        handle_turn(pending, fixed(key))


# 16
@pytest.mark.parametrize("key", ["B2", "F2", "G2", "X1"])
def test_anything_with_pending_is_provisional_and_keeps_state(key):
    outcome = handle_turn(a1_pending(), fixed(key))

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
