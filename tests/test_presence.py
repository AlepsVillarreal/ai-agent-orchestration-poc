import pytest

from core.presence import drop_absent, is_absent, missing_fields
from core.registry import TOOLS


# 1
@pytest.mark.parametrize("value", [None, "", "  ", [], ["", " "], {}, {"weekday": ""}])
def test_absent_values_follow_d4_rule_2(value):
    assert is_absent(value)


@pytest.mark.parametrize("value", [0, {"hour": 0}, ["Ana", ""], "x"])
def test_present_values_follow_d4_rule_2(value):
    assert not is_absent(value)


# 2
def test_drop_absent_keeps_zero_and_cleans_lists():
    args = {"title": " ", "attendees": ["", "Ana"], "duration_minutes": 0, "date": {}}
    assert drop_absent(args) == {"attendees": ["Ana"], "duration_minutes": 0}


# 3
def test_missing_fields_by_presence_in_registry_order():
    meeting = TOOLS["schedule_meeting"]

    assert missing_fields(meeting, {"attendees": ["Juan"]}) == [
        "title",
        "date",
        "time",
        "duration_minutes",
    ]
    # Day 40 is present: whether it is valid is the validator's job (D5).
    args = {"attendees": ["Juan"], "date": {"day": 40, "month": 11}}
    assert "date" not in missing_fields(meeting, args)
