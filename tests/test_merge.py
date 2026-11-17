import copy
from typing import Any

import pytest
from pydantic import BaseModel

from core.merge import (
    EXCLUDED_WORDS,
    Change,
    MergeResult,
    Note,
    Rejected,
    anchored,
    merge_args,
)
from core.registry import TOOLS, FieldKind, FieldSpec, ToolSpec
from tests.interpretations import FIXED

TICKET = TOOLS["create_ticket"]
MEETING = TOOLS["schedule_meeting"]


def fixed_args(key: str) -> dict[str, Any]:
    """`args` of the only operation of a fixed interpretation."""
    (operation,) = FIXED[key].interpretation["operations"]
    return operation["args"]


def merge_fixed(tool: ToolSpec, stored: dict[str, Any], key: str) -> MergeResult:
    return merge_args(tool, stored, fixed_args(key), FIXED[key].message)


def f1_stored() -> dict[str, Any]:
    """The meeting with Juan that `F1` leaves pending."""
    return merge_fixed(MEETING, {}, "F1").args


# 1
def test_excluded_words_are_exactly_d4():
    d4 = {
        "yo", "mi", "me", "tu", "ti", "te", "el", "ella", "usted", "ustedes", "nosotros",
        "nosotras", "ellos", "ellas", "le", "les", "lo", "la", "los", "las", "se", "nos",
    }  # fmt: skip
    assert len(d4) == 22
    assert EXCLUDED_WORDS == d4


# 2
@pytest.mark.parametrize(
    ("name", "message", "expected"),
    [
        ("Carlos", "Alta, asígnalo a Carlos", True),
        ("Ángel", "avísale a angel", True),
        ("Juan Pérez", "con juan perez", True),
        ("Ana", "mañana revisamos el servidor", False),  # substring only (D4 *Descartadas*)
        ("Juan Pérez", "con Juan", False),
        ("Él", "asígnalo a él", False),  # only excluded words
        ("Carlitos", "avísale a Carlos", False),  # a nickname (D4 *Limitaciones*)
    ],
)
def test_anchor_needs_whole_words_of_this_message(name, message, expected):
    assert anchored(name, message) is expected


# 3
def test_rejected_name_is_dropped_alone():
    result = merge_args(TICKET, {"assignee": "Carlos"}, {"assignee": "él"}, "asígnalo a él")

    assert result.args == {"assignee": "Carlos"}
    assert result.changes == []
    assert result.rejected == [Rejected("assignee", "él")]

    result = merge_args(
        MEETING, {"attendees": ["Juan"]}, {"attendees_add": ["Ana", "Pedro"]}, "Agrega a Ana"
    )

    assert result.args == {"attendees": ["Juan", "Ana"]}
    assert result.rejected == [Rejected("attendees_add", "Pedro")]
    assert result.changes == [Change("attendees", "overwrite", ["Juan"], ["Juan", "Ana"])]


# 4
def test_stored_values_are_never_re_anchored():
    result = merge_args(TICKET, {"assignee": "Carlos"}, {"priority": "media"}, "mejor media")

    assert result.args == {"assignee": "Carlos", "priority": "media"}
    assert result.rejected == []


# 5
def test_absent_never_changes_stored():
    ticket_stored = {"title": "Servidor caído", "assignee": "Carlos"}
    result = merge_args(TICKET, ticket_stored, {"title": "", "assignee": "  "}, "nada nuevo")
    assert result == MergeResult(ticket_stored, [], [], [])

    meeting_stored = {
        "attendees": ["Juan"],
        "date": {"day": 26, "month": 11},
        "time": {"hour": 10, "minute": 30},
    }
    incoming = {"attendees": [], "date": {}, "time": {"hour": None}}
    result = merge_args(MEETING, meeting_stored, incoming, "nada nuevo")
    assert result == MergeResult(meeting_stored, [], [], [])

    # Zero is a value.
    result = merge_args(MEETING, {}, {"duration_minutes": 0}, "cero minutos")
    assert result.args == {"duration_minutes": 0}
    assert result.changes == [Change("duration_minutes", "fill", None, 0)]


# 6
def test_last_scalar_wins_equal_changes_nothing():
    stored = {"priority": "Alta"}

    same = merge_args(TICKET, stored, {"priority": "alta "}, "alta")
    assert same.args == {"priority": "Alta"}
    assert same.changes == []

    other = merge_args(TICKET, stored, {"priority": "media"}, "mejor media")
    assert other.args == {"priority": "media"}
    assert other.changes == [Change("priority", "overwrite", "Alta", "media")]


# 7
def test_date_and_time_are_replaced_whole():
    result = merge_args(MEETING, {"date": {"day": 26, "month": 11}}, {"date": {"day": 27}}, "el 27")
    assert result.args == {"date": {"day": 27}}  # the month is lost; the validator asks (D5.1)
    assert result.changes == [Change("date", "overwrite", {"day": 26, "month": 11}, {"day": 27})]

    result = merge_args(
        MEETING, {"time": {"hour": 10, "minute": 30}}, {"time": {"hour": 11}}, "a las 11"
    )
    assert result.args == {"time": {"hour": 11}}
    assert [change.kind for change in result.changes] == ["overwrite"]

    stored = {"date": {"weekday": "Viernes"}}
    result = merge_args(MEETING, stored, {"date": {"weekday": "viernes"}}, "el viernes")
    assert result.args == stored
    assert result.changes == []


# 8
@pytest.mark.parametrize(
    ("key", "expected", "kind"),
    [
        ("L1", ["Juan", "Ana"], "overwrite"),
        ("L2", None, "clear"),
        ("L3", ["Ana"], "overwrite"),
        ("L4", ["Ana"], "overwrite"),
    ],
)
def test_list_scenarios_l1_to_l4(key, expected, kind):
    stored = f1_stored()
    assert stored == {"attendees": ["Juan"]}

    result = merge_fixed(MEETING, stored, key)

    assert result.args.get("attendees") == expected
    assert result.changes == [Change("attendees", kind, ["Juan"], expected)]
    assert result.rejected == []
    assert result.notes == []


def test_list_replace_then_add_then_remove():
    incoming = {
        "attendees": ["Ana", "Luis"],
        "attendees_add": ["Pedro"],
        "attendees_remove": ["Luis"],
    }
    message = "Con Ana y Luis, agrega a Pedro y quita a Luis"
    result = merge_args(MEETING, f1_stored(), incoming, message)

    assert result.args == {"attendees": ["Ana", "Pedro"]}
    assert result.changes == [Change("attendees", "overwrite", ["Juan"], ["Ana", "Pedro"])]


# 9
def test_list_edge_cases_of_the_d2_amendment():
    stored = f1_stored()

    absent = merge_args(MEETING, stored, {"attendees_remove": ["Ana"]}, "Quita a Ana")
    assert absent.args == stored
    assert absent.changes == []
    assert absent.notes == [Note("attendees", "remove_absent", "Ana")]

    present = merge_args(MEETING, stored, {"attendees_add": ["Juan"]}, "Agrega a Juan")
    assert present.args == stored
    assert present.changes == []
    assert present.notes == [Note("attendees", "add_present", "Juan")]

    both = merge_args(
        MEETING,
        stored,
        {"attendees_add": ["Ana"], "attendees_remove": ["Ana"]},
        "Agrega a Ana, no, quita a Ana",
    )
    assert both.args == stored  # Ana is left out: remove wins
    assert both.changes == []
    assert both.notes == [Note("attendees", "add_and_remove", "Ana")]

    repeated = merge_args(MEETING, stored, {"attendees": ["juan", "Juan"]}, "con juan, Juan")
    assert repeated.args == {"attendees": ["Juan"]}  # the stored spelling stays
    assert repeated.changes == []
    assert repeated.notes == []


# 10
def test_merge_ignores_validity():
    result = merge_fixed(TICKET, {"priority": "Alta"}, "X1")
    assert result.args["priority"] == "urgente"
    assert Change("priority", "overwrite", "Alta", "urgente") in result.changes

    result = merge_fixed(MEETING, {}, "D1")
    assert result.args["date"] == {"day": 40, "month": 11}
    assert result.args["time"] == {"hour": 25, "minute": 90}


# 11
def test_new_flow_merges_against_empty_record():
    result = merge_args(MEETING, {}, {"attendees_add": ["Ana"]}, "Agenda una reunión con Ana")

    assert result.args == {"attendees": ["Ana"]}
    assert not any(key.endswith(("_add", "_remove")) for key in result.args)
    assert result.changes == [Change("attendees", "fill", None, ["Ana"])]

    result = merge_fixed(TICKET, {}, "B1")
    assert result.changes == [
        Change("description", "fill", None, "revisar el problema de autenticación")
    ]


# 12
def test_only_contract_fields_are_written():
    incoming = {
        "title": "Caída",
        "ticket_id": "TCK-0001",
        "recipient": "Carlos",
        "date": {"day": 1},
    }
    result = merge_args(TICKET, {}, incoming, "Caída, avísale a Carlos el 1")

    assert result.args == {"title": "Caída"}
    assert set(result.args) <= {spec.name for spec in TICKET.fields}


# 13
def test_case_b_second_message():
    stored = fixed_args("B1")
    result = merge_fixed(TICKET, stored, "B2")

    assert result.args == {
        "description": "revisar el problema de autenticación",
        "priority": "Alta",
        "assignee": "Carlos",
    }
    assert result.changes == [
        Change("priority", "fill", None, "Alta"),
        Change("assignee", "fill", None, "Carlos"),
    ]
    assert result.notes == []
    assert result.rejected == []


# 14
def test_repeated_request_changes_nothing():
    stored = merge_fixed(TICKET, {}, "H1").args

    result = merge_fixed(TICKET, stored, "H1")

    assert result == MergeResult(stored, [], [], [])


# 15
def test_rules_are_declared_per_kind():
    class ReviewArgs(BaseModel):
        owner: str
        reviewers: list[str]

    review = ToolSpec(
        name="request_review",
        description="Ask people to review a document.",
        action_phrase="pedir una revisión",
        short_name="revisión",
        fields=(
            FieldSpec("owner", FieldKind.PERSON, "Owner.", "el responsable"),
            FieldSpec("reviewers", FieldKind.PERSON_LIST, "Reviewers.", "los revisores"),
        ),
        contract=ReviewArgs,
        result_id_field="review_id",
        execute=lambda args: {"success": True},
    )
    stored = {"owner": "Marta", "reviewers": ["Luis"]}
    incoming = {"owner": "ella", "reviewers_add": ["Sofía", "Pablo"], "reviewers_remove": ["Luis"]}

    result = merge_args(review, stored, incoming, "que lo lleve ella; agrega a Sofia, quita a Luis")

    assert result.args == {"owner": "Marta", "reviewers": ["Sofía"]}
    assert result.rejected == [Rejected("owner", "ella"), Rejected("reviewers_add", "Pablo")]
    assert result.changes == [Change("reviewers", "overwrite", ["Luis"], ["Sofía"])]


# 16
def test_merge_does_not_mutate_inputs():
    stored = {"attendees": ["Juan", "Luis"], "date": {"day": 26, "month": 11}, "title": "Revisión"}
    incoming = {
        "attendees": ["Ana", "Luis"],
        "attendees_add": ["Pedro"],
        "attendees_remove": ["Luis"],
        "date": {"day": 27, "weekday": ""},
        "time": {"hour": None},
    }
    stored_before, incoming_before = copy.deepcopy(stored), copy.deepcopy(incoming)

    result = merge_args(MEETING, stored, incoming, "Con Ana y Luis, agrega a Pedro, quita a Luis")
    result.args["attendees"].append("X")
    result.args["date"]["day"] = 0

    assert stored == stored_before
    assert incoming == incoming_before


# Review 07, finding 1: only `person` values and list names are anchored (D4 rule 3).
def test_other_kinds_are_not_anchored():
    incoming = {"title": "Falla de login", "priority": "alta", "description": "no puedo entrar"}
    result = merge_args(TICKET, {}, incoming, "no puedo entrar")

    assert result.args == incoming
    assert [change.kind for change in result.changes] == ["fill", "fill", "fill"]
    assert result.rejected == []


# Review 07, finding 2: every bucket is anchored, not only `_add` (D4 rule 3).
def test_replacement_and_remove_buckets_are_anchored():
    replaced = merge_args(MEETING, {}, {"attendees": ["Ana", "Pedro"]}, "con Ana")
    assert replaced.args == {"attendees": ["Ana"]}
    assert replaced.rejected == [Rejected("attendees", "Pedro")]
    assert replaced.changes == [Change("attendees", "fill", None, ["Ana"])]

    stored = f1_stored()
    removed = merge_args(MEETING, stored, {"attendees_remove": ["Juan"]}, "quítalo")
    assert removed.args == stored
    assert removed.changes == []
    assert removed.notes == []
    assert removed.rejected == [Rejected("attendees_remove", "Juan")]


# Review 07, note 5: a replacement list with every name rejected counts as not sent (rule 2).
def test_all_rejected_replacement_keeps_stored_list():
    stored = f1_stored()
    result = merge_args(MEETING, stored, {"attendees": ["Pedro"]}, "con él")

    assert result.args == stored
    assert result.changes == []
    assert result.rejected == [Rejected("attendees", "Pedro")]


# Review 07, finding 3: a repeated name is kept once, with its first spelling (D4 rule 2).
def test_repeated_name_is_kept_once():
    result = merge_args(MEETING, {}, {"attendees": ["Ana", "ana"]}, "con Ana y ana")
    assert result.args == {"attendees": ["Ana"]}
    assert result.changes == [Change("attendees", "fill", None, ["Ana"])]

    result = merge_args(MEETING, f1_stored(), {"attendees_add": ["Ana", "ANA"]}, "agrega a Ana")
    assert result.args == {"attendees": ["Juan", "Ana"]}
    assert result.notes == []


# Review 07, finding 4: `_remove` compares the whole normalized name, never a part (D4 rule 6).
@pytest.mark.parametrize("stored_name", ["Juan Pérez", "Juana"])
def test_remove_compares_whole_names(stored_name):
    stored = {"attendees": [stored_name]}
    result = merge_args(MEETING, stored, {"attendees_remove": ["Juan"]}, "quita a Juan")

    assert result.args == stored
    assert result.changes == []
    assert result.notes == [Note("attendees", "remove_absent", "Juan")]


def test_remove_keeps_a_longer_name_that_contains_it():
    stored = {"attendees": ["Juan", "Juan Pérez"]}
    result = merge_args(MEETING, stored, {"attendees_remove": ["Juan"]}, "quita a Juan")

    assert result.args == {"attendees": ["Juan Pérez"]}
    assert result.notes == []


# Review 07, note 5: outside strings, equal means same type and value (stricter than rule 5).
def test_non_string_equality_needs_the_same_type():
    result = merge_args(MEETING, {"duration_minutes": 30}, {"duration_minutes": 30}, "30")
    assert result.changes == []

    result = merge_args(MEETING, {"duration_minutes": 30}, {"duration_minutes": 30.0}, "30")
    assert result.changes == [Change("duration_minutes", "overwrite", 30, 30.0)]


# Review 07 round 2, finding 1: a stored name in `_add` and `_remove` is removed (D4 rule 6,
# *Quitar al final*).
def test_stored_name_in_add_and_remove_is_removed():
    result = merge_args(
        MEETING,
        f1_stored(),
        {"attendees_add": ["Juan"], "attendees_remove": ["Juan"]},
        "Agrega a Juan, no, quita a Juan",
    )

    assert result.args == {}
    assert result.changes == [Change("attendees", "clear", ["Juan"], None)]
    assert result.notes == [Note("attendees", "add_and_remove", "Juan")]


# Review 07 round 2, finding 2: `_remove` compares the normalized name (D4 rule 6).
def test_remove_matches_the_normalized_name():
    stored = {"attendees": ["Sofía", "Juan"]}
    result = merge_args(MEETING, stored, {"attendees_remove": ["sofia"]}, "quita a sofia")

    assert result.args == {"attendees": ["Juan"]}
    assert result.changes == [Change("attendees", "overwrite", ["Sofía", "Juan"], ["Juan"])]
    assert result.notes == []


# Review 07 round 2, finding 3: lists are compared as sets, so another order is no change.
def test_same_names_in_another_order_change_nothing():
    stored = {"attendees": ["Juan", "Ana"]}
    result = merge_args(MEETING, stored, {"attendees": ["Ana", "Juan"]}, "con Ana y Juan")

    assert result == MergeResult(stored, [], [], [])


# Review 07 round 2, note 4: every key of a date counts; adding the month is a change.
def test_date_with_a_new_component_is_an_overwrite():
    stored = {"date": {"day": 27}}
    result = merge_args(MEETING, stored, {"date": {"day": 27, "month": 11}}, "el 27 de noviembre")

    assert result.args == {"date": {"day": 27, "month": 11}}
    assert result.changes == [Change("date", "overwrite", {"day": 27}, {"day": 27, "month": 11})]


# Review 07 round 2, note 5: removing from a record without the list stays absent.
def test_remove_from_an_absent_list_only_notes():
    result = merge_args(MEETING, {}, {"attendees_remove": ["Ana"]}, "quita a Ana")

    assert result == MergeResult({}, [], [], [Note("attendees", "remove_absent", "Ana")])


# Should-fix of review 07-3: "add one that is already there" is measured against the turn's
# base (`<field>` when it came), not only against what was stored (D37).
def test_add_present_is_measured_against_the_turn_base():
    incoming = {"attendees": ["Ana"], "attendees_add": ["ana"]}

    result = merge_args(MEETING, {}, incoming, "con Ana")

    assert result.args == {"attendees": ["Ana"]}
    assert result.changes == [Change("attendees", "fill", None, ["Ana"])]
    assert result.notes == [Note("attendees", "add_present", "ana")]
    assert result.rejected == []
