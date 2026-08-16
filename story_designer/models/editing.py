"""Qt-independent editing primitives for the Story Designer.

The Core project is an immutable snapshot.  This module owns the mutable
semantic mapping used by the Designer until a later phase deliberately turns
it into serialized source documents.  It intentionally knows about Core's
schema metadata, but has no Qt dependency.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
import re
from typing import Any

from engine.story_core import (
    ActionError,
    ActionScope,
    Schema,
    StoryProject,
    TypeSpec,
    action_editor_spec,
    parse_action,
)
from engine.story_core.conditions import ConditionError, parse_condition
from engine.story_core.schema import FieldSpec, MISSING

from .project_session import DefinitionSelection


PropertyPath = tuple[str | int, ...]


def _copy(value: Any) -> Any:
    """Copy an authored/editor value without leaking mutable containers."""

    if value is MISSING:
        return MISSING
    return deepcopy(value)


def _title(value: str | int) -> str:
    return str(value).replace("_", " ").replace("-", " ").title()


@dataclass(frozen=True)
class ValidationResult:
    """The small validation result exposed to future input widgets."""

    valid: bool
    message: str = ""

    @classmethod
    def ok(cls) -> "ValidationResult":
        return cls(True, "")

    @classmethod
    def error(cls, message: str) -> "ValidationResult":
        return cls(False, message)


@dataclass(frozen=True)
class PropertyDescriptor:
    """Schema-derived metadata and values for one editable property path."""

    selection: DefinitionSelection
    path: PropertyPath
    key: str | int
    display_name: str
    description: str
    type_spec: TypeSpec | None
    required: bool
    default: Any = MISSING
    authored_value: Any = MISSING
    effective_value: Any = MISSING
    is_authored: bool = False
    is_editable: bool = True
    supported: bool = True
    unsupported_reason: str | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None
    allowed_values: tuple[Any, ...] = ()
    reference_target: str | None = None
    reference_candidates: tuple[str, ...] = ()
    asset_kind: str | None = None
    nullable: bool = False
    field_spec: FieldSpec | None = field(default=None, repr=False, compare=False)

    @property
    def type(self) -> TypeSpec | None:
        """Convenient alias for widget factories."""

        return self.type_spec

    @property
    def has_default(self) -> bool:
        return self.default is not MISSING

    @property
    def editable(self) -> bool:
        return self.is_editable

    @property
    def is_supported(self) -> bool:
        return self.supported


@dataclass
class DefinitionWorkingCopy:
    """One editor-owned semantic mapping and its loaded baseline."""

    selection: DefinitionSelection
    schema: Schema | None
    original_mapping: dict[str, Any]
    mapping: dict[str, Any]

    @classmethod
    def from_mapping(
        cls,
        selection: DefinitionSelection,
        mapping: Mapping[str, Any] | None,
        schema: Schema | None,
    ) -> "DefinitionWorkingCopy":
        original = _copy(dict(mapping or {}))
        return cls(selection, schema, original, _copy(original))

    @property
    def authored_mapping(self) -> dict[str, Any]:
        return self.mapping

    @property
    def is_dirty(self) -> bool:
        return self.mapping != self.original_mapping

    def to_mapping(self) -> dict[str, Any]:
        return _copy(self.mapping)

    def original(self) -> dict[str, Any]:
        return _copy(self.original_mapping)

    def value(self, path: Sequence[str | int], default: Any = MISSING) -> Any:
        return _get_path(self.mapping, tuple(path), default=default, schema=self.schema)

    def original_value(self, path: Sequence[str | int], default: Any = MISSING) -> Any:
        return _get_path(self.original_mapping, tuple(path), default=default, schema=self.schema)

    def set_value(self, path: Sequence[str | int], value: Any) -> None:
        _set_path(self.mapping, tuple(path), _copy(value), schema=self.schema)

    def remove_value(self, path: Sequence[str | int]) -> None:
        _remove_path(self.mapping, tuple(path), schema=self.schema)

    def revert(self) -> None:
        self.mapping = _copy(self.original_mapping)


@dataclass
class SourceDocumentWorkingCopy:
    """Mutable complete source document used for file-level settings."""

    relative_path: str
    original_mapping: Any
    mapping: Any
    patches: dict[PropertyPath, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_mapping(cls, relative_path: str, mapping: Any) -> "SourceDocumentWorkingCopy":
        return cls(str(relative_path), _copy(mapping), _copy(mapping))

    @property
    def is_dirty(self) -> bool:
        return self.mapping != self.original_mapping

    def to_mapping(self) -> Any:
        return _copy(self.mapping)

    def revert(self) -> None:
        self.mapping = _copy(self.original_mapping)
        self.patches.clear()

    def set_value(self, path: Sequence[str | int], value: Any) -> None:
        property_path = tuple(path)
        _set_path(self.mapping, property_path, _copy(value))
        self.patches[property_path] = _copy(value)

    def remove_value(self, path: Sequence[str | int]) -> None:
        property_path = tuple(path)
        _remove_path(self.mapping, property_path)
        self.patches[property_path] = MISSING

    def rebase(self, base_mapping: Any) -> None:
        self.mapping = _copy(base_mapping)
        for path, value in self.patches.items():
            if value is MISSING:
                _remove_path(self.mapping, path)
            else:
                _set_path(self.mapping, path, _copy(value))


class EditValidationError(ValueError):
    """Raised when an editing command fails schema-level validation."""

    def __init__(self, path: PropertyPath, message: str) -> None:
        self.path = tuple(path)
        self.message = str(message)
        super().__init__(f"{_path_text(self.path)}: {self.message}")


class PropertyModel:
    """Adapter from a working mapping and Core schema to property metadata."""

    _SUPPORTED_KINDS = {
        "string", "multiline_string", "integer", "float", "number", "boolean",
        "enum", "reference", "asset", "object", "list", "mapping", "condition",
        "union", "discriminated_union",
    }

    def __init__(
        self,
        project: StoryProject,
        selection: DefinitionSelection,
        working_copy: DefinitionWorkingCopy,
    ) -> None:
        self.project = project
        self.selection = selection
        self.working_copy = working_copy
        self.schema = working_copy.schema

    @property
    def mapping(self) -> dict[str, Any]:
        return self.working_copy.mapping

    def descriptor(self, path: Sequence[str | int]) -> PropertyDescriptor:
        property_path = tuple(path)
        if not property_path:
            raise KeyError("A property path cannot be empty")
        field_spec, type_spec = self._spec_for_path(property_path)
        authored = self.working_copy.value(property_path)
        effective = self._effective_value(property_path, field_spec)
        default = field_spec.default_value() if field_spec is not None and field_spec.has_default else MISSING
        if len(property_path) > 1 and field_spec is None:
            nested_field = self._nested_field_spec(property_path)
            if nested_field is not None:
                field_spec = nested_field
                default = nested_field.default_value() if nested_field.has_default else MISSING
        supported, reason = self._support(type_spec)
        candidates = self._reference_candidates(type_spec, field_spec)
        reference_target = self._reference_target(type_spec, field_spec)
        asset_kind = self._asset_kind(type_spec, field_spec)
        key = property_path[-1]
        description = field_spec.description if field_spec is not None else ""
        read_only = bool(field_spec.read_only) if field_spec is not None else False
        return PropertyDescriptor(
            selection=self.selection,
            path=property_path,
            key=key,
            display_name=field_spec.display_name if field_spec is not None else _title(key),
            description=description,
            type_spec=type_spec,
            required=bool(field_spec.required) if field_spec is not None else False,
            default=_copy(default),
            authored_value=_copy(authored),
            effective_value=_copy(effective),
            is_authored=authored is not MISSING,
            is_editable=not read_only and supported,
            supported=supported,
            unsupported_reason=reason,
            minimum=field_spec.minimum if field_spec is not None else None,
            maximum=field_spec.maximum if field_spec is not None else None,
            allowed_values=tuple(_copy(value) for value in (field_spec.allowed_values if field_spec is not None else ())),
            reference_target=reference_target,
            reference_candidates=candidates,
            asset_kind=asset_kind,
            nullable=bool(type_spec.nullable) if type_spec is not None else False,
            field_spec=field_spec,
        )

    property = descriptor

    def properties(self, *, include_nested: bool = True) -> tuple[PropertyDescriptor, ...]:
        """Return schema fields, optionally followed by existing nested paths."""

        if self.schema is None:
            return ()
        result: list[PropertyDescriptor] = []
        for field_spec in self.schema.fields:
            if field_spec.is_applicable(self._effective_mapping()):
                path = (field_spec.key,)
                result.append(self.descriptor(path))
                if include_nested:
                    self._append_nested(result, path, self._path_value(path), field_spec.type, depth=0)
        return tuple(result)

    fields = properties

    def validate(self, path: Sequence[str | int], value: Any = MISSING, *, removing: bool = False) -> ValidationResult:
        property_path = tuple(path)
        descriptor = self.descriptor(property_path)
        if not descriptor.is_editable:
            return ValidationResult.error("This property is read-only.")
        if not descriptor.supported:
            return ValidationResult.error(descriptor.unsupported_reason or "This property type is not editable.")
        field_spec, type_spec = self._spec_for_path(property_path)
        if removing or value is MISSING:
            if descriptor.required:
                return ValidationResult.error("A required authored value cannot be removed.")
            return ValidationResult.ok()
        if type_spec is None:
            return ValidationResult.ok()
        result = _validate_type(value, type_spec)
        if not result.valid:
            return result
        if field_spec is not None and isinstance(value, (int, float)) and not isinstance(value, bool):
            if field_spec.minimum is not None and value < field_spec.minimum:
                return ValidationResult.error(f"Value must be at least {field_spec.minimum}.")
            if field_spec.maximum is not None and value > field_spec.maximum:
                return ValidationResult.error(f"Value must be at most {field_spec.maximum}.")
        if field_spec is not None and field_spec.allowed_values and value not in field_spec.allowed_values:
            return ValidationResult.error("Value is not one of the allowed values.")
        return ValidationResult.ok()

    validate_value = validate

    def validate_command(self, command: "EditCommand") -> ValidationResult:
        if isinstance(command, RemovePropertyCommand):
            return self.validate(command.path, removing=True)
        return self.validate(command.path, command.value)

    def _effective_mapping(self) -> dict[str, Any]:
        result = _copy(self.mapping)
        if self.schema is not None:
            for field_spec in self.schema.fields:
                if field_spec.key not in result:
                    for alias in field_spec.aliases:
                        if alias in result:
                            result[field_spec.key] = result[alias]
                            break
        return result

    def _path_value(self, path: PropertyPath) -> Any:
        return self.working_copy.value(path)

    def _effective_value(self, path: PropertyPath, field_spec: FieldSpec | None) -> Any:
        value = self.working_copy.value(path)
        if value is not MISSING:
            return value
        if len(path) == 1 and field_spec is not None and field_spec.has_default:
            return field_spec.default_value()
        if len(path) > 1:
            root_spec = self.schema.field(str(path[0])) if self.schema is not None and isinstance(path[0], str) else None
            root_value = self.working_copy.value((path[0],))
            if root_value is MISSING and root_spec is not None and root_spec.has_default:
                root_value = root_spec.default_value()
            if root_value is not MISSING:
                return _get_path(root_value, path[1:], default=MISSING)
        return MISSING

    def _spec_for_path(self, path: PropertyPath) -> tuple[FieldSpec | None, TypeSpec | None]:
        if not path or self.schema is None or not isinstance(path[0], str):
            return None, None
        field_spec = self.schema.field(path[0])
        if field_spec is None:
            return None, None
        type_spec: TypeSpec | None = field_spec.type
        nested_field = field_spec
        for component in path[1:]:
            nested_field = self._field_for_nested(nested_field, type_spec, component)
            type_spec = self._child_type(type_spec, component)
        return (field_spec if len(path) == 1 else nested_field), type_spec

    def _nested_field_spec(self, path: PropertyPath) -> FieldSpec | None:
        field_spec, _ = self._spec_for_path(path)
        return field_spec

    def _field_for_nested(self, parent: FieldSpec | None, type_spec: TypeSpec | None, component: str | int) -> FieldSpec | None:
        if type_spec is None or not isinstance(component, str):
            return None
        if type_spec.object_schema:
            schema = self.project.schema_registry.get(type_spec.object_schema) if self.project.schema_registry else None
            return schema.field(component) if schema is not None else None
        return None

    def _child_type(self, type_spec: TypeSpec | None, component: str | int) -> TypeSpec | None:
        if type_spec is None:
            return None
        if type_spec.kind == "mapping":
            return type_spec.value_type
        if type_spec.kind == "list":
            return type_spec.element_type
        if type_spec.kind == "object" and isinstance(component, str) and type_spec.object_schema and self.project.schema_registry:
            nested_schema = self.project.schema_registry.get(type_spec.object_schema)
            nested_field = nested_schema.field(component) if nested_schema else None
            return nested_field.type if nested_field else None
        if type_spec.kind in {"union", "discriminated_union"}:
            for variant in type_spec.variants.values():
                child = self._child_type(variant, component)
                if child is not None:
                    return child
        return None

    def _append_nested(
        self,
        result: list[PropertyDescriptor],
        parent_path: PropertyPath,
        value: Any,
        type_spec: TypeSpec | None,
        *,
        depth: int,
    ) -> None:
        if depth >= 8 or type_spec is None:
            return
        if type_spec.kind == "object" and type_spec.object_schema and self.project.schema_registry:
            nested_schema = self.project.schema_registry.get(type_spec.object_schema)
            if nested_schema is None:
                return
            values = value if isinstance(value, Mapping) else {}
            for field_spec in nested_schema.fields:
                path = parent_path + (field_spec.key,)
                result.append(self.descriptor(path))
                if field_spec.type.kind in {"object", "mapping", "list", "union", "discriminated_union"}:
                    self._append_nested(result, path, values.get(field_spec.key, field_spec.default_value() if field_spec.has_default else MISSING), field_spec.type, depth=depth + 1)
        elif type_spec.kind == "mapping" and isinstance(value, Mapping) and type_spec.value_type is not None:
            for key, child_value in value.items():
                if isinstance(key, (str, int)):
                    path = parent_path + (key,)
                    result.append(self.descriptor(path))
                    self._append_nested(result, path, child_value, type_spec.value_type, depth=depth + 1)
        elif type_spec.kind == "list" and isinstance(value, (list, tuple)) and type_spec.element_type is not None:
            for index, child_value in enumerate(value):
                path = parent_path + (index,)
                result.append(self.descriptor(path))
                self._append_nested(result, path, child_value, type_spec.element_type, depth=depth + 1)
        elif type_spec.kind in {"union", "discriminated_union"}:
            for variant in type_spec.variants.values():
                self._append_nested(result, parent_path, value, variant, depth=depth + 1)

    def _support(self, type_spec: TypeSpec | None) -> tuple[bool, str | None]:
        if type_spec is None:
            return False, "The schema does not describe this nested property."
        if type_spec.kind not in self._SUPPORTED_KINDS:
            return False, f"No editor adapter exists for type {type_spec.kind!r}."
        return True, None

    def _reference_target(self, type_spec: TypeSpec | None, field_spec: FieldSpec | None) -> str | None:
        if field_spec is not None and field_spec.reference_target:
            return field_spec.reference_target
        if type_spec is None:
            return None
        if type_spec.kind == "reference":
            return type_spec.reference_target
        if type_spec.element_type is not None:
            return self._reference_target(type_spec.element_type, None)
        return None

    def _asset_kind(self, type_spec: TypeSpec | None, field_spec: FieldSpec | None) -> str | None:
        if field_spec is not None and field_spec.asset_kind:
            return field_spec.asset_kind
        return type_spec.asset_kind if type_spec is not None else None

    def _reference_candidates(self, type_spec: TypeSpec | None, field_spec: FieldSpec | None) -> tuple[str, ...]:
        target = self._reference_target(type_spec, field_spec)
        if not target or self.project.index is None:
            return ()
        return tuple(dict.fromkeys(reference.identifier for reference in self.project.index.references(target)))


def _validate_type(value: Any, type_spec: TypeSpec) -> ValidationResult:
    if value is None:
        return ValidationResult.ok() if type_spec.nullable else ValidationResult.error("Null is not allowed.")
    kind = type_spec.kind
    if kind in {"string", "multiline_string", "reference", "asset"} and not isinstance(value, str):
        return ValidationResult.error("Value must be a string.")
    if kind == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        return ValidationResult.error("Value must be an integer.")
    if kind == "float" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        return ValidationResult.error("Value must be a number.")
    if kind == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        return ValidationResult.error("Value must be a number.")
    if kind == "boolean" and not isinstance(value, bool):
        return ValidationResult.error("Value must be a boolean.")
    if kind == "enum" and value not in type_spec.enum_values:
        return ValidationResult.error("Value is not one of the allowed enum values.")
    if kind in {"object", "mapping", "discriminated_union"} and not isinstance(value, Mapping):
        return ValidationResult.error("Value must be a mapping.")
    if kind == "list":
        if not isinstance(value, (list, tuple)):
            return ValidationResult.error("Value must be a list.")
        if type_spec.element_type is not None:
            for index, item in enumerate(value):
                result = _validate_type(item, type_spec.element_type)
                if not result.valid:
                    return ValidationResult.error(f"List entry {index}: {result.message}")
    if kind == "mapping" and type_spec.value_type is not None:
        for key, item in value.items() if isinstance(value, Mapping) else ():
            result = _validate_type(item, type_spec.value_type)
            if not result.valid:
                return ValidationResult.error(f"Mapping entry {key!r}: {result.message}")
    if kind == "condition" and not isinstance(value, (str, Mapping, list, tuple)):
        return ValidationResult.error("Condition must be text, a mapping, or a list.")
    if kind == "discriminated_union" and type_spec.discriminator:
        discriminator_value = value.get(type_spec.discriminator) if isinstance(value, Mapping) else None
        if discriminator_value is not None and type_spec.variants and str(discriminator_value) not in type_spec.variants:
            return ValidationResult.error("Value selects an unknown union variant.")
    if kind == "union":
        if not any(_validate_type(value, variant).valid for variant in type_spec.variants.values()):
            return ValidationResult.error("Value does not match any allowed type.")
    return ValidationResult.ok()


def _path_text(path: PropertyPath) -> str:
    result = ""
    for component in path:
        if isinstance(component, int):
            result += f"[{component}]"
        elif result:
            result += f".{component}"
        else:
            result = str(component)
    return result


def _actual_root_key(mapping: Mapping[str, Any], key: str, schema: Schema | None) -> str:
    if key in mapping or schema is None:
        return key
    field_spec = schema.field(key)
    if field_spec is not None:
        for alias in field_spec.aliases:
            if alias in mapping:
                return alias
        return field_spec.key
    return key


def _get_path(value: Any, path: PropertyPath, *, default: Any = MISSING, schema: Schema | None = None) -> Any:
    current = value
    for index, component in enumerate(path):
        if index == 0 and isinstance(current, Mapping) and isinstance(component, str):
            component = _actual_root_key(current, component, schema)
        if isinstance(current, Mapping):
            if component not in current:
                return default
            current = current[component]
        elif isinstance(current, (list, tuple)) and isinstance(component, int) and 0 <= component < len(current):
            current = current[component]
        else:
            return default
    return current


def _set_path(mapping: dict[str, Any], path: PropertyPath, value: Any, *, schema: Schema | None = None) -> None:
    if not path:
        raise KeyError("A property path cannot be empty")
    root = path[0]
    if not isinstance(root, str):
        raise KeyError("A definition root property must be named by a string")
    actual_root = _actual_root_key(mapping, root, schema)
    if len(path) == 1:
        mapping[actual_root] = value
        return
    current: Any = mapping
    for index, component in enumerate(path[:-1]):
        if index == 0 and isinstance(current, Mapping) and isinstance(component, str):
            component = _actual_root_key(current, component, schema)
        next_component = path[index + 1]
        if isinstance(current, dict):
            if component not in current:
                current[component] = [] if isinstance(next_component, int) else {}
            current = current[component]
        elif isinstance(current, list) and isinstance(component, int) and 0 <= component < len(current):
            current = current[component]
        else:
            raise KeyError(f"Cannot descend through {_path_text(path[:index + 1])}")
    final = path[-1]
    if isinstance(current, dict):
        current[final] = value
    elif isinstance(current, list) and isinstance(final, int) and 0 <= final < len(current):
        current[final] = value
    else:
        raise KeyError(f"Cannot assign {_path_text(path)}")


def _remove_path(mapping: dict[str, Any], path: PropertyPath, *, schema: Schema | None = None) -> None:
    if not path:
        raise KeyError("A property path cannot be empty")
    root = path[0]
    if not isinstance(root, str):
        return
    actual_root = _actual_root_key(mapping, root, schema)
    if len(path) == 1:
        mapping.pop(actual_root, None)
        if schema is not None:
            field_spec = schema.field(root)
            if field_spec is not None:
                for alias in field_spec.aliases:
                    mapping.pop(alias, None)
        return
    parent = _get_path(mapping, path[:-1], schema=schema)
    final = path[-1]
    if isinstance(parent, dict):
        parent.pop(final, None)


class EditCommand:
    """Base class for explicit application-layer property mutations."""

    operation = "edit"

    def __init__(self, selection: DefinitionSelection, path: Sequence[str | int]) -> None:
        self.selection = selection
        self.path = tuple(path)
        self.old_authored_value: Any = MISSING
        self._old_mapping: dict[str, Any] | None = None

    @property
    def old_value(self) -> Any:
        return self.old_authored_value

    def apply(self, working_copy: DefinitionWorkingCopy) -> None:
        raise NotImplementedError

    def validate(self, model: PropertyModel) -> ValidationResult:
        """Return validation for this command before it touches working state."""

        return model.validate_command(self)

    def undo(self, working_copy: DefinitionWorkingCopy) -> None:
        """Restore the authored value that existed before this command."""

        if self._old_mapping is not None:
            working_copy.mapping = _copy(self._old_mapping)
            return
        if self.old_authored_value is MISSING:
            working_copy.remove_value(self.path)
        else:
            working_copy.set_value(self.path, self.old_authored_value)


class SetPropertyCommand(EditCommand):
    operation = "set"

    def __init__(self, selection: DefinitionSelection, path: Sequence[str | int], value: Any) -> None:
        super().__init__(selection, path)
        self.value = _copy(value)
        self.new_authored_value = _copy(value)

    def apply(self, working_copy: DefinitionWorkingCopy) -> None:
        self._old_mapping = working_copy.to_mapping()
        self.old_authored_value = _copy(working_copy.value(self.path))
        working_copy.set_value(self.path, self.value)


class SetSourcePropertyCommand(EditCommand):
    """Edit a file-level property while retaining its authored absence."""

    operation = "set_source_property"

    def __init__(self, selection: DefinitionSelection, source_path: str, path: Sequence[str | int], value: Any) -> None:
        self.source_path = str(source_path).replace("\\", "/")
        self.value = _copy(value)
        super().__init__(selection, path)

    def validate(self, model: PropertyModel | None = None) -> ValidationResult:
        if not self.path or any(not isinstance(component, (str, int)) for component in self.path):
            return ValidationResult.error("A source property path is required.")
        if self.path[:1] == ("skill_progression",) and len(self.path) == 2:
            key = self.path[1]
            if key in {"evaluation_attempts", "minimum_level"} and (not isinstance(self.value, int) or isinstance(self.value, bool) or self.value < 1):
                return ValidationResult.error(f"skill_progression.{key} must be a positive integer.")
            if key in {"promotion_average", "demotion_average"} and (not isinstance(self.value, (int, float)) or isinstance(self.value, bool) or not 0 <= float(self.value) <= 3):
                return ValidationResult.error(f"skill_progression.{key} must be a number between 0 and 3.")
        return ValidationResult.ok()

    def validate_source(self, document: Any) -> ValidationResult:
        result = self.validate(None)
        if not result.valid or self.path[:1] != ("skill_progression",):
            return result
        candidate = _copy(document)
        _set_path(candidate, self.path, _copy(self.value))
        try:
            from engine.battle.move_progression import SkillProgressionConfig
            from engine.errors import BattleConfigError
            progression = candidate.get("skill_progression") if isinstance(candidate, Mapping) else None
            SkillProgressionConfig.from_data(progression)
        except (BattleConfigError, ValueError, TypeError) as exc:
            return ValidationResult.error(str(exc))
        return ValidationResult.ok()

    def apply(self, working_copy: SourceDocumentWorkingCopy) -> None:  # type: ignore[override]
        self._old_mapping = working_copy.to_mapping()
        self.old_authored_value = _get_path(working_copy.mapping, self.path, default=MISSING)
        working_copy.set_value(self.path, self.value)

    def undo(self, working_copy: SourceDocumentWorkingCopy) -> None:  # type: ignore[override]
        if self._old_mapping is None:
            raise RuntimeError("Source command has not been applied")
        if self.old_authored_value is MISSING:
            working_copy.remove_value(self.path)
            working_copy.patches.pop(tuple(self.path), None)
        else:
            working_copy.set_value(self.path, self.old_authored_value)


class CombatMoveCommand(EditCommand):
    """Atomic command base for combat-move structure/QTE operations."""

    operation = "combat_move_edit"

    def __init__(self, selection: DefinitionSelection, path: Sequence[str | int] = ()) -> None:
        super().__init__(selection, path)
        self._before_mapping: dict[str, Any] | None = None
        self._after_mapping: dict[str, Any] | None = None

    def validate(self, model: PropertyModel) -> ValidationResult:
        return ValidationResult.ok()

    def apply(self, working_copy: DefinitionWorkingCopy) -> None:
        if self._after_mapping is not None:
            working_copy.mapping = _copy(self._after_mapping)
            return
        self._before_mapping = working_copy.to_mapping()
        self._apply_once(working_copy)
        self._after_mapping = working_copy.to_mapping()

    def undo(self, working_copy: DefinitionWorkingCopy) -> None:
        if self._before_mapping is None:
            raise RuntimeError("Combat move command has not been applied")
        working_copy.mapping = _copy(self._before_mapping)

    def _apply_once(self, working_copy: DefinitionWorkingCopy) -> None:
        raise NotImplementedError


class AddDifficultyLevelCommand(CombatMoveCommand):
    operation = "add_difficulty_level"

    def __init__(self, selection: DefinitionSelection, level: int | None = None) -> None:
        self.level = level
        super().__init__(selection, ("difficulty_levels", level if level is not None else "next"))

    def validate(self, model: PropertyModel) -> ValidationResult:
        raw = model.mapping
        if "difficulty_levels" not in raw:
            return ValidationResult.error("Legacy moves do not have adaptive difficulty levels; add the modern schema explicitly first.")
        levels = raw.get("difficulty_levels")
        if not isinstance(levels, Mapping):
            return ValidationResult.error("difficulty_levels must be a mapping.")
        numeric = sorted(key for key in levels if isinstance(key, int) and not isinstance(key, bool) and key >= 0)
        self.level = (max(numeric) + 1) if numeric else 1 if self.level is None else self.level
        if self.level < 0 or self.level in levels:
            return ValidationResult.error(f"Difficulty level {self.level} already exists or is invalid.")
        return ValidationResult.ok()

    def _apply_once(self, working_copy: DefinitionWorkingCopy) -> None:
        levels = working_copy.mapping.setdefault("difficulty_levels", {})
        if not isinstance(levels, dict):
            raise TypeError("difficulty_levels must be a mapping")
        assert self.level is not None
        levels[self.level] = {}


class DeleteDifficultyLevelCommand(CombatMoveCommand):
    operation = "delete_difficulty_level"

    def __init__(self, selection: DefinitionSelection, level: int) -> None:
        self.level = int(level)
        super().__init__(selection, ("difficulty_levels", self.level))

    def validate(self, model: PropertyModel) -> ValidationResult:
        raw = model.mapping
        levels = raw.get("difficulty_levels")
        if not isinstance(levels, Mapping) or self.level not in levels:
            return ValidationResult.error(f"Difficulty level {self.level} is not authored.")
        if self.level >= 1:
            remaining = sorted(key for key in levels if isinstance(key, int) and key >= 1 and key != self.level)
            if not remaining or remaining[0] != 1 or remaining != list(range(1, remaining[-1] + 1)):
                return ValidationResult.error("Cannot delete this normal level; level 1 and contiguous adaptive levels are required.")
        return ValidationResult.ok()

    def _apply_once(self, working_copy: DefinitionWorkingCopy) -> None:
        levels = working_copy.mapping.get("difficulty_levels")
        if not isinstance(levels, dict):
            raise TypeError("difficulty_levels must be a mapping")
        del levels[self.level]


class DuplicateDifficultyLevelCommand(CombatMoveCommand):
    operation = "duplicate_difficulty_level"

    def __init__(self, selection: DefinitionSelection, source_level: int, target_level: int | None = None) -> None:
        self.source_level = int(source_level)
        self.target_level = target_level
        super().__init__(selection, ("difficulty_levels", target_level if target_level is not None else "next"))

    def validate(self, model: PropertyModel) -> ValidationResult:
        levels = model.mapping.get("difficulty_levels")
        if not isinstance(levels, Mapping) or self.source_level not in levels:
            return ValidationResult.error(f"Difficulty level {self.source_level} is not authored.")
        numeric = sorted(key for key in levels if isinstance(key, int) and not isinstance(key, bool) and key >= 0)
        self.target_level = max(numeric) + 1 if numeric and self.target_level is None else (1 if self.target_level is None else self.target_level)
        if self.target_level < 0 or self.target_level in levels:
            return ValidationResult.error(f"Difficulty level {self.target_level} already exists or is invalid.")
        return ValidationResult.ok()

    def _apply_once(self, working_copy: DefinitionWorkingCopy) -> None:
        levels = working_copy.mapping.get("difficulty_levels")
        if not isinstance(levels, dict) or self.source_level not in levels or self.target_level is None:
            raise KeyError("Difficulty level duplication source is no longer available")
        levels[self.target_level] = _copy(levels[self.source_level])


class ReplaceQTETypeCommand(CombatMoveCommand):
    operation = "replace_qte_type"

    def __init__(self, selection: DefinitionSelection, type_name: str) -> None:
        self.type_name = str(type_name)
        super().__init__(selection, ("qte", "type"))

    def validate(self, model: PropertyModel) -> ValidationResult:
        from engine.battle.qte import qte_editor_spec
        if qte_editor_spec(self.type_name) is None:
            return ValidationResult.error(f"Unknown or unsupported QTE type {self.type_name!r}.")
        return ValidationResult.ok()

    def _apply_once(self, working_copy: DefinitionWorkingCopy) -> None:
        from engine.battle.qte import minimal_qte_payload
        payload = minimal_qte_payload(self.type_name)
        if payload is None:
            raise ValueError(f"Unknown QTE type {self.type_name!r}")
        mapping = working_copy.mapping
        if isinstance(mapping.get("common"), dict) and isinstance(mapping["common"].get("qte"), dict):
            mapping["common"]["qte"] = payload
        elif isinstance(mapping.get("qte"), dict):
            mapping["qte"] = payload
        elif "pattern" in mapping:
            mapping["pattern"] = self.type_name
            mapping.pop("pattern_config", None)
        else:
            mapping.setdefault("common", {})["qte"] = payload


class SetCombatMoveFieldCommand(CombatMoveCommand):
    operation = "set_combat_move_field"

    def __init__(self, selection: DefinitionSelection, path: Sequence[str | int], value: Any) -> None:
        self.value = _copy(value)
        super().__init__(selection, path)

    def validate(self, model: PropertyModel) -> ValidationResult:
        if not self.path:
            return ValidationResult.error("A combat-move field path is required.")
        # Metadata validates scalar/list shape before mutation.  Unknown
        # fields remain editable only through an explicitly visible Advanced
        # payload path, where arbitrary authored data is intentionally kept.
        from .combat_move_presentation import CombatMoveDocumentModel
        presentation = CombatMoveDocumentModel(model.selection.id, model.mapping, model.project)
        field = next((item for item in presentation.qte_fields() if item.path == self.path), None)
        if field is not None and field.spec is not None:
            kind = field.spec.value_type
            valid = (
                (kind == "integer" and isinstance(self.value, int) and not isinstance(self.value, bool))
                or (kind in {"float", "number"} and isinstance(self.value, (int, float)) and not isinstance(self.value, bool))
                or (kind == "boolean" and isinstance(self.value, bool))
                or (kind in {"string", "enum", "asset"} and isinstance(self.value, str))
                or (kind == "list" and isinstance(self.value, (list, tuple)))
                or (kind == "mapping" and isinstance(self.value, Mapping))
            )
            if not valid:
                return ValidationResult.error(f"{field.label} has an invalid value type.")
            if field.spec.minimum is not None and isinstance(self.value, (int, float)) and self.value < field.spec.minimum:
                return ValidationResult.error(f"{field.label} must be at least {field.spec.minimum}.")
            if field.spec.maximum is not None and isinstance(self.value, (int, float)) and self.value > field.spec.maximum:
                return ValidationResult.error(f"{field.label} must be at most {field.spec.maximum}.")
        return ValidationResult.ok()

    def _apply_once(self, working_copy: DefinitionWorkingCopy) -> None:
        working_copy.set_value(self.path, self.value)


class RemovePropertyCommand(EditCommand):
    operation = "remove"

    def __init__(self, selection: DefinitionSelection, path: Sequence[str | int]) -> None:
        super().__init__(selection, path)
        self.new_authored_value = MISSING

    def apply(self, working_copy: DefinitionWorkingCopy) -> None:
        self._old_mapping = working_copy.to_mapping()
        self.old_authored_value = _copy(working_copy.value(self.path))
        working_copy.remove_value(self.path)


@dataclass(frozen=True)
class GeometryChange:
    """One authored geometry path changed by a completed visual gesture."""

    path: PropertyPath
    shape: str
    old_value: tuple[int, ...]
    new_value: tuple[int, ...]


class SetGeometryCommand(EditCommand):
    """Atomically apply all fields changed by one scene geometry gesture."""

    operation = "set_geometry"

    def __init__(self, selection: DefinitionSelection, element: Any, changes: Sequence[GeometryChange]) -> None:
        changes = tuple(changes)
        if not changes:
            raise ValueError("A geometry command requires at least one change")
        super().__init__(selection, changes[0].path)
        self.element = element
        self.changes = changes

    def validate(self, model: PropertyModel) -> ValidationResult:
        for change in self.changes:
            if change.shape == "point":
                if not _valid_geometry_point(change.new_value):
                    return ValidationResult.error("Position must contain two integer coordinates.")
            elif change.shape == "rect":
                if not _valid_geometry_rect(change.new_value):
                    return ValidationResult.error("Rectangle must contain integer x, y, width, height with positive size.")
            else:
                return ValidationResult.error(f"Unsupported geometry shape: {change.shape!r}.")
        return ValidationResult.ok()

    def apply(self, working_copy: DefinitionWorkingCopy) -> None:
        self._old_mapping = working_copy.to_mapping()
        self.old_authored_value = _copy(working_copy.value(self.path))
        for change in self.changes:
            # YAML geometry is authored as a sequence, even though the editor
            # uses immutable tuples while calculating a gesture preview.
            working_copy.set_value(change.path, list(change.new_value))


def _collection_at(mapping: Mapping[str, Any], path: PropertyPath) -> Any:
    """Resolve a structural collection path without applying schema aliases."""

    return _get_path(mapping, path, default=MISSING)


def _collection_entries(collection: Any) -> tuple[tuple[Any, Any], ...]:
    """Return collection entries while retaining list indexes and mapping keys."""

    if isinstance(collection, list):
        return tuple((index, value) for index, value in enumerate(collection))
    if isinstance(collection, Mapping):
        return tuple(collection.items())
    return ()


def _element_id(value: Any, key: Any = None) -> str | None:
    if isinstance(value, Mapping) and isinstance(value.get("id"), str) and value["id"]:
        return value["id"]
    if isinstance(key, str) and key:
        return key
    return None


def _scene_element_ids(mapping: Mapping[str, Any]) -> set[str]:
    """Collect IDs from both scene-local element namespaces."""

    result: set[str] = set()
    exploration = mapping.get("exploration")
    sources: list[Mapping[str, Any]] = []
    if isinstance(exploration, Mapping):
        sources.append(exploration)
    sources.append(mapping)
    for source in sources:
        for key in ("objects", "look_regions"):
            for entry_key, value in _collection_entries(source.get(key)):
                identifier = _element_id(value, entry_key)
                if identifier is not None:
                    result.add(identifier)
    return result


def _ensure_collection_parent(mapping: dict[str, Any], path: PropertyPath) -> tuple[dict[str, Any], str | int]:
    if not path:
        raise KeyError("A collection path cannot be empty")
    current: Any = mapping
    for component in path[:-1]:
        if not isinstance(component, (str, int)):
            raise KeyError(f"Invalid collection path component: {component!r}")
        if isinstance(current, dict):
            if component not in current:
                current[component] = {}
            if component == "exploration" and not isinstance(current[component], Mapping):
                current[component] = {}
            current = current[component]
        else:
            raise KeyError(f"Cannot descend through collection path {path!r}")
    if not isinstance(current, dict):
        raise KeyError(f"Collection parent is not a mapping: {path!r}")
    return current, path[-1]


def _insert_collection_value(mapping: dict[str, Any], path: PropertyPath, value: Any, index: int | None) -> int | None:
    parent, key = _ensure_collection_parent(mapping, path)
    existing = parent.get(key, MISSING)
    if existing is MISSING or existing is None:
        existing = []
        parent[key] = existing
    if isinstance(existing, list):
        position = len(existing) if index is None else max(0, min(index, len(existing)))
        existing.insert(position, _copy(value))
        return position
    if isinstance(existing, dict):
        identifier = _element_id(value)
        if identifier is None:
            raise ValueError("Mapping-backed scene elements require an id")
        existing[identifier] = _copy(value)
        return None
    raise TypeError(f"Scene collection {path!r} must be a list or mapping")


def _find_collection_value(mapping: Mapping[str, Any], path: PropertyPath, identifier: str) -> tuple[Any, Any, Any] | None:
    collection = _collection_at(mapping, path)
    for key, value in _collection_entries(collection):
        if _element_id(value, key) == identifier:
            return collection, key, value
    return None


def _remove_collection_value(mapping: dict[str, Any], path: PropertyPath, identifier: str) -> tuple[Any, Any, Any]:
    found = _find_collection_value(mapping, path, identifier)
    if found is None:
        raise KeyError(f"Scene element {identifier!r} was not found at {path!r}")
    collection, key, value = found
    if isinstance(collection, list):
        collection.pop(key)
    else:
        del collection[key]
    return collection, key, value


def _offset_element_geometry(value: Any, offset: tuple[int, int]) -> Any:
    """Offset known geometry fields while leaving all other authored data intact."""

    if not isinstance(value, Mapping):
        return value
    result = _copy(value)
    dx, dy = offset
    position = result.get("position")
    if isinstance(position, list) and len(position) == 2:
        result["position"] = [position[0] + dx, position[1] + dy]
    elif isinstance(position, tuple) and len(position) == 2:
        result["position"] = [position[0] + dx, position[1] + dy]
    for geometry_key in ("rect", "hitbox"):
        geometry = result.get(geometry_key)
        if isinstance(geometry, (list, tuple)) and len(geometry) == 4:
            result[geometry_key] = [geometry[0] + dx, geometry[1] + dy, geometry[2], geometry[3]]
    look = result.get("look")
    if isinstance(look, Mapping):
        result["look"] = _offset_element_geometry(look, offset)
    return result


class StructuralEditCommand(EditCommand):
    """Base for one atomic scene collection mutation.

    The first application records both complete semantic snapshots.  Redo
    reuses the accepted after snapshot, so it never regenerates IDs or loses
    unknown sibling data.
    """

    operation = "structural_edit"

    def __init__(self, selection: DefinitionSelection, collection_path: Sequence[str | int]) -> None:
        super().__init__(selection, collection_path)
        self.collection_path = tuple(collection_path)
        self._before_mapping: dict[str, Any] | None = None
        self._after_mapping: dict[str, Any] | None = None

    def validate(self, model: PropertyModel) -> ValidationResult:
        return ValidationResult.ok()

    def _apply_once(self, working_copy: DefinitionWorkingCopy) -> None:
        raise NotImplementedError

    def apply(self, working_copy: DefinitionWorkingCopy) -> None:
        if self._after_mapping is not None:
            working_copy.mapping = _copy(self._after_mapping)
            return
        self._before_mapping = working_copy.to_mapping()
        self._apply_once(working_copy)
        self._after_mapping = working_copy.to_mapping()

    def undo(self, working_copy: DefinitionWorkingCopy) -> None:
        if self._before_mapping is None:
            raise RuntimeError("Structural command has not been applied")
        working_copy.mapping = _copy(self._before_mapping)


class InsertSceneElementCommand(StructuralEditCommand):
    """Insert one complete authored object/region into its existing collection."""

    operation = "insert_scene_element"

    def __init__(
        self,
        selection: DefinitionSelection,
        collection_path: Sequence[str | int],
        element: Mapping[str, Any],
        *,
        index: int | None = None,
    ) -> None:
        super().__init__(selection, collection_path)
        self.element = _copy(dict(element))
        self.index = index

    def _apply_once(self, working_copy: DefinitionWorkingCopy) -> None:
        identifier = _element_id(self.element)
        if identifier is None:
            raise ValueError("A scene element requires a non-empty id")
        if identifier in _scene_element_ids(working_copy.mapping):
            raise ValueError(f"Scene element id {identifier!r} is already in use")
        self.index = _insert_collection_value(working_copy.mapping, self.collection_path, self.element, self.index)


class RemoveSceneElementCommand(StructuralEditCommand):
    """Remove one element while retaining its exact prior collection position."""

    operation = "remove_scene_element"

    def __init__(self, selection: DefinitionSelection, collection_path: Sequence[str | int], element_id: str) -> None:
        super().__init__(selection, collection_path)
        self.element_id = str(element_id)
        self.removed_element: Any = MISSING
        self.index: int | None = None

    def _apply_once(self, working_copy: DefinitionWorkingCopy) -> None:
        collection, key, value = _remove_collection_value(working_copy.mapping, self.collection_path, self.element_id)
        self.removed_element = _copy(value)
        self.index = key if isinstance(collection, list) and isinstance(key, int) else None


class DuplicateSceneElementCommand(StructuralEditCommand):
    """Deep-copy an element, changing only its own ID and known geometry."""

    operation = "duplicate_scene_element"

    def __init__(
        self,
        selection: DefinitionSelection,
        collection_path: Sequence[str | int],
        source_id: str,
        duplicate_id: str,
        *,
        offset: tuple[int, int] = (8, 8),
    ) -> None:
        super().__init__(selection, collection_path)
        self.source_id = str(source_id)
        self.duplicate_id = str(duplicate_id)
        self.offset = (int(offset[0]), int(offset[1]))
        self.duplicate_element: Any = MISSING
        self.index: int | None = None

    def _apply_once(self, working_copy: DefinitionWorkingCopy) -> None:
        if self.duplicate_id in _scene_element_ids(working_copy.mapping):
            raise ValueError(f"Scene element id {self.duplicate_id!r} is already in use")
        found = _find_collection_value(working_copy.mapping, self.collection_path, self.source_id)
        if found is None:
            raise KeyError(f"Scene element {self.source_id!r} was not found at {self.collection_path!r}")
        _collection, key, source = found
        if not isinstance(source, Mapping):
            raise TypeError("Scene elements must be mappings")
        duplicate = _offset_element_geometry(source, self.offset)
        duplicate["id"] = self.duplicate_id
        self.duplicate_element = _copy(duplicate)
        self.index = key + 1 if isinstance(key, int) else None
        _insert_collection_value(working_copy.mapping, self.collection_path, duplicate, self.index)


def _navigation_entry(mapping: Mapping[str, Any], collection_path: PropertyPath, index: int) -> Mapping[str, Any] | None:
    collection = _collection_at(mapping, collection_path)
    if not isinstance(collection, list) or not 0 <= index < len(collection):
        return None
    value = collection[index]
    return value if isinstance(value, Mapping) else None


class InsertNavigationEntryCommand(StructuralEditCommand):
    """Insert one exploration scene link while retaining its authored shape."""

    operation = "insert_navigation_entry"

    def __init__(
        self,
        selection: DefinitionSelection,
        collection_path: Sequence[str | int],
        entry: Mapping[str, Any],
        *,
        index: int | None = None,
    ) -> None:
        super().__init__(selection, collection_path)
        self.entry = _copy(dict(entry))
        self.index = index

    def _apply_once(self, working_copy: DefinitionWorkingCopy) -> None:
        destination = self.entry.get("scene")
        if not isinstance(destination, str) or not destination:
            raise ValueError("A navigation entry requires a non-empty scene destination")
        position = _insert_collection_value(working_copy.mapping, self.collection_path, self.entry, self.index)
        self.index = position


class RemoveNavigationEntryCommand(StructuralEditCommand):
    """Remove one navigation entry and restore its exact list position on undo."""

    operation = "remove_navigation_entry"

    def __init__(self, selection: DefinitionSelection, collection_path: Sequence[str | int], index: int) -> None:
        super().__init__(selection, collection_path)
        self.index = int(index)
        self.removed_entry: Any = MISSING

    def _apply_once(self, working_copy: DefinitionWorkingCopy) -> None:
        collection = _collection_at(working_copy.mapping, self.collection_path)
        if not isinstance(collection, list) or not 0 <= self.index < len(collection):
            raise KeyError(f"Navigation entry {self.index} was not found at {self.collection_path!r}")
        self.removed_entry = _copy(collection.pop(self.index))


class SetNavigationDestinationCommand(EditCommand):
    """Change only ``scene`` on one link, preserving all sibling fields."""

    operation = "set_navigation_destination"

    def __init__(self, selection: DefinitionSelection, collection_path: Sequence[str | int], index: int, destination: str) -> None:
        super().__init__(selection, tuple(collection_path) + (int(index), "scene"))
        self.collection_path = tuple(collection_path)
        self.index = int(index)
        self.destination = str(destination)
        self.value = self.destination

    def validate(self, model: PropertyModel) -> ValidationResult:
        if not self.destination.strip():
            return ValidationResult.error("Destination scene ID cannot be empty.")
        if _navigation_entry(model.mapping, self.collection_path, self.index) is None:
            return ValidationResult.error("The selected navigation entry no longer exists.")
        return ValidationResult.ok()

    def apply(self, working_copy: DefinitionWorkingCopy) -> None:
        self._old_mapping = working_copy.to_mapping()
        entry = _navigation_entry(working_copy.mapping, self.collection_path, self.index)
        if entry is None:
            raise KeyError(f"Navigation entry {self.index} was not found")
        target = _get_path(working_copy.mapping, self.collection_path + (self.index,))
        if not isinstance(target, dict):
            raise TypeError("Navigation entries must be mappings")
        target["scene"] = self.destination


class SetNavigationConditionCommand(EditCommand):
    """Author, replace, or remove a condition without rewriting its dialect."""

    operation = "set_navigation_condition"

    def __init__(
        self,
        selection: DefinitionSelection,
        collection_path: Sequence[str | int],
        index: int,
        condition: Any = MISSING,
    ) -> None:
        super().__init__(selection, tuple(collection_path) + (int(index),))
        self.collection_path = tuple(collection_path)
        self.index = int(index)
        self.condition = _copy(condition)
        self.value = self.condition

    def validate(self, model: PropertyModel) -> ValidationResult:
        if _navigation_entry(model.mapping, self.collection_path, self.index) is None:
            return ValidationResult.error("The selected navigation entry no longer exists.")
        if self.condition is MISSING:
            return ValidationResult.ok()
        try:
            parse_condition(self.condition)
        except (ConditionError, TypeError, ValueError) as exc:
            return ValidationResult.error(str(exc))
        return ValidationResult.ok()

    def apply(self, working_copy: DefinitionWorkingCopy) -> None:
        self._old_mapping = working_copy.to_mapping()
        target = _get_path(working_copy.mapping, self.collection_path + (self.index,))
        if not isinstance(target, dict):
            raise TypeError("Navigation entries must be mappings")
        if self.condition is MISSING:
            target.pop("conditions", None)
            target.pop("condition", None)
            return
        key = "conditions" if "conditions" in target else ("condition" if "condition" in target else "conditions")
        target[key] = _copy(self.condition)


def _scene_element_condition_location(
    mapping: Mapping[str, Any],
    element: Any,
) -> tuple[PropertyPath, Mapping[str, Any]] | None:
    """Locate an exploration object/look-region without normalizing aliases."""

    kind = getattr(element, "kind", None)
    identifier = getattr(element, "id", None)
    if kind not in {"object", "look_region"}:
        return None
    key = "objects" if kind == "object" else "look_regions"
    # Match the same root-vs-exploration precedence used by presentation and
    # geometry editing.
    exploration = mapping.get("exploration")
    if isinstance(exploration, Mapping) and key in exploration:
        collection_path: PropertyPath = ("exploration", key)
    elif key in mapping:
        collection_path = (key,)
    else:
        return None
    collection = _collection_at(mapping, collection_path)
    if not isinstance(collection, list):
        return None
    for index, value in enumerate(collection):
        if isinstance(value, Mapping) and value.get("id") == identifier:
            return collection_path + (index,), value
    return None


class SetSceneElementConditionCommand(EditCommand):
    """Edit an object/look-region condition while retaining its field alias."""

    operation = "set_scene_element_condition"

    def __init__(self, selection: DefinitionSelection, element: Any, condition: Any = MISSING) -> None:
        super().__init__(selection, ())
        self.element = element
        self.condition = _copy(condition)
        self.value = self.condition

    def validate(self, model: PropertyModel) -> ValidationResult:
        found = _scene_element_condition_location(model.mapping, self.element)
        if found is None:
            return ValidationResult.error("The selected scene element no longer exists.")
        if self.condition is MISSING:
            return ValidationResult.ok()
        try:
            parse_condition(self.condition)
        except (ConditionError, TypeError, ValueError) as exc:
            return ValidationResult.error(str(exc))
        return ValidationResult.ok()

    def apply(self, working_copy: DefinitionWorkingCopy) -> None:
        self._old_mapping = working_copy.to_mapping()
        found = _scene_element_condition_location(working_copy.mapping, self.element)
        if found is None:
            raise KeyError("The selected scene element no longer exists")
        path, _entry = found
        target = _get_path(working_copy.mapping, path)
        if not isinstance(target, dict):
            raise TypeError("Scene elements must be mappings")
        if self.condition is MISSING:
            target.pop("visible_when", None)
            target.pop("conditions", None)
            return
        key = "visible_when" if "visible_when" in target else ("conditions" if "conditions" in target else "visible_when")
        target[key] = _copy(self.condition)


class SetSceneElementPropertyCommand(EditCommand):
    """Edit a portable field on a nested scene object/look-region."""

    operation = "set_scene_element_property"

    def __init__(self, selection: DefinitionSelection, element: Any, key: str, value: Any) -> None:
        super().__init__(selection, ())
        self.element = element
        self.key = str(key)
        self.value = _copy(value)
        self.new_authored_value = _copy(value)

    def validate(self, model: PropertyModel) -> ValidationResult:
        found = _scene_element_condition_location(model.mapping, self.element)
        if found is None:
            return ValidationResult.error("The selected scene element no longer exists.")
        return ValidationResult.ok()

    def apply(self, working_copy: DefinitionWorkingCopy) -> None:
        self._old_mapping = working_copy.to_mapping()
        found = _scene_element_condition_location(working_copy.mapping, self.element)
        if found is None:
            raise KeyError("The selected scene element no longer exists")
        _path, target = found
        if not isinstance(target, dict):
            raise TypeError("Scene elements must be mappings")
        self.old_authored_value = _copy(target.get(self.key, MISSING))
        if self.value is MISSING:
            target.pop(self.key, None)
        else:
            target[self.key] = _copy(self.value)


class RenameSceneElementCommand(EditCommand):
    """Rename one local object/look-region ID as one undoable edit."""

    operation = "rename_scene_element"

    def __init__(self, selection: DefinitionSelection, element: Any, new_id: str) -> None:
        super().__init__(selection, ())
        self.element = element
        self.old_id = str(getattr(element, "id", ""))
        self.new_id = str(new_id).strip()

    def validate(self, model: PropertyModel) -> ValidationResult:
        kind = getattr(self.element, "kind", None)
        if kind not in {"object", "look_region"}:
            return ValidationResult.error("Only scene objects and Look Regions can be renamed.")
        if not self.old_id:
            return ValidationResult.error("The selected scene element has no ID.")
        if not self.new_id:
            return ValidationResult.error("A Look Region ID cannot be empty.")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", self.new_id):
            return ValidationResult.error("ID must start with a letter and contain only letters, numbers, '_' or '-'.")
        found = _scene_element_condition_location(model.mapping, self.element)
        if found is None:
            return ValidationResult.error("The selected scene element no longer exists.")
        if self.new_id != self.old_id and self.new_id in _scene_element_ids(model.mapping):
            return ValidationResult.error(f"Scene element id {self.new_id!r} is already in use.")
        return ValidationResult.ok()

    def apply(self, working_copy: DefinitionWorkingCopy) -> None:
        self._old_mapping = working_copy.to_mapping()
        found = _scene_element_condition_location(working_copy.mapping, self.element)
        if found is None:
            raise KeyError("The selected scene element no longer exists")
        _path, target = found
        if not isinstance(target, dict):
            raise TypeError("Scene elements must be mappings")
        target["id"] = self.new_id


class CreateLookEventCommand(EditCommand):
    """Create a local look-event payload and attach it to one target."""

    operation = "create_look_event"

    def __init__(self, selection: DefinitionSelection, element: Any, event_id: str) -> None:
        super().__init__(selection, ())
        self.element = element
        self.event_id = str(event_id).strip()

    def validate(self, model: PropertyModel) -> ValidationResult:
        if getattr(self.element, "kind", None) != "look_region":
            return ValidationResult.error("Only Look Regions can create a Look Event.")
        if not self.event_id:
            return ValidationResult.error("A Look Event ID cannot be empty.")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", self.event_id):
            return ValidationResult.error("Event ID must start with a letter and contain only letters, numbers, '_' or '-'.")
        if _scene_element_condition_location(model.mapping, self.element) is None:
            return ValidationResult.error("The selected Look Region no longer exists.")
        events = _look_events_mapping(model.mapping)
        if self.event_id in events:
            return ValidationResult.error(f"Look Event {self.event_id!r} already exists.")
        return ValidationResult.ok()

    def apply(self, working_copy: DefinitionWorkingCopy) -> None:
        self._old_mapping = working_copy.to_mapping()
        found = _scene_element_condition_location(working_copy.mapping, self.element)
        if found is None:
            raise KeyError("The selected Look Region no longer exists")
        _path, target = found
        if not isinstance(target, dict):
            raise TypeError("Look Regions must be mappings")
        events = _ensure_look_events_mapping(working_copy.mapping)
        if self.event_id in events:
            raise ValueError(f"Look Event {self.event_id!r} already exists")
        events[self.event_id] = {"actions": []}
        target["event"] = self.event_id


def _look_events_mapping(mapping: Mapping[str, Any]) -> Mapping[str, Any]:
    exploration = mapping.get("exploration")
    if isinstance(exploration, Mapping) and isinstance(exploration.get("look_events"), Mapping):
        return exploration["look_events"]
    events = mapping.get("look_events")
    return events if isinstance(events, Mapping) else {}


def _ensure_look_events_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
    exploration = mapping.get("exploration")
    if isinstance(exploration, dict):
        events = exploration.get("look_events")
        if events is None:
            exploration["look_events"] = {}
            events = exploration["look_events"]
        if isinstance(events, dict):
            return events
        raise TypeError("exploration.look_events must be a mapping")
    if exploration is True or exploration is None:
        mapping["exploration"] = {"look_events": {}}
        return mapping["exploration"]["look_events"]
    events = mapping.get("look_events")
    if events is None:
        mapping["look_events"] = {}
        events = mapping["look_events"]
    if isinstance(events, dict):
        return events
    raise TypeError("look_events must be a mapping")


class SetDialogueTextCommand(EditCommand):
    """Commit one completed dialogue text editing session."""

    operation = "set_dialogue_text"

    def __init__(self, selection: DefinitionSelection, text_path: Sequence[str | int], text: str) -> None:
        super().__init__(selection, text_path)
        self.text = str(text)
        self.value = self.text

    def validate(self, model: PropertyModel) -> ValidationResult:
        current = _collection_at(model.mapping, self.path)
        if current is MISSING:
            return ValidationResult.error("The selected dialogue entry no longer exists.")
        if not isinstance(current, str):
            return ValidationResult.error("Only authored string dialogue text can be edited here.")
        return ValidationResult.ok()

    def apply(self, working_copy: DefinitionWorkingCopy) -> None:
        self._old_mapping = working_copy.to_mapping()
        self.old_authored_value = _copy(_collection_at(working_copy.mapping, self.path))
        _set_path(working_copy.mapping, self.path, self.text)


class SetDialogueConditionCommand(EditCommand):
    """Author, replace, or remove a scene-entry condition without rewriting its dialect."""

    operation = "set_dialogue_condition"

    def __init__(self, selection: DefinitionSelection, entry_path: Sequence[str | int], condition: Any = MISSING) -> None:
        super().__init__(selection, entry_path)
        self.entry_path = tuple(entry_path)
        self.condition = _copy(condition)
        self.value = self.condition

    def validate(self, model: PropertyModel) -> ValidationResult:
        entry = _collection_at(model.mapping, self.entry_path)
        if not isinstance(entry, Mapping):
            return ValidationResult.error("The selected dialogue entry has no editable metadata.")
        if self.condition is MISSING:
            return ValidationResult.ok()
        try:
            parse_condition(self.condition)
        except (ConditionError, TypeError, ValueError) as exc:
            return ValidationResult.error(str(exc))
        return ValidationResult.ok()

    def apply(self, working_copy: DefinitionWorkingCopy) -> None:
        self._old_mapping = working_copy.to_mapping()
        entry = _collection_at(working_copy.mapping, self.entry_path)
        if not isinstance(entry, dict):
            raise TypeError("Dialogue entries must be mappings")
        if self.condition is MISSING:
            entry.pop("conditions", None)
            entry.pop("condition", None)
            return
        key = "conditions" if "conditions" in entry else ("condition" if "condition" in entry else "conditions")
        entry[key] = _copy(self.condition)


class SetDialogueMetadataCommand(EditCommand):
    """Set one small authored metadata field while preserving all siblings."""

    operation = "set_dialogue_metadata"

    def __init__(self, selection: DefinitionSelection, entry_path: Sequence[str | int], key: str, value: Any) -> None:
        super().__init__(selection, tuple(entry_path) + (str(key),))
        self.entry_path = tuple(entry_path)
        self.key = str(key)
        self.value = _copy(value)

    def validate(self, model: PropertyModel) -> ValidationResult:
        return ValidationResult.ok() if isinstance(_collection_at(model.mapping, self.entry_path), Mapping) else ValidationResult.error(
            "The selected dialogue entry has no editable metadata."
        )

    def apply(self, working_copy: DefinitionWorkingCopy) -> None:
        self._old_mapping = working_copy.to_mapping()
        entry = _collection_at(working_copy.mapping, self.entry_path)
        if not isinstance(entry, dict):
            raise TypeError("Dialogue entries must be mappings")
        entry[self.key] = _copy(self.value)


class DialogueStructuralEditCommand(StructuralEditCommand):
    """Base for list-backed dialogue entry operations."""

    operation = "dialogue_structural_edit"

    def __init__(self, selection: DefinitionSelection, collection_path: Sequence[str | int]) -> None:
        super().__init__(selection, collection_path)

    def _dialogue_list(self, mapping: dict[str, Any], *, create: bool = False) -> list[Any]:
        collection = _collection_at(mapping, self.collection_path)
        if collection is MISSING and create:
            parent, key = _ensure_collection_parent(mapping, self.collection_path)
            parent[key] = []
            collection = parent[key]
        if not isinstance(collection, list):
            raise TypeError("Dialogue entry operations require a list-backed dialogue collection")
        return collection


class InsertDialogueEntryCommand(DialogueStructuralEditCommand):
    operation = "insert_dialogue_entry"

    def __init__(self, selection: DefinitionSelection, collection_path: Sequence[str | int], entry: Any, *, index: int | None = None) -> None:
        super().__init__(selection, collection_path)
        self.entry = _copy(entry)
        self.index = index

    def _apply_once(self, working_copy: DefinitionWorkingCopy) -> None:
        entries = self._dialogue_list(working_copy.mapping, create=True)
        self.index = len(entries) if self.index is None else max(0, min(int(self.index), len(entries)))
        entries.insert(self.index, _copy(self.entry))


class RemoveDialogueEntryCommand(DialogueStructuralEditCommand):
    operation = "remove_dialogue_entry"

    def __init__(self, selection: DefinitionSelection, collection_path: Sequence[str | int], index: int) -> None:
        super().__init__(selection, collection_path)
        self.index = int(index)
        self.removed_entry: Any = MISSING

    def _apply_once(self, working_copy: DefinitionWorkingCopy) -> None:
        entries = self._dialogue_list(working_copy.mapping)
        if not 0 <= self.index < len(entries):
            raise KeyError(f"Dialogue entry {self.index} was not found at {self.collection_path!r}")
        self.removed_entry = _copy(entries.pop(self.index))


class DuplicateDialogueEntryCommand(DialogueStructuralEditCommand):
    operation = "duplicate_dialogue_entry"

    def __init__(self, selection: DefinitionSelection, collection_path: Sequence[str | int], index: int) -> None:
        super().__init__(selection, collection_path)
        self.index = int(index)
        self.duplicate_index: int | None = None
        self.duplicate_entry: Any = MISSING

    def _apply_once(self, working_copy: DefinitionWorkingCopy) -> None:
        entries = self._dialogue_list(working_copy.mapping)
        if not 0 <= self.index < len(entries):
            raise KeyError(f"Dialogue entry {self.index} was not found at {self.collection_path!r}")
        self.duplicate_index = self.index + 1
        self.duplicate_entry = _copy(entries[self.index])
        entries.insert(self.duplicate_index, _copy(self.duplicate_entry))


class MoveDialogueEntryCommand(DialogueStructuralEditCommand):
    operation = "move_dialogue_entry"

    def __init__(self, selection: DefinitionSelection, collection_path: Sequence[str | int], index: int, new_index: int) -> None:
        super().__init__(selection, collection_path)
        self.index = int(index)
        self.new_index = int(new_index)

    def _apply_once(self, working_copy: DefinitionWorkingCopy) -> None:
        entries = self._dialogue_list(working_copy.mapping)
        if not 0 <= self.index < len(entries):
            raise KeyError(f"Dialogue entry {self.index} was not found at {self.collection_path!r}")
        destination = max(0, min(self.new_index, len(entries) - 1))
        value = entries.pop(self.index)
        entries.insert(destination, value)
        self.new_index = destination


def _unique_dialogue_id(collection: Mapping[str, Any], base: str = "dialogue") -> str:
    candidate = str(base).strip() or "dialogue"
    if candidate not in collection:
        return candidate
    suffix = 2
    while f"{candidate}_{suffix}" in collection:
        suffix += 1
    return f"{candidate}_{suffix}"


def _named_sequence_collection(mapping: dict[str, Any], path: PropertyPath, *, create: bool = False) -> dict[str, Any]:
    value = _collection_at(mapping, path)
    if value is MISSING and create:
        parent, key = _ensure_collection_parent(mapping, path)
        parent[key] = {}
        value = parent[key]
    if not isinstance(value, dict):
        raise TypeError("Named dialogue sequences require a mapping")
    return value


class DialogueSequenceStructuralEditCommand(StructuralEditCommand):
    """Atomic mapping operations for named local dialogue sequences."""

    operation = "dialogue_sequence_structural_edit"

    def __init__(self, selection: DefinitionSelection, sequences_path: Sequence[str | int]) -> None:
        super().__init__(selection, sequences_path)

    def _sequences(self, mapping: dict[str, Any], *, create: bool = False) -> dict[str, Any]:
        return _named_sequence_collection(mapping, self.collection_path, create=create)


class InsertNamedDialogueSequenceCommand(DialogueSequenceStructuralEditCommand):
    operation = "insert_named_dialogue_sequence"

    def __init__(
        self,
        selection: DefinitionSelection,
        sequences_path: Sequence[str | int],
        sequence_id: str | None = None,
        sequence: Any = None,
    ) -> None:
        super().__init__(selection, sequences_path)
        self.sequence_id = str(sequence_id) if sequence_id is not None else None
        self.sequence = _copy([] if sequence is None else sequence)

    def _apply_once(self, working_copy: DefinitionWorkingCopy) -> None:
        sequences = self._sequences(working_copy.mapping, create=True)
        requested = self.sequence_id or "dialogue"
        self.sequence_id = _unique_dialogue_id(sequences, requested)
        sequences[self.sequence_id] = _copy(self.sequence)


class DuplicateNamedDialogueSequenceCommand(DialogueSequenceStructuralEditCommand):
    operation = "duplicate_named_dialogue_sequence"

    def __init__(
        self,
        selection: DefinitionSelection,
        sequences_path: Sequence[str | int],
        source_id: str,
        duplicate_id: str | None = None,
    ) -> None:
        super().__init__(selection, sequences_path)
        self.source_id = str(source_id)
        self.duplicate_id = str(duplicate_id) if duplicate_id is not None else None
        self.duplicate_sequence: Any = MISSING

    def _apply_once(self, working_copy: DefinitionWorkingCopy) -> None:
        sequences = self._sequences(working_copy.mapping)
        if self.source_id not in sequences:
            raise KeyError(f"Named dialogue sequence {self.source_id!r} was not found")
        requested = self.duplicate_id or f"{self.source_id}_copy"
        self.duplicate_id = _unique_dialogue_id(sequences, requested)
        self.duplicate_sequence = _copy(sequences[self.source_id])
        sequences[self.duplicate_id] = _copy(self.duplicate_sequence)


class RemoveNamedDialogueSequenceCommand(DialogueSequenceStructuralEditCommand):
    operation = "remove_named_dialogue_sequence"

    def __init__(self, selection: DefinitionSelection, sequences_path: Sequence[str | int], sequence_id: str) -> None:
        super().__init__(selection, sequences_path)
        self.sequence_id = str(sequence_id)
        self.removed_sequence: Any = MISSING

    def validate(self, model: PropertyModel) -> ValidationResult:
        sequences = _collection_at(model.mapping, self.collection_path)
        if not isinstance(sequences, Mapping) or self.sequence_id not in sequences:
            return ValidationResult.error(f"Named dialogue sequence {self.sequence_id!r} was not found.")
        from .dialogue_presentation import dialogue_sequence_references
        references = dialogue_sequence_references(model.mapping, self.sequence_id)
        if references:
            return ValidationResult.error(
                f"Cannot delete referenced dialogue sequence {self.sequence_id!r}: "
                + ", ".join(references)
            )
        return ValidationResult.ok()

    def _apply_once(self, working_copy: DefinitionWorkingCopy) -> None:
        sequences = self._sequences(working_copy.mapping)
        if self.sequence_id not in sequences:
            raise KeyError(f"Named dialogue sequence {self.sequence_id!r} was not found")
        self.removed_sequence = _copy(sequences.pop(self.sequence_id))


class RenameNamedDialogueSequenceCommand(DialogueSequenceStructuralEditCommand):
    """Rename a local sequence and update the known local reference sites atomically."""

    operation = "rename_named_dialogue_sequence"

    def __init__(self, selection: DefinitionSelection, sequences_path: Sequence[str | int], old_id: str, new_id: str) -> None:
        super().__init__(selection, sequences_path)
        self.old_id = str(old_id)
        self.new_id = str(new_id).strip()

    def validate(self, model: PropertyModel) -> ValidationResult:
        sequences = _collection_at(model.mapping, self.collection_path)
        if not isinstance(sequences, Mapping) or self.old_id not in sequences:
            return ValidationResult.error(f"Named dialogue sequence {self.old_id!r} was not found.")
        if not self.new_id:
            return ValidationResult.error("A dialogue sequence ID cannot be empty.")
        if self.new_id != self.old_id and self.new_id in sequences:
            return ValidationResult.error(f"Named dialogue sequence {self.new_id!r} already exists.")
        return ValidationResult.ok()

    def _apply_once(self, working_copy: DefinitionWorkingCopy) -> None:
        sequences = self._sequences(working_copy.mapping)
        value = sequences.pop(self.old_id)
        sequences[self.new_id] = value
        _rewrite_known_dialogue_references(working_copy.mapping, self.old_id, self.new_id)


def _rewrite_known_dialogue_references(mapping: dict[str, Any], old_id: str, new_id: str) -> None:
    """Rewrite only fields owned by the exploration/dialogue dialect."""

    config = mapping.get("exploration") if isinstance(mapping.get("exploration"), Mapping) else mapping
    if not isinstance(config, dict):
        return
    entries = config.get("dialog")
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict):
                for key in ("sequence", "dialog"):
                    if entry.get(key) == old_id:
                        entry[key] = new_id
                for key in ("sequence", "dialog"):
                    if isinstance(entry.get(key), dict):
                        _rewrite_dialogue_reference_value(entry[key], old_id, new_id)
    events = config.get("look_events")
    if isinstance(events, dict):
        for event in events.values():
            if isinstance(event, dict):
                _rewrite_dialogue_reference_value(event.get("actions"), old_id, new_id)
    for namespace in ("objects", "look_regions"):
        values = config.get(namespace)
        if isinstance(values, list):
            for value in values:
                if isinstance(value, dict):
                    _rewrite_dialogue_reference_value(value.get("look"), old_id, new_id)
    sequences = config.get("dialogue_sequences")
    if isinstance(sequences, dict):
        for sequence in sequences.values():
            if isinstance(sequence, dict):
                _rewrite_dialogue_reference_value(sequence.get("actions"), old_id, new_id)


def _rewrite_dialogue_reference_value(value: Any, old_id: str, new_id: str) -> None:
    if isinstance(value, dict):
        for key, child in list(value.items()):
            if key in {"sequence", "dialog"} and child == old_id:
                value[key] = new_id
            else:
                _rewrite_dialogue_reference_value(child, old_id, new_id)
    elif isinstance(value, list):
        for child in value:
            _rewrite_dialogue_reference_value(child, old_id, new_id)


class DialogueActionStructuralEditCommand(StructuralEditCommand):
    """Atomic list operations which retain action dialects and sibling data."""

    operation = "dialogue_action_structural_edit"

    def __init__(self, selection: DefinitionSelection, actions_path: Sequence[str | int], *, scope: ActionScope | str = ActionScope.EXPLORATION) -> None:
        super().__init__(selection, actions_path)
        self.scope = ActionScope(scope)

    def _actions(self, mapping: dict[str, Any], *, create: bool = False) -> list[Any]:
        actions = _collection_at(mapping, self.collection_path)
        if actions is MISSING and create:
            parent, key = _ensure_collection_parent(mapping, self.collection_path)
            parent[key] = []
            actions = parent[key]
        if not isinstance(actions, list):
            raise TypeError("Dialogue actions require a list")
        return actions


class InsertDialogueActionCommand(DialogueActionStructuralEditCommand):
    operation = "insert_dialogue_action"

    def __init__(self, selection: DefinitionSelection, actions_path: Sequence[str | int], action: Mapping[str, Any], *, index: int | None = None, scope: ActionScope | str = ActionScope.EXPLORATION) -> None:
        super().__init__(selection, actions_path, scope=scope)
        self.action = _copy(dict(action))
        self.index = index

    def _apply_once(self, working_copy: DefinitionWorkingCopy) -> None:
        actions = self._actions(working_copy.mapping, create=True)
        self.index = len(actions) if self.index is None else max(0, min(int(self.index), len(actions)))
        actions.insert(self.index, _copy(self.action))


class RemoveDialogueActionCommand(DialogueActionStructuralEditCommand):
    operation = "remove_dialogue_action"

    def __init__(self, selection: DefinitionSelection, actions_path: Sequence[str | int], index: int, *, scope: ActionScope | str = ActionScope.EXPLORATION) -> None:
        super().__init__(selection, actions_path, scope=scope)
        self.index = int(index)
        self.removed_action: Any = MISSING

    def _apply_once(self, working_copy: DefinitionWorkingCopy) -> None:
        actions = self._actions(working_copy.mapping)
        if not 0 <= self.index < len(actions):
            raise KeyError(f"Dialogue action {self.index} was not found at {self.collection_path!r}")
        self.removed_action = _copy(actions.pop(self.index))


class DuplicateDialogueActionCommand(DialogueActionStructuralEditCommand):
    operation = "duplicate_dialogue_action"

    def __init__(self, selection: DefinitionSelection, actions_path: Sequence[str | int], index: int, *, scope: ActionScope | str = ActionScope.EXPLORATION) -> None:
        super().__init__(selection, actions_path, scope=scope)
        self.index = int(index)
        self.duplicate_index: int | None = None

    def _apply_once(self, working_copy: DefinitionWorkingCopy) -> None:
        actions = self._actions(working_copy.mapping)
        if not 0 <= self.index < len(actions):
            raise KeyError(f"Dialogue action {self.index} was not found at {self.collection_path!r}")
        self.duplicate_index = self.index + 1
        actions.insert(self.duplicate_index, _copy(actions[self.index]))


class MoveDialogueActionCommand(DialogueActionStructuralEditCommand):
    operation = "move_dialogue_action"

    def __init__(self, selection: DefinitionSelection, actions_path: Sequence[str | int], index: int, new_index: int, *, scope: ActionScope | str = ActionScope.EXPLORATION) -> None:
        super().__init__(selection, actions_path, scope=scope)
        self.index = int(index)
        self.new_index = int(new_index)

    def _apply_once(self, working_copy: DefinitionWorkingCopy) -> None:
        actions = self._actions(working_copy.mapping)
        if not 0 <= self.index < len(actions):
            raise KeyError(f"Dialogue action {self.index} was not found at {self.collection_path!r}")
        destination = max(0, min(self.new_index, len(actions) - 1))
        value = actions.pop(self.index)
        actions.insert(destination, value)
        self.new_index = destination


class SetDialogueActionParameterCommand(EditCommand):
    """Edit one typed action parameter while leaving unknown siblings intact."""

    operation = "set_dialogue_action_parameter"

    def __init__(self, selection: DefinitionSelection, action_path: Sequence[str | int], key: str, value: Any, *, scope: ActionScope | str = ActionScope.EXPLORATION) -> None:
        super().__init__(selection, tuple(action_path) + (str(key),))
        self.action_path = tuple(action_path)
        self.key = str(key)
        self.value = _copy(value)
        self.scope = ActionScope(scope)

    def validate(self, model: PropertyModel) -> ValidationResult:
        action = _collection_at(model.mapping, self.action_path)
        if not isinstance(action, Mapping):
            return ValidationResult.error("The selected dialogue action no longer exists.")
        if "type" not in action:
            return ValidationResult.error("Legacy action syntax is read-only; remove and add a typed action instead.")
        action_type = action.get("type")
        try:
            adapted = parse_action(action, self.scope)
        except (ActionError, TypeError, ValueError) as exc:
            return ValidationResult.error(str(exc))
        spec = action_editor_spec(adapted.action_type, self.scope) if isinstance(action_type, str) else None
        if spec is None:
            return ValidationResult.error(f"Action type {action_type!r} is not editor-supported.")
        field = next((item for item in spec.fields if item.key == self.key), None)
        if field is None:
            return ValidationResult.error(f"Unknown action parameter {self.key!r}.")
        if field.kind in {"string", "multiline", "asset", "reference"} and not isinstance(self.value, str):
            return ValidationResult.error(f"{field.display_name} must be text.")
        if field.kind == "boolean" and not isinstance(self.value, bool):
            return ValidationResult.error(f"{field.display_name} must be true or false.")
        if field.kind in {"integer", "number"} and (not isinstance(self.value, (int, float)) or isinstance(self.value, bool)):
            return ValidationResult.error(f"{field.display_name} must be numeric.")
        return ValidationResult.ok()

    def apply(self, working_copy: DefinitionWorkingCopy) -> None:
        self._old_mapping = working_copy.to_mapping()
        action = _collection_at(working_copy.mapping, self.action_path)
        if not isinstance(action, dict):
            raise TypeError("Dialogue actions must be mappings")
        self.old_authored_value = _copy(action.get(self.key, MISSING))
        action[self.key] = _copy(self.value)


# Friendly aliases used by callers that prefer field terminology.
SetDialogueActionFieldCommand = SetDialogueActionParameterCommand
AddNamedDialogueSequenceCommand = InsertNamedDialogueSequenceCommand
DeleteNamedDialogueSequenceCommand = RemoveNamedDialogueSequenceCommand


# Friendly names for callers that describe the action rather than the
# collection mutation.  They intentionally share the same command classes.
AddSceneElementCommand = InsertSceneElementCommand
DeleteSceneElementCommand = RemoveSceneElementCommand


def _valid_geometry_point(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
    )


def _valid_geometry_rect(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) == 4
        and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
        and value[2] > 0
        and value[3] > 0
    )


# ---------------------------------------------------------------------------
# Battle defense-pattern commands

def _defense_sequence_list(mapping: Mapping[str, Any], collection_path: PropertyPath) -> list[Any]:
    value = _collection_at(mapping, collection_path)
    if value is MISSING:
        return []
    if not isinstance(value, list):
        raise TypeError("Defense sequences must be authored as a list")
    return value


def _defense_sequence(mapping: Mapping[str, Any], sequence_path: PropertyPath) -> Mapping[str, Any]:
    value = _collection_at(mapping, sequence_path)
    if not isinstance(value, Mapping):
        raise KeyError(f"Defense sequence was not found at {sequence_path!r}")
    return value


def _defense_pattern(mapping: Mapping[str, Any], sequence_path: PropertyPath, index: int) -> Mapping[str, Any]:
    sequence = _defense_sequence(mapping, sequence_path)
    patterns = sequence.get("patterns")
    if not isinstance(patterns, list) or not 0 <= index < len(patterns):
        raise KeyError(f"Defense pattern {index} was not found at {sequence_path!r}")
    value = patterns[index]
    if not isinstance(value, Mapping):
        raise TypeError("Defense pattern entries must be mappings")
    return value


def _defense_references(mapping: Any, identifier: str, *, skip_prefix: PropertyPath = ()) -> tuple[str, ...]:
    """Find authored references without normalizing legacy battle shapes."""

    reference_keys = {"pattern", "defense_sequence", "pattern_id", "sequence_id", "sequence"}
    found: list[str] = []

    def visit(value: Any, path: PropertyPath) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                child_path = path + (str(key),)
                if child_path[:len(skip_prefix)] == skip_prefix:
                    continue
                if str(key) in reference_keys and child == identifier:
                    found.append(_path_text(child_path))
                visit(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, path + (index,))

    visit(mapping, ())
    return tuple(dict.fromkeys(found))


def defense_pattern_references(mapping: Mapping[str, Any], identifier: str) -> tuple[str, ...]:
    """Public reference report used by the defense editor and diagnostics."""

    return _defense_references(mapping, str(identifier))


def _defense_type_valid(value: Any, type_spec: Any) -> bool:
    if value is None:
        return bool(getattr(type_spec, "nullable", False))
    kind = getattr(type_spec, "kind", None)
    if kind in {"string", "multiline_string", "reference", "asset"}:
        return isinstance(value, str)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind in {"float", "number"}:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "enum":
        return value in type_spec.enum_values
    if kind in {"object", "mapping"}:
        return isinstance(value, Mapping)
    if kind == "list":
        return isinstance(value, (list, tuple)) and (
            type_spec.element_type is None or all(_defense_type_valid(item, type_spec.element_type) for item in value)
        )
    if kind == "union":
        return any(_defense_type_valid(value, variant) for variant in type_spec.variants.values())
    return True


def _defense_field_for_path(pattern: Mapping[str, Any], relative_path: PropertyPath):
    from engine.battle.defense_metadata import defense_pattern_editor_spec

    pattern_type = pattern.get("type")
    spec = defense_pattern_editor_spec(pattern_type) if isinstance(pattern_type, str) else None
    if spec is None or not spec.supported:
        return None
    field = spec.field(str(part) for part in relative_path)
    if field is not None:
        return field
    wanted = tuple(str(part) for part in relative_path)
    for candidate in spec.fields:
        if len(candidate.path) == len(wanted) and all(
            left == right or (index == len(wanted) - 1 and right in candidate.field.aliases)
            for index, (left, right) in enumerate(zip(candidate.path, wanted))
        ):
            return candidate
    return None


def _set_nested_value(mapping: dict[str, Any], path: PropertyPath, value: Any) -> None:
    current: Any = mapping
    for component in path[:-1]:
        if not isinstance(current, dict):
            raise TypeError("Defense nested fields require mappings")
        child = current.get(component)
        if not isinstance(child, dict):
            child = {}
            current[component] = child
        current = child
    current[path[-1]] = _copy(value)


class DefensePatternCommand(EditCommand):
    """Base command for an entry in a modern defense sequence."""

    def __init__(self, selection: DefinitionSelection, sequence_path: Sequence[str | int], index: int | None = None) -> None:
        normalized_path = tuple(sequence_path)
        # Callers may naturally pass either the sequence mapping path or its
        # authored ``patterns`` collection path. Accept both without changing
        # the serialized document shape.
        if normalized_path and normalized_path[-1] == "patterns":
            normalized_path = normalized_path[:-1]
        path = normalized_path + ((int(index),) if index is not None else ())
        super().__init__(selection, path)
        self.sequence_path = normalized_path
        self.index = int(index) if index is not None else None

    @staticmethod
    def _candidate_valid(candidate: Mapping[str, Any]) -> ValidationResult:
        from engine.battle.defense import DefenseConfigError, validate_defense_sequence

        try:
            validate_defense_sequence({"patterns": [dict(candidate)]}, "defense_pattern")
        except (DefenseConfigError, TypeError, ValueError) as exc:
            return ValidationResult.error(str(exc))
        return ValidationResult.ok()


class InsertDefensePatternCommand(DefensePatternCommand):
    operation = "insert_defense_pattern"

    def __init__(self, selection: DefinitionSelection, sequence_path: Sequence[str | int], pattern: Mapping[str, Any], *, index: int | None = None) -> None:
        super().__init__(selection, sequence_path, index)
        self.pattern = _copy(dict(pattern))

    def validate(self, model: PropertyModel) -> ValidationResult:
        if not isinstance(self.pattern, dict) or not isinstance(self.pattern.get("type"), str):
            return ValidationResult.error("A defense pattern requires a registered type.")
        candidate = self._candidate_valid(self.pattern)
        if not candidate.valid:
            return candidate
        try:
            sequence = _defense_sequence(model.mapping, self.sequence_path)
        except (KeyError, TypeError) as exc:
            return ValidationResult.error(str(exc))
        if not isinstance(sequence.get("patterns"), list):
            return ValidationResult.error("The selected defense sequence has no editable patterns list.")
        return ValidationResult.ok()

    def apply(self, working_copy: DefinitionWorkingCopy) -> None:
        self._old_mapping = working_copy.to_mapping()
        sequence = _defense_sequence(working_copy.mapping, self.sequence_path)
        patterns = sequence.setdefault("patterns", [])
        if not isinstance(patterns, list):
            raise TypeError("Defense sequence patterns must be a list")
        self.index = len(patterns) if self.index is None else max(0, min(self.index, len(patterns)))
        patterns.insert(self.index, _copy(self.pattern))


class DuplicateDefensePatternCommand(DefensePatternCommand):
    operation = "duplicate_defense_pattern"

    def validate(self, model: PropertyModel) -> ValidationResult:
        try:
            _defense_pattern(model.mapping, self.sequence_path, self.index if self.index is not None else -1)
        except (KeyError, TypeError) as exc:
            return ValidationResult.error(str(exc))
        return ValidationResult.ok()

    def apply(self, working_copy: DefinitionWorkingCopy) -> None:
        self._old_mapping = working_copy.to_mapping()
        source = _defense_pattern(working_copy.mapping, self.sequence_path, self.index if self.index is not None else -1)
        sequence = _defense_sequence(working_copy.mapping, self.sequence_path)
        patterns = sequence["patterns"]
        self.duplicate_index = (self.index or 0) + 1
        duplicate = _copy(dict(source))
        if isinstance(duplicate.get("id"), str):
            base = duplicate["id"] + "_copy"
            candidate = base
            suffix = 2
            ids = {item.get("id") for item in patterns if isinstance(item, Mapping)}
            while candidate in ids:
                candidate = f"{base}_{suffix}"
                suffix += 1
            duplicate["id"] = candidate
        patterns.insert(self.duplicate_index, duplicate)


class RemoveDefensePatternCommand(DefensePatternCommand):
    operation = "remove_defense_pattern"

    def validate(self, model: PropertyModel) -> ValidationResult:
        try:
            pattern = _defense_pattern(model.mapping, self.sequence_path, self.index if self.index is not None else -1)
        except (KeyError, TypeError) as exc:
            return ValidationResult.error(str(exc))
        identifier = pattern.get("id")
        if isinstance(identifier, str) and identifier:
            references = _defense_references(model.mapping, identifier, skip_prefix=self.sequence_path + ("patterns", self.index))
            if references:
                return ValidationResult.error(
                    f"Cannot delete referenced defense pattern {identifier!r}: " + ", ".join(references)
                )
        return ValidationResult.ok()

    def apply(self, working_copy: DefinitionWorkingCopy) -> None:
        self._old_mapping = working_copy.to_mapping()
        sequence = _defense_sequence(working_copy.mapping, self.sequence_path)
        patterns = sequence["patterns"]
        self.removed_pattern = _copy(patterns.pop(self.index if self.index is not None else -1))


class MoveDefensePatternCommand(DefensePatternCommand):
    operation = "move_defense_pattern"

    def __init__(self, selection: DefinitionSelection, sequence_path: Sequence[str | int], index: int, new_index: int) -> None:
        super().__init__(selection, sequence_path, index)
        self.new_index = int(new_index)

    def validate(self, model: PropertyModel) -> ValidationResult:
        try:
            patterns = _defense_sequence(model.mapping, self.sequence_path).get("patterns")
            if not isinstance(patterns, list) or not 0 <= self.index < len(patterns):
                raise KeyError("The selected defense pattern no longer exists.")
        except (KeyError, TypeError) as exc:
            return ValidationResult.error(str(exc))
        return ValidationResult.ok()

    def apply(self, working_copy: DefinitionWorkingCopy) -> None:
        self._old_mapping = working_copy.to_mapping()
        patterns = _defense_sequence(working_copy.mapping, self.sequence_path)["patterns"]
        destination = max(0, min(self.new_index, len(patterns) - 1))
        value = patterns.pop(self.index)
        patterns.insert(destination, value)
        self.new_index = destination


class SetDefensePatternParameterCommand(EditCommand):
    operation = "set_defense_pattern_parameter"

    def __init__(self, selection: DefinitionSelection, pattern_path: Sequence[str | int], field_path: Sequence[str], value: Any) -> None:
        self.pattern_path = tuple(pattern_path)
        self.field_path = tuple(str(part) for part in field_path)
        super().__init__(selection, self.pattern_path + self.field_path)
        self.value = _copy(value)

    def validate(self, model: PropertyModel) -> ValidationResult:
        pattern = _collection_at(model.mapping, self.pattern_path)
        if not isinstance(pattern, Mapping):
            return ValidationResult.error("The selected defense pattern no longer exists.")
        field = _defense_field_for_path(pattern, self.field_path)
        if field is None:
            return ValidationResult.error(f"Field {'.'.join(self.field_path)!r} is not editor-supported for this pattern.")
        if field.editor_hint == "read_only" or field.field.read_only:
            return ValidationResult.error(f"{field.field.display_name} is read-only in the dedicated editor.")
        if not _defense_type_valid(self.value, field.field.type):
            return ValidationResult.error(f"{field.field.display_name} has an invalid value type.")
        if isinstance(self.value, (int, float)) and not isinstance(self.value, bool):
            if field.field.minimum is not None and self.value < field.field.minimum:
                return ValidationResult.error(f"{field.field.display_name} must be at least {field.field.minimum}.")
            if field.field.maximum is not None and self.value > field.field.maximum:
                return ValidationResult.error(f"{field.field.display_name} must be at most {field.field.maximum}.")
        if field.field.allowed_values and self.value not in field.field.allowed_values:
            return ValidationResult.error(f"{field.field.display_name} is not an allowed value.")
        candidate = _copy(dict(pattern))
        _set_nested_value(candidate, self.field_path, self.value)
        return DefensePatternCommand._candidate_valid(candidate)

    def apply(self, working_copy: DefinitionWorkingCopy) -> None:
        self._old_mapping = working_copy.to_mapping()
        pattern = _collection_at(working_copy.mapping, self.pattern_path)
        if not isinstance(pattern, dict):
            raise TypeError("Defense pattern must be a mapping")
        self.old_authored_value = _copy(_get_path(pattern, self.field_path))
        _set_nested_value(pattern, self.field_path, self.value)


class SetDefensePatternTypeCommand(EditCommand):
    operation = "set_defense_pattern_type"

    def __init__(self, selection: DefinitionSelection, pattern_path: Sequence[str | int], type_name: str) -> None:
        self.pattern_path = tuple(pattern_path)
        self.type_name = str(type_name)
        super().__init__(selection, self.pattern_path + ("type",))

    def validate(self, model: PropertyModel) -> ValidationResult:
        from engine.battle.defense_metadata import defense_pattern_editor_spec, minimal_defense_pattern

        if defense_pattern_editor_spec(self.type_name) is None or not defense_pattern_editor_spec(self.type_name).supported:  # type: ignore[union-attr]
            return ValidationResult.error(f"Unknown or unsupported defense pattern type {self.type_name!r}.")
        if not isinstance(_collection_at(model.mapping, self.pattern_path), Mapping):
            return ValidationResult.error("The selected defense pattern no longer exists.")
        return self._candidate_valid(minimal_defense_pattern(self.type_name))

    def apply(self, working_copy: DefinitionWorkingCopy) -> None:
        self._old_mapping = working_copy.to_mapping()
        old = _collection_at(working_copy.mapping, self.pattern_path)
        if not isinstance(old, Mapping):
            raise TypeError("Defense pattern must be a mapping")
        replacement = {"type": self.type_name}
        if isinstance(old.get("id"), str):
            replacement["id"] = old["id"]
        for key in ("start", "duration"):
            if key in old:
                replacement[key] = _copy(old[key])
        parent = _get_path(working_copy.mapping, self.pattern_path[:-1])
        if not isinstance(parent, list) or not isinstance(self.pattern_path[-1], int):
            raise TypeError("Defense pattern path must address a list entry")
        parent[self.pattern_path[-1]] = replacement


class InsertDefenseSequenceCommand(EditCommand):
    operation = "insert_defense_sequence"

    def __init__(self, selection: DefinitionSelection, collection_path: Sequence[str | int], sequence: Mapping[str, Any]) -> None:
        super().__init__(selection, tuple(collection_path))
        self.collection_path = tuple(collection_path)
        self.sequence = _copy(dict(sequence))
        self.index: int | None = None

    def validate(self, model: PropertyModel) -> ValidationResult:
        from engine.battle.defense import DefenseConfigError, validate_defense_sequence
        if not isinstance(self.sequence.get("id"), str) or not self.sequence["id"]:
            return ValidationResult.error("A defense sequence requires a non-empty id.")
        try:
            validate_defense_sequence(self.sequence, "defense_sequence")
        except (DefenseConfigError, TypeError, ValueError) as exc:
            return ValidationResult.error(str(exc))
        return ValidationResult.ok()

    def apply(self, working_copy: DefinitionWorkingCopy) -> None:
        self._old_mapping = working_copy.to_mapping()
        collection = _defense_sequence_list(working_copy.mapping, self.collection_path)
        if _collection_at(working_copy.mapping, self.collection_path) is MISSING:
            parent, key = _ensure_collection_parent(working_copy.mapping, self.collection_path)
            parent[key] = collection
        ids = {entry.get("id") for entry in collection if isinstance(entry, Mapping)}
        base = str(self.sequence.get("id") or "defense")
        identifier = base
        suffix = 2
        while identifier in ids:
            identifier = f"{base}_{suffix}"
            suffix += 1
        self.sequence["id"] = identifier
        collection.append(_copy(self.sequence))
        self.index = len(collection) - 1


class RemoveDefenseSequenceCommand(EditCommand):
    operation = "remove_defense_sequence"

    def __init__(self, selection: DefinitionSelection, collection_path: Sequence[str | int], index: int) -> None:
        super().__init__(selection, tuple(collection_path) + (int(index),))
        self.collection_path = tuple(collection_path)
        self.index = int(index)

    def validate(self, model: PropertyModel) -> ValidationResult:
        try:
            sequence = _defense_sequence_list(model.mapping, self.collection_path)[self.index]
        except (IndexError, TypeError) as exc:
            return ValidationResult.error(str(exc))
        identifier = sequence.get("id") if isinstance(sequence, Mapping) else None
        if isinstance(identifier, str):
            references = _defense_references(model.mapping, identifier, skip_prefix=self.collection_path + (self.index,))
            if references:
                return ValidationResult.error(
                    f"Cannot delete referenced defense sequence {identifier!r}: " + ", ".join(references)
                )
        return ValidationResult.ok()

    def apply(self, working_copy: DefinitionWorkingCopy) -> None:
        self._old_mapping = working_copy.to_mapping()
        collection = _defense_sequence_list(working_copy.mapping, self.collection_path)
        self.removed_sequence = _copy(collection.pop(self.index))


class MoveDefenseSequenceCommand(EditCommand):
    operation = "move_defense_sequence"

    def __init__(self, selection: DefinitionSelection, collection_path: Sequence[str | int], index: int, new_index: int) -> None:
        super().__init__(selection, tuple(collection_path) + (int(index),))
        self.collection_path = tuple(collection_path)
        self.index = int(index)
        self.new_index = int(new_index)

    def validate(self, model: PropertyModel) -> ValidationResult:
        collection = _defense_sequence_list(model.mapping, self.collection_path)
        if not 0 <= self.index < len(collection):
            return ValidationResult.error("The selected defense sequence no longer exists.")
        return ValidationResult.ok()

    def apply(self, working_copy: DefinitionWorkingCopy) -> None:
        self._old_mapping = working_copy.to_mapping()
        collection = _defense_sequence_list(working_copy.mapping, self.collection_path)
        destination = max(0, min(self.new_index, len(collection) - 1))
        value = collection.pop(self.index)
        collection.insert(destination, value)
        self.new_index = destination


__all__ = [
    "DefinitionWorkingCopy",
    "SourceDocumentWorkingCopy",
    "EditCommand",
    "EditValidationError",
    "StructuralEditCommand",
    "InsertSceneElementCommand",
    "RemoveSceneElementCommand",
    "DuplicateSceneElementCommand",
    "RenameSceneElementCommand",
    "CreateLookEventCommand",
    "InsertNavigationEntryCommand",
    "RemoveNavigationEntryCommand",
    "SetNavigationDestinationCommand",
    "SetNavigationConditionCommand",
    "SetDialogueTextCommand",
    "SetDialogueConditionCommand",
    "SetDialogueMetadataCommand",
    "DialogueStructuralEditCommand",
    "InsertDialogueEntryCommand",
    "RemoveDialogueEntryCommand",
    "DuplicateDialogueEntryCommand",
    "MoveDialogueEntryCommand",
    "DialogueSequenceStructuralEditCommand",
    "InsertNamedDialogueSequenceCommand",
    "AddNamedDialogueSequenceCommand",
    "DuplicateNamedDialogueSequenceCommand",
    "RemoveNamedDialogueSequenceCommand",
    "DeleteNamedDialogueSequenceCommand",
    "RenameNamedDialogueSequenceCommand",
    "DialogueActionStructuralEditCommand",
    "InsertDialogueActionCommand",
    "RemoveDialogueActionCommand",
    "DuplicateDialogueActionCommand",
    "MoveDialogueActionCommand",
    "SetDialogueActionParameterCommand",
    "SetDialogueActionFieldCommand",
    "AddSceneElementCommand",
    "DeleteSceneElementCommand",
    "GeometryChange",
    "PropertyDescriptor",
    "PropertyModel",
    "PropertyPath",
    "RemovePropertyCommand",
    "SetPropertyCommand",
    "SetSourcePropertyCommand",
    "SetGeometryCommand",
    "CombatMoveCommand",
    "AddDifficultyLevelCommand",
    "DeleteDifficultyLevelCommand",
    "DuplicateDifficultyLevelCommand",
    "ReplaceQTETypeCommand",
    "SetCombatMoveFieldCommand",
    "DefensePatternCommand",
    "InsertDefensePatternCommand",
    "DuplicateDefensePatternCommand",
    "RemoveDefensePatternCommand",
    "MoveDefensePatternCommand",
    "SetDefensePatternParameterCommand",
    "SetDefensePatternTypeCommand",
    "InsertDefenseSequenceCommand",
    "RemoveDefenseSequenceCommand",
    "MoveDefenseSequenceCommand",
    "defense_pattern_references",
    "ValidationResult",
]
