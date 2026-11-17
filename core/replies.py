"""Every Spanish reply of the agent, in one place (D2: replies only from templates).

Tool-specific words (action phrases, field labels) come from the registry, never from here
(TASK.md line 562).
"""

from collections.abc import Sequence
from typing import Any

from core.history import FAILED, RAN, REUSED
from core.merge import Change, Note
from core.plan import receives_from
from core.registry import TOOLS, FieldKind, FieldSpec, ToolSpec
from core.validation import Cause

# D10: something in the request matched no tool, and something else did.
UNKNOWN_TOOL_LINE = "Parte de tu solicitud no corresponde a ninguna de mis herramientas."

# D11: two operations of the same tool in one turn.
ONE_PER_TOOL = "Puedo hacer una operación de cada tipo por mensaje; ¿por cuál empiezo?"

# D3 gate, step 4: a confirmation, rejection or bare data with nothing pending (TASK.md line 284).
NO_PENDING_OPERATION = "No hay ninguna operación pendiente."

# D3, *Lectura de la respuesta*, rule 1: a "no" or a "cancel" to a clarification. Nothing had
# started, so it is not the discard line of D66 ("No voy a…").
NO_ACTION_STARTED = "No inicié ninguna acción. Dime qué necesitas cuando quieras."

# D1.1 *Fallos* and D2: the interpreter's output was broken, outside the schema or empty.
# Nothing in the state changed (the turn node returns no state delta).
NOT_UNDERSTOOD = "No te entendí; no cambié nada. ¿Puedes decirlo de otra forma?"

# D1.1 *Fallos*: any other failure of the model call (429, timeout, no key, network).
MODEL_UNAVAILABLE = (
    "El modelo no está disponible ahora; no cambié nada. Intenta de nuevo en un momento."
)

# D4 rule 9: a name not written in the message was dropped. Fixed, so it never shows the name.
REJECTED_NAME_LINE = "No tomé en cuenta un nombre que no aparece escrito en tu mensaje."

# D7.2 and the author's answer on note (ddd): a "yes" arrived, the values shown now differ from
# the ones the hash bound ("mañana" after midnight) and the record is still valid, so the
# confirmation is shown again after this line instead of running.
DATA_CHANGED_LINE = "Los datos cambiaron desde que te los mostré; revísalos antes de confirmar."


def join_es(items: list[str], last_word: str = "y") -> str:
    """Join items the Spanish way: "a, b y c" (or "a, b o c")."""
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} {last_word} {items[-1]}"


def capabilities(ask: bool = True) -> str:
    """R1 and the reply to `question`/`other`: what the agent can do, from the registry.

    `ask=False` drops the closing "¿Qué necesitas?": with something pending, the reply goes on
    with what is pending instead of inviting a new request that would abandon it.
    """
    actions = join_es([tool.action_phrase for tool in TOOLS.values()], "o")
    sentence = f"Puedo ayudarte a {actions}."
    return f"{sentence} ¿Qué necesitas?" if ask else sentence


def discarded_line(tools: list[ToolSpec]) -> str:
    """D7.3 and D3.1: what was pending was discarded without running, named by its actions.

    Used alone after a "cancel" or a plain "no", and before the new flow's reply after a change
    of intention. No values and no "Listo" (the word of a result, D48).
    """
    actions = join_es([tool.action_phrase for tool in tools], "ni")
    return f"No voy a {actions}; descarté lo que tenía pendiente."


def clarification_question(tools: list[ToolSpec]) -> str | None:
    """D3, *Pregunta*: the clarification question by number of candidates, from the registry.

    Every candidate is listed, the `explicit` one too: a yes/no on the `plausible` one alone
    could not say "only the other". None with four or more candidates, which D3 never asks
    about (unreachable with three tools). It carries no value, so it is stored as
    `last_question` (D2, D14), and it never reads the model's reason for the ambiguity.
    """
    actions = [tool.action_phrase for tool in tools]
    if len(actions) == 1:
        return f"¿Quieres {actions[0]}? Responde sí o no."
    if len(actions) == 2:
        return f"¿Quieres {actions[0]}, {actions[1]}, o ambas?"
    if len(actions) == 3:
        return f"¿Qué necesitas: {join_es(actions, 'o')}? Puedes elegir varias."
    return None


def inherited_lines(tool: ToolSpec, args: dict[str, Any]) -> list[str]:
    """D4 rule 9: a flow born from a clarification lists every value it inherited.

    One line per stored field, in registry order, written like a `fill` (D39), so the user sees
    what the chosen candidate already had before the lines of the turn that chose it.
    """
    return [
        change_line(spec, Change(spec.name, "fill", None, args[spec.name]))
        for spec in tool.fields
        if spec.name in args
    ]


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
    # The next four are used by `date` and `time`, whose labels are feminine ("la fecha", "la
    # hora"); the first two are the phrases of TASK.md line 198.
    if cause == "nonexistent":
        return f"{_capitalized(spec.label)} indicada no existe."
    if cause == "bad_format":
        return f"{_capitalized(spec.label)} está fuera del formato válido."
    if cause == "past":
        return f"{_capitalized(spec.label)} indicada ya pasó."
    if cause == "mismatch":
        return f"Las partes de {spec.label} que indicaste no dan el mismo día."
    return f"No pude leer {spec.label}."


def confirmation_summary(shown: list[dict[str, Any]], depends_on: Sequence[str | None] = ()) -> str:
    """D7.1: the normalized values of each step, the ones the hash binds (D7.2).

    One header per step with the action phrase, then one line per field with its label, in
    registry order. A `word` value is shown by its Spanish label ("alta", "correo"), never by the
    canonical value the tool receives ("high", "email"). `depends_on` gives each step's
    dependency, in step order (none for a single operation): a step that depends on the tool its
    registry says it receives an ID from gets `id_link_line` after its values (D8.2), by the
    same condition that appends the ID when the plan runs (`core/plan.py`, `receives_from`).
    """
    lines = []
    for index, step in enumerate(shown):
        tool = TOOLS[step["tool"]]
        values = step["normalized"]
        lines.append(f"Voy a {tool.action_phrase} con estos datos:")
        lines += [
            f"- {_capitalized(spec.label)}: {_normalized_value(spec, values[spec.name])}"
            for spec in tool.fields
            if spec.name in values
        ]
        if index < len(depends_on) and receives_from(tool.name, depends_on[index]) is not None:
            lines.append(id_link_line(tool))
    return "\n".join(lines)


def id_link_line(tool: ToolSpec) -> str:
    """D8.2, decision D of brief 14a: which field of a step gets the producer's ID at its end.

    Written from the registry ("El mensaje llevará al final el número de ticket."), so no tool
    is named here (TASK.md line 562). Shown in the confirmation only, never in `last_question`.
    """
    link = tool.receives_id
    label = next(spec.label for spec in tool.fields if spec.name == link.field)
    return f"{_capitalized(label)} llevará al final el número de {TOOLS[link.source].short_name}."


def confirmation_question(tools: list[ToolSpec]) -> str:
    """D7.1 and D43: the question stored as `last_question` while awaiting confirmation.

    It names the action only, never a value (collected or allowed): the interpreter reads it in
    the pending summary (D2, D14), and a value it saw could come back in a "yes" (D38).
    """
    actions = join_es([tool.action_phrase for tool in tools])
    return f"¿Confirmas que quieres {actions}? Responde sí o no."


def result_line(tool: ToolSpec, result_id: str) -> str:
    """The reply for one step that ran (TASK.md lines 24 and 246): what was done and its ID.

    The phrase comes from the registry and the ID from the field the registry names (TASK.md
    line 562), read once by the core and copied as the tool returned it, never assumed to have a
    format. The tool's own `message` is not shown: it has the canonical values ("high", "email")
    and repeats what was just confirmed; it stays in `history` with the rest of the result (D6).
    """
    return f"Listo: {tool.done_phrase} {result_id}."


def step_line(tool: ToolSpec, outcome: str, result_id: str | None) -> str:
    """One line per step tried after a "yes" (D48, D9.1-D9.3; D61).

    Phrases from the registry, never the tool's `message`, and no grammatical gender that would
    fail with "reunión" or "notificación". `reused`: the same execution was done already;
    `failed`: the tool answered an error and asking again runs it again (D9.3); `uncertain`: it
    cannot be known whether it happened, and it is not retried (D9.2).
    """
    if outcome == RAN:
        return result_line(tool, result_id)
    if outcome == REUSED:
        return f"Ya {tool.done_phrase} {result_id} con estos mismos datos; no lo repetí."
    if outcome == FAILED:
        return (
            f"No pude {tool.action_phrase}: la herramienta respondió con un error. "
            "Si me lo pides de nuevo, lo intento otra vez."
        )
    return (
        f"No pude confirmar si llegué a {tool.action_phrase}; "
        "no lo voy a reintentar para no duplicarlo."
    )


def skipped_line(tool: ToolSpec) -> str:
    """One line per step of a plan not tried, after the line of the step that stopped it (D8.1,
    D8.3; decision B of brief 14b): the user hears about every operation (TASK.md line 268).
    "Un paso anterior": in a plan of three, the third step's previous step was not tried either.

    No values and no "Listo" (the word of a result, D48).
    """
    return f"No intenté {tool.action_phrase} porque un paso anterior no se completó."


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
