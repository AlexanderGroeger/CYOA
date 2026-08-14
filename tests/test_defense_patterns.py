"""Headless coverage for the generic enemy-defense framework."""

from __future__ import annotations

import random
from pathlib import Path

import pytest
import yaml

from engine.battle.config import load_battle_config
from engine.battle.controller import BattleController, BattleState
from engine.battle.controls import BattleInput
from engine.battle.defense import (
    PATTERN_TYPES,
    DefenseConfigError,
    DefenseSequence,
    Projectile,
    apply_difficulty_overrides,
    compile_pattern_events,
    resolve_position,
    resolve_random_value,
    validate_defense_sequence,
)
from engine.errors import BattleConfigError
from engine.core.game_state import GameState


ARENA = {"x": 10, "y": 20, "width": 100, "height": 80, "player_speed": 100}


def make_sequence(data: dict) -> DefenseSequence:
    return DefenseSequence(data, ARENA, random.Random(9))


def advance(sequence: DefenseSequence, seconds: float, movement: BattleInput = BattleInput()) -> list[int]:
    hits: list[int] = []
    remaining = seconds
    while remaining > 1e-9:
        step = min(.05, remaining)
        sequence.update(step, movement, hits.append)
        remaining -= step
    return hits


def test_registry_covers_the_initial_public_pattern_library():
    required = {
        "aimed_stream", "predictive_stream", "radial_burst", "spiral", "gap_wall", "moving_gap_wall",
        "sweeping_beam", "lane_attack", "telegraph_strike", "falling_rain", "crossfire", "chaser",
        "mine", "expanding_ring", "contracting_ring", "bouncing_projectiles", "curving_projectiles",
        "accelerating_stream", "wave_stream", "orbiting_hazards", "shrinking_arena", "maze_corridor", "rhythm",
    }
    assert required <= set(PATTERN_TYPES)


def test_scheduler_spawns_at_the_exact_event_boundary_not_the_start_of_a_long_frame():
    defense = make_sequence({
        "duration": 2.0,
        "patterns": [{
            "type": "aimed_stream", "start": 1.0, "duration": .1,
            "origin": {"x": .5, "y": 0, "normalized": True}, "fire_interval": 1,
            "projectile": {"speed": 10, "radius": 2, "damage": 0, "lifetime": 4},
        }],
    })

    defense.update(1.5, BattleInput(), lambda _: None)

    assert len(defense.projectiles) == 1
    # Spawn point is y=20; it gets only the .5 seconds after t=1.0.
    assert defense.projectiles[0].y == pytest.approx(25.0)


def test_groups_and_repeats_flatten_to_independent_overlapping_pattern_events():
    data = {
        "duration": 8,
        "pattern_groups": {
            "pair": [
                {"type": "radial_burst", "start": 0, "projectile_count": 2, "projectile": {"speed": 20}},
                {"type": "radial_burst", "start": .5, "projectile_count": 3, "projectile": {"speed": 20}},
            ],
        },
        "patterns": [{"group": "pair", "start": 1, "repeat": {"count": 2, "interval": 2}}],
    }
    events = compile_pattern_events(data)

    assert [event.start for event in events] == [1.0, 1.5, 3.0, 3.5]
    defense = make_sequence(data)
    advance(defense, 1.01)
    assert len(defense.projectiles) == 2
    advance(defense, .55)
    assert len(defense.projectiles) == 5


def test_ranges_choices_and_seeded_randomness_are_deterministic():
    first = random.Random(3)
    second = random.Random(3)
    value = {"speed": {"min": 50, "max": 70}, "angle": {"choices": [0, 45, 90]}}

    assert resolve_random_value(value, first) == resolve_random_value(value, second)
    with pytest.raises(DefenseConfigError, match="min cannot exceed"):
        resolve_random_value({"min": 2, "max": 1}, random.Random())


def test_difficulty_overrides_deep_merge_without_copying_the_base_pattern():
    base = {
        "fire_interval": .4,
        "projectile": {"speed": 60, "damage": 2},
        "difficulty": {"hard": {"fire_interval": .2, "projectile": {"speed": 90}}},
    }
    assert apply_difficulty_overrides(base, "hard") == {
        "fire_interval": .2, "projectile": {"speed": 90, "damage": 2},
    }


def test_selected_sequence_difficulty_applies_to_nested_pattern_entries():
    sequence = {
        "duration": 1,
        "patterns": [{
            "type": "aimed_stream", "start": 0, "duration": .2,
            "origin": {"x": .5, "y": 0, "normalized": True}, "projectile": {"speed": 20},
            "difficulty": {"hard": {"projectile": {"speed": 70}}},
        }],
    }
    defense = DefenseSequence(sequence, ARENA, random.Random(1), difficulty="hard")
    defense.update(.01, BattleInput(), lambda _: None)
    assert defense.projectiles[0].speed == pytest.approx(70)


def test_normalized_coordinates_are_relative_to_the_defense_arena():
    assert resolve_position({"x": .5, "y": .25, "normalized": True}, ARENA) == (60.0, 40.0)
    assert resolve_position([20, 10], ARENA) == (30.0, 30.0)


def test_radial_spiral_and_motion_modifiers_use_reusable_projectiles():
    defense = make_sequence({
        "duration": 1,
        "patterns": [
            {"type": "radial_burst", "start": 0, "projectile_count": 6,
             "origin": {"x": .5, "y": .5, "normalized": True}, "projectile": {"speed": 20, "radius": 2}},
            {"type": "curving_projectiles", "start": 0, "initial_angle": 0,
             "origin": {"x": .1, "y": .5, "normalized": True}, "projectile": {"speed": 20, "motion": {"angular_velocity": 90}}},
        ],
    })
    defense.update(.5, BattleInput(), lambda _: None)

    assert len(defense.projectiles) == 7
    curved = defense.projectiles[-1]
    assert curved.vy > 0  # 90°/sec bends its initial rightward velocity down.


def test_radial_burst_supports_initial_rotation_and_outward_orbits():
    defense = make_sequence({
        "duration": 1,
        "patterns": [{
            "type": "radial_burst", "start": 0, "projectile_count": 4,
            "origin": {"x": .5, "y": .5, "normalized": True},
            "initial_rotation_angle": 45, "orbital_speed": 90,
            "projectile": {"speed": 20, "radius": 2},
        }],
    })
    defense.update(.5, BattleInput(), lambda _: None)

    # The first projectile begins at 45 degrees, advances another 45 degrees,
    # and expands ten units from the center at (60, 60).
    first = defense.projectiles[0]
    assert first.x == pytest.approx(60)
    assert first.y == pytest.approx(70)


def test_radial_burst_per_burst_overrides_merge_projectiles_and_sample_ranges():
    defense = make_sequence({
        "duration": 1,
        "patterns": [{
            "type": "radial_burst", "start": 0, "duration": .2,
            "projectile_count": 1, "burst_interval": .1,
            "origin": {"x": .5, "y": .5, "normalized": True},
            "projectile": {"speed": 8, "radius": 2, "motion": {"angular_velocity": 5}},
            "bursts": [
                {
                    "initial_rotation_angle": [0, 0],
                    "orbital_speed": [0, 0],
                    "projectile": {"speed": [10, 10], "motion": {"angular_velocity": [30, 30]}},
                },
                {
                    "initial_rotation_angle": [90, 90],
                    "orbital_speed": [90, 90],
                    "projectile": {"speed": [20, 20], "motion": {"angular_velocity": [40, 40]}},
                },
            ],
        }],
    })
    defense.update(.11, BattleInput(), lambda _: None)

    first, second = defense.projectiles
    assert first.speed == pytest.approx(10)
    assert first.angular_velocity == pytest.approx(30)
    assert second.orbital_speed == pytest.approx(90)
    assert second.orbital_radial_speed == pytest.approx(20)
    assert second.y > 60  # Its per-burst initial rotation points it downward.


def test_radial_burst_repetitions_repeat_the_entire_burst_sequence():
    defense = make_sequence({
        "duration": 1,
        "patterns": [{
            "type": "radial_burst", "start": 0, "projectile_count": 1,
            "burst_interval": .1, "repetitions": 3,
            "projectile": {"speed": 10, "radius": 2},
            "bursts": [
                {"initial_rotation_angle": 0},
                {"initial_rotation_angle": 90},
            ],
        }],
    })
    defense.update(.7, BattleInput(), lambda _: None)

    assert len(defense.projectiles) == 6
    assert [projectile.vx > projectile.vy for projectile in defense.projectiles] == [True, False] * 3


def test_telegraphs_do_not_damage_until_their_active_phase_and_share_iframes():
    defense = make_sequence({
        "duration": 1,
        "hit_invulnerability": .5,
        "patterns": [{
            "type": "telegraph_strike", "start": 0, "shape": "circle",
            "position": {"x": .5, "y": .5, "normalized": True}, "radius": 20,
            "warning_duration": .2, "active_duration": .5, "damage": 4,
        }],
    })
    hits: list[int] = []
    defense.update(.1, BattleInput(), hits.append)
    defense.update(.11, BattleInput(), hits.append)
    defense.update(.1, BattleInput(), hits.append)

    assert hits == [4]
    assert defense.player_invulnerable_for > 0
    assert any(item["telegraph"] for item in defense.renderables) is False


def test_projectile_bounces_curves_and_expires_without_pygame():
    projectile = Projectile(107, 40, 4, 4, 1, 3, vx=30, vy=0, bounce_count=1, angular_velocity=90)
    projectile.update(.2, (10, 20, 100, 80))
    assert projectile.bounces == 1
    assert projectile.vx < 0
    projectile.update(3, (10, 20, 100, 80))
    assert projectile.expired


def test_shrinking_arena_is_temporary_and_clamps_movement_inside_its_current_bounds():
    defense = make_sequence({
        "duration": 3,
        "patterns": [{
            "type": "shrinking_arena", "start": 0, "duration": 2.5,
            "end_bounds": {"x": 20, "y": 15, "width": 50, "height": 40},
            "shrink_duration": 1, "hold_duration": 1, "restore_duration": .5,
        }],
    })
    advance(defense, 1.1, BattleInput(move_x=-1, move_y=-1))
    left, top, width, height = defense.effective_bounds
    assert left > ARENA["x"] and top > ARENA["y"]
    assert left + 4 <= defense.player_x <= left + width - 4
    advance(defense, 1.5)
    assert defense.effective_bounds == (10.0, 20.0, 100.0, 80.0)


def test_moving_gap_wall_recomputes_its_safe_opening_while_crossing_the_arena():
    defense = make_sequence({
        "duration": 2,
        "patterns": [{
            "type": "moving_gap_wall", "start": 0, "direction": "top_to_bottom", "wall_speed": 20,
            "gap_width": 20, "gap_position": 50, "gap_movement": "linear", "gap_speed": 25,
            "damage": 2,
        }],
    })
    defense.update(.1, BattleInput(), lambda _: None)
    first = next(item for item in defense.renderables if item["kind"] == "moving_gap_wall")["pieces"]
    defense.update(.5, BattleInput(), lambda _: None)
    second = next(item for item in defense.renderables if item["kind"] == "moving_gap_wall")["pieces"]
    assert first != second


def _minimal_battle_with_defense() -> dict:
    return {
        "id": "defense_aliases",
        "enemy": {"name": "Wisp", "hp": 10},
        "arena": {"width": 100, "height": 80},
        "player_moves": [{"id": "tap", "name": "Tap", "pattern": "timing_bar", "base_power": 1,
                          "pattern_config": {"duration": 1}}],
        "defense_sequences": [{"id": "pulse", "duration": 1, "patterns": [
            {"type": "radial_burst", "start": 0, "projectile_count": 4, "projectile": {"speed": 25}},
        ]}],
        "enemy_moves": [{"id": "pulse_move", "name": "Pulse", "defense_sequence": "pulse", "defense_difficulty": "hard"}],
    }


def test_battle_loader_accepts_clear_defense_aliases_and_reports_bad_pattern_paths():
    config = load_battle_config(_minimal_battle_with_defense(), source="defense.yaml")
    assert config.defense_sequences is config.enemy_patterns
    assert config.enemy_moves["pulse_move"]["pattern"] == "pulse"

    bad = _minimal_battle_with_defense()
    bad["defense_sequences"][0]["patterns"][0]["type"] = "not_a_defense_pattern"
    with pytest.raises(BattleConfigError, match=r"enemy_patterns\.pulse\.patterns\[0\]\.type"):
        load_battle_config(bad, source="defense.yaml")


def test_optional_defense_sprite_can_be_preflighted_by_the_story_asset_resolver():
    data = _minimal_battle_with_defense()
    data["defense_sequences"][0]["patterns"][0]["projectile"]["sprite"] = "sprites/battle/missing.png"
    with pytest.raises(BattleConfigError, match="missing sprite"):
        load_battle_config(data, source="defense.yaml", sprite_exists=lambda _: False)
    assert load_battle_config(data, source="defense.yaml", sprite_exists=lambda name: name == "battle/missing.png")


def test_canonical_defense_sequence_runs_through_the_existing_enemy_turn_states():
    config = load_battle_config(_minimal_battle_with_defense(), source="defense.yaml")
    state = GameState(stats={"hp": 20, "max_hp": 20, "attack": 2, "defense": 0})
    battle = BattleController(config, state, rng=random.Random(2))
    battle.active_enemy_move = "pulse_move"
    battle._start_defense()
    battle.update(.25)  # opening transition

    assert battle.state == BattleState.DEFENSE
    battle.update(.1, BattleInput())
    assert battle.active_defense is not None
    assert len(battle.active_defense.projectiles) == 4


def test_every_documented_example_validates_and_completes_as_a_headless_smoke_test():
    path = Path(__file__).resolve().parents[1] / "docs" / "examples" / "defense_sequences.yaml"
    examples = [example for example in yaml.safe_load_all(path.read_text(encoding="utf-8")) if example]
    assert len(examples) >= 20
    for example in examples:
        validate_defense_sequence(example, example["id"])
        defense = DefenseSequence(example, ARENA, random.Random(11))
        for _ in range(300):
            if defense.update(.05, BattleInput(), lambda _: None).completed:
                break
        assert defense.elapsed == pytest.approx(defense.duration), example["id"]
