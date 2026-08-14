"""Headless checks of the demo story's pygame-facing configuration."""

from pathlib import Path
from copy import deepcopy

import pytest

from engine.core.asset_loader import AssetLoader
from engine.core.game_state import GameState
from engine.core.story_interpreter import StoryInterpreter
from engine.battle.config import load_battle_config
from engine.battle.controller import BattleController
from engine.errors import BattleConfigError
from engine.render.display import parse_display_config


DEMO_STORY = Path(__file__).resolve().parent.parent / "stories" / "demo_story"


def test_demo_story_has_a_valid_logical_canvas_and_start_scene():
    assets = AssetLoader(str(DEMO_STORY), "shared_assets")
    manifest = assets.load_manifest()
    display = parse_display_config(manifest)
    assert display.width > 0 and display.height > 0
    state = GameState.new_from_manifest(manifest, assets.load_player())
    scene, _ = StoryInterpreter(assets, state).enter_scene()
    assert scene["id"] == manifest["start_scene"]


def test_demo_battles_load_as_modern_and_legacy_configurations():
    assets = AssetLoader(str(DEMO_STORY), "shared_assets")
    modern = load_battle_config(assets.load_battle("wolf_fight"), assets.load_items(), "wolf_fight.yaml", assets.load_moves())
    legacy = load_battle_config(assets.load_battle("deer_fight"), assets.load_items(), "deer_fight.yaml", assets.load_moves())
    assert modern.legacy is False and len(modern.enemy_patterns) >= 2
    assert modern.enemy.sprite == "wolf.png"
    assert modern.initial_enemy_moves == ["shard_fall", "echo_ring"]
    assert modern.enemy_moves["moonlit_hunt"]["pattern"] == "moonlit_hunt"
    assert [pattern["type"] for pattern in modern.enemy_patterns["moonlit_hunt"]["patterns"]] == [
        "predictive_stream", "moving_gap_wall"
    ]
    desperate_phase = next(phase for phase in modern.phases if phase["id"] == "desperate_wolf")
    assert {"add_enemy_move": "moonlit_hunt"} in desperate_phase["actions"]
    assert any(entry.get("type") == "opponent" for entry in modern.dialogue)
    assert next(entry for entry in modern.dialogue if entry.get("type") == "opponent")["pause"] == 2.25
    environment_descriptions = [entry for entry in modern.dialogue if entry.get("type") == "environment"]
    assert {entry["trigger"] for entry in environment_descriptions} == {"battle_start", "turn_start"}
    assert any(entry.get("when", {}).get("phase") == "desperate_wolf" for entry in environment_descriptions)
    assert legacy.legacy is True


def test_demo_player_moves_require_both_learning_and_weapon_permission():
    assets = AssetLoader(str(DEMO_STORY), "shared_assets")
    state = GameState.new_from_manifest(assets.load_manifest(), assets.load_player())
    config = load_battle_config(assets.load_battle("wolf_fight"), assets.load_items(), "wolf_fight.yaml", assets.load_moves())
    battle = BattleController(config, state, assets.load_items())
    weapon = state.get_equipped("weapon")
    expected = [move_id for move_id in state.known_moves if move_id in assets.load_items()[weapon]["combat"]["move_grants"]]
    assert battle.available_player_moves() == expected

    state.equip_item("weapon", "hunter_bow")
    state.forget_move("hunter_shot")
    assert battle.available_player_moves() == []  # The player has not learned Hunter Shot.
    state.learn_move("hunter_shot")
    assert battle.available_player_moves() == ["hunter_shot"]


def test_global_move_loading_rejects_encounter_owned_player_moves():
    assets = AssetLoader(str(DEMO_STORY), "shared_assets")
    battle = deepcopy(assets.load_battle("wolf_fight"))
    battle["player_moves"] = []
    with pytest.raises(BattleConfigError, match="moves/ folder"):
        load_battle_config(battle, assets.load_items(), "wolf_fight.yaml", assets.load_moves())
