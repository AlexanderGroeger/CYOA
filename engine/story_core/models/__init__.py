"""Immutable static story-definition models.

The runtime currently consumes mutable YAML dictionaries.  The classes in
this package deliberately sit on the other side of that boundary: they keep
the complete authored payload for compatibility, while exposing a small set
of useful typed top-level fields to headless tooling.  ``to_mapping()``
always returns a fresh mutable copy suitable for a legacy consumer.

The package contains no pygame, UI, or runtime-controller imports.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, TypeAlias


FieldPath: TypeAlias = tuple[str | int, ...]
FrozenMapping: TypeAlias = Mapping[str, Any]


def freeze_value(value: Any) -> Any:
    """Return an immutable, recursively isolated view of authored data.

    Story YAML consists of mappings, sequences, and scalar values.  Copying
    those recursively keeps a project definition independent from the
    mutable cache maintained for the legacy runtime.  Mapping proxies and
    tuples are intentionally used instead of a third-party frozen container
    so the core remains lightweight and dependency-free.
    """

    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(freeze_value(item) for item in value)
    if isinstance(value, bytearray):
        return bytes(value)
    return value


def freeze_mapping(value: Mapping[str, Any] | None = None) -> FrozenMapping:
    """Deep-freeze a mapping, treating a missing mapping as an empty one."""

    if value is None:
        return MappingProxyType({})
    if not isinstance(value, Mapping):
        raise TypeError(f"Expected a mapping, got {type(value).__name__}")
    return freeze_value(value)


def thaw_value(value: Any) -> Any:
    """Create a fresh ordinary-Python value from a frozen authored value."""

    if isinstance(value, Mapping):
        return {key: thaw_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_value(item) for item in value]
    if isinstance(value, frozenset):
        return {thaw_value(item) for item in value}
    return value


def thaw_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deep mutable copy of a frozen authored mapping."""

    return {key: thaw_value(item) for key, item in value.items()}


def as_frozen_sequence(value: Any) -> tuple[Any, ...]:
    """Freeze a sequence while avoiding accidental string character splits."""

    if isinstance(value, (list, tuple)):
        return tuple(freeze_value(item) for item in value)
    return ()


def as_frozen_mapping(value: Any) -> FrozenMapping:
    """Freeze a mapping, returning an empty mapping for incompatible input."""

    return freeze_mapping(value) if isinstance(value, Mapping) else MappingProxyType({})


@dataclass(frozen=True)
class SourceProvenance:
    """The source file and nested field path that produced a definition."""

    source: Path
    field_path: FieldPath = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", Path(self.source))
        object.__setattr__(self, "field_path", tuple(self.field_path))

    @property
    def source_path(self) -> Path:
        return self.source


class DefinitionEnvelope:
    """Mixin shared by every static definition dataclass.

    Subclasses declare ``source``, ``authored``, and ``field_path`` as normal
    dataclass fields, then call :meth:`_freeze_envelope` from
    ``__post_init__``.  Keeping the mixin field-free lets each public model
    use the intuitive ``id, source, authored`` constructor order.
    """

    source: Path
    authored: FrozenMapping
    field_path: FieldPath

    def _freeze_envelope(self) -> None:
        object.__setattr__(self, "source", Path(self.source))
        object.__setattr__(self, "field_path", tuple(self.field_path))
        object.__setattr__(self, "authored", freeze_mapping(self.authored))

    @property
    def raw(self) -> FrozenMapping:
        """Immutable compatibility payload containing every authored key."""

        return self.authored

    @property
    def source_path(self) -> Path:
        """Alias used by source/index/diagnostic consumers."""

        return self.source

    @property
    def provenance(self) -> SourceProvenance:
        return SourceProvenance(self.source, self.field_path)

    def to_mapping(self) -> dict[str, Any]:
        """Return a deep mutable semantic copy of the authored payload."""

        return thaw_mapping(self.authored)

    as_mapping = to_mapping


def mapping_value(data: Mapping[str, Any], key: str) -> FrozenMapping:
    """Fetch one optional authored mapping as an immutable mapping."""

    return as_frozen_mapping(data.get(key))


def sequence_value(data: Mapping[str, Any], key: str) -> tuple[Any, ...]:
    """Fetch one optional authored sequence as an immutable tuple."""

    return as_frozen_sequence(data.get(key))


def string_value(data: Mapping[str, Any], key: str, default: str | None = None) -> str | None:
    """Fetch a string without coercing malformed authored values."""

    value = data.get(key, default)
    return value if isinstance(value, str) else default


# Imports live at the end so model modules can reuse the helpers above
# without a circular import during package initialization.
from .animation import AnimationDefinition
from .battle import BattleDefinition
from .event_pool import EventPoolDefinition
from .item import ItemDefinition, StoryItemDefinition
from .manifest import ManifestDefinition, StoryManifest
from .move import CombatMoveDefinition, MoveDefinition
from .player import PlayerProfile, PlayerProfileDefinition
from .scene import SceneDefinition


__all__ = [
    "AnimationDefinition",
    "as_frozen_mapping",
    "as_frozen_sequence",
    "BattleDefinition",
    "CombatMoveDefinition",
    "DefinitionEnvelope",
    "EventPoolDefinition",
    "FieldPath",
    "freeze_mapping",
    "freeze_value",
    "FrozenMapping",
    "ItemDefinition",
    "ManifestDefinition",
    "mapping_value",
    "MoveDefinition",
    "PlayerProfile",
    "PlayerProfileDefinition",
    "SceneDefinition",
    "sequence_value",
    "SourceProvenance",
    "StoryItemDefinition",
    "StoryManifest",
    "string_value",
    "thaw_mapping",
    "thaw_value",
]
