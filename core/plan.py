"""The order of a plan and the dependency of each step (D8.1, D8.4, D25).

A plan is two or more operations of different tools (D11 already holds) that R3, a change of
intention that falls in R3 or a clarification resolved to two or more candidates turn into one
pending record with one step per tool (D3, D8.1). This module decides, before any step is
merged, in which order the steps go and which step each one depends on:

1. A `depends_on` counts if it names another tool of the plan. One that names a tool outside
   the plan, or the step's own tool, is ignored and logged.
2. If the dependencies that count form a cycle, every one in the cycle is ignored and logged:
   the code never picks which one wins (like D35 with two different `depends_on`).
3. If after that no step keeps a dependency (one ignored counts as absent, D8.1), each step
   whose tool declares in the registry that it receives the ID of another tool of the plan
   depends on it, logged as inferred (D8.4, D25). The registry declares no loop of links
   (`tests/test_plan.py` checks it), so an inferred dependency never forms a cycle.
4. Order: walk the steps in the order they came (the interpreter's, or the candidates') and
   place the first one whose dependency is already placed or that has none; repeat until all
   are placed. Stable, it orders a chain of three that came backwards, and needs no graph.

It also decides what a step receives from the step it depends on (D8.2, D85): a step whose
`depends_on` is the source of its registry's `receives_id` gets that producer's ID at the end of
the declared field, after validation and after the hash, so neither `pending` nor the hash ever
carry it (D4 rule 10). The same condition decides the confirmation line of D85, so the line is
shown if and only if the ID is appended.

It reads only tool names, `depends_on` and the registry's `receives_id`: never the message
text, a value, the model's reason for an ambiguity, nor a tool name of its own (TASK.md line
562), so a new link between two tools is one `IdLink` in the registry and no code here.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from core.registry import TOOLS, IdLink

IGNORED = "ignored"
INFERRED = "inferred"


@dataclass(frozen=True)
class PlanOrder:
    """The steps in execution order, each with its effective `depends_on` (None if none), and
    one log entry per effective or ignored dependency (`receiver<producer`, `…:inferred`,
    `step<named:ignored`; tool names only, never a user value)."""

    steps: list[dict]
    log: list[str]


def plan_order(operations: Sequence[dict]) -> PlanOrder:
    """Order `operations` (dicts with `tool` and `depends_on`, one per tool) as a plan.

    Each returned dict is a copy of its operation with `depends_on` replaced by the effective
    one; every other key is kept as it came.
    """
    tools = [operation["tool"] for operation in operations]
    log = []
    depends: dict[str, str] = {}
    for operation in operations:
        tool, named = operation["tool"], operation["depends_on"]
        if named is None:
            continue
        if named in tools and named != tool:
            depends[tool] = named
        else:
            log.append(f"{tool}<{named}:{IGNORED}")
    for tool in _in_cycles(depends):
        log.append(f"{tool}<{depends.pop(tool)}:{IGNORED}")

    inferred = set()
    if not depends:
        for tool in tools:
            link = TOOLS[tool].receives_id
            if link is not None and link.source in tools and link.source != tool:
                depends[tool] = link.source
                inferred.add(tool)

    ordered, placed = [], set()
    remaining = list(operations)
    while remaining:
        operation = next(op for op in remaining if depends.get(op["tool"]) in (None, *placed))
        remaining.remove(operation)
        placed.add(operation["tool"])
        tool = operation["tool"]
        ordered.append({**operation, "depends_on": depends.get(tool)})
        if tool in depends:
            suffix = f":{INFERRED}" if tool in inferred else ""
            log.append(f"{tool}<{depends[tool]}{suffix}")
    return PlanOrder(ordered, log)


def _in_cycles(depends: dict[str, str]) -> list[str]:
    """The tools whose chain of dependencies comes back to them, in `depends` order."""
    looped = []
    for start in depends:
        tool, seen = depends[start], {start}
        while tool in depends and tool not in seen:
            seen.add(tool)
            tool = depends[tool]
        if tool == start:
            looped.append(start)
    return looped


def receives_from(tool: str, depends_on: str | None) -> IdLink | None:
    """The link along which a step gets its producer's ID, or None (D8.2, D85).

    Only when the step depends on the tool its registry says it receives an ID from: a
    notification alone, or one that depends on a meeting, receives nothing.
    """
    link = TOOLS[tool].receives_id
    return link if link is not None and depends_on == link.source else None


def received_values(
    tool: str, depends_on: str | None, values: dict[str, Any], ids: Mapping[str, str]
) -> dict[str, Any]:
    """The values a step's tool receives: its confirmed `values`, with the producer's ID at the
    end of the linked field when the step receives one and `ids` (by tool, the IDs this "yes"
    already got) has it: "ya se registró la falla (ticket TCK-1)" (D8.2). A new dict; `values`
    is not changed.
    """
    link = receives_from(tool, depends_on)
    if link is None or link.source not in ids:
        return dict(values)
    suffix = f"({TOOLS[link.source].short_name} {ids[link.source]})"
    return {**values, link.field: f"{values[link.field]} {suffix}"}
