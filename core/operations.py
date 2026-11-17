"""The interpreter's operations as the core reads them: the registry filter and D11.1.

Moved out of `core/turn.py` unchanged (note (A13)): `filter_by_registry` is step 1 of
the D3 gate (D2, D10) and `join_split_operations` is the D11.1 join that only the *Misma tool*
branch applies (D35, D54). Neither reads the message text nor decides a reply.
"""

from core.interpretation import TurnInterpretation
from core.presence import drop_absent
from core.registry import TOOLS, FieldKind, ToolSpec, list_buckets


def filter_by_registry(interpretation: TurnInterpretation) -> list[dict]:
    """D3 step 1 and D2's contract filter, as plain dicts.

    Operations of a tool not in `TOOLS` are dropped (D10). The rest keep only the keys of
    their tool (its field names and, for a `person_list` field, its `_add`/`_remove` buckets)
    and their `depends_on` (None when the model gave none), which tasks 08b and 14a read.
    """
    kept = []
    for operation in interpretation.operations:
        tool = TOOLS.get(operation.tool)
        if tool is None:
            continue
        keys = _contract_keys(tool)
        args = operation.args.model_dump(exclude_none=True)
        kept.append(
            {
                "tool": tool.name,
                "certainty": operation.certainty,
                "depends_on": operation.depends_on,
                "args": {name: value for name, value in args.items() if name in keys},
            }
        )
    return kept


def _contract_keys(tool: ToolSpec) -> set[str]:
    keys = set()
    for spec in tool.fields:
        keys.add(spec.name)
        if spec.kind == FieldKind.PERSON_LIST:
            keys.update(list_buckets(spec))
    return keys


def join_split_operations(operations: list[dict]) -> list[dict]:
    """D11.1: join the operations of one tool into one when their args share no key.

    Keys are counted after the registry filter and without absent values (D4 rule 2), so a
    `""` in one operation never overwrites a value of another. Each `_add`/`_remove` bucket is
    its own key. The whole group of a tool is joined, or none of it: a repeated key or a
    different `depends_on` (None included, D35) leaves the group as it came, and D11 asks.
    The joined operation keeps the first one's `certainty`, which *Misma tool* ignores (D2).
    """
    result = []
    for tool in dict.fromkeys(operation["tool"] for operation in operations):
        group = [operation for operation in operations if operation["tool"] == tool]
        args_list = [drop_absent(operation["args"]) for operation in group]
        keys = [key for args in args_list for key in args]
        same_dependency = len({operation["depends_on"] for operation in group}) == 1
        if len(group) == 1 or len(set(keys)) < len(keys) or not same_dependency:
            result += group
            continue
        joined_args = {key: value for args in args_list for key, value in args.items()}
        result.append({**group[0], "args": joined_args})
    return result
