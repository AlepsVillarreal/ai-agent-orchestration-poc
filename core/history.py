"""Closed operations (D6): what `session.state["history"]` keeps.

When a pending record closes, it leaves `pending` and its entry is appended to `history`, which
keeps the last `MAX_HISTORY` with their final status, their values and their results. History
is structured state, not conversation (TASK.md line 229): no rule reads it, neither the merge
(D4) nor the gate (D3), and the interpreter never sees it (`pending_summary` reads only
`pending`; D2, D14). Every closing writes its entry with these helpers: a confirmed record
closes `completed`, `failed` or `uncertain` (D9.2, D9.3); a record closed without running
closes `cancelled` (a "no" or a "cancel", D7.3 and D3 rule 1) or `abandoned` (a change of
intention, D3.1).
A closing without running keeps the steps as they were in `pending`, `{tool, args}`: the user's
words after the merge, neither normalized nor validated, and no `outcome`, `values` or
`result`, because no tool received anything.
"""

from collections.abc import Sequence
from typing import Any

MAX_HISTORY = 10

# Final status of a record, and the outcome of each step that was tried (D6, D9; D59).
# `failed` and `uncertain` are both: the step that stopped the record names its status.
COMPLETED = "completed"
FAILED = "failed"  # the tool answered `success: false`; its key was released (D9.3)
UNCERTAIN = "uncertain"  # it may or may not have happened; never run again (D9.2)
RAN = "ran"  # the tool ran now and answered a success with its ID
REUSED = "reused"  # the same execution was already done: the stored result, nothing ran (D9.1)
DONE_OUTCOMES = (RAN, REUSED)

# Final status of a record closed without running anything (D6, D7.3, D3.1).
CANCELLED = "cancelled"  # a "no" without arguments, or a "cancel" (also to a clarification)
ABANDONED = "abandoned"  # a change of intention: the turn asked for a tool that was not pending


def step_entry(
    tool: str, values: dict[str, Any], outcome: str, result: Any, error: str | None = None
) -> dict[str, Any]:
    """One tried step: `{tool, values, outcome, result}`, plus `error` after an exception.

    `values` are the normalized values the tool received (final, unlike the `args` kept in
    `pending`, D44). `result` is the dict the tool returned (the stored one when `reused`), or
    None when there is none. `error` is the exception's type name, never its text.
    """
    entry = {"tool": tool, "values": values, "outcome": outcome, "result": result}
    if error is not None:
        entry["error"] = error
    return entry


def closed_status(outcomes: Sequence[str]) -> str:
    """`completed` if every step ran or was reused, else the outcome of the step that stopped
    the record (only the steps tried are listed, so it is the last one)."""
    return next((o for o in outcomes if o not in DONE_OUTCOMES), COMPLETED)


def closed_entry(record: dict, status: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
    """The history entry of a closed record: `{operation_id, status, steps}`.

    After a "yes", `steps` lists the steps that were tried, in step order, as `step_entry`
    writes them. Closed without running (`cancelled`, `abandoned`), it is the record's own
    `steps`, `{tool, args}`; a clarification, which has no steps, keeps its candidates in their
    order as `{tool, args}`, steps never started (without `depends_on`, not an argument).
    """
    return {"operation_id": record["operation_id"], "status": status, "steps": steps}


def appended(history: Sequence[dict], entry: dict) -> list[dict]:
    """A new list with `entry` last, keeping only the newest `MAX_HISTORY`; `history` is not
    changed."""
    return [*history, entry][-MAX_HISTORY:]
