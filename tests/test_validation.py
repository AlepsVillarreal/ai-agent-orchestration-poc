"""The validator of D5, in pure Python (task 09a): no ADK, no model."""

import copy

import pytest

from core.interpretation import TurnInterpretation
from core.registry import TOOLS, FieldKind
from core.turn import TurnInput, handle_turn
from core.validation import RULES, UNCHECKED_KINDS, Invalid, validate
from tests.interpretations import FIXED

TICKET = TOOLS["create_ticket"]
MEETING = TOOLS["schedule_meeting"]
NOTICE = TOOLS["send_notification"]

# A present value of the right type for each kind, to reach the rule of every field.
_SAMPLE = {
    FieldKind.TEXT: "Revisión",
    FieldKind.WORD: None,  # each `word` field has its own table: see _WORDS
    FieldKind.INT: 30,
    FieldKind.PERSON: "Carlos",
    FieldKind.DATE: {"day": 20, "month": 11},
    FieldKind.TIME: {"hour": 10, "minute": 30},
    FieldKind.PERSON_LIST: ["Juan"],
}

_WORDS = {"priority": "alta", "channel": "correo"}

COMPLETE_MEETING = {
    "title": "Revisión del servidor",
    "date": {"day": 20, "month": 11, "year": 2026},
    "time": {"hour": 10, "minute": 30},
    "duration_minutes": 30,
    "attendees": ["Juan", "Ana"],
}


# 1
def test_every_kind_has_a_rule_or_waits_for_09b():
    assert UNCHECKED_KINDS == {FieldKind.DATE, FieldKind.TIME}
    assert set(RULES) == set(FieldKind) - UNCHECKED_KINDS

    for tool in TOOLS.values():
        args = {spec.name: _WORDS.get(spec.name, _SAMPLE[spec.kind]) for spec in tool.fields}
        result = validate(tool, args)
        expected = [spec.name for spec in tool.fields if spec.kind in UNCHECKED_KINDS]
        assert result.unchecked == expected, tool.name
        assert result.missing == [] and result.invalid == [], tool.name
        assert set(result.normalized) == {spec.name for spec in tool.fields} - set(expected)


# 2
@pytest.mark.parametrize(
    ("tool", "field_name", "word", "canonical"),
    [
        (TICKET, "priority", "Alta", "high"),
        (TICKET, "priority", "alta", "high"),
        (TICKET, "priority", "high", "high"),
        (TICKET, "priority", " ALTA ", "high"),
        (TICKET, "priority", "media", "medium"),
        (NOTICE, "channel", "correo", "email"),
        (NOTICE, "channel", "mail", "email"),
        (NOTICE, "channel", "email", "email"),
        (NOTICE, "channel", "SMS", "sms"),
        (TICKET, "priority", "urgente", None),
    ],
)
def test_word_synonyms(tool, field_name, word, canonical):
    result = validate(tool, {field_name: word})

    if canonical is None:
        assert result.invalid == [Invalid(field_name, "not_allowed")]
        assert field_name not in result.normalized
    else:
        assert result.invalid == []
        assert result.normalized[field_name] == canonical


# 3 (D5.3; D13 for the types)
@pytest.mark.parametrize(
    ("value", "cause"),
    [
        (1, None),
        (30, None),
        (480, None),
        (0, "out_of_range"),
        (-5, "out_of_range"),
        (481, "out_of_range"),
        ("30", "wrong_type"),
        (True, "wrong_type"),
        (30.0, "wrong_type"),
    ],
)
def test_int_range_d5_3(value, cause):
    result = validate(MEETING, {"duration_minutes": value})

    if cause is None:
        assert result.invalid == []
        assert result.normalized["duration_minutes"] == value
    else:
        assert result.invalid == [Invalid("duration_minutes", cause)]
        assert "duration_minutes" not in result.normalized
    assert "duration_minutes" not in result.missing


# 4 (note (hh))
def test_text_and_people_are_trimmed():
    ticket = validate(TICKET, {"title": " Caída ", "assignee": " Carlos "})
    assert ticket.normalized == {"title": "Caída", "assignee": "Carlos"}

    meeting = validate(MEETING, {"attendees": [" Ana ", "Juan"]})
    assert meeting.normalized == {"attendees": ["Ana", "Juan"]}

    wrong = validate(TICKET, {"assignee": 5})
    assert wrong.invalid == [Invalid("assignee", "wrong_type")]
    assert validate(MEETING, {"attendees": ["Ana", 5]}).invalid == [
        Invalid("attendees", "wrong_type")
    ]
    assert validate(TICKET, {"title": {"x": "y"}}).invalid == [Invalid("title", "wrong_type")]


# 5
def test_missing_and_invalid_together():
    result = validate(TICKET, {"description": "el servidor está caído", "priority": "urgente"})

    assert result.missing == ["title", "assignee"]
    assert result.invalid == [Invalid("priority", "not_allowed")]
    assert result.normalized == {"description": "el servidor está caído"}
    assert not result.ready


# 6 (moved from tests/test_presence.py: `missing` is the validator's now)
def test_missing_comes_from_the_validator():
    assert validate(MEETING, {"attendees": ["Juan"]}).missing == [
        "title",
        "date",
        "time",
        "duration_minutes",
    ]
    # Day 40 is present: it is not missing (whether it is valid is task 09b).
    args = {"attendees": ["Juan"], "date": {"day": 40, "month": 11}}
    assert "date" not in validate(MEETING, args).missing
    # Zero is a value (D4 rule 2): out of range, never missing.
    zero = validate(MEETING, {"duration_minutes": 0})
    assert "duration_minutes" not in zero.missing
    assert zero.invalid == [Invalid("duration_minutes", "out_of_range")]
    # Blank strings, empty lists and empty objects are absent.
    blank = validate(MEETING, {"title": "  ", "attendees": ["", " "], "date": {}})
    assert blank.missing == ["title", "date", "time", "duration_minutes", "attendees"]


# 7 (finding 3 of backlog-06)
@pytest.mark.parametrize(
    ("date", "time"),
    [
        ({"day": 20, "month": 11, "year": 2026}, {"hour": 10, "minute": 30}),
        ({"day": 40, "month": 11}, {"hour": 25, "minute": 90}),
    ],
)
def test_date_and_time_are_never_valid_yet(date, time):
    result = validate(MEETING, {**COMPLETE_MEETING, "date": date, "time": time})

    assert result.missing == []
    assert result.invalid == []
    assert result.unchecked == ["date", "time"]
    assert "date" not in result.normalized and "time" not in result.normalized
    assert not result.ready


def _collected_args(*keys: str) -> tuple[str, dict]:
    """The tool and arguments the core keeps after the fixed turns `keys`, in one session."""
    pending = None
    for key in keys:
        interpretation = TurnInterpretation.model_validate(FIXED[key].interpretation)
        outcome = handle_turn(pending, interpretation, TurnInput(FIXED[key].message))
        pending = outcome.state_delta.get("pending", pending)
    (step,) = pending["steps"]
    return step["tool"], step["args"]


# 8 (bridge to task 10b; D12, D13)
@pytest.mark.parametrize("keys", [("G1",), ("H1",), ("B1", "B2", "B3")])
def test_ready_values_pass_the_contract(keys):
    tool_name, args = _collected_args(*keys)
    tool = TOOLS[tool_name]
    result = validate(tool, args)

    assert result.ready
    tool.contract.model_validate(result.normalized)


# 9
def test_validate_does_not_mutate_args():
    args = {
        "title": " Revisión ",
        "date": {"day": 40, "month": 11},
        "duration_minutes": 0,
        "attendees": [" Ana ", ""],
    }
    before = copy.deepcopy(args)

    validate(MEETING, args)

    assert args == before
