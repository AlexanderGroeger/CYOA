from engine.battle.config import load_battle_config
from engine.battle.controller import BattleController, BattleState
from engine.battle.defense import BattleInput, DefenseSequence
from engine.core.asset_loader import AssetLoader
from engine.core.game_state import GameState
from engine.core.story_interpreter import StoryInterpreter
from pathlib import Path


def _lab():
    assets = AssetLoader("stories/mechanics_lab")
    state = GameState.new_from_manifest(assets.load_manifest(), assets.load_player())
    config = load_battle_config(
        assets.load_battle("lab_sequence"), assets.load_items(), "lab_sequence.yaml", assets.load_combat_move_config()
    )
    return assets, state, config


def test_mechanics_lab_starts_at_the_feature_menu():
    assets, state, _ = _lab()
    assert assets.load_manifest()["default_scene_background"] == "repeating_cubes.png"
    scene, _ = StoryInterpreter(assets, state).enter_scene()
    assert scene["id"] == "lab_menu"
    assert [choice["text"] for choice in scene["choices"]] == [
        "Player attacks", "Enemy attacks / defend patterns", "Inventory & equipment", "Dialogue & presentation"
    ]
    assert scene["checkpoint"] is True


def test_mechanics_lab_direct_sequences_report_their_result():
    assets, state, config = _lab()
    attack = BattleController(config, state, assets.load_items())
    assert attack.start_test_sequence({"mode": "player_attack", "move": "poised_slash", "difficulty": 1})
    attack.update(3.0)
    assert attack.state is BattleState.VICTORY
    assert attack.test_result == {
        "mode": "player_attack", "subject": "Poised Slash", "difficulty": 1,
        "performance": "Miss", "damage": 0, "damage_taken": 0,
    }

    defense = BattleController(config, state, assets.load_items())
    assert defense.start_test_sequence({"mode": "enemy_attack", "move": "aimed_stream", "difficulty": 0})
    for _ in range(100):
        defense.update(0.1)
        if defense.outcome:
            break
    assert defense.outcome == "win"
    assert defense.test_result and defense.test_result["damage_taken"] >= 0


def test_mechanics_lab_defense_sequences_never_restore_player_hp():
    assets, state, config = _lab()
    state.set_stat("hp", 23)
    battle = BattleController(config, state, assets.load_items())

    assert battle.start_test_sequence({"mode": "enemy_attack", "move": "aimed_stream", "difficulty": 0})
    assert state.get_stat("hp") == 23

    battle.state = BattleState.VICTORY
    assert battle.handle_action("SELECT")
    assert state.get_stat("hp") == 23


def test_mechanics_lab_defense_patterns_allow_one_second_of_safe_movement():
    assets, _, config = _lab()
    for move in config.enemy_moves.values():
        sequence = DefenseSequence(config.enemy_patterns[move["pattern"]], config.arena)
        starting_x = sequence.player_x
        result = sequence.update(0.99, BattleInput(move_x=1.0), lambda damage: None)
        assert not sequence.projectiles
        assert sequence.player_x > starting_x
        assert not result.completed
        sequence.update(0.01, BattleInput(), lambda damage: None)
        assert sequence.elapsed == 1.0


def test_mechanics_lab_defense_victory_can_repeat_or_return_to_difficulty_menu():
    assets, state, config = _lab()
    battle = BattleController(config, state, assets.load_items())
    assert battle.start_test_sequence({"mode": "enemy_attack", "move": "aimed_stream", "difficulty": 0})
    for _ in range(100):
        battle.update(0.1)
        if battle.state is BattleState.VICTORY:
            break
    assert battle.state is BattleState.VICTORY
    assert battle.test_sequence_victory
    assert battle.handle_action("SELECT")
    assert battle.state is BattleState.ENEMY_TELEGRAPH

    for _ in range(100):
        battle.update(0.1)
        if battle.state is BattleState.VICTORY:
            break
    assert battle.state is BattleState.VICTORY
    assert battle.handle_action("BACK")
    assert battle.finished


def test_mechanics_lab_defense_victories_return_to_their_full_difficulty_menu():
    assets, _, _ = _lab()
    for path in Path("stories/mechanics_lab/scenes").glob("defend_*.yaml"):
        scene = assets.load_scene(path.stem)
        assert len(scene["choices"]) == 4
        assert all(choice["on_win"] == scene["id"] for choice in scene["choices"])


def test_mechanics_lab_defense_deaths_use_the_battle_game_over_sequence():
    assets, _, config = _lab()
    assert config.on_lose is not None
    assert config.on_lose.type == "game_over"
    assert config.on_lose.game_over is not None
    assert config.on_lose.game_over.music == "game_over.ogg"
    for path in Path("stories/mechanics_lab/scenes").glob("defend_*.yaml"):
        scene = assets.load_scene(path.stem)
        assert all("on_lose" not in choice for choice in scene["choices"])


def test_every_mechanics_lab_attack_victory_can_repeat_with_select_or_return_with_back():
    assets, state, config = _lab()
    battle = BattleController(config, state, assets.load_items())
    assert battle.start_test_sequence({"mode": "player_attack", "move": "poised_slash", "difficulty": 0})
    battle.update(3.0)
    assert battle.state is BattleState.VICTORY
    assert battle.test_attack_victory
    assert battle.handle_action("SELECT")
    assert battle.state is BattleState.PLAYER_ATTACK
    assert battle.test_result is None

    normal = BattleController(config, state, assets.load_items())
    assert normal.start_test_sequence({"mode": "player_attack", "move": "poised_slash", "difficulty": 1})
    normal.update(3.0)
    assert normal.test_attack_victory
    assert normal.handle_action("SELECT")
    assert normal.state is BattleState.PLAYER_ATTACK

    returned = BattleController(config, state, assets.load_items())
    assert returned.start_test_sequence({"mode": "player_attack", "move": "poised_slash", "difficulty": 1})
    returned.update(3.0)
    assert returned.handle_action("BACK")
    assert returned.finished
