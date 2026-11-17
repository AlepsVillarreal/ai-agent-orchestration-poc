"""The validator of D5, in pure Python (tasks 09a and 09b): no ADK, no model.

Dates and times are read against `NOW`, Tuesday 2026-11-17 at 09:00.
"""

import copy
import dataclasses
from datetime import datetime

import pytest

from core.interpretation import TurnInterpretation
from core.registry import TOOLS, FieldKind
from core.turn import TurnInput, handle_turn
from core.validation import RULES, Invalid, Validation, validate
from tests.interpretations import FIXED, NOW

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


# 1 (task 09b: dates and times have their rules; no kind is left without one)
def test_every_kind_has_a_rule():
    assert set(RULES) == set(FieldKind)
    assert {item.name for item in dataclasses.fields(Validation)} == {
        "missing",
        "invalid",
        "normalized",
    }

    for tool in TOOLS.values():
        args = {spec.name: _WORDS.get(spec.name, _SAMPLE[spec.kind]) for spec in tool.fields}
        result = validate(tool, args, NOW)
        assert result.ready, tool.name
        assert result.missing == [] and result.invalid == [], tool.name
        assert set(result.normalized) == {spec.name for spec in tool.fields}, tool.name


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
    result = validate(tool, {field_name: word}, NOW)

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
    result = validate(MEETING, {"duration_minutes": value}, NOW)

    if cause is None:
        assert result.invalid == []
        assert result.normalized["duration_minutes"] == value
    else:
        assert result.invalid == [Invalid("duration_minutes", cause)]
        assert "duration_minutes" not in result.normalized
    assert "duration_minutes" not in result.missing


# 4 (note (hh))
def test_text_and_people_are_trimmed():
    ticket = validate(TICKET, {"title": " Caída ", "assignee": " Carlos "}, NOW)
    assert ticket.normalized == {"title": "Caída", "assignee": "Carlos"}

    meeting = validate(MEETING, {"attendees": [" Ana ", "Juan"]}, NOW)
    assert meeting.normalized == {"attendees": ["Ana", "Juan"]}

    wrong = validate(TICKET, {"assignee": 5}, NOW)
    assert wrong.invalid == [Invalid("assignee", "wrong_type")]
    assert validate(MEETING, {"attendees": ["Ana", 5]}, NOW).invalid == [
        Invalid("attendees", "wrong_type")
    ]
    assert validate(TICKET, {"title": {"x": "y"}}, NOW).invalid == [Invalid("title", "wrong_type")]


# 5
def test_missing_and_invalid_together():
    result = validate(TICKET, {"description": "el servidor está caído", "priority": "urgente"}, NOW)

    assert result.missing == ["title", "assignee"]
    assert result.invalid == [Invalid("priority", "not_allowed")]
    assert result.normalized == {"description": "el servidor está caído"}
    assert not result.ready


# 6 (moved from tests/test_presence.py: `missing` is the validator's now)
def test_missing_comes_from_the_validator():
    assert validate(MEETING, {"attendees": ["Juan"]}, NOW).missing == [
        "title",
        "date",
        "time",
        "duration_minutes",
    ]
    # Day 40 is present: it is not missing, it does not exist (task 09b).
    args = {"attendees": ["Juan"], "date": {"day": 40, "month": 11}}
    assert "date" not in validate(MEETING, args, NOW).missing
    # Zero is a value (D4 rule 2): out of range, never missing.
    zero = validate(MEETING, {"duration_minutes": 0}, NOW)
    assert "duration_minutes" not in zero.missing
    assert zero.invalid == [Invalid("duration_minutes", "out_of_range")]
    # Blank strings, empty lists and empty objects are absent.
    blank = validate(MEETING, {"title": "  ", "attendees": ["", " "], "date": {}}, NOW)
    assert blank.missing == ["title", "date", "time", "duration_minutes", "attendees"]


def _collected_args(*keys: str) -> tuple[str, dict]:
    """The tool and arguments the core keeps after the fixed turns `keys`, in one session."""
    pending = None
    for key in keys:
        interpretation = TurnInterpretation.model_validate(FIXED[key].interpretation)
        outcome = handle_turn(pending, interpretation, TurnInput(FIXED[key].message, NOW))
        pending = outcome.state_delta.get("pending", pending)
    (step,) = pending["steps"]
    return step["tool"], step["args"]


# 8 (bridge to task 10b; D12, D13). Task 09b adds two meetings, given as their arguments: the
# normalized date is a real ISO date and the time has its leading zero ("09:00").
_FRIDAY_AT_NINE = {**COMPLETE_MEETING, "date": {"weekday": "viernes"}, "time": {"hour": 9}}


@pytest.mark.parametrize(
    "source", [("G1",), ("H1",), ("B1", "B2", "B3"), COMPLETE_MEETING, _FRIDAY_AT_NINE]
)
def test_ready_values_pass_the_contract(source):
    if isinstance(source, dict):
        tool_name, args = MEETING.name, source
    else:
        tool_name, args = _collected_args(*source)
    tool = TOOLS[tool_name]
    result = validate(tool, args, NOW)

    assert result.ready
    tool.contract.model_validate(result.normalized)
    if source is _FRIDAY_AT_NINE:
        assert (result.normalized["date"], result.normalized["time"]) == ("2026-11-20", "09:00")


# 9
def test_validate_does_not_mutate_args():
    args = {
        "title": " Revisión ",
        "date": {"day": 40, "month": 11},
        "duration_minutes": 0,
        "attendees": [" Ana ", ""],
    }
    before = copy.deepcopy(args)

    validate(MEETING, args, NOW)

    assert args == before


# Brief 09b ------------------------------------------------------------------------------------


# 2 (D5.1: each form against today; a weekday is its next occurrence without today, a day and a
# month without a year their next occurrence with today; a weekday next to a form that fixes the
# day is checked against it)
@pytest.mark.parametrize(
    ("parts", "iso"),
    [
        ({"relative_days": 0}, "2026-11-17"),
        ({"relative_days": 1}, "2026-11-18"),
        ({"weekday": "viernes"}, "2026-11-20"),
        ({"weekday": "martes"}, "2026-11-24"),  # today is Tuesday: next week's
        ({"weekday": "Miércoles"}, "2026-11-18"),
        ({"weekday": "miercoles"}, "2026-11-18"),
        ({"day": 20, "month": 11}, "2026-11-20"),
        ({"day": 17, "month": 11}, "2026-11-17"),  # today counts
        ({"day": 10, "month": 11}, "2027-11-10"),
        ({"day": 5, "month": 1, "year": 2027}, "2027-01-05"),
        ({"weekday": "viernes", "day": 27, "month": 11}, "2026-11-27"),  # the 27th is a Friday
        ({"relative_days": 3, "weekday": "viernes"}, "2026-11-20"),
        # Review 09b-1, should-fix 2.1: the table is Spanish and English.
        ({"weekday": "friday"}, "2026-11-20"),
        ({"weekday": "Wednesday"}, "2026-11-18"),
    ],
)
def test_date_forms(parts, iso):
    result = validate(MEETING, {"date": parts}, NOW)

    assert result.invalid == []
    assert "date" not in result.missing
    assert result.normalized["date"] == iso


# 3 (D5.1, D5.1b, D5.1c: the exact cause, and the date is not normalized)
@pytest.mark.parametrize(
    ("value", "cause"),
    [
        ({"day": 40, "month": 11}, "nonexistent"),
        ({"day": 31, "month": 4}, "nonexistent"),
        ({"day": 5, "month": 13}, "nonexistent"),
        ({"day": 29, "month": 2}, "nonexistent"),  # its next occurrence is in 2027
        ({"day": 10, "month": 11, "year": 2026}, "past"),
        ({"relative_days": -1}, "past"),
        ({"weekday": "jueves", "day": 27, "month": 11}, "mismatch"),
        ({"relative_days": 1, "weekday": "viernes"}, "mismatch"),  # tomorrow is Wednesday
        ({"relative_days": 1, "day": 20, "month": 11}, "mismatch"),
        ({"weekday": "juernes"}, "wrong_type"),
        ("2026-11-20", "wrong_type"),
        # Review 09b-1, should-fix 2.2: a day or a month out of range does not exist, even
        # alone (rule 2 before rule 3: it is not "missing the month").
        ({"day": 40}, "nonexistent"),
        ({"month": 13}, "nonexistent"),
        # Should-fix 2.5: a number of days the calendar cannot reach (the schema takes any int).
        ({"relative_days": 10**7}, "nonexistent"),  # past year 9999
        ({"relative_days": 10**12}, "nonexistent"),  # too big for a timedelta
        ({"relative_days": -(10**7)}, "nonexistent"),  # before year 1: no calendar day
        # Review 09b-2, should-fix 1: a year that does not fit a C integer (`OverflowError`).
        ({"day": 5, "month": 1, "year": 10**10}, "nonexistent"),
        ({"day": 5, "month": 1, "year": -(10**10)}, "nonexistent"),
        ({"day": 5, "month": 1, "year": 10000}, "nonexistent"),
        # Note 3: the validator does not trust the shape of a stored value.
        ({"day": "20", "month": 11}, "wrong_type"),
        ({"day": True, "month": 11}, "wrong_type"),
        ({"day": 20, "month": 11, "hour": 10}, "wrong_type"),
        ({"weekday": 5}, "wrong_type"),
    ],
)
def test_invalid_dates(value, cause):
    result = validate(MEETING, {"date": value}, NOW)

    assert result.invalid == [Invalid("date", cause)]
    assert "date" not in result.normalized
    assert "date" not in result.missing


# 4 (D5.1, D5.2: "el campo falta"; the month and the hour are never completed, TASK.md line 184)
@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("date", {"day": 27}),
        ("date", {"month": 11}),
        ("date", {"year": 2027}),
        ("date", {"weekday": "viernes", "day": 27}),  # decision B of brief 09b
        ("date", {"weekday": "viernes", "year": 2027}),  # review 09b-1, should-fix 2.3
        ("time", {"minute": 30}),
    ],
)
def test_incomplete_date_or_time_is_missing(field_name, value):
    result = validate(MEETING, {field_name: value}, NOW)

    assert field_name in result.missing
    assert all(item.field != field_name for item in result.invalid)
    assert field_name not in result.normalized


# 5 (D5.2)
@pytest.mark.parametrize(
    ("parts", "normalized"),
    [
        ({"hour": 10}, "10:00"),
        ({"hour": 9, "minute": 5}, "09:05"),
        ({"hour": 0, "minute": 0}, "00:00"),
        ({"hour": 23, "minute": 59}, "23:59"),
        ({"hour": 25, "minute": 90}, None),
        ({"hour": 24}, None),
        ({"hour": 10, "minute": 60}, None),
    ],
)
def test_time_forms(parts, normalized):
    result = validate(MEETING, {"time": parts}, NOW)

    if normalized is None:
        assert result.invalid == [Invalid("time", "bad_format")]
        assert "time" not in result.normalized
    else:
        assert result.invalid == []
        assert result.normalized["time"] == normalized


# 6 (D5.2, coherence; decision D of brief 09b: the time is the field reported)
def test_date_and_time_together_in_the_past():
    today_at_8 = {**COMPLETE_MEETING, "date": {"relative_days": 0}, "time": {"hour": 8}}
    result = validate(MEETING, today_at_8, NOW)

    assert result.invalid == [Invalid("time", "past")]
    assert result.normalized["date"] == "2026-11-17"
    assert "time" not in result.normalized
    assert "time" not in result.missing

    # Review 09b-1, should-fix 2.4: the time joins `invalid` in registry order, before a later
    # invalid field.
    with_zero = validate(MEETING, {**today_at_8, "duration_minutes": 0}, NOW)
    assert with_zero.invalid == [
        Invalid("time", "past"),
        Invalid("duration_minutes", "out_of_range"),
    ]

    # Review 09b-2, note 5: the exact moment of `now` is not past (D5.2 reads "before now").
    today, tomorrow = {"relative_days": 0}, {"relative_days": 1}
    for date, hour in ((today, 9), (today, 10), (tomorrow, 8)):
        args = {**COMPLETE_MEETING, "date": date, "time": {"hour": hour}}
        assert validate(MEETING, args, NOW).ready, (date, hour)


# The core never reads the clock: the same arguments give what `now` says (D5.1).
def test_validation_depends_only_on_now():
    tomorrow = {**COMPLETE_MEETING, "date": {"relative_days": 1}, "time": {"hour": 10}}

    assert validate(MEETING, tomorrow, NOW).normalized["date"] == "2026-11-18"
    later = validate(MEETING, tomorrow, datetime(2026, 11, 18, 8, 0))
    assert later.normalized["date"] == "2026-11-19"


# Review 09b-1, note 3: a time whose shape is not the schema's cannot be read.
@pytest.mark.parametrize(
    "parts", [{"hour": "10"}, {"hour": True}, {"hour": 10, "second": 0}, "10:30", [10, 30]]
)
def test_unreadable_time(parts):
    result = validate(MEETING, {"time": parts}, NOW)

    assert result.invalid == [Invalid("time", "wrong_type")]
    assert "time" not in result.normalized
