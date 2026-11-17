"""Absent values and missing fields (D4 rule 2).

A value is absent when it is not there, or when it is a blank string, an empty list or an
empty object. The same rule applies to each component of a date or a time and to each item of
a list: blank items are dropped, and a list or object left with nothing is absent. Zero is a
value (`duration_minutes=0`, `{"hour": 0}`).

This is presence, not validity: day 40 is present here, and so is a date with a day and no
month. The validator decides whether a present value is acceptable (D5).
"""

from collections.abc import Mapping
from typing import Any

from core.registry import ToolSpec


def clean(value: Any) -> Any:
    """Return `value` without its absent parts, or `None` if nothing is left.

    Strings are kept exactly as said; only a blank one is dropped.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value if value.strip() else None
    if isinstance(value, list):
        items = [item for item in map(clean, value) if item is not None]
        return items or None
    if isinstance(value, dict):
        return drop_absent(value) or None
    return value


def is_absent(value: Any) -> bool:
    return clean(value) is None


def drop_absent(args: Mapping[str, Any]) -> dict[str, Any]:
    """Return `args` without absent fields, with blank list items and empty components removed."""
    return {name: kept for name, value in args.items() if (kept := clean(value)) is not None}


def missing_fields(tool: ToolSpec, args: Mapping[str, Any]) -> list[str]:
    """Names of the fields of `tool` that are absent from `args`, in registry order."""
    return [spec.name for spec in tool.fields if is_absent(args.get(spec.name))]
