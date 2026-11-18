"""The clarification of D3: the record R4 and R5 store, and how its answer is read.

When the interpreter reports one `explicit` reading and at least one `plausible` (R4), or only
`plausible` ones (R5), nothing starts: the candidates are stored with their arguments already
anchored and the user is asked which one (D3, *Aclaración*; D4 rule 4 and *Precisa D3*). The
next turn is read as the answer by `answer_rule`, before `act` is looked at (D3 gate, step 2).

Nothing here reads the message text beyond the anchoring of `merge_args`, nor the model's
free-text reason for the ambiguity, which only the log shows (D2, *Solo para logs*); the
interpreter's output arrives already filtered to operations, `act` and `confirms_tool`. The
rules count operations and compare tool names with the candidates; they never name a tool
(TASK.md line 562).
"""

from collections.abc import Sequence
from uuid import uuid4

from core import replies
from core.interpretation import Act, Certainty
from core.merge import Change, Rejected, merge_args
from core.registry import TOOLS, ToolSpec

AWAITING_CLARIFICATION = "awaiting_clarification"

DISCARD = 1  # `cancel`/`reject` inside the candidates: nothing starts
RESOLVE = 2  # the `explicit` candidates of the answer are chosen
RESOLVE_ONLY_CANDIDATE = 3  # a bare "yes" to a one-candidate question
CHANGE_OF_INTENTION = 4  # another tool, `explicit` (D3.1)
REPEAT = 5  # anything else: the same question again (D3.2)


def new_record(
    operations: Sequence[dict], message: str
) -> tuple[dict | None, list[Change], list[Rejected]]:
    """The `awaiting_clarification` record of R4 and R5, with the changes and the rejected
    names of anchoring its candidates (for the log, and the fixed line of D4 rule 9).

    Candidates go `explicit` first, then the rest in the interpreter's order, one per tool
    (D11 already holds). Each keeps its `args` after anchoring and merging against nothing
    (D4 rule 4: a name not written in this message never gets in, buckets are applied and not
    stored) and its `depends_on` as it came (task 14a reads it). `certainty` is not stored.
    `operation_id` is the clarification's id: a flow born from it keeps it. No `steps`, so no
    branch of an operation (*Misma tool*, a "yes") ever takes it for one. Returns `None` as the
    record with four or more candidates, which D3 never asks about (unreachable with three tools).
    """
    ordered = sorted(operations, key=lambda op: op["certainty"] != Certainty.EXPLICIT)
    tools = [TOOLS[operation["tool"]] for operation in ordered]
    question = replies.clarification_question(tools)
    if question is None:
        return None, [], []

    candidates, changes, rejected = [], [], []
    for tool, operation in zip(tools, ordered, strict=True):
        merged = merge_args(tool, {}, operation["args"], message)
        candidates.append(
            {"tool": tool.name, "args": merged.args, "depends_on": operation["depends_on"]}
        )
        changes += merged.changes
        rejected += merged.rejected
    record = {
        "operation_id": uuid4().hex,
        "status": AWAITING_CLARIFICATION,
        "tools": [tool.name for tool in tools],
        "candidates": candidates,
        "missing": [],
        "last_question": question,
    }
    return record, changes, rejected


def answer_rule(
    candidates: Sequence[str],
    act: Act,
    operations: Sequence[dict],
    confirms_tool: str | None,
) -> int:
    """D3, *Lectura de la respuesta*, with D3.1 and D3.2: which of the five rules applies.

    `candidates` are the candidate tool names; `operations` the turn's, after the registry
    filter. S is the set of tools with an `explicit` operation: only `explicit` selects, and a
    `plausible` of another candidate makes the answer unclear, so repeating the first message
    does not resolve it. The rules are tried in order.
    """
    candidate_set = set(candidates)
    explicit_tools = {op["tool"] for op in operations if op["certainty"] == Certainty.EXPLICIT}
    plausible = {op["tool"] for op in operations if op["certainty"] == Certainty.PLAUSIBLE}
    inside = explicit_tools <= candidate_set

    if act in (Act.CANCEL, Act.REJECT) and inside:
        return DISCARD  # a "no" never selects: "No, la reunión no" names what is not wanted
    if (
        explicit_tools
        and inside
        and act in (Act.REQUEST_ACTION, Act.PROVIDE_INFO, Act.CONFIRM)
        and not ((candidate_set - explicit_tools) & plausible)
        and (act != Act.CONFIRM or len(explicit_tools) == 1)
    ):
        return RESOLVE
    if (
        act == Act.CONFIRM
        and not explicit_tools
        and len(candidate_set) == 1
        and confirms_tool in (None, *candidate_set)
    ):
        return RESOLVE_ONLY_CANDIDATE
    if not inside and act in (Act.REQUEST_ACTION, Act.PROVIDE_INFO, Act.REJECT, Act.CANCEL):
        return CHANGE_OF_INTENTION
    return REPEAT


def chosen(
    record: dict, rule: int, operations: Sequence[dict]
) -> list[tuple[ToolSpec, dict, dict]]:
    """What rule 2 or 3 resolves to, in the candidates' order (D3, D8): per chosen candidate,
    its tool, its stored args and the args this turn brings for it (`{}` if none).

    Rule 2 chooses the `explicit` tools; rule 3, the only candidate. The turn's operation of a
    chosen tool is merged whatever its `certainty`: it only adds data to a choice already made.
    """
    picked = {op["tool"] for op in operations if op["certainty"] == Certainty.EXPLICIT}
    incoming = {op["tool"]: op["args"] for op in operations}
    return [
        (TOOLS[c["tool"]], c["args"], incoming.get(c["tool"], {}))
        for c in record["candidates"]
        if rule == RESOLVE_ONLY_CANDIDATE or c["tool"] in picked
    ]


def candidate_steps(record: dict) -> list[dict]:
    """The candidates as steps never started, `{tool, args}`, for `history` (D6, D65)."""
    return [{"tool": c["tool"], "args": c["args"]} for c in record["candidates"]]
