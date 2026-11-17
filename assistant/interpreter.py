"""ADK adapter for the interpreter: the `LlmAgent` that fills `TurnInterpretation`.

The interpreter only reads the user's message and reports it in the schema (D1, D2). It has no
tools and no sub-agents, sees only the current message (`include_contents='none'`) and does
not write to the session state (`output_key=None`): the turn node writes state with its final
event (D1.1, *Estado*). The turn node of task 04 calls it with `ctx.run_node`.

The instruction is a function (an ADK `InstructionProvider`), not a text template. With a text
template ADK would replace `{pending?}` with the whole pending record, collected values
included, which D2 forbids; a function skips that substitution and renders the record through
the pure summary builder (autodecided in task 02).

The production model retries a 503 once at the HTTP level (D1.2, `INTERPRETER_RETRY_OPTIONS`):
the interpreter has no effects, so repeating its request cannot run a tool twice. The turn node
itself still has no `retry_config` (D1.1).
"""

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models import BaseLlm, Gemini
from google.genai import types

from core.instruction import render_instruction
from core.interpretation import TurnInterpretation

MODEL = "gemini-3.5-flash-lite"  # D2, *Umbral*; F0
# D1.2: the google-genai client retries a 503 once (wait of 1-2 s with the SDK's exp_base and
# jitter). Not a 429: that is spent quota, and waiting seconds does not bring it back. With any
# retry_options the SDK also retries, once here, an httpx connection error or timeout; there is
# no public switch to turn that off (as in D30). The turn node's retry_config stays None.
INTERPRETER_RETRY_OPTIONS = types.HttpRetryOptions(
    attempts=2, initial_delay=1.0, max_delay=5.0, http_status_codes=[503]
)


def interpreter_instruction(ctx: ReadonlyContext) -> str:
    """Instruction for this turn: the catalog plus a value-free summary of `pending` (D6)."""
    return render_instruction(ctx.state.get("pending"))


def interpreter_model() -> Gemini:
    """The production model: `MODEL` with the 503 retry of D1.2. Opens no connection."""
    return Gemini(model=MODEL, retry_options=INTERPRETER_RETRY_OPTIONS)


def build_interpreter(model: str | BaseLlm | None = None) -> LlmAgent:
    """Build the interpreter. `None` uses `interpreter_model()`; tests pass a fake `BaseLlm`.

    Building opens no connection and reads no API key.
    """
    return LlmAgent(
        name="interpreter",  # the CLI of task 04 filters events by this author
        model=interpreter_model() if model is None else model,
        instruction=interpreter_instruction,
        output_schema=TurnInterpretation,
        include_contents="none",
        # Left at their defaults on purpose: no output_key (D1.1), no tools or sub-agents (D1),
        # no generate_content_config (D2: default temperature and thinking). The 503 retry
        # lives in the model's retry_options (D1.2), not in generate_content_config.
    )


interpreter = build_interpreter()
