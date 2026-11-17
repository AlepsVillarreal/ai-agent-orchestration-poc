"""The judge of task 10c (`tests/case_c_judge.py`) on fixed and saved outputs: no model call."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from core.interpretation import TurnInterpretation
from core.turn import TurnInput, handle_turn
from tests import case_c_judge
from tests.case_c_judge import (
    NO_OUTPUT,
    ONLY_S3,
    PASS,
    REVERT,
    ROUND_2,
    Judgement,
    judge_record,
    judge_round,
    main,
    round_outcome,
)
from tests.interpretations import FIXED, NOW

RUNS = Path(__file__).parent.parent / "phase0" / "runs"
RUN_06 = RUNS / "20261117T073844Z-gemini-3.5-flash-lite.jsonl"


def _record(key: str, interpretation: dict[str, Any] | None, fixture: str = "") -> dict:
    """A JSONL line as the harness writes it, with only what the judge reads."""
    fixture = fixture or key
    return {
        "id": key,
        "fixture": fixture,
        "message": FIXED[fixture].message,
        "interpretation": interpretation,
        "error": None,
    }


def _fixed(key: str) -> dict[str, Any]:
    return copy.deepcopy(FIXED[key].interpretation)


def _op(tool: str, certainty: str = "explicit", **args: Any) -> dict[str, Any]:
    return {"tool": tool, "certainty": certainty, "args": args}


def _write(path: Path, records: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), "utf-8")
    return path


def test_fixed_outputs_pass(tmp_path):
    records = [
        _record("A1", _fixed("A1")),
        _record("C1", _fixed("C1")),
        _record("E1", _fixed("E1")),
        _record("N1", _fixed("N1")),
        _record("S3", _fixed("C1"), fixture="C1"),
    ]

    result = judge_round(_write(tmp_path / "fixed.jsonl", records), 1)

    rows = {j.id: (j.row, j.passed) for j in result.judgements}
    assert rows == {
        "A1": ("R2", True),
        "C1": ("R4", True),
        "E1": ("R3", True),
        "N1": ("R2", True),
        "S3": ("regla_5", True),
    }
    assert result.outcome == PASS


def test_06_run_reproduces_the_hand_reading():
    result = judge_round(RUN_06, 1)

    by_id = {j.id: j for j in result.judgements}
    assert [j.id for j in result.judgements] == ["A1", "C1", "E1", "N1", "S3"]
    assert {key: (j.row, j.passed) for key, j in by_id.items()} == {
        "C1": ("R2", False),
        "N1": ("R2", True),
        "S3": ("regla_2", False),
        "A1": ("R2", True),
        "E1": ("R3", True),
    }
    assert "depends_on=presente" in by_id["E1"].detail
    assert result.outcome == ROUND_2


EDGES = {
    "C1 two explicit": (
        "C1",
        {
            "act": "request_action",
            "operations": [_op("send_notification"), _op("schedule_meeting")],
        },
        "R3",
        False,
    ),
    "C1 two plausible": (
        "C1",
        {
            "act": "request_action",
            "operations": [
                _op("send_notification", "plausible"),
                _op("schedule_meeting", "plausible"),
            ],
        },
        "R5",
        True,
    ),
    "N1 with a plausible meeting": (
        "N1",
        {
            "act": "request_action",
            "operations": [
                *_fixed("N1")["operations"],
                _op("schedule_meeting", "plausible", attendees=["Carlos"]),
            ],
        },
        "R4",
        False,
    ),
    "A1 with an invented title": (
        "A1",
        {
            "act": "request_action",
            "operations": [
                _op("create_ticket", title="Servidor caído", description="el servidor está caído")
            ],
        },
        "R2",
        False,
    ),
    "E1 only the ticket": (
        "E1",
        {
            "act": "request_action",
            "operations": [_op("create_ticket", priority="alta", assignee="Carlos")],
        },
        "R2",
        False,
    ),
    "S3 explicit notice and plausible meeting": (
        "S3",
        {
            "act": "request_action",
            "operations": [
                _op("send_notification", recipient="Carlos"),
                _op("schedule_meeting", "plausible", attendees=["Carlos"]),
            ],
        },
        "regla_5",
        True,
    ),
    "S3 confirm without operations": ("S3", {"act": "confirm", "operations": []}, "regla_5", True),
    # Review 10c-1, should-fix 2: one case per condition no other case pins.
    "C1 plausible of a tool that is not the meeting": (
        "C1",
        {
            "act": "request_action",
            "operations": [
                *_fixed("N1")["operations"],
                _op("create_ticket", "plausible", description="mañana revisamos el servidor"),
            ],
        },
        "R4",
        False,
    ),
    "E1 two explicit of another pair": (
        "E1",
        {
            "act": "request_action",
            "operations": [
                _op("create_ticket", priority="alta", assignee="Carlos"),
                _op("schedule_meeting", attendees=["Carlos"]),
            ],
        },
        "R3",
        False,
    ),
    # Review 10c-2, should-fix 1: `confirm` in rule 2 and `reject`/`cancel` in rule 4.
    "S3 confirm with one candidate explicit": (
        "S3",
        {"act": "confirm", "operations": [_op("send_notification")]},
        "regla_2",
        False,
    ),
    "S3 cancel with a ticket explicit": (
        "S3",
        {"act": "cancel", "operations": [_op("create_ticket")]},
        "regla_4",
        False,
    ),
    "S3 confirm with both candidates explicit": (
        "S3",
        {
            "act": "confirm",
            "operations": [_op("send_notification"), _op("schedule_meeting")],
        },
        "regla_5",
        True,
    ),
    "S3 explicit ticket": (
        "S3",
        {"act": "request_action", "operations": [_op("create_ticket")]},
        "regla_4",
        False,
    ),
    "S3 reject without operations": ("S3", {"act": "reject", "operations": []}, "regla_1", False),
}


@pytest.mark.parametrize("case", list(EDGES))
def test_judge_edges(case):
    key, interpretation, row, passed = EDGES[case]
    fixture = "C1" if key == "S3" else key

    judgement = judge_record(_record(key, interpretation, fixture=fixture))

    assert (judgement.row, judgement.passed) == (row, passed)


@pytest.mark.parametrize(("key", "other"), [("N1", "create_ticket"), ("A1", "send_notification")])
def test_n1_and_a1_need_their_own_tool(monkeypatch, key, other):
    # Review 10c-1, should-fix 2: in this registry no other tool leaves the same `missing`, so
    # the expected `missing` is made to match the other tool's and only the tool check is left.
    interpretation = {"act": "request_action", "operations": [_op(other)]}
    pending_missing = handle_turn(
        None, TurnInterpretation.model_validate(interpretation), TurnInput(FIXED[key].message, NOW)
    ).state_delta["pending"]["missing"]
    monkeypatch.setattr(case_c_judge, "_expected_missing", lambda _key: pending_missing)

    judgement = judge_record(_record(key, interpretation))

    assert (judgement.row, judgement.passed) == ("R2", False)


def test_e1_reports_a_missing_depends_on():
    # Review 10c-2, note 3: informative only (D8.4), but task 14a reads it from the note.
    interpretation = _fixed("E1")
    del interpretation["operations"][1]["depends_on"]
    judgement = judge_record(_record("E1", interpretation))
    assert (judgement.row, judgement.passed) == ("R3", True)
    assert "depends_on=ausente" in judgement.detail


def test_an_output_that_does_not_validate_has_no_output():
    judgement = judge_record(_record("C1", {"act": "dance", "operations": []}))
    assert (judgement.row, judgement.passed) == (NO_OUTPUT, False)


def test_a_line_without_output_fails():
    record = {**_record("C1", None), "error": "ServerError: 503"}
    judgement = judge_record(record)
    assert (judgement.row, judgement.passed) == (NO_OUTPUT, False)


def _judgements(failed: set[str]) -> list[Judgement]:
    return [Judgement(key, "x", key not in failed, "") for key in ("C1", "N1", "S3", "A1", "E1")]


@pytest.mark.parametrize(
    ("failed", "first", "second"),
    [
        (set(), PASS, PASS),
        ({"S3"}, ONLY_S3, ONLY_S3),
        ({"C1"}, ROUND_2, REVERT),
        ({"N1"}, ROUND_2, REVERT),
        ({"A1"}, ROUND_2, REVERT),  # C1 and N1 pass (the author's second stop of 10c)
        ({"E1"}, ROUND_2, REVERT),
        ({"S3", "A1"}, ROUND_2, REVERT),
    ],
)
def test_round_outcomes(failed, first, second):
    assert round_outcome(_judgements(failed), 1) == first
    assert round_outcome(_judgements(failed), 2) == second


def test_cli_prints_one_line_per_id_and_the_outcome(capsys):
    assert main([str(RUN_06)]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert [line.split()[:3] for line in lines[:-1]] == [
        ["A1", "R2", "PASA"],
        ["C1", "R2", "FALLA"],
        ["E1", "R3", "PASA"],
        ["N1", "R2", "PASA"],
        ["S3", "regla_2", "FALLA"],
    ]
    assert lines[-1] == "Resultado: ronda_2"


def test_cli_reports_an_unreadable_file(tmp_path, capsys):
    assert main([str(tmp_path / "missing.jsonl")]) == 2
    assert "No se pudo leer" in capsys.readouterr().err


# Written after the calls of task 10c: what each round's note says, by id and outcome.
RECORDED: dict[str, tuple[int, dict[str, tuple[str, bool]], str]] = {
    "20261117T164006Z-gemini-3.5-flash-lite.jsonl": (
        1,
        {
            "A1": ("R2", True),
            "C1": ("R2", False),
            "E1": ("R3", True),
            "N1": ("R2", True),
            "S3": ("regla_2", False),
        },
        ROUND_2,
    ),
    "20261117T164255Z-gemini-3.5-flash-lite.jsonl": (
        2,
        {
            "A1": ("R2", True),
            "C1": ("R2", False),
            "E1": ("R3", True),
            "N1": ("R2", True),
            "S3": ("regla_5", True),
        },
        REVERT,
    ),
}


@pytest.mark.parametrize("name", list(RECORDED))
def test_10c_runs_as_recorded(name):
    round_number, expected, outcome = RECORDED[name]

    result = judge_round(RUNS / name, round_number)

    assert {j.id: (j.row, j.passed) for j in result.judgements} == expected
    assert result.outcome == outcome
