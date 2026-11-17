"""The function that decides one turn: version 0 of the core.

`handle_turn` receives the pending record and the interpreter's output and returns a template
reply plus the state change. It reads no message text, calls no model and runs no tool. The
ADK turn node (task 04) is its only caller: it puts `reply` in the event's message and
`state_delta` in the event's state (D1.1).

Order of a turn (D3, *Compuerta de entrada*):
1. Registry filter: drop operations of unknown tools and keep only contract keys (D2, D10).
2. D11: two operations of the same tool -> ask which one first; state unchanged.
3. Something pending -> provisional template (steps 2 and 3 of D3 come in later tasks).
4. Nothing pending -> route by `act`; a request goes to the rules table (R1-R5), which counts
   operations by certainty and never looks at tool names (TASK.md line 562).
"""

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from core import replies
from core.interpretation import Act, Certainty, TurnInterpretation
from core.presence import drop_absent, missing_fields
from core.registry import TOOLS, FieldKind, ToolSpec


@dataclass
class TurnOutcome:
    reply: str
    state_delta: dict[str, Any] = field(default_factory=dict)  # empty = state unchanged (D1.1)
    route: str = ""  # which branch decided the turn: "R2", "d11", ... (tests and the log read it)


def handle_turn(pending: dict | None, interpretation: TurnInterpretation) -> TurnOutcome:
    operations = _filter_by_registry(interpretation)
    dropped_some = len(operations) < len(interpretation.operations)

    outcome = _decide(pending, interpretation.act, operations)

    # D10: tell the user part of the request was ignored, unless nothing was left (R1 then
    # answers with the capabilities). The line is not part of `last_question`.
    if dropped_some and operations:
        outcome.reply = f"{replies.UNKNOWN_TOOL_LINE}\n{outcome.reply}"
    return outcome


def _decide(pending: dict | None, act: Act, operations: list[dict]) -> TurnOutcome:
    tool_counts = Counter(operation["tool"] for operation in operations)
    if any(count > 1 for count in tool_counts.values()):
        return TurnOutcome(replies.ONE_PER_TOOL, route="d11")

    if pending:
        return TurnOutcome(replies.PENDING_NOT_SUPPORTED_YET, route="pending_provisional")

    # D3 gate, step 4: nothing is pending.
    if act in (Act.CONFIRM, Act.REJECT, Act.CANCEL) or (act == Act.PROVIDE_INFO and not operations):
        return TurnOutcome(replies.NO_PENDING_OPERATION, route="nothing_pending")
    if act in (Act.QUESTION, Act.OTHER):
        return TurnOutcome(replies.capabilities(), route="template")
    # `request_action`, or `provide_info` with operations, which is treated the same.
    return _rules_table(operations)


def _rules_table(operations: list[dict]) -> TurnOutcome:
    explicit = [op for op in operations if op["certainty"] == Certainty.EXPLICIT]
    plausible = [op for op in operations if op["certainty"] == Certainty.PLAUSIBLE]

    if not operations:
        return TurnOutcome(replies.capabilities(), route="R1")
    if len(explicit) == 1 and not plausible:
        return _start_flow(explicit[0])
    if len(explicit) >= 2 and not plausible:
        route = "R3"
    elif explicit:
        route = "R4"
    else:
        route = "R5"
    return TurnOutcome(replies.ONE_OPERATION_AT_A_TIME, route=route)


def _start_flow(operation: dict) -> TurnOutcome:
    """R2: create the pending record in `collecting` and ask for every missing field.

    `args` keeps the user's words unvalidated (D4 rule 7) and only contract fields (D4 rule
    10); list buckets are left out, because the merge of task 08 applies them. A record is
    always a list of steps: one operation is a one-step plan (autodecided G in task 03).
    """
    tool = TOOLS[operation["tool"]]
    field_names = {spec.name for spec in tool.fields}
    args = drop_absent(
        {name: value for name, value in operation["args"].items() if name in field_names}
    )
    missing = missing_fields(tool, args)
    question = replies.missing_question(tool, missing) if missing else None

    record = {
        "status": "collecting",
        "tools": [tool.name],
        "steps": [{"tool": tool.name, "args": args}],
        "missing": missing,
        "last_question": question,
    }
    # Nothing runs here even when the request is complete: validation and confirmation
    # (tasks 09b and 10a) come first (D7.1).
    reply = question or replies.READY_NOT_AVAILABLE_YET
    return TurnOutcome(reply, state_delta={"pending": record}, route="R2")


def _filter_by_registry(interpretation: TurnInterpretation) -> list[dict]:
    """D3 step 1 and D2's contract filter, as plain dicts.

    Operations of a tool not in `TOOLS` are dropped (D10). The rest keep only the keys of
    their tool: its field names and, for a `person_list` field, its `_add`/`_remove` buckets.
    """
    kept = []
    for operation in interpretation.operations:
        tool = TOOLS.get(operation.tool)
        if tool is None:
            continue
        keys = _contract_keys(tool)
        args = operation.args.model_dump(exclude_none=True)
        kept.append(
            {
                "tool": tool.name,
                "certainty": operation.certainty,
                "args": {name: value for name, value in args.items() if name in keys},
            }
        )
    return kept


def _contract_keys(tool: ToolSpec) -> set[str]:
    keys = set()
    for spec in tool.fields:
        keys.add(spec.name)
        if spec.kind == FieldKind.PERSON_LIST:
            keys.update({f"{spec.name}_add", f"{spec.name}_remove"})
    return keys
