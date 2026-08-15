"""Qt-independent presentation data for the graphical Scene Editor.

This module is deliberately a small adapter.  It does not add a second scene
schema and it never mutates a ``StoryProject`` or a session working mapping.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engine.story_core import StoryProject
from engine.story_core.diagnostics import StorySourceError


@dataclass(frozen=True)
class SceneElementSelection:
    """Stable identity for an element inside one selected scene."""

    scene_id: str
    kind: str
    id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "scene_id", str(self.scene_id))
        object.__setattr__(self, "kind", str(self.kind))
        object.__setattr__(self, "id", str(self.id))


@dataclass(frozen=True)
class NavigationEntrySelection:
    """Editor-only identity for one authored exploration navigation entry."""

    scene_id: str
    path: tuple[str | int, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "scene_id", str(self.scene_id))
        object.__setattr__(self, "path", tuple(self.path))


@dataclass(frozen=True)
class SceneGeometryTarget:
    """The authored path and validated shape used by graphical editing."""

    path: tuple[str | int, ...]
    shape: str
    value: tuple[int, ...]


@dataclass(frozen=True)
class SceneObjectPresentation:
    id: str
    position: tuple[int, int]
    size: tuple[int, int] | None
    z: int
    sprite: str | None
    sprite_path: Path | None
    asset_error: str | None = None
    condition: Any = None
    conditional: bool = False
    authored: Mapping[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class LookRegionPresentation:
    id: str
    rect: tuple[int, int, int, int]
    z: int
    condition: Any = None
    conditional: bool = False
    authored: Mapping[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class ScenePresentation:
    scene_id: str
    logical_size: tuple[int, int]
    background: str | None
    background_path: Path | None
    background_error: str | None
    objects: tuple[SceneObjectPresentation, ...]
    look_regions: tuple[LookRegionPresentation, ...]
    navigation: tuple["NavigationPresentation", ...] = ()
    legacy_sprite: str | None = None
    legacy_sprite_path: Path | None = None
    legacy_sprite_error: str | None = None
    legacy_sprite_position: tuple[int, int] = (0, 0)
    unsupported_animation: str | None = None


@dataclass(frozen=True)
class NavigationPresentation:
    """A non-mutating view of one authored exploration link."""

    selection: NavigationEntrySelection
    destination: str | None
    resolves: bool
    condition: Any = None
    condition_key: str | None = None
    authored: Mapping[str, Any] = field(default_factory=dict, repr=False)


def build_scene_presentation(
    project: StoryProject,
    scene_id: str,
    working_mapping: Mapping[str, Any],
) -> ScenePresentation:
    """Build authoring-space data from the current semantic scene mapping."""

    logical_size = _logical_size(project)
    config = _exploration_mapping(working_mapping)
    objects = tuple(
        _object_presentation(project, entry, index)
        for index, entry in enumerate(_sequence(config.get("objects")))
    )
    regions = tuple(
        _region_presentation(entry, index)
        for index, entry in enumerate(_sequence(config.get("look_regions")))
    )
    navigation_path = navigation_collection_path(working_mapping)
    navigation = tuple(
        _navigation_presentation(project, scene_id, entry, index, navigation_path)
        for index, entry in enumerate(_sequence(config.get("navigation")))
    )

    explicit_background = working_mapping.get("background")
    default_background = getattr(project.manifest, "default_scene_background", None)
    background = explicit_background if isinstance(explicit_background, str) and explicit_background else (
        default_background if isinstance(default_background, str) and default_background else None
    )
    background_path, background_error = _resolve(project, background, "backgrounds")

    sprite = working_mapping.get("sprite")
    sprite = sprite if isinstance(sprite, str) and sprite else None
    sprite_path, sprite_error = _resolve(project, sprite, "sprites")
    sprite_position = _pair(working_mapping.get("sprite_position"), (0, 0))
    animation = working_mapping.get("animation")
    return ScenePresentation(
        scene_id=str(scene_id),
        logical_size=logical_size,
        background=background,
        background_path=background_path,
        background_error=background_error,
        objects=objects,
        look_regions=regions,
        navigation=navigation,
        legacy_sprite=sprite,
        legacy_sprite_path=sprite_path,
        legacy_sprite_error=sprite_error,
        legacy_sprite_position=sprite_position,
        unsupported_animation=animation if isinstance(animation, str) and animation else None,
    )


def _logical_size(project: StoryProject) -> tuple[int, int]:
    display = project.manifest.display
    width = _positive_int(display.get("width"), 1) if isinstance(display, Mapping) else 1
    height = _positive_int(display.get("height"), 1) if isinstance(display, Mapping) else 1
    return width, height


def _exploration_mapping(scene: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = scene.get("exploration")
    if isinstance(raw, Mapping):
        config = dict(raw)
    elif raw is True:
        config = {}
    else:
        config = {}
    # This mirrors the pure runtime normalizer without importing runtime
    # state or evaluating conditions.
    for key in ("objects", "look_regions", "navigation"):
        if key not in config and key in scene:
            config[key] = scene[key]
    return config


def scene_collection_path(working_mapping: Mapping[str, Any], key: str) -> tuple[str, ...]:
    """Return the authored path for a scene-local collection.

    Exploration-local collections win over root aliases.  A new collection
    uses the canonical ``exploration.<key>`` form, which also activates the
    exploration runtime for a previously empty scene.
    """

    if key not in {"objects", "look_regions", "navigation"}:
        raise ValueError(f"Unsupported scene collection: {key!r}")
    exploration = working_mapping.get("exploration")
    if isinstance(exploration, Mapping) and key in exploration:
        return ("exploration", key)
    if key in working_mapping:
        return (key,)
    return ("exploration", key)


def navigation_collection_path(working_mapping: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the semantic path of the current navigation collection."""

    return scene_collection_path(working_mapping, "navigation")


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes, Mapping)) else ()


def _navigation_presentation(
    project: StoryProject,
    scene_id: str,
    raw: Any,
    index: int,
    collection_path: tuple[str, ...],
) -> NavigationPresentation:
    entry = raw if isinstance(raw, Mapping) else {}
    destination = entry.get("scene")
    destination = destination if isinstance(destination, str) and destination else None
    condition_key = "conditions" if "conditions" in entry else ("condition" if "condition" in entry else None)
    condition = entry.get(condition_key) if condition_key is not None else None
    return NavigationPresentation(
        selection=NavigationEntrySelection(scene_id, (*collection_path, index)),
        destination=destination,
        resolves=destination in project.scenes if destination is not None else False,
        condition=condition,
        condition_key=condition_key,
        authored=entry,
    )


def _object_presentation(project: StoryProject, raw: Any, index: int) -> SceneObjectPresentation:
    entry = raw if isinstance(raw, Mapping) else {}
    identifier = entry.get("id")
    object_id = identifier if isinstance(identifier, str) and identifier else f"object_{index}"
    sprite = entry.get("sprite")
    sprite = sprite if isinstance(sprite, str) and sprite else None
    path, error = _resolve(project, sprite, "sprites")
    size = _optional_pair(entry.get("size"))
    condition = entry.get("visible_when", entry.get("conditions"))
    return SceneObjectPresentation(
        id=object_id,
        position=_pair(entry.get("position"), (0, 0)),
        size=size,
        z=_integer(entry.get("z"), 0),
        sprite=sprite,
        sprite_path=path,
        asset_error=error,
        condition=condition,
        conditional="visible_when" in entry or "conditions" in entry,
        authored=entry,
    )


def _region_presentation(raw: Any, index: int) -> LookRegionPresentation:
    entry = raw if isinstance(raw, Mapping) else {}
    identifier = entry.get("id")
    region_id = identifier if isinstance(identifier, str) and identifier else f"look_region_{index}"
    rect = entry.get("rect", entry.get("hitbox"))
    if not isinstance(rect, Sequence) or isinstance(rect, (str, bytes, Mapping)) or len(rect) != 4:
        normalized = (0, 0, 1, 1)
    else:
        normalized = tuple(_integer(value, 0) for value in rect)  # type: ignore[assignment]
        normalized = (normalized[0], normalized[1], max(1, normalized[2]), max(1, normalized[3]))
    condition = entry.get("visible_when", entry.get("conditions"))
    return LookRegionPresentation(
        id=region_id,
        rect=normalized,
        z=_integer(entry.get("z"), 0),
        condition=condition,
        conditional="visible_when" in entry or "conditions" in entry,
        authored=entry,
    )


def _resolve(project: StoryProject, reference: str | None, category: str) -> tuple[Path | None, str | None]:
    if reference is None:
        return None, None
    try:
        return project.source.resolve_asset_reference(reference, category), None
    except (StorySourceError, OSError, ValueError) as exc:
        return None, str(exc)


def _pair(value: Any, default: tuple[int, int]) -> tuple[int, int]:
    pair = _optional_pair(value)
    return pair if pair is not None else default


def _optional_pair(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, Mapping)) or len(value) != 2:
        return None
    return (_integer(value[0], 0), _integer(value[1], 0))


def _integer(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _positive_int(value: Any, default: int) -> int:
    result = _integer(value, default)
    return result if result > 0 else default


def scene_geometry_target(
    working_mapping: Mapping[str, Any],
    selection: SceneElementSelection,
) -> SceneGeometryTarget | None:
    """Resolve one editable element to its existing authored geometry path.

    This deliberately follows the compatibility layout used by the runtime:
    exploration-local ``objects``/``look_regions`` win, otherwise the legacy
    scene-root aliases are used.  No editor-only geometry is synthesized for
    regions that do not already author a rectangular field.
    """

    if selection.kind not in {"object", "look_region"}:
        return None
    key = "objects" if selection.kind == "object" else "look_regions"
    section, section_path = _geometry_section(working_mapping, key)
    entries = _sequence(section.get(key))
    for index, raw in enumerate(entries):
        if not isinstance(raw, Mapping) or raw.get("id") != selection.id:
            continue
        entry_path = section_path + (key, index)
        if selection.kind == "object":
            if "position" not in raw:
                return SceneGeometryTarget(entry_path + ("position",), "point", (0, 0))
            value = _strict_point(raw.get("position"))
            return None if value is None else SceneGeometryTarget(entry_path + ("position",), "point", value)

        for geometry_key in ("rect", "hitbox"):
            if geometry_key in raw:
                value = _strict_rect(raw.get(geometry_key))
                return None if value is None else SceneGeometryTarget(entry_path + (geometry_key,), "rect", value)
        look = raw.get("look")
        if isinstance(look, Mapping):
            for geometry_key in ("rect", "hitbox"):
                if geometry_key in look:
                    value = _strict_rect(look.get(geometry_key))
                    return None if value is None else SceneGeometryTarget(
                        entry_path + ("look", geometry_key), "rect", value
                    )
        return None
    return None


def _geometry_section(mapping: Mapping[str, Any], key: str) -> tuple[Mapping[str, Any], tuple[str | int, ...]]:
    exploration = mapping.get("exploration")
    if isinstance(exploration, Mapping) and key in exploration:
        return exploration, ("exploration",)
    return mapping, ()


def _strict_point(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        return None
    return int(value[0]), int(value[1])


def _strict_rect(value: Any) -> tuple[int, int, int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        return None
    x, y, width, height = (int(item) for item in value)
    if width <= 0 or height <= 0:
        return None
    return x, y, width, height


__all__ = [
    "LookRegionPresentation",
    "SceneElementSelection",
    "NavigationEntrySelection",
    "NavigationPresentation",
    "SceneGeometryTarget",
    "SceneObjectPresentation",
    "ScenePresentation",
    "build_scene_presentation",
    "scene_geometry_target",
    "scene_collection_path",
    "navigation_collection_path",
]
