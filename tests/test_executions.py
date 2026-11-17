"""The execution registry of D9 without Redis: the key and the in-memory variant (brief 11)."""

import json
from datetime import datetime

import pytest

from core.executions import InMemoryExecutions, decoded, execution_key

VALUES = {"title": "Servidor caído", "priority": "high", "assignee": "Carlos"}


# 1 (D9.1)
def test_execution_key_binds_session_tool_and_values():
    key = execution_key("s-1", "create_ticket", VALUES)

    reordered = dict(reversed(list(VALUES.items())))
    assert execution_key("s-1", "create_ticket", reordered) == key
    assert execution_key("s-2", "create_ticket", VALUES) != key
    assert execution_key("s-1", "send_notification", VALUES) != key
    assert execution_key("s-1", "create_ticket", {**VALUES, "priority": "low"}) != key
    for value in ("s-1", "create_ticket", *VALUES.values()):
        assert value not in key  # a hash: no value in clear
    assert len(key) == 64 and int(key, 16) >= 0


# 2
def test_in_memory_reserve_complete_release():
    executions = InMemoryExecutions()
    result = {"success": True, "ticket_id": "TCK-1", "message": "listo", "extra": [1, "dos"]}

    assert executions.reserve("k") is None
    assert executions.reserve("k") == {"status": "started"}

    executions.complete("k", result)
    stored = executions.reserve("k")
    assert stored == {"status": "done", "result": json.loads(json.dumps(result))}
    stored["result"]["ticket_id"] = "CHANGED"  # a copy: what is kept does not change
    assert executions.reserve("k")["result"]["ticket_id"] == "TCK-1"

    executions.release("k")
    assert executions.reserve("k") is None

    # D50 *Pendiente*: a result that is not JSON fails here, as it would in Redis.
    with pytest.raises(TypeError):
        executions.complete("k", {"success": True, "ticket_id": "TCK-2", "at": datetime.now()})


def test_complete_never_brings_back_a_released_key():
    # As `SET XX` in Redis: only a reserved key becomes `done`.
    executions = InMemoryExecutions()

    executions.complete("k", {"success": True, "ticket_id": "TCK-1"})

    assert executions.snapshot() == {}


@pytest.mark.parametrize("raw", ["no es JSON", "[1, 2]", '{"status": "otro"}', ""])
def test_an_unreadable_record_counts_as_started(raw):
    # Decision D: never run again what cannot be read (D9.2).
    assert decoded(raw) == {"status": "started"}
    assert decoded(None) is None
