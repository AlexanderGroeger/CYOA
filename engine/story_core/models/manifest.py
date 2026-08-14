"""Static story-manifest model."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import (
    DefinitionEnvelope,
    FieldPath,
    FrozenMapping,
    as_frozen_mapping,
    freeze_mapping,
    freeze_value,
    mapping_value,
    string_value,
)


@dataclass(frozen=True)
class StoryManifest(DefinitionEnvelope):
    """An immutable envelope for ``story.yaml``.

    The fields mirror the stable, story-wide values consumed by the current
    engine.  Less common or future fields remain in :attr:`authored`, which
    is the serialization and compatibility authority.
    """

    id: str
    source: Path
    authored: FrozenMapping = field(default_factory=dict, repr=False)
    field_path: FieldPath = ()
    title: str = ""
    version: str = "0.0"
    start_scene: str | None = None
    display: FrozenMapping = field(default_factory=dict)
    starting_flags: FrozenMapping = field(default_factory=dict)
    starting_variables: FrozenMapping = field(default_factory=dict)
    starting_stats: FrozenMapping = field(default_factory=dict)
    starting_inventory: Any = ()
    starting_equipment: FrozenMapping = field(default_factory=dict)
    render: FrozenMapping = field(default_factory=dict)
    navigation: FrozenMapping = field(default_factory=dict)
    debug: FrozenMapping = field(default_factory=dict)
    default_scene_background: str | None = None

    def __post_init__(self) -> None:
        self._freeze_envelope()
        object.__setattr__(self, "id", str(self.id))
        object.__setattr__(self, "title", self.title if isinstance(self.title, str) and self.title else self.id)
        object.__setattr__(self, "version", str(self.version) if self.version is not None else "0.0")
        object.__setattr__(self, "start_scene", self.start_scene if isinstance(self.start_scene, str) else None)
        for name in (
            "display",
            "starting_flags",
            "starting_variables",
            "starting_stats",
            "starting_equipment",
            "render",
            "navigation",
            "debug",
        ):
            object.__setattr__(self, name, as_frozen_mapping(getattr(self, name)))
        object.__setattr__(self, "starting_inventory", freeze_value(self.starting_inventory))
        object.__setattr__(
            self,
            "default_scene_background",
            self.default_scene_background if isinstance(self.default_scene_background, str) else None,
        )

    @classmethod
    def from_mapping(
        cls,
        data: Mapping[str, Any],
        source: Path | str,
        *,
        identifier: str | None = None,
    ) -> "StoryManifest":
        """Build a manifest without mutating or retaining ``data``.

        A missing legacy ID follows the runtime's directory-name fallback.
        """

        if not isinstance(data, Mapping):
            raise TypeError("Story manifest must be a mapping")
        source_path = Path(source)
        fallback_id = identifier or source_path.parent.name
        manifest_id = data.get("id", fallback_id)
        if not isinstance(manifest_id, str) or not manifest_id:
            manifest_id = fallback_id
        version = data.get("version", "0.0")
        return cls(
            id=manifest_id,
            source=source_path,
            authored=data,
            title=string_value(data, "title", manifest_id) or manifest_id,
            version=str(version) if version is not None else "0.0",
            start_scene=string_value(data, "start_scene"),
            display=mapping_value(data, "display"),
            starting_flags=mapping_value(data, "starting_flags"),
            starting_variables=mapping_value(data, "starting_variables"),
            starting_stats=mapping_value(data, "starting_stats"),
            starting_inventory=freeze_value(data.get("starting_inventory", ())),
            starting_equipment=mapping_value(data, "starting_equipment"),
            render=mapping_value(data, "render"),
            navigation=mapping_value(data, "navigation"),
            debug=mapping_value(data, "debug"),
            default_scene_background=string_value(data, "default_scene_background"),
        )

    @property
    def story_id(self) -> str:
        """Explicit alias for code that wants to distinguish model IDs."""

        return self.id


# ``ManifestDefinition`` is kept as a descriptive alias for callers that use
# the generic "definition" vocabulary for all static files.
ManifestDefinition = StoryManifest


__all__ = ["ManifestDefinition", "StoryManifest"]
