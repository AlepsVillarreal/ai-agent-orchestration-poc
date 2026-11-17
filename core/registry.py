"""Tool registry: the single source of truth about the tools.

Everything the rest of the code needs to know about a tool comes from here: its fields and
their kinds, the descriptions the interpreter reads, the Spanish labels used in replies, the
synonym tables, the name of the result ID field, the contract and the function that runs it.
Adding a tool means adding one `ToolSpec` to `TOOLS` (TASK.md line 562).

Rules elsewhere (merge, validation, list buckets) are declared per field *kind*, never per tool,
so they keep working as the catalog grows (D4 rule 1; D2 amendment). Their parameters (allowed
values, synonyms, ranges) live on each `FieldSpec`.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, get_args

from pydantic import BaseModel

from core import mock_tools
from core.contracts import (
    Channel,
    CreateTicketArgs,
    Priority,
    ScheduleMeetingArgs,
    SendNotificationArgs,
)
from core.text import normalize


class FieldKind(StrEnum):
    """Kinds of field (D4 rule 1). Every later rule is declared per kind."""

    TEXT = "text"  # free text: title, description, message
    WORD = "word"  # one word from a closed set: priority, channel
    INT = "int"  # whole number: duration_minutes
    PERSON = "person"  # one name: assignee, recipient
    DATE = "date"  # YYYY-MM-DD, filled by components
    TIME = "time"  # HH:mm, filled by components
    PERSON_LIST = "person_list"  # list of names: attendees


@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: FieldKind
    description: str  # English, read by the interpreter: how to transcribe this field
    label: str  # Spanish, used when asking for it: "la prioridad", "a quién se asigna"
    allowed: tuple[str, ...] = ()  # canonical values of a `word` field
    synonyms: dict[str, tuple[str, ...]] = field(default_factory=dict)  # canonical -> words
    # Spanish name of each value of `allowed`, for replies: "alta" for "high".
    value_labels: dict[str, str] = field(default_factory=dict)
    int_range: tuple[int, int] | None = None  # inclusive bounds of an `int` field (D5.3)

    def resolve_word(self, word: str) -> str | None:
        """Map the user's word to its canonical value ("Alta" -> "high"), or None if unknown.

        Only direct translations are in the tables, so "urgente" returns None and is reported
        as invalid instead of silently becoming "high" (D2).
        """
        wanted = normalize(word)
        for canonical, words in self.synonyms.items():
            if any(normalize(known) == wanted for known in words):
                return canonical
        return None


@dataclass(frozen=True)
class IdLink:
    """A tool receives the result ID of another tool of the same plan (D8.2, D25).

    Declared on the receiving tool. `source` names the producing tool, whose ID is the value of
    its `result_id_field`; `field` is the receiver's own field that gets the ID at its end when
    the plan runs (`core/plan.py`, `received_values`). The plan reads this to infer a dependency
    the model did not report (D8.4), so no tool name is written in the core (TASK.md line 562).
    """

    source: str  # the tool that produces the ID: "create_ticket"
    field: str  # this tool's field that receives it: "message"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str  # English, read by the interpreter
    action_phrase: str  # Spanish, D3: "crear un ticket"
    short_name: str  # Spanish, D3: "ticket"
    done_phrase: str  # Spanish, the reply after it runs: "creé el ticket" (+ the result ID)
    fields: tuple[FieldSpec, ...]  # all required, in the order of TASK.md §2.1
    contract: type[BaseModel]
    result_id_field: str  # "ticket_id", ...
    execute: Callable[[dict[str, Any]], dict[str, Any]]  # the mock
    receives_id: IdLink | None = None  # the ID this tool gets from another step (D8.2, D25)


# Field descriptions follow D2: the model transcribes what the user said; code validates.
# The `word` descriptions must not list the canonical values, or the model would turn
# "urgente" into a valid value instead of passing it on to be rejected.
_PERSON_RULE = "Copy the name exactly as written in the message; never infer it from a pronoun."

TOOLS: dict[str, ToolSpec] = {
    "create_ticket": ToolSpec(
        name="create_ticket",
        description="Create a support ticket to report or track a problem.",
        action_phrase="crear un ticket",
        short_name="ticket",
        done_phrase="creé el ticket",
        fields=(
            FieldSpec(
                name="title",
                kind=FieldKind.TEXT,
                description="Ticket title, only if the user stated one. "
                "Do not derive it from the description.",
                label="el título",
            ),
            FieldSpec(
                name="description",
                kind=FieldKind.TEXT,
                description="What the problem or request is, in the user's words.",
                label="la descripción",
            ),
            FieldSpec(
                name="priority",
                kind=FieldKind.WORD,
                description="The priority word exactly as the user said it (e.g. 'alta'). "
                "Copy it; do not translate it or replace it with another word.",
                label="la prioridad",
                allowed=get_args(Priority),
                synonyms={
                    "high": ("high", "alta"),
                    "medium": ("medium", "media"),
                    "low": ("low", "baja"),
                },
                value_labels={"low": "baja", "medium": "media", "high": "alta"},
            ),
            FieldSpec(
                name="assignee",
                kind=FieldKind.PERSON,
                description=f"Person the ticket is assigned to. {_PERSON_RULE}",
                label="a quién se asigna",
            ),
        ),
        contract=CreateTicketArgs,
        result_id_field="ticket_id",
        execute=mock_tools.create_ticket,
    ),
    "schedule_meeting": ToolSpec(
        name="schedule_meeting",
        description="Schedule a meeting with one or more people.",
        action_phrase="programar una reunión",
        short_name="reunión",
        done_phrase="programé la reunión",
        fields=(
            FieldSpec(
                name="title",
                kind=FieldKind.TEXT,
                description="Meeting title or topic, only if the user stated one.",
                label="el título",
            ),
            FieldSpec(
                name="date",
                kind=FieldKind.DATE,
                description="Meeting date. Fill its components as the user said them; "
                "do not resolve them to a calendar date or complete missing parts.",
                label="la fecha",
            ),
            FieldSpec(
                name="time",
                kind=FieldKind.TIME,
                description="Meeting start time, filled by its components exactly as the "
                "user said them. Do not complete missing parts.",
                label="la hora",
            ),
            FieldSpec(
                name="duration_minutes",
                kind=FieldKind.INT,
                description="Meeting length as a number of minutes. Convert words to a "
                "number ('media hora' -> 30, 'una hora' -> 60).",
                label="la duración en minutos",
                int_range=(1, 480),  # D5.3: up to 8 h; 0 and negatives are invalid
            ),
            FieldSpec(
                name="attendees",
                kind=FieldKind.PERSON_LIST,
                description=f"People attending the meeting. For each one: {_PERSON_RULE}",
                label="los asistentes",
            ),
        ),
        contract=ScheduleMeetingArgs,
        result_id_field="meeting_id",
        execute=mock_tools.schedule_meeting,
    ),
    "send_notification": ToolSpec(
        name="send_notification",
        description="Send a notification message to a person through a channel.",
        action_phrase="enviar una notificación",
        short_name="notificación",
        done_phrase="envié la notificación",
        fields=(
            FieldSpec(
                name="recipient",
                kind=FieldKind.PERSON,
                description=f"Person who receives the notification. {_PERSON_RULE}",
                label="a quién se envía",
            ),
            FieldSpec(
                name="message",
                kind=FieldKind.TEXT,
                description="The content to send, in the user's words.",
                label="el mensaje",
            ),
            FieldSpec(
                name="channel",
                kind=FieldKind.WORD,
                description="The channel word exactly as the user said it (e.g. 'correo'). "
                "Copy it; do not translate it or replace it with another word.",
                label="el canal",
                allowed=get_args(Channel),
                synonyms={
                    "email": ("email", "e-mail", "mail", "correo", "correo electrónico"),
                    "sms": ("sms", "mensaje de texto"),
                    "push": ("push", "notificación push"),
                },
                value_labels={"email": "correo", "sms": "SMS", "push": "push"},
            ),
        ),
        contract=SendNotificationArgs,
        result_id_field="notification_id",
        execute=mock_tools.send_notification,
        # D8.2: after `create_ticket` in a plan, the ticket's ID goes at the end of `message`.
        receives_id=IdLink(source="create_ticket", field="message"),
    ),
}


def list_buckets(spec: FieldSpec) -> tuple[str, str]:
    """The `(add, remove)` bucket keys of a `person_list` field (D2 amendment).

    `attendees` -> `("attendees_add", "attendees_remove")`. The core builds these keys here
    only, so the suffix is written once for `core/merge.py` and `core/turn.py`.
    """
    if spec.kind != FieldKind.PERSON_LIST:
        raise ValueError(f"field {spec.name!r} is not a list and has no buckets")
    return f"{spec.name}_add", f"{spec.name}_remove"


def fields_of_kind(kind: FieldKind) -> list[tuple[str, FieldSpec]]:
    """All `(tool_name, field)` pairs of the given kind across `TOOLS`, in catalog order."""
    return [
        (tool.name, spec) for tool in TOOLS.values() for spec in tool.fields if spec.kind == kind
    ]
