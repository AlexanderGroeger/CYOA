"""Compatibility-aware condition descriptions for static story tooling.

The runtime currently has two condition dialects.  This module deliberately
keeps them distinct: the legacy expression language is not a serialization
replacement for the structured exploration language.  The implementation is
pure Python and accepts either a runtime-like state object or a plain mapping,
which makes it useful to validation tools without importing pygame or a game
session.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol, runtime_checkable

from .models import freeze_value, thaw_value


class ConditionError(ValueError):
    """An authored condition is syntactically invalid or unsupported."""


class ConditionDialect(str, Enum):
    """The authored syntax used by a condition."""

    EMPTY = "empty"
    LEGACY = "legacy"
    STRUCTURED = "structured"


@runtime_checkable
class ConditionState(Protocol):
    """Small state protocol shared by runtime and headless tooling."""

    def get_flag(self, name: str) -> Any: ...

    def get_var(self, name: str, default: Any = None) -> Any: ...

    def has_item(self, item_id: Any, quantity: Any = 1) -> bool: ...


@dataclass(frozen=True)
class ConditionSymbols:
    """Names referenced by a static condition, separated by state scope."""

    flags: frozenset[str] = frozenset()
    variables: frozenset[str] = frozenset()
    items: frozenset[str] = frozenset()

    def merged(self, other: "ConditionSymbols") -> "ConditionSymbols":
        return ConditionSymbols(
            flags=self.flags | other.flags,
            variables=self.variables | other.variables,
            items=self.items | other.items,
        )


@dataclass(frozen=True)
class StoryCondition:
    """A non-executing static condition envelope.

    ``raw`` intentionally preserves the authored form.  Tooling can inspect
    ``symbols`` and ``dialect`` without being forced to rewrite a condition
    into a different syntax.
    """

    dialect: ConditionDialect
    raw: Any
    symbols: ConditionSymbols = field(default_factory=ConditionSymbols)

    def __post_init__(self) -> None:
        # Conditions are part of the immutable project definition layer too.
        # Freezing a structured condition prevents caller mutations from
        # changing evaluation while its precomputed symbols stay stale.
        object.__setattr__(self, "raw", freeze_value(self.raw))

    def evaluate(self, state: ConditionState | Mapping[str, Any]) -> bool:
        return evaluate_condition(self.raw, state, dialect=self.dialect)

    def to_value(self) -> Any:
        """Return a fresh YAML-compatible copy of the authored condition."""

        return thaw_value(self.raw)


_ALLOWED_COMPARE_OPS = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)


def parse_condition(raw: Any, *, dialect: ConditionDialect | str = "auto") -> StoryCondition:
    """Describe and validate an authored condition without running it.

    ``dialect='auto'`` chooses legacy for strings and structured for every
    other value, matching exploration's current dispatch behavior.
    """

    selected = _select_dialect(raw, dialect)
    if selected is ConditionDialect.EMPTY:
        return StoryCondition(selected, raw)
    if selected is ConditionDialect.LEGACY:
        validate_legacy_condition(raw)
        return StoryCondition(selected, raw, legacy_condition_symbols(raw))
    validate_structured_condition(raw)
    return StoryCondition(selected, raw, structured_condition_symbols(raw))


def evaluate_condition(
    raw: Any,
    state: ConditionState | Mapping[str, Any],
    *,
    dialect: ConditionDialect | str = "auto",
) -> bool:
    """Evaluate either compatibility dialect against a state-like object."""

    selected = _select_dialect(raw, dialect)
    if selected is ConditionDialect.EMPTY:
        return True
    if selected is ConditionDialect.LEGACY:
        return evaluate_legacy_condition(raw, state)
    return evaluate_structured_condition(raw, state)


def evaluate_legacy_condition(expr: Any, state: ConditionState | Mapping[str, Any]) -> bool:
    """Evaluate the safe legacy expression grammar.

    This mirrors ``engine.core.condition_eval`` on purpose, including empty
    expression semantics and the identifier restriction on underscore-prefixed
    flag/variable names.
    """

    # Keep the legacy runtime's deliberately permissive empty check.  Its
    # public evaluator has historically considered *any* falsy value to mean
    # "no condition" before it attempts string parsing.  This matters for
    # old YAML such as ``condition: {}``, even though non-empty non-strings
    # remain invalid legacy expressions.
    if not expr:
        return True
    if not isinstance(expr, str):
        raise ConditionError(f"Legacy condition must be a string, got {expr!r}")
    if not expr.strip():
        return True
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ConditionError(f"Invalid condition syntax: {expr!r} ({exc})") from exc
    return bool(_evaluate_legacy_node(tree.body, state, expr))


def validate_legacy_condition(expr: Any) -> None:
    """Validate the legacy AST grammar without requiring a live state."""

    # See :func:`evaluate_legacy_condition`: preserve the runtime's falsy
    # "always available" compatibility behavior before enforcing strings.
    if not expr:
        return
    if not isinstance(expr, str):
        raise ConditionError(f"Legacy condition must be a string, got {expr!r}")
    if not expr.strip():
        return
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ConditionError(f"Invalid condition syntax: {expr!r} ({exc})") from exc
    _validate_legacy_node(tree.body, expr)


def evaluate_structured_condition(raw: Any, state: ConditionState | Mapping[str, Any]) -> bool:
    """Evaluate the structured exploration condition semantics.

    This preserves two compatibility details that are easy for a future tool
    to miss: empty ``all`` is true while empty ``any`` is false, and leaf
    operator precedence is ``equals``, then ``not_equals``, then ``exists``.
    """

    if raw is None or raw == {}:
        return True
    if isinstance(raw, str):
        return evaluate_legacy_condition(raw, state)
    if isinstance(raw, (list, tuple)):
        return all(evaluate_structured_condition(item, state) for item in raw)
    if not isinstance(raw, Mapping):
        raise ConditionError(f"Exploration conditions must be a mapping or string, got {raw!r}")
    if "condition" in raw:
        condition = raw["condition"]
        if not isinstance(condition, str):
            raise ConditionError("Exploration condition must be a string")
        if not evaluate_legacy_condition(condition, state):
            return False
    if "all" in raw:
        entries = raw["all"]
        if not isinstance(entries, (list, tuple)):
            raise ConditionError("Exploration conditions.all must be a list")
        if not all(evaluate_structured_condition(entry, state) for entry in entries):
            return False
    if "any" in raw:
        entries = raw["any"]
        if not isinstance(entries, (list, tuple)):
            raise ConditionError("Exploration conditions.any must be a list")
        if not any(evaluate_structured_condition(entry, state) for entry in entries):
            return False
    if "not" in raw and evaluate_structured_condition(raw["not"], state):
        return False

    leaf_keys = {"flag", "variable", "var", "has_item"}
    if leaf_keys & set(raw):
        return _evaluate_structured_leaf(raw, state)

    # The existing evaluator only rejects unknown keys when there is no leaf.
    # ``validate_structured_condition`` deliberately remains stricter.
    known = {"condition", "all", "any", "not"} | leaf_keys | {
        "equals", "not_equals", "quantity", "exists"
    }
    unknown = set(raw) - known
    if unknown:
        raise ConditionError(
            f"Unknown exploration condition field(s): {', '.join(sorted(str(key) for key in unknown))}"
        )
    return True


def validate_structured_condition(raw: Any, context: str = "conditions") -> None:
    """Validate the existing structured-condition shape without a state."""

    if raw is None or raw == {} or isinstance(raw, str):
        return
    if isinstance(raw, (list, tuple)):
        for index, entry in enumerate(raw):
            validate_structured_condition(entry, f"{context}[{index}]")
        return
    if not isinstance(raw, Mapping):
        raise ConditionError(f"{context} must be a mapping or condition string")
    for key in ("all", "any"):
        if key in raw:
            entries = raw[key]
            if not isinstance(entries, (list, tuple)):
                raise ConditionError(f"{context}.{key} must be a list")
            for index, entry in enumerate(entries):
                validate_structured_condition(entry, f"{context}.{key}[{index}]")
    if "not" in raw:
        validate_structured_condition(raw["not"], f"{context}.not")
    if "condition" in raw and not isinstance(raw["condition"], str):
        raise ConditionError(f"{context}.condition must be a string")
    leaves = [key for key in ("flag", "variable", "var", "has_item") if key in raw]
    if len(leaves) > 1:
        raise ConditionError(f"{context} has more than one condition subject")
    if leaves:
        name = raw[leaves[0]]
        if not isinstance(name, str) or not name:
            raise ConditionError(f"{context}.{leaves[0]} must be a non-empty string")
    if "quantity" in raw and (
        isinstance(raw["quantity"], bool)
        or not isinstance(raw["quantity"], int)
        or raw["quantity"] < 1
    ):
        raise ConditionError(f"{context}.quantity must be a positive integer")
    known = {
        "condition", "all", "any", "not", "flag", "variable", "var", "has_item",
        "equals", "not_equals", "quantity", "exists",
    }
    unknown = set(raw) - known
    if unknown:
        raise ConditionError(
            f"{context} has unknown field(s): {', '.join(sorted(str(key) for key in unknown))}"
        )


def legacy_condition_symbols(expr: Any) -> ConditionSymbols:
    """Return statically discoverable legacy flag/variable/item references."""

    if not expr:
        return ConditionSymbols()
    if isinstance(expr, str) and not expr.strip():
        return ConditionSymbols()
    validate_legacy_condition(expr)
    tree = ast.parse(expr, mode="eval")
    flags: set[str] = set()
    variables: set[str] = set()
    items: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "flags":
                flags.add(node.attr)
            elif node.value.id == "vars":
                variables.add(node.attr)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "has_item"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            items.add(node.args[0].value)
    return ConditionSymbols(frozenset(flags), frozenset(variables), frozenset(items))


def structured_condition_symbols(raw: Any) -> ConditionSymbols:
    """Collect references from structured conditions (including strings)."""

    if raw is None or raw == {}:
        return ConditionSymbols()
    if isinstance(raw, str):
        return legacy_condition_symbols(raw)
    if isinstance(raw, (list, tuple)):
        result = ConditionSymbols()
        for item in raw:
            result = result.merged(structured_condition_symbols(item))
        return result
    if not isinstance(raw, Mapping):
        return ConditionSymbols()
    result = ConditionSymbols()
    if "condition" in raw and isinstance(raw["condition"], str):
        result = result.merged(legacy_condition_symbols(raw["condition"]))
    for key in ("all", "any"):
        entries = raw.get(key)
        if isinstance(entries, (list, tuple)):
            for item in entries:
                result = result.merged(structured_condition_symbols(item))
    if "not" in raw:
        result = result.merged(structured_condition_symbols(raw["not"]))
    if isinstance(raw.get("flag"), str):
        result = result.merged(ConditionSymbols(flags=frozenset({raw["flag"]})))
    variable = raw.get("variable", raw.get("var"))
    if isinstance(variable, str):
        result = result.merged(ConditionSymbols(variables=frozenset({variable})))
    if isinstance(raw.get("has_item"), str):
        result = result.merged(ConditionSymbols(items=frozenset({raw["has_item"]})))
    return result


def _select_dialect(raw: Any, dialect: ConditionDialect | str) -> ConditionDialect:
    if isinstance(dialect, str):
        try:
            dialect = ConditionDialect(dialect)
        except ValueError:
            if dialect != "auto":
                raise ValueError(f"Unknown condition dialect {dialect!r}") from None
            dialect = None
    if dialect is not None:
        return dialect
    if raw is None or raw == {}:
        return ConditionDialect.EMPTY
    return ConditionDialect.LEGACY if isinstance(raw, str) else ConditionDialect.STRUCTURED


def _evaluate_legacy_node(node: ast.AST, state: ConditionState | Mapping[str, Any], source: str) -> Any:
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(_evaluate_legacy_node(value, state, source) for value in node.values)
        if isinstance(node.op, ast.Or):
            return any(_evaluate_legacy_node(value, state, source) for value in node.values)
        raise ConditionError(f"Unsupported boolean operator in: {source!r}")
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return not _evaluate_legacy_node(node.operand, state, source)
        if isinstance(node.op, ast.USub):
            return -_evaluate_legacy_node(node.operand, state, source)
        raise ConditionError(f"Unsupported unary operator in: {source!r}")
    if isinstance(node, ast.Compare):
        left = _evaluate_legacy_node(node.left, state, source)
        for operator, comparator in zip(node.ops, node.comparators):
            if not isinstance(operator, _ALLOWED_COMPARE_OPS):
                raise ConditionError(f"Unsupported comparison operator in: {source!r}")
            right = _evaluate_legacy_node(comparator, state, source)
            if not _apply_compare(operator, left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id in {"flags", "vars"}:
            if node.attr.startswith("_"):
                raise ConditionError(
                    f"Invalid name {node.attr!r} in: {source!r} "
                    "(no real attribute access happens here -- this is just a "
                    "dict lookup -- but underscore-prefixed names aren't allowed "
                    "as flag/variable names, to keep conditions unambiguous)"
                )
            return _flag_value(state, node.attr) if node.value.id == "flags" else _variable_value(state, node.attr)
        raise ConditionError(
            f"Unsupported attribute access in: {source!r} (only flags.NAME and vars.NAME are allowed)"
        )
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id == "has_item":
            args = [_evaluate_legacy_node(argument, state, source) for argument in node.args]
            if len(args) == 1:
                return _has_item(state, args[0])
            if len(args) == 2:
                return _has_item(state, args[0], args[1])
            raise ConditionError(f"has_item() takes 1 or 2 arguments in: {source!r}")
        raise ConditionError(f"Unsupported function call in: {source!r} (only has_item(...) is allowed)")
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        lowered = node.id.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        if lowered in {"null", "none"}:
            return None
        raise ConditionError(f"Unknown name {node.id!r} in: {source!r}")
    raise ConditionError(f"Unsupported expression element ({type(node).__name__}) in: {source!r}")


def _validate_legacy_node(node: ast.AST, source: str) -> None:
    if isinstance(node, ast.BoolOp):
        if not isinstance(node.op, (ast.And, ast.Or)):
            raise ConditionError(f"Unsupported boolean operator in: {source!r}")
        for value in node.values:
            _validate_legacy_node(value, source)
        return
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, (ast.Not, ast.USub)):
            raise ConditionError(f"Unsupported unary operator in: {source!r}")
        _validate_legacy_node(node.operand, source)
        return
    if isinstance(node, ast.Compare):
        _validate_legacy_node(node.left, source)
        for operator, comparator in zip(node.ops, node.comparators):
            if not isinstance(operator, _ALLOWED_COMPARE_OPS):
                raise ConditionError(f"Unsupported comparison operator in: {source!r}")
            _validate_legacy_node(comparator, source)
        return
    if isinstance(node, ast.Attribute):
        if not (
            isinstance(node.value, ast.Name)
            and node.value.id in {"flags", "vars"}
            and not node.attr.startswith("_")
        ):
            raise ConditionError(
                f"Unsupported attribute access in: {source!r} (only flags.NAME and vars.NAME are allowed)"
            )
        return
    if isinstance(node, ast.Call):
        if not (isinstance(node.func, ast.Name) and node.func.id == "has_item"):
            raise ConditionError(f"Unsupported function call in: {source!r} (only has_item(...) is allowed)")
        if len(node.args) not in {1, 2}:
            raise ConditionError(f"has_item() takes 1 or 2 arguments in: {source!r}")
        for argument in node.args:
            _validate_legacy_node(argument, source)
        return
    if isinstance(node, ast.Constant):
        return
    if isinstance(node, ast.Name):
        if node.id.lower() in {"true", "false", "null", "none"}:
            return
        raise ConditionError(f"Unknown name {node.id!r} in: {source!r}")
    raise ConditionError(f"Unsupported expression element ({type(node).__name__}) in: {source!r}")


def _evaluate_structured_leaf(raw: Mapping[str, Any], state: ConditionState | Mapping[str, Any]) -> bool:
    active = [key for key in ("flag", "variable", "var", "has_item") if key in raw]
    if len(active) != 1:
        raise ConditionError("An exploration condition leaf requires exactly one of flag, variable, or has_item")
    kind = active[0]
    name = raw[kind]
    if not isinstance(name, str) or not name:
        raise ConditionError(f"Exploration condition {kind} must be a non-empty string")
    if kind == "flag":
        value: Any = _flag_value(state, name)
    elif kind in {"variable", "var"}:
        value = _variable_value(state, name)
    else:
        quantity = raw.get("quantity", 1)
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            raise ConditionError("Exploration has_item quantity must be a positive integer")
        value = _has_item(state, name, quantity)
    if "equals" in raw:
        return value == raw["equals"]
    if "not_equals" in raw:
        return value != raw["not_equals"]
    if "exists" in raw:
        return bool(value) is bool(raw["exists"])
    return bool(value)


def _flag_value(state: ConditionState | Mapping[str, Any], name: str) -> Any:
    if isinstance(state, Mapping):
        flags = state.get("flags", {})
        # ``GameState.get_flag`` returns a boolean, not its unnormalised
        # stored payload.  Keep plain mapping adapters semantically aligned.
        return bool(flags.get(name, False)) if isinstance(flags, Mapping) else False
    return state.get_flag(name)


def _variable_value(state: ConditionState | Mapping[str, Any], name: str) -> Any:
    if isinstance(state, Mapping):
        variables = state.get("variables", state.get("vars", {}))
        # Match ``GameState.get_var(name)``: unset variables read as zero.
        return variables.get(name, 0) if isinstance(variables, Mapping) else 0
    try:
        return state.get_var(name)
    except TypeError:
        return state.get_var(name, None)


def _has_item(state: ConditionState | Mapping[str, Any], item_id: Any, quantity: Any = 1) -> bool:
    if isinstance(state, Mapping):
        inventory = state.get("inventory", {})
        if isinstance(inventory, Mapping):
            try:
                return int(inventory.get(item_id, 0)) >= int(quantity)
            except (TypeError, ValueError):
                return False
        if isinstance(inventory, (list, tuple, set)):
            return item_id in inventory
        return False
    return state.has_item(item_id, quantity)


def _apply_compare(operator: ast.cmpop, left: Any, right: Any) -> bool:
    if isinstance(operator, ast.Eq):
        return left == right
    if isinstance(operator, ast.NotEq):
        return left != right
    if isinstance(operator, ast.Lt):
        return left < right
    if isinstance(operator, ast.LtE):
        return left <= right
    if isinstance(operator, ast.Gt):
        return left > right
    if isinstance(operator, ast.GtE):
        return left >= right
    raise ConditionError("Unsupported comparison operator")
