from __future__ import annotations

from pathlib import Path

from engine.core import game_engine as game_engine_module
from engine.core.game_engine import GameEngine
from engine.core.story_interpreter import Transition
from story_core_fixture import write_fixture_story


class _Renderer:
    def __init__(self, assets, display_config, render_config):
        self.assets = assets
        self.config = display_config
        self.render_config = render_config


class _Audio:
    def __init__(self, assets, **kwargs):
        self.assets = assets
        self.options = kwargs

    def preload_sfx(self, filename: str) -> None:
        pass


def test_game_engine_owns_one_core_project_and_preserves_legacy_startup_views(
    tmp_path: Path, monkeypatch
) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    monkeypatch.setattr(game_engine_module, "Renderer", _Renderer)
    monkeypatch.setattr(game_engine_module, "AudioSystem", _Audio)

    engine = GameEngine(str(story_root), str(shared_root))

    assert engine.story_project is engine.assets.load_project()
    assert engine.story_project_diagnostics.has_errors is False
    assert engine.story_project.manifest.to_mapping() == engine.manifest
    assert engine.story_project.player_profile.to_mapping() == engine.player_profile
    assert engine.story_project.audio_config == engine.audio_preferences
    assert engine.story_project.item("intro").to_mapping() == engine.items["intro"]
    assert engine.story_project.combat_move_config == engine.combat_move_config
    assert engine.story_view.project is engine.story_project
    assert engine.interpreter.project_view is engine.story_view

    # The legacy cache remains the runtime-facing mutable contract, while the
    # project snapshot remains isolated from those mutations.
    engine.assets.load_scene("intro")["text"] = "runtime mutation"
    assert engine.assets.load_scene("intro")["text"] == "runtime mutation"
    assert engine.story_project.scene("intro").text == "Welcome."
    assert engine.interpreter.project_view.project is engine.story_project

    # Scene lookup in the live interpreter is now project-backed, not a
    # second read from AssetLoader's mutable scene cache.
    def fail_legacy_scene(_scene_id):
        raise AssertionError("legacy scene cache used")

    monkeypatch.setattr(engine.assets, "load_scene", fail_legacy_scene)
    scene, _ = engine.interpreter.enter_scene("intro")
    assert scene["text"] == "Welcome."


def test_game_engine_item_startup_uses_project_registry_not_asset_loader(
    tmp_path: Path, monkeypatch
) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    monkeypatch.setattr(game_engine_module, "Renderer", _Renderer)
    monkeypatch.setattr(game_engine_module, "AudioSystem", _Audio)

    def fail_legacy_items(_loader):
        raise AssertionError("normal startup used AssetLoader.load_items")

    monkeypatch.setattr(game_engine_module.AssetLoader, "load_items", fail_legacy_items)

    engine = GameEngine(str(story_root), str(shared_root))

    assert engine.items == engine.story_view.load_items()
    assert engine.items == {"intro": engine.story_project.item("intro").to_mapping()}
    assert engine.inventory.definition("intro").name == "Intro Token"
    engine.items["intro"]["name"] = "Runtime-only item"
    assert engine.story_project.item("intro").name == "Intro Token"


def test_game_engine_move_startup_uses_project_registry_not_asset_loader(
    tmp_path: Path, monkeypatch
) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    monkeypatch.setattr(game_engine_module, "Renderer", _Renderer)
    monkeypatch.setattr(game_engine_module, "AudioSystem", _Audio)

    def fail_legacy_moves(_loader):
        raise AssertionError("normal startup used AssetLoader.load_combat_move_config")

    monkeypatch.setattr(game_engine_module.AssetLoader, "load_combat_move_config", fail_legacy_moves)

    engine = GameEngine(str(story_root), str(shared_root))

    assert engine.combat_move_config == engine.story_view.load_combat_move_config()
    assert engine.moves == engine.combat_move_config["moves"]
    engine.combat_move_config["moves"][0]["future_move_extension"]["preserves"] = False
    assert engine.story_project.move("intro").raw["future_move_extension"]["preserves"] is True


def test_game_engine_battle_uses_shared_project_item_registry(
    tmp_path: Path, monkeypatch
) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    monkeypatch.setattr(game_engine_module, "Renderer", _Renderer)
    monkeypatch.setattr(game_engine_module, "AudioSystem", _Audio)
    engine = GameEngine(str(story_root), str(shared_root))

    def fail_legacy_items(_loader):
        raise AssertionError("battle startup used AssetLoader.load_items")

    monkeypatch.setattr(engine.assets, "load_items", fail_legacy_items)
    engine._start_battle(Transition(kind="battle", battle_id="intro"))

    assert engine.battle is not None
    assert engine.battle.items is engine.items


def test_game_engine_scene_orchestration_uses_shared_project_view_and_isolated_mapping(
    tmp_path: Path, monkeypatch
) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    monkeypatch.setattr(game_engine_module, "Renderer", _Renderer)
    monkeypatch.setattr(game_engine_module, "AudioSystem", _Audio)

    engine = GameEngine(str(story_root), str(shared_root))
    expected = engine.story_project.scene("intro").to_mapping()

    def fail_legacy_scene(_scene_id):
        raise AssertionError("GameEngine scene orchestration used AssetLoader.load_scene")

    monkeypatch.setattr(engine.assets, "load_scene", fail_legacy_scene)
    monkeypatch.setattr(engine.renderer, "paginate_text", lambda text, font_size: [text], raising=False)
    monkeypatch.setattr(engine, "_reset_dialogue_animation", lambda: None)
    monkeypatch.setattr(engine, "_render", lambda: None)

    engine._enter_scene("intro")

    assert engine.scene["text"] == expected["text"]
    assert engine.scene["choices"] == expected["choices"]
    engine.scene["text"] = "runtime mutation"
    assert engine.story_project.scene("intro").text == "Welcome."


def test_game_engine_random_events_use_shared_project_view_and_preserve_transition(
    tmp_path: Path, monkeypatch
) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    monkeypatch.setattr(game_engine_module, "Renderer", _Renderer)
    monkeypatch.setattr(game_engine_module, "AudioSystem", _Audio)

    engine = GameEngine(str(story_root), str(shared_root))
    entered_scenes: list[str] = []
    monkeypatch.setattr(engine, "_enter_scene", entered_scenes.append)

    def fail_legacy_event_pool(_pool_id: str):
        raise AssertionError("normal random-event resolution used AssetLoader.load_event_pool")

    monkeypatch.setattr(engine.assets, "load_event_pool", fail_legacy_event_pool)

    assert engine._run_random_event("intro") is True
    assert entered_scenes == ["ending"]

    runtime_pool = engine.story_view.load_event_pool("intro")
    runtime_pool["events"][0]["id"] = "runtime-only"
    assert engine.story_project.event_pool("intro").event_ids == ("ending",)


def test_game_engine_battle_start_uses_project_view_and_existing_config_path(
    tmp_path: Path, monkeypatch
) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    monkeypatch.setattr(game_engine_module, "Renderer", _Renderer)
    monkeypatch.setattr(game_engine_module, "AudioSystem", _Audio)

    engine = GameEngine(str(story_root), str(shared_root))

    def fail_legacy_battle(_battle_id: str):
        raise AssertionError("normal battle startup used AssetLoader.load_battle")

    monkeypatch.setattr(engine.assets, "load_battle", fail_legacy_battle)
    engine._start_battle(Transition(kind="battle", battle_id="intro"))

    assert engine.battle is not None
    assert engine.battle.config.legacy is True
    assert engine.story_project.battle("intro").enemy["name"] == "Training Dummy"
