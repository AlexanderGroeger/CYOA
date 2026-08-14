from engine.battle.battle_system import (
    Enemy, BattleLog, apply_player_attack, choose_enemy_move, apply_enemy_move,
    check_outcome,
)
from engine.core.game_state import GameState


def test_damage_floors_at_one_even_vs_high_defense():
    state = GameState(stats={"attack": 2, "defense": 0, "hp": 20, "max_hp": 20})
    tough = Enemy.from_data({"name": "Tank", "hp": 10, "attack": 1, "defense": 99, "moves": []})
    log = BattleLog()
    apply_player_attack(state, tough, log)
    assert tough.hp == 9


def test_choose_enemy_move_weighting():
    enemy = Enemy.from_data({"name": "X", "hp": 10, "moves": [
        {"name": "A", "damage": [1, 1], "weight": 1},
        {"name": "B", "damage": [1, 1], "weight": 3},
    ]})
    assert choose_enemy_move(enemy, rng=lambda: 0.0)["name"] == "A"
    assert choose_enemy_move(enemy, rng=lambda: 0.99)["name"] == "B"


def test_apply_enemy_move_damage():
    state = GameState(stats={"hp": 20, "max_hp": 20, "defense": 0})
    enemy = Enemy.from_data({"name": "Y", "hp": 10, "moves": []})
    log = BattleLog()
    apply_enemy_move(state, enemy, {"name": "Bite", "damage": [5, 5]}, log, rng_int=lambda lo, hi: 5)
    assert state.get_stat("hp") == 15


def test_apply_enemy_move_buff_effect_is_non_damaging():
    state = GameState(stats={"hp": 20, "max_hp": 20})
    enemy = Enemy.from_data({"name": "Y", "hp": 5, "attack": 3, "moves": []})
    log = BattleLog()
    apply_enemy_move(state, enemy, {"name": "Howl", "effect": "buff_attack"}, log)
    assert enemy.attack == 4
    assert state.get_stat("hp") == 20


def test_check_outcome():
    assert check_outcome(GameState(stats={"hp": 10}), Enemy.from_data({"name": "Z", "hp": 0, "moves": []})) == "win"
    assert check_outcome(GameState(stats={"hp": 0}), Enemy.from_data({"name": "Z", "hp": 10, "moves": []})) == "lose"
    assert check_outcome(GameState(stats={"hp": 10}), Enemy.from_data({"name": "Z", "hp": 10, "moves": []})) is None

