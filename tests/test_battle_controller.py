"""Deterministic coverage for non-rendering interactive battle logic."""

from __future__ import annotations

import random

import pytest

from engine.battle.config import load_battle_config
from engine.battle.controller import BattleController, BattleState
from engine.battle.controls import BattleInput
from engine.battle.patterns import DefenseSequence, TimingBarSequence, calculate_player_damage, create_attack_sequence
from engine.battle.qte import ChargeReleaseQTE, DirectionalComboQTE, QuickSlashQTE, RhythmComboQTE
from engine.core.game_state import GameState
from engine.errors import BattleConfigError


ITEMS = {
    "wood_sword": {
        "name": "Wood Sword", "type": "weapon", "equipment": {"bonuses": {"attack": 2}},
        "combat": {"move_grants": ["jab", "weapon_cut", "late_move"]},
    },
    "potion": {
        "name": "Potion", "description": "Restores health.", "type": "consumable",
        "combat": {"usable": True, "consume_turn": True, "effects": [{"heal": 7}]},
    },
    "key": {"name": "Key", "description": "Story-only.", "type": "key_item"},
}


def battle_data(**overrides):
    data = {
        "id": "test_fight",
        "enemy": {"name": "Test Wisp", "hp": 30, "attack": 2, "defense": 1},
        "arena": {"x": 0, "y": 0, "width": 100, "height": 80, "player_speed": 100},
        "player_moves": [
            {"id": "jab", "name": "Jab", "pattern": "timing_bar", "base_power": 5,
             "pattern_config": {"duration": 1, "target_position": .5, "perfect_window": .1, "good_window": .3}},
            {"id": "weapon_cut", "name": "Weapon Cut", "pattern": "timing_bar", "base_power": 8,
             "pattern_config": {"duration": 1, "target_position": .5, "perfect_window": .1, "good_window": .3}},
            {"id": "late_move", "name": "Late Move", "pattern": "position_target", "base_power": 7,
             "pattern_config": {"duration": 1}},
        ],
        "initial_player_moves": ["jab", "weapon_cut"],
        "enemy_patterns": [
            {"id": "dot", "duration": .4, "player": {"invulnerability_time": .5}, "timeline": [
                {"at": 0, "action": "spawn", "projectile": {"shape": "circle", "size": 10, "spawn": {"origin": [50, 40]}, "damage": 3, "lifetime": .3}},
            ]},
            {"id": "burst", "duration": .2, "timeline": [
                {"at": 0, "action": "spawn_radial", "count": 3, "projectile": {"size": 5, "damage": 1, "lifetime": .1}},
            ]},
        ],
        "enemy_moves": [
            {"id": "a", "name": "Dot", "pattern": "dot", "weight": 1, "no_immediate_repeat": True, "cooldown": 1},
            {"id": "b", "name": "Burst", "pattern": "burst", "weight": 3, "no_immediate_repeat": True},
        ],
        "phases": [
            {"id": "enraged", "when": {"enemy_hp_ratio_lte": .5}, "actions": [
                {"add_player_move": "late_move"},
                {"augment_player_move": {"move": "jab", "fields": {"base_power_add": 3, "timing_window_multiplier": .5}}},
                {"augment_enemy_pattern": {"pattern": "burst", "fields": {"projectile_count_add": 2}}},
            ]},
        ],
    }
    data.update(overrides)
    return data


def make_controller(data=None, state=None, rng=None):
    state = state or GameState(stats={"hp": 20, "max_hp": 20, "attack": 4, "defense": 1}, inventory={"potion": 1})
    state.equip_item("weapon", "wood_sword")
    for move_id in ("jab", "weapon_cut", "late_move"):
        state.learn_move(move_id)
    controller = BattleController(load_battle_config(data or battle_data(), ITEMS, "test.yaml"), state, ITEMS, rng or random.Random(1))
    controller.update(0)
    return controller


def revival_battle_data(**on_lose_overrides):
    data = battle_data()
    data["phases"].append({
        "id": "revived_phase", "name": "Revived", "when": {"fight_flag": "never_auto_activate"},
        "actions": [
            {"set_background": "revived_sky.png"}, {"set_enemy_sprite": "revived_enemy.png"},
            {"add_player_move": "late_move"}, {"set_fight_flag": {"revived": True}},
        ],
    })
    data["on_lose"] = {
        "type": "determined_revival",
        "dialogue": [{"speaker": "narrator", "text": "But you refuse to fall."}],
        "enemy_message": {"speaker": "enemy", "text": "You are still standing?"},
        "next_phase": "revived_phase",
        "revived_hp": 3,
    }
    data["on_lose"].update(on_lose_overrides)
    return data


def test_explicit_battle_flow_reaches_defense_and_returns_to_command():
    battle = make_controller()
    assert battle.state == BattleState.COMMAND and battle.menu_entries() == ["Fight", "Inventory"]
    battle.handle_action("SELECT")  # Fight
    assert battle.state == BattleState.MOVE_MENU
    battle.handle_action("SELECT")  # Jab
    assert battle.state == BattleState.PLAYER_ATTACK
    battle.active_attack.elapsed = battle.active_attack.target_position
    battle.handle_action("SELECT")
    assert battle.state == BattleState.ENEMY_SELECT
    battle.update(0)
    assert battle.state == BattleState.ENEMY_TELEGRAPH
    battle.update(1)
    assert battle.state == BattleState.DEFENSE_OPENING
    battle.update(.25)
    assert battle.state == BattleState.DEFENSE
    battle.update(1, BattleInput())
    assert battle.state == BattleState.DEFENSE_CLOSING
    battle.update(.25)
    assert battle.state == BattleState.COMMAND


def test_moving_weak_point_plays_the_arrow_sound_when_fired():
    battle = make_controller(battle_data(initial_player_moves=["late_move"]))
    battle.handle_action("SELECT")  # Fight
    battle.handle_action("SELECT")  # Moving weak point

    assert battle.state == BattleState.PLAYER_ATTACK
    assert battle.handle_action("SELECT")
    assert battle.consume_audio_events() == [("sfx", "arrow.wav")]


def test_charge_release_plays_shadow_sound_for_its_release_transition_only():
    battle = make_controller()
    battle.state = BattleState.PLAYER_ATTACK
    battle.active_attack = ChargeReleaseQTE()

    assert battle.handle_action("SELECT")
    assert battle.consume_audio_events() == []
    assert battle.handle_action("SELECT_RELEASE")
    assert battle.consume_audio_events() == []

    battle.update(.333)
    assert battle.consume_audio_events() == [("sfx", "shadow.wav")]
    battle.update(.01)
    assert battle.consume_audio_events() == []


def test_moving_weak_point_plays_the_hit_sound_when_the_arrow_connects():
    data = battle_data(initial_player_moves=["late_move"])
    data["player_moves"][2]["pattern_config"].update({"speed": .01, "target_y": .30})
    battle = make_controller(data)
    battle.handle_action("SELECT")  # Fight
    battle.handle_action("SELECT")  # Moving weak point
    battle.active_attack.elapsed = 50  # Center the deliberately slow target.
    battle.handle_action("SELECT")
    battle.consume_audio_events()  # Arrow release cue.

    battle.update(.4)

    assert battle.consume_audio_events() == [("sfx", "hit.wav")]


def test_rhythm_combo_plays_hit_and_penalty_sounds_for_the_matching_actions():
    battle = make_controller()
    battle.state = BattleState.PLAYER_ATTACK
    battle.active_player_move = "jab"
    battle.active_attack = RhythmComboQTE(duration=2, beats=[.5], tolerance=.1)

    battle.active_attack.update(.5)
    assert battle.handle_action("SELECT")
    assert battle.consume_audio_events() == [("sfx", "hit.wav")]

    assert battle.handle_action("SELECT")
    assert battle.consume_audio_events() == [("sfx", "swallow.wav")]


def test_rhythm_combo_includes_the_escalated_pitch_in_its_hit_event():
    battle = make_controller()
    battle.state = BattleState.PLAYER_ATTACK
    battle.active_player_move = "jab"
    qte = RhythmComboQTE(duration=4.8, rng=random.Random(3))
    block = next(index for index, count in enumerate(qte.block_counts) if count == 2)
    first, second = [bar for bar in qte.bars if bar["block"] == block]
    battle.active_attack = qte

    qte.elapsed = first["beat"]
    assert battle.handle_action("SELECT")
    assert battle.consume_audio_events() == [("sfx", "hit.wav")]
    qte.elapsed = second["beat"]
    assert battle.handle_action("SELECT")
    assert battle.consume_audio_events() == [("sfx", "hit.wav", pytest.approx(1.059463))]


def test_directional_combo_plays_one_hit_sound_per_successful_strike():
    battle = make_controller()
    battle.state = BattleState.PLAYER_ATTACK
    qte = DirectionalComboQTE(required_hits=2, rng=random.Random(1))
    left, top, width, height = qte.region_rect("UP")
    qte.target_x, qte.target_y = left + width / 2, top + height / 2
    qte.outbound_direction = "UP"
    qte.update(0, BattleInput(move_y=-1))
    battle.active_attack = qte

    assert not battle.handle_action("SELECT")
    assert battle.handle_action("UP")
    assert battle.consume_audio_events() == [("sfx", "hit.wav")]
    assert battle.handle_action("UP")
    assert battle.consume_audio_events() == []


def test_rapid_slash_plays_escalating_hit_sounds_for_cut_blocks():
    battle = make_controller()
    battle.state = BattleState.PLAYER_ATTACK
    battle.active_player_move = "jab"
    qte = QuickSlashQTE(block_count=2, strong_threshold=1)
    for block in qte.blocks:
        block["top"] = qte.slash_region_vertical_position - qte.block_height / 2
    battle.active_attack = qte

    assert battle.handle_action("LEFT")
    assert battle.consume_audio_events() == [("sfx", "slash.wav")]
    assert battle.handle_action("RIGHT")
    assert battle.consume_audio_events() == [("sfx", "slash.wav", pytest.approx(1.059463))]
    assert battle.handle_action("LEFT")
    assert battle.consume_audio_events() == [("sfx", "arrow.wav", 2.0)]


def test_weapon_dependent_moves_and_gear_inspection():
    battle = make_controller()
    assert battle.available_player_moves() == ["jab", "weapon_cut"]
    gear = battle.gear_data()
    assert gear["stats"]["attack"] == 6
    assert gear["weapon_moves"] == ["Jab", "Weapon Cut"]
    battle.game_state.equipment.clear()
    assert battle.available_player_moves() == ["jab", "weapon_cut"]


def test_phase_runs_once_and_applies_move_and_augmentations():
    battle = make_controller()
    battle.enemy.hp = 15
    assert not battle._check_phases(BattleState.COMMAND)
    assert battle.phase_id == "enraged"
    assert "late_move" in battle.available_player_moves()
    augmented = battle._effective_player_move("jab")
    assert augmented["base_power"] == 8
    assert augmented["pattern_config"]["good_window"] == .15
    pattern = battle._effective_enemy_pattern("burst")
    assert pattern["timeline"][0]["count"] == 5
    battle._check_phases(BattleState.COMMAND)
    assert len(battle.phase_ids) == 1


def test_phase_can_replace_the_battle_background_and_enemy_sprite():
    data = battle_data(phases=[{
        "id": "visual_shift", "when": {"fight_flag": "visual_shift"},
        "actions": [{"set_background": "storm.png"}, {"set_enemy_sprite": "storm_wisp.png"}],
    }])
    battle = make_controller(data)
    battle.fight_flags["visual_shift"] = True

    assert not battle._check_phases(BattleState.COMMAND)
    assert battle.background == "storm.png"
    assert battle.enemy_sprite == "storm_wisp.png"


def test_weighted_selection_no_repeat_and_cooldown():
    battle = make_controller(rng=random.Random(4))
    battle.config.enemy_moves["a"]["no_immediate_repeat"] = False
    battle.config.enemy_moves["b"]["no_immediate_repeat"] = False
    choices = [battle.select_enemy_move() for _ in range(20)]
    assert choices.count("b") > choices.count("a")
    battle.config.enemy_moves["b"]["no_immediate_repeat"] = True
    battle.last_enemy_move = "b"
    assert battle.select_enemy_move() == "a"
    battle._set_enemy_cooldown("a")
    assert battle.enemy_cooldowns["a"] == 1


def test_enemy_scripted_sequence_and_conditional_availability():
    data = battle_data(enemy_sequence=["a", "b"])
    data["enemy_moves"][1]["availability"] = {"min_turn": 2}
    battle = make_controller(data)
    assert battle.select_enemy_move() == "a"
    # The second scripted entry is not ready, so weighted selection falls
    # back to the only currently available move.
    assert battle.select_enemy_move() == "a"
    battle.turn = 2
    assert battle.select_enemy_move() in {"a", "b"}


def test_performance_damage_and_health_clamping():
    move = {"base_power": 9}
    assert [calculate_player_damage(move, 4, 1, score) for score in (0.0, .5, 1.0, 1.25)] == [0, 6, 12, 15]
    battle = make_controller()
    battle._take_player_damage(999)
    assert battle.current_player_hp() == 0
    battle.game_state.set_stat("hp", 19)
    battle._use_item("potion")
    assert battle.current_player_hp() == 20


def test_timing_qte_uses_discrete_nested_bands_and_a_safe_random_target():
    outcomes = ((0.0, "Critical", 1.25), (.02, "Strong", 1.0), (.08, "Weak", .5), (.17, "Miss", 0.0))
    for distance, label, multiplier in outcomes:
        attack = TimingBarSequence(target_position=.5)
        attack.elapsed = attack.target_position + distance
        assert attack.confirm()
        assert (attack.outcome, attack.score) == (label, multiplier)

    move = {"pattern": "timing_bar", "pattern_config": {"duration": 1}}
    rng = random.Random(9)
    positions = [create_attack_sequence(move, rng).target_position for _ in range(4)]
    assert len(set(positions)) > 1
    assert all(.42 <= position <= .75 for position in positions)
    # The generous weak region still leaves a quarter of the track on the
    # left and a visible buffer on the right.
    assert all(position - .16 >= .26 and position + .16 <= .91 for position in positions)


def test_defense_invulnerability_prevents_repeated_collision_damage():
    pattern = {"duration": .4, "player": {"invulnerability_time": .5}, "timeline": [
        {"at": 0, "action": "spawn", "projectile": {"shape": "circle", "size": 20, "spawn": {"origin": [50, 40]}, "damage": 4, "lifetime": .35}},
    ]}
    defense = DefenseSequence(pattern, {"x": 0, "y": 0, "width": 100, "height": 80, "player_speed": 100}, random.Random(1))
    hits = []
    defense.update(.1, BattleInput(), hits.append)
    defense.update(.1, BattleInput(), hits.append)
    assert hits == [4]
    assert defense.player_invulnerable_for > 0
    assert defense.player_hurt_for > 0


def test_defense_dialogue_never_pauses_player_control_or_pattern_time():
    data = battle_data(dialogue=[
        {"trigger": "player_hit", "text": "This must not open."},
        {"trigger": "player_low_health", "text": "This must not open either."},
    ])
    data["enemy_patterns"][0]["timeline"].append({"at": 0, "action": "dialogue", "text": "Still no prompt."})
    battle = make_controller(data)
    battle.active_enemy_move = "a"
    battle._start_defense()
    assert battle.state == BattleState.DEFENSE_OPENING
    battle.update(.25)
    battle.update(.1)
    assert battle.state == BattleState.DEFENSE
    assert battle.dialogue_text is None
    assert "Still no prompt." in battle.logs


def test_defense_opening_delays_attacks_and_closing_is_immune():
    data = battle_data()
    data["enemy_patterns"][0].update({"duration": .1, "attack_delay": .4})
    battle = make_controller(data)
    battle.active_enemy_move = "a"
    battle._start_defense()
    battle.update(.25)
    assert battle.state == BattleState.DEFENSE_OPENING
    assert battle.defense_window_scale == 1.0
    battle.update(.15)
    assert battle.state == BattleState.DEFENSE
    battle.update(.1)
    assert battle.state == BattleState.DEFENSE_CLOSING
    hp_at_closing = battle.current_player_hp()
    battle.update(.125)
    assert battle.state == BattleState.DEFENSE_CLOSING
    assert battle.defense_window_scale == pytest.approx(.5)
    assert battle.current_player_hp() == hp_at_closing
    battle.update(.125)
    assert battle.state == BattleState.COMMAND


def test_combat_item_filter_quantity_consumption_and_back_navigation():
    battle = make_controller()
    battle.game_state.add_item("key")
    assert battle.combat_item_ids() == ["potion"]
    battle.handle_action("DOWN")
    battle.handle_action("SELECT")
    assert battle.state == BattleState.INVENTORY_MENU
    battle.handle_action("SELECT")
    assert battle.state == BattleState.ITEM_MENU
    battle.handle_action("SELECT")
    assert not battle.game_state.has_item("potion")
    assert battle.state == BattleState.ENEMY_SELECT
    assert battle.handle_action("BACK") is False  # cannot cancel a committed turn


def test_dialogue_is_one_time_and_pauses_intro():
    data = battle_data(dialogue=[{"id": "start", "trigger": "battle_start", "text": "Hello"}])
    state = GameState(stats={"hp": 20, "max_hp": 20, "attack": 4, "defense": 1})
    state.equip_item("weapon", "wood_sword")
    state.learn_move("jab")
    battle = BattleController(load_battle_config(data, ITEMS, "dialogue.yaml"), state, ITEMS)
    battle.update(0)
    assert battle.state == BattleState.DIALOGUE
    battle.handle_action("SELECT")
    assert battle.state == BattleState.COMMAND
    assert not battle._request_dialogue("battle_start", BattleState.COMMAND, {})


def test_move_and_turn_dialogue_triggers_pause_the_correct_transition():
    data = battle_data(dialogue=[
        {"trigger": "move_used", "when": {"move": "jab"}, "text": "The jab lands."},
        {"trigger": "turn_start", "text": "Your next turn."},
    ])
    battle = make_controller(data)
    battle.handle_action("SELECT")
    battle.handle_action("SELECT")
    battle.active_attack.elapsed = battle.active_attack.target_position
    battle.handle_action("SELECT")
    assert battle.state == BattleState.DIALOGUE and battle.dialogue_text == "The jab lands."
    battle.handle_action("SELECT")
    assert battle.state == BattleState.ENEMY_SELECT
    battle._complete_enemy_turn()
    assert battle.state == BattleState.DIALOGUE and battle.dialogue_text == "Your next turn."
    battle.handle_action("SELECT")
    assert battle.state == BattleState.COMMAND


def test_remark_dialogue_keeps_the_command_menu_usable_until_fight_is_chosen():
    data = battle_data(dialogue=[{
        "trigger": "turn_start", "text": "Keep your guard up.", "type": "remark", "once": False,
    }])
    battle = make_controller(data)
    battle._complete_enemy_turn()
    assert battle.state == BattleState.COMMAND
    assert battle.remark_text == "Keep your guard up."
    battle.handle_action("DOWN")
    assert battle.state == BattleState.COMMAND and battle.remark_text
    battle.handle_action("UP")
    battle.handle_action("SELECT")
    assert battle.state == BattleState.MOVE_MENU and battle.remark_text is None


def test_environment_dialogue_types_without_blocking_action_selection_and_clears_on_attack():
    data = battle_data(dialogue=[{
        "trigger": "battle_start", "text": "Cold rain  falls\nthrough the trees.", "type": "environment",
    }])
    battle = make_controller(data)

    assert battle.state == BattleState.COMMAND
    assert battle.environment_text == "Cold rain falls through the trees."
    assert battle.visible_environment_text == ""
    assert not battle.update(.01)  # No character is ready yet, so no redraw is needed.
    assert battle.update(.04)  # A typed character marks the battle frame as changed.
    assert battle.visible_environment_text == "Co"

    battle.handle_action("SELECT")
    assert battle.state == BattleState.MOVE_MENU
    assert battle.environment_text
    battle.handle_action("SELECT")
    assert battle.state == BattleState.PLAYER_ATTACK
    assert battle.environment_text is None


def test_opponent_dialogue_types_then_waits_before_automatically_starting_enemy_turn():
    data = battle_data(dialogue=[{
        "trigger": "after_player_action", "text": "Not bad.", "type": "opponent", "pause": .25,
    }])
    battle = make_controller(data)
    battle.handle_action("SELECT")
    battle.handle_action("SELECT")
    battle.active_attack.elapsed = battle.active_attack.target_position
    battle.handle_action("SELECT")
    assert battle.state == BattleState.DIALOGUE
    assert battle.dialogue_type == "opponent"
    assert battle.visible_dialogue_text == ""
    assert battle.handle_action("SELECT") is False
    battle.update(.75)
    assert battle.visible_dialogue_text == ""
    battle.update(.25)
    assert battle.visible_dialogue_text == ""
    battle.update(.2)
    assert battle.state == BattleState.DIALOGUE
    assert battle.visible_dialogue_text == "Not bad."
    battle.update(0)
    assert battle.state == BattleState.DIALOGUE
    battle.update(.24)
    assert battle.state == BattleState.DIALOGUE
    battle.update(.01)
    assert battle.state == BattleState.ENEMY_SELECT


def test_only_first_opponent_line_is_shown_for_a_transition():
    data = battle_data(dialogue=[
        {"trigger": "after_player_action", "text": "First.", "type": "opponent", "pause": 0},
        {"trigger": "after_player_action", "text": "Second.", "type": "opponent", "pause": 0},
    ])
    battle = make_controller(data)
    battle._request_dialogue("after_player_action", BattleState.ENEMY_SELECT, {"move": "jab"})
    assert battle.dialogue_text == "First."


def test_opponent_dialogue_uses_prepared_line_breaks_from_its_first_character():
    battle = make_controller()
    battle._show_dialogue(["Raw text"], BattleState.COMMAND, "opponent", 0)
    battle.prepare_opponent_dialogue("Prepared\ntext")
    battle.update(.2)
    assert battle.visible_dialogue_text == "Prepared"
    battle.update(.025)
    assert battle.visible_dialogue_text == "Prepared\n"


def test_victory_and_defeat_are_detected():
    battle = make_controller()
    battle.enemy.hp = 1
    battle.active_player_move = "jab"
    battle._start_player_attack()
    battle.active_attack.elapsed = battle.active_attack.target_position
    battle.active_attack.confirm()
    battle._resolve_player_attack()
    assert battle.state == BattleState.VICTORY_ANIMATION and battle.outcome == "win"
    defeat = make_controller()
    defeat._take_player_damage(999)
    defeat.active_enemy_move = "a"
    defeat._resolve_enemy_attack()
    assert defeat.state == BattleState.DEFEAT_ANIMATION and defeat.outcome == "lose"


def test_player_damage_sound_and_loss_animation_timeline():
    battle = make_controller()
    battle.active_player_move = "jab"
    battle._start_player_attack()
    battle.active_attack.elapsed = battle.active_attack.target_position + .02
    battle.active_attack.confirm()
    battle._resolve_player_attack()
    assert ("sfx", "damage.wav") in battle.consume_audio_events()
    assert any(animation.kind == "enemy_shake" and animation.duration == .25 for animation in battle.animations.active)

    battle.active_enemy_move = "a"
    battle._start_defense()
    battle.update(.25)
    assert battle.active_defense is not None
    battle.active_defense.player_x, battle.active_defense.player_y = 24, 53
    battle._take_player_damage(999)
    death = battle.death_animation
    assert death is not None
    assert battle.state == BattleState.DEFEAT_ANIMATION
    assert (death.x, death.y) == (24, 53)
    assert death.phase == "heart"
    assert battle.consume_audio_events() == [("sfx", "hurt.wav"), ("stop_music", None)]

    battle.update(.7)
    assert death.phase == "heart" and death.heart_shaking
    assert battle.consume_audio_events() == []
    battle.update(.25)
    assert death.phase == "broken_heart"
    assert battle.consume_audio_events() == [("sfx", "break1.wav")]
    battle.update(.5)
    assert death.phase == "broken_heart"
    assert battle.consume_audio_events() == []
    battle.update(.375)
    assert death.phase == "shards"
    assert len(death.shards) == 7
    assert all(shard.sprite in {"heart_shard1.png", "heart_shard2.png", "heart_shard3.png"}
               for shard in death.shards)
    shard_type_counts = [sum(shard.sprite == sprite for shard in death.shards)
                         for sprite in ("heart_shard1.png", "heart_shard2.png", "heart_shard3.png")]
    assert max(shard_type_counts) - min(shard_type_counts) <= 1
    assert battle.consume_audio_events() == [("sfx", "break2.wav")]
    battle.update(2.99)
    assert not battle.finished
    battle.update(.01)
    assert battle.finished


def test_determined_revival_replaces_death_and_enters_the_configured_phase():
    battle = make_controller(revival_battle_data())
    original_turn = battle.turn

    battle._take_player_damage(999)

    assert battle.state == BattleState.REVIVAL_CUTSCENE
    assert battle.death_animation is None
    assert battle.revival_stage == "heart_split"
    assert battle.active_defense is None and battle.active_attack is None
    assert battle.handle_action("SELECT") is False
    assert battle.consume_audio_events() == [("sfx", "hurt.wav"), ("stop_music", None)]

    # The shared first half of the death animation ends in a split heart but
    # never queues break2/debris.  Damage callbacks and menu input remain
    # inert for the entire cutscene.
    battle.update(battle.DEATH_BREAK_1_AT)
    assert battle.revival_stage == "split_pause"
    assert battle.consume_audio_events() == [("sfx", "break1.wav")]
    battle._take_player_damage(5)
    assert battle.current_player_hp() == 0 and battle.turn == original_turn
    battle.update(battle.REVIVAL_SPLIT_PAUSE_DURATION)
    assert battle.state == BattleState.REVIVAL_CUTSCENE
    assert battle.revival_stage == "revival_dialogue_delay"
    assert battle.consume_audio_events() == [("music", "refused_to_die.ogg", 0.5)]
    battle.update(battle.REVIVAL_DIALOGUE_DELAY_DURATION)
    assert battle.state == BattleState.DIALOGUE
    assert battle.revival_stage == "revival_dialogue"
    assert battle.dialogue_text == "But you refuse to fall."
    assert battle.visible_revival_dialogue_text == ""
    battle.update(battle.REVIVAL_DIALOGUE_CHARACTER_DELAY)
    assert battle.visible_revival_dialogue_text == "B"
    assert battle.consume_audio_events() == []
    battle.update(battle.REVIVAL_DIALOGUE_CHARACTER_DELAY)
    assert battle.consume_audio_events() == [("sfx", "dialog_loud.wav")]

    assert battle.handle_action("UP") is False
    assert battle.handle_action("SELECT")  # Complete the typewriter reveal.
    assert battle.visible_revival_dialogue_text == battle.dialogue_text
    assert battle.handle_action("SELECT")  # Fade the fully revealed line.
    assert battle.revival_dialogue_alpha == 1.0
    battle.update(battle.REVIVAL_DIALOGUE_FADE_DURATION / 2)
    assert battle.revival_dialogue_alpha == 0.5
    assert battle.handle_action("SELECT") is False
    battle.update(battle.REVIVAL_DIALOGUE_FADE_DURATION / 2)
    assert battle.revival_dialogue_alpha == 0.0
    battle.update(battle.REVIVAL_DIALOGUE_NEXT_LINE_DELAY_DURATION)
    assert battle.state == BattleState.REVIVAL_CUTSCENE
    assert battle.revival_stage == "music_fade"
    assert battle.consume_audio_events() == [("fade_music", None, 1.0)]

    battle.update(battle.REVIVAL_MUSIC_FADE_DURATION)
    assert battle.revival_stage == "heart_recombine"
    battle.update(battle.REVIVAL_HEART_RECOMBINE_DURATION)
    assert battle.revival_stage == "post_recombine_pause"
    battle.update(battle.REVIVAL_POST_RECOMBINE_PAUSE_DURATION)
    assert battle.revival_stage == "hero_music_pause"
    assert battle.consume_audio_events() == [
        ("sfx", "heal.wav"), ("music_sequence", "true_hero_intro.ogg", "true_hero_loop.ogg"),
    ]
    battle.update(battle.REVIVAL_HERO_MUSIC_PAUSE_DURATION)
    assert battle.revival_stage == "heart_fade"
    assert battle.current_player_hp() == 3
    assert battle.animations.displayed_health["player"] == 3 / battle.maximum_player_hp()
    assert battle.background == "revived_sky.png"
    assert battle.enemy_sprite == "revived_enemy.png"
    battle.update(battle.REVIVAL_HEART_FADE_DURATION)
    assert battle.revival_stage == "background_fade_delay"
    battle.update(battle.REVIVAL_BACKGROUND_FADE_DELAY_DURATION)
    assert battle.revival_stage == "background_fade"
    battle.update(battle.REVIVAL_BACKGROUND_FADE_DURATION)
    assert battle.state == BattleState.DIALOGUE
    assert battle.revival_stage == "enemy_dialogue"
    assert battle.dialogue_text == "You are still standing?"
    assert battle.visible_revival_dialogue_text == ""

    assert battle.handle_action("SELECT")  # Complete the enemy line first.
    assert battle.handle_action("SELECT")
    assert battle.state == BattleState.COMMAND
    assert battle.revival_stage == "complete"
    assert battle.current_player_hp() == 3
    assert battle.phase_id == "revived_phase"
    assert battle.fight_flags["revived"] is True
    assert "late_move" in battle.player_move_ids
    assert battle.active_defense is None and battle.active_enemy_move is None


def test_game_over_on_lose_plays_break_then_delays_music_and_menu():
    battle = make_controller(battle_data(on_lose={
        "type": "game_over", "music": "loss.ogg", "text": "Defeated",
    }))

    battle._take_player_damage(999)

    assert battle.state is BattleState.GAME_OVER_CUTSCENE
    assert battle.game_over_cutscene is not None
    assert battle.game_over_cutscene.stage_name == "heart_split"
    assert battle.consume_audio_events() == [("sfx", "hurt.wav"), ("stop_music", None)]

    battle.update(battle.DEATH_BREAK_1_AT)
    assert battle.game_over_cutscene.stage_name == "music_delay"
    assert battle.consume_audio_events() == [("sfx", "break1.wav")]

    battle.update(.99)
    assert battle.consume_audio_events() == []
    assert not battle.game_over_menu_ready
    battle.update(.01)
    assert battle.game_over_cutscene.stage_name == "menu_delay"
    assert battle.consume_audio_events() == [("music", "loss.ogg", 0.5)]

    battle.update(.99)
    assert not battle.game_over_menu_ready
    battle.update(.01)
    assert battle.game_over_menu_ready


def test_determined_revival_is_single_use_unless_marked_repeatable():
    battle = make_controller(revival_battle_data(dialogue=[], enemy_message=None))
    battle._take_player_damage(999)
    battle.update(battle.DEATH_BREAK_1_AT + battle.REVIVAL_SPLIT_PAUSE_DURATION)
    battle.update(battle.REVIVAL_MUSIC_FADE_DURATION + battle.REVIVAL_HEART_RECOMBINE_DURATION
                  + battle.REVIVAL_POST_RECOMBINE_PAUSE_DURATION + battle.REVIVAL_HERO_MUSIC_PAUSE_DURATION
                  + battle.REVIVAL_HEART_FADE_DURATION + battle.REVIVAL_BACKGROUND_FADE_DELAY_DURATION
                  + battle.REVIVAL_BACKGROUND_FADE_DURATION)

    assert battle.state == BattleState.COMMAND and battle.revival_uses == 1
    battle._take_player_damage(999)
    assert battle.state == BattleState.DEFEAT_ANIMATION
    assert battle.death_animation is not None


def test_determined_revival_fades_before_printing_the_next_narration_line():
    battle = make_controller(revival_battle_data(dialogue=[
        {"speaker": "narrator", "text": "First resolve."},
        {"speaker": "narrator", "text": "Then rise."},
    ]))
    battle._take_player_damage(999)
    battle.update(battle.DEATH_BREAK_1_AT + battle.REVIVAL_SPLIT_PAUSE_DURATION)
    battle.update(battle.REVIVAL_DIALOGUE_DELAY_DURATION)

    assert battle.handle_action("SELECT")  # Complete the first typewriter reveal.
    assert battle.handle_action("SELECT")  # Begin fading the first line.
    battle.update(battle.REVIVAL_DIALOGUE_FADE_DURATION)
    assert battle.revival_dialogue_alpha == 0.0
    assert battle.visible_revival_dialogue_text == "First resolve."
    battle.update(battle.REVIVAL_DIALOGUE_NEXT_LINE_DELAY_DURATION - .01)
    assert battle.visible_revival_dialogue_text == "First resolve."
    battle.update(.01)
    assert battle.dialogue_text == "Then rise."
    assert battle.visible_revival_dialogue_text == ""
    battle.update(battle.REVIVAL_DIALOGUE_CHARACTER_DELAY)
    assert battle.visible_revival_dialogue_text == "T"


def test_determined_revival_can_be_explicitly_repeatable():
    battle = make_controller(revival_battle_data(dialogue=[], enemy_message=None, repeatable=True))
    complete_duration = (battle.DEATH_BREAK_1_AT + battle.REVIVAL_SPLIT_PAUSE_DURATION
                         + battle.REVIVAL_MUSIC_FADE_DURATION + battle.REVIVAL_HEART_RECOMBINE_DURATION
                         + battle.REVIVAL_POST_RECOMBINE_PAUSE_DURATION + battle.REVIVAL_HERO_MUSIC_PAUSE_DURATION
                         + battle.REVIVAL_HEART_FADE_DURATION + battle.REVIVAL_BACKGROUND_FADE_DELAY_DURATION
                         + battle.REVIVAL_BACKGROUND_FADE_DURATION)
    battle._take_player_damage(999)
    battle.update(complete_duration)
    assert battle.state == BattleState.COMMAND

    battle._take_player_damage(999)
    assert battle.state == BattleState.REVIVAL_CUTSCENE
    assert battle.revival_uses == 2


def test_determined_revival_skips_empty_optional_dialogue_without_stalling():
    battle = make_controller(revival_battle_data(dialogue=[], enemy_message=None))
    battle._take_player_damage(999)
    battle.consume_audio_events()
    battle.update(battle.DEATH_BREAK_1_AT + battle.REVIVAL_SPLIT_PAUSE_DURATION)

    assert battle.state == BattleState.REVIVAL_CUTSCENE
    assert battle.revival_stage == "music_fade"
    assert battle.consume_audio_events() == [
        ("sfx", "break1.wav"), ("music", "refused_to_die.ogg", 0.5), ("fade_music", None, 1.0),
    ]


def test_determined_revival_uses_the_configured_dialogue_sound():
    battle = make_controller(revival_battle_data(dialog_sound="resolve.wav"))
    battle._take_player_damage(999)
    battle.consume_audio_events()
    battle.update(battle.DEATH_BREAK_1_AT + battle.REVIVAL_SPLIT_PAUSE_DURATION)
    battle.update(battle.REVIVAL_DIALOGUE_DELAY_DURATION)
    battle.update(2 * battle.REVIVAL_DIALOGUE_CHARACTER_DELAY)

    assert ("sfx", "resolve.wav") in battle.consume_audio_events()


def test_death_heart_shards_are_randomized_but_seed_reproducible():
    first = make_controller(rng=random.Random(12))
    matching_seed = make_controller(rng=random.Random(12))
    different_seed = make_controller(rng=random.Random(13))
    for battle in (first, matching_seed, different_seed):
        battle._begin_defeat_animation()

    assert first.death_animation.shards == matching_seed.death_animation.shards
    assert first.death_animation.shards != different_seed.death_animation.shards


@pytest.mark.parametrize(("distance", "sound"), [(.17, "fall.wav"), (.08, "damage.wav")])
def test_miss_and_weak_attacks_use_their_outcome_sounds(distance, sound):
    battle = make_controller()
    battle.active_player_move = "jab"
    battle._start_player_attack()
    battle.active_attack.elapsed = battle.active_attack.target_position + distance
    battle.active_attack.confirm()
    battle._resolve_player_attack()
    assert battle.consume_audio_events() == [("sfx", sound)]


def test_healing_hurt_and_lethal_attack_use_their_specific_sounds():
    battle = make_controller()
    battle._take_player_damage(5)
    assert battle.consume_audio_events() == [("sfx", "hurt.wav")]
    battle._use_item("potion")
    assert battle.consume_audio_events() == [("sfx", "heal.wav")]

    battle = make_controller()
    battle.enemy.hp = 1
    battle.active_player_move = "jab"
    battle._start_player_attack()
    battle.active_attack.elapsed = battle.active_attack.target_position
    battle.active_attack.confirm()
    battle._resolve_player_attack()
    assert battle.consume_audio_events() == [("sfx", "slash.wav")]


def test_enemy_vaporizes_before_victory_dialogue_is_shown():
    battle = make_controller()
    battle.enemy.hp = 1
    battle.active_player_move = "jab"
    battle._start_player_attack()
    battle.active_attack.elapsed = battle.active_attack.target_position
    battle.active_attack.confirm()
    battle._resolve_player_attack()
    victory = battle.victory_animation
    assert victory is not None
    assert battle.state == BattleState.VICTORY_ANIMATION
    assert victory.enemy_alpha == 255
    battle.consume_audio_events()  # The critical slash is covered separately.

    # The hit flash completes before the vaporization timer starts, while the
    # enemy is already desaturated because victory_animation is present.
    battle.update(.16)
    assert victory.elapsed == 0
    battle.update(.99)
    assert battle.state == BattleState.VICTORY_ANIMATION
    assert battle.consume_audio_events() == []
    battle.update(.01)
    assert victory.enemy_alpha == 255
    assert battle.consume_audio_events() == [("sfx", "vaporized.wav")]
    battle.update(.25)
    assert 0 < victory.enemy_alpha < 255
    battle.update(.75)
    assert victory.enemy_alpha == 0
    assert battle.state == BattleState.VICTORY


def test_legacy_battle_is_adapted_without_editing_old_yaml():
    legacy = {"id": "old", "enemy": {"name": "Old", "hp": 4, "attack": 1, "defense": 0,
              "moves": [{"name": "Bite", "damage": [1, 1], "weight": 1}]}}
    config = load_battle_config(legacy, ITEMS, "old.yaml")
    assert config.legacy and config.initial_player_moves == ["basic_attack"]


@pytest.mark.parametrize("mutate, expected", [
    (lambda data: data["enemy_moves"][0].update({"pattern": "missing"}), "missing pattern"),
    (lambda data: data["phases"][0]["actions"].__setitem__(1, {"augment_player_move": {"move": "jab", "fields": {"arbitrary": 1}}}), "unsupported augmentation"),
    (lambda data: data.update({"dialogue": [{"trigger": "battle_start", "text": "Nope", "type": "unknown"}]}), "dialogue.*type"),
    (lambda data: data["enemy_patterns"][0].update({"attack_delay": -1}), "attack_delay"),
    (lambda data: data["player_moves"][0].update({"availability": {"weapons": ["wood_sword"]}}), "availability.weapons is no longer supported"),
    (lambda data: data.update({"on_lose": {"type": "determined_revival", "next_phase": "missing", "revived_hp": 1}}), "on_lose.next_phase references missing phase"),
    (lambda data: data["phases"][0]["actions"].append({"set_background": ""}), "set_background must be a non-empty asset filename"),
])
def test_invalid_yaml_references_and_augmentations_are_clear(mutate, expected):
    data = battle_data()
    mutate(data)
    with pytest.raises(BattleConfigError, match=expected):
        load_battle_config(data, ITEMS, "invalid.yaml")
