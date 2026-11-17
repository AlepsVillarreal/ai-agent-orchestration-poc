"""The function that decides one turn.

`handle_turn` receives the pending record, the interpreter's output and the turn's own inputs
(`TurnInput`: today the message text) and returns a template reply plus the state change. It
calls no model and runs no tool. The ADK turn node is its only caller: it puts `reply` in the
event's message and `state_delta` in the event's state (D1.1).

Order of a turn (D3, *Compuerta de entrada*):
1. Registry filter (`filter_by_registry`): drop operations of unknown tools and keep only the
   contract keys of each tool, list buckets included, and `depends_on` (D2, D10).
2. D11: two operations of the same tool -> ask which one first; state unchanged.
3. Something pending:
   - *Misma tool*: a `collecting` record, `act` is `request_action` or `provide_info` and every
     operation is of a pending tool (none at all counts too) -> each operation is anchored and
     merged into the step of its tool (D4, D18); `certainty` is ignored (D2). The reply shows
     every change, every note and the fixed rejected-name line, then the question.
   - Anything else -> provisional template, state unchanged (tasks 10a, 12 and 13).
4. Nothing pending -> route by `act`; a request goes to the rules table (R1-R5), which counts
   operations by certainty and never looks at tool names (TASK.md line 562). R2 anchors and
   merges against an empty record (D4 rule 4), so a name not written in the message never
   gets in and the `_add`/`_remove` buckets are applied, never stored (D4 rule 10).

The message text is used only to anchor names (D4 rule 3): it goes neither to the state nor to
the log.
"""

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from core import replies
from core.interpretation import Act, Certainty, TurnInterpretation
from core.merge import Change, MergeResult, Rejected, merge_args
from core.presence import missing_fields
from core.registry import TOOLS, FieldKind, ToolSpec, list_buckets


@dataclass(frozen=True)
class TurnInput:
    """What the core needs from the turn that does not come from the interpreter.

    `message` is the user's text, read only to anchor names (D4 rule 3). Later tasks add
    `today` (09b) and `session_id` (11) here.
    """

    message: str


@dataclass
class TurnOutcome:
    reply: str
    state_delta: dict[str, Any] = field(default_factory=dict)  # empty = state unchanged (D1.1)
    route: str = ""  # which branch decided the turn: "R2", "d11", ... (tests and the log read it)
    changes: list[Change] = field(default_factory=list)  # for the log (D4 rule 8)
    rejected: list[Rejected] = field(default_factory=list)  # for the log (D4 rule 3)


def handle_turn(
    pending: dict | None, interpretation: TurnInterpretation, turn_input: TurnInput
) -> TurnOutcome:
    operations = filter_by_registry(interpretation)
    dropped_some = len(operations) < len(interpretation.operations)

    outcome = _decide(pending, interpretation.act, operations, turn_input.message)

    # D10: tell the user part of the request was ignored, unless nothing was left and nothing
    # is pending (R1 then answers with the capabilities). With something pending and every
    # operation dropped, the line goes before the repeated question (autodecided AB). The line
    # is not part of `last_question`.
    if dropped_some and (operations or pending):
        outcome.reply = f"{replies.UNKNOWN_TOOL_LINE}\n{outcome.reply}"
    return outcome


def _decide(pending: dict | None, act: Act, operations: list[dict], message: str) -> TurnOutcome:
    tool_counts = Counter(operation["tool"] for operation in operations)
    if any(count > 1 for count in tool_counts.values()):
        return TurnOutcome(replies.ONE_PER_TOOL, route="d11")

    if pending:
        # D3 gate, step 3. Only *Misma tool* exists so far.
        if (
            pending.get("status") == "collecting"
            and act in (Act.REQUEST_ACTION, Act.PROVIDE_INFO)
            and all(operation["tool"] in pending["tools"] for operation in operations)
        ):
            return _same_tool(pending, operations, message)
        return TurnOutcome(replies.PENDING_NOT_SUPPORTED_YET, route="pending_provisional")

    # D3 gate, step 4: nothing is pending.
    if act in (Act.CONFIRM, Act.REJECT, Act.CANCEL) or (act == Act.PROVIDE_INFO and not operations):
        return TurnOutcome(replies.NO_PENDING_OPERATION, route="nothing_pending")
    if act in (Act.QUESTION, Act.OTHER):
        return TurnOutcome(replies.capabilities(), route="template")
    # `request_action`, or `provide_info` with operations, which is treated the same.
    return _rules_table(operations, message)


def _rules_table(operations: list[dict], message: str) -> TurnOutcome:
    explicit = [op for op in operations if op["certainty"] == Certainty.EXPLICIT]
    plausible = [op for op in operations if op["certainty"] == Certainty.PLAUSIBLE]

    if not operations:
        return TurnOutcome(replies.capabilities(), route="R1")
    if len(explicit) == 1 and not plausible:
        return _start_flow(explicit[0], message)
    if len(explicit) >= 2 and not plausible:
        route = "R3"
    elif explicit:
        route = "R4"
    else:
        route = "R5"
    return TurnOutcome(replies.ONE_OPERATION_AT_A_TIME, route=route)


def _start_flow(operation: dict, message: str) -> TurnOutcome:
    """R2: anchor and merge against an empty record, then ask for every missing field.

    `args` keeps the user's words unvalidated (D4 rule 7) and only contract fields (D4 rule
    10). A record is always a list of steps: one operation is a one-step plan (D18).
    """
    tool = TOOLS[operation["tool"]]
    merged = merge_args(tool, {}, operation["args"], message)
    steps = [{"tool": tool.name, "args": merged.args}]
    record = {"status": "collecting", "tools": [tool.name], "steps": steps}
    record.update(_missing_and_question(steps))
    return _collecting_outcome(record, [(tool, merged)], route="R2")


def _same_tool(pending: dict, operations: list[dict], message: str) -> TurnOutcome:
    """D3 gate, step 3, *Misma tool*: merge each operation into the step of its tool (D4, D18).

    `certainty` is ignored (D2). No operation, or a merge that changes nothing, leaves the
    state as it is and repeats the pending question (D9 *Caso H*; TASK.md line 447).
    """
    by_tool = {operation["tool"]: operation for operation in operations}
    steps, merges = [], []
    for step in pending["steps"]:
        operation = by_tool.get(step["tool"])
        if operation is None:
            steps.append(step)
            continue
        tool = TOOLS[step["tool"]]
        merged = merge_args(tool, step["args"], operation["args"], message)
        steps.append({**step, "args": merged.args})
        merges.append((tool, merged))

    if not any(merged.changes for _, merged in merges):
        return _collecting_outcome(pending, merges, route="same_tool", changed=False)

    record = {**pending, "steps": steps}  # `status` stays `collecting`
    record.update(_missing_and_question(steps))
    return _collecting_outcome(record, merges, route="same_tool")


def _missing_and_question(steps: list[dict]) -> dict[str, Any]:
    """`missing` (by presence until the validator of task 09a) and `last_question` (D17, D19).

    The question never carries a collected value or a change line (D2, D14).
    """
    missing, questions = [], []
    for step in steps:
        tool = TOOLS[step["tool"]]
        absent = missing_fields(tool, step["args"])
        missing.extend(absent)
        if absent:
            questions.append(replies.missing_question(tool, absent))
    return {"missing": missing, "last_question": " ".join(questions) or None}


def _collecting_outcome(
    record: dict,
    merges: list[tuple[ToolSpec, MergeResult]],
    route: str,
    changed: bool = True,
) -> TurnOutcome:
    """Reply: change and note lines, the rejected-name line, then the question (D4 rule 9).

    Nothing runs here even when every field is present: validation and confirmation come
    first (D7.1).
    """
    lines, changes, rejected = [], [], []
    for tool, merged in merges:
        specs = {spec.name: spec for spec in tool.fields}
        lines += [replies.change_line(specs[change.field], change) for change in merged.changes]
        lines += [replies.note_line(specs[note.field], note) for note in merged.notes]
        changes += merged.changes
        rejected += merged.rejected
    if rejected:
        lines.append(replies.REJECTED_NAME_LINE)
    lines.append(record["last_question"] or replies.READY_NOT_AVAILABLE_YET)

    return TurnOutcome(
        "\n".join(lines),
        state_delta={"pending": record} if changed else {},
        route=route,
        changes=changes,
        rejected=rejected,
    )


def filter_by_registry(interpretation: TurnInterpretation) -> list[dict]:
    """D3 step 1 and D2's contract filter, as plain dicts.

    Operations of a tool not in `TOOLS` are dropped (D10). The rest keep only the keys of
    their tool (its field names and, for a `person_list` field, its `_add`/`_remove` buckets)
    and their `depends_on` (None when the model gave none), which tasks 08b and 14a read.
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
                "depends_on": operation.depends_on,
                "args": {name: value for name, value in args.items() if name in keys},
            }
        )
    return kept


def _contract_keys(tool: ToolSpec) -> set[str]:
    keys = set()
    for spec in tool.fields:
        keys.add(spec.name)
        if spec.kind == FieldKind.PERSON_LIST:
            keys.update(list_buckets(spec))
    return keys
