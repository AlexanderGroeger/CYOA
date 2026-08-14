from __future__ import annotations

from pathlib import Path

from engine.core.asset_loader import AssetLoader
from engine.story_core import LegacyProjectView, load_story_project
from story_core_fixture import write_fixture_story


def test_legacy_view_matches_loader_shapes_and_returns_fresh_mutable_copies(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    project = load_story_project(story_root, shared_root)
    view = LegacyProjectView(project)
    loader = AssetLoader(str(story_root), str(shared_root))

    assert view.load_manifest() == loader.load_manifest()
    assert view.load_player() == loader.load_player()
    assert view.load_audio_config() == loader.load_audio_config()
    assert view.load_items() == loader.load_items()
    assert view.load_moves() == loader.load_moves()
    assert view.load_combat_move_config() == loader.load_combat_move_config()
    assert view.load_scene("intro") == loader.load_scene("intro")
    assert view.load_battle("intro") == loader.load_battle("intro")
    assert view.load_event_pool("intro") == loader.load_event_pool("intro")
    assert view.load_animation("intro") == loader.load_animation("intro")

    first_scene = view.load_scene("intro")
    first_scene["text"] = "caller mutation"
    second_scene = view.load_scene("intro")
    assert second_scene["text"] == "Welcome."
    assert project.scene("intro").text == "Welcome."


def test_asset_loader_can_construct_the_noninvasive_core_bridge(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    loader = AssetLoader(str(story_root), str(shared_root))

    project = loader.load_project()

    assert project.manifest.id == "fixture_story"
    assert project.scene("intro").to_mapping() == loader.load_scene("intro")


def test_asset_loader_project_snapshot_isolated_from_mutable_legacy_cache(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    loader = AssetLoader(str(story_root), str(shared_root))

    # Existing loader consumers intentionally share this mutable cached graph.
    loader.load_scene("intro")["text"] = "Runtime-only mutation"
    assert loader.load_scene("intro")["text"] == "Runtime-only mutation"

    # The additive Core bridge must instead snapshot authored source through
    # its own source cache, otherwise a later designer serialization could
    # persist the runtime mutation.
    project = loader.load_project()
    assert project.scene("intro").text == "Welcome."
