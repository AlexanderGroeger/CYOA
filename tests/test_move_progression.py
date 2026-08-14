"""Coverage for resolved move difficulty, adaptive skill, and persistence."""

from __future__ import annotations

from pathlib import Path
import random

import pytest

from engine.battle.config import load_battle_config
from engine.battle.controller import BattleController, BattleState
from engine.battle.move_progression import (
    CombatMoveSkillTracker,
    SkillProgressionConfig,
    highest_normal_difficulty,
    normal_difficulty_levels,
    resolve_combat_move,
    result_score,
)
from engine.core.asset_loader import AssetLoader
from engine.core.game_state import GameState
from engine.errors import BattleConfigError
from engine.save.save_system import load_game, save_game
from engine.battle.qte import create_attack_qte


def move_definition(move_id: str = "jab", *, tutorial: bool = True) -> dict:
    levels = {
        1: {"qte": {"duration": 1.0, "parameters": {"critical_window": .05, "strong_window": .10, "weak_window": .20}}},
        2: {"qte": {"duration": .8, "parameters": {"critical_window": .03, "strong_window": .07, "weak_window": .15}}},
        3: {"qte": {"duration": .6, "parameters": {"critical_window": .02, "strong_window": .05, "weak_window": .10}}},
    }
    if tutorial:
        levels[0] = {"qte": {"duration": 1.5, "parameters": {"critical_window": .08, "strong_window": .15, "weak_window": .28}}}
    return {
        "id": move_id,
        "name": move_id.title(),
        "common": {
            "base_power": 3,
            "qte": {"type": "precision_bar", "parameters": {"target_position": .5}},
        },
        "difficulty_levels": levels,
    }


def tracker(state: GameState | None = None, *, attempts: int = 2,
            levels: dict[str, tuple[int, ...]] | None = None) -> CombatMoveSkillTracker:
    state = state or GameState(known_moves=["jab", "kick"])
    return CombatMoveSkillTracker(
        state,
        SkillProgressionConfig(evaluation_attempts=attempts, promotion_average=2.5, demotion_average=1.5),
        levels or {"jab": (1, 2, 3), "kick": (1, 2, 3)},
    )


def test_resolver_deep_merges_common_and_every_level_including_tutorial():
    move = move_definition()
    level_one = resolve_combat_move(move, 1)
    level_three = resolve_combat_move(move, 3)
    tutorial = resolve_combat_move(move, 0)

    assert level_one["base_power"] == 3
    assert level_one["qte"]["type"] == "precision_bar"
    assert level_one["qte"]["parameters"]["target_position"] == .5
    assert level_three["qte"]["duration"] == .6
    assert tutorial["qte"]["duration"] == 1.5
    assert normal_difficulty_levels(move) == (1, 2, 3)
    assert highest_normal_difficulty(move) == 3


@pytest.mark.parametrize("bad, message", [
    ({"id": "bad", "difficulty_levels": {1: {}}}, "common must be a mapping"),
    ({"id": "bad", "common": {}, "difficulty_levels": {0: {}}}, "difficulty level 1 is required"),
    ({"id": "bad", "common": {}, "difficulty_levels": {1: "nope"}}, "difficulty level 1 must be a mapping"),
])
def test_invalid_difficulty_data_reports_the_move_level_and_field(bad, message):
    with pytest.raises(BattleConfigError, match=message):
        normal_difficulty_levels(bad)

    with pytest.raises(BattleConfigError, match="difficulty level 9"):
        resolve_combat_move(move_definition(), 9)


def test_result_names_have_the_fixed_skill_scores():
    assert [result_score(name) for name in ("miss", "weak", "strong", "critical")] == [0, 1, 2, 3]


def test_promotion_at_exact_threshold_clears_history_and_changes_one_level():
    state = GameState(known_moves=["jab"])
    skills = tracker(state, attempts=2, levels={"jab": (1, 2, 3)})
    skills.record_result("jab", "strong")
    changed = skills.record_result("jab", "critical")  # mean (2 + 3) / 2 = 2.5

    assert changed.current_level == 2
    assert changed.recent_scores == []
    assert skills.current_level("jab") == 2


def test_demotion_boundaries_and_history_rules():
    state = GameState(known_moves=["jab"], known_combat_moves={"jab": {"current_level": 2, "recent_scores": []}})
    skills = tracker(state, attempts=2, levels={"jab": (1, 2, 3)})
    skills.record_result("jab", "weak")
    demoted = skills.record_result("jab", "weak")  # 1.0, below 1.5
    assert (demoted.current_level, demoted.recent_scores) == (1, [])

    skills.record_result("jab", "weak")
    at_boundary = skills.record_result("jab", "weak")
    assert at_boundary.current_level == 1
    assert at_boundary.recent_scores == [1, 1]  # Cannot demote, so preserve evidence.

    state.known_combat_moves["jab"] = {"current_level": 2, "recent_scores": []}
    skills = tracker(state, attempts=2, levels={"jab": (1, 2, 3)})
    skills.record_result("jab", "weak")
    exact = skills.record_result("jab", "strong")  # mean 1.5: no demotion
    assert (exact.current_level, exact.recent_scores) == (2, [1, 2])


def test_history_window_is_bounded_and_not_evaluated_early():
    state = GameState(known_moves=["jab"])
    skills = tracker(state, attempts=3, levels={"jab": (1, 2, 3)})
    assert skills.record_result("jab", "critical").current_level == 1
    assert skills.record_result("jab", "critical").recent_scores == [3, 3]
    assert skills.record_result("jab", "critical").current_level == 2

    # The previous promotion cleared history; this checks trimming on a
    # boundary where promotion cannot change level.
    state.known_combat_moves["jab"] = {"current_level": 3, "recent_scores": [3, 3, 3, 3]}
    skills = tracker(state, attempts=3, levels={"jab": (1, 2, 3)})
    assert skills.state_for("jab").recent_scores == [3, 3, 3]


def test_moves_are_isolated_and_tutorial_scores_are_excluded():
    state = GameState(known_moves=["jab", "kick"])
    skills = tracker(state, attempts=2)
    skills.record_result("jab", "critical")
    skills.record_result("jab", "critical")
    skills.record_result("kick", "weak")
    skills.record_result("jab", "critical", tutorial=True)

    assert skills.current_level("jab") == 2
    assert skills.state_for("jab").recent_scores == []
    assert skills.state_for("kick").recent_scores == [1]


def test_save_round_trip_migrates_and_repairs_skill_data(tmp_path: Path):
    state = GameState(known_moves=["jab"], known_combat_moves={"jab": {"current_level": 2, "recent_scores": [1, 2, 3]}})
    save_game(state, "story", "1", tmp_path, "slot")
    loaded = load_game(tmp_path, "slot", "story")
    assert loaded.known_combat_moves == {"jab": {"current_level": 2, "recent_scores": [1, 2, 3]}}
    skills = tracker(loaded, attempts=2, levels={"jab": (1, 2, 3)})
    assert skills.state_for("jab").current_level == 2
    assert skills.state_for("jab").recent_scores == [2, 3]

    legacy = GameState.from_dict({"known_moves": ["jab"]})
    assert tracker(legacy, attempts=2, levels={"jab": (1, 2, 3)}).state_for("jab").to_dict() == {
        "current_level": 1, "recent_scores": [],
    }
    malformed = GameState(known_moves=["jab"], known_combat_moves={
        "jab": {"current_level": 99, "recent_scores": [3, "bad"]}, "removed_move": {"current_level": 1, "recent_scores": []},
    })
    repaired = tracker(malformed, attempts=2, levels={"jab": (1, 2, 3)})
    assert repaired.state_for("jab").to_dict() == {"current_level": 1, "recent_scores": []}
    assert "removed_move" not in malformed.known_combat_moves


def _battle_config() -> dict:
    return {
        "id": "skill_test",
        "enemy": {"name": "Dummy", "hp": 100},
        "player_moves": [move_definition()],
        "initial_player_moves": ["jab"],
        "enemy_patterns": [{"id": "wait", "duration": 0, "timeline": []}],
        "enemy_moves": [{"id": "wait", "name": "Wait", "pattern": "wait"}],
    }


def test_controller_snapshots_level_records_once_and_keeps_tutorial_separate():
    state = GameState(stats={"hp": 5, "max_hp": 5, "attack": 5}, known_moves=["jab"])
    config = load_battle_config(_battle_config(), source="skill-test.yaml", skill_progression={"evaluation_attempts": 1})
    battle = BattleController(config, state, rng=random.Random(1))
    battle.update(0)
    battle.handle_action("SELECT")
    battle.handle_action("SELECT")
    assert battle.state == BattleState.PLAYER_ATTACK
    assert battle.active_player_level == 1
    assert battle.active_attack.duration == 1.0

    battle.active_attack.elapsed = battle.active_attack.target_cross_time
    battle.handle_action("SELECT")
    assert state.known_combat_moves["jab"] == {"current_level": 2, "recent_scores": []}
    # Calling the resolve path again cannot double-record the same QTE.
    battle._resolve_player_attack()
    assert state.known_combat_moves["jab"] == {"current_level": 2, "recent_scores": []}

    tutorial = BattleController(config, state, rng=random.Random(1))
    assert tutorial.start_move_tutorial("jab")
    assert tutorial.active_player_level == 0
    assert tutorial.active_attack.duration == 1.5
    tutorial.active_attack.elapsed = tutorial.active_attack.target_cross_time
    tutorial.handle_action("SELECT")
    assert state.known_combat_moves["jab"] == {"current_level": 2, "recent_scores": []}


def test_every_demo_move_has_three_meaningfully_distinct_normal_levels():
    assets = AssetLoader(str(Path(__file__).resolve().parents[1] / "stories" / "demo_story"), "shared_assets")
    for move in assets.load_moves():
        assert normal_difficulty_levels(move) == (1, 2, 3)
        level_one = resolve_combat_move(move, 1)["qte"]
        level_three = resolve_combat_move(move, 3)["qte"]
        assert level_one != level_three, move["id"]


def test_hunter_shot_tunes_critical_first_and_preserves_more_of_the_weak_band():
    assets = AssetLoader(str(Path(__file__).resolve().parents[1] / "stories" / "demo_story"), "shared_assets")
    hunter = next(move for move in assets.load_moves() if move["id"] == "hunter_shot")
    first = resolve_combat_move(hunter, 1)["qte"]["parameters"]
    third = resolve_combat_move(hunter, 3)["qte"]["parameters"]

    assert third["critical_radius"] < first["critical_radius"]
    assert third["strong_radius"] < first["strong_radius"]
    assert first["target_radius"] - third["target_radius"] < first["strong_radius"] - third["strong_radius"]
    assert 0 < third["critical_radius"] <= third["strong_radius"] <= third["target_radius"]


def test_pattern_randomness_is_stable_across_difficulties_but_not_authored_as_a_fixed_target():
    assets = AssetLoader(str(Path(__file__).resolve().parents[1] / "stories" / "demo_story"), "shared_assets")
    moves = {move["id"]: move for move in assets.load_moves()}
    poised = moves["poised_slash"]
    assert "target_position" not in poised["difficulty_levels"][1]["qte"]["tuning_parameters"]

    low = create_attack_qte(resolve_combat_move(poised, 1), random.Random(37))
    high = create_attack_qte(resolve_combat_move(poised, 3), random.Random(37))
    assert low.target_position == high.target_position
    assert .42 <= low.target_position <= .75

    hunter = moves["hunter_shot"]
    low = create_attack_qte(resolve_combat_move(hunter, 1), random.Random(8))
    high = create_attack_qte(resolve_combat_move(hunter, 3), random.Random(8))
    assert (low.target_y, low.launch_x) == (high.target_y, high.launch_x)
    assert low.target_radius > high.target_radius
