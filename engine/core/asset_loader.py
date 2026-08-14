"""
engine/core/asset_loader.py

Everything in the engine that needs to read a story file (scene YAML,
battle YAML, a background .txt/.png, ...) goes through here. Two jobs:

    1. Resolve asset references (e.g. "forest.txt") by checking the
       story's own assets/<category>/ folder first, then falling back to
       shared_assets/<category>/ -- so common sprites/music can be shared
       across stories while a story can still override with its own.
    2. Cache everything by resolved path, so re-entering a scene doesn't
       re-parse its YAML or re-read its art off disk.

This module never interprets what it loads -- it doesn't know what a
"scene" or a "flag" means, just how to find and parse files.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from engine.errors import AssetNotFoundError, StoryValidationError
from engine.story_core.source import StorySource, StorySourceError

# Extensions routed through the pygame image pipeline rather than loaded as
# plain text art. Kept here because AssetLoader resolves the original path.
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif"}


class AssetLoader:
    def __init__(self, story_dir: str, shared_dir: str = "shared_assets"):
        self.story_dir = Path(story_dir)
        self.shared_dir = Path(shared_dir)
        self._cache: dict[str, Any] = {}
        self._scene_paths: dict[str, Path] = {}
        # The shared source owns YAML/path mechanics.  It receives the exact
        # legacy cache so every existing load_* API keeps returning the same
        # mutable cached object as before.
        self._source = StorySource(self.story_dir, self.shared_dir, cache=self._cache)
        # Story/Core is intentionally opt-in for now.  Keeping its project
        # object separate and lazy means all established loader methods keep
        # their current mutable-cache and exception behavior.
        self._project: Any | None = None

    # -- low-level path resolution ------------------------------------------
    def resolve_asset_path(self, category: str, filename: str) -> Path:
        """category is one of: backgrounds, sprites, animations, music, sfx."""
        try:
            return self._source.resolve_asset_path(category, filename)
        except StorySourceError as error:
            raise AssetNotFoundError(str(error)) from None

    def resolve_asset_reference(self, filename: str, default_category: str) -> Path:
        """Resolve either a conventional asset filename or a story-relative path.

        Existing content conventionally stores a short filename such as
        ``forest.png`` and relies on an asset category.  Exploration content
        benefits from being able to name a more specific path such as
        ``assets/scenes/study/desk.png``.  Keeping that normalization here
        means renderers, validators, and event handlers all agree on what a
        reference means.

        Absolute paths are intentionally not accepted: authored content must
        remain portable with its story directory or the shared asset bundle.
        """
        try:
            return self._source.resolve_asset_reference(filename, default_category)
        except StorySourceError as error:
            raise AssetNotFoundError(str(error)) from None

    def is_image_asset(self, filename: str) -> bool:
        return Path(filename).suffix.lower() in IMAGE_EXTENSIONS

    # -- YAML (story-relative, not asset-category-relative) ------------------
    def load_yaml(self, relative_path: str) -> Any:
        try:
            return self._source.load_yaml_legacy(relative_path)
        except FileNotFoundError:
            raise AssetNotFoundError(f"Story file not found: {self.story_dir / relative_path}") from None

    # -- text/binary assets ---------------------------------------------------
    def load_text_asset(self, category: str, filename: str) -> str:
        path = self.resolve_asset_path(category, filename)
        cache_key = str(path)
        if cache_key in self._cache:
            return self._cache[cache_key]
        with open(path, "r", encoding="utf-8") as f:
            data = f.read()
        self._cache[cache_key] = data
        return data

    # -- convenience loaders for each story data file type -------------------
    def load_manifest(self) -> dict[str, Any]:
        return self.load_yaml("story.yaml")

    def load_audio_config(self) -> dict[str, Any]:
        """Load optional story-wide default audio preferences."""
        path = self.story_dir / "audio.yaml"
        if not path.exists():
            return {}
        data = self.load_yaml("audio.yaml")
        if not isinstance(data, dict):
            raise StoryValidationError("audio.yaml must contain a mapping")
        return data

    def load_scene(self, scene_id: str) -> dict[str, Any]:
        """Find a scene by filename anywhere under ``scenes/``.

        Nested scene folders are supported, but a filename may identify only
        one scene. Duplicate ``<id>.yaml`` files are an authoring ambiguity.
        """
        if Path(scene_id).name != scene_id or not scene_id:
            raise StoryValidationError(f"Scene id must be a bare filename, got {scene_id!r}")
        path = self._scene_paths.get(scene_id)
        if path is None:
            scenes_dir = self.story_dir / "scenes"
            matches = sorted(candidate for candidate in scenes_dir.rglob("*.yaml") if candidate.stem == scene_id)
            if not matches:
                raise AssetNotFoundError(f"No scene file named '{scene_id}.yaml' found under {scenes_dir}")
            if len(matches) > 1:
                paths = ", ".join(str(match.relative_to(self.story_dir)) for match in matches)
                raise StoryValidationError(f"Scene id '{scene_id}' is ambiguous; matching files: {paths}")
            path = matches[0]
            self._scene_paths[scene_id] = path
        relative_path = str(path.relative_to(self.story_dir))
        data = self.load_yaml(relative_path)
        if not isinstance(data, dict):
            raise StoryValidationError(f"{relative_path} must contain a mapping")
        if data.get("id") and data["id"] != scene_id:
            raise AssetNotFoundError(
                f"{relative_path} declares id '{data['id']}', "
                f"which doesn't match its filename"
            )
        # Fast structural validation happens on individual loads; the full
        # story pass below additionally checks cross-scene targets/assets.
        from engine.core.exploration import validate_exploration_scene
        validate_exploration_scene(data, scene_id)
        return data

    def scene_ids(self) -> set[str]:
        """Return every unambiguous scene id available to this story.

        The regular ``load_scene`` lookup supports nested folders by filename.
        Exploration validation needs the complete set once so a bad Move
        destination is reported when content loads rather than after a player
        reaches the menu.
        """
        scenes_dir = self.story_dir / "scenes"
        if not scenes_dir.is_dir():
            return set()
        by_id: dict[str, list[Path]] = {}
        for path in scenes_dir.rglob("*.yaml"):
            by_id.setdefault(path.stem, []).append(path)
        duplicate = {scene_id: paths for scene_id, paths in by_id.items() if len(paths) > 1}
        if duplicate:
            scene_id, paths = next(iter(sorted(duplicate.items())))
            joined = ", ".join(str(path.relative_to(self.story_dir)) for path in sorted(paths))
            raise StoryValidationError(f"Scene id {scene_id!r} is ambiguous; matching files: {joined}")
        return set(by_id)

    def validate_exploration_content(self) -> None:
        """Validate opt-in exploration YAML and referenced assets up front.

        Legacy scenes/items keep their permissive loading behavior.  New
        exploration content is intentionally checked as a unit, so authors
        receive the scene, field, and bad reference before starting pygame.
        """
        from engine.core.exploration import (
            exploration_config,
            normalise_event_action,
            validate_exploration_scene,
        )
        from engine.core.inventory import InventoryService, InventorySchemaError

        raw_items = self.load_items()
        try:
            inventory = InventoryService(raw_items)
        except InventorySchemaError as error:
            raise StoryValidationError(f"items/items.yaml: {error}") from error
        for item_id, item in inventory.definitions.items():
            if item.icon:
                try:
                    self.resolve_asset_reference(item.icon, "items")
                except AssetNotFoundError as error:
                    raise StoryValidationError(f"items/items.yaml item {item_id!r} icon {item.icon!r}: {error}") from error

        scene_ids = self.scene_ids()
        battle_ids = self.battle_ids()
        for scene_id in sorted(scene_ids):
            scene = self.load_scene(scene_id)
            config = exploration_config(scene)
            if config is None:
                continue
            try:
                validate_exploration_scene(scene, scene_id, known_scene_ids=scene_ids,
                                           known_battle_ids=battle_ids,
                                           item_ids=set(raw_items))
                if isinstance(scene.get("background"), str) and scene["background"]:
                    self.resolve_asset_reference(scene["background"], "backgrounds")
                if isinstance(scene.get("sprite"), str) and scene["sprite"]:
                    self.resolve_asset_reference(scene["sprite"], "sprites")
                for name, filename in config.get("cursors", {}).items():
                    self.resolve_asset_reference(filename, "sprites")
                for index, obj in enumerate(config.get("objects", [])):
                    if isinstance(obj, dict) and obj.get("sprite"):
                        self.resolve_asset_reference(obj["sprite"], "sprites")
                for event_id, event in config.get("look_events", {}).items():
                    for action in event.get("actions", []) if isinstance(event, dict) else []:
                        action = normalise_event_action(action)
                        if action["type"] == "sound":
                            self.resolve_asset_reference(action["file"], "sfx")
                        elif action["type"] == "music" and action.get("file"):
                            self.resolve_asset_reference(action["file"], "music")
                        elif action["type"] == "animation":
                            self.load_animation(action["animation"])
                        elif action["type"] == "change_sprite":
                            self.resolve_asset_reference(action["sprite"], "sprites")
            except (AssetNotFoundError, StoryValidationError) as error:
                raise StoryValidationError(f"scenes/{scene_id}.yaml: {error}") from error

    def load_battle(self, battle_id: str) -> dict[str, Any]:
        return self.load_yaml(os.path.join("battles", f"{battle_id}.yaml"))

    def battle_ids(self) -> set[str]:
        """Return battle ids addressable by :meth:`load_battle`."""
        battles_dir = self.story_dir / "battles"
        if not battles_dir.is_dir():
            return set()
        return {path.stem for path in battles_dir.glob("*.yaml")}

    def load_event_pool(self, pool_id: str) -> dict[str, Any]:
        return self.load_yaml(os.path.join("events", f"{pool_id}.yaml"))

    def load_items(self) -> dict[str, Any]:
        """items/items.yaml is optional -- a story with no inventory
        content at all doesn't need the file to exist."""
        path = self.story_dir / "items" / "items.yaml"
        if not path.exists():
            return {}
        data = self.load_yaml(os.path.join("items", "items.yaml"))
        if not isinstance(data, dict):
            raise StoryValidationError("items/items.yaml must contain a mapping")
        from engine.core.inventory import InventorySchemaError, InventoryService
        try:
            inventory = InventoryService(data)
        except InventorySchemaError as error:
            raise StoryValidationError(f"items/items.yaml: {error}") from error
        for item_id, item in inventory.definitions.items():
            if item.icon:
                try:
                    self.resolve_asset_reference(item.icon, "items")
                except AssetNotFoundError as error:
                    raise StoryValidationError(
                        f"items/items.yaml item {item_id!r} icon {item.icon!r}: {error}"
                    ) from error
        return data

    def load_player(self) -> dict[str, Any]:
        """Load the story's starting player profile, when it has one."""
        path = self.story_dir / "player.yaml"
        return self.load_yaml("player.yaml") if path.exists() else {}

    def load_moves(self) -> list[dict[str, Any]]:
        """Load move declarations from every YAML file under ``moves/``.

        A file can contain either a top-level ``moves:`` list, a single move
        mapping with ``id``, or a list directly.  Semantic validation and
        duplicate-id checks remain in the battle configuration layer.
        """
        return self.load_combat_move_config()["moves"]

    def load_combat_move_config(self) -> dict[str, Any]:
        """Load global moves plus optional shared skill-progression settings.

        ``load_moves`` remains a compatibility view for integrations that only
        need declarations.  The richer mapping retains the story-level
        ``skill_progression`` block from a combat-move YAML file.
        """
        moves_dir = self.story_dir / "moves"
        if not moves_dir.is_dir():
            return {"moves": [], "skill_progression": {}}
        result: list[dict[str, Any]] = []
        skill_progression: dict[str, Any] | None = None
        for path in sorted(moves_dir.rglob("*.yaml")):
            data = self.load_yaml(str(path.relative_to(self.story_dir)))
            if isinstance(data, dict) and "skill_progression" in data:
                value = data["skill_progression"]
                if not isinstance(value, dict):
                    raise StoryValidationError(f"Move file {path.relative_to(self.story_dir)} skill_progression must be a mapping")
                if skill_progression is not None:
                    raise StoryValidationError("Only one moves/ file may define skill_progression")
                skill_progression = dict(value)
            entries = data.get("moves", []) if isinstance(data, dict) and "moves" in data else data
            if isinstance(entries, dict) and "id" in entries:
                entries = [entries]
            if not isinstance(entries, list):
                raise StoryValidationError(f"Move file {path.relative_to(self.story_dir)} must contain a move or moves list")
            result.extend(entries)
        return {"moves": result, "skill_progression": skill_progression or {}}

    def load_animation(self, animation_name: str) -> dict[str, Any]:
        story_path = self.story_dir / "assets" / "animations" / animation_name / "anim.yaml"
        if story_path.exists():
            return self.load_yaml(os.path.join("assets", "animations", animation_name, "anim.yaml"))
        shared_path = self.shared_dir / "animations" / animation_name / "anim.yaml"
        if shared_path.exists():
            return self._source.load_yaml_path_legacy(shared_path)
        raise AssetNotFoundError(
            f"Animation '{animation_name}' not found in either "
            f"'{story_path}' or '{shared_path}'"
        )

    def animation_dir(self, animation_name: str) -> Path:
        """Directory containing an animation's anim.yaml and frame files --
        needed by animation.py to resolve relative frame filenames."""
        story_path = self.story_dir / "assets" / "animations" / animation_name
        if (story_path / "anim.yaml").exists():
            return story_path
        return self.shared_dir / "animations" / animation_name

    def load_project(self) -> Any:
        """Lazily load the headless Story/Core project for this story.

        This is an additive migration seam only: existing ``load_*`` methods
        continue to read and return their current cached mutable YAML objects.
        It uses the same source/path implementation with an independent cache,
        so runtime mutations of legacy cached mappings cannot become authored
        Story/Core definitions.  Existing loader object identity and error
        behavior remain unchanged.
        """
        if self._project is None:
            # Imports are local to keep current runtime startup independent of
            # the new core until a caller explicitly asks for this bridge.
            from engine.story_core.project import load_story_project
            from engine.story_core.source import StorySource

            # ``self._source`` intentionally shares ``self._cache`` with the
            # old mutable API.  A project needs its own source/cache before it
            # freezes definitions so legacy caller mutations cannot leak into
            # a designer/editor snapshot.
            project_source = StorySource(self.story_dir, self.shared_dir)
            self._project = load_story_project(self.story_dir, self.shared_dir, source=project_source)
        return self._project
