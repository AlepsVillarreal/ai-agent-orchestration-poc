"""The assessment of a pending record: its status, what is missing or invalid, and its question.

Moved out of `core/turn.py` unchanged (note (A18)), as `core/operations.py` was in task 13.
After every merge (R2 and *Misma tool*, `confirm` included) each step is validated
(`core/validation.py`, D5):
- something missing or invalid -> `status: collecting`; `missing` and `invalid`
  (`{tool, field, cause}`, never a value) come from the validator. One reply names the
  invalid fields (with their allowed values) after the change lines, then asks once for
  every missing or invalid field (`last_question`, D17: labels only).
- every step valid -> `status: awaiting_confirmation` with the hash of the shown values
  (`confirmation_hash`, D7.2). The reply shows the normalized values of each step and asks
  for a yes; `last_question` keeps only that question, without values (D43). Nothing runs
  in the turn a record becomes complete (D7.1).
Dates and times are resolved against the turn's `now` (D5.1, D5.2), so the shown values
(the ISO date of "mañana") and their hash depend on the moment of the turn. Nothing here reads
the message text or the clock.
"""

from datetime import datetime
from typing import Any

from core import replies
from core.confirmation import confirmation_hash, shown_values
from core.registry import TOOLS
from core.validation import validate

COLLECTING = "collecting"
AWAITING_CONFIRMATION = "awaiting_confirmation"


def assessment(steps: list[dict], now: datetime) -> dict[str, Any]:
    """`status`, `missing`, `invalid`, `last_question` and maybe `confirmation_hash` (D5, D7).

    `missing` is what is absent, plus a date or a time without a part the code must not
    complete (D5.1, D5.2); `invalid` lists `{tool, field, cause}` without the value. While
    something is missing or invalid, the question names every missing and invalid field of each
    step by its label, in registry order, and never carries a collected or an allowed value
    (D2, D14, D17). When every step is valid, the record awaits confirmation: the question names
    the actions only (D43) and the hash binds the values shown (D7.2), resolved against `now`.
    """
    missing, invalid, questions, ready = [], [], [], True
    for step in steps:
        tool = TOOLS[step["tool"]]
        validation = validate(tool, step["args"], now)
        ready = ready and validation.ready
        missing += validation.missing
        invalid += [
            {"tool": tool.name, "field": item.field, "cause": item.cause}
            for item in validation.invalid
        ]
        to_ask = validation.missing + [item.field for item in validation.invalid]
        if to_ask:
            questions.append(replies.missing_question(tool, to_ask))

    if ready:
        return {
            "status": AWAITING_CONFIRMATION,
            "missing": [],
            "invalid": [],
            "last_question": replies.confirmation_question([TOOLS[s["tool"]] for s in steps]),
            "confirmation_hash": confirmation_hash(shown_values(steps, now)),
        }
    return {
        "status": COLLECTING,
        "missing": missing,
        "invalid": invalid,
        "last_question": " ".join(questions),  # never empty: something is missing or invalid
    }


def reassessed(pending: dict, steps: list[dict], now: datetime) -> dict:
    """`pending` with `steps` and their assessment against `now`; the old hash never stays."""
    record = {key: value for key, value in pending.items() if key != "confirmation_hash"}
    record["steps"] = steps
    record.update(assessment(steps, now))
    return record


def closing_line(record: dict, now: datetime) -> str:
    """The confirmation (values shown, then the question) or the question.

    The shown values are recomputed from `args` against `now`, never stored (D44). A plan step
    that receives another step's ID says so after its values (D8.2).
    """
    if record["status"] == AWAITING_CONFIRMATION:
        steps = record["steps"]
        depends_on = [step.get("depends_on") for step in steps]
        summary = replies.confirmation_summary(shown_values(steps, now), depends_on)
        return f"{summary}\n{record['last_question']}"
    return record["last_question"]
