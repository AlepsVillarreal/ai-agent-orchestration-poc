"""The function that decides one turn.

`handle_turn` receives the pending record, the interpreter's output, the turn's own inputs
(`TurnInput`: the message text, the moment of the turn and the session), the closed operations
(`history`, D6, read by no rule) and the execution registry (`executions`, D9), and returns a
template reply plus the state change. It calls no model. The ADK turn node is its only caller:
it puts `reply` in the event's message and `state_delta` in the event's state (D1.1).
`_run_confirmed` is the only place in the code that runs a tool (D46).

Order of a turn (D3, *Compuerta de entrada*):
1. Registry filter (`filter_by_registry`): drop operations of unknown tools and keep only the
   contract keys of each tool, list buckets included, and `depends_on` (D2, D10).
2. D11: two operations of the same tool -> ask which one first; state unchanged. Exception
   (D11.1, D35): in the *Misma tool* branch below, the operations of one pending tool whose
   args share no key (each `_add`/`_remove` bucket is its own key; absent values do not count)
   and that bring the same `depends_on` are first joined into one, which then goes through
   the merge like any single operation. Nothing pending, another tool in the turn or `confirm`:
   D11 as written, so outside *Misma tool* two requested tickets never become one (inside it,
   two tickets with disjoint keys join into the pending one, D54).
3. Something pending, in `collecting` or `awaiting_confirmation` (`awaiting_clarification` keeps
   a provisional template until task 13). S = tools with `explicit` operations. In this order:
   - `confirm` -> `_confirm`, in this order: (1) an operation of another tool, or something
     the filter dropped -> repeat what is pending (author's answer to brief 10a, option (a));
     (2) `confirms_tool` naming a tool that is not pending -> the same (D7.4); (3) operations of
     the pending tool go through the merge first, and a change, a rejected name or a note never
     runs (what D4 requires of D7); (4) still `collecting` -> repeat the question (D7.1);
     (5) the hash of the shown values must match (D7.2), else show them again with the new
     hash, after a line that says the values changed (author's answer on note (ddd));
     (6) `_run_confirmed` runs each step under its idempotency key (D9.1-D9.3), stops at the
     first step that does not end done, empties `pending`, answers with one line per step
     tried and appends the record to `history` as completed, failed or uncertain.
   - *Otra tool* (D3.1): S has a tool that is not pending and `act` is `request_action`,
     `provide_info`, `reject` or `cancel` -> the record goes to `history` as `abandoned` and the
     rules table runs on every operation of the turn against nothing stored, after the discard
     line (author's answer B to brief 12). A `confirm` with another tool is D45, not this.
   - *Misma tool*: `act` is `request_action` or `provide_info` and every operation is of a
     pending tool (none at all counts too) -> each operation is anchored and merged into the
     step of its tool (D4, D18); `certainty` is ignored (D2). The reply shows every change,
     every note and the fixed rejected-name line, then the question or the confirmation.
   - *Solo `plausible` de otra tool* (`request_action`/`provide_info` left) -> nothing of the
     message is applied, not even data of the pending tool; what is pending is repeated (D3).
   - `reject` with an argument of a pending tool -> a correction merged like *Misma tool*
     (D7.3); `reject` without one, or `cancel` -> `pending` emptied, the record to `history`
     as `cancelled` and the discard line as the reply.
   - `question`/`other` -> what the agent can do, without its question, then what is pending.
   A turn that repeats what is pending (a merge without changes, (1), (2), (4), *Solo
   `plausible`*, `question`/`other`) assesses it again against `now` and writes it only if its
   validity changed with time (D9 *Caso H*, D40, D57). Only `confirm` runs anything.
4. Nothing pending -> route by `act`; a request goes to the rules table (R1-R5), which counts
   operations by certainty and never looks at tool names (TASK.md line 562). R2 anchors and
   merges against an empty record (D4 rule 4), so a name not written in the message never
   gets in and the `_add`/`_remove` buckets are applied, never stored (D4 rule 10). Every
   record R2 creates gets an `operation_id` (a uuid, read by the log).
5. After every merge (R2 and *Misma tool*, `confirm` included) each step is validated
   (`core/validation.py`, D5):
   - something missing or invalid -> `status: collecting`; `missing` and `invalid`
     (`{tool, field, cause}`, never a value) come from the validator. One reply names the
     invalid fields (with their allowed values) after the change lines, then asks once for
     every missing or invalid field (`last_question`, D17: labels only).
   - every step valid -> `status: awaiting_confirmation` with the hash of the shown values
     (`confirmation_hash`, D7.2). The reply shows the normalized values of each step and asks
     for a yes; `last_question` keeps only that question, without values (D43). Nothing runs
     in the turn a record becomes complete (D7.1).
   Dates and times are resolved against `TurnInput.now` (D5.1, D5.2), so the shown values
   (the ISO date of "mañana") and their hash depend on the moment of the turn.

The message text is used only to anchor names (D4 rule 3): it goes neither to the state nor to
the log. The core never reads the clock: `now` comes from the turn node. The normalized values
are recomputed every time, never stored (D44).
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

from core import replies
from core.confirmation import confirmation_hash, shown_values
from core.executions import (
    Executions,
    InMemoryExecutions,
    StepRun,
    execution_key,
    from_stored,
    settled,
)
from core.history import (
    ABANDONED,
    CANCELLED,
    DONE_OUTCOMES,
    UNCERTAIN,
    appended,
    closed_entry,
    closed_status,
)
from core.interpretation import Act, Certainty, TurnInterpretation
from core.merge import Change, MergeResult, Rejected, merge_args
from core.presence import drop_absent
from core.registry import TOOLS, FieldKind, ToolSpec, list_buckets
from core.validation import validate

COLLECTING = "collecting"
AWAITING_CONFIRMATION = "awaiting_confirmation"


@dataclass(frozen=True)
class TurnInput:
    """What the core needs from the turn that does not come from the interpreter.

    `message` is the user's text, read only to anchor names (D4 rule 3). `now` is the moment of
    the turn, local and naive: dates and times are validated against it (D5.1, D5.2). It has no
    default, so every caller, tests included, says which moment it validates against; only the
    turn node reads the clock. `session_id` goes into every idempotency key (D9.1); it has no
    default either, so two sessions never share keys by a forgotten argument.
    """

    message: str
    now: datetime
    session_id: str


@dataclass
class TurnOutcome:
    reply: str
    state_delta: dict[str, Any] = field(default_factory=dict)  # empty = state unchanged (D1.1)
    route: str = ""  # which branch decided the turn: "R2", "confirmed", ... (tests and the log)
    changes: list[Change] = field(default_factory=list)  # for the log (D4 rule 8)
    rejected: list[Rejected] = field(default_factory=list)  # for the log (D4 rule 3)
    invalid: list[dict] = field(default_factory=list)  # the record's `invalid`, for the log (D5)
    results: list[StepRun] = field(default_factory=list)  # per step tried after a "yes" (D9)


def handle_turn(
    pending: dict | None,
    interpretation: TurnInterpretation,
    turn_input: TurnInput,
    history: Sequence[dict] = (),
    executions: Executions | None = None,  # None: a new in-memory registry for this call only
) -> TurnOutcome:
    operations = filter_by_registry(interpretation)
    dropped_some = len(operations) < len(interpretation.operations)

    outcome = _decide(
        pending,
        interpretation.act,
        operations,
        turn_input,
        confirms_tool=interpretation.confirms_tool,
        dropped_some=dropped_some,
        history=history,
        executions=executions if executions is not None else InMemoryExecutions(),
    )

    # D10: tell the user part of the request was ignored, unless nothing was left and nothing
    # is pending (R1 then answers with the capabilities). With something pending the line goes
    # before whatever is repeated or shown: the question or the confirmation (D41). The line is
    # not part of `last_question`.
    if dropped_some and (operations or pending):
        outcome.reply = f"{replies.UNKNOWN_TOOL_LINE}\n{outcome.reply}"
    return outcome


def _decide(
    pending: dict | None,
    act: Act,
    operations: list[dict],
    turn_input: TurnInput,
    confirms_tool: str | None,
    dropped_some: bool,
    history: Sequence[dict],
    executions: Executions,
) -> TurnOutcome:
    same_tool = _is_same_tool(pending, act, operations)
    if same_tool:
        operations = _join_split_operations(operations)  # D11.1

    tool_counts = Counter(operation["tool"] for operation in operations)
    if any(count > 1 for count in tool_counts.values()):
        return TurnOutcome(replies.ONE_PER_TOOL, route="d11")

    if pending:
        if pending.get("status") not in (COLLECTING, AWAITING_CONFIRMATION):  # D3 step 2
            return TurnOutcome(replies.PENDING_NOT_SUPPORTED_YET, route="pending_provisional")
        if act == Act.CONFIRM:
            outcome = _confirm(pending, operations, turn_input, confirms_tool, dropped_some)
            return outcome or _run_confirmed(pending, turn_input, history, executions)
        return _pending_gate(pending, act, operations, turn_input, history, same_tool)

    # D3 gate, step 4: nothing is pending.
    if act in (Act.CONFIRM, Act.REJECT, Act.CANCEL) or (act == Act.PROVIDE_INFO and not operations):
        return TurnOutcome(replies.NO_PENDING_OPERATION, route="nothing_pending")
    if act in (Act.QUESTION, Act.OTHER):
        return TurnOutcome(replies.capabilities(), route="template")
    # `request_action`, or `provide_info` with operations, which is treated the same.
    return _rules_table(operations, turn_input)


def _pending_gate(
    pending: dict,
    act: Act,
    operations: list[dict],
    turn_input: TurnInput,
    history: Sequence[dict],
    same_tool: bool,
) -> TurnOutcome:
    """D3 gate, step 3, for every `act` but `confirm`, in the order of the module docstring.

    It decides by counts and by the names of the pending tools, never by the message text.
    """
    now = turn_input.now
    other_tool = any(
        op["certainty"] == Certainty.EXPLICIT and op["tool"] not in pending["tools"]
        for op in operations
    )
    if other_tool and act in (Act.REQUEST_ACTION, Act.PROVIDE_INFO, Act.REJECT, Act.CANCEL):
        outcome = _rules_table(operations, turn_input)  # D3.1: nothing stored is inherited
        outcome.route = f"other_tool:{outcome.route}"
        outcome.reply = f"{_discarded_line(pending)}\n{outcome.reply}"
        # R2 writes the new record; R3-R5 create nothing, so `pending` is emptied.
        closed = _closed_without_running(pending, ABANDONED, history)
        outcome.state_delta = {**closed, **outcome.state_delta}
        return outcome
    if same_tool:
        steps, merges = _merge_steps(pending, operations, turn_input.message)
        return _after_merge(pending, steps, merges, now, route="same_tool")
    if act in (Act.REQUEST_ACTION, Act.PROVIDE_INFO):  # only `plausible` of another tool
        return _repeat_pending(pending, [], now, route="plausible_other_tool")
    if act == Act.REJECT and any(
        op["tool"] in pending["tools"] and drop_absent(op["args"]) for op in operations
    ):  # decision F: a "no" with arguments of a pending tool is a correction (D7.3)
        steps, merges = _merge_steps(pending, operations, turn_input.message)
        return _after_merge(pending, steps, merges, now, route="reject_merged")
    if act in (Act.REJECT, Act.CANCEL):
        closed = _closed_without_running(pending, CANCELLED, history)
        return TurnOutcome(_discarded_line(pending), state_delta=closed, route="cancelled")
    # `question` or `other`: answer what the agent can do, then repeat what is pending.
    outcome = _repeat_pending(pending, [], now, route="pending_template")
    outcome.reply = f"{replies.capabilities(ask=False)}\n{outcome.reply}"
    return outcome


def _closed_without_running(pending: dict, status: str, history: Sequence[dict]) -> dict:
    """The state delta of a record closed without running: `pending` emptied and the record in
    `history` with its steps as they were (D6; decision A of brief 12)."""
    entry = closed_entry(pending, status, pending["steps"])
    return {"pending": None, "history": appended(history, entry)}


def _discarded_line(pending: dict) -> str:
    return replies.discarded_line([TOOLS[name] for name in pending["tools"]])


def _is_same_tool(pending: dict | None, act: Act, operations: list[dict]) -> bool:
    """D3 gate, step 3, *Misma tool*: a `collecting` or `awaiting_confirmation` record, `act`
    `request_action` or `provide_info`, and every operation of a pending tool (none counts too).

    A clarification (`awaiting_clarification`, task 13) is not *Misma tool*, so its candidates
    are not pending tools here (D11.1, pending (c)).
    """
    return (
        bool(pending)
        and pending.get("status") in (COLLECTING, AWAITING_CONFIRMATION)
        and act in (Act.REQUEST_ACTION, Act.PROVIDE_INFO)
        and all(operation["tool"] in pending["tools"] for operation in operations)
    )


def _join_split_operations(operations: list[dict]) -> list[dict]:
    """D11.1: join the operations of one tool into one when their args share no key.

    Keys are counted after the registry filter and without absent values (D4 rule 2), so a
    `""` in one operation never overwrites a value of another. Each `_add`/`_remove` bucket is
    its own key. The whole group of a tool is joined, or none of it: a repeated key or a
    different `depends_on` (None included, D35) leaves the group as it came, and D11 asks.
    The joined operation keeps the first one's `certainty`, which *Misma tool* ignores (D2).
    """
    result = []
    for tool in dict.fromkeys(operation["tool"] for operation in operations):
        group = [operation for operation in operations if operation["tool"] == tool]
        args_list = [drop_absent(operation["args"]) for operation in group]
        keys = [key for args in args_list for key in args]
        same_dependency = len({operation["depends_on"] for operation in group}) == 1
        if len(group) == 1 or len(set(keys)) < len(keys) or not same_dependency:
            result += group
            continue
        joined_args = {key: value for args in args_list for key, value in args.items()}
        result.append({**group[0], "args": joined_args})
    return result


def _rules_table(operations: list[dict], turn_input: TurnInput) -> TurnOutcome:
    explicit = [op for op in operations if op["certainty"] == Certainty.EXPLICIT]
    plausible = [op for op in operations if op["certainty"] == Certainty.PLAUSIBLE]

    if not operations:
        return TurnOutcome(replies.capabilities(), route="R1")
    if len(explicit) == 1 and not plausible:
        return _start_flow(explicit[0], turn_input)
    if len(explicit) >= 2 and not plausible:
        route = "R3"
    elif explicit:
        route = "R4"
    else:
        route = "R5"
    return TurnOutcome(replies.ONE_OPERATION_AT_A_TIME, route=route)


def _start_flow(operation: dict, turn_input: TurnInput) -> TurnOutcome:
    """R2: anchor and merge against an empty record, then ask for what is missing or confirm.

    `args` keeps the user's words unvalidated (D4 rule 7) and only contract fields (D4 rule
    10). A record is always a list of steps: one operation is a one-step plan (D18). Its
    `operation_id` is born here and the log reads it.
    """
    tool = TOOLS[operation["tool"]]
    merged = merge_args(tool, {}, operation["args"], turn_input.message)
    steps = [{"tool": tool.name, "args": merged.args}]
    record = {"operation_id": uuid4().hex, "tools": [tool.name], "steps": steps}
    record.update(_assessment(steps, turn_input.now))
    return _pending_outcome(record, [(tool, merged)], turn_input.now, route="R2")


def _merge_steps(
    pending: dict, operations: list[dict], message: str
) -> tuple[list[dict], list[tuple[ToolSpec, MergeResult]]]:
    """Merge each operation into the step of its tool (D4, D18); `certainty` is ignored (D2).

    Returns the new steps and one merge result per operation. Nothing is validated here.
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
    return steps, merges


def _after_merge(
    pending: dict,
    steps: list[dict],
    merges: list[tuple[ToolSpec, MergeResult]],
    now: datetime,
    route: str,
) -> TurnOutcome:
    """Validate again after a merge (D5) and pick the status.

    No change (no operation, a repeated request, only a note or a rejected name) leaves the
    state as it is and repeats the pending question or confirmation after those lines (D9
    *Caso H*, D40, D42; TASK.md line 447), unless the stored steps assessed against `now` give
    another result than the stored one: "hoy a las 10" confirmed at 09:00 and repeated at 10:30
    (as D47 does with a "yes"). Then the reassessed record is written.
    A change that leaves a step incomplete or invalid sends the record back to `collecting`
    and drops its `confirmation_hash` (D7.1).
    """
    if not any(merged.changes for _, merged in merges):
        return _repeat_pending(pending, merges, now, route)

    return _pending_outcome(_reassessed(pending, steps, now), merges, now, route=route)


def _repeat_pending(
    pending: dict, merges: list[tuple[ToolSpec, MergeResult]], now: datetime, route: str
) -> TurnOutcome:
    """Repeat what is pending, after the lines of `merges`, with nothing of the message applied.

    The stored steps are assessed again against `now` (D57): the record is
    written only if the result differs from the stored one (a time that passed while it
    waited), else the state is unchanged (D40, D42). Nothing runs.
    """
    record = _reassessed(pending, pending["steps"], now)
    return _pending_outcome(record, merges, now, route=route, changed=record != pending)


def _reassessed(pending: dict, steps: list[dict], now: datetime) -> dict:
    """`pending` with `steps` and their assessment against `now`; the old hash never stays."""
    record = {key: value for key, value in pending.items() if key != "confirmation_hash"}
    record["steps"] = steps
    record.update(_assessment(steps, now))
    return record


def _confirm(
    pending: dict,
    operations: list[dict],
    turn_input: TurnInput,
    confirms_tool: str | None,
    dropped_some: bool,
) -> TurnOutcome | None:
    """A "yes" with something pending (D7). None, only at the end of this order, means "run".

    1. Another tool, or something the filter dropped: the "yes" no longer answers only what was
       shown. Nothing runs and nothing of the message is applied; the pending question or
       confirmation is repeated, after the D10 line if something was dropped (author's answer
       to brief 10a, option (a)).
    2. D7.4: `confirms_tool` naming a tool that is not pending vetoes, before the merge ("ni
       cambia nada"). None does not block (D2).
       In 1 and 2 the record is assessed again against `now` as in a turn without changes
       (`_repeat_pending`): only the passing of time can change it (autodecided AT).
    3. Operations of the pending tool are merged first. A change, a rejected name or a note
       never runs: the lines are shown and the record is validated again (D4 to D7; D38).
    4. Still `collecting`: a "yes" while something is missing or invalid repeats the invalid
       lines and the question (D7.1), assessed again against `now` like 1 and 2 (D57).
    5. D7.2: the steps are validated again against `now` and the hash of the values shown now
       must match the stored one. Otherwise the record is assessed again, as after a change:
       still valid -> the fixed line that says the values changed, then the confirmation with
       the current values and the new hash (author's answer on note (ddd)); a step that
       stopped being complete or valid -> back to `collecting` with its invalid lines and
       question (D7.1; review 10a-1, should-fix 1).
    6. None: the caller runs `_run_confirmed`.
    """
    now = turn_input.now
    if dropped_some or any(operation["tool"] not in pending["tools"] for operation in operations):
        return _repeat_pending(pending, [], now, route="confirm_other_tool")
    if confirms_tool is not None and confirms_tool not in pending["tools"]:
        return _repeat_pending(pending, [], now, route="confirm_vetoed")

    steps, merges = _merge_steps(pending, operations, turn_input.message)
    if any(merged.changes or merged.rejected or merged.notes for _, merged in merges):
        return _after_merge(pending, steps, merges, now, route="confirm_merged")

    if pending["status"] != AWAITING_CONFIRMATION:
        return _repeat_pending(pending, [], now, route="confirm_incomplete")

    record = _reassessed(pending, pending["steps"], now)
    if record["status"] != AWAITING_CONFIRMATION:
        return _pending_outcome(record, [], now, route="confirm_hash_changed")
    if record["confirmation_hash"] != pending.get("confirmation_hash"):
        outcome = _pending_outcome(record, [], now, route="confirm_hash_changed")
        outcome.reply = f"{replies.DATA_CHANGED_LINE}\n{outcome.reply}"
        return outcome
    return None


def _run_confirmed(
    record: dict, turn_input: TurnInput, history: Sequence[dict], executions: Executions
) -> TurnOutcome:
    """The only place in the code that runs a tool (D46), each step under its idempotency key:
    session + tool + the normalized values confirmed (D9.1). A key already `done` gives its result
    without running; one `started` is uncertain, never run again (D9.2). After the call,
    `core/executions.py` stores `done`, releases the key after a failure (D9.3) or leaves it
    `started`. The first step not done stops the rest (D8.1, TASK.md line 270). `pending` is
    emptied (a repeated "yes" finds nothing, TASK.md line 284); the record goes to `history` (D6).
    """
    runs = []
    for step in shown_values(record["steps"], turn_input.now):
        tool, values = TOOLS[step["tool"]], step["normalized"]
        key = execution_key(turn_input.session_id, tool.name, values)
        stored = executions.reserve(key)
        if stored is not None:
            run = from_stored(tool.name, tool.result_id_field, key, values, stored)
        else:
            try:
                result = tool.execute(values)
            except Exception as ex:  # it may have happened: uncertain, never retried (D9.2)
                run = StepRun(tool.name, values, key, UNCERTAIN, error=type(ex).__name__)
            else:
                run = settled(executions, tool.name, tool.result_id_field, key, values, result)
        runs.append(run)
        if run.outcome not in DONE_OUTCOMES:
            break
    status = closed_status([run.outcome for run in runs])
    entry = closed_entry(record, status, [run.entry() for run in runs])
    return TurnOutcome(
        "\n".join(replies.step_line(TOOLS[r.tool], r.outcome, r.result_id) for r in runs),
        state_delta={"pending": None, "history": appended(history, entry)},
        route="confirmed",
        results=runs,
    )


def _assessment(steps: list[dict], now: datetime) -> dict[str, Any]:
    """`status`, `missing`, `invalid`, `last_question` and maybe `confirmation_hash` (D5, D7).

    `missing` is what is absent, plus a date or a time without a part the code must not
    complete (D5.1, D5.2); `invalid` lists `{tool, field, cause}` without the value. While
    something is missing or invalid, the question names every missing and invalid field of each
    step by its label, in registry order, and never carries a collected or an allowed value
    (D2, D14, D17). When every step is valid, the record awaits confirmation: the question names
    the actions only (D43) and the hash binds the values shown (D7.2), resolved against `now`.
    """
    missing, invalid, questions, ready = [], [], [], True
    for step in steps:
        tool = TOOLS[step["tool"]]
        validation = validate(tool, step["args"], now)
        ready = ready and validation.ready
        missing += validation.missing
        invalid += [
            {"tool": tool.name, "field": item.field, "cause": item.cause}
            for item in validation.invalid
        ]
        to_ask = validation.missing + [item.field for item in validation.invalid]
        if to_ask:
            questions.append(replies.missing_question(tool, to_ask))

    if ready:
        return {
            "status": AWAITING_CONFIRMATION,
            "missing": [],
            "invalid": [],
            "last_question": replies.confirmation_question([TOOLS[s["tool"]] for s in steps]),
            "confirmation_hash": confirmation_hash(shown_values(steps, now)),
        }
    return {
        "status": COLLECTING,
        "missing": missing,
        "invalid": invalid,
        "last_question": " ".join(questions),  # never empty: something is missing or invalid
    }


def _closing_line(record: dict, now: datetime) -> str:
    """The confirmation (values shown, then the question) or the question.

    The shown values are recomputed from `args` against `now`, never stored (D44).
    """
    if record["status"] == AWAITING_CONFIRMATION:
        summary = replies.confirmation_summary(shown_values(record["steps"], now))
        return f"{summary}\n{record['last_question']}"
    return record["last_question"]


def _pending_outcome(
    record: dict,
    merges: list[tuple[ToolSpec, MergeResult]],
    now: datetime,
    route: str,
    changed: bool = True,
) -> TurnOutcome:
    """Reply: change and note lines, the rejected-name line, the invalid lines, then the
    question or the confirmation (D4 rule 9, D39, D5, D7.1).

    The invalid lines come from the record, so a turn that changes nothing repeats the stored
    ones. Nothing runs here, even when every field is valid: that takes a "yes" (D7.1).
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
    invalid = record.get("invalid", [])
    for item in invalid:
        specs = {spec.name: spec for spec in TOOLS[item["tool"]].fields}
        lines.append(replies.invalid_line(specs[item["field"]], item["cause"]))
    lines.append(_closing_line(record, now))

    return TurnOutcome(
        "\n".join(lines),
        state_delta={"pending": record} if changed else {},
        route=route,
        changes=changes,
        rejected=rejected,
        invalid=invalid,
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
