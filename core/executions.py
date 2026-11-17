"""The execution registry (D9.1-D9.3): what keeps a confirmed step from running twice.

Before a tool runs, the core reserves an idempotency key, the hash of session + tool +
normalized values (`execution_key`), as `started`; after it, the key holds `done` with the
result, or is released when the tool answered a failure. The registry lives outside
`session.state` because the state is written only by the turn's final event (D1.1): a crash
between the tool call and that event would lose it.

The registry itself (the `Executions` protocol and its two variants) is "dumb": it stores and
returns records and has no rule. The rules are the core's (D46), in pure Python and tested
without Redis: `core/turn.py` reserves the key, calls the tool and stops at the first step not
done; this module's `settled` and `from_stored` read the answer (D9.1-D9.3, D60), store
`done`, release the key after a failure or leave it `started`. `InMemoryExecutions` serves
tests and `adk web`; the CLI passes the Redis variant (`assistant/executions.py`).
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol

from core.history import FAILED, RAN, REUSED, UNCERTAIN, step_entry

STARTED = "started"
DONE = "done"
TTL_SECONDS = 24 * 60 * 60  # D9.1: a key lives 24 h (Redis; the in-memory registry never expires)


class Executions(Protocol):
    def reserve(self, key: str) -> dict[str, Any] | None:
        """Mark `key` as `started` if it is new and return None; else return what it holds,
        `{"status": "started"}` or `{"status": "done", "result": {...}}`, and change nothing."""

    def complete(self, key: str, result: dict[str, Any]) -> None:
        """Store `done` with `result` under a reserved key (JSON; it fails if it is not JSON)."""

    def release(self, key: str) -> None:
        """Forget `key`, so the same execution runs again next time (D9.3)."""


def execution_key(session_id: str, tool: str, normalized: dict[str, Any]) -> str:
    """D9.1: sha256 of the canonical JSON of `[session_id, tool, normalized]`.

    `normalized` are the values the tool receives (D46, D62). They equal the values the
    confirmation hash covers (D7.2), except in the receiving step of a plan, whose values also
    carry its producer's ID, which the hash never covers (D8.2; the author's answer A to brief
    14b, option (b)). A hash, so no user value is readable in the key or in the log line that
    shows it.
    """
    canonical = json.dumps([session_id, tool, normalized], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def encoded_started() -> str:
    return json.dumps({"status": STARTED})


def encoded_done(result: dict[str, Any]) -> str:
    return json.dumps({"status": DONE, "result": result}, ensure_ascii=False)


def decoded(raw: str | bytes | None) -> dict[str, Any] | None:
    """A stored record; an unreadable value counts as `started` (never run again, D9.2)."""
    if raw is None:
        return None
    try:
        record = json.loads(raw)
    except ValueError:
        return {"status": STARTED}
    if not isinstance(record, dict) or record.get("status") not in (STARTED, DONE):
        return {"status": STARTED}
    return record


class InMemoryExecutions:
    """A registry in a dict, for tests and `adk web`. It never expires: it lives as long as the
    process. Records are kept as JSON, so what it returns is a copy and a result that is not
    JSON fails here as it would in Redis."""

    def __init__(self) -> None:
        self._records: dict[str, str] = {}

    def reserve(self, key: str) -> dict[str, Any] | None:
        if key in self._records:
            return decoded(self._records[key])
        self._records[key] = encoded_started()
        return None

    def complete(self, key: str, result: dict[str, Any]) -> None:
        encoded = encoded_done(result)
        if key in self._records:  # as `SET XX`: a released key is not brought back
            self._records[key] = encoded

    def release(self, key: str) -> None:
        self._records.pop(key, None)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Every key and its record, as copies (for tests: `reserve` would change a new key)."""
        return {key: decoded(raw) for key, raw in self._records.items()}


@dataclass(frozen=True)
class StepRun:
    """One step tried after a "yes": what the reply, `history` and the log need."""

    tool: str
    values: dict[str, Any]
    key: str
    outcome: str  # `ran`, `reused`, `failed` or `uncertain` (core/history.py)
    result: Any = None  # the tool's dict, or None when there is none
    result_id: str | None = None  # read once from the registry's ID field, only when done
    error: str | None = None  # the exception's type name, when the tool raised

    def entry(self) -> dict[str, Any]:
        return step_entry(self.tool, self.values, self.outcome, self.result, self.error)


def classified(result: Any, id_field: str) -> tuple[str, str | None]:
    """The outcome of a tool's answer and its ID, read once (D9.2, D9.3; D60).

    Only `success` exactly True with a non-empty ID is `ran`; only `success` exactly False is
    `failed`; anything else (no `success`, no ID or an empty one, not a dict, or a dict that is
    not JSON and so could be neither stored nor kept in `history`) is `uncertain`. The ID is
    returned only with `ran`, as the tool gave it, in text.
    """
    if not isinstance(result, dict) or not _is_json(result):
        return UNCERTAIN, None
    if result.get("success") is False:
        return FAILED, None
    result_id = result.get(id_field)
    if result.get("success") is True and result_id is not None and str(result_id).strip():
        return RAN, str(result_id)
    return UNCERTAIN, None


def settled(
    executions: Executions, tool: str, id_field: str, key: str, values: dict, result: Any
) -> StepRun:
    """After the tool answered: `done` with the result, the key released after a failure
    (D9.3), or the key left `started` when the answer cannot be read (D9.2). A result that is
    not JSON is kept as None, so the state the turn writes stays JSON."""
    outcome, result_id = classified(result, id_field)
    if outcome == RAN:
        executions.complete(key, result)
    elif outcome == FAILED:
        executions.release(key)
    kept = result if isinstance(result, dict) and _is_json(result) else None
    return StepRun(tool, values, key, outcome, kept, result_id)


def from_stored(tool: str, id_field: str, key: str, values: dict, stored: dict) -> StepRun:
    """The key was already there: `done` gives its stored result without running (D9.1);
    `started` (or a `done` whose result cannot be read) is uncertain and never runs (D9.2)."""
    result = stored.get("result") if stored.get("status") == DONE else None
    outcome, result_id = classified(result, id_field)
    if outcome == RAN:
        return StepRun(tool, values, key, REUSED, result, result_id)
    return StepRun(tool, values, key, UNCERTAIN)


def _is_json(value: Any) -> bool:
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return False
    return True
