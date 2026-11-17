"""ADK adapter for the turn: the dynamic node that hosts one turn, the root workflow and the app.

The turn node is the only place where ADK meets the pure core (D1.1, *Frontera*). Each turn it:
1. reads the pending record from the session state (read only);
2. calls the interpreter as a child node with `ctx.run_node` (D1: the LLM only interprets);
3. hands the interpretation, the pending record, the message text, the moment of the turn and
   the session id (`TurnInput`), the closed operations (`history`, D6) and the execution
   registry (`executions`, D9) to `core.turn.handle_turn`;
4. returns exactly one `Event` with the template reply and the state change.

The node runs no tool and holds no rule of D9: it only passes the registry it was built with
(D46). The registry is the one thing written before the final event, on purpose: a crash
between a tool call and that event must not lose the fact that the tool was called (D9.1).

The state is written only through that final event (`Event(state=...)` becomes
`actions.state_delta`, which the `Runner` applies when it stores the event). Nothing writes to
`ctx.state` in the body, because a write made before an exception would be persisted anyway
(D1.1, *Estado*). A failed interpreter call answers with a template and no state change.

Importing this module opens no connection, needs no API key and does not load `.env`: only
the CLI does that (with a Redis registry), so tests never see the real key. The module's
`root_agent`, used by `adk web`, keeps its executions in memory.
"""

import json
import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from google.adk import Context, Event, Workflow
from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.workflow import node
from google.genai import types
from pydantic import ValidationError

from assistant.interpreter import interpreter
from core import replies
from core.executions import Executions, InMemoryExecutions, StepRun
from core.interpretation import TurnInterpretation
from core.merge import Change, Rejected
from core.turn import TurnInput, handle_turn

logger = logging.getLogger("assistant.turn")

APP_NAME = "assistant"  # same as the folder: `adk web` names each agent after its folder

NOT_UNDERSTOOD = "not_understood"
MODEL_UNAVAILABLE = "model_unavailable"
_FAILURE_REPLIES = {
    NOT_UNDERSTOOD: replies.NOT_UNDERSTOOD,
    MODEL_UNAVAILABLE: replies.MODEL_UNAVAILABLE,
}


def classify_failure(error: BaseException | None) -> str:
    """D1.1 *Fallos*, in order: a schema error or no output -> not understood; else unavailable.

    ADK wraps a child's exception in its own error, which keeps the original in `.error`; it is
    read with `getattr` so no private ADK module is imported. `None` means empty output.
    """
    if error is None or isinstance(getattr(error, "error", error), ValidationError):
        return NOT_UNDERSTOOD
    return MODEL_UNAVAILABLE


def message_text(node_input: Any) -> str:
    """The user's text from the turn node's input.

    For a new message the `Runner` hands the root workflow the `types.Content` it received
    (`workflow/_node_runner_utils.py:136-141`, google-adk 2.14.1), and the workflow passes it
    on to the node after START. Text parts are joined with a space so that two parts never glue
    two words into one (D4 rule 3 compares whole words). Anything else gives "": every name of
    the turn is then rejected, which costs a question, never an execution.
    """
    if isinstance(node_input, types.Content):
        return " ".join(part.text for part in node_input.parts or [] if part.text)
    return ""


def _value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def log_turn(
    session_id: str,
    route: str,
    interpretation: TurnInterpretation | None = None,
    error: str | None = None,
    changes: Sequence[Change] = (),
    rejected: Sequence[Rejected] = (),
    invalid: Sequence[dict] = (),
    operation_id: str | None = None,
    results: Sequence[StepRun] = (),
) -> None:
    """One `key=value` line per turn. `route` comes from the core, never from the reply text.

    `op` is the `operation_id` of the turn's record (the one the turn created or changed, else
    the one that was pending), when there is one; a uuid, never a user value.
    A turn that tried a tool after a "yes" adds, after `route=`, `results=` (`tool:ID:outcome`
    per step tried; `ID` is `-` when there is none, `outcome` is ran, reused, failed or
    uncertain) and `idem=` (the idempotency key of each step, in the same order: a hash, never a
    user value; D9.1), then `tool_error=` with the exception's type name when a tool raised.
    The ID comes from the tool, not from the user.
    `ops` lists the operations as the interpreter returned them, before the registry filter, so
    an unknown tool dropped by D10 stays visible. The keys before `changes=` carry no argument
    value and no message text. A turn the interpreter understood adds `invalid=`
    (`field:cause`, the invalid fields of the pending record, without values; D5), then, at the
    end of the line, `changes=` (`field:kind:old->new`, values as JSON; D4 rule 8) and
    `rejected=` (`key:name`; D4 rule 3), then `ambiguity`, the model's own free text (D2:
    logged, read by nothing).
    """
    act = interpretation.act.value if interpretation else ""
    ops = ",".join(
        f"{operation.tool}:{operation.certainty.value}"
        for operation in (interpretation.operations if interpretation else [])
    )
    parts = [f"session={session_id}"]
    if operation_id:
        parts.append(f"op={operation_id}")
    parts += [f"act={act}", f"ops={ops}", f"route={route}"]
    if results:
        parts.append(
            "results=" + ",".join(f"{r.tool}:{r.result_id or '-'}:{r.outcome}" for r in results)
        )
        parts.append("idem=" + ",".join(run.key for run in results))
        parts += [f"tool_error={run.error}" for run in results if run.error]
    if error:
        parts.append(f"error={error}")
    if interpretation:
        parts.append("invalid=" + ",".join(f"{i['field']}:{i['cause']}" for i in invalid))
        parts.append(
            "changes="
            + ",".join(f"{c.field}:{c.kind}:{_value(c.old)}->{_value(c.new)}" for c in changes)
        )
        parts.append("rejected=" + ",".join(f"{r.key}:{_value(r.name)}" for r in rejected))
    if interpretation and interpretation.ambiguity_reason:
        parts.append(f"ambiguity={interpretation.ambiguity_reason!r}")
    logger.info(" ".join(parts))


def _operation_id(record: dict | None) -> str | None:
    return record.get("operation_id") if record else None


def build_root_agent(interpreter: LlmAgent, executions: Executions | None = None) -> Workflow:
    """The root workflow: one edge from START to the turn node (D1.1). Tests pass an
    interpreter built on a fake model. Without `executions`, each agent built gets its own
    in-memory registry (D46; D62); the CLI passes the Redis one."""
    if executions is None:
        executions = InMemoryExecutions()

    # rerun_on_resume=True as D1.1 fixes it, and no retry_config: a retry would run the body (and
    # the model) again and spend free-tier quota. The HTTP retry of a 503 lives in the
    # interpreter's model (D1.2), below this node.
    @node(rerun_on_resume=True)
    async def turn(ctx: Context, node_input: Any) -> Event:
        # Only `ctx` and `node_input`: any other parameter would be bound to a state key.
        pending = ctx.state.get("pending")
        try:
            output = await ctx.run_node(interpreter, node_input=node_input)
            error = None
        except Exception as ex:  # D1.1: every model failure ends in a template
            output, error = None, ex

        if error is not None or not output:
            route = classify_failure(error)
            inner = getattr(error, "error", error)
            log_turn(
                ctx.session.id,
                route,
                error=type(inner).__name__ if inner is not None else "empty_output",
                operation_id=_operation_id(pending),
            )
            return Event(message=_FAILURE_REPLIES[route])  # no `state`: nothing changes

        # ADK already validated `output` against the schema, so this does not fail.
        interpretation = TurnInterpretation.model_validate(output)
        # Outside the `try`: an error in the core is a bug and must surface as one.
        # The only place in the code that reads the clock: the local time of the machine, naive
        # (D5.1). The core validates dates and times against it and never reads it itself.
        turn_input = TurnInput(
            message=message_text(node_input), now=datetime.now(), session_id=ctx.session.id
        )
        history = ctx.state.get("history") or []  # read only: the core returns the new list
        outcome = handle_turn(
            pending, interpretation, turn_input, history=history, executions=executions
        )
        log_turn(
            ctx.session.id,
            outcome.route,
            interpretation,
            changes=outcome.changes,
            rejected=outcome.rejected,
            invalid=outcome.invalid,
            results=outcome.results,
            # The record the turn wrote, or the pending one when it wrote none or emptied it.
            operation_id=_operation_id(outcome.state_delta.get("pending") or pending),
        )
        return Event(message=outcome.reply, state=outcome.state_delta)

    return Workflow(name="root_agent", edges=[("START", turn)])


root_agent = build_root_agent(interpreter)
# resumability_config stays None (D1.1): the CLI always sends a new message.
app = App(name=APP_NAME, root_agent=root_agent)
