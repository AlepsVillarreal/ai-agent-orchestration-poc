"""What a confirmation shows and binds (D7.1, D7.2).

The values shown in a confirmation, hashed by it and passed to the tool all come from
`validate(...).normalized` of each step, never from `args`, which keeps the user's words
(" Carlos ", "correo"; D4 rule 7). They are recomputed every time and never stored (D44); only
the hash is kept in the record, so a "yes" can be checked against what the user saw.
"""

import hashlib
import json
from typing import Any

from core.registry import TOOLS
from core.validation import validate


def shown_values(steps: list[dict]) -> list[dict[str, Any]]:
    """`[{tool, normalized}]` per step, in step order: what the confirmation shows (D7.1)."""
    return [
        {"tool": step["tool"], "normalized": validate(TOOLS[step["tool"]], step["args"]).normalized}
        for step in steps
    ]


def confirmation_hash(shown: list[dict[str, Any]]) -> str:
    """D7.2: sha256 of the canonical JSON of the shown values (`sort_keys`, no ASCII escapes).

    A "yes" counts only if the hash recomputed when it arrives is the same: a value that
    changed between the confirmation and the "yes" (a date resolved on another day, task 09b)
    asks again instead of running.
    """
    canonical = json.dumps(shown, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
