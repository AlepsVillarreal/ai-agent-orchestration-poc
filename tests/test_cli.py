"""The CLI: the chat loop with a fake model, and startup errors in a subprocess.

The subprocess tests give the CLI empty stdin, so no turn ever reaches a model, and set
`GOOGLE_API_KEY=""` so `load_dotenv` cannot fill it from `.env` (it never overrides a variable
that is already set).
"""

import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import redis
import redis.asyncio as redis_asyncio
from google.adk import Runner
from google.adk.apps import App
from google.adk.integrations.redis import RedisSessionService
from google.adk.sessions import InMemorySessionService

import assistant.cli
from assistant.agent import build_root_agent
from assistant.cli import _without_password, chat
from assistant.interpreter import build_interpreter
from core.executions import execution_key
from core.instruction import pending_summary
from core.interpretation import TurnInterpretation
from core.registry import TOOLS
from core.turn import TurnInput, handle_turn
from tests.fake_llm import ScriptedLlm, system_text
from tests.interpretations import FIXED, NOW, SESSION
from tests.node_helpers import h1_values

ROOT = Path(__file__).resolve().parent.parent


def run_cli(**env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "assistant.cli"],
        cwd=ROOT,
        env={**os.environ, "GOOGLE_API_KEY": "", **env},
        input="",
        capture_output=True,
        text=True,
        timeout=20,
    )


# 7
async def test_chat_prints_only_replies():
    fake = ScriptedLlm(script=[json.dumps(FIXED["A1"].interpretation)])
    app = App(name="assistant", root_agent=build_root_agent(build_interpreter(fake)))
    runner = Runner(app=app, session_service=InMemorySessionService(), auto_create_session=True)
    out = io.StringIO()

    await chat(runner, "cli", "s-cli", [FIXED["A1"].message, "   ", ""], out, echo=True)

    text = out.getvalue()
    question = handle_turn(
        None,
        TurnInterpretation.model_validate(FIXED["A1"].interpretation),
        TurnInput(FIXED["A1"].message, NOW, SESSION),
    ).reply
    assert text == f"> {FIXED['A1'].message}\n{question}\n"
    assert "{" not in text and '"act"' not in text  # nothing from the interpreter
    assert fake.calls == 1  # blank lines are not turns


# 8
@pytest.mark.parametrize("url", ["redis://127.0.0.1:1/0", "notaurl"], ids=["down", "malformed"])
def test_cli_exits_clearly_when_redis_is_down(url):
    result = run_cli(REDIS_URL=url)

    assert result.returncode == 1
    assert "Redis" in result.stderr
    assert "docker compose up -d redis" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


# 9
def test_cli_exits_clearly_without_api_key(redis_url):
    result = run_cli(REDIS_URL=redis_url)

    assert result.returncode == 1
    assert "GOOGLE_API_KEY" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


def _new_session_ids(stderr: str) -> list[str]:
    return re.findall(r"Sesión nueva: (\w+)\.", stderr)


async def _cli_sessions(redis_url: str) -> set[str]:
    """The ids of every session of the CLI's app and user stored in Redis."""
    client = redis_asyncio.from_url(redis_url, decode_responses=True)
    try:
        listed = await RedisSessionService(redis_client=client).list_sessions(
            app_name="assistant", user_id=assistant.cli.USER_ID
        )
        return {session.id for session in listed.sessions}
    finally:
        await client.aclose()


async def _delete_sessions_not_in(redis_url: str, before: set[str]) -> None:
    """Note (k): delete every CLI session the test created, found by listing them (not by
    reading stderr, so a run that failed before printing its id leaves nothing behind)."""
    client = redis_asyncio.from_url(redis_url, decode_responses=True)
    try:
        service = RedisSessionService(redis_client=client)
        listed = await service.list_sessions(app_name="assistant", user_id=assistant.cli.USER_ID)
        for session in listed.sessions:
            if session.id not in before:
                await service.delete_session(
                    app_name="assistant", user_id=assistant.cli.USER_ID, session_id=session.id
                )
    finally:
        await client.aclose()


# Review 04-1, note 2: the wiring of `_run` (Redis service, `auto_create_session`, a new
# session per run, `--session` continues it). In process, with the app's model replaced by the
# scripted fake, so no turn reaches the real model.
async def test_run_creates_a_session_per_run_and_continues_it(redis_url, monkeypatch, capsys):
    fake = ScriptedLlm(
        script=[json.dumps(FIXED["A1"].interpretation), json.dumps(FIXED["B2"].interpretation)]
    )
    # Task 11: the CLI builds its own agent (with the Redis registry), so the interpreter is
    # what gets replaced.
    monkeypatch.setattr(assistant.cli, "interpreter", build_interpreter(fake))
    monkeypatch.setenv("REDIS_URL", redis_url)
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-key-never-sent")  # only passes the startup check

    before = await _cli_sessions(redis_url)
    try:
        monkeypatch.setattr(sys, "stdin", io.StringIO(FIXED["A1"].message + "\n"))
        assert await assistant.cli._run(None) == 0
        first = capsys.readouterr()
        [session_id] = _new_session_ids(first.err)
        a1 = handle_turn(
            None,
            TurnInterpretation.model_validate(FIXED["A1"].interpretation),
            TurnInput(FIXED["A1"].message, NOW, SESSION),
        )
        assert first.out == f"> {FIXED['A1'].message}\n{a1.reply}\n"

        monkeypatch.setattr(sys, "stdin", io.StringIO(FIXED["B2"].message + "\n"))
        assert await assistant.cli._run(session_id) == 0
        second = capsys.readouterr()
        assert _new_session_ids(second.err) == []  # --session continues, no new session
        # Task 08: B2 merges into the record the first process saved (*Misma tool*).
        b2 = handle_turn(
            a1.state_delta["pending"],
            TurnInterpretation.model_validate(FIXED["B2"].interpretation),
            TurnInput(FIXED["B2"].message, NOW, SESSION),
        )
        assert b2.route == "same_tool"
        assert second.out == f"> {FIXED['B2'].message}\n{b2.reply}\n"
        # The second process saw the pending record saved by the first one.
        assert pending_summary(a1.state_delta["pending"]) in system_text(fake.requests[1])

        # Without --session, every run gets its own new session (empty stdin: no turn).
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        assert await assistant.cli._run(None) == 0
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        assert await assistant.cli._run(None) == 0
        ids = _new_session_ids(capsys.readouterr().err)
        assert len(ids) == 2 and len({session_id, *ids}) == 3
        assert fake.calls == 2
    finally:
        await _delete_sessions_not_in(redis_url, before)


@pytest.mark.parametrize(
    ("url", "shown"),
    [
        ("redis://localhost:6379/0", "redis://localhost:6379/0"),
        ("redis://user:s3cret@host:6380/1", "redis://***@host:6380/1"),
        ("redis://:s3cret@host/0", "redis://***@host/0"),
        ("redis://:s3cret@host:notaport/0", "(URL no válida)"),
        ("notaurl", "notaurl"),
    ],
)
def test_redis_url_in_messages_never_shows_a_password(url, shown):
    assert _without_password(url) == shown


# Brief 11 -------------------------------------------------------------------------------------


# 19: the CLI keeps its executions in Redis, and `MOCK_FAIL` makes a tool answer a failure.
async def test_cli_uses_the_redis_registry_and_mock_fail(redis_url, monkeypatch, capsys):
    for name in list(TOOLS):  # the CLI replaces tools in TOOLS; monkeypatch restores them
        monkeypatch.setitem(TOOLS, name, TOOLS[name])
    script = [json.dumps(FIXED[key].interpretation) for key in ("H1", "B4", "H1", "B4")]
    fake = ScriptedLlm(script=script)
    monkeypatch.setattr(assistant.cli, "interpreter", build_interpreter(fake))
    monkeypatch.setenv("REDIS_URL", redis_url)
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-key-never-sent")  # only passes the startup check
    monkeypatch.delenv("MOCK_FAIL", raising=False)
    conversation = f"{FIXED['H1'].message}\n{FIXED['B4'].message}\n"
    values = h1_values()
    check = redis.Redis.from_url(redis_url, decode_responses=True)
    before = await _cli_sessions(redis_url)
    keys = []
    try:
        monkeypatch.setattr(sys, "stdin", io.StringIO(conversation))
        assert await assistant.cli._run(None) == 0
        captured = capsys.readouterr()
        [session_id] = _new_session_ids(captured.err)
        keys.append(f"idem:{execution_key(session_id, 'create_ticket', values)}")
        assert "Listo: creé el ticket " in captured.out
        assert json.loads(check.get(keys[0]))["status"] == "done"
        assert 0 < check.ttl(keys[0]) <= 86400

        monkeypatch.setenv("MOCK_FAIL", "create_ticket")
        monkeypatch.setattr(sys, "stdin", io.StringIO(conversation))
        assert await assistant.cli._run(None) == 0
        captured = capsys.readouterr()
        [session_id] = _new_session_ids(captured.err)
        keys.append(f"idem:{execution_key(session_id, 'create_ticket', values)}")
        assert "MOCK_FAIL: create_ticket" in captured.err
        assert captured.out.endswith(
            "No pude crear un ticket: la herramienta respondió con un error. "
            "Si me lo pides de nuevo, lo intento otra vez.\n"
        )
        assert "Listo" not in captured.out
        assert check.exists(keys[1]) == 0  # D9.3: released
        assert fake.calls == 4
    finally:
        if keys:
            check.delete(*keys)
        check.close()
        await _delete_sessions_not_in(redis_url, before)


def test_cli_exits_clearly_with_an_unknown_mock_fail(redis_url):
    result = run_cli(REDIS_URL=redis_url, GOOGLE_API_KEY="fake-key-never-sent", MOCK_FAIL="pizza")

    assert result.returncode == 1
    assert "MOCK_FAIL" in result.stderr and "pizza" in result.stderr
    assert "create_ticket" in result.stderr  # the names it accepts
    assert "Traceback" not in result.stderr
    assert result.stdout == ""
