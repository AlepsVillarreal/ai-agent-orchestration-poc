"""What the core receives from the turn besides the interpreter's output, and what it returns.

Moved out of `core/turn.py` unchanged (brief 14a, decision I); `core/turn.py` re-exports both,
so `from core.turn import TurnInput, TurnOutcome, handle_turn` keeps working.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from core.executions import StepRun
from core.merge import Change, Rejected


@dataclass(frozen=True)
class TurnInput:
    """What the core needs from the turn that does not come from the interpreter.

    `message` is the user's text, read only to anchor names (D4 rule 3). `now` is the moment of
    the turn, local and naive: dates and times are validated against it (D5.1, D5.2). It has no
    default, so every caller, tests included, says which moment it validates against; only the
    turn node reads the clock. `session_id` goes into every idempotency key (D9.1); it has no
    default either, so two sessions never share keys by a forgotten argument.
    """

    message: str
    now: datetime
    session_id: str


@dataclass
class TurnOutcome:
    reply: str
    state_delta: dict[str, Any] = field(default_factory=dict)  # empty = state unchanged (D1.1)
    route: str = ""  # which branch decided the turn: "R2", "confirmed", ... (tests and the log)
    changes: list[Change] = field(default_factory=list)  # for the log (D4 rule 8)
    rejected: list[Rejected] = field(default_factory=list)  # for the log (D4 rule 3)
    invalid: list[dict] = field(default_factory=list)  # the record's `invalid`, for the log (D5)
    results: list[StepRun] = field(default_factory=list)  # per step tried after a "yes" (D9)
    depends: list[str] | None = None  # the plan's dependencies, only when a turn builds one (D8.1)
