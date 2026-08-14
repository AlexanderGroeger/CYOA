"""Combat-move difficulty resolution and per-move skill progression.

This module deliberately sits between authored combat-move data and QTE
objects.  QTEs receive one fully resolved move definition for an attempt;
they neither merge YAML nor know about saved player progression.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping

from engine.core.game_state import GameState
from engine.errors import BattleConfigError


RESULT_SCORES = {"miss": 0, "weak": 1, "strong": 2, "critical": 3}
DEFAULT_SKILL_PROGRESSION = {
    "evaluation_attempts": 10,
    "promotion_average": 2.5,
    "demotion_average": 1.5,
    "minimum_level": 1,
}


def result_score(result: str) -> int:
    """Convert a final QTE result tier into the saved skill score."""
    try:
        return RESULT_SCORES[result]
    except KeyError as exc:
        raise ValueError(f"Unknown combat result tier {result!r}") from exc


@dataclass(frozen=True)
class SkillProgressionConfig:
    evaluation_attempts: int = 10
    promotion_average: float = 2.5
    demotion_average: float = 1.5
    minimum_level: int = 1

    @classmethod
    def from_data(cls, data: Mapping[str, Any] | None, source: str = "combat moves") -> "SkillProgressionConfig":
        values = dict(DEFAULT_SKILL_PROGRESSION)
        if data is not None:
            if not isinstance(data, Mapping):
                raise BattleConfigError(f"Combat move config {source}: skill_progression must be a mapping")
            unknown = set(data) - set(values)
            if unknown:
                raise BattleConfigError(f"Combat move config {source}: skill_progression has unsupported field {sorted(unknown)[0]!r}")
            values.update(data)
        attempts = values["evaluation_attempts"]
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts <= 0:
            raise BattleConfigError(f"Combat move config {source}: skill_progression.evaluation_attempts must be a positive integer")
        minimum = values["minimum_level"]
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
            raise BattleConfigError(f"Combat move config {source}: skill_progression.minimum_level must be an integer of at least 1; level 0 is tutorial-only")
        promotion, demotion = values["promotion_average"], values["demotion_average"]
        if (isinstance(promotion, bool) or not isinstance(promotion, (int, float))
                or isinstance(demotion, bool) or not isinstance(demotion, (int, float))):
            raise BattleConfigError(f"Combat move config {source}: skill progression averages must be numbers")
        if not 0 <= float(demotion) <= 3 or not 0 <= float(promotion) <= 3:
            raise BattleConfigError(f"Combat move config {source}: skill progression averages must be between 0 and 3")
        if float(demotion) >= float(promotion):
            raise BattleConfigError(f"Combat move config {source}: demotion_average must be below promotion_average")
        return cls(attempts, float(promotion), float(demotion), minimum)


@dataclass
class MoveSkillState:
    current_level: int = 1
    recent_scores: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"current_level": self.current_level, "recent_scores": list(self.recent_scores)}


def _merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Deep merge mappings without mutating either authored YAML mapping."""
    merged = deepcopy(dict(base))
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _compile_qte_sections(resolved: dict[str, Any], move_id: str, level: int) -> None:
    """Flatten authored pattern/tuning sections for the QTE factory.

    ``pattern_parameters`` describe an attack's stable layout and seeded
    randomization.  ``tuning_parameters`` are the level-specific execution
    requirements.  The factory receives their already-resolved union through
    its established ``qte.parameters`` interface.
    """
    qte = resolved.get("qte")
    if not isinstance(qte, Mapping):
        return
    qte = dict(qte)
    sections = ("parameters", "pattern_parameters", "tuning_parameters")
    parameters: dict[str, Any] = {}
    for section in sections:
        values = qte.pop(section, {})
        if not isinstance(values, Mapping):
            raise _move_error(move_id, f"difficulty level {level} qte.{section} must be a mapping")
        parameters = _merge(parameters, values)
    qte["parameters"] = parameters
    resolved["qte"] = qte


def _move_error(move_id: str, message: str) -> BattleConfigError:
    return BattleConfigError(f"Combat move {move_id!r}: {message}")


def _difficulty_levels(move: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    """Return normalized authored levels, validating only the new schema.

    Definitions without ``difficulty_levels`` are retained as legacy single
    level definitions.  That compatibility path is intentional; a malformed
    definition *with* the new key never falls back to unrelated values.
    """
    move_id = str(move.get("id", "<unknown>"))
    if "difficulty_levels" not in move:
        return {1: {}}
    common = move.get("common")
    if not isinstance(common, Mapping):
        raise _move_error(move_id, "common must be a mapping when difficulty_levels is present")
    raw_levels = move.get("difficulty_levels")
    if not isinstance(raw_levels, Mapping):
        raise _move_error(move_id, "difficulty_levels must be a mapping with integer keys")
    levels: dict[int, dict[str, Any]] = {}
    for raw_level, values in raw_levels.items():
        if isinstance(raw_level, bool) or not isinstance(raw_level, int) or raw_level < 0:
            raise _move_error(move_id, f"difficulty level {raw_level!r} must be a non-negative integer")
        if not isinstance(values, Mapping):
            raise _move_error(move_id, f"difficulty level {raw_level} must be a mapping")
        levels[raw_level] = deepcopy(dict(values))
    if 1 not in levels:
        raise _move_error(move_id, "difficulty level 1 is required for normal adaptive play")
    if not any(level >= 1 for level in levels):
        raise _move_error(move_id, "requires at least one normal difficulty level")
    normal = sorted(level for level in levels if level >= 1)
    for expected, actual in enumerate(normal, start=1):
        if actual != expected:
            raise _move_error(move_id, f"difficulty level {expected} is missing; normal levels must be contiguous from 1")
    return levels


def normal_difficulty_levels(move: Mapping[str, Any]) -> tuple[int, ...]:
    """Return every configured adaptive (non-tutorial) difficulty level."""
    return tuple(sorted(level for level in _difficulty_levels(move) if level >= 1))


def highest_normal_difficulty(move: Mapping[str, Any]) -> int:
    return normal_difficulty_levels(move)[-1]


def initial_difficulty_level(move: Mapping[str, Any]) -> int:
    """Return an authored initial level, defaulting to normal level 1."""
    move_id = str(move.get("id", "<unknown>"))
    level = move.get("initial_level", 1)
    if isinstance(level, bool) or not isinstance(level, int):
        raise _move_error(move_id, "initial_level must be an integer")
    normal_levels = normal_difficulty_levels(move)
    if level not in normal_levels:
        raise _move_error(move_id, f"initial_level {level} is not a configured normal difficulty level")
    return level


def tutorial_records_skill(move: Mapping[str, Any]) -> bool:
    value = move.get("tutorial_records_skill", False)
    if not isinstance(value, bool):
        raise _move_error(str(move.get("id", "<unknown>")), "tutorial_records_skill must be true or false")
    return value


def resolve_combat_move(move: Mapping[str, Any], level: int) -> dict[str, Any]:
    """Return one complete move definition for a requested difficulty level.

    New-schema moves merge ``common`` with the selected level deeply, so a
    level can override just (for example) ``qte.parameters.target_radius``.
    Legacy definitions remain usable as their original level 1 only.
    """
    move_id = str(move.get("id", "<unknown>"))
    if isinstance(level, bool) or not isinstance(level, int) or level < 0:
        raise _move_error(move_id, f"difficulty level {level!r} must be a non-negative integer")
    levels = _difficulty_levels(move)
    if level not in levels:
        available = ", ".join(str(candidate) for candidate in sorted(levels)) or "none"
        raise _move_error(move_id, f"difficulty level {level} is not configured (available: {available})")
    if "difficulty_levels" not in move:
        resolved = deepcopy(dict(move))
    else:
        metadata = {
            key: deepcopy(value) for key, value in move.items()
            if key not in {"common", "difficulty_levels", "initial_level", "tutorial_records_skill"}
        }
        resolved = _merge(move["common"], levels[level])
        # Identity and availability are metadata; they are stable across QTE
        # difficulties and should not be duplicated in every level.
        resolved = _merge(metadata, resolved)
    resolved["_resolved_difficulty_level"] = level
    _compile_qte_sections(resolved, move_id, level)
    return resolved


def validate_move_skill_data(raw: Any, valid_levels: Mapping[str, tuple[int, ...]],
                              progression: SkillProgressionConfig,
                              initial_levels: Mapping[str, int] | None = None) -> dict[str, dict[str, Any]]:
    """Repair compatible saved skill data and discard only unknown moves.

    The save system historically accepts old/incomplete GameState mappings.
    We follow that strategy here: absent or malformed entries become safe
    defaults, while impossible entries never leak into the battle runtime.
    """
    entries = raw if isinstance(raw, Mapping) else {}
    initial_levels = initial_levels or {}
    result: dict[str, dict[str, Any]] = {}
    for move_id, levels in valid_levels.items():
        item = entries.get(move_id, {})
        if not isinstance(item, Mapping):
            item = {}
        current = item.get("current_level", initial_levels.get(move_id, 1))
        if isinstance(current, bool) or not isinstance(current, int) or current not in levels:
            current = 1 if 1 in levels else levels[0]
        scores = item.get("recent_scores", [])
        if not isinstance(scores, list) or any(isinstance(score, bool) or not isinstance(score, int) or score not in range(4)
                                                for score in scores):
            scores = []
        result[move_id] = {"current_level": current, "recent_scores": list(scores[-progression.evaluation_attempts:])}
    return result


class CombatMoveSkillTracker:
    """Own and evaluate saved adaptive skill state for the player's moves."""

    def __init__(self, game_state: GameState, progression: SkillProgressionConfig,
                 normal_levels: Mapping[str, tuple[int, ...]], initial_levels: Mapping[str, int] | None = None):
        self.game_state = game_state
        self.progression = progression
        self.normal_levels = {
            move_id: tuple(level for level in levels if level >= progression.minimum_level)
            for move_id, levels in normal_levels.items()
        }
        empty = [move_id for move_id, levels in self.normal_levels.items() if not levels]
        if empty:
            raise BattleConfigError(
                f"Combat move {empty[0]!r} has no normal difficulty at or above "
                f"skill_progression.minimum_level {progression.minimum_level}"
            )
        self.initial_levels = dict(initial_levels or {})
        self._repair_saved_states()

    def _repair_saved_states(self) -> None:
        valid_known = {move_id: self.normal_levels[move_id] for move_id in self.game_state.known_moves if move_id in self.normal_levels}
        repaired = validate_move_skill_data(self.game_state.known_combat_moves, valid_known, self.progression,
                                            self.initial_levels)
        # Drop removed/unknown move states just as absent unknown inventory
        # definitions are ignored by combat.  They never crash a load.
        self.game_state.known_combat_moves = repaired

    def state_for(self, move_id: str) -> MoveSkillState:
        if move_id not in self.normal_levels:
            raise KeyError(f"No configured combat move named {move_id!r}")
        raw = self.game_state.known_combat_moves.get(move_id)
        if raw is None:
            initial = self.initial_levels.get(move_id, 1)
            levels = self.normal_levels[move_id]
            raw = {"current_level": initial if initial in levels else levels[0], "recent_scores": []}
            self.game_state.known_combat_moves[move_id] = raw
        return MoveSkillState(int(raw["current_level"]), list(raw["recent_scores"]))

    def current_level(self, move_id: str) -> int:
        return self.state_for(move_id).current_level

    def record_result(self, move_id: str, result: str, *, tutorial: bool = False,
                      tutorial_records_skill: bool = False) -> MoveSkillState:
        """Record one completed result and apply at most one level change."""
        state = self.state_for(move_id)
        if tutorial and not tutorial_records_skill:
            return state
        state.recent_scores.append(result_score(result))
        state.recent_scores = state.recent_scores[-self.progression.evaluation_attempts:]
        if len(state.recent_scores) == self.progression.evaluation_attempts:
            average = sum(state.recent_scores) / self.progression.evaluation_attempts
            levels = self.normal_levels[move_id]
            index = levels.index(state.current_level)
            new_level = state.current_level
            if average >= self.progression.promotion_average and index < len(levels) - 1:
                new_level = levels[index + 1]
            elif average < self.progression.demotion_average and index > 0:
                new_level = levels[index - 1]
            if new_level != state.current_level:
                state.current_level = new_level
                state.recent_scores.clear()
        self.game_state.known_combat_moves[move_id] = state.to_dict()
        return state
