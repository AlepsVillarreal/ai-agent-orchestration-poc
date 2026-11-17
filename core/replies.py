"""Every Spanish reply of the agent, in one place (D2: replies only from templates).

Tool-specific words (action phrases, field labels) come from the registry, never from here
(TASK.md line 562). Templates marked *provisional* stand in for branches that later tasks
build; they never start or run anything.
"""

from core.registry import TOOLS, ToolSpec

# D10: something in the request matched no tool, and something else did.
UNKNOWN_TOOL_LINE = "Parte de tu solicitud no corresponde a ninguna de mis herramientas."

# D11: two operations of the same tool in one turn.
ONE_PER_TOOL = "Puedo hacer una operación de cada tipo por mensaje; ¿por cuál empiezo?"

# D3 gate, step 4: a confirmation, rejection or bare data with nothing pending (TASK.md line 284).
NO_PENDING_OPERATION = "No hay ninguna operación pendiente."

# Provisional: steps 2 and 3 of the D3 gate (tasks 08, 10a, 12 and 13 replace it).
PENDING_NOT_SUPPORTED_YET = "Todavía no puedo continuar esa operación en esta versión."

# Provisional: rules R3, R4 and R5 (tasks 13 and 14a replace it).
ONE_OPERATION_AT_A_TIME = "Por ahora puedo atender una operación a la vez; ¿cuál necesitas primero?"

# Provisional: a request that arrives with every field (task 09b replaces it).
READY_NOT_AVAILABLE_YET = (
    "Tengo los datos; la validación y la confirmación aún no están disponibles."
)


def join_es(items: list[str], last_word: str = "y") -> str:
    """Join items the Spanish way: "a, b y c" (or "a, b o c")."""
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} {last_word} {items[-1]}"


def capabilities() -> str:
    """R1 and the reply to `question`/`other`: what the agent can do, from the registry."""
    actions = join_es([tool.action_phrase for tool in TOOLS.values()], "o")
    return f"Puedo ayudarte a {actions}. ¿Qué necesitas?"


def missing_question(tool: ToolSpec, missing: list[str]) -> str:
    """One question for all missing fields, with registry labels in registry order.

    It never lists allowed values (autodecided F in task 03) and never shows a collected value
    (D2): it is stored as `last_question`, which the interpreter reads.
    """
    labels = [spec.label for spec in tool.fields if spec.name in missing]
    return f"Para {tool.action_phrase} necesito {join_es(labels)}."
