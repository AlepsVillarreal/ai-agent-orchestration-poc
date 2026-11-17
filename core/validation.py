"""Deterministic validation of one step (D5): the model transcribes, the code validates.

`validate(tool, args, now)` reads the arguments the merge kept for one step of one tool (D4,
D18) and returns what is missing, what is invalid and why, and the normalized values of the
valid fields. It runs after every merge (R2 and *Misma tool*), so "urgente" is stored as said
(D4 rule 7) and reported here, and nothing runs while something is missing or invalid (D7.1).

Each rule is declared per field *kind* in `RULES`, and its parameters come from the
`FieldSpec` (`synonyms`, `int_range`), never from the name of a tool or a field (TASK.md line
562). A field is checked in this order:
1. Absent (D4 rule 2, `presence.clean`) -> `missing`.
2. The rule of its kind: a normalized value, an `Invalid` with its cause, or, for a date or a
   time without a component that cannot be completed (no month, no hour), `missing` (D5.1,
   D5.2: "el campo falta"; the code never invents it, TASK.md line 184).
Then, if the tool has one `date` field and one `time` field and both are valid, the two
together must not be before `now` (D5.2, coherence); if they are, the time is invalid (`past`).

`now` is the moment of the turn, local and naive, which the turn node reads from the clock and
passes in `TurnInput`: the core never reads the clock (D5.1: "los tests la inyectan").

The function is pure: it mutates nothing and stores nothing. The normalized values are not
kept in the state; the confirmation recomputes them to show them and to hash them (D7.2).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal

from core.presence import clean
from core.registry import FieldKind, FieldSpec, ToolSpec
from core.text import normalize

Cause = Literal[
    "not_allowed",  # a `word` outside its synonym table ("urgente")
    "out_of_range",  # an `int` outside `int_range`
    "wrong_type",  # a value of the wrong type, or a weekday word that is not one
    "nonexistent",  # a date that is not in the calendar (day 40, April 31)
    "past",  # a date before today, or a date and time together before now (D5.1b, D5.2)
    "mismatch",  # two date forms that do not give the same day (D5.1c)
    "bad_format",  # an hour outside 0-23 or a minute outside 0-59 (D5.2)
]


@dataclass(frozen=True)
class Invalid:
    """A present value that breaks the rule of its kind. It never carries the value (D2)."""

    field: str
    cause: Cause


@dataclass(frozen=True)
class Validation:
    missing: list[str] = field(default_factory=list)  # absent or incomplete, in registry order
    invalid: list[Invalid] = field(default_factory=list)  # in registry order
    normalized: dict[str, Any] = field(default_factory=dict)  # only the valid fields

    @property
    def ready(self) -> bool:
        """Nothing missing or invalid: the step can go to confirmation (D7.1)."""
        return not (self.missing or self.invalid)


class _Rejected(Exception):
    def __init__(self, cause: Cause) -> None:
        self.cause = cause


class _Incomplete(Exception):
    """A date or a time without a part the code must not complete: the field is missing."""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)  # a bool is not an int (D13)


def _text(spec: FieldSpec, value: Any, now: datetime) -> str:
    """`text` and `person` (D5.4): a string, trimmed. The merge already anchored a person."""
    if not isinstance(value, str):
        raise _Rejected("wrong_type")
    return value.strip()


def _word(spec: FieldSpec, value: Any, now: datetime) -> str:
    """`word`: a string the synonym table maps to a canonical value; "urgente" is not (D2)."""
    if not isinstance(value, str):
        raise _Rejected("wrong_type")
    canonical = spec.resolve_word(value)
    if canonical is None:
        raise _Rejected("not_allowed")
    return canonical


def _int(spec: FieldSpec, value: Any, now: datetime) -> int:
    """`int`: a whole number that is not a bool (as D13), inside `int_range` (D5.3)."""
    if not _is_int(value):
        raise _Rejected("wrong_type")
    if spec.int_range is not None:
        low, high = spec.int_range
        if not low <= value <= high:
            raise _Rejected("out_of_range")
    return value


def _person_list(spec: FieldSpec, value: Any, now: datetime) -> list[str]:
    """`person_list` (D5.4): a list of strings, each one trimmed."""
    if not isinstance(value, list) or not all(isinstance(name, str) for name in value):
        raise _Rejected("wrong_type")
    return [name.strip() for name in value]


# Weekday words (Spanish and English) -> `date.weekday()` (Monday is 0). Compared after
# `normalize`, so "Miércoles", "miercoles" and "MIÉRCOLES" are the same word.
_WEEKDAY_WORDS = (
    ("lunes", "monday"),
    ("martes", "tuesday"),
    ("miércoles", "wednesday"),
    ("jueves", "thursday"),
    ("viernes", "friday"),
    ("sábado", "saturday"),
    ("domingo", "sunday"),
)
WEEKDAYS: dict[str, int] = {
    normalize(word): number for number, words in enumerate(_WEEKDAY_WORDS) for word in words
}

_DATE_PARTS = frozenset({"day", "month", "year", "relative_days", "weekday"})
_TIME_PARTS = frozenset({"hour", "minute"})


def _date(spec: FieldSpec, value: Any, now: datetime) -> str:
    """`date` (D5.1, D5.1b, D5.1c): components as said -> `YYYY-MM-DD`, against today.

    In this order: unreadable -> `wrong_type`; a day or a month out of its range ->
    `nonexistent`; a day without a month, a month without a day, or a year without both ->
    missing, even next to another form; each form that fixes a day gives its day
    (`relative_days` from today; day and month in the given year or, without one, their next
    occurrence, today included), and a lone weekday is its next occurrence, today excluded; two
    forms that give different days, or a weekday that is not the day's -> `mismatch` (none
    wins); before today -> `past`.
    """
    if not isinstance(value, dict) or not value.keys() <= _DATE_PARTS:
        raise _Rejected("wrong_type")
    if not all(_is_int(part) for name, part in value.items() if name != "weekday"):
        raise _Rejected("wrong_type")
    weekday = None
    if "weekday" in value:
        if not isinstance(value["weekday"], str):
            raise _Rejected("wrong_type")
        weekday = WEEKDAYS.get(normalize(value["weekday"]))
        if weekday is None:
            raise _Rejected("wrong_type")

    day, month, year = value.get("day"), value.get("month"), value.get("year")
    if (day is not None and not 1 <= day <= 31) or (month is not None and not 1 <= month <= 12):
        raise _Rejected("nonexistent")
    if (day is None) != (month is None) or (year is not None and day is None):
        raise _Incomplete

    today = now.date()
    days = []  # the day each form that fixes one gives
    if "relative_days" in value:
        try:
            days.append(today + timedelta(days=value["relative_days"]))
        except OverflowError:
            raise _Rejected("nonexistent") from None
    if day is not None:
        days.append(_calendar_day(day, month, year, today))

    if days:
        resolved = days[0]
        if any(other != resolved for other in days):
            raise _Rejected("mismatch")
        if weekday is not None and resolved.weekday() != weekday:
            raise _Rejected("mismatch")
    else:  # only a weekday is left: its next occurrence, from 1 to 7 days ahead
        resolved = today + timedelta(days=(weekday - today.weekday() - 1) % 7 + 1)

    if resolved < today:
        raise _Rejected("past")
    return resolved.isoformat()


def _calendar_day(day: int, month: int, year: int | None, today: date) -> date:
    """Day and month in `year` or, without one, in the year of their next occurrence (today
    counts). A day the year does not have (April 31; February 29 of 2027) does not exist."""
    if year is None:
        year = today.year if (month, day) >= (today.month, today.day) else today.year + 1
    try:
        return date(year, month, day)
    except (ValueError, OverflowError):  # a year too large for C, e.g. 10**10, overflows
        raise _Rejected("nonexistent") from None


def _time(spec: FieldSpec, value: Any, now: datetime) -> str:
    """`time` (D5.2): components as said -> `HH:mm`. No hour -> missing; no minute -> `:00`.

    An hour from 1 to 12 is taken as it came; the confirmation shows it (D5.2, D7).
    """
    if not isinstance(value, dict) or not value.keys() <= _TIME_PARTS:
        raise _Rejected("wrong_type")
    if not all(_is_int(part) for part in value.values()):
        raise _Rejected("wrong_type")
    if "hour" not in value:
        raise _Incomplete
    hour, minute = value["hour"], value.get("minute", 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise _Rejected("bad_format")
    return f"{hour:02d}:{minute:02d}"


RULES: dict[FieldKind, Callable[[FieldSpec, Any, datetime], Any]] = {
    FieldKind.TEXT: _text,
    FieldKind.WORD: _word,
    FieldKind.INT: _int,
    FieldKind.PERSON: _text,
    FieldKind.DATE: _date,
    FieldKind.TIME: _time,
    FieldKind.PERSON_LIST: _person_list,
}


def validate(tool: ToolSpec, args: Mapping[str, Any], now: datetime) -> Validation:
    """Validate the arguments of one step of `tool`, field by field in registry order."""
    missing, invalid, normalized = [], [], {}
    for spec in tool.fields:
        value = clean(args.get(spec.name))
        if value is None:
            missing.append(spec.name)
            continue
        try:
            normalized[spec.name] = RULES[spec.kind](spec, value, now)
        except _Incomplete:
            missing.append(spec.name)
        except _Rejected as rejected:
            invalid.append(Invalid(spec.name, rejected.cause))

    past_time = _time_in_the_past(tool, normalized, now)
    if past_time is not None:
        del normalized[past_time]
        invalid.append(Invalid(past_time, "past"))
        order = [spec.name for spec in tool.fields]
        invalid.sort(key=lambda item: order.index(item.field))
    return Validation(missing, invalid, normalized)


def _time_in_the_past(tool: ToolSpec, normalized: dict[str, Any], now: datetime) -> str | None:
    """D5.2 coherence: the name of the `time` field if the valid date and time are before now.

    Paired by kind, never by name, and only when the tool has exactly one field of each: with
    more, which time goes with which date is not declared. The time is the field reported
    (only today's date can get here: an earlier one is already `past`), so the question asks
    for the time and keeps a date that was valid.
    """
    dates = [spec.name for spec in tool.fields if spec.kind == FieldKind.DATE]
    times = [spec.name for spec in tool.fields if spec.kind == FieldKind.TIME]
    if len(dates) != 1 or len(times) != 1:
        return None
    day, time = normalized.get(dates[0]), normalized.get(times[0])
    if day is None or time is None:
        return None
    return times[0] if datetime.fromisoformat(f"{day}T{time}") < now else None
