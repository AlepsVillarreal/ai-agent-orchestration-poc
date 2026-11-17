"""Phase 0 harness: send the 23 scenarios to the interpreter and count the shape failures.

    uv run python -m phase0.run          # list the scenarios; no model, no `.env`, no files
    uv run python -m phase0.run --run    # one real model call per scenario (task 06)
    uv run python -m phase0.run --run --resume RUTA   # call only what RUTA did not save

Run it from the repository root: `phase0.scenarios` imports `tests.interpretations`.

The interpreter runs alone (it measures the schema, not the core): a `Runner` over the agent of
`build_interpreter` and an in-memory session per scenario whose state holds the pending record,
so the instruction carries the pending summary of D14. One call per scenario. The raw output
goes to a JSONL file in `phase0/runs/`; what the code would do with it is judged by hand in
task 06. The only number that decides anything is the count of shape failures (F0.1).

Retries (task 06, round 2, the author's decision after a 503): only this harness gives the
model `HttpRetryOptions`, so the google-genai client retries a 429 or a 503 with exponential
backoff and jitter, up to 3 attempts. The turn node and `assistant/` keep D1.1: no retries.
"""

import argparse
import asyncio
import copy
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from dotenv import load_dotenv
from google.adk import Runner
from google.adk.models import BaseLlm, Gemini
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
# Between calls. ai.google.dev/gemini-api/docs/rate-limits (-> aistudio.google.com/docs/
# rate-limits), read 2026-11-17: no free-tier RPM/RPD/TPM for gemini-3.5-flash-lite, so 13 s
# (fits 5 RPM) as task 06 sets; never a guessed higher limit (D2).
DELAY_SECONDS = 13.0
# Harness only (autodecided R: 429 and 503 are the API's "try again" codes; a 500 may be the
# request's fault). `exp_base` and `jitter` stay at the SDK's defaults (2 and 1): waits of
# about 2 s and 4 s, at most 30 s. The SDK also retries its transient network errors.
RETRY_OPTIONS = types.HttpRetryOptions(
    attempts=3, initial_delay=2.0, max_delay=30.0, http_status_codes=[429, 503]
)
# The google-genai logger that writes one INFO line before each retry (autodecided S).
SDK_LOGGER = "google_genai._api_client"

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
    resumed: int = 0  # records copied from a previous run (`--resume`)
    called: int = 0  # scenarios sent to the model in this run
    resumed_from: str | None = None  # the previous JSONL, if any


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
    saved: dict[str, str] | None = None,
) -> RunResult:
    """One new session and one call per scenario, in order; one JSONL line each to `out`.

    A scenario in `saved` (id → its JSONL line from a previous run, see `load_saved`) is not
    called: its line is written unchanged. The wait runs only between real calls. A model
    exception (after the SDK's retries, if the model has any) is recorded as
    `"<Type>: <message>"`, is not a shape failure and stops the run.
    """
    name = model if isinstance(model, str) else model.model
    runner = Runner(
        app_name=APP_NAME, agent=build_interpreter(model), session_service=InMemorySessionService()
    )
    result = RunResult(model=name, total=len(scenarios))
    saved = saved or {}

    for scenario in scenarios:
        if scenario.id in saved:
            line = saved[scenario.id]
            result.records.append(Record(**json.loads(line)))
            result.resumed += 1
            if out is not None:
                out.write(line + "\n")
                out.flush()
            continue
        if result.called and delay:
            await asyncio.sleep(delay)
        result.called += 1
        record = Record(
            id=scenario.id,
            fixture=scenario.fixture,
            message=FIXED[scenario.fixture].message,
            pending_summary=pending_summary(scenario.pending),
        )
        try:
            record.raw = await _call(runner, runner.session_service, scenario)
        except Exception as ex:  # 429/503 after the retries, timeout, missing key...: stop
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
    if result.resumed_from:
        lines.append(
            f"Reanudados de `{result.resumed_from}`: {result.resumed}; "
            f"llamadas en esta corrida: {result.called}"
        )

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
        f"--run hace {len(SCENARIOS)} llamadas a {MODEL}, una por escenario; el cliente "
        f"reintenta un 429 o un 503 hasta {RETRY_OPTIONS.attempts} intentos.",
    ]
    return "\n".join(lines)


class ResumeError(Exception):
    """The previous JSONL cannot be resumed; nothing has been called or written."""


def load_saved(path: Path) -> dict[str, str]:
    """The lines of a previous JSONL that `--resume` copies, by scenario id.

    A scenario is saved if its line has a `raw` and no `error`. Every line must be a record of
    this harness for a current scenario (same `fixture` and `message`), each id once;
    otherwise `ResumeError`. The lines are returned exactly as read, without the newline.
    """
    by_id = {scenario.id: scenario for scenario in SCENARIOS}
    keys = {item.name for item in fields(Record)}
    try:
        lines = path.read_bytes().decode("utf-8").split("\n")
    except (OSError, UnicodeDecodeError) as ex:
        raise ResumeError(f"no se pudo leer {path}: {ex}") from ex
    if lines and lines[-1] == "":
        lines.pop()  # the newline that ends the last line

    saved: dict[str, str] = {}
    seen: set[str] = set()
    for number, line in enumerate(lines, start=1):
        where = f"{path}, línea {number}"
        try:
            record = json.loads(line)
        except json.JSONDecodeError as ex:
            raise ResumeError(f"{where}: no es JSON ({ex})") from ex
        if not isinstance(record, dict) or set(record) != keys:
            raise ResumeError(f"{where}: no es un registro de este arnés")
        scenario = by_id.get(record["id"])
        if scenario is None:
            raise ResumeError(f"{where}: escenario desconocido {record['id']!r}")
        if record["id"] in seen:
            raise ResumeError(f"{where}: escenario repetido {record['id']!r}")
        seen.add(record["id"])
        if (
            record["fixture"] != scenario.fixture
            or record["message"] != FIXED[scenario.fixture].message
        ):
            raise ResumeError(f"{where}: {record['id']} no coincide con el escenario actual")
        if record["raw"] is not None and record["error"] is None:
            saved[record["id"]] = line
    return saved


def harness_model() -> Gemini:
    """The real model of `--run`: Gemini with the harness's retries (the turn node has none)."""
    return Gemini(model=MODEL, retry_options=RETRY_OPTIONS)


def _log_http_retries() -> None:
    """Under `--run`, one stderr line per SDK retry, to count HTTP requests (autodecided S)."""
    logger = logging.getLogger(SDK_LOGGER)
    logger.setLevel(logging.INFO)
    if not any(handler.get_name() == APP_NAME for handler in logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.set_name(APP_NAME)
        handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
        logger.addHandler(handler)


async def _run_and_save(saved: dict[str, str] | None, resumed_from: Path | None) -> int:
    RUNS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = RUNS_DIR / f"{stamp}-{MODEL}.jsonl"
    with path.open("w", encoding="utf-8") as out:
        result = await run_scenarios(harness_model(), SCENARIOS, out, saved=saved)
    if resumed_from is not None:
        result.resumed_from = str(resumed_from)
    print(format_report(result))
    print(f"Salida cruda: {path}")
    return 1 if result.stopped_at else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prueba de humo de la fase 0 (D2, F0, F0.1).")
    parser.add_argument(
        "--run", action="store_true", help="llama al modelo real, una vez por escenario"
    )
    parser.add_argument(
        "--resume",
        type=Path,
        metavar="RUTA",
        help="con --run: copia lo guardado en ese JSONL y llama solo a lo que falta",
    )
    args = parser.parse_args(argv)
    if args.resume is not None and not args.run:
        parser.error("--resume solo va junto con --run")
    if not args.run:
        print(list_scenarios())
        return 0

    saved = None
    if args.resume is not None:
        try:
            saved = load_saved(args.resume)
        except ResumeError as ex:  # before `.env`, any call or any file
            print(f"No se puede reanudar: {ex}", file=sys.stderr)
            return 2

    load_dotenv()  # only with --run: the listing never reads `.env`
    _log_http_retries()
    return asyncio.run(_run_and_save(saved, args.resume))


if __name__ == "__main__":
    sys.exit(main())
