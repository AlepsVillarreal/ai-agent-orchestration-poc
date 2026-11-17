"""Task 14a: the order of a plan and the ID link the registry declares (D8.1, D8.2, D8.4, D25).

Pure core, no ADK, no model: `core/plan.py` on operations as `filter_by_registry` gives them.
"""

import dataclasses

import pytest

from core.interpretation import TurnInterpretation
from core.operations import filter_by_registry
from core.plan import plan_order
from core.registry import TOOLS, FieldKind, IdLink, ToolSpec
from tests.interpretations import FIXED

TICKET, MEETING, NOTICE = "create_ticket", "schedule_meeting", "send_notification"


def e1_operations() -> list[dict]:
    interpretation = TurnInterpretation.model_validate(FIXED["E1"].interpretation)
    return filter_by_registry(interpretation)


def ops(*pairs: tuple[str, str | None]) -> list[dict]:
    """Operations with only what the plan reads: `tool` and `depends_on` (plus a marker)."""
    return [
        {"tool": tool, "depends_on": named, "args": {"n": i}}
        for i, (tool, named) in enumerate(pairs)
    ]


def order_of(operations: list[dict]) -> tuple[list[str], list[str | None], list[str]]:
    plan = plan_order(operations)
    return (
        [step["tool"] for step in plan.steps],
        [step["depends_on"] for step in plan.steps],
        plan.log,
    )


# 1 (D8.2, D25)
def test_id_link_is_declared_in_the_registry():
    declared = {name: tool.receives_id for name, tool in TOOLS.items() if tool.receives_id}

    assert list(declared) == [NOTICE]
    link = declared[NOTICE]
    assert link == IdLink(source=TICKET, field="message")
    assert link.source in TOOLS and TOOLS[link.source].result_id_field == "ticket_id"
    [receiver] = [spec for spec in TOOLS[NOTICE].fields if spec.name == link.field]
    assert receiver.kind == FieldKind.TEXT

    # A `ToolSpec` built without the new field is still valid (default).
    bare = ToolSpec(
        name="x",
        description="x",
        action_phrase="x",
        short_name="x",
        done_phrase="x",
        fields=(),
        contract=TOOLS[TICKET].contract,
        result_id_field="x_id",
        execute=lambda args: {},
    )
    assert bare.receives_id is None


# Review 14a-1, finding 3: `plan_order` breaks cycles only among the model's `depends_on`; the
# links it infers come from the registry, which must never declare a loop.
def test_declared_links_never_loop():
    for name in TOOLS:
        seen, link = {name}, TOOLS[name].receives_id
        while link is not None:
            assert link.source not in seen, (name, link.source)
            seen.add(link.source)
            link = TOOLS[link.source].receives_id


# 2 (D8.1, decision C)
@pytest.mark.parametrize(
    ("operations", "tools", "depends", "log"),
    [
        (e1_operations(), [TICKET, NOTICE], [None, TICKET], [f"{NOTICE}<{TICKET}"]),
        (
            list(reversed(e1_operations())),
            [TICKET, NOTICE],
            [None, TICKET],
            [f"{NOTICE}<{TICKET}"],
        ),
        (
            # A depends on B, B on C, arriving A, B, C -> C, B, A.
            ops((NOTICE, MEETING), (MEETING, TICKET), (TICKET, None)),
            [TICKET, MEETING, NOTICE],
            [None, TICKET, MEETING],
            [f"{MEETING}<{TICKET}", f"{NOTICE}<{MEETING}"],
        ),
        (
            ops((MEETING, None), (TICKET, None), (NOTICE, None)),
            [MEETING, TICKET, NOTICE],
            [None, None, TICKET],
            [f"{NOTICE}<{TICKET}:inferred"],
        ),
    ],
    ids=["E1", "E1_reversed", "chain_of_three", "three_without_depends_on"],
)
def test_plan_order(operations, tools, depends, log):
    assert order_of(operations) == (tools, depends, log)


def test_plan_order_keeps_every_other_key():
    operations = e1_operations()
    plan = plan_order(list(reversed(operations)))

    assert [step["args"] for step in plan.steps] == [op["args"] for op in operations]
    assert [step["certainty"] for step in plan.steps] == [op["certainty"] for op in operations]


def test_three_without_dependencies_or_links_keep_the_interpreter_order(monkeypatch):
    monkeypatch.setitem(TOOLS, NOTICE, dataclasses.replace(TOOLS[NOTICE], receives_id=None))

    assert order_of(ops((NOTICE, None), (MEETING, None), (TICKET, None))) == (
        [NOTICE, MEETING, TICKET],
        [None, None, None],
        [],
    )


# 3 (D8.1, decision C): an ignored `depends_on` is logged; the order stays the interpreter's
# except for what the inference of criterion 4 adds.
@pytest.mark.parametrize(
    ("operations", "tools", "depends", "log"),
    [
        (  # outside the plan
            ops((MEETING, "book_flight"), (TICKET, None)),
            [MEETING, TICKET],
            [None, None],
            [f"{MEETING}<book_flight:ignored"],
        ),
        (  # outside the plan, and the declared link is inferred
            ops((NOTICE, MEETING), (TICKET, None)),
            [TICKET, NOTICE],
            [None, TICKET],
            [f"{NOTICE}<{MEETING}:ignored", f"{NOTICE}<{TICKET}:inferred"],
        ),
        (  # itself
            ops((MEETING, MEETING), (TICKET, None)),
            [MEETING, TICKET],
            [None, None],
            [f"{MEETING}<{MEETING}:ignored"],
        ),
        (  # a cycle of two: both ignored
            ops((MEETING, TICKET), (TICKET, MEETING)),
            [MEETING, TICKET],
            [None, None],
            [f"{MEETING}<{TICKET}:ignored", f"{TICKET}<{MEETING}:ignored"],
        ),
        (  # a cycle of two with the declared pair: both ignored, then the link is inferred
            ops((NOTICE, TICKET), (TICKET, NOTICE)),
            [TICKET, NOTICE],
            [None, TICKET],
            [
                f"{NOTICE}<{TICKET}:ignored",
                f"{TICKET}<{NOTICE}:ignored",
                f"{NOTICE}<{TICKET}:inferred",
            ],
        ),
    ],
    ids=["outside", "outside_then_inferred", "itself", "cycle", "cycle_then_inferred"],
)
def test_ignored_dependencies(operations, tools, depends, log):
    assert order_of(operations) == (tools, depends, log)


# 4 (D8.4, D25, decision C)
@pytest.mark.parametrize(
    ("operations", "tools", "depends", "log"),
    [
        (
            ops((TICKET, None), (NOTICE, None)),
            [TICKET, NOTICE],
            [None, TICKET],
            [f"{NOTICE}<{TICKET}:inferred"],
        ),
        (
            ops((NOTICE, None), (TICKET, None)),
            [TICKET, NOTICE],
            [None, TICKET],
            [f"{NOTICE}<{TICKET}:inferred"],
        ),
        (  # the only `depends_on` was ignored (outside the plan): it counts as absent
            ops((NOTICE, None), (TICKET, "book_flight")),
            [TICKET, NOTICE],
            [None, TICKET],
            [f"{TICKET}<book_flight:ignored", f"{NOTICE}<{TICKET}:inferred"],
        ),
        (  # a step keeps a valid `depends_on`: nothing is inferred (decision C, option (a))
            ops((NOTICE, None), (TICKET, None), (MEETING, TICKET)),
            [NOTICE, TICKET, MEETING],
            [None, None, TICKET],
            [f"{MEETING}<{TICKET}"],
        ),
        (  # no declaration between these two
            ops((NOTICE, None), (MEETING, None)),
            [NOTICE, MEETING],
            [None, None],
            [],
        ),
    ],
    ids=["ticket_first", "notice_first", "after_an_ignored_one", "one_kept", "no_declaration"],
)
def test_inferred_dependency(operations, tools, depends, log):
    assert order_of(operations) == (tools, depends, log)


def test_inference_follows_the_registry(monkeypatch):
    # Another declared link, and none on the notice: `core/plan.py` follows it unchanged (D25).
    monkeypatch.setitem(TOOLS, NOTICE, dataclasses.replace(TOOLS[NOTICE], receives_id=None))
    monkeypatch.setitem(
        TOOLS,
        MEETING,
        dataclasses.replace(TOOLS[MEETING], receives_id=IdLink(source=NOTICE, field="title")),
    )

    assert order_of(ops((TICKET, None), (MEETING, None), (NOTICE, None))) == (
        [TICKET, NOTICE, MEETING],
        [None, None, NOTICE],
        [f"{MEETING}<{NOTICE}:inferred"],
    )
    assert order_of(ops((TICKET, None), (NOTICE, None))) == ([TICKET, NOTICE], [None, None], [])
