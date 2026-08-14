"""Legacy mutable mapping views over immutable Story/Core definitions.

``StoryProject`` owns isolated, immutable definition envelopes.  The pygame
runtime still consumes raw YAML-shaped dictionaries, however, and historically
expects it may mutate the object returned by an individual loader call.  This
adapter is the intentionally temporary boundary between those contracts:
every returned value is a newly-created mutable copy of the authored payload.

The view is not an ``AssetLoader`` replacement.  In particular it does not
provide asset-path lookup or the old mutable YAML cache.  It exists so a
future runtime consumer can opt into a project one loader-shaped operation at
a time without obtaining the core's immutable objects directly.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import TYPE_CHECKING, Any, TypeVar

from engine.errors import AssetNotFoundError

if TYPE_CHECKING:  # Avoid a runtime import cycle while project.py is loading.
    from engine.story_core.project import StoryProject


_MISSING = object()
T = TypeVar("T")


def _mutable_copy(value: Any) -> Any:
    """Return an ordinary, recursively independent Python value.

    Core models expose ``to_mapping()`` specifically for this boundary.  The
    structural fallback also supports simple mapping-backed test projects and
    immutable mapping proxies without relying on ``deepcopy`` support for a
    particular proxy implementation.
    """

    to_mapping = getattr(value, "to_mapping", None)
    if callable(to_mapping):
        # Model implementations already thaw their immutable envelope.  A
        # second copy protects callers if a future model returns a cached
        # mutable mapping from this method.
        return _mutable_copy(to_mapping())
    if isinstance(value, Mapping):
        return {_mutable_copy(key): _mutable_copy(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mutable_copy(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return {_mutable_copy(item) for item in value}
    try:
        return deepcopy(value)
    except Exception:
        # Authored YAML is normally made of the containers handled above and
        # scalar values.  Preserve an opaque scalar rather than making this
        # read-only transition adapter unexpectedly fail on a custom value.
        return value


def _mapping_copy(value: Any, *, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    """Copy one authored mapping, retaining loader-style empty defaults."""

    if value is _MISSING or value is None:
        return {} if fallback is None else _mutable_copy(fallback)
    copied = _mutable_copy(value)
    if isinstance(copied, dict):
        return copied
    # A StoryProject normally prevents this before the view is built.  The
    # empty result keeps optional files (audio/player/items) loader-compatible
    # for a lightweight/dummy project while avoiding exposure of immutables.
    return {} if fallback is None else _mutable_copy(fallback)


def _sequence_copy(value: Any) -> list[Any]:
    """Copy one authored sequence, using an empty list for absent content."""

    if value is _MISSING or value is None:
        return []
    copied = _mutable_copy(value)
    return copied if isinstance(copied, list) else []


class LegacyProjectView:
    """Provide fresh legacy-shaped data from a ``StoryProject``.

    Each method deliberately returns a new object graph.  Mutating one scene,
    item, move, or manifest result therefore never mutates the project and
    never changes a later result from this view.  This differs from
    :class:`engine.core.asset_loader.AssetLoader`, whose existing YAML cache
    identity behavior remains unchanged for current runtime callers.
    """

    def __init__(self, project: "StoryProject | Any") -> None:
        self.project = project

    # -- Story-wide authored files -----------------------------------------
    def load_manifest(self) -> dict[str, Any]:
        return _mapping_copy(self._attribute("manifest"))

    def load_player(self) -> dict[str, Any]:
        return _mapping_copy(self._attribute("player_profile", "player", default=_MISSING))

    def load_audio_config(self) -> dict[str, Any]:
        return _mapping_copy(self._attribute("audio_config", "audio", default=_MISSING))

    def load_items(self) -> dict[str, Any]:
        items = self._attribute("items", default=_MISSING)
        if isinstance(items, Mapping):
            return {
                str(item_id): _mapping_copy(definition)
                for item_id, definition in items.items()
            }
        return {
            str(getattr(definition, "id")): _mapping_copy(definition)
            for definition in self._definitions(items)
            if isinstance(getattr(definition, "id", None), str)
        }

    def load_moves(self) -> list[dict[str, Any]]:
        moves = self._attribute("moves", "combat_moves", default=_MISSING)
        if isinstance(moves, Mapping):
            return [_mapping_copy(definition) for definition in moves.values()]
        return [_mapping_copy(definition) for definition in self._definitions(moves)]

    def load_combat_move_config(self) -> dict[str, Any]:
        """Return the old global move envelope, with fresh nested copies."""

        config = self._attribute("combat_move_config", default=_MISSING)
        if config is not _MISSING and config is not None:
            result = _mapping_copy(config)
            # A project may expose models in ``moves`` rather than copied raw
            # mappings.  Ensure callers always receive legacy dicts/lists.
            if "moves" in result:
                result["moves"] = self._copy_move_entries(result["moves"])
            else:
                result["moves"] = self.load_moves()
            if "skill_progression" not in result:
                result["skill_progression"] = _mapping_copy(
                    self._attribute("move_skill_progression", "skill_progression", default=_MISSING)
                )
            return result
        return {
            "moves": self.load_moves(),
            "skill_progression": _mapping_copy(
                self._attribute("move_skill_progression", "skill_progression", default=_MISSING)
            ),
        }

    # Short properties are useful for headless callers and each intentionally
    # invokes the corresponding loader so it remains a fresh copy.
    @property
    def manifest(self) -> dict[str, Any]:
        return self.load_manifest()

    @property
    def player(self) -> dict[str, Any]:
        return self.load_player()

    @property
    def audio_config(self) -> dict[str, Any]:
        return self.load_audio_config()

    @property
    def items(self) -> dict[str, Any]:
        return self.load_items()

    @property
    def moves(self) -> list[dict[str, Any]]:
        return self.load_moves()

    @property
    def combat_move_config(self) -> dict[str, Any]:
        return self.load_combat_move_config()

    # -- ID-addressed authored files ---------------------------------------
    def load_scene(self, scene_id: str) -> dict[str, Any]:
        return self._load_definition("scene", scene_id, "scenes")

    def load_battle(self, battle_id: str) -> dict[str, Any]:
        return self._load_definition("battle", battle_id, "battles")

    def load_event_pool(self, pool_id: str) -> dict[str, Any]:
        return self._load_definition("event pool", pool_id, "event_pools", "events")

    def load_animation(self, animation_name: str) -> dict[str, Any]:
        return self._load_definition("animation", animation_name, "animations")

    # -- internal project-shape adapters -----------------------------------
    def _attribute(self, *names: str, default: T = _MISSING) -> Any | T:
        for name in names:
            try:
                return getattr(self.project, name)
            except AttributeError:
                continue
        return default

    @staticmethod
    def _definitions(value: Any) -> Iterable[Any]:
        if value is _MISSING or value is None or isinstance(value, (str, bytes, Mapping)):
            return ()
        if isinstance(value, Iterable):
            return value
        return ()

    def _copy_move_entries(self, entries: Any) -> list[dict[str, Any]]:
        if isinstance(entries, Mapping):
            entries = entries.values()
        return [_mapping_copy(entry) for entry in self._definitions(entries)]

    def _load_definition(self, label: str, identifier: str, *collection_names: str) -> dict[str, Any]:
        definition = self._definition(identifier, *collection_names)
        if definition is _MISSING:
            # Keep the same exception family as AssetLoader's direct YAML
            # lookup without pretending this view owns file paths/caching.
            suffix = "" if label == "animation" else ".yaml"
            raise AssetNotFoundError(f"No {label} definition named '{identifier}{suffix}' found in StoryProject")
        return _mapping_copy(definition)

    def _definition(self, identifier: str, *collection_names: str) -> Any:
        method_names = {
            "scenes": ("scene", "get_scene"),
            "battles": ("battle", "get_battle"),
            "event_pools": ("event_pool", "get_event_pool"),
            "events": ("event_pool", "get_event_pool"),
            "animations": ("animation", "get_animation"),
        }

        # A real StoryProject has a typed index that retains duplicate
        # candidates.  Consult its strict accessors before the first-wins
        # compatibility mapping so a duplicate filename remains an ambiguity
        # just as AssetLoader.load_scene() reports it.
        if self._attribute("index", default=None) is not None:
            for collection_name in collection_names:
                for method_name in method_names.get(collection_name, ()):
                    method = self._attribute(method_name, default=_MISSING)
                    if not callable(method):
                        continue
                    try:
                        return method(identifier)
                    except KeyError:
                        continue

        for collection_name in collection_names:
            collection = self._attribute(collection_name, default=_MISSING)
            if isinstance(collection, Mapping):
                try:
                    return collection[identifier]
                except KeyError:
                    continue
            for definition in self._definitions(collection):
                if getattr(definition, "id", _MISSING) == identifier:
                    return definition

        # The project API also exposes singular lookup methods.  Supporting
        # these keeps the adapter resilient to a future lazy index-backed
        # collection without tying it to an implementation detail today.
        for collection_name in collection_names:
            for method_name in method_names.get(collection_name, ()):
                method = self._attribute(method_name, default=_MISSING)
                if not callable(method):
                    continue
                try:
                    result = method(identifier)
                except (KeyError, LookupError):
                    continue
                if result is not None:
                    return result
        return _MISSING


__all__ = ["LegacyProjectView"]
