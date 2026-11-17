"""Every Spanish reply of the agent, in one place (D2: replies only from templates).

Tool-specific words (action phrases, field labels) come from the registry, never from here
(TASK.md line 562). Templates marked *provisional* stand in for branches that later tasks
build; they never start or run anything.
"""

from typing import Any

from core.merge import Change, Note
from core.registry import TOOLS, FieldKind, FieldSpec, ToolSpec
from core.validation import Cause

# D10: something in the request matched no tool, and something else did.
UNKNOWN_TOOL_LINE = "Parte de tu solicitud no corresponde a ninguna de mis herramientas."

# D11: two operations of the same tool in one turn.
ONE_PER_TOOL = "Puedo hacer una operación de cada tipo por mensaje; ¿por cuál empiezo?"

# D3 gate, step 4: a confirmation, rejection or bare data with nothing pending (TASK.md line 284).
NO_PENDING_OPERATION = "No hay ninguna operación pendiente."

# Provisional: steps 2 and 3 of the D3 gate that are not built yet (tasks 12 and 13 replace it).
PENDING_NOT_SUPPORTED_YET = "Todavía no puedo continuar esa operación en esta versión."

# Provisional: rules R3, R4 and R5 (tasks 13 and 14a replace it).
ONE_OPERATION_AT_A_TIME = "Por ahora puedo atender una operación a la vez; ¿cuál necesitas primero?"

# D1.1 *Fallos* and D2: the interpreter's output was broken, outside the schema or empty.
# Nothing in the state changed (the turn node returns no state delta).
NOT_UNDERSTOOD = "No te entendí; no cambié nada. ¿Puedes decirlo de otra forma?"

# D1.1 *Fallos*: any other failure of the model call (429, timeout, no key, network).
MODEL_UNAVAILABLE = (
    "El modelo no está disponible ahora; no cambié nada. Intenta de nuevo en un momento."
)

# D4 rule 9: a name not written in the message was dropped. Fixed, so it never shows the name.
REJECTED_NAME_LINE = "No tomé en cuenta un nombre que no aparece escrito en tu mensaje."

# Provisional: every field is in, but a date or a time has no rule yet (task 09b replaces it).
# It names no tool, so it holds for any step with a `date` or `time` field.
DATE_TIME_NOT_CHECKED_YET = (
    "Tengo los datos; todavía no puedo validar fechas ni horas en esta versión."
)


def join_es(items: list[str], last_word: str = "y") -> str:
    """Join items the Spanish way: "a, b y c" (or "a, b o c")."""
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} {last_word} {items[-1]}"


def capabilities() -> str:
    """R1 and the reply to `question`/`other`: what the agent can do, from the registry."""
    actions = join_es([tool.action_phrase for tool in TOOLS.values()], "o")
    return f"Puedo ayudarte a {actions}. ¿Qué necesitas?"


def missing_question(tool: ToolSpec, fields: list[str]) -> str:
    """One question for every missing or invalid field, with registry labels in registry order.

    It never lists allowed values (D17) and never shows a collected value (D2): it is stored as
    `last_question`, which the interpreter reads. The invalid lines that come before it in the
    reply may list the allowed values; they are not part of it.
    """
    labels = [spec.label for spec in tool.fields if spec.name in fields]
    return f"Para {tool.action_phrase} necesito {join_es(labels)}."


def invalid_line(spec: FieldSpec, cause: Cause) -> str:
    """One line per invalid field, written by cause and never by tool (TASK.md lines 186-200).

    It names the field and the problem, never the value the user gave (the value was shown by
    its change line, D39). A `not_allowed` line lists the allowed values in Spanish.
    """
    if cause == "not_allowed":
        options = join_es([spec.value_labels.get(value, value) for value in spec.allowed], "o")
        return f"No reconozco {spec.label} que indicaste; puede ser {options}."
    if cause == "out_of_range" and spec.int_range is not None:
        low, high = spec.int_range
        return f"{_capitalized(spec.label)} debe ser un número entre {low} y {high}."
    return f"No pude leer {spec.label}."


def confirmation_summary(shown: list[dict[str, Any]]) -> str:
    """D7.1: the normalized values of each step, the ones the hash binds (D7.2).

    One header per step with the action phrase, then one line per field with its label, in
    registry order. A `word` value is shown by its Spanish label ("alta", "correo"), never by the
    canonical value the tool receives ("high", "email").
    """
    lines = []
    for step in shown:
        tool = TOOLS[step["tool"]]
        values = step["normalized"]
        lines.append(f"Voy a {tool.action_phrase} con estos datos:")
        lines += [
            f"- {_capitalized(spec.label)}: {_normalized_value(spec, values[spec.name])}"
            for spec in tool.fields
            if spec.name in values
        ]
    return "\n".join(lines)


def confirmation_question(tools: list[ToolSpec]) -> str:
    """D7.1 and D43: the question stored as `last_question` while awaiting confirmation.

    It names the action only, never a value (collected or allowed): the interpreter reads it in
    the pending summary (D2, D14), and a value it saw could come back in a "yes" (D38).
    """
    actions = join_es([tool.action_phrase for tool in tools])
    return f"¿Confirmas que quieres {actions}? Responde sí o no."


def _normalized_value(spec: FieldSpec, value: Any) -> str:
    if spec.kind == FieldKind.WORD:
        return spec.value_labels.get(value, value)
    if spec.kind == FieldKind.PERSON_LIST:
        return join_es(value)
    return str(value)


def _capitalized(text: str) -> str:
    return f"{text[:1].upper()}{text[1:]}"


def show_value(spec: FieldSpec, value: Any) -> str:
    """A collected value as the user said it, written by field kind, never by tool (D4 rule 9).

    Nothing is converted or completed: a date or a time shows the components that arrived
    (day 40 stays 40, a lone hour gets no minutes); resolving them is the validator's job (D5).
    Surrounding spaces of a string are trimmed, as the validator does (note (hh)).
    """
    if spec.kind == FieldKind.PERSON_LIST:
        return join_es([_trimmed(name) for name in value])
    if spec.kind == FieldKind.DATE:
        return _show_date(value)
    if spec.kind == FieldKind.TIME:
        return _show_time(value)
    return _trimmed(value)


def _trimmed(value: Any) -> str:
    return value.strip() if isinstance(value, str) else str(value)


def _show_date(parts: dict[str, Any]) -> str:
    shown = []
    if "weekday" in parts:
        shown.append(str(parts["weekday"]))
    if "relative_days" in parts:
        days = parts["relative_days"]
        shown.append(_RELATIVE_DAYS.get(days, f"{days} días desde hoy"))
    for key, word in (("day", "día"), ("month", "mes"), ("year", "año")):
        if key in parts:
            shown.append(f"{word} {parts[key]}")
    return ", ".join(shown)


_RELATIVE_DAYS = {0: "hoy", 1: "mañana", 2: "pasado mañana"}


def _show_time(parts: dict[str, Any]) -> str:
    if "hour" in parts and "minute" in parts:
        return f"{parts['hour']}:{parts['minute']:02d}"
    if "hour" in parts:
        return f"las {parts['hour']}"
    return f"minuto {parts['minute']}"


def change_line(spec: FieldSpec, change: Change) -> str:
    """D4 rules 8 and 9: a fill with its value, an overwrite old -> new, a clear with the old
    list. Never stored in `last_question` (D2, D14)."""
    if change.kind == "fill":
        return f"Anoté {spec.label}: {show_value(spec, change.new)}."
    old = show_value(spec, change.old)
    if change.kind == "overwrite":
        return f"Cambié {spec.label}: {old} → {show_value(spec, change.new)}."
    return f"Vacié {spec.label} (antes: {old})."


def note_line(spec: FieldSpec, note: Note) -> str:
    """D4 rule 6: a list operation that changed nothing, or the same name added and removed.

    Written in the present, so `add_present` is also true in a new flow whose list already
    brought the name in this turn (autodecided in task 08).
    """
    if note.kind == "add_present":
        return f"{note.name} ya está en {spec.label}."
    if note.kind == "remove_absent":
        return f"{note.name} no está en {spec.label}, así que no quité a nadie."
    return f"Pediste agregar y quitar a {note.name}; queda fuera de {spec.label}."
