"""Phase 0 harness: send the 23 scenarios to the interpreter and count the shape failures.

    uv run python -m phase0.run          # list the scenarios; no model, no `.env`, no files
    uv run python -m phase0.run --run    # one real model call per scenario (task 06)

Run it from the repository root: `phase0.scenarios` imports `tests.interpretations`.

The interpreter runs alone (it measures the schema, not the core): a `Runner` over the agent of
`build_interpreter` and an in-memory session per scenario whose state holds the pending record,
so the instruction carries the pending summary of D14. One call per scenario, no retries
(D1.1). The raw output goes to a JSONL file in `phase0/runs/`; what the code would do with it
is judged by hand in task 06. The only number that decides anything is the count of shape
failures (F0.1).
"""

import argparse
import asyncio
import copy
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from dotenv import load_dotenv
from google.adk import Runner
from google.adk.models import BaseLlm
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import ValidationError

from assistant.interpreter import MODEL, build_interpreter
from core.instruction import pending_summary
from core.interpretation import TurnInterpretation
from phase0.scenarios import SCENARIOS, Scenario
from tests.interpretations import FIXED

APP_NAME = "phase0"
USER_ID = "phase0"
RUNS_DIR = Path(__file__).parent / "runs"
DELAY_SECONDS = 6.0  # between calls (10 per minute); check the real limit before --run (D2)

EMPTY = "empty"
VALIDATION = "validation"
_NESTED = ("date", "time")  # nested objects inside `args` checked for `{}`
# The language tag after the opening fence; same pattern as ADK (linear, no backtracking).
_FENCE_LANGUAGE = re.compile(r"^[A-Za-z0-9_]*[ \t]*\n?")


@dataclass
class Record:
    """One line of the JSONL file: one scenario attempted."""

    id: str
    fixture: str
    message: str
    pending_summary: str
    raw: str | None = None  # the interpreter's text, untouched; None if the call raised
    error: str | None = None  # "<Type>: <message>" of a model exception (it stops the run)
    shape_failure: str | None = None  # D2, *Fallo de forma*
    interpretation: dict[str, Any] | None = None  # what ADK would hand the turn node
    matches_expected: bool | None = None  # act and {(tool, certainty)}; None without output


@dataclass
class RunResult:
    model: str
    total: int  # scenarios asked for
    records: list[Record] = field(default_factory=list)
    stopped_at: str | None = None  # id of the scenario whose call raised


def strip_code_fence(text: str) -> str:
    """Remove a markdown fence that wraps the whole output, as ADK does before validating.

    Option (a) of the brief: ADK 2.14.1 strips a ```json ... ``` fence before
    `model_validate_json` (`google/adk/utils/_schema_utils.py:214-252`), so a fenced valid
    JSON reaches the turn node as valid and is not a shape failure here either. Same checks as
    ADK's helper, written here instead of importing a private module.
    """
    stripped = text.strip()
    if len(stripped) < 6 or not (stripped.startswith("```") and stripped.endswith("```")):
        return text
    inner = _FENCE_LANGUAGE.sub("", stripped[3:-3], count=1)  # drop a tag such as "json"
    return inner.strip()


def _expected(scenario: Scenario) -> dict[str, Any]:
    return FIXED[scenario.fixture].interpretation


def shape_failure(raw: str, expected: dict[str, Any]) -> tuple[str | None, dict | None]:
    """D2, *Fallo de forma*, the first that applies, and the validated output if any.

    `empty`: no text or only blanks. `validation`: `model_validate_json` raises. An empty
    nested object (`args`, `date`, `time` arriving as `{}`) for a tool whose expected operation
    carries that object with content: `empty_args`, `empty_date`, `empty_time`.
    """
    if not raw.strip():
        return EMPTY, None
    try:
        parsed = TurnInterpretation.model_validate_json(strip_code_fence(raw))
    except ValidationError:
        return VALIDATION, None
    output = parsed.model_dump(exclude_none=True)

    expected_args: dict[str, list[dict]] = {}
    for operation in expected["operations"]:
        expected_args.setdefault(operation["tool"], []).append(operation.get("args") or {})
    for operation in output["operations"]:
        wanted = expected_args.get(operation["tool"], [])
        args = operation.get("args") or {}
        if not args and any(wanted):
            return "empty_args", output
        for name in _NESTED:
            if args.get(name) == {} and any(want.get(name) for want in wanted):
                return f"empty_{name}", output
    return None, output


def matches(output: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Informative only: same `act` and the same set of `(tool, certainty)`."""

    def operations(value: dict[str, Any]) -> set[tuple[str, str]]:
        return {(op["tool"], str(op["certainty"])) for op in value.get("operations", [])}

    return str(output["act"]) == expected["act"] and operations(output) == operations(expected)


def _text(event) -> str:
    if not event.content or not event.content.parts:
        return ""
    return "".join(part.text or "" for part in event.content.parts if not part.thought)


async def _call(runner: Runner, sessions: InMemorySessionService, scenario: Scenario) -> str:
    state = {"pending": copy.deepcopy(scenario.pending)} if scenario.pending else {}
    session = await sessions.create_session(app_name=APP_NAME, user_id=USER_ID, state=state)
    message = types.Content(role="user", parts=[types.Part(text=FIXED[scenario.fixture].message)])
    texts = []
    async for event in runner.run_async(
        user_id=USER_ID, session_id=session.id, new_message=message
    ):
        if event.author == "interpreter" and not event.partial:
            texts.append(_text(event))
    return "".join(texts)


async def run_scenarios(
    model: str | BaseLlm,
    scenarios: list[Scenario] = SCENARIOS,
    out: TextIO | None = None,
    delay: float = DELAY_SECONDS,
) -> RunResult:
    """One new session and one call per scenario, in order; one JSONL line each to `out`.

    A model exception is recorded as `"<Type>: <message>"`, is not a shape failure and stops
    the run (no retry: D1.1).
    """
    name = model if isinstance(model, str) else model.model
    runner = Runner(
        app_name=APP_NAME, agent=build_interpreter(model), session_service=InMemorySessionService()
    )
    result = RunResult(model=name, total=len(scenarios))

    for index, scenario in enumerate(scenarios):
        if index and delay:
            await asyncio.sleep(delay)
        record = Record(
            id=scenario.id,
            fixture=scenario.fixture,
            message=FIXED[scenario.fixture].message,
            pending_summary=pending_summary(scenario.pending),
        )
        try:
            record.raw = await _call(runner, runner.session_service, scenario)
        except Exception as ex:  # 429, timeout, missing key...: stop, do not retry
            record.error = f"{type(ex).__name__}: {ex}"
            result.stopped_at = scenario.id
        else:
            record.shape_failure, output = shape_failure(record.raw, _expected(scenario))
            if output is not None:
                record.interpretation = output
                record.matches_expected = matches(output, _expected(scenario))

        result.records.append(record)
        if out is not None:
            out.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
            out.flush()
        if result.stopped_at:
            break
    return result


def _e1_depends_on(result: RunResult) -> str:
    record = next((r for r in result.records if r.id == "E1"), None)
    if record is None or record.interpretation is None:
        return "`depends_on` de E1: sin dato (E1 no se corrió o no se pudo leer)"
    if any(op.get("depends_on") for op in record.interpretation["operations"]):
        return "`depends_on` de E1: presente"
    return "`depends_on` de E1: ausente (la tarea 14a lo infiere: F7)"


def format_report(result: RunResult) -> str:
    """The short report for stdout, in Spanish like the rest of the user-facing text."""
    run = len(result.records)
    failures = [r for r in result.records if r.shape_failure]
    lines = [f"Modelo: {result.model}", f"Escenarios corridos: {run} de {result.total}"]

    if failures:
        lines.append(f"Fallos de forma: {len(failures)}")
        lines += [f"  - {r.id}: {r.shape_failure}" for r in failures]
    else:
        lines.append("Fallos de forma: 0")

    if result.stopped_at:
        error = result.records[-1].error
        lines.append(
            f"Corrida detenida en {result.stopped_at}: {error}; {run} de {result.total}; "
            "sin veredicto"
        )
    elif run == result.total == len(SCENARIOS):
        if failures:
            lines.append(
                f"Veredicto F0.1: {len(failures)}/{run} → parar y mostrar estos casos al autor"
            )
        else:
            lines.append(f"Veredicto F0.1: 0/{run} → se sigue con la opción B")
    else:
        lines.append(f"Sin veredicto de F0.1: no se corrieron los {len(SCENARIOS)} escenarios")

    lines.append(_e1_depends_on(result))
    differ = [r.id for r in result.records if r.matches_expected is False]
    lines.append(f"Difieren de lo esperado (act y tools): {', '.join(differ) or 'ninguno'}")
    return "\n".join(lines)


def list_scenarios() -> str:
    lines = [f"{len(SCENARIOS)} escenarios de la fase 0 (D2):"]
    for scenario in SCENARIOS:
        status = scenario.pending["status"] if scenario.pending else "nada pendiente"
        lines.append(f"  {scenario.id:<3} [{status}] {FIXED[scenario.fixture].message}")
    lines += [
        "",
        f"--run hace {len(SCENARIOS)} llamadas a {MODEL}, una por escenario, sin reintentos.",
    ]
    return "\n".join(lines)


async def _run_and_save() -> int:
    RUNS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = RUNS_DIR / f"{stamp}-{MODEL}.jsonl"
    with path.open("w", encoding="utf-8") as out:
        result = await run_scenarios(MODEL, SCENARIOS, out)
    print(format_report(result))
    print(f"Salida cruda: {path}")
    return 1 if result.stopped_at else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prueba de humo de la fase 0 (D2, F0, F0.1).")
    parser.add_argument(
        "--run", action="store_true", help="llama al modelo real, una vez por escenario"
    )
    args = parser.parse_args(argv)
    if not args.run:
        print(list_scenarios())
        return 0

    load_dotenv()  # only with --run: the listing never reads `.env`
    return asyncio.run(_run_and_save())


if __name__ == "__main__":
    sys.exit(main())
