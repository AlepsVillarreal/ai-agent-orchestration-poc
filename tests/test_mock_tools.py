import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from core import mock_tools
from core.registry import TOOLS

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    ("tool_name", "prefix"),
    [("create_ticket", "TCK-"), ("schedule_meeting", "MTG-"), ("send_notification", "NTF-")],
)
def test_each_mock_returns_success_id_and_message(tool_name, prefix, canonical_args):
    tool = TOOLS[tool_name]
    result = tool.execute(canonical_args[tool_name])

    assert set(result) == {"success", tool.result_id_field, "message"}
    assert result["success"] is True
    result_id = result[tool.result_id_field]
    assert isinstance(result_id, str)
    assert result_id.startswith(prefix)
    assert len(result_id) > len(prefix)
    assert result["message"].strip()


def test_mock_ids_are_unique_across_calls_and_processes(canonical_args):
    args = canonical_args["create_ticket"]
    ids = {mock_tools.create_ticket(args)["ticket_id"] for _ in range(100)}
    assert len(ids) == 100

    # The first call of two separate processes: a counter restarting at 1 would repeat.
    code = f"from core.mock_tools import create_ticket; print(create_ticket({args!r})['ticket_id'])"
    first_ids = [
        subprocess.run(
            [sys.executable, "-c", code],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        for _ in range(2)
    ]
    assert all(first_ids)
    assert first_ids[0] != first_ids[1]


def test_mock_rejects_args_that_break_the_contract(canonical_args):
    with pytest.raises(ValidationError):
        mock_tools.create_ticket({**canonical_args["create_ticket"], "priority": "urgente"})


def test_registry_maps_each_tool_to_its_mock(canonical_args):
    expected_mocks = {
        "create_ticket": mock_tools.create_ticket,
        "schedule_meeting": mock_tools.schedule_meeting,
        "send_notification": mock_tools.send_notification,
    }
    for name, tool in TOOLS.items():
        assert tool.execute is expected_mocks[name]
        result = tool.execute(canonical_args[name])
        assert result[tool.result_id_field]
