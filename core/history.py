"""Closed operations (D6): what `session.state["history"]` keeps.

When a pending record closes, it leaves `pending` and its entry is appended to `history`, which
keeps the last `MAX_HISTORY` with their final status, their values and their results. History
is structured state, not conversation (TASK.md line 229): no rule reads it, neither the merge
(D4) nor the gate (D3), and the interpreter never sees it (`pending_summary` reads only
`pending`; D2, D14). Every closing writes its entry with these helpers: today "completed";
"failed", "uncertain", "cancelled" and "abandoned" come with the tasks that close that way.
"""

from collections.abc import Sequence
from typing import Any

MAX_HISTORY = 10
COMPLETED = "completed"


def closed_entry(record: dict, status: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
    """The history entry of a closed record: `{operation_id, status, steps}`.

    `steps` is `[{tool, values, result}]` in step order: `values` are the normalized values the
    tool received (final, unlike the `args` kept in `pending`, D44) and `result` is the dict the
    tool returned, as it returned it.
    """
    return {"operation_id": record["operation_id"], "status": status, "steps": steps}


def appended(history: Sequence[dict], entry: dict) -> list[dict]:
    """A new list with `entry` last, keeping only the newest `MAX_HISTORY`; `history` is not
    changed."""
    return [*history, entry][-MAX_HISTORY:]
