"""Argument contracts of the three tools (TASK.md §2.1), as Pydantic models.

A contract describes the *already normalized* values a tool receives: "high", not "alta";
"2026-11-20", not "el viernes". It checks shape only (required fields, types, allowed values,
formats). Business and coherence rules (duration range, past dates, mixed date forms) belong to
the validator, which also explains the problem to the user. The contract is the last barrier
before a tool runs: if a value breaks it, Pydantic raises `ValidationError` and nothing runs.
"""

import re
from datetime import date
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictInt, StringConstraints

# Text that is not empty once surrounding spaces are removed.
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

Priority = Literal["low", "medium", "high"]
Channel = Literal["email", "sms", "push"]

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _check_iso_date(value: str) -> str:
    """Accept only `YYYY-MM-DD` that is a real calendar day ("2026-11-40" fails)."""
    if not _ISO_DATE.fullmatch(value):
        raise ValueError("date must have the form YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("date does not exist in the calendar") from error
    return value


IsoDate = Annotated[str, AfterValidator(_check_iso_date)]

# HH:mm from 00:00 to 23:59. Pydantic's `pattern` searches, so the anchors are required.
HourMinute = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]


class _Contract(BaseModel):
    # An argument that is not in the contract is an error, not something to ignore silently.
    model_config = ConfigDict(extra="forbid")


class CreateTicketArgs(_Contract):
    title: NonEmptyText
    description: NonEmptyText
    priority: Priority
    assignee: NonEmptyText


class ScheduleMeetingArgs(_Contract):
    title: NonEmptyText
    date: IsoDate
    time: HourMinute
    duration_minutes: StrictInt  # strict: "30" or True are not integers here
    attendees: Annotated[list[NonEmptyText], Field(min_length=1)]


class SendNotificationArgs(_Contract):
    recipient: NonEmptyText
    message: NonEmptyText
    channel: Channel
