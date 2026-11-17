"""Judge of task 10c: what the core would do with saved interpreter outputs (D3, D33).

    uv run python -m tests.case_c_judge RUTA.jsonl [--round 1|2]

It reads a JSONL of the phase 0 harness, judges the lines of `C1`, `N1`, `S3`, `A1` and `E1`
that the file holds (other ids, and absent ones, are not judged) and prints one line per id and
the round's outcome. No model is called: D3, *Método*, repeats saved outputs through the rules.

- `C1`, `N1`, `A1`, `E1` (nothing pending) go through the real core: `handle_turn` gives the row
  of D3's table and `filter_by_registry` the operations the core keeps.
- `S3` (a clarification pending) goes through the real core too: `handle_turn` on the
  clarification the core stores for the fixed `C1`, and the rule is read from its route
  (`clarification:regla_<n>`; any other route, such as `d11`, is not `regla_5`).

The outcome follows D33 and the author's answers for 10c: all pass → "pasa"; only `S3` fails →
"solo_s3"; any of `C1`, `N1`, `A1`, `E1` fails → "ronda_2" in round 1, "revertir" in round 2.
It lives in `tests/` because the harness gives no verdicts and the core does not change in 10c.
"""

import argparse
import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from core.interpretation import TurnInterpretation
from core.turn import TurnInput, filter_by_registry, handle_turn
from tests.interpretations import FIXED, NOW, SESSION

JUDGED = ("C1", "N1", "S3", "A1", "E1")
CORE_IDS = ("C1", "N1", "A1", "E1")  # a failure of any of these asks for round 2 or a revert
NO_OUTPUT = "sin_salida"

PASS = "pasa"
ONLY_S3 = "solo_s3"
ROUND_2 = "ronda_2"
REVERT = "revertir"


@dataclass(frozen=True)
class Judgement:
    id: str
    row: str  # D3's row ("R2", "R4", "d11"...) or the clarification rule ("regla_5")
    passed: bool
    detail: str


@dataclass(frozen=True)
class RoundResult:
    judgements: list[Judgement]
    outcome: str


def _expected_missing(key: str) -> list[str]:
    """`missing` of the record the core creates for the fixed interpretation of `key`."""
    fixed = FIXED[key]
    interpretation = TurnInterpretation.model_validate(fixed.interpretation)
    outcome = handle_turn(None, interpretation, TurnInput(fixed.message, NOW, SESSION))
    return outcome.state_delta["pending"]["missing"]


def _tools(operations: list[dict]) -> list[str]:
    return [operation["tool"] for operation in operations]


def _pairs(operations: list[dict]) -> set[tuple[str, str]]:
    return {(operation["tool"], str(operation["certainty"])) for operation in operations}


def _first_message(key: str, record: dict, interpretation: TurnInterpretation) -> Judgement:
    """`C1`, `N1`, `A1`, `E1`: nothing pending, so the real core decides the row.

    A saved output is read against the fixed `NOW`, so a judgement never depends on the day the
    judge runs (a date the model copied is resolved against the same moment every time).
    """
    outcome = handle_turn(None, interpretation, TurnInput(record["message"], NOW, SESSION))
    row = outcome.route
    operations = filter_by_registry(interpretation)
    tools = _tools(operations)
    pending = outcome.state_delta.get("pending") or {}

    if key == "C1":
        passed = row in {"R4", "R5"} and {"send_notification", "schedule_meeting"} <= set(tools)
        reason = "presente" if interpretation.ambiguity_reason else "ausente"
        detail = f"ops={sorted(_pairs(operations))} ambiguity_reason={reason}"
    elif key in ("N1", "A1"):
        tool = "send_notification" if key == "N1" else "create_ticket"
        missing = pending.get("missing")
        expected = _expected_missing(key)
        passed = row == "R2" and tools == [tool] and missing == expected
        detail = f"tools={tools} missing={missing} esperado={expected}"
    else:  # E1
        wanted = {("create_ticket", "explicit"), ("send_notification", "explicit")}
        passed = row == "R3" and _pairs(operations) == wanted
        depends = "presente" if any(op["depends_on"] for op in operations) else "ausente"
        detail = f"ops={sorted(_pairs(operations))} depends_on={depends}"
    return Judgement(key, row, passed, detail)


def _c1_clarification() -> dict:
    """The clarification the core stores for the fixed `C1` (D3, *Estado*): what `S3` answers."""
    fixed = FIXED["C1"]
    interpretation = TurnInterpretation.model_validate(fixed.interpretation)
    outcome = handle_turn(None, interpretation, TurnInput(fixed.message, NOW, SESSION))
    return outcome.state_delta["pending"]


def _probe(record: dict, interpretation: TurnInterpretation) -> Judgement:
    """`S3`: the message of `C1` repeated as the answer; it must not resolve (D2, *Sondas*)."""
    turn_input = TurnInput(record["message"], NOW, SESSION)
    route = handle_turn(_c1_clarification(), interpretation, turn_input).route
    rule = route.split(":")[1] if route.startswith("clarification:") else route
    operations = filter_by_registry(interpretation)
    detail = f"act={interpretation.act.value} ops={sorted(_pairs(operations))}"
    return Judgement("S3", rule, rule == "regla_5", detail)


def judge_record(record: dict) -> Judgement:
    """One JSONL line of `C1`, `N1`, `S3`, `A1` or `E1`."""
    key = record["id"]
    if key not in JUDGED:
        raise ValueError(f"{key!r} is not judged in task 10c")
    if record.get("interpretation") is None:
        return Judgement(key, NO_OUTPUT, False, f"error={record.get('error')}")
    try:
        interpretation = TurnInterpretation.model_validate(record["interpretation"])
    except ValidationError as ex:  # not written by the harness, which saves only valid output
        return Judgement(key, NO_OUTPUT, False, f"no valida: {ex.error_count()} errores")
    if key == "S3":
        return _probe(record, interpretation)
    return _first_message(key, record, interpretation)


def round_outcome(judgements: Iterable[Judgement], round_number: int) -> str:
    """D33 with option (b) and the author's second stop of 10c."""
    failed = {judgement.id for judgement in judgements if not judgement.passed}
    if failed & set(CORE_IDS):
        return ROUND_2 if round_number == 1 else REVERT
    return ONLY_S3 if failed else PASS


def read_records(path: Path) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def judge_round(path: Path | str, round_number: int = 1) -> RoundResult:
    """Judge every line of a judged id in `path`, in file order, and the round's outcome."""
    records = read_records(Path(path))
    judgements = [judge_record(record) for record in records if record["id"] in JUDGED]
    return RoundResult(judgements, round_outcome(judgements, round_number))


def format_round(result: RoundResult) -> str:
    lines = [
        f"{j.id} {j.row} {'PASA' if j.passed else 'FALLA'} {j.detail}" for j in result.judgements
    ]
    lines.append(f"Resultado: {result.outcome}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Juez de la tarea 10c (D3, D33), sin modelo.")
    parser.add_argument("path", type=Path, metavar="RUTA")
    parser.add_argument("--round", type=int, choices=(1, 2), default=1, dest="round_number")
    args = parser.parse_args(argv)
    try:
        result = judge_round(args.path, args.round_number)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as ex:
        print(f"No se pudo leer {args.path}: {ex}", file=sys.stderr)
        return 2
    print(format_round(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
