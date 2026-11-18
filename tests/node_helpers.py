"""Helpers shared by the tests that run turns through the real ADK `Runner` with a fake model.

`tests/test_agent.py` (the node's mechanics) and `tests/test_cases.py` (the cases of TASK.md §4)
import them. Nothing here holds a rule: they build the runner, wrap the mock tools to record
their arguments or results, fix the node's clock and read back the turn log.
"""

import dataclasses
from datetime import datetime

import pytest
from google.adk import Runner
from google.adk.apps import App
from google.adk.sessions import InMemorySessionService

import assistant.agent
from assistant.agent import build_root_agent
from assistant.interpreter import build_interpreter
from core.confirmation import shown_values
from core.interpretation import TurnInterpretation
from core.registry import TOOLS
from core.turn import TurnInput, handle_turn
from tests.fake_llm import event_text, reply_events, run_turn
from tests.interpretations import FIXED, NOW, SESSION

USER = "u1"


def fixed(key: str) -> TurnInterpretation:
    return TurnInterpretation.model_validate(FIXED[key].interpretation)


def handle_fixed(pending: dict | None, key: str):
    return handle_turn(pending, fixed(key), TurnInput(FIXED[key].message, NOW, SESSION))


def h1_values() -> dict:
    """The normalized values `create_ticket` receives for `H1` (no date: independent of now)."""
    [step] = shown_values(handle_fixed(None, "H1").state_delta["pending"]["steps"], NOW)
    return step["normalized"]


def without_id(record: dict) -> dict:
    """A record without its `operation_id`, a new uuid on every `handle_turn` call (task 10a)."""
    return {key: value for key, value in record.items() if key != "operation_id"}


def make_runner(fake, sessions=None, executions=None) -> tuple[Runner, InMemorySessionService]:
    """A `Runner` over the real root workflow, with an interpreter built on `fake`."""
    sessions = sessions or InMemorySessionService()
    root_agent = build_root_agent(build_interpreter(fake), executions=executions)
    app = App(name="assistant", root_agent=root_agent)
    return Runner(app=app, session_service=sessions, auto_create_session=True), sessions


def freeze_clock(monkeypatch, moment: datetime = NOW) -> None:
    """The node reads `datetime.now()` (its only clock); fix it, so no result depends on the day
    the suite runs (note (A3))."""

    class FrozenClock:
        @staticmethod
        def now():
            return moment

    monkeypatch.setattr(assistant.agent, "datetime", FrozenClock)


def fail_every_tool(monkeypatch) -> None:
    """Replace every tool by a double that fails the test if it is ever called."""

    def fail(args):
        pytest.fail(f"a tool ran with {args}")

    for name, spec in list(TOOLS.items()):
        monkeypatch.setitem(TOOLS, name, dataclasses.replace(spec, execute=fail))


def record_tools(monkeypatch) -> dict[str, list[dict]]:
    """Wrap every tool's `execute` to record its arguments; the real mock still runs (D12)."""
    calls: dict[str, list[dict]] = {name: [] for name in TOOLS}
    for name, spec in list(TOOLS.items()):

        def recorder(args, name=name, real=spec.execute):
            calls[name].append(args)
            return real(args)

        monkeypatch.setitem(TOOLS, name, dataclasses.replace(spec, execute=recorder))
    return calls


def record_results(monkeypatch) -> dict[str, list[dict]]:
    """Wrap every tool's current `execute` to record what it returned (the mock's own ID)."""
    results: dict[str, list[dict]] = {name: [] for name in TOOLS}
    for name, spec in list(TOOLS.items()):

        def recorder(args, name=name, inner=spec.execute):
            results[name].append(inner(args))
            return results[name][-1]

        monkeypatch.setitem(TOOLS, name, dataclasses.replace(spec, execute=recorder))
    return results


def call_counts(calls: dict[str, list[dict]]) -> dict[str, int]:
    """How many times each tool ran, by name (note (A29): not by the registry's order)."""
    return {name: len(args) for name, args in calls.items()}


def turn_records(caplog) -> list[str]:
    """The turn log lines (`assistant.turn`) captured so far."""
    return [r.getMessage() for r in caplog.records if r.name == "assistant.turn"]


async def conversation(runner, sessions, session_id, messages, caplog):
    """Each message through the `Runner`: its reply, its log line and the stored `pending`."""
    answers, lines, records = [], [], []
    for message in messages:
        caplog.clear()
        events = await run_turn(runner, USER, session_id, message)
        answers.append(event_text(reply_events(events)[0]))
        [line] = turn_records(caplog)
        lines.append(line)
        session = await sessions.get_session(
            app_name="assistant", user_id=USER, session_id=session_id
        )
        records.append(session.state.get("pending"))
    return answers, lines, records
