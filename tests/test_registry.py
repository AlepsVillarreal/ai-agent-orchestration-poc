import re
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.registry import TOOLS, FieldKind, fields_of_kind
from core.text import normalize

REPO_ROOT = Path(__file__).resolve().parent.parent

# The tables of TASK.md §2.1 with the field kinds of D4 rule 1, in order.
EXPECTED_FIELDS = {
    "create_ticket": [
        ("title", FieldKind.TEXT),
        ("description", FieldKind.TEXT),
        ("priority", FieldKind.WORD),
        ("assignee", FieldKind.PERSON),
    ],
    "schedule_meeting": [
        ("title", FieldKind.TEXT),
        ("date", FieldKind.DATE),
        ("time", FieldKind.TIME),
        ("duration_minutes", FieldKind.INT),
        ("attendees", FieldKind.PERSON_LIST),
    ],
    "send_notification": [
        ("recipient", FieldKind.PERSON),
        ("message", FieldKind.TEXT),
        ("channel", FieldKind.WORD),
    ],
}


def _field(tool_name: str, field_name: str):
    return next(spec for spec in TOOLS[tool_name].fields if spec.name == field_name)


def test_catalog_has_exactly_the_three_task_tools():
    assert list(TOOLS) == ["create_ticket", "schedule_meeting", "send_notification"]
    assert all(name == tool.name for name, tool in TOOLS.items())


@pytest.mark.parametrize("tool_name", EXPECTED_FIELDS)
def test_contract_fields_match_task_tables(tool_name):
    tool = TOOLS[tool_name]
    expected = EXPECTED_FIELDS[tool_name]

    assert [(spec.name, spec.kind) for spec in tool.fields] == expected
    # The field list is declared twice (registry and Pydantic contract): they must agree.
    contract_fields = tool.contract.model_fields
    assert list(contract_fields) == [name for name, _ in expected]
    assert all(info.is_required() for info in contract_fields.values())


@pytest.mark.parametrize(
    ("tool_name", "field_name", "allowed"),
    [
        ("create_ticket", "priority", {"low", "medium", "high"}),
        ("send_notification", "channel", {"email", "sms", "push"}),
    ],
)
def test_allowed_values_for_word_fields(tool_name, field_name, allowed, canonical_args):
    spec = _field(tool_name, field_name)
    assert set(spec.allowed) == allowed
    # The synonym table only maps to allowed values.
    assert set(spec.synonyms) == allowed
    # The contract accepts each allowed value.
    for value in allowed:
        TOOLS[tool_name].contract.model_validate({**canonical_args[tool_name], field_name: value})


def test_same_field_name_has_same_kind_across_tools():
    kinds_by_name: dict[str, set[FieldKind]] = {}
    for tool in TOOLS.values():
        for spec in tool.fields:
            kinds_by_name.setdefault(spec.name, set()).add(spec.kind)

    assert kinds_by_name["title"] == {FieldKind.TEXT}
    assert all(len(kinds) == 1 for kinds in kinds_by_name.values()), kinds_by_name


def test_person_list_fields_are_derived_by_kind():
    found = [(tool_name, spec.name) for tool_name, spec in fields_of_kind(FieldKind.PERSON_LIST)]
    assert found == [("schedule_meeting", "attendees")]


def test_every_tool_and_field_has_descriptions_and_labels():
    for tool in TOOLS.values():
        assert tool.description.strip()
        assert tool.action_phrase.strip()
        assert tool.short_name.strip()
        assert tool.result_id_field.strip()
        for spec in tool.fields:
            assert spec.description.strip(), (tool.name, spec.name)
            assert spec.label.strip(), (tool.name, spec.name)


@pytest.mark.parametrize(
    ("tool_name", "field_name", "canonical_words"),
    [
        ("create_ticket", "priority", {"low", "medium", "high"}),
        ("send_notification", "channel", {"email", "sms", "push"}),
    ],
)
def test_word_field_descriptions_do_not_list_canonical_values(
    tool_name, field_name, canonical_words
):
    words = set(re.findall(r"[a-z]+", _field(tool_name, field_name).description.lower()))
    assert not words & canonical_words


@pytest.mark.parametrize(
    ("field_name", "word", "expected"),
    [
        ("priority", "alta", "high"),
        ("priority", "ALTA", "high"),
        ("priority", " Alta ", "high"),
        ("priority", "high", "high"),
        ("priority", "media", "medium"),
        ("priority", "baja", "low"),
        ("channel", "correo", "email"),
        ("channel", "Correo electrónico", "email"),
        ("channel", "email", "email"),
        ("channel", "sms", "sms"),
        ("channel", "push", "push"),
    ],
)
def test_resolve_word_maps_spanish_and_english_synonyms(field_name, word, expected):
    tool_name = "create_ticket" if field_name == "priority" else "send_notification"
    assert _field(tool_name, field_name).resolve_word(word) == expected


@pytest.mark.parametrize(
    ("field_name", "word"),
    [
        ("priority", "urgente"),
        ("priority", "normal"),
        ("priority", ""),
        ("channel", "whatsapp"),
        ("channel", ""),
    ],
)
def test_resolve_word_rejects_values_outside_table(field_name, word):
    tool_name = "create_ticket" if field_name == "priority" else "send_notification"
    assert _field(tool_name, field_name).resolve_word(word) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Ángel", "angel"), ("  CARLOS ", "carlos"), ("él", "el")],
)
def test_normalize_follows_d4(raw, expected):
    assert normalize(raw) == expected


@pytest.mark.parametrize(
    ("tool_name", "field_name", "bad_value"),
    [
        ("create_ticket", "priority", "urgente"),
        ("send_notification", "channel", "whatsapp"),
        ("schedule_meeting", "date", "2026-11-40"),
        ("schedule_meeting", "date", "40/11/2026"),
        ("schedule_meeting", "time", "25:90"),
        ("schedule_meeting", "attendees", []),
        ("create_ticket", "title", ""),
        ("schedule_meeting", "title", ""),
    ],
)
def test_contract_accepts_canonical_example_and_rejects_bad_shapes(
    tool_name, field_name, bad_value, canonical_args
):
    contract = TOOLS[tool_name].contract
    contract.model_validate(canonical_args[tool_name])  # the canonical example passes

    with pytest.raises(ValidationError):
        contract.model_validate({**canonical_args[tool_name], field_name: bad_value})


def test_contract_rejects_unknown_fields(canonical_args):
    with pytest.raises(ValidationError):
        TOOLS["send_notification"].contract.model_validate(
            {**canonical_args["send_notification"], "ticket_id": "TCK-1"}
        )


def test_core_imports_no_adk():
    # A fresh process: in this one, another test may already have imported ADK.
    code = (
        "import sys, core.registry, core.mock_tools; "
        "print(sorted(m for m in sys.modules if m.startswith('google.adk')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]"
