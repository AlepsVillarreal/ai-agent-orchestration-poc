"""ADK adapter for the interpreter: the `LlmAgent` that fills `TurnInterpretation`.

The interpreter only reads the user's message and reports it in the schema (D1, D2). It has no
tools and no sub-agents, sees only the current message (`include_contents='none'`) and does
not write to the session state (`output_key=None`): the turn node writes state with its final
event (D1.1, *Estado*). The turn node of task 04 calls it with `ctx.run_node`.

The instruction is a function (an ADK `InstructionProvider`), not a text template. With a text
template ADK would replace `{pending?}` with the whole pending record, collected values
included, which D2 forbids; a function skips that substitution and renders the record through
the pure summary builder (autodecided in task 02).
"""

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models import BaseLlm

from core.instruction import render_instruction
from core.interpretation import TurnInterpretation

MODEL = "gemini-3.5-flash-lite"  # D2, *Umbral*; F0


def interpreter_instruction(ctx: ReadonlyContext) -> str:
    """Instruction for this turn: the catalog plus a value-free summary of `pending` (D6)."""
    return render_instruction(ctx.state.get("pending"))


def build_interpreter(model: str | BaseLlm = MODEL) -> LlmAgent:
    """Build the interpreter. Tests pass a fake `BaseLlm`; building opens no connection."""
    return LlmAgent(
        name="interpreter",  # the CLI of task 04 filters events by this author
        model=model,
        instruction=interpreter_instruction,
        output_schema=TurnInterpretation,
        include_contents="none",
        # Left at their defaults on purpose: no output_key (D1.1), no tools or sub-agents (D1),
        # no generate_content_config (D2: default temperature and thinking), no retry (D1.1).
    )


interpreter = build_interpreter()
