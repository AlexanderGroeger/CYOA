"""Read-only version-1 save/reference compatibility assistance.

The runtime save system remains authoritative in Step 2.  This module neither
reads nor writes a slot on its own, creates no ``GameState``, and never
modifies an input mapping.  It merely reports *warnings* about save IDs that
no longer resolve against a loaded StoryProject, which is useful to headless
tools and migration diagnostics while preserving the runtime's tolerated
unknown-item/move behavior.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from engine.story_core.diagnostics import Diagnostics

if TYPE_CHECKING:  # Avoid project import cycles and runtime-save coupling.
    from engine.story_core.project import StoryProject


SAVE_FORMAT_VERSION = 1
_MISSING = object()


class SaveCompatibilityAdapter:
    """Validate static references in a v1 save without loading/changing it."""

    def __init__(self, project: "StoryProject | Any") -> None:
        self.project = project

    def validate(
        self,
        payload: Mapping[str, Any] | Any,
        *,
        source: str | Path | None = None,
    ) -> Diagnostics:
        return validate_v1_save_references(payload, self.project, source=source)

    validate_payload = validate
    validate_save = validate

    def validate_state(
        self,
        state: Mapping[str, Any] | Any,
        *,
        source: str | Path | None = None,
    ) -> Diagnostics:
        """Validate a state mapping or ``GameState``-like snapshot directly."""

        state_mapping = _state_mapping(state)
        return _validate_state_references(state_mapping, self.project, source=_coerce_source(source))


# A descriptive alias is handy for tool code that calls the object a
# validator rather than an adapter.  It intentionally has identical behavior.
SaveReferenceValidator = SaveCompatibilityAdapter


def validate_v1_save_references(
    payload: Mapping[str, Any] | Any,
    project: "StoryProject | Any",
    *,
    source: str | Path | None = None,
) -> Diagnostics:
    """Return warning diagnostics for v1 save IDs unresolved by ``project``.

    The function accepts a decoded JSON payload, not a file path; keeping I/O
    outside the adapter makes its read-only nature explicit and leaves all
    runtime ``load_game`` errors/semantics untouched.  Malformed values are
    reported as warnings rather than exceptions because old v1 saves are
    intentionally loaded permissively by the runtime where possible.
    """

    diagnostics = Diagnostics()
    source_path = _coerce_source(source)
    if not isinstance(payload, Mapping):
        diagnostics.warning(
            "invalid_save_payload",
            "Save payload must be a mapping to inspect Story/Core references.",
            source=source_path,
        )
        return diagnostics

    # Tool callers sometimes already hold the decoded ``state`` object rather
    # than its enclosing JSON payload.  Treat that as a read-only convenience
    # form without confusing a genuinely incomplete save payload for a state.
    if "state" not in payload and _looks_like_state(payload):
        return _validate_state_references(payload, project, source=source_path)

    version = payload.get("save_format_version", _MISSING)
    if version is _MISSING:
        diagnostics.warning(
            "missing_save_format_version",
            "Save has no save_format_version; reference checks use v1-compatible fields.",
            source=source_path,
            path=("save_format_version",),
        )
    elif version != SAVE_FORMAT_VERSION:
        diagnostics.warning(
            "unsupported_save_format_version",
            f"Save format version {version!r} is not version 1; reference checks are advisory only.",
            source=source_path,
            path=("save_format_version",),
        )

    _validate_story_identity(payload, project, diagnostics, source_path)
    state = payload.get("state", _MISSING)
    if state is _MISSING:
        diagnostics.warning(
            "missing_save_state",
            "Save payload has no state mapping to inspect.",
            source=source_path,
            path=("state",),
        )
        return diagnostics
    diagnostics.extend(_validate_state_references(state, project, source=source_path, path_prefix=("state",)))
    return diagnostics


def validate_save_references(
    payload: Mapping[str, Any] | Any,
    project: "StoryProject | Any",
    *,
    source: str | Path | None = None,
) -> Diagnostics:
    """Compatibility alias for :func:`validate_v1_save_references`."""

    return validate_v1_save_references(payload, project, source=source)


def _validate_story_identity(
    payload: Mapping[str, Any],
    project: Any,
    diagnostics: Diagnostics,
    source: Path | None,
) -> None:
    saved_id = payload.get("story_id")
    if not isinstance(saved_id, str) or not saved_id:
        return
    manifest = _attribute(project, "manifest", default=None)
    project_id = getattr(manifest, "id", None)
    if project_id is None and isinstance(manifest, Mapping):
        project_id = manifest.get("id")
    if isinstance(project_id, str) and project_id and saved_id != project_id:
        diagnostics.warning(
            "save_story_id_mismatch",
            f"Save references story '{saved_id}', while this project is '{project_id}'.",
            source=source,
            path=("story_id",),
        )


def _validate_state_references(
    state: Mapping[str, Any] | Any,
    project: Any,
    *,
    source: Path | None,
    path_prefix: tuple[str | int, ...] = (),
) -> Diagnostics:
    diagnostics = Diagnostics()
    state_mapping = _state_mapping(state)
    if state_mapping is None:
        diagnostics.warning(
            "invalid_save_state",
            "Save state must be a mapping to inspect Story/Core references.",
            source=source,
            path=path_prefix,
        )
        return diagnostics

    scene_ids = _known_ids(project, "scenes")
    item_ids = _known_ids(project, "items")
    move_ids = _known_ids(project, "moves", "combat_moves")

    current_scene = state_mapping.get("current_scene")
    if isinstance(current_scene, str) and current_scene and current_scene not in scene_ids:
        diagnostics.warning(
            "unknown_saved_scene",
            f"Saved current scene '{current_scene}' is not present in this project.",
            source=source,
            path=path_prefix + ("current_scene",),
        )

    _warn_unknown_mapping_keys(
        state_mapping.get("inventory"),
        item_ids,
        diagnostics,
        code="unknown_saved_item",
        label="inventory item",
        source=source,
        path=path_prefix + ("inventory",),
    )
    _warn_unknown_mapping_values(
        state_mapping.get("equipment"),
        item_ids,
        diagnostics,
        code="unknown_saved_equipment_item",
        label="equipped item",
        source=source,
        path=path_prefix + ("equipment",),
    )
    _warn_unknown_sequence_entries(
        state_mapping.get("known_moves"),
        move_ids,
        diagnostics,
        code="unknown_saved_move",
        label="known move",
        source=source,
        path=path_prefix + ("known_moves",),
    )
    _warn_unknown_mapping_keys(
        state_mapping.get("known_combat_moves"),
        move_ids,
        diagnostics,
        code="unknown_saved_combat_move",
        label="saved combat move",
        source=source,
        path=path_prefix + ("known_combat_moves",),
    )
    _warn_unknown_sequence_entries(
        state_mapping.get("history"),
        scene_ids,
        diagnostics,
        code="unknown_saved_history_scene",
        label="history scene",
        source=source,
        path=path_prefix + ("history",),
    )
    return diagnostics


def _warn_unknown_mapping_keys(
    value: Any,
    known_ids: set[str],
    diagnostics: Diagnostics,
    *,
    code: str,
    label: str,
    source: Path | None,
    path: tuple[str | int, ...],
) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping):
        diagnostics.warning(
            "invalid_saved_reference_mapping",
            f"Saved {label} collection is not a mapping; it could not be checked.",
            source=source,
            path=path,
        )
        return
    for identifier in value:
        if isinstance(identifier, str) and identifier and identifier not in known_ids:
            diagnostics.warning(
                code,
                f"Saved {label} '{identifier}' is not present in this project; runtime compatibility keeps it inert/tolerated.",
                source=source,
                path=path + (identifier,),
            )


def _warn_unknown_mapping_values(
    value: Any,
    known_ids: set[str],
    diagnostics: Diagnostics,
    *,
    code: str,
    label: str,
    source: Path | None,
    path: tuple[str | int, ...],
) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping):
        diagnostics.warning(
            "invalid_saved_reference_mapping",
            f"Saved {label} collection is not a mapping; it could not be checked.",
            source=source,
            path=path,
        )
        return
    for slot, identifier in value.items():
        if isinstance(identifier, str) and identifier and identifier not in known_ids:
            diagnostics.warning(
                code,
                f"Saved {label} '{identifier}' is not present in this project; ownership is intentionally not required.",
                source=source,
                path=path + (str(slot),),
            )


def _warn_unknown_sequence_entries(
    value: Any,
    known_ids: set[str],
    diagnostics: Diagnostics,
    *,
    code: str,
    label: str,
    source: Path | None,
    path: tuple[str | int, ...],
) -> None:
    if value is None:
        return
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        diagnostics.warning(
            "invalid_saved_reference_sequence",
            f"Saved {label} collection is not a sequence; it could not be checked.",
            source=source,
            path=path,
        )
        return
    for index, identifier in enumerate(value):
        if isinstance(identifier, str) and identifier and identifier not in known_ids:
            diagnostics.warning(
                code,
                f"Saved {label} '{identifier}' is not present in this project; runtime compatibility keeps it tolerated.",
                source=source,
                path=path + (index,),
            )


def _state_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        candidate = to_dict()
        return candidate if isinstance(candidate, Mapping) else None
    return None


def _looks_like_state(value: Mapping[str, Any]) -> bool:
    """Recognize a state snapshot without treating arbitrary YAML as one."""

    state_keys = {
        "current_scene",
        "flags",
        "variables",
        "inventory",
        "stats",
        "equipment",
        "known_moves",
        "known_combat_moves",
        "history",
        "ending_reached",
    }
    return bool(state_keys.intersection(value))


def _known_ids(project: Any, *attribute_names: str) -> set[str]:
    identifiers: set[str] = set()
    for name in attribute_names:
        collection = _attribute(project, name, default=_MISSING)
        if isinstance(collection, Mapping):
            identifiers.update(str(identifier) for identifier in collection)
        elif isinstance(collection, Iterable) and not isinstance(collection, (str, bytes)):
            identifiers.update(
                str(identifier)
                for definition in collection
                if isinstance((identifier := getattr(definition, "id", None)), str) and identifier
            )
    return identifiers


def _attribute(value: Any, *names: str, default: Any = _MISSING) -> Any:
    for name in names:
        try:
            return getattr(value, name)
        except AttributeError:
            continue
    return default


def _coerce_source(source: str | Path | None) -> Path | None:
    return Path(source) if source is not None else None


__all__ = [
    "SAVE_FORMAT_VERSION",
    "SaveCompatibilityAdapter",
    "SaveReferenceValidator",
    "validate_save_references",
    "validate_v1_save_references",
]
