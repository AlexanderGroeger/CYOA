"""Qt-independent condition editing models.

The model in this module is deliberately a thin presentation layer over the
condition vocabulary implemented by :mod:`engine.story_core.conditions`.  It
does not evaluate conditions or invent a new serialized language.  Unsupported
values stay as opaque payloads so opening a project is never a migration.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from engine.story_core.conditions import (
    ConditionError,
    STRUCTURED_GROUP_OPERATORS,
    STRUCTURED_LEAF_OPERATORS,
    STRUCTURED_LEAF_PARAMETERS,
    parse_condition,
    validate_structured_condition,
)
from engine.story_core.schema import MISSING


GROUP_TYPES = STRUCTURED_GROUP_OPERATORS
LEAF_TYPES = STRUCTURED_LEAF_OPERATORS
NODE_TYPES = GROUP_TYPES + LEAF_TYPES
PARAMETER_TYPES = STRUCTURED_LEAF_PARAMETERS


def condition_symbol_candidates(symbols: Any | None, kind: str) -> tuple[str, ...]:
    """Return editable suggestions from the current project-symbol shape.

    ``ProjectSymbols`` intentionally separates declarations from references
    because flags and variables are dynamic.  The condition editor only needs
    the union for suggestions; the line edit remains editable so new names
    are still valid.  Keeping this translation here prevents Qt widgets and
    condition nodes from depending on that storage detail.
    """

    if symbols is None:
        return ()
    normalized = str(kind).strip().lower()
    if normalized == "flag":
        attributes = ("declared_flags", "referenced_flags")
    elif normalized in {"variable", "var"}:
        attributes = ("declared_variables", "referenced_variables")
    elif normalized == "has_item":
        attributes = ("referenced_items",)
    else:
        return ()
    values: set[str] = set()
    for attribute in attributes:
        values.update(str(value) for value in getattr(symbols, attribute, ()) or ())
    return tuple(sorted(values))


def _copy(value: Any) -> Any:
    return MISSING if value is MISSING else deepcopy(value)


def _default_name(kind: str, symbols: "ConditionSymbolsLike | None") -> str:
    values: Iterable[str] = condition_symbol_candidates(symbols, kind)
    return sorted(str(value) for value in values)[0] if values else {
        "flag": "flag_name",
        "variable": "variable_name",
        "var": "variable_name",
        "has_item": "item_id",
    }.get(kind, "flag_name")


class ConditionSymbolsLike:
    """Structural protocol for project symbols used by the editor."""

    declared_flags: Iterable[str]
    declared_variables: Iterable[str]
    referenced_flags: Iterable[str]
    referenced_variables: Iterable[str]
    referenced_items: Iterable[str]


@dataclass
class ConditionNode:
    """One editable condition node.

    ``parameters`` and ``extras`` retain authored fields that the visual
    editor does not actively interpret.  ``raw`` is only used for an
    unsupported node and is serialized byte-for-byte at the mapping level.
    """

    kind: str
    children: list["ConditionNode"] = field(default_factory=list)
    name: str | None = None
    subject_key: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)
    raw: Any = MISSING
    supported: bool = True
    source_shape: str = "mapping"

    @classmethod
    def from_value(cls, value: Any) -> "ConditionNode":
        if isinstance(value, list):
            return cls(
                "list",
                children=[cls.from_value(item) for item in value],
                supported=all(cls.from_value(item).supported for item in value),
                source_shape="list",
            )
        if not isinstance(value, Mapping):
            return cls("unsupported", raw=_copy(value), supported=False, source_shape="opaque")

        active_groups = [key for key in GROUP_TYPES if key in value]
        active_leaves = [key for key in LEAF_TYPES if key in value]
        if (active_groups and (len(active_groups) != 1 or active_leaves)) or (
            not active_groups and len(active_leaves) != 1
        ):
            return cls("unsupported", raw=_copy(dict(value)), supported=False)
        if active_groups:
            kind = active_groups[0]
            raw_children = value.get(kind)
            if kind in {"all", "any"} and isinstance(raw_children, (list, tuple)):
                children = [cls.from_value(item) for item in raw_children]
                extras = {key: _copy(item) for key, item in value.items() if key != kind}
                return cls(kind, children=children, extras=extras,
                           supported=not extras and all(child.supported for child in children))
            if kind == "not":
                child = cls.from_value(raw_children)
                extras = {key: _copy(item) for key, item in value.items() if key != kind}
                return cls(kind, children=[child], extras=extras,
                           supported=not extras and child.supported)
            return cls("unsupported", raw=_copy(dict(value)), supported=False)

        if len(active_leaves) == 1:
            subject_key = active_leaves[0]
            name = value.get(subject_key)
            if not isinstance(name, str) or not name:
                return cls("unsupported", raw=_copy(dict(value)), supported=False)
            parameters = {key: _copy(item) for key, item in value.items() if key != subject_key}
            return cls(subject_key, name=name, subject_key=subject_key,
                       parameters=parameters,
                       supported=not any(key not in PARAMETER_TYPES for key in parameters))
        if not value:
            return cls("empty", extras={})
        return cls("unsupported", raw=_copy(dict(value)), supported=False)

    from_mapping = from_value

    @classmethod
    def new(cls, kind: str, symbols: ConditionSymbolsLike | None = None) -> "ConditionNode":
        kind = str(kind)
        if kind in {"all", "any"}:
            return cls(kind)
        if kind == "not":
            return cls(kind, children=[cls.new("flag", symbols)])
        if kind in LEAF_TYPES:
            return cls(kind, name=_default_name(kind, symbols), subject_key=kind)
        raise ValueError(f"Unsupported condition node type {kind!r}")

    def clone(self) -> "ConditionNode":
        return deepcopy(self)

    @property
    def is_group(self) -> bool:
        return self.kind in GROUP_TYPES or self.kind == "list"

    @property
    def is_leaf(self) -> bool:
        return self.kind in LEAF_TYPES

    @property
    def label(self) -> str:
        return {
            "all": "ALL",
            "any": "ANY",
            "not": "NOT",
            "has_item": "Has Item",
            "flag": "Flag",
            "variable": "Variable",
            "var": "Variable (var alias)",
            "list": "ALL (list form)",
            "empty": "Explicit empty condition",
            "unsupported": "Unsupported condition",
        }.get(self.kind, self.kind.replace("_", " ").title())

    def to_value(self) -> Any:
        if self.kind == "unsupported":
            return _copy(self.raw)
        if self.kind == "empty":
            return {}
        if self.kind == "list":
            return [child.to_value() for child in self.children]
        if self.kind in {"all", "any"}:
            result = {key: _copy(item) for key, item in self.extras.items()}
            result[self.kind] = [child.to_value() for child in self.children]
            return result
        if self.kind == "not":
            result = {key: _copy(item) for key, item in self.extras.items()}
            result["not"] = self.children[0].to_value() if self.children else {}
            return result
        if self.is_leaf:
            result = {key: _copy(item) for key, item in self.parameters.items()}
            result[self.subject_key or self.kind] = self.name
            # Keep the subject first when a new node is authored.  Existing
            # mappings retain every semantic field regardless of order.
            ordered = {self.subject_key or self.kind: self.name}
            ordered.update(result)
            return ordered
        return _copy(self.raw)


@dataclass
class ConditionTreeModel:
    """Mutable tree adapter used by the Qt form."""

    root: ConditionNode
    symbols: ConditionSymbolsLike | None = None

    @classmethod
    def from_value(cls, value: Any, symbols: ConditionSymbolsLike | None = None) -> "ConditionTreeModel":
        return cls(ConditionNode.from_value(value), symbols)

    from_mapping = from_value

    @classmethod
    def new(cls, kind: str = "flag", symbols: ConditionSymbolsLike | None = None) -> "ConditionTreeModel":
        return cls(ConditionNode.new(kind, symbols), symbols)

    @property
    def supported(self) -> bool:
        return self.root.supported

    def value(self) -> Any:
        return self.root.to_value()

    semantic_value = value
    to_mapping = value

    def validate(self) -> None:
        validate_structured_condition(self.value())

    def node(self, path: Sequence[int] = ()) -> ConditionNode:
        current = self.root
        for index in path:
            current = current.children[int(index)]
        return current

    def _replace(self, path: Sequence[int], replacement: ConditionNode) -> None:
        path = tuple(path)
        if not path:
            self.root = replacement
            return
        parent = self.node(path[:-1])
        parent.children[path[-1]] = replacement

    def change_type(self, path: Sequence[int], kind: str) -> None:
        candidate = self.clone()
        candidate._replace(path, ConditionNode.new(kind, candidate.symbols))
        candidate.validate()
        self.root = candidate.root

    def set_leaf_name(self, path: Sequence[int], name: str) -> None:
        candidate = self.clone()
        target = candidate.node(path)
        if not target.is_leaf or not isinstance(name, str) or not name.strip():
            raise ConditionError("Condition names must be non-empty strings")
        target.name = name.strip()
        candidate.validate()
        self.root = candidate.root

    def set_parameter(self, path: Sequence[int], key: str, value: Any = MISSING) -> None:
        candidate = self.clone()
        target = candidate.node(path)
        if not target.is_leaf or key not in PARAMETER_TYPES:
            raise ConditionError(f"Unsupported condition parameter {key!r}")
        if value is MISSING:
            target.parameters.pop(key, None)
        else:
            target.parameters[key] = _copy(value)
        candidate.validate()
        self.root = candidate.root

    def set_comparison(self, path: Sequence[int], key: str, value: Any = True) -> None:
        """Replace the one visible leaf comparison in one semantic edit."""

        candidate = self.clone()
        target = candidate.node(path)
        if not target.is_leaf or key not in {"", "equals", "not_equals", "exists"}:
            raise ConditionError(f"Unsupported condition comparison {key!r}")
        for parameter in ("equals", "not_equals", "exists"):
            target.parameters.pop(parameter, None)
        if key:
            target.parameters[key] = _copy(value)
        candidate.validate()
        self.root = candidate.root

    def add_child(self, path: Sequence[int], kind: str = "flag", index: int | None = None) -> int:
        candidate = self.clone()
        parent = candidate.node(path)
        if parent.kind not in {"all", "any"}:
            raise ConditionError("Only ALL and ANY nodes can contain editable child lists")
        child = ConditionNode.new(kind, candidate.symbols)
        position = len(parent.children) if index is None else max(0, min(int(index), len(parent.children)))
        parent.children.insert(position, child)
        candidate.validate()
        self.root = candidate.root
        return position

    def remove_child(self, path: Sequence[int]) -> None:
        path = tuple(path)
        if not path:
            raise ConditionError("The root condition cannot be removed")
        candidate = self.clone()
        parent = candidate.node(path[:-1])
        if parent.kind not in {"all", "any"}:
            raise ConditionError("Only ALL and ANY nodes can remove children")
        parent.children.pop(path[-1])
        candidate.validate()
        self.root = candidate.root

    def move_child(self, parent_path: Sequence[int], index: int, delta: int) -> int:
        candidate = self.clone()
        parent = candidate.node(parent_path)
        if parent.kind not in {"all", "any"}:
            raise ConditionError("Only ALL and ANY nodes can reorder children")
        new_index = max(0, min(len(parent.children) - 1, int(index) + int(delta)))
        if new_index == index:
            return int(index)
        parent.children[index], parent.children[new_index] = parent.children[new_index], parent.children[index]
        candidate.validate()
        self.root = candidate.root
        return new_index

    def clone(self) -> "ConditionTreeModel":
        return ConditionTreeModel(self.root.clone(), self.symbols)


ConditionEditorModel = ConditionTreeModel


def condition_mode(value: Any) -> str:
    """Return the UI representation without treating explicit ``{}`` as absent."""

    if value is MISSING:
        return "absent"
    if isinstance(value, str):
        return "string"
    return "structured"


def validate_condition_value(value: Any) -> None:
    """Validate a complete authored condition through Story/Core."""

    parse_condition(value)


__all__ = [
    "ConditionEditorModel",
    "ConditionNode",
    "ConditionTreeModel",
    "GROUP_TYPES",
    "LEAF_TYPES",
    "NODE_TYPES",
    "PARAMETER_TYPES",
    "condition_mode",
    "validate_condition_value",
    "condition_symbol_candidates",
]
