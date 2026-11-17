"""The structured state survives a new process: sessions saved in and reloaded from Redis.

A new `RedisSessionService` with a new client stands in for a new process (RF-07, TASK.md line
227; D1.1 spike; D6; D14).
"""

import json
import uuid

import redis.asyncio as redis_asyncio
from google.adk import Runner
from google.adk.apps import App
from google.adk.integrations.redis import RedisSessionService

from assistant.agent import build_root_agent
from assistant.interpreter import build_interpreter
from core import replies
from core.instruction import pending_summary
from core.interpretation import TurnInterpretation
from core.turn import TurnInput, handle_turn
from tests.fake_llm import ScriptedLlm, event_text, quota_error, reply_events, run_turn, system_text
from tests.interpretations import FIXED

APP, USER = "assistant", "test"


def runner_for(fake: ScriptedLlm, service: RedisSessionService) -> Runner:
    app = App(name=APP, root_agent=build_root_agent(build_interpreter(fake)))
    return Runner(app=app, session_service=service, auto_create_session=True)


def new_client(redis_url: str) -> redis_asyncio.Redis:
    return redis_asyncio.from_url(redis_url, decode_responses=True, socket_connect_timeout=1)


# 6
async def test_state_survives_reload_from_redis(redis_url):
    session_id = f"test-{uuid.uuid4().hex}"
    a1_outcome = handle_turn(
        None,
        TurnInterpretation.model_validate(FIXED["A1"].interpretation),
        TurnInput(FIXED["A1"].message),
    )
    record = a1_outcome.state_delta["pending"]
    # Task 08: B2 merges into the reloaded record (*Misma tool*).
    b2_outcome = handle_turn(
        record,
        TurnInterpretation.model_validate(FIXED["B2"].interpretation),
        TurnInput(FIXED["B2"].message),
    )

    first_client, second_client = new_client(redis_url), new_client(redis_url)
    first = RedisSessionService(redis_client=first_client)
    second = RedisSessionService(redis_client=second_client)  # as a new process would have
    try:
        fake = ScriptedLlm(script=[json.dumps(FIXED["A1"].interpretation), quota_error()])
        runner = runner_for(fake, first)
        a1 = reply_events(await run_turn(runner, USER, session_id, FIXED["A1"].message))
        assert [event_text(event) for event in a1] == [a1_outcome.reply]
        failed = reply_events(await run_turn(runner, USER, session_id, "otra cosa"))
        assert [event_text(event) for event in failed] == [replies.MODEL_UNAVAILABLE]

        session = await second.get_session(app_name=APP, user_id=USER, session_id=session_id)
        assert session.state["pending"] == record  # the 429 did not change it
        assert any(event.error_code for event in session.events)

        fake2 = ScriptedLlm(script=[json.dumps(FIXED["B2"].interpretation)])
        b2 = reply_events(
            await run_turn(runner_for(fake2, second), USER, session_id, FIXED["B2"].message)
        )
        assert [event_text(event) for event in b2] == [b2_outcome.reply]

        system = system_text(fake2.requests[0])
        assert pending_summary(record) in system  # D14: the summary reads the reloaded state
        assert "servidor" not in system  # D2: no collected value reaches the model

        session = await second.get_session(app_name=APP, user_id=USER, session_id=session_id)
        assert session.state["pending"] == b2_outcome.state_delta["pending"]
    finally:
        await second.delete_session(app_name=APP, user_id=USER, session_id=session_id)
        await first_client.aclose()
        await second_client.aclose()
