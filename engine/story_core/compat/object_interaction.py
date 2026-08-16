"""One-way migration for the retired object-owned exploration interaction.

The normal authored/runtime model is ``look_regions`` plus ``look_events``.
This module is deliberately a compatibility boundary: it accepts the two
legacy object forms that the repository historically recognized and returns a
new canonical scene mapping without changing the source document.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any


# This is the existing Scene Editor fallback for an object with no authored
# size.  Keeping it here makes load-time migration agree with the editor's
# preview instead of creating a zero-size interaction surface.
DEFAULT_OBJECT_SIZE = (48, 36)


def migrate_legacy_object_interactions(
    scene: Mapping[str, Any], *, preserve_object_ids: bool = False,
) -> dict[str, Any]:
    """Return ``scene`` with legacy object interactions canonicalized.

    The input is never mutated.  Only objects containing the legacy ``look``
    mapping or deprecated direct ``actions`` list are migrated.
    Existing regions and events are retained; generated IDs use stable
    ``<object>_interaction`` / ``<region>_event`` names with deterministic
    numeric suffixes on collision.
    """

    result = deepcopy(dict(scene))
    location = _exploration_location(result)
    if location is None:
        return result
    container, write_back = location
    objects = container.get("objects")
    if not _sequence(objects):
        return result
    if not any(_object_has_legacy_interaction(value) for value in objects):
        return result
    if scene.get("exploration") is None and "objects" in scene:
        scene["exploration"] = True

    regions = container.get("look_regions")
    if regions is None:
        regions = []
        container["look_regions"] = regions
    if not isinstance(regions, list):
        return result
    events = container.get("look_events")
    if events is None:
        events = {}
        container["look_events"] = events
    if not isinstance(events, dict):
        return result

    region_ids = {str(value.get("id")) for value in regions if isinstance(value, Mapping) and value.get("id")}
    if not preserve_object_ids:
        region_ids.update(
            str(value.get("id")) for value in objects
            if isinstance(value, Mapping) and value.get("id")
        )
    event_ids = {str(key) for key in events if isinstance(key, str) and key}
    migrated_objects: list[dict[str, Any]] = []
    for raw_object in objects:
        if not isinstance(raw_object, dict):
            migrated_objects.append(raw_object)
            continue
        object_id = raw_object.get("id")
        look = raw_object.get("look")
        direct_actions = raw_object.get("actions")
        has_legacy = isinstance(look, Mapping) or isinstance(direct_actions, list)
        if not has_legacy or not isinstance(object_id, str) or not object_id:
            migrated_objects.append(raw_object)
            continue

        region_base = object_id if preserve_object_ids else f"{object_id}_interaction"
        region_id = _unique_id(region_base, region_ids)
        region_ids.add(region_id)
        look_mapping = dict(look) if isinstance(look, Mapping) else {}
        interaction = look_mapping.get("interaction", raw_object.get("interaction"))
        if interaction not in {"inspect", "action"}:
            interaction = "action" if isinstance(direct_actions, list) else "inspect"

        actions, source_event = _legacy_actions(direct_actions, look_mapping, events)
        event_id = _unique_id(f"{region_id}_event", event_ids)
        event_ids.add(event_id)
        event_payload = deepcopy(dict(events[source_event])) if source_event and isinstance(events.get(source_event), Mapping) else {}
        event_payload["actions"] = deepcopy(actions)
        events[event_id] = event_payload

        region: dict[str, Any] = {
            key: deepcopy(value)
            for key, value in look_mapping.items()
            if key not in {"rect", "hitbox", "event", "interaction"}
        }
        x, y = _pair(raw_object.get("position"), (0, 0))
        authored_size = _optional_pair(raw_object.get("size"))
        if authored_size is not None:
            rect = [x, y, max(1, authored_size[0]), max(1, authored_size[1])]
        else:
            # Before migration, the runtime's only usable geometry fallback
            # for an unsized object was its explicit legacy look rectangle.
            # Retain that area when present; otherwise use the Scene Editor's
            # established 48x36 preview fallback.
            legacy_rect = _legacy_rect(look_mapping, x, y)
            rect = legacy_rect or [x, y, DEFAULT_OBJECT_SIZE[0], DEFAULT_OBJECT_SIZE[1]]
        region.update({
            "id": region_id,
            "rect": rect,
            "interaction": interaction,
            "event": event_id,
        })
        if "visible_when" in raw_object and "visible_when" not in region:
            region["visible_when"] = deepcopy(raw_object["visible_when"])
        elif "conditions" in raw_object and "conditions" not in region:
            region["conditions"] = deepcopy(raw_object["conditions"])
        if raw_object.get("visible") is False:
            # Look Regions historically use conditions rather than a visible
            # flag.  The runtime now honors this compatibility-preserved flag
            # so a hidden object does not gain an interactive surface.
            region["visible"] = False
        regions.append(region)

        cleaned = dict(raw_object)
        cleaned.pop("look", None)
        cleaned.pop("actions", None)
        cleaned.pop("interaction", None)
        migrated_objects.append(cleaned)

    container["objects"] = migrated_objects
    write_back(container)
    return result


def _legacy_actions(
    direct_actions: Any,
    look: Mapping[str, Any],
    events: Mapping[str, Any],
) -> tuple[list[Any], str | None]:
    if isinstance(direct_actions, list):
        return deepcopy(direct_actions), None
    if isinstance(look.get("actions"), list):
        return deepcopy(look["actions"]), None
    event_id = look.get("event")
    if isinstance(event_id, str) and event_id:
        event = events.get(event_id)
        if isinstance(event, Mapping) and isinstance(event.get("actions"), list):
            return deepcopy(event["actions"]), event_id
    return [], None


def _object_has_legacy_interaction(value: Any) -> bool:
    return isinstance(value, Mapping) and (
        isinstance(value.get("look"), Mapping)
        or isinstance(value.get("actions"), list)
    )


def has_legacy_object_interactions(scene: Mapping[str, Any]) -> bool:
    """Return whether a scene contains an object form this adapter migrates."""

    raw = scene.get("exploration")
    containers: list[Any] = []
    if isinstance(raw, Mapping):
        containers.append(raw)
    elif raw is True:
        containers.append(scene)
    elif isinstance(scene.get("objects"), list):
        containers.append(scene)
    return any(
        isinstance(container, Mapping)
        and any(_object_has_legacy_interaction(value) for value in _sequence(container.get("objects")))
        for container in containers
    )


def _exploration_location(scene: dict[str, Any]) -> tuple[dict[str, Any], Any] | None:
    raw = scene.get("exploration")
    if isinstance(raw, dict):
        return raw, lambda value: scene.__setitem__("exploration", value)
    if raw is True:
        container = scene
        return container, lambda _value: None
    if isinstance(scene.get("objects"), list):
        # Root aliases are accepted by the current exploration normalizer.
        # Activating exploration makes a root-only legacy scene executable.
        return scene, lambda _value: None
    return None


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes, Mapping)) else ()


def _pair(value: Any, default: tuple[int, int]) -> tuple[int, int]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, Mapping)) and len(value) == 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            pass
    return default


def _optional_pair(value: Any) -> tuple[int, int] | None:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, Mapping)) and len(value) == 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            pass
    return None


def _legacy_rect(look: Mapping[str, Any], x: int, y: int) -> list[int] | None:
    raw = look.get("rect")
    local = False
    if raw is None:
        raw = look.get("hitbox")
        local = raw is not None
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, Mapping)) or len(raw) != 4:
        return None
    try:
        values = [int(value) for value in raw]
    except (TypeError, ValueError):
        return None
    if local:
        values[0] += x
        values[1] += y
    values[2] = max(1, values[2])
    values[3] = max(1, values[3])
    return values


def _unique_id(base: str, existing: set[str]) -> str:
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


__all__ = ["DEFAULT_OBJECT_SIZE", "has_legacy_object_interactions", "migrate_legacy_object_interactions"]
