"""Compatibility surface for player attack QTE helpers.

Enemy dodge/bullet-hell behavior lives in :mod:`engine.battle.defense`.
This module retains historical imports used by existing player attack code and
older integrations, without conflating player QTE patterns with enemy defense
patterns.
"""

from __future__ import annotations

import random
from typing import Any

from engine.battle.qte import (
    AttackQTE as AttackSequence,
    MovingWeakPointQTE as PositionTargetSequence,
    PrecisionBarQTE as TimingBarSequence,
    QTEResult,
    RhythmComboQTE as TimingSequence,
    create_attack_qte,
)


def create_attack_sequence(move: dict[str, Any], rng: random.Random | None = None) -> AttackSequence:
    """Compatibility factory; new callers should use ``create_attack_qte``."""
    return create_attack_qte(move, rng)


def calculate_player_damage(move: dict[str, Any], player_attack: int, enemy_defense: int,
                            performance: float | QTEResult,
                            modifiers: dict[str, float] | None = None) -> int:
    """The shared player-QTE damage formula.

    Enemy defense hazards never call this function: they damage the player's
    existing GameState through ``BattleController._take_player_damage``.
    """
    modifiers = modifiers or {}
    _, multiplier = (_score_outcome(performance) if isinstance(performance, (float, int))
                     else (performance.label, performance.multiplier))
    base_power = float(move.get("base_power", 0)) * float(modifiers.get("base_power_multiplier", 1.0))
    base_power += float(modifiers.get("base_power_add", 0.0))
    raw = (base_power + player_attack - enemy_defense) * multiplier
    return max(0, int(round(raw)))


def _score_outcome(score: float) -> tuple[str, float]:
    if score >= 1.125:
        return "Critical", 1.25
    if score >= 0.75:
        return "Strong", 1.0
    if score > 0:
        return "Weak", 0.5
    return "Miss", 0.0


# Backward-compatible enemy-defense imports.  New code should import these
# directly from ``engine.battle.defense`` for clear terminology.
from engine.battle.defense import (  # noqa: E402
    ArenaConstraint,
    BeamHazard,
    DEFENSE_PATTERN_TYPES,
    DefenseConfigError,
    DefenseResult,
    DefenseSequence,
    MovingGapWallHazard,
    OrbitingHazard,
    PATTERN_TYPES,
    Projectile,
    RingHazard,
    ScheduledEvent,
    ZoneHazard,
    apply_difficulty_overrides,
    compile_pattern_events,
    create_defense_pattern,
    normalize_sprite_reference,
    register_defense_pattern,
    resolve_position,
    resolve_random,
    resolve_random_value,
    validate_defense_sequence,
    validate_defense_sprites,
)

