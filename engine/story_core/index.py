"""Type-local story-definition references and project indexing."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar, TypeAlias


FieldPath: TypeAlias = tuple[str | int, ...]


class ContentKind(str, Enum):
    """Namespaces whose IDs may legitimately overlap in one story."""

    MANIFEST = "manifest"
    PLAYER = "player"
    SCENE = "scene"
    ITEM = "item"
    MOVE = "move"
    BATTLE = "battle"
    EVENT_POOL = "event_pool"
    ANIMATION = "animation"
    AUDIO = "audio"

    @classmethod
    def coerce(cls, value: "ContentKind | str") -> "ContentKind":
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "event": cls.EVENT_POOL,
            "events": cls.EVENT_POOL,
            "eventpool": cls.EVENT_POOL,
            "event_pool": cls.EVENT_POOL,
            "event_pools": cls.EVENT_POOL,
            "scenes": cls.SCENE,
            "items": cls.ITEM,
            "moves": cls.MOVE,
            "battles": cls.BATTLE,
            "animations": cls.ANIMATION,
            "player_profile": cls.PLAYER,
            "player_profiles": cls.PLAYER,
        }
        if normalized in aliases:
            return aliases[normalized]
        return cls(normalized)


# ``ContentType`` is a natural alternate name in callers that are concerned
# with static data rather than source-file categories.
ContentType = ContentKind


@dataclass(frozen=True, init=False, eq=False)
class Reference:
    """A content-type-qualified identifier such as ``scene:intro``."""

    kind: ContentKind
    identifier: str

    def __init__(
        self,
        kind: ContentKind | str | None = None,
        identifier: str | None = None,
        *,
        content_type: ContentKind | str | None = None,
        id: str | None = None,
    ) -> None:
        if kind is None:
            kind = content_type
        elif content_type is not None and ContentKind.coerce(kind) is not ContentKind.coerce(content_type):
            raise ValueError("kind and content_type disagree")
        if identifier is None:
            identifier = id
        elif id is not None and identifier != id:
            raise ValueError("identifier and id disagree")
        if kind is None:
            raise TypeError("Reference requires a content kind")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("Reference identifier must be a non-empty string")
        object.__setattr__(self, "kind", ContentKind.coerce(kind))
        object.__setattr__(self, "identifier", identifier)

    @property
    def id(self) -> str:
        return self.identifier

    @property
    def content_type(self) -> ContentKind:
        return self.kind

    @property
    def type(self) -> ContentKind:
        """Short alias convenient for generic reference pickers."""

        return self.kind

    def __str__(self) -> str:
        return f"{self.kind.value}:{self.identifier}"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Reference) and self.kind is other.kind and self.identifier == other.identifier

    def __hash__(self) -> int:
        return hash((self.kind, self.identifier))

    @classmethod
    def parse(cls, value: "Reference | str", *, default_kind: ContentKind | str | None = None) -> "Reference":
        if isinstance(value, Reference):
            return value
        if not isinstance(value, str):
            raise TypeError("Reference must be a Reference or 'kind:id' string")
        if ":" in value:
            raw_kind, identifier = value.split(":", 1)
            return cls(raw_kind, identifier)
        if default_kind is None:
            raise ValueError("An unqualified reference requires default_kind")
        return cls(default_kind, value)

    @classmethod
    def scene(cls, identifier: str) -> "SceneReference":
        return SceneReference(identifier)

    @classmethod
    def item(cls, identifier: str) -> "ItemReference":
        return ItemReference(identifier)

    @classmethod
    def move(cls, identifier: str) -> "MoveReference":
        return MoveReference(identifier)

    @classmethod
    def battle(cls, identifier: str) -> "BattleReference":
        return BattleReference(identifier)

    @classmethod
    def event_pool(cls, identifier: str) -> "EventPoolReference":
        return EventPoolReference(identifier)

    event = event_pool

    @classmethod
    def animation(cls, identifier: str) -> "AnimationReference":
        return AnimationReference(identifier)

    @classmethod
    def player(cls, identifier: str = "player") -> "PlayerReference":
        return PlayerReference(identifier)

    @classmethod
    def manifest(cls, identifier: str) -> "ManifestReference":
        return ManifestReference(identifier)


class _KindReference(Reference):
    """Small concrete reference type used by editor pickers/type checkers."""

    CONTENT_KIND: ClassVar[ContentKind]

    def __init__(self, identifier: str | None = None, *, id: str | None = None) -> None:
        super().__init__(self.CONTENT_KIND, identifier, id=id)


class ManifestReference(_KindReference):
    CONTENT_KIND = ContentKind.MANIFEST


class PlayerReference(_KindReference):
    CONTENT_KIND = ContentKind.PLAYER


class SceneReference(_KindReference):
    CONTENT_KIND = ContentKind.SCENE


class ItemReference(_KindReference):
    CONTENT_KIND = ContentKind.ITEM


class MoveReference(_KindReference):
    CONTENT_KIND = ContentKind.MOVE


class BattleReference(_KindReference):
    CONTENT_KIND = ContentKind.BATTLE


class EventPoolReference(_KindReference):
    CONTENT_KIND = ContentKind.EVENT_POOL


class AnimationReference(_KindReference):
    CONTENT_KIND = ContentKind.ANIMATION


TypedReference = Reference


class UnknownReferenceError(KeyError):
    """A type-qualified reference cannot be found in a project index."""

    def __init__(self, reference: Reference):
        self.reference = reference
        super().__init__(f"Unknown {reference.kind.value} reference {reference.identifier!r}")


class AmbiguousReferenceError(LookupError):
    """Multiple source definitions claim one type-local reference."""

    def __init__(self, reference: Reference, entries: Iterable["IndexEntry"]):
        self.reference = reference
        self.entries = tuple(entries)
        locations = ", ".join(str(entry.source) for entry in self.entries if entry.source is not None)
        message = f"Ambiguous {reference.kind.value} reference {reference.identifier!r}"
        if locations:
            message += f" ({locations})"
        super().__init__(message)


@dataclass(frozen=True)
class IndexEntry:
    """A definition together with its type-local reference and provenance."""

    reference: Reference
    definition: Any
    source: Path | None = None
    field_path: FieldPath = ()

    def __post_init__(self) -> None:
        if not isinstance(self.reference, Reference):
            object.__setattr__(self, "reference", Reference.parse(self.reference))
        source = self.source if self.source is not None else getattr(self.definition, "source", None)
        object.__setattr__(self, "source", Path(source) if source is not None else None)
        object.__setattr__(self, "field_path", tuple(self.field_path or getattr(self.definition, "field_path", ())))

    @property
    def kind(self) -> ContentKind:
        return self.reference.kind

    @property
    def id(self) -> str:
        return self.reference.identifier

    @property
    def source_path(self) -> Path | None:
        return self.source


class ProjectIndex:
    """Mutable build-time index with immutable public mapping views.

    A project loader may register every candidate even when two source files
    collide.  The index retains those candidates for source-qualified
    diagnostics; ``resolve`` refuses an ambiguous reference while ``get``
    offers the first registered definition as a compatibility-oriented view.
    """

    def __init__(self, entries: Iterable[IndexEntry] = ()) -> None:
        self._candidates: dict[Reference, list[IndexEntry]] = {}
        for entry in entries:
            self.register_entry(entry)

    @staticmethod
    def _reference(
        kind_or_reference: ContentKind | str | Reference,
        identifier: str | None = None,
    ) -> Reference:
        if isinstance(kind_or_reference, Reference):
            if identifier is not None and identifier != kind_or_reference.identifier:
                raise ValueError("Reference and identifier disagree")
            return kind_or_reference
        if identifier is None:
            return Reference.parse(str(kind_or_reference))
        return Reference(kind_or_reference, identifier)

    def register(
        self,
        kind_or_reference: ContentKind | str | Reference,
        identifier: str | Any | None = None,
        definition: Any = None,
        *,
        source: Path | str | None = None,
        field_path: FieldPath = (),
    ) -> IndexEntry:
        """Register a candidate without discarding a duplicate.

        ``register(Reference.scene('intro'), definition)`` and
        ``register('scene', 'intro', definition)`` are both accepted.
        """

        if isinstance(kind_or_reference, Reference):
            if definition is None:
                definition = identifier
                identifier = None
            reference = self._reference(kind_or_reference, identifier if isinstance(identifier, str) else None)
        else:
            if definition is None:
                raise TypeError("register(kind, identifier, definition) requires a definition")
            if not isinstance(identifier, str):
                raise TypeError("register(kind, identifier, definition) requires a string identifier")
            reference = self._reference(kind_or_reference, identifier)
        entry = IndexEntry(reference, definition, Path(source) if source is not None else None, tuple(field_path))
        self._candidates.setdefault(reference, []).append(entry)
        return entry

    def register_entry(self, entry: IndexEntry) -> IndexEntry:
        if not isinstance(entry, IndexEntry):
            raise TypeError("Expected an IndexEntry")
        self._candidates.setdefault(entry.reference, []).append(entry)
        return entry

    def add(
        self,
        kind_or_reference: ContentKind | str | Reference,
        identifier: str | Any | None = None,
        definition: Any = None,
        *,
        source: Path | str | None = None,
        field_path: FieldPath = (),
    ) -> IndexEntry:
        """Register one unique definition, raising for a duplicate key."""

        entry = self.register(kind_or_reference, identifier, definition, source=source, field_path=field_path)
        candidates = self._candidates[entry.reference]
        if len(candidates) > 1:
            candidates.pop()
            raise AmbiguousReferenceError(entry.reference, (*candidates, entry))
        return entry

    def add_collection(
        self,
        kind: ContentKind | str,
        definitions: Mapping[str, Any] | Iterable[Any],
    ) -> None:
        """Register a type-local mapping or iterable of models with ``id``."""

        if isinstance(definitions, Mapping):
            iterable = definitions.items()
        else:
            iterable = ((getattr(definition, "id"), definition) for definition in definitions)
        for identifier, definition in iterable:
            self.register(kind, identifier, definition)

    @classmethod
    def from_collections(cls, **collections: Any) -> "ProjectIndex":
        """Build an index from named collections such as ``scenes=...``."""

        index = cls()
        aliases = {
            "manifests": ContentKind.MANIFEST,
            "manifest": ContentKind.MANIFEST,
            "player": ContentKind.PLAYER,
            "players": ContentKind.PLAYER,
            "player_profile": ContentKind.PLAYER,
            "scenes": ContentKind.SCENE,
            "items": ContentKind.ITEM,
            "moves": ContentKind.MOVE,
            "battles": ContentKind.BATTLE,
            "event_pools": ContentKind.EVENT_POOL,
            "events": ContentKind.EVENT_POOL,
            "animations": ContentKind.ANIMATION,
        }
        for name, definitions in collections.items():
            if definitions is None:
                continue
            kind = aliases[name] if name in aliases else ContentKind.coerce(name)
            if kind in {ContentKind.MANIFEST, ContentKind.PLAYER} and not isinstance(definitions, Mapping):
                if isinstance(definitions, Iterable) and not isinstance(definitions, (str, bytes)):
                    index.add_collection(kind, definitions)
                else:
                    index.register(kind, getattr(definitions, "id"), definitions)
            else:
                index.add_collection(kind, definitions)
        return index

    def candidates(self, kind_or_reference: ContentKind | str | Reference, identifier: str | None = None) -> tuple[IndexEntry, ...]:
        return tuple(self._candidates.get(self._reference(kind_or_reference, identifier), ()))

    def entry(self, kind_or_reference: ContentKind | str | Reference, identifier: str | None = None) -> IndexEntry | None:
        entries = self.candidates(kind_or_reference, identifier)
        return entries[0] if entries else None

    def get(
        self,
        kind_or_reference: ContentKind | str | Reference,
        identifier: str | None = None,
        default: Any = None,
    ) -> Any:
        entry = self.entry(kind_or_reference, identifier)
        return entry.definition if entry is not None else default

    find = get
    lookup = get

    def resolve(self, kind_or_reference: ContentKind | str | Reference, identifier: str | None = None) -> Any:
        reference = self._reference(kind_or_reference, identifier)
        entries = self._candidates.get(reference, ())
        if not entries:
            raise UnknownReferenceError(reference)
        if len(entries) != 1:
            raise AmbiguousReferenceError(reference, entries)
        return entries[0].definition

    def contains(self, kind_or_reference: ContentKind | str | Reference, identifier: str | None = None) -> bool:
        return bool(self.candidates(kind_or_reference, identifier))

    __contains__ = contains

    def source_for(self, kind_or_reference: ContentKind | str | Reference, identifier: str | None = None) -> Path | None:
        entry = self.entry(kind_or_reference, identifier)
        return entry.source if entry is not None else None

    def references(self, kind: ContentKind | str | None = None) -> tuple[Reference, ...]:
        refs = self._candidates
        if kind is not None:
            target_kind = ContentKind.coerce(kind)
            refs = {reference: entries for reference, entries in refs.items() if reference.kind is target_kind}
        return tuple(sorted(refs, key=lambda reference: (reference.kind.value, reference.identifier)))

    def entries(self, kind: ContentKind | str | None = None) -> tuple[IndexEntry, ...]:
        if kind is None:
            entries = (entry for candidates in self._candidates.values() for entry in candidates)
        else:
            target_kind = ContentKind.coerce(kind)
            entries = (entry for reference, candidates in self._candidates.items() if reference.kind is target_kind for entry in candidates)
        return tuple(entries)

    def definitions(self, kind: ContentKind | str) -> Mapping[str, Any]:
        target_kind = ContentKind.coerce(kind)
        return MappingProxyType({
            reference.identifier: candidates[0].definition
            for reference, candidates in self._candidates.items()
            if reference.kind is target_kind and candidates
        })

    @property
    def duplicates(self) -> Mapping[Reference, tuple[IndexEntry, ...]]:
        return MappingProxyType({
            reference: tuple(entries)
            for reference, entries in self._candidates.items()
            if len(entries) > 1
        })

    @property
    def has_duplicates(self) -> bool:
        return any(len(entries) > 1 for entries in self._candidates.values())

    @property
    def scenes(self) -> Mapping[str, Any]:
        return self.definitions(ContentKind.SCENE)

    @property
    def items(self) -> Mapping[str, Any]:
        return self.definitions(ContentKind.ITEM)

    @property
    def moves(self) -> Mapping[str, Any]:
        return self.definitions(ContentKind.MOVE)

    @property
    def battles(self) -> Mapping[str, Any]:
        return self.definitions(ContentKind.BATTLE)

    @property
    def event_pools(self) -> Mapping[str, Any]:
        return self.definitions(ContentKind.EVENT_POOL)

    @property
    def animations(self) -> Mapping[str, Any]:
        return self.definitions(ContentKind.ANIMATION)


__all__ = [
    "AmbiguousReferenceError",
    "AnimationReference",
    "BattleReference",
    "ContentKind",
    "ContentType",
    "EventPoolReference",
    "IndexEntry",
    "ItemReference",
    "ManifestReference",
    "MoveReference",
    "PlayerReference",
    "ProjectIndex",
    "Reference",
    "SceneReference",
    "TypedReference",
    "UnknownReferenceError",
]
