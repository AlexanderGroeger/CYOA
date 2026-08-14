"""
engine/core/condition_eval.py

Evaluates story-author-written condition strings like:

    flags.wolf_alive == true
    has_item('torch') and vars.gold >= 10
    not flags.door_locked or has_item('key', 2)

SAFETY: this deliberately never calls eval() or exec() on story data, since
story files may come from an untrusted or simply careless author. We use
Python's `ast.parse` purely as a expression *tokenizer/parser* (it never
executes anything by itself), then walk the resulting tree ourselves with
`_eval_node`, which only knows how to handle a small whitelisted set of
node types:

    - boolean combinators: and / or / not
    - comparisons: == != < <= > >=
    - dotted lookups, restricted to `flags.NAME` and `vars.NAME`
    - exactly one function call form: has_item('id') / has_item('id', qty)
    - literal constants (numbers, strings, true/false/null)

Anything else -- attribute access on arbitrary objects, other function
calls, arithmetic, comprehensions, lambdas, imports, whatever -- raises
ConditionError instead of silently doing something unexpected. There is no
code path in this file that can execute arbitrary Python.
"""

from __future__ import annotations

import ast

from engine.errors import ConditionError
from engine.core.game_state import GameState

_ALLOWED_COMPARE_OPS = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)


def evaluate_condition(expr: str | None, state: GameState) -> bool:
    """Empty/missing condition means 'always available' -- most choices in
    a story won't have one at all."""
    if not expr or not expr.strip():
        return True
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ConditionError(f"Invalid condition syntax: {expr!r} ({e})") from e
    return bool(_eval_node(tree.body, state, expr))


def _eval_node(node: ast.AST, state: GameState, source: str):
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(_eval_node(v, state, source) for v in node.values)
        if isinstance(node.op, ast.Or):
            return any(_eval_node(v, state, source) for v in node.values)
        raise ConditionError(f"Unsupported boolean operator in: {source!r}")

    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return not _eval_node(node.operand, state, source)
        if isinstance(node.op, ast.USub):
            return -_eval_node(node.operand, state, source)
        raise ConditionError(f"Unsupported unary operator in: {source!r}")

    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, state, source)
        for op, comparator in zip(node.ops, node.comparators):
            if not isinstance(op, _ALLOWED_COMPARE_OPS):
                raise ConditionError(f"Unsupported comparison operator in: {source!r}")
            right = _eval_node(comparator, state, source)
            if not _apply_compare(op, left, right):
                return False
            left = right  # supports chained comparisons: 0 <= vars.x < 10
        return True

    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id in ("flags", "vars"):
            if node.attr.startswith("_"):
                raise ConditionError(
                    f"Invalid name {node.attr!r} in: {source!r} "
                    "(no real attribute access happens here -- this is just a "
                    "dict lookup -- but underscore-prefixed names aren't allowed "
                    "as flag/variable names, to keep conditions unambiguous)"
                )
            if node.value.id == "flags":
                return state.get_flag(node.attr)
            return state.get_var(node.attr)
        raise ConditionError(
            f"Unsupported attribute access in: {source!r} "
            "(only flags.NAME and vars.NAME are allowed)"
        )

    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id == "has_item":
            args = [_eval_node(a, state, source) for a in node.args]
            if len(args) == 1:
                return state.has_item(args[0])
            if len(args) == 2:
                return state.has_item(args[0], args[1])
            raise ConditionError(f"has_item() takes 1 or 2 arguments in: {source!r}")
        raise ConditionError(
            f"Unsupported function call in: {source!r} (only has_item(...) is allowed)"
        )

    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.Name):
        # YAML-style lowercase literals, for authors writing == true / == false
        lowered = node.id.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        if lowered in ("null", "none"):
            return None
        raise ConditionError(f"Unknown name {node.id!r} in: {source!r}")

    raise ConditionError(
        f"Unsupported expression element ({type(node).__name__}) in: {source!r}"
    )


def _apply_compare(op: ast.cmpop, left, right) -> bool:
    if isinstance(op, ast.Eq):
        return left == right
    if isinstance(op, ast.NotEq):
        return left != right
    if isinstance(op, ast.Lt):
        return left < right
    if isinstance(op, ast.LtE):
        return left <= right
    if isinstance(op, ast.Gt):
        return left > right
    if isinstance(op, ast.GtE):
        return left >= right
    raise ConditionError("Unsupported comparison operator")
