"""The interpreter's instruction, generated from the catalog, and the pending summary.

Nothing here is written by hand for a tool: tool names, tool descriptions, field names and field
descriptions come from the registry (TASK.md line 562). The general text says only what the
interpreter's job is and what each schema value means. It holds no rule about what the agent
does next (ask, confirm, run, refuse) and no canonical value of a `word` field: those decisions
live in code (TASK.md lines 166-168 and 587; D1, D2).

The pending summary is the `{pending?}` of D2 and D3. It reads only four keys of the pending
record and never shows a collected value, because the model must not see or repeat them (D2,
*Contexto y respuestas*; D3, *Aclaración*, *Estado*).
"""

from collections.abc import Mapping
from typing import Any

from core.interpretation import ACT_MEANINGS, CERTAINTY_MEANINGS
from core.registry import TOOLS, FieldKind, ToolSpec

NOTHING_PENDING = "Nothing is pending."

_JOB = (
    "You read one user message and report what the user said in THIS message, filling the "
    "response schema. Do not validate values, do not complete them and do not invent them: "
    "copy the user's words and numbers as said. Omit every field the user did not state."
)


def build_instruction(catalog: Mapping[str, ToolSpec] = TOOLS) -> str:
    """Return the fixed part of the instruction for `catalog`."""
    lines = [_JOB, "", "act (exactly one):"]
    lines += [f"- {act.value}: {meaning}" for act, meaning in ACT_MEANINGS.items()]
    lines += ["", "certainty of each operation:"]
    lines += [f"- {value.value}: {meaning}" for value, meaning in CERTAINTY_MEANINGS.items()]
    lines += [
        "",
        "depends_on: the tool an operation must wait for, only when the user says so.",
        "confirms_tool: with a confirmation, the tool the user named while confirming, if any.",
    ]

    list_fields = dict.fromkeys(
        spec.name
        for tool in catalog.values()
        for spec in tool.fields
        if spec.kind == FieldKind.PERSON_LIST
    )
    for name in list_fields:
        lines.append(
            f"{name}: the complete list the user gave (replaces); {name}_add: names the user "
            f"asked to add; {name}_remove: names the user asked to remove."
        )

    lines += [
        "When the user answers something that is pending (see Pending below), put the new "
        "data in an operation of the pending tool.",
        "",
        "Tools:",
    ]
    for tool in catalog.values():
        lines.append(f"- {tool.name}: {tool.description}")
        lines += [f"  - {spec.name}: {spec.description}" for spec in tool.fields]
    return "\n".join(lines)


def pending_summary(pending: Mapping[str, Any] | None) -> str:
    """Describe the pending record without any collected value.

    Reads only `tools`, `status`, `missing` and `last_question`; every other key of the
    record (`args`, `id`, ...) is ignored. With no record, returns `NOTHING_PENDING`.
    """
    if not pending:
        return NOTHING_PENDING
    tools = ", ".join(pending.get("tools") or []) or "none"
    missing = ", ".join(pending.get("missing") or []) or "none"
    status = pending.get("status") or "unknown"
    question = pending.get("last_question") or "none"
    return "\n".join(
        [
            f"Pending tools: {tools}",
            f"Status: {status}",
            f"Missing fields: {missing}",
            f"Last question to the user: {question}",
        ]
    )


def render_instruction(
    pending: Mapping[str, Any] | None, catalog: Mapping[str, ToolSpec] = TOOLS
) -> str:
    """The full instruction: the fixed part plus the pending summary."""
    return f"{build_instruction(catalog)}\n\nPending:\n{pending_summary(pending)}"
