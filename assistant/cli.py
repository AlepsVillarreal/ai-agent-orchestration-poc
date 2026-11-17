"""Command-line chat with the agent: `uv run python -m assistant.cli [--session ID]`.

One stdin line is one new user message (never an `invocation_id`: resumability stays off,
D1.1). Sessions live in Redis, so `--session ID` continues a conversation in a new process.
stdout carries only the conversation; the turn log and notices go to stderr.

Startup, in order: load `.env`; check Redis; check `GOOGLE_API_KEY`. Each check exits with
code 1 and a clear message instead of a traceback.
"""

import argparse
import asyncio
import logging
import os
import sys
import uuid
from collections.abc import Iterable, Iterator
from typing import TextIO
from urllib.parse import urlsplit

import redis.asyncio as redis_asyncio
from dotenv import load_dotenv
from google.adk import Runner
from google.adk.integrations.redis import RedisSessionService
from google.genai import types

from assistant.agent import app

DEFAULT_REDIS_URL = "redis://localhost:6379/0"  # the one in .env.example
USER_ID = "cli"
INTERPRETER = "interpreter"  # its events carry the JSON interpretation, never shown (D1.1)


def event_text(event) -> str:
    if not event.content or not event.content.parts:
        return ""
    return "".join(part.text or "" for part in event.content.parts if not part.thought)


async def chat(
    runner: Runner,
    user_id: str,
    session_id: str,
    lines: Iterable[str],
    out: TextIO,
    echo: bool,
) -> None:
    """Send each non-blank line as a new message and print the agent's replies.

    Only events with text whose author is not the interpreter are printed (D1.1, *Costos
    aceptados*). With `echo`, each line is printed as `> line` first, so piped output reads as
    a transcript.
    """
    for line in lines:
        text = line.strip()
        if not text:
            continue
        if echo:
            print(f"> {text}", file=out)
        message = types.Content(role="user", parts=[types.Part(text=text)])
        async for event in runner.run_async(
            user_id=user_id, session_id=session_id, new_message=message
        ):
            reply = event_text(event)
            if reply and event.author != INTERPRETER:
                print(reply, file=out)
        out.flush()


def _prompted_lines() -> Iterator[str]:
    while True:
        try:
            yield input("> ")
        except EOFError:
            return


def _without_password(url: str) -> str:
    """The URL for an error message: a password in it is never printed."""
    try:
        parts = urlsplit(url)
        if parts.password is None:
            return url
        host = parts.hostname or ""
        netloc = f"***@{host}:{parts.port}" if parts.port else f"***@{host}"
    except ValueError:  # e.g. a port that is not a number: show nothing of it
        return "(URL no válida)"
    return parts._replace(netloc=netloc).geturl()


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


async def _run(session_id: str | None) -> int:
    redis_url = os.environ.get("REDIS_URL") or DEFAULT_REDIS_URL
    client = None
    try:
        try:
            # Created inside the `try`: a malformed URL raises here, not on the ping.
            client = redis_asyncio.from_url(
                redis_url, decode_responses=True, socket_connect_timeout=2
            )
            await client.ping()
        except Exception as ex:  # malformed URL, connection refused, timeout, auth
            return _fail(
                f"No pude conectar con Redis en REDIS_URL={_without_password(redis_url)} "
                f"({type(ex).__name__}). "
                "Levántalo con `docker compose up -d redis` o corrige REDIS_URL en .env."
            )

        if not os.environ.get("GOOGLE_API_KEY"):
            return _fail("Falta GOOGLE_API_KEY en .env (ver .env.example).")

        if session_id is None:
            session_id = uuid.uuid4().hex
            print(
                f"Sesión nueva: {session_id}. Para continuarla: "
                f"uv run python -m assistant.cli --session {session_id}",
                file=sys.stderr,
            )

        runner = Runner(
            app=app,
            session_service=RedisSessionService(redis_client=client),
            auto_create_session=True,
        )
        interactive = sys.stdin.isatty()
        lines = _prompted_lines() if interactive else sys.stdin
        await chat(runner, USER_ID, session_id, lines, sys.stdout, echo=not interactive)
        return 0
    finally:
        if client is not None:
            await client.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Conversa con el agente por stdin.")
    parser.add_argument("--session", help="id de una sesión guardada para continuarla")
    args = parser.parse_args(argv)

    load_dotenv()
    # Root at WARNING so ADK's own logging does not flood the output; our turn log at INFO.
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(name)s %(message)s")
    logging.getLogger("assistant").setLevel(logging.INFO)

    try:
        return asyncio.run(_run(args.session))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
