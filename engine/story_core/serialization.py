"""Semantic Story/Core definition serialization.

PyYAML exposes semantic values rather than comments, source layout, or exact
quoting.  This module therefore guarantees a load -> serialize -> reload
semantic round trip for StoryProject's supported source documents, not textual
fidelity.  Unknown authored fields survive because serialization starts from
the project-held source envelopes rather than rebuilding YAML from typed
fields alone.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from .models import thaw_value


def serialize_definition(definition: Any) -> Any:
    """Return a fresh YAML-compatible semantic mapping for one definition."""

    serializer = getattr(definition, "to_mapping", None)
    if callable(serializer):
        return serializer()
    if isinstance(definition, Mapping):
        return thaw_value(definition)
    raise TypeError("Story definition must expose to_mapping() or be a mapping")


def serialize_project(project: Any, *, include_shared: bool = False) -> dict[str, Any]:
    """Serialize a project to ``{relative_yaml_path: value}`` mappings.

    The mapping is intentionally file-oriented because stories have multiple
    YAML root shapes (notably move files).  It can be passed to
    :func:`write_serialized_project` and then reloaded with
    ``load_story_project``.
    """

    documents = getattr(project, "source_documents", None)
    if isinstance(documents, Mapping) and documents:
        result: dict[str, Any] = {}
        for relative_path, value in documents.items():
            key = _safe_relative_path(relative_path)
            result[key] = thaw_value(value)
        if include_shared:
            # Shared fallback definitions are deliberately read-only from a
            # story's perspective.  They have no story-relative document key
            # and are therefore not written unless a caller explicitly builds
            # its own source package around them.
            _add_shared_animation_documents(project, result)
        return result
    return _fallback_project_documents(project)


def semantic_equivalent(left: Any, right: Any) -> bool:
    """Compare serialized semantic values without claiming textual equality."""

    return serialize_project(left) == serialize_project(right)


def write_serialized_project(
    serialized: Mapping[str, Any] | Any,
    destination: str | Path,
    *,
    sort_keys: bool = False,
) -> tuple[Path, ...]:
    """Write semantic YAML documents under ``destination``.

    This is an explicit utility; normal Story/Core loading never rewrites
    shipped YAML.  Only relative paths are accepted, preventing a serialized
    document from escaping its destination tree.
    """

    documents = serialize_project(serialized) if not isinstance(serialized, Mapping) else dict(serialized)
    root = Path(destination)
    written: list[Path] = []
    for relative_path, value in documents.items():
        relative = Path(_safe_relative_path(relative_path))
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            yaml.safe_dump(thaw_value(value), handle, allow_unicode=True, sort_keys=sort_keys)
        written.append(target)
    return tuple(written)


def dump_project_yaml(project: Any, *, sort_keys: bool = False) -> dict[str, str]:
    """Return YAML text per semantic source document without writing files."""

    return {
        relative_path: yaml.safe_dump(value, allow_unicode=True, sort_keys=sort_keys)
        for relative_path, value in serialize_project(project).items()
    }


def _fallback_project_documents(project: Any) -> dict[str, Any]:
    """Best-effort serializer for manually constructed project-like values."""

    result: dict[str, Any] = {}
    manifest = getattr(project, "manifest", None)
    if manifest is not None:
        result["story.yaml"] = serialize_definition(manifest)
    player = getattr(project, "player_profile", getattr(project, "player", None))
    if player is not None:
        result["player.yaml"] = serialize_definition(player)
    audio = getattr(project, "audio_config", None)
    if isinstance(audio, Mapping) and audio:
        result["audio.yaml"] = thaw_value(audio)
    for directory, attribute in (
        ("scenes", "scenes"), ("battles", "battles"), ("events", "event_pools"),
    ):
        values = getattr(project, attribute, {})
        if isinstance(values, Mapping):
            for identifier, definition in values.items():
                result[f"{directory}/{identifier}.yaml"] = serialize_definition(definition)
    items = getattr(project, "items", {})
    if isinstance(items, Mapping) and items:
        result["items/items.yaml"] = {identifier: serialize_definition(definition) for identifier, definition in items.items()}
    moves = getattr(project, "moves", {})
    if isinstance(moves, Mapping) and moves:
        result["moves/moves.yaml"] = {
            "moves": [serialize_definition(definition) for definition in moves.values()],
            "skill_progression": thaw_value(getattr(project, "move_skill_progression", {})),
        }
    animations = getattr(project, "animations", {})
    if isinstance(animations, Mapping):
        for identifier, definition in animations.items():
            source = getattr(definition, "source", None)
            if source is not None and "shared" in str(source).replace("\\", "/"):
                continue
            result[f"assets/animations/{identifier}/anim.yaml"] = serialize_definition(definition)
    return result


def _add_shared_animation_documents(project: Any, result: dict[str, Any]) -> None:
    animations = getattr(project, "animations", {})
    story_root = Path(getattr(getattr(project, "source", None), "story_root", ""))
    if not isinstance(animations, Mapping):
        return
    for identifier, definition in animations.items():
        source = getattr(definition, "source", None)
        if source is None:
            continue
        try:
            Path(source).relative_to(story_root)
            continue
        except ValueError:
            pass
        result.setdefault(f"_shared/animations/{identifier}/anim.yaml", serialize_definition(definition))


def _safe_relative_path(value: str | Path) -> str:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Serialized source path must be relative and stay inside the project: {value!r}")
    normalized = path.as_posix()
    if not normalized or normalized == ".":
        raise ValueError("Serialized source path must be non-empty")
    return normalized


__all__ = [
    "dump_project_yaml",
    "semantic_equivalent",
    "serialize_definition",
    "serialize_project",
    "write_serialized_project",
]
