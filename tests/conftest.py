import os

import pytest
import redis

DEFAULT_REDIS_URL = "redis://localhost:6379/0"


@pytest.fixture(autouse=True)
def no_model_credentials(monkeypatch):
    """Tests never call the real model: without a key, an accidental call fails instead of
    spending quota."""
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


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


@pytest.fixture
def redis_url() -> str:
    """The URL of a live Redis, or a clear failure (not a timeout) when it is down.

    Not autouse: only the tests that need Redis ask for it. Each test creates its own async
    client on its own event loop, uses a unique session id and deletes its session at the end.
    """
    url = os.environ.get("REDIS_URL") or DEFAULT_REDIS_URL
    client = redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
    try:
        client.ping()
        alive = True
    except redis.RedisError:
        alive = False
    finally:
        client.close()
    if not alive:  # failed outside the `except`, so no chained connection traceback is shown
        pytest.fail(
            f"Redis no responde en {url}: levántalo con `docker compose up -d redis`",
            pytrace=False,
        )
    return url
