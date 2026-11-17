"""Deterministic validation of one step (D5): the model transcribes, the code validates.

`validate(tool, args)` reads the arguments the merge kept for one step of one tool (D4, D18)
and returns what is missing, what is invalid and why, and the normalized values of the valid
fields. It runs after every merge (R2 and *Misma tool*), so "urgente" is stored as said (D4
rule 7) and reported here, and nothing runs while something is missing or invalid (D7.1).

Each rule is declared per field *kind* in `RULES`, and its parameters come from the
`FieldSpec` (`synonyms`, `int_range`), never from the name of a tool or a field (TASK.md line
562). A field is checked in this order:
1. Absent (D4 rule 2, `presence.clean`) -> `missing`.
2. A kind without a rule yet (`date`, `time`; task 09b) -> `unchecked`: present but never
   valid, so a step with one is never `ready`.
3. The rule of its kind: a normalized value, or an `Invalid` with its cause.

The function is pure: it mutates nothing and stores nothing. The normalized values are not
kept in the state; the confirmation (task 10a) recomputes them to show them and to hash them
(D7.2).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from core.presence import clean
from core.registry import FieldKind, FieldSpec, ToolSpec

Cause = Literal["not_allowed", "out_of_range", "wrong_type"]


@dataclass(frozen=True)
class Invalid:
    """A present value that breaks the rule of its kind. It never carries the value (D2)."""

    field: str
    cause: Cause


@dataclass(frozen=True)
class Validation:
    missing: list[str] = field(default_factory=list)  # absent fields, in registry order
    invalid: list[Invalid] = field(default_factory=list)  # in registry order
    unchecked: list[str] = field(default_factory=list)  # present, of a kind with no rule yet
    normalized: dict[str, Any] = field(default_factory=dict)  # only the valid fields

    @property
    def ready(self) -> bool:
        """Nothing missing, invalid or unchecked: the step can go to confirmation (D7.1)."""
        return not (self.missing or self.invalid or self.unchecked)


class _Rejected(Exception):
    def __init__(self, cause: Cause) -> None:
        self.cause = cause


def _text(spec: FieldSpec, value: Any) -> str:
    """`text` and `person` (D5.4): a string, trimmed. The merge already anchored a person."""
    if not isinstance(value, str):
        raise _Rejected("wrong_type")
    return value.strip()


def _word(spec: FieldSpec, value: Any) -> str:
    """`word`: a string the synonym table maps to a canonical value; "urgente" is not (D2)."""
    if not isinstance(value, str):
        raise _Rejected("wrong_type")
    canonical = spec.resolve_word(value)
    if canonical is None:
        raise _Rejected("not_allowed")
    return canonical


def _int(spec: FieldSpec, value: Any) -> int:
    """`int`: a whole number that is not a bool (as D13), inside `int_range` (D5.3)."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise _Rejected("wrong_type")
    if spec.int_range is not None:
        low, high = spec.int_range
        if not low <= value <= high:
            raise _Rejected("out_of_range")
    return value


def _person_list(spec: FieldSpec, value: Any) -> list[str]:
    """`person_list` (D5.4): a list of strings, each one trimmed."""
    if not isinstance(value, list) or not all(isinstance(name, str) for name in value):
        raise _Rejected("wrong_type")
    return [name.strip() for name in value]


RULES: dict[FieldKind, Callable[[FieldSpec, Any], Any]] = {
    FieldKind.TEXT: _text,
    FieldKind.WORD: _word,
    FieldKind.INT: _int,
    FieldKind.PERSON: _text,
    FieldKind.PERSON_LIST: _person_list,
}

# Kinds whose rules are task 09b (D5.1, D5.2). Present values of these kinds are never valid yet.
UNCHECKED_KINDS: frozenset[FieldKind] = frozenset({FieldKind.DATE, FieldKind.TIME})


def validate(tool: ToolSpec, args: Mapping[str, Any]) -> Validation:
    """Validate the arguments of one step of `tool`, field by field in registry order."""
    missing, invalid, unchecked, normalized = [], [], [], {}
    for spec in tool.fields:
        value = clean(args.get(spec.name))
        if value is None:
            missing.append(spec.name)
        elif spec.kind in UNCHECKED_KINDS:
            unchecked.append(spec.name)
        else:
            try:
                normalized[spec.name] = RULES[spec.kind](spec, value)
            except _Rejected as rejected:
                invalid.append(Invalid(spec.name, rejected.cause))
    return Validation(missing, invalid, unchecked, normalized)
