import json
import re
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from core.interpretation import (
    Act,
    Certainty,
    DateParts,
    TimeParts,
    ToolArgs,
    TurnInterpretation,
    build_args_model,
)
from core.registry import TOOLS, FieldKind, FieldSpec, ToolSpec
from core.text import normalize
from tests.interpretations import FIXED

PERSON_KINDS = {FieldKind.PERSON, FieldKind.PERSON_LIST}


def adk_dump(payload: Any) -> dict:
    """Exactly what ADK hands the turn node for an `output_schema` agent.

    `workflow/_llm_agent_wrapper.py:410` -> `utils/_schema_utils.py:260` in google-adk 2.14.1.
    Reproduced here instead of importing ADK's private function.
    """
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return TurnInterpretation.model_validate_json(text).model_dump(exclude_none=True)


def _contains_none(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, dict):
        return any(_contains_none(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_none(item) for item in value)
    return False


def _fake_tool(name: str, *fields: tuple[str, FieldKind]) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"Fake tool {name}.",
        action_phrase="hacer algo",
        short_name="algo",
        fields=tuple(
            FieldSpec(name=field, kind=kind, description=f"The {field}.", label=field)
            for field, kind in fields
        ),
        contract=BaseModel,
        result_id_field="fake_id",
        execute=lambda args: args,
    )


# 1
def test_act_values_are_exactly_d3():
    assert {act.value for act in Act} == {
        "request_action",
        "provide_info",
        "confirm",
        "reject",
        "cancel",
        "question",
        "other",
    }
    with pytest.raises(ValidationError):
        adk_dump({"act": "select"})


# 2
def test_certainty_values_are_explicit_and_plausible():
    assert {value.value for value in Certainty} == {"explicit", "plausible"}
    with pytest.raises(ValidationError):
        adk_dump({"act": "request_action", "operations": [{"tool": "x", "certainty": "maybe"}]})


# 3
def test_tool_is_a_free_string():
    dumped = adk_dump(
        {
            "act": "request_action",
            "operations": [{"tool": "book_flight", "certainty": "explicit"}],
        }
    )
    assert dumped["operations"][0]["tool"] == "book_flight"


# 4
def test_args_fields_are_generated_from_the_registry():
    field_names = {spec.name for tool in TOOLS.values() for spec in tool.fields}
    assert set(ToolArgs.model_fields) == field_names | {"attendees_add", "attendees_remove"}
    assert not any(info.is_required() for info in ToolArgs.model_fields.values())


# 5
def test_list_buckets_follow_the_kind_not_the_tool():
    catalog = {
        **TOOLS,
        "watch_issue": _fake_tool(
            "watch_issue", ("watchers", FieldKind.PERSON_LIST), ("issue", FieldKind.TEXT)
        ),
    }
    fields = build_args_model(catalog).model_fields

    assert {"watchers", "watchers_add", "watchers_remove"} <= set(fields)
    assert {"issue_add", "issue_remove"}.isdisjoint(fields)
    assert fields["watchers_add"].annotation == fields["attendees_add"].annotation


# 6
def test_arg_types_follow_field_kinds():
    args = ToolArgs.model_validate(
        {
            "priority": "urgente",
            "channel": "whatsapp",
            "duration_minutes": 45,
            "date": {"day": 3},
            "time": {"hour": 9},
        }
    )
    assert args.priority == "urgente"
    assert args.channel == "whatsapp"
    assert args.duration_minutes == 45
    assert isinstance(args.date, DateParts)
    assert isinstance(args.time, TimeParts)
    with pytest.raises(ValidationError):
        ToolArgs.model_validate({"duration_minutes": "media hora"})


# 7
def test_values_arrive_as_said_without_ranges():
    dumped = adk_dump(
        {
            "act": "request_action",
            "operations": [
                {
                    "tool": "schedule_meeting",
                    "certainty": "explicit",
                    "args": {
                        "date": {"day": 40, "month": 11},
                        "time": {"hour": 25, "minute": 90},
                        "duration_minutes": 0,
                        "priority": "urgente",
                    },
                }
            ],
        }
    )
    assert dumped["operations"][0]["args"] == {
        "date": {"day": 40, "month": 11},
        "time": {"hour": 25, "minute": 90},
        "duration_minutes": 0,
        "priority": "urgente",
    }


# 8
def test_dump_matches_what_adk_delivers():
    raw = json.dumps(
        {
            "act": "provide_info",
            "confirms_tool": None,
            "ambiguity_reason": None,
            "operations": [
                {
                    "tool": "schedule_meeting",
                    "certainty": "explicit",
                    "depends_on": None,
                    "args": {
                        "title": "",
                        "attendees": [],
                        "attendees_add": None,
                        "duration_minutes": 0,
                        "date": {"day": None, "month": 11},
                        "time": {},
                    },
                }
            ],
        }
    )
    dumped = adk_dump(raw)

    assert not _contains_none(dumped)
    assert dumped == {
        "act": "provide_info",
        "operations": [
            {
                "tool": "schedule_meeting",
                "certainty": "explicit",
                "args": {
                    "title": "",
                    "attendees": [],
                    "duration_minutes": 0,
                    "date": {"month": 11},
                    "time": {},
                },
            }
        ],
    }


# 9
@pytest.mark.parametrize("key", FIXED)
def test_fixed_interpretations_validate(key):
    dumped = adk_dump(FIXED[key].interpretation)
    assert not _contains_none(dumped)
    # Nothing the fixture wrote is lost or renamed on the way through the schema.
    assert dumped == FIXED[key].interpretation


def test_fixed_interpretations_spot_checks():
    dumped = {key: adk_dump(turn.interpretation) for key, turn in FIXED.items()}

    d1_args = dumped["D1"]["operations"][0]["args"]
    assert d1_args["date"] == {"day": 40, "month": 11}
    assert d1_args["time"] == {"hour": 25, "minute": 90}

    e1 = dumped["E1"]["operations"]
    assert [op["tool"] for op in e1] == ["create_ticket", "send_notification"]
    assert e1[1]["depends_on"] == "create_ticket"

    assert dumped["G2"] == {"act": "confirm", "operations": []}

    c1 = {op["tool"]: op["certainty"] for op in dumped["C1"]["operations"]}
    assert c1 == {"send_notification": "explicit", "schedule_meeting": "plausible"}

    buckets = {
        key: {
            name: value
            for name, value in dumped[key]["operations"][0]["args"].items()
            if name.startswith("attendees")
        }
        for key in ("L1", "L2", "L3", "L4")
    }
    assert buckets == {
        "L1": {"attendees_add": ["Ana"]},
        "L2": {"attendees_remove": ["Juan"]},
        "L3": {"attendees": ["Ana"]},
        "L4": {"attendees_remove": ["Juan"], "attendees_add": ["Ana"]},
    }


# 10
def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", normalize(text)))


def _person_field_names() -> set[str]:
    names = {
        spec.name for tool in TOOLS.values() for spec in tool.fields if spec.kind in PERSON_KINDS
    }
    lists = {
        spec.name
        for tool in TOOLS.values()
        for spec in tool.fields
        if spec.kind == FieldKind.PERSON_LIST
    }
    return names | {f"{name}_{bucket}" for name in lists for bucket in ("add", "remove")}


@pytest.mark.parametrize("key", FIXED)
def test_fixed_interpretation_values_appear_in_their_message(key):
    message = FIXED[key].message
    person_fields = _person_field_names()

    for operation in FIXED[key].interpretation["operations"]:
        for field_name, value in operation["args"].items():
            if field_name in person_fields:
                for name in [value] if isinstance(value, str) else value:
                    assert _words(name) <= _words(message), (key, field_name, name)
            elif isinstance(value, str):
                # Text and word values are copied, so they are part of the message too.
                assert normalize(value) in normalize(message), (key, field_name, value)


# 11
@pytest.mark.parametrize(
    "raw",
    [
        "",
        "{",
        json.dumps({"operations": []}),
        json.dumps({"act": "request_action", "operations": [{"tool": "x", "certainty": "sure"}]}),
    ],
    ids=["empty", "broken_json", "no_act", "bad_certainty"],
)
def test_malformed_output_raises_validation_error(raw):
    with pytest.raises(ValidationError):
        adk_dump(raw)


def test_unknown_keys_are_dropped():
    # Autodecided in task 02: no `extra="forbid"` at this boundary (unlike D12's contracts).
    dumped = adk_dump(
        {
            "act": "request_action",
            "mood": "urgent",
            "operations": [
                {"tool": "create_ticket", "certainty": "explicit", "args": {"severity": "x"}}
            ],
        }
    )
    assert "mood" not in dumped
    assert dumped["operations"][0]["args"] == {}


# 12
def _keys_anywhere(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _keys_anywhere(item)}
    if isinstance(value, list):
        return {key for item in value for key in _keys_anywhere(item)}
    return set()


def test_schema_is_accepted_by_the_developer_api():
    assert "additionalProperties" not in _keys_anywhere(TurnInterpretation.model_json_schema())

    # Private import, allowed only in this test: it is the conversion the SDK applies to
    # `response_schema` in Gemini Developer API mode (`vertexai=False`). The adapter code
    # itself never imports private modules (D1.1).
    from google.genai._transformers import t_schema

    assert t_schema(SimpleNamespace(vertexai=False), TurnInterpretation) is not None
