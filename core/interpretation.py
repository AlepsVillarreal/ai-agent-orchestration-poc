"""The interpreter's output schema: the LLM/code boundary of D2.

The model fills `TurnInterpretation` with what the user said in the current message, never
with what is valid: values arrive as the user's words or as plain numbers, without ranges or
allowed values, so the deterministic validator sees "urgente", day 40 or hour 25 and can name
the problem (D2; TASK.md lines 166-168 and 194-198).

The shared `args` model is generated from the tool catalog, one field per field *name*, with
its type taken from the field *kind*, never from the tool (TASK.md line 562). Every
`person_list` field gets two extra buckets, `<field>_add` and `<field>_remove` (D2 amendment).

Constraints of the boundary (ADK 2.14.1, GenAI SDK 2.34.0):
- No `dict` field at any level: it would produce `additionalProperties`, which the SDK rejects
  in Gemini Developer API mode.
- No `extra="forbid"`: a key the schema does not declare is dropped, like the contract filter of
  D2 drops an argument of another tool (autodecided in task 02).
- ADK hands the code `model_validate_json(text).model_dump(exclude_none=True)`: nulls vanish,
  while `""`, `[]`, `{}` and `0` arrive (D4 rule 2).
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, create_model

from core.registry import TOOLS, FieldKind, ToolSpec

# Pydantic sends a class docstring to the model as the schema description, so the docstrings
# of the schema classes below are written for the model; references to decisions go in comments.


class Act(StrEnum):  # the seven values fixed in D3
    """What the user is doing with this message."""

    REQUEST_ACTION = "request_action"
    PROVIDE_INFO = "provide_info"
    CONFIRM = "confirm"
    REJECT = "reject"
    CANCEL = "cancel"
    QUESTION = "question"
    OTHER = "other"


class Certainty(StrEnum):  # D2
    """How directly the message asks for an operation."""

    EXPLICIT = "explicit"
    PLAUSIBLE = "plausible"


# One line per value, in English. The instruction renders these (core/instruction.py), so the
# meaning of each value is written once. They describe the user's message, never what the
# agent does next.
ACT_MEANINGS: dict[Act, str] = {
    Act.REQUEST_ACTION: "the user asks for one or more operations.",
    Act.PROVIDE_INFO: "the user gives data for something that is pending.",
    Act.CONFIRM: "the user says yes to what is pending.",
    Act.REJECT: "the user says no to what is pending, possibly with a correction.",
    Act.CANCEL: "the user wants to drop what is pending.",
    Act.QUESTION: "the user asks for information and does not ask for an operation.",
    Act.OTHER: "anything else (a greeting, small talk, an unclear message).",
}

CERTAINTY_MEANINGS: dict[Certainty, str] = {
    Certainty.EXPLICIT: "the user asked for this operation.",
    Certainty.PLAUSIBLE: "a reading the message allows but does not ask for.",
}

_AS_SAID = (
    "Never correct or complete a value: copy the number as said, even if it looks wrong. "
    "Omit a component the user did not say."
)


class DateParts(BaseModel):  # the validator resolves it (D5.1)
    """A date as the user said it."""

    day: int | None = Field(None, description=f"Day of the month as said. {_AS_SAID}")
    month: int | None = Field(None, description=f"Month number ('noviembre' -> 11). {_AS_SAID}")
    year: int | None = Field(None, description=f"Year as said. {_AS_SAID}")
    relative_days: int | None = Field(
        None,
        description="Days from today ('hoy' -> 0, 'mañana' -> 1, 'pasado mañana' -> 2). "
        "Omit it if the user did not say a relative day.",
    )
    weekday: str | None = Field(
        None,
        description="The weekday word as the user said it ('viernes'). Do not turn it into a date.",
    )


class TimeParts(BaseModel):  # the validator checks the ranges (D5.2)
    """A time of day as the user said it."""

    hour: int | None = Field(
        None,
        description="Hour. Use the 24-hour clock only when the user said the part of the day "
        f"('3 de la tarde' -> 15); otherwise the number as said. {_AS_SAID}",
    )
    minute: int | None = Field(None, description=f"Minutes as said. {_AS_SAID}")


def _list_descriptions(name: str) -> dict[str, str]:
    return {
        name: "The complete list of names the user gave; it replaces any earlier list.",
        f"{name}_add": f"Names the user asked to add to {name}.",
        f"{name}_remove": f"Names the user asked to remove from {name}.",
    }


# Type of an `args` field per field kind. Lists also get their buckets (see below).
_TYPE_BY_KIND: dict[FieldKind, Any] = {
    FieldKind.TEXT: str,
    FieldKind.WORD: str,
    FieldKind.PERSON: str,
    FieldKind.INT: int,
    FieldKind.DATE: DateParts,
    FieldKind.TIME: TimeParts,
    FieldKind.PERSON_LIST: list[str],
}


def build_args_model(catalog: Mapping[str, ToolSpec] = TOOLS) -> type[BaseModel]:
    """Generate the shared `args` model from a catalog.

    One optional field per field name, in order of first appearance; a `person_list` field is
    followed by its `_add` and `_remove` buckets. No ranges and no allowed values (D2).
    A field name used with two different kinds, or a bucket name that clashes with a field,
    raises `ValueError`: the shared model could not represent both.
    """
    kinds: dict[str, FieldKind] = {}
    for tool in catalog.values():
        for spec in tool.fields:
            if kinds.setdefault(spec.name, spec.kind) != spec.kind:
                raise ValueError(f"field {spec.name!r} has two kinds in the catalog")

    definitions: dict[str, Any] = {}
    for name, kind in kinds.items():
        python_type = _TYPE_BY_KIND[kind]
        if kind == FieldKind.PERSON_LIST:
            for list_name, description in _list_descriptions(name).items():
                if list_name != name and list_name in kinds:
                    raise ValueError(f"bucket {list_name!r} clashes with a catalog field")
                definitions[list_name] = (
                    python_type | None,
                    Field(None, description=description),
                )
        else:
            definitions[name] = (python_type | None, None)

    return create_model("ToolArgs", **definitions)


ToolArgs = build_args_model()


class Operation(BaseModel):
    tool: str = Field(description="Name of the tool, from the catalog in the instruction.")
    certainty: Certainty
    depends_on: str | None = Field(
        None,
        description="Name of the tool this operation must wait for, only if the user said so "
        "('cuando esté creado').",
    )
    args: ToolArgs = Field(  # type: ignore[valid-type]
        default_factory=ToolArgs,
        description="Arguments the user stated in this message. Omit what was not said.",
    )


class TurnInterpretation(BaseModel):  # D2: code decides everything else
    """What the user said in the current message."""

    act: Act
    operations: list[Operation] = Field(default_factory=list)
    confirms_tool: str | None = Field(
        None,
        description="With a confirmation, the tool the user named while confirming, if any.",
    )
    ambiguity_reason: str | None = Field(
        None, description="Why the message could mean more than one thing, if it could."
    )
