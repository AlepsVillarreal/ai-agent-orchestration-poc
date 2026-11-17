"""The 23 scenarios of the phase 0 smoke test (D2, *Umbral de fiabilidad*; F0, F0.1).

Each scenario sends one message to the interpreter alone, with the pending record the
interpreter would see at that point of the conversation. Ten are first messages (nothing
pending); thirteen are second messages:
- `B2`, `F2` and `L1`-`L4` see the record the core creates today: `handle_turn` on the first
  message of their conversation (`B1`, or `F1`, the meeting with Juan).
- `G2` and the clarification answers and probes (`K1`-`K3`, `S1`-`S3`) see a literal record,
  because the core does not create those states yet (tasks 10a and 15). The literals carry no
  collected value: the interpreter only sees `tools`, `status`, `missing` and `last_question`
  (D14), and the clarification question is D3's *Pregunta* built from the registry's
  `action_phrase`.

The messages and expected outputs are the fixtures of `tests/interpretations.py`, imported as
`tests.interpretations`: run everything from the repository root (`uv run python -m
phase0.run`), where `tests/` is importable.
"""

from dataclasses import dataclass
from typing import Any

from core.interpretation import TurnInterpretation
from core.registry import TOOLS
from core.turn import TurnInput, handle_turn
from tests.interpretations import FIXED, NOW, SESSION


@dataclass(frozen=True)
class Scenario:
    id: str
    fixture: str  # key in FIXED: the message sent and the expected output
    pending: dict[str, Any] | None  # what `state["pending"]` holds when the message arrives
    previous: str | None = None  # key in FIXED of the message before it, if any


def _core_record(first_message: str) -> dict[str, Any]:
    """The pending record `handle_turn` creates for a first message, with nothing pending.

    `now` is the fixed `NOW` of the tests, so the harness never reads the clock: `B1` and `F1`
    bring no date, and the records (and the summaries D32 was measured with) do not depend on it.
    """
    fixed = FIXED[first_message]
    interpretation = TurnInterpretation.model_validate(fixed.interpretation)
    return handle_turn(None, interpretation, TurnInput(fixed.message, NOW, SESSION)).state_delta[
        "pending"
    ]


def _literal_record(tools: list[str], status: str, question: str) -> dict[str, Any]:
    return {"tools": tools, "status": status, "missing": [], "last_question": question}


_CANDIDATES = ["send_notification", "schedule_meeting"]
# D3, *Pregunta*, with two candidates: "¿Quieres {acción A}, {acción B}, o ambas?".
CLARIFICATION_QUESTION = (
    f"¿Quieres {TOOLS[_CANDIDATES[0]].action_phrase}, "
    f"{TOOLS[_CANDIDATES[1]].action_phrase}, o ambas?"
)
CONFIRMATION_QUESTION = "¿Confirmas que envíe la notificación?"


def _build() -> list[Scenario]:
    ticket = _core_record("B1")
    meeting_with_juan = _core_record("F1")
    confirmation = _literal_record(
        ["send_notification"], "awaiting_confirmation", CONFIRMATION_QUESTION
    )
    clarification = _literal_record(_CANDIDATES, "awaiting_clarification", CLARIFICATION_QUESTION)

    def first(key: str) -> Scenario:
        return Scenario(id=key, fixture=key, pending=None)

    def second(key: str, pending: dict[str, Any], previous: str, fixture: str = "") -> Scenario:
        return Scenario(id=key, fixture=fixture or key, pending=pending, previous=previous)

    return [
        first("A1"),
        second("B2", ticket, "B1"),
        first("C1"),
        first("D1"),
        first("E1"),
        second("F2", meeting_with_juan, "F1"),
        first("G1"),
        second("G2", confirmation, "G1"),
        first("X1"),
        first("Q1"),
        first("W1"),
        first("N1"),
        first("T1"),
        second("K1", clarification, "C1"),
        second("K2", clarification, "C1"),
        second("K3", clarification, "C1"),
        second("S1", clarification, "C1"),
        second("S2", clarification, "C1"),
        second("S3", clarification, "C1", fixture="C1"),  # case C's message, repeated
        second("L1", meeting_with_juan, "F1"),
        second("L2", meeting_with_juan, "F1"),
        second("L3", meeting_with_juan, "F1"),
        second("L4", meeting_with_juan, "F1"),
    ]


SCENARIOS: list[Scenario] = _build()
