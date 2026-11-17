import re

from pydantic import BaseModel

from core.instruction import NOTHING_PENDING, build_instruction, pending_summary
from core.registry import TOOLS, FieldKind, FieldSpec, ToolSpec

PENDING = {
    "tools": ["create_ticket"],
    "status": "collecting",
    "missing": ["title", "priority"],
    "last_question": "¿Qué título y qué prioridad tendrá?",
    # Keys the summary must never read.
    "id": "op-1",
    "args": {"assignee": "Carlos", "description": "Servidor caído"},
}


# 13
def test_instruction_lists_every_tool_and_field_from_the_registry():
    text = build_instruction()
    for tool in TOOLS.values():
        assert tool.name in text
        assert tool.description in text
        for spec in tool.fields:
            assert f"{spec.name}: {spec.description}" in text, (tool.name, spec.name)


# 14
def test_instruction_is_generated_from_the_catalog():
    fake = ToolSpec(
        name="book_room",
        description="Book a meeting room.",
        action_phrase="reservar una sala",
        short_name="sala",
        fields=(
            FieldSpec(name="room", kind=FieldKind.TEXT, description="Room name.", label="sala"),
        ),
        contract=BaseModel,
        result_id_field="booking_id",
        execute=lambda args: args,
    )
    text = build_instruction({"book_room": fake})

    assert "book_room: Book a meeting room." in text
    assert "room: Room name." in text
    assert not any(name in text for name in TOOLS)
    # No list field in this catalog, so no list bucket is described.
    assert "_add" not in text


# 15
def test_instruction_does_not_list_canonical_word_values():
    words = set(re.findall(r"[a-z]+", build_instruction().lower()))
    assert not words & {"low", "medium", "high", "email", "sms", "push"}


# 16
def test_pending_summary_has_no_collected_values():
    summary = pending_summary(PENDING)

    assert "create_ticket" in summary
    assert "collecting" in summary
    assert "title" in summary and "priority" in summary
    assert "¿Qué título y qué prioridad tendrá?" in summary
    assert "Carlos" not in summary
    assert "Servidor caído" not in summary
    assert "op-1" not in summary


# 17
def test_pending_summary_when_nothing_is_pending():
    assert pending_summary(None) == NOTHING_PENDING
    assert pending_summary({}) == NOTHING_PENDING


# Note (f) of task 02 (D16)
def test_pending_summary_with_a_partial_record():
    summary = pending_summary({"status": "collecting"})
    assert "Pending tools: none" in summary
    assert "Status: collecting" in summary
    assert "Missing fields: none" in summary
    assert "Last question to the user: none" in summary

    assert "Status: unknown" in pending_summary({"tools": ["create_ticket"]})
