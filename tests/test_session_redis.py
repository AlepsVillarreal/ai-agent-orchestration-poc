"""The structured state survives a new process: sessions saved in and reloaded from Redis.

A new `RedisSessionService` with a new client stands in for a new process (RF-07, TASK.md line
227; D1.1 spike; D6; D14).
"""

import dataclasses
import json
import uuid

import redis
import redis.asyncio as redis_asyncio
from google.adk import Runner
from google.adk.apps import App
from google.adk.integrations.redis import RedisSessionService

from assistant.agent import build_root_agent
from assistant.executions import RedisExecutions
from assistant.interpreter import build_interpreter
from core import replies
from core.executions import execution_key
from core.instruction import pending_summary
from core.interpretation import TurnInterpretation
from core.registry import TOOLS
from core.turn import TurnInput, handle_turn
from tests.fake_llm import ScriptedLlm, event_text, quota_error, reply_events, run_turn, system_text
from tests.interpretations import FIXED, NOW, SESSION

APP, USER = "assistant", "test"


def runner_for(fake: ScriptedLlm, service: RedisSessionService) -> Runner:
    app = App(name=APP, root_agent=build_root_agent(build_interpreter(fake)))
    return Runner(app=app, session_service=service, auto_create_session=True)


def new_client(redis_url: str) -> redis_asyncio.Redis:
    return redis_asyncio.from_url(redis_url, decode_responses=True, socket_connect_timeout=1)


def without_id(record: dict) -> dict:
    """A record without its `operation_id`, a new uuid on every `handle_turn` call (task 10a)."""
    return {key: value for key, value in record.items() if key != "operation_id"}


# 6
async def test_state_survives_reload_from_redis(redis_url):
    session_id = f"test-{uuid.uuid4().hex}"
    a1_outcome = handle_turn(
        None,
        TurnInterpretation.model_validate(FIXED["A1"].interpretation),
        TurnInput(FIXED["A1"].message, NOW, SESSION),
    )
    record = a1_outcome.state_delta["pending"]
    # Task 08: B2 merges into the reloaded record (*Misma tool*).
    b2_outcome = handle_turn(
        record,
        TurnInterpretation.model_validate(FIXED["B2"].interpretation),
        TurnInput(FIXED["B2"].message, NOW, SESSION),
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
        stored = session.state["pending"]
        assert without_id(stored) == without_id(record)  # the 429 did not change it
        assert any(event.error_code for event in session.events)

        fake2 = ScriptedLlm(script=[json.dumps(FIXED["B2"].interpretation)])
        b2 = reply_events(
            await run_turn(runner_for(fake2, second), USER, session_id, FIXED["B2"].message)
        )
        assert [event_text(event) for event in b2] == [b2_outcome.reply]

        system = system_text(fake2.requests[0])
        assert pending_summary(record) in system  # D14: the summary reads the reloaded state
        assert "servidor" not in system  # D2: no collected value reaches the model
        assert stored["operation_id"] not in system  # the summary never shows the id (D14)

        session = await second.get_session(app_name=APP, user_id=USER, session_id=session_id)
        reloaded = session.state["pending"]
        assert without_id(reloaded) == without_id(b2_outcome.state_delta["pending"])
        assert reloaded["operation_id"] == stored["operation_id"]  # the same operation
    finally:
        await second.delete_session(app_name=APP, user_id=USER, session_id=session_id)
        await first_client.aclose()
        await second_client.aclose()


# 17 (brief 10a): after the "yes", a new service instance reloads `pending` as None.
# Brief 10b: and `history` with the entry the turn wrote (D6; RF-07, TASK.md lines 220-227).
async def test_confirmed_operation_is_gone_after_reload(redis_url, monkeypatch):
    calls = []

    def recorder(args, real=TOOLS["send_notification"].execute):
        calls.append(args)
        return real(args)

    spec = TOOLS["send_notification"]
    monkeypatch.setitem(TOOLS, spec.name, dataclasses.replace(spec, execute=recorder))
    session_id = f"test-{uuid.uuid4().hex}"
    first_client, second_client = new_client(redis_url), new_client(redis_url)
    first = RedisSessionService(redis_client=first_client)
    second = RedisSessionService(redis_client=second_client)
    try:
        fake = ScriptedLlm(
            script=[json.dumps(FIXED["G1"].interpretation), json.dumps(FIXED["G2"].interpretation)]
        )
        runner = runner_for(fake, first)
        await run_turn(runner, USER, session_id, FIXED["G1"].message)
        session = await second.get_session(app_name=APP, user_id=USER, session_id=session_id)
        assert session.state["pending"]["status"] == "awaiting_confirmation"

        events = await run_turn(runner, USER, session_id, FIXED["G2"].message)

        session = await second.get_session(app_name=APP, user_id=USER, session_id=session_id)
        assert "pending" in session.state and session.state["pending"] is None
        assert len(calls) == 1
        [answer] = reply_events(events)
        written = answer.actions.state_delta["history"]
        assert len(written) == 1 and written[0]["steps"][0]["values"] == calls[0]
        assert session.state["history"] == written
    finally:
        await second.delete_session(app_name=APP, user_id=USER, session_id=session_id)
        await first_client.aclose()
        await second_client.aclose()


# Brief 11 -------------------------------------------------------------------------------------


def sync_client(redis_url: str) -> redis.Redis:
    return redis.Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=1)


# 17 (D9.1; decision D): `SET NX` with a 24 h TTL, `done` survives a new process, `DEL` releases.
def test_redis_executions_reserve_is_set_nx_with_ttl(redis_url):
    key = execution_key(f"test-{uuid.uuid4().hex}", "create_ticket", {"title": "x"})
    stored_key = f"idem:{key}"
    result = {"success": True, "ticket_id": "TCK-1", "message": "listo"}
    first, second = sync_client(redis_url), sync_client(redis_url)
    try:
        executions = RedisExecutions(first)

        assert executions.reserve(key) is None
        assert 0 < first.ttl(stored_key) <= 86400
        assert executions.reserve(key) == {"status": "started"}

        executions.complete(key, result)
        assert 0 < first.ttl(stored_key) <= 86400
        assert executions.reserve(key) == {"status": "done", "result": result}
        # Another instance on a new client, as a new process would have.
        assert RedisExecutions(second).reserve(key) == {"status": "done", "result": result}

        executions.release(key)
        assert first.exists(stored_key) == 0
        assert first.exists(key) == 0  # never stored without its prefix
        # Review 11-1, note 4: `SET XX` never brings back a released key.
        executions.complete(key, result)
        assert first.exists(stored_key) == 0
    finally:
        first.delete(stored_key)
        first.close()
        second.close()


# 18 (c on Redis; D9 *Caso H*): the turn that ran was not saved and the process is new: the
# resent "yes" finds `done` and nothing runs again.
def test_resent_yes_survives_a_new_process(redis_url, monkeypatch):
    calls = []

    def recorder(args, real=TOOLS["create_ticket"].execute):
        calls.append(args)
        return real(args)

    spec = TOOLS["create_ticket"]
    monkeypatch.setitem(TOOLS, spec.name, dataclasses.replace(spec, execute=recorder))
    session_id = f"test-{uuid.uuid4().hex}"
    yes = TurnInterpretation.model_validate(FIXED["B4"].interpretation)
    turn_input = TurnInput(FIXED["B4"].message, NOW, session_id)
    record = handle_turn(
        None,
        TurnInterpretation.model_validate(FIXED["H1"].interpretation),
        TurnInput(FIXED["H1"].message, NOW, session_id),
    ).state_delta["pending"]
    first, second = sync_client(redis_url), sync_client(redis_url)
    try:
        ran = handle_turn(record, yes, turn_input, executions=RedisExecutions(first))
        resent = handle_turn(record, yes, turn_input, executions=RedisExecutions(second))

        assert len(calls) == 1
        [run], [reused] = ran.results, resent.results
        assert (run.outcome, reused.outcome) == ("ran", "reused")
        assert reused.result_id == run.result_id
    finally:
        if calls:
            first.delete(f"idem:{execution_key(session_id, 'create_ticket', calls[0])}")
        first.close()
        second.close()
