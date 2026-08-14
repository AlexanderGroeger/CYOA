"""
engine/battle/battle_system.py

Turn-based combat rules. The pygame application owns the battle menu and
uses these pure resolution functions for each confirmed action.

Single enemy at a time for this version -- the data schema (a `moves` list
with weights) already supports richer AI later without a format change.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable

from engine.core.game_state import GameState


@dataclass
class Enemy:
    name: str
    hp: int
    max_hp: int
    attack: int
    defense: int
    moves: list[dict[str, Any]]

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> "Enemy":
        return cls(
            name=data["name"],
            hp=data["hp"],
            max_hp=data["hp"],
            attack=data.get("attack", 1),
            defense=data.get("defense", 0),
            moves=data.get("moves", [{"name": "Attack", "damage": [1, 3], "weight": 1}]),
        )


@dataclass
class BattleLog:
    lines: list[str] = field(default_factory=list)

    def add(self, line: str) -> None:
        self.lines.append(line)

    def clear(self) -> None:
        self.lines.clear()


def apply_player_attack(state: GameState, enemy: Enemy, log: BattleLog) -> None:
    """Damage = player attack minus enemy defense, floored at 1 so combat
    always makes progress even against a heavily-defended enemy."""
    raw = state.get_stat("attack", 1) - enemy.defense
    damage = max(1, raw)
    enemy.hp = max(0, enemy.hp - damage)
    log.add(f"You hit {enemy.name} for {damage} damage. ({enemy.hp}/{enemy.max_hp} HP left)")


def choose_enemy_move(enemy: Enemy, rng: Callable[[], float] = random.random) -> dict[str, Any]:
    """Weighted random pick among the enemy's moves."""
    weights = [m.get("weight", 1) for m in enemy.moves]
    total = sum(weights)
    roll = rng() * total
    cumulative = 0.0
    for move, weight in zip(enemy.moves, weights):
        cumulative += weight
        if roll <= cumulative:
            return move
    return enemy.moves[-1]


def apply_enemy_move(state: GameState, enemy: Enemy, move: dict[str, Any], log: BattleLog,
                      rng_int: Callable[[int, int], int] = random.randint) -> None:
    """Applies one enemy move against player state. Supports two move
    shapes: a damage range [min, max], or a named effect (currently just
    buff_attack, as a minimal example of a non-damage move)."""
    if "damage" in move:
        lo, hi = move["damage"]
        raw = rng_int(lo, hi) - state.get_stat("defense", 0)
        damage = max(1, raw)
        state.add_stat("hp", -damage)
        state.set_stat("hp", max(0, state.get_stat("hp", 0)))
        log.add(f"{enemy.name} uses {move['name']} for {damage} damage. "
                f"({state.get_stat('hp')}/{state.get_stat('max_hp')} HP left)")
    elif move.get("effect") == "buff_attack":
        enemy.attack += 1
        log.add(f"{enemy.name} uses {move['name']} and grows stronger!")
    else:
        log.add(f"{enemy.name} uses {move['name']}.")


def check_outcome(state: GameState, enemy: Enemy) -> str | None:
    """Returns 'win', 'lose', or None if the battle continues."""
    if enemy.hp <= 0:
        return "win"
    if state.get_stat("hp", 0) <= 0:
        return "lose"
    return None

