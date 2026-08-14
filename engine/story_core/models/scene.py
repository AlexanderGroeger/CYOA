"""Static scene-definition envelope."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from . import (
    DefinitionEnvelope,
    FieldPath,
    FrozenMapping,
    as_frozen_mapping,
    as_frozen_sequence,
    mapping_value,
    sequence_value,
    string_value,
)


@dataclass(frozen=True)
class SceneDefinition(DefinitionEnvelope):
    """A scene file with typed common fields and a complete raw envelope.

    Exploration, dialogue, renderer, and legacy-transition content remain
    intentionally extensible.  Their full authored syntax is preserved in
    :attr:`authored`; the selected top-level fields make navigation/indexing
    and editor inspection possible without forcing a runtime migration.
    """

    id: str
    source: Path
    authored: FrozenMapping = field(default_factory=dict, repr=False)
    field_path: FieldPath = ()
    declared_id: str | None = None
    text: str = ""
    background: str | None = None
    sprite: str | None = None
    music: str | None = None
    actions: tuple[Any, ...] = ()
    choices: tuple[Any, ...] = ()
    exploration: bool | FrozenMapping | None = None
    navigation: tuple[Any, ...] = ()
    dialogue_sequences: FrozenMapping = field(default_factory=dict)
    objects: tuple[Any, ...] = ()
    look_regions: tuple[Any, ...] = ()
    look_events: FrozenMapping = field(default_factory=dict)
    checkpoint: bool = False
    animation: str | None = None

    def __post_init__(self) -> None:
        self._freeze_envelope()
        object.__setattr__(self, "id", str(self.id))
        object.__setattr__(self, "declared_id", self.declared_id if isinstance(self.declared_id, str) else None)
        object.__setattr__(self, "text", self.text if isinstance(self.text, str) else "")
        for name in ("background", "sprite", "music", "animation"):
            value = getattr(self, name)
            object.__setattr__(self, name, value if isinstance(value, str) else None)
        for name in ("actions", "choices", "navigation", "objects", "look_regions"):
            object.__setattr__(self, name, as_frozen_sequence(getattr(self, name)))
        for name in ("dialogue_sequences", "look_events"):
            object.__setattr__(self, name, as_frozen_mapping(getattr(self, name)))
        if isinstance(self.exploration, Mapping):
            object.__setattr__(self, "exploration", as_frozen_mapping(self.exploration))
        elif self.exploration is not True and self.exploration is not False:
            object.__setattr__(self, "exploration", None)
        object.__setattr__(self, "checkpoint", bool(self.checkpoint))

    @classmethod
    def from_mapping(
        cls,
        data: Mapping[str, Any],
        source: Path | str,
        *,
        identifier: str | None = None,
    ) -> "SceneDefinition":
        if not isinstance(data, Mapping):
            raise TypeError("Scene definition must be a mapping")
        source_path = Path(source)
        declared_id = data.get("id")
        scene_id = identifier or (declared_id if isinstance(declared_id, str) and declared_id else source_path.stem)
        return cls(
            id=scene_id,
            source=source_path,
            authored=data,
            declared_id=declared_id if isinstance(declared_id, str) else None,
            text=string_value(data, "text", "") or "",
            background=string_value(data, "background"),
            sprite=string_value(data, "sprite"),
            music=string_value(data, "music"),
            actions=sequence_value(data, "actions"),
            choices=sequence_value(data, "choices"),
            exploration=data.get("exploration"),
            navigation=sequence_value(data, "navigation"),
            dialogue_sequences=mapping_value(data, "dialogue_sequences"),
            objects=sequence_value(data, "objects"),
            look_regions=sequence_value(data, "look_regions"),
            look_events=mapping_value(data, "look_events"),
            checkpoint=bool(data.get("checkpoint", False)),
            animation=string_value(data, "animation"),
        )

    @property
    def is_exploration(self) -> bool:
        return self.exploration is True or isinstance(self.exploration, Mapping)

    @property
    def exploration_payload(self) -> FrozenMapping:
        """The explicit exploration mapping, or an empty mapping for ``true``.

        Root-level exploration aliases are intentionally left in
        :attr:`authored`; the existing exploration normalizer remains the
        compatibility authority for combining them.
        """

        return self.exploration if isinstance(self.exploration, Mapping) else MappingProxyType({})

    @property
    def exploration_raw(self) -> Any:
        """The immutable authored exploration value, including malformed data.

        ``exploration`` is a convenient typed projection and therefore uses
        ``None`` for an invalid shape.  Static tooling still needs to expose
        the exact authored value so validation can report the same malformed
        field the runtime would reject.
        """

        return self.authored.get("exploration", self.exploration)


__all__ = ["SceneDefinition"]
