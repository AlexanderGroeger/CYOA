import os

import pytest
import yaml

from engine.core.asset_loader import AssetLoader
from engine.errors import AssetNotFoundError, StoryValidationError


@pytest.fixture
def loader(tmp_path):
    story_dir = tmp_path / "story"
    shared_dir = tmp_path / "shared"
    (story_dir / "scenes").mkdir(parents=True)
    (story_dir / "moves").mkdir(parents=True)
    (story_dir / "assets" / "backgrounds").mkdir(parents=True)
    (shared_dir / "backgrounds").mkdir(parents=True)
    (shared_dir / "sprites").mkdir(parents=True)

    (story_dir / "story.yaml").write_text(yaml.dump({"title": "Test", "start_scene": "intro"}))
    (story_dir / "player.yaml").write_text(yaml.dump({"stats": {"hp": 9}, "known_moves": ["jab"]}))
    (story_dir / "moves" / "attacks.yaml").write_text(yaml.dump({"moves": [{"id": "jab", "base_power": 1, "pattern": "timing_bar", "pattern_config": {"duration": 1}}]}))
    (story_dir / "scenes" / "intro.yaml").write_text(yaml.dump({"id": "intro", "text": "hello"}))
    (story_dir / "assets" / "backgrounds" / "forest.txt").write_text("STORY-LOCAL FOREST ART")
    (shared_dir / "backgrounds" / "cave.txt").write_text("SHARED CAVE ART")
    (shared_dir / "sprites" / "wolf.txt").write_text("SHARED WOLF SPRITE")

    return AssetLoader(str(story_dir), str(shared_dir))


def test_load_manifest(loader):
    assert loader.load_manifest()["title"] == "Test"


def test_load_player_and_moves(loader):
    assert loader.load_player()["known_moves"] == ["jab"]
    assert loader.load_moves()[0]["id"] == "jab"


def test_load_scene(loader):
    assert loader.load_scene("intro")["text"] == "hello"


def test_story_local_asset_takes_precedence(loader):
    assert loader.load_text_asset("backgrounds", "forest.txt") == "STORY-LOCAL FOREST ART"


def test_shared_assets_fallback(loader):
    assert loader.load_text_asset("backgrounds", "cave.txt") == "SHARED CAVE ART"
    assert loader.load_text_asset("sprites", "wolf.txt") == "SHARED WOLF SPRITE"


def test_missing_asset_raises(loader):
    with pytest.raises(AssetNotFoundError):
        loader.load_text_asset("backgrounds", "nonexistent.txt")


def test_caching_survives_file_mutation(loader, tmp_path):
    loader.load_text_asset("backgrounds", "forest.txt")
    (tmp_path / "story" / "assets" / "backgrounds" / "forest.txt").write_text("MUTATED")
    assert loader.load_text_asset("backgrounds", "forest.txt") == "STORY-LOCAL FOREST ART"


def test_is_image_asset(loader):
    assert loader.is_image_asset("cave.png")
    assert not loader.is_image_asset("cave.txt")


def test_items_yaml_optional(loader):
    assert loader.load_items() == {}


def test_scene_id_mismatch_rejected(loader, tmp_path):
    (tmp_path / "story" / "scenes" / "bad.yaml").write_text(
        yaml.dump({"id": "totally_different", "text": "x"})
    )
    with pytest.raises(AssetNotFoundError):
        loader.load_scene("bad")


def test_scene_can_be_found_in_a_nested_folder(loader, tmp_path):
    nested = tmp_path / "story" / "scenes" / "events"
    nested.mkdir()
    (nested / "surprise.yaml").write_text(yaml.dump({"id": "surprise", "text": "Found"}))
    assert loader.load_scene("surprise")["text"] == "Found"


def test_duplicate_scene_filename_is_reported_as_ambiguous(loader, tmp_path):
    nested = tmp_path / "story" / "scenes" / "chapter_two"
    nested.mkdir()
    (tmp_path / "story" / "scenes" / "duplicate.yaml").write_text(yaml.dump({"id": "duplicate"}))
    (nested / "duplicate.yaml").write_text(yaml.dump({"id": "duplicate"}))
    with pytest.raises(StoryValidationError, match="ambiguous"):
        loader.load_scene("duplicate")


def test_explicit_story_relative_asset_reference_is_supported(loader, tmp_path):
    path = tmp_path / "story" / "assets" / "scenes" / "study"
    path.mkdir(parents=True)
    (path / "desk.png").write_bytes(b"not-rendered-in-this-test")

    assert loader.resolve_asset_reference("assets/scenes/study/desk.png", "sprites") == path / "desk.png"


def test_declared_item_icon_and_exploration_cross_references_validate_early(loader, tmp_path):
    story = tmp_path / "story"
    (story / "items").mkdir()
    (story / "items" / "items.yaml").write_text(yaml.dump({
        "bad_icon": {"name": "Bad", "icon": "missing.png", "actions": []},
    }))
    with pytest.raises(StoryValidationError, match="icon"):
        loader.load_items()

    # An exploration scene gets structural validation at scene load and its
    # destination is checked by the complete-story pass.
    (story / "scenes" / "explore.yaml").write_text(yaml.dump({
        "id": "explore",
        "exploration": {"navigation": [{"scene": "does_not_exist"}]},
    }))
    (story / "items" / "items.yaml").write_text(yaml.dump({}))
    loader._cache.clear()
    with pytest.raises(StoryValidationError, match="nonexistent scene"):
        loader.validate_exploration_content()
