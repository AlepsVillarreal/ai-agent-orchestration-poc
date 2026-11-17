"""Spike D1.1: one dynamic node hosts the turn. Fake model, no API key, real Redis.

Five turns on one session: valid, invalid JSON, empty output, simulated 429, valid.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, AsyncGenerator, Optional

import redis.asyncio as redis_asyncio
from google.adk import Context, Event, Runner, Workflow
from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.integrations.redis import RedisSessionService, RedisSessionServiceConfig
from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.adk.workflow import node
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, ValidationError

PORT = int(sys.argv[1])
APP, USER, SID = "spike_d11", "u1", "s1"


# ---------- pure-Python core (no ADK imports are used here) ----------
class TurnInterpretation(BaseModel):
  act: str
  title: Optional[str] = None


def core(flow: dict, interp: dict) -> tuple[str, dict]:
  new_flow = {
      "ok_turns": flow.get("ok_turns", 0) + 1,
      "titles": [*flow.get("titles", []), interp.get("title")],
  }
  return f"Entendido ({interp['act']}).", {"flow": new_flow}


# ---------- fake model ----------
SCRIPT = [
    ("valid", '{"act": "request", "title": "t1"}'),
    ("invalid_json", '{"act": 123, "title": '),
    ("empty", ""),
    ("429", None),
    ("valid", '{"act": "request", "title": "t5"}'),
]


class FakeLlm(BaseLlm):
  calls: int = 0
  seen_texts: list = []

  async def generate_content_async(
      self, llm_request: LlmRequest, stream: bool = False
  ) -> AsyncGenerator[LlmResponse, None]:
    kind, text = SCRIPT[self.calls]
    self.calls += 1
    self.seen_texts.append(
        [p.text for c in llm_request.contents for p in (c.parts or []) if p.text]
    )
    if kind == "429":
      raise genai_errors.ClientError(
          429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota"}}
      )
    yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))


fake = FakeLlm(model="fake")
interpreter = LlmAgent(
    name="interpreter",
    model=fake,
    instruction="Interpret. Pending: {pending?}",
    output_schema=TurnInterpretation,
    include_contents="none",
)

BODY_RUNS = {"n": 0}


# ---------- the only adapter ----------
@node(rerun_on_resume=True)
async def turn(ctx: Context, node_input: Any):
  BODY_RUNS["n"] += 1
  flow = dict(ctx.state.get("flow") or {})
  try:
    interp = await ctx.run_node(interpreter, node_input=node_input)
  except Exception as ex:  # noqa: BLE001
    inner = getattr(ex, "error", ex)
    if isinstance(inner, ValidationError):
      return Event(message="No te entendí.")
    if getattr(ex, "status_code", None) == 429:
      return Event(message="Modelo no disponible (cuota).")
    return Event(message="Modelo no disponible.")
  if not interp:
    return Event(message="No te entendí.")
  reply, delta = core(flow, interp)
  return Event(message=reply, state=delta)


root_agent = Workflow(name="root_agent", edges=[("START", turn)])
app = App(name=APP, root_agent=root_agent)


def new_service() -> RedisSessionService:
  return RedisSessionService(
      config=RedisSessionServiceConfig(host="127.0.0.1", port=PORT),
  )


def text_of(ev) -> str:
  if not ev.content or not ev.content.parts:
    return ""
  return "".join(p.text or "" for p in ev.content.parts)


async def main() -> int:
  svc = new_service()
  await svc.create_session(app_name=APP, user_id=USER, session_id=SID)
  runner = Runner(app=app, session_service=svc)
  assert app.resumability_config is None

  rows, failures = [], []
  prev_state: dict = {}
  for i, (kind, _) in enumerate(SCRIPT, start=1):
    runs_before, calls_before = BODY_RUNS["n"], fake.calls
    events, raised = [], None
    try:
      async for ev in runner.run_async(
          user_id=USER,
          session_id=SID,
          new_message=types.Content(role="user", parts=[types.Part(text=f"mensaje {i}")]),
      ):
        events.append(ev)
    except Exception as ex:  # noqa: BLE001
      raised = repr(ex)

    # what the CLI would print: events with text, not authored by the interpreter
    replies = [e for e in events if text_of(e) and e.author != "interpreter"]
    reply_delta = dict(replies[0].actions.state_delta) if replies else None
    err_events = [e for e in events if e.error_code]

    # reload from Redis with a brand-new service instance
    fresh = await new_service().get_session(app_name=APP, user_id=USER, session_id=SID)
    state = dict(fresh.state)
    stored_err = [e for e in fresh.events if e.error_code and e.invocation_id == events[0].invocation_id] if events else []

    row = {
        "turn": i,
        "kind": kind,
        "raised": raised,
        "events": [(e.author, text_of(e)[:40], e.error_code, dict(e.actions.state_delta)) for e in events],
        "reply": [text_of(e) for e in replies],
        "reply_delta": reply_delta,
        "state_after": state,
        "state_changed": state != prev_state,
        "err_events_stream": len(err_events),
        "err_events_reloaded": len(stored_err),
        "body_runs": BODY_RUNS["n"] - runs_before,
        "model_calls": fake.calls - calls_before,
        "model_saw": fake.seen_texts[-1] if fake.calls > calls_before else None,
    }
    rows.append(row)

    ok = kind == "valid"
    if raised:
      failures.append(f"T{i}: exception escaped run_async: {raised}")
    if len(replies) != 1:
      failures.append(f"T{i}: expected exactly 1 reply event, got {len(replies)}")
    elif ok and not reply_delta:
      failures.append(f"T{i}: valid turn reply carries no state delta")
    elif not ok and reply_delta:
      failures.append(f"T{i}: failed turn reply carries a state delta {reply_delta}")
    if not ok and state != prev_state:
      failures.append(f"T{i}: state changed on a failed turn")
    if ok and state == prev_state:
      failures.append(f"T{i}: state did not change on a valid turn")
    if kind == "429" and replies and "no disponible" not in text_of(replies[0]):
      failures.append(f"T{i}: 429 did not get the 'model unavailable' reply")
    if kind in ("invalid_json", "429") and len(stored_err) < 1:
      failures.append(f"T{i}: error_code event not found after reload from Redis")
    if row["body_runs"] != 1:
      failures.append(f"T{i}: body ran {row['body_runs']} times")
    prev_state = state

  # raw check of what Redis holds
  r = redis_asyncio.Redis(host="127.0.0.1", port=PORT)
  keys = [k.decode() for k in await r.keys("adk:session:*")]
  raw = json.loads(await r.get(keys[0]))
  await r.aclose()

  for row in rows:
    print(json.dumps(row, ensure_ascii=False, default=str))
  print("redis_keys:", keys)
  print("stored_events:", len(raw.get("events", [])),
        "with_error_code:", sum(1 for e in raw.get("events", []) if e.get("error_code") or e.get("errorCode")))
  print("final_state:", rows[-1]["state_after"])
  print("total_body_runs:", BODY_RUNS["n"], "total_model_calls:", fake.calls)
  print("RESULT:", "PASS" if not failures else "FAIL")
  for f in failures:
    print("  -", f)
  return 0 if not failures else 1


if __name__ == "__main__":
  sys.exit(asyncio.run(main()))
