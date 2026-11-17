"""Anchoring and merge of the arguments of one step (D4 rules 1-8 and 10).

The model transcribes; the code decides what is kept. `merge_args` takes the arguments the
interpreter read in the current message (`incoming`) and the ones already collected for one
step of one tool (`stored`), and returns the new arguments plus everything that happened:
changes, rejected names and notes. A name that is not written in the message does not get in,
the absent never erases, the new replaces, and every change is reported.

Order inside `merge_args`:
1. Filter (rule 10): only the fields of the tool count and, for a `person_list` field, its
   `<field>_add` / `<field>_remove` buckets. Buckets are never written to `args`.
2. Absent (rule 2): `presence.clean` on every value. The absent never changes what is stored.
3. Hard anchoring (rules 3 and 4): `person` values and every name of every list bucket must be
   written in this message. A name that fails is dropped alone and reported as rejected.
   Stored values are never anchored again.
4. Scalars, dates and times (rule 5): the last value wins, whole. Equal after `normalize` ->
   nothing changes and the stored spelling stays.
5. Lists (rule 6): start from `<field>` if it came, else from the stored list; add `_add`; then
   remove `_remove`. Lists are compared as sets of normalized names.
6. No validity (rule 7): "urgente" or day 40 are kept as said; the validator reports them.
7. Changes (rule 8): `fill`, `overwrite` or `clear` per field, with old and new values.

Every rule is chosen by `FieldSpec.kind`, never by tool or field name (rule 1; TASK.md line
562). The function is pure: it mutates neither `stored` nor `incoming`. It works on one step of
one tool (D18), so a plan merges each of its steps with the same call.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from core.presence import clean
from core.registry import FieldKind, FieldSpec, ToolSpec, list_buckets
from core.text import normalize

# D4 rule 3, written as the rule lists them and normalized like the names ("él" -> "el").
EXCLUDED_WORDS: frozenset[str] = frozenset(
    normalize(word)
    for word in (
        "yo", "mi", "me", "tu", "ti", "te", "el", "ella", "usted", "ustedes", "nosotros",
        "nosotras", "ellos", "ellas", "le", "les", "lo", "la", "los", "las", "se", "nos",
    )
)  # fmt: skip


@dataclass(frozen=True)
class Change:
    """One field that changed (D4 rule 8). `old` is None for a fill, `new` for a clear."""

    field: str
    kind: Literal["fill", "overwrite", "clear"]
    old: Any
    new: Any


@dataclass(frozen=True)
class Note:
    """A list operation that changed nothing, or a contradiction (D4 rule 6).

    `remove_absent`: removing a name that is not in the list. `add_present`: adding a name that
    is already in it. `add_and_remove`: the same name in both buckets; it is left out.
    """

    field: str
    kind: Literal["remove_absent", "add_present", "add_and_remove"]
    name: str


@dataclass(frozen=True)
class Rejected:
    """A name not written in the message (D4 rule 3).

    `key` is the key it arrived under, a field or one of its buckets ("<list>_add"), so the
    log can say where it came from. The reply never shows the name (D4 rule 9).
    """

    key: str
    name: str


@dataclass
class MergeResult:
    args: dict[str, Any]  # contract fields only, absent ones left out
    changes: list[Change]
    rejected: list[Rejected]
    notes: list[Note]


def _words(text: str) -> list[str]:
    """Whole words of `text` after `normalize`: "asígnalo" is one word, "Ana-María" two."""
    return re.findall(r"\w+", normalize(text))


def anchored(name: str, message: str) -> bool:
    """D4 rule 3: every word of `name` outside `EXCLUDED_WORDS` is a whole word of `message`.

    At least one such word must remain, so a bare pronoun ("Él") never anchors. A substring is
    not enough: "Ana" is not anchored by "mañana".
    """
    name_words = [word for word in _words(name) if word not in EXCLUDED_WORDS]
    message_words = set(_words(message))
    return bool(name_words) and all(word in message_words for word in name_words)


def merge_args(
    tool: ToolSpec, stored: Mapping[str, Any], incoming: Mapping[str, Any], message: str
) -> MergeResult:
    """Anchor `incoming` against `message` and merge it into `stored` (D4).

    `stored` holds the collected arguments of one step of `tool`; `incoming` the arguments the
    interpreter read in `message`. Neither is mutated: `clean` returns new lists and dicts.
    """
    result = MergeResult(args={}, changes=[], rejected=[], notes=[])
    for spec in tool.fields:
        old = clean(stored.get(spec.name))
        if spec.kind == FieldKind.PERSON_LIST:
            new = _merge_list(spec, old, incoming, message, result)
        else:
            new = _merge_scalar(spec, old, incoming.get(spec.name), message, result)
        if new is not None:
            result.args[spec.name] = new
    return result


def _merge_scalar(spec: FieldSpec, old: Any, raw: Any, message: str, result: MergeResult) -> Any:
    """Rule 5 for `text`, `word`, `int`, `person`, `date` and `time`. Returns the kept value."""
    new = clean(raw)
    if new is not None and spec.kind == FieldKind.PERSON and not anchored(new, message):
        result.rejected.append(Rejected(spec.name, new))
        new = None
    if new is None or (old is not None and _same(old, new)):
        return old  # absent never changes the stored value; an equal one keeps its spelling
    kind = "fill" if old is None else "overwrite"
    result.changes.append(Change(spec.name, kind, old, new))
    return new


def _merge_list(
    spec: FieldSpec,
    old: list[str] | None,
    incoming: Mapping[str, Any],
    message: str,
    result: MergeResult,
) -> list[str] | None:
    """Rule 6 for a `person_list` field: replace, then add, then remove. Returns the kept list."""
    replace, add, remove = (
        _anchored_names(key, incoming.get(key), message, result)
        for key in (spec.name, *list_buckets(spec))
    )
    if replace is None and add is None and remove is None:
        return old

    names = replace if replace is not None else list(old or [])
    add, remove = add or [], remove or []
    contradicted = {normalize(name) for name in add} & {normalize(name) for name in remove}

    for name in add:
        if normalize(name) in contradicted:
            result.notes.append(Note(spec.name, "add_and_remove", name))
        elif _contains(names, name):
            result.notes.append(Note(spec.name, "add_present", name))
        else:
            names.append(name)
    for name in remove:
        if _contains(names, name):
            names = [kept for kept in names if normalize(kept) != normalize(name)]
        elif normalize(name) not in contradicted:
            result.notes.append(Note(spec.name, "remove_absent", name))

    new = names or None
    if old is not None and new is not None and _as_set(old) == _as_set(new):
        return old  # the same set: no change, and the stored spelling stays
    if old is None and new is None:
        return None
    kind = "fill" if old is None else "clear" if new is None else "overwrite"
    result.changes.append(Change(spec.name, kind, old, new))
    return new


def _anchored_names(key: str, raw: Any, message: str, result: MergeResult) -> list[str] | None:
    """The cleaned names of one bucket that are written in `message`, once each.

    A name that fails is dropped alone and recorded under `key`. None when nothing is left.
    """
    names: list[str] = []
    for name in clean(raw) or []:
        if not anchored(name, message):
            result.rejected.append(Rejected(key, name))
        elif not _contains(names, name):
            names.append(name)  # a repeated name is kept once, with its first spelling
    return names or None


def _contains(names: list[str], name: str) -> bool:
    return normalize(name) in _as_set(names)


def _as_set(names: list[str]) -> set[str]:
    return {normalize(name) for name in names}


def _same(old: Any, new: Any) -> bool:
    """Equal after `normalize` for strings, also inside a date or a time (rule 5)."""
    if isinstance(old, str) and isinstance(new, str):
        return normalize(old) == normalize(new)
    if isinstance(old, dict) and isinstance(new, dict):
        return old.keys() == new.keys() and all(_same(old[key], new[key]) for key in old)
    return type(old) is type(new) and old == new
