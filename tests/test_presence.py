import pytest

from core.presence import drop_absent, is_absent


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


# 3 moved to tests/test_validation.py (test_missing_comes_from_the_validator, task 09a).
