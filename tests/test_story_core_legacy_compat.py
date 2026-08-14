from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from engine.core.asset_loader import AssetLoader
from engine.core.inventory import InventoryService
from engine.errors import AssetNotFoundError, StoryValidationError
from engine.events.random_events import maybe_trigger
from engine.battle.config import load_battle_config
from engine.battle.move_progression import resolve_combat_move
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

    first_pool = view.load_event_pool("intro")
    first_pool["events"][0]["id"] = "caller mutation"
    second_pool = view.load_event_pool("intro")
    assert second_pool["events"][0]["id"] == "ending"
    assert project.event_pool("intro").event_ids == ("ending",)

    first_scene = view.load_scene("intro")
    first_scene["text"] = "caller mutation"
    second_scene = view.load_scene("intro")
    assert second_scene["text"] == "Welcome."
    assert project.scene("intro").text == "Welcome."


def test_legacy_item_registry_is_fresh_and_isolated_from_project(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    project = load_story_project(story_root, shared_root)
    view = LegacyProjectView(project)
    loader = AssetLoader(str(story_root), str(shared_root))

    runtime_items = view.load_items()
    assert runtime_items == loader.load_items()
    runtime_items["intro"]["future_item_extension"]["preserves"] = False
    runtime_items["runtime_only"] = {"name": "Runtime only"}

    assert view.load_items()["intro"]["future_item_extension"]["preserves"] is True
    assert "runtime_only" not in view.load_items()
    assert project.item("intro").to_mapping()["future_item_extension"]["preserves"] is True


@pytest.mark.parametrize(
    "root",
    [
        "moves:\n  - id: intro\n    name: Intro Strike\n    qte: {type: precision_bar}\n",
        "id: intro\nname: Intro Strike\nqte: {type: precision_bar}\n",
        "- id: intro\n  name: Intro Strike\n  qte: {type: precision_bar}\n",
    ],
)
def test_project_move_view_preserves_supported_global_root_forms(tmp_path: Path, root: str) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    (story_root / "moves" / "moves.yaml").write_text(root, encoding="utf-8")

    project = load_story_project(story_root, shared_root)
    view = project.legacy_view()
    loader = AssetLoader(str(story_root), str(shared_root))

    assert view.load_combat_move_config() == loader.load_combat_move_config()
    assert view.load_moves() == loader.load_moves()


def test_project_move_view_preserves_deep_merge_and_battle_move_consumers(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    (story_root / "moves" / "moves.yaml").write_text(
        "skill_progression: {evaluation_attempts: 1, promotion_average: 2.5, demotion_average: 1.5, minimum_level: 1}\n"
        "moves:\n"
        "  - id: intro\n"
        "    name: Intro Strike\n"
        "    common:\n"
        "      base_power: 4\n"
        "      qte: {type: precision_bar, parameters: {target_position: 0.5, weak_window: 0.2}}\n"
        "    difficulty_levels:\n"
        "      0: {qte: {parameters: {weak_window: 0.3}}}\n"
        "      1: {qte: {parameters: {weak_window: 0.1}}}\n",
        encoding="utf-8",
    )
    project = load_story_project(story_root, shared_root)
    view = project.legacy_view()
    loader = AssetLoader(str(story_root), str(shared_root))

    project_config = view.load_combat_move_config()
    loader_config = loader.load_combat_move_config()
    assert project_config == loader_config
    assert resolve_combat_move(project_config["moves"][0], 1) == resolve_combat_move(loader_config["moves"][0], 1)

    battle_data = deepcopy(loader.load_battle("intro"))
    battle_data["enemy_patterns"] = [{"id": "wait", "duration": 0, "timeline": []}]
    battle_data["enemy_moves"] = [{"id": "wait", "name": "Wait", "pattern": "wait"}]
    battle = load_battle_config(battle_data, view.load_items(), "battles/intro.yaml", project_config)
    assert battle.player_moves["intro"] == project_config["moves"][0]
    assert battle.skill_progression.evaluation_attempts == 1


def test_project_item_registry_preserves_inventory_normalization_parity(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    (story_root / "items" / "items.yaml").write_text(
        "canonical_weapon:\n"
        "  name: Canonical Weapon\n"
        "  type: weapon\n"
        "  description: A current-schema weapon.\n"
        "  stats: {hp: 2, attack: 5, defense: 1}\n"
        "  equipment_slot: weapon\n"
        "  actions: [equip, toss]\n"
        "legacy_ration:\n"
        "  name: Legacy Ration\n"
        "  type: consumable\n"
        "  equipment: {bonuses: {max_hp: 4, attack: 2}}\n"
        "  combat:\n"
        "    usable: true\n"
        "    effects: [{heal: 7}]\n",
        encoding="utf-8",
    )
    project = load_story_project(story_root, shared_root)
    view = LegacyProjectView(project)
    loader = AssetLoader(str(story_root), str(shared_root))

    project_items = InventoryService(view.load_items()).definitions
    loader_items = InventoryService(loader.load_items()).definitions

    assert project_items == loader_items


@pytest.mark.parametrize("story_name", ["demo_story", "mechanics_lab"])
def test_shipped_story_item_registry_matches_asset_loader(story_name: str) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    story_root = repository_root / "stories" / story_name
    shared_root = repository_root / "shared_assets"

    project = load_story_project(story_root, shared_root)
    loader = AssetLoader(str(story_root), str(shared_root))

    assert project.legacy_view().load_items() == loader.load_items()


def test_legacy_battle_view_matches_loader_and_isolates_runtime_mutations(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    project = load_story_project(story_root, shared_root)
    view = LegacyProjectView(project)
    loader = AssetLoader(str(story_root), str(shared_root))

    assert view.load_battle("intro") == loader.load_battle("intro")

    runtime_battle = view.load_battle("intro")
    runtime_battle["enemy"]["name"] = "Runtime-only enemy"
    runtime_battle["enemy"]["moves"][0]["damage"] = [999, 999]
    fresh_battle = view.load_battle("intro")
    assert fresh_battle["enemy"]["name"] == "Training Dummy"
    assert fresh_battle["enemy"]["moves"][0]["damage"] == [0, 0]
    assert project.battle("intro").enemy["name"] == "Training Dummy"


def test_legacy_battle_view_preserves_missing_battle_error(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    view = LegacyProjectView(load_story_project(story_root, shared_root))

    with pytest.raises(AssetNotFoundError):
        view.load_battle("missing")


def test_legacy_event_pool_view_preserves_missing_pool_error(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    view = LegacyProjectView(load_story_project(story_root, shared_root))

    with pytest.raises(AssetNotFoundError):
        view.load_event_pool("missing")


def test_project_event_pool_view_preserves_weighted_selection_parity(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    (story_root / "events" / "intro.yaml").write_text(
        "chance: 0.5\n"
        "events:\n"
        "  - id: ending\n"
        "    weight: 70\n"
        "  - id: intro\n"
        "    weight: 30\n",
        encoding="utf-8",
    )

    project = load_story_project(story_root, shared_root)
    view = LegacyProjectView(project)
    loader = AssetLoader(str(story_root), str(shared_root))

    assert maybe_trigger(loader.load_event_pool("intro"), rng=iter((0.0, 0.99)).__next__) == "intro"
    assert maybe_trigger(view.load_event_pool("intro"), rng=iter((0.0, 0.99)).__next__) == "intro"


def test_asset_loader_can_construct_the_noninvasive_core_bridge(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    loader = AssetLoader(str(story_root), str(shared_root))

    project = loader.load_project()

    assert project.manifest.id == "fixture_story"
    assert project.scene("intro").to_mapping() == loader.load_scene("intro")
    assert loader.load_project() is project


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


def test_legacy_scene_view_preserves_recursive_id_and_missing_scene_behavior(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    nested = story_root / "scenes" / "nested" / "surprise.yaml"
    nested.parent.mkdir(parents=True)
    nested.write_text("id: surprise\ntext: Found\n", encoding="utf-8")
    (story_root / "scenes" / "declared_mismatch.yaml").write_text(
        "id: another_scene\ntext: Invalid\n",
        encoding="utf-8",
    )

    project = load_story_project(story_root, shared_root)
    view = LegacyProjectView(project)

    assert view.load_scene("surprise")["text"] == "Found"
    with pytest.raises(AssetNotFoundError):
        view.load_scene("missing")
    with pytest.raises(StoryValidationError):
        view.load_scene("nested/surprise")
    with pytest.raises(AssetNotFoundError):
        view.load_scene("declared_mismatch")
