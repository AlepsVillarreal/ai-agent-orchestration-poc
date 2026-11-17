import pytest


@pytest.fixture
def canonical_args() -> dict[str, dict]:
    """One valid, already-normalized set of arguments per tool."""
    return {
        "create_ticket": {
            "title": "Servidor caído",
            "description": "El servidor de producción no responde",
            "priority": "high",
            "assignee": "Carlos",
        },
        "schedule_meeting": {
            "title": "Revisión del servidor",
            "date": "2026-11-20",
            "time": "10:30",
            "duration_minutes": 30,
            "attendees": ["Juan", "Ana"],
        },
        "send_notification": {
            "recipient": "Carlos",
            "message": "El ticket quedó resuelto",
            "channel": "email",
        },
    }
