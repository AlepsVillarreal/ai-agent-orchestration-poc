"""Simulated (mock) implementations of the three tools.

These are plain Python functions, not ADK `FunctionTool`s: the LLM never sees or calls them
(D1). Deterministic code calls them once the arguments are validated and confirmed.

Each mock first passes its arguments through the tool's contract, so even if every earlier
check were skipped, invalid arguments raise `ValidationError` and nothing is executed.
"""

import uuid
from typing import Any

from core.contracts import CreateTicketArgs, ScheduleMeetingArgs, SendNotificationArgs


def _new_id(prefix: str) -> str:
    """Random ID such as "TCK-3F9A12BC".

    Random instead of a counter so IDs never repeat after a process restart: results are kept
    in Redis for 24 h (D9.1), and a counter starting again at 1 would reuse old IDs.
    """
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


def create_ticket(args: dict[str, Any]) -> dict[str, Any]:
    ticket = CreateTicketArgs.model_validate(args)
    ticket_id = _new_id("TCK")
    return {
        "success": True,
        "ticket_id": ticket_id,
        "message": f"Ticket {ticket_id} creado con prioridad {ticket.priority} "
        f"y asignado a {ticket.assignee}.",
    }


def schedule_meeting(args: dict[str, Any]) -> dict[str, Any]:
    meeting = ScheduleMeetingArgs.model_validate(args)
    meeting_id = _new_id("MTG")
    return {
        "success": True,
        "meeting_id": meeting_id,
        "message": f"Reunión {meeting_id} programada el {meeting.date} a las {meeting.time} "
        f"({meeting.duration_minutes} min) con {', '.join(meeting.attendees)}.",
    }


def send_notification(args: dict[str, Any]) -> dict[str, Any]:
    notification = SendNotificationArgs.model_validate(args)
    notification_id = _new_id("NTF")
    return {
        "success": True,
        "notification_id": notification_id,
        "message": f"Notificación {notification_id} enviada a {notification.recipient} "
        f"por {notification.channel}.",
    }
