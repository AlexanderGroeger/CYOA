"""Battle YAML normalization and lightweight validation.

Fight data intentionally stays in one readable YAML file.  Validation occurs
when the fight is entered, so content errors mention the battle and field
instead of surfacing as an unrelated KeyError mid-turn.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from engine.battle.qte import QTE_TYPES, QTE_TYPE_ALIASES, canonical_qte_type
from engine.battle.defense import DefenseConfigError, validate_defense_sequence, validate_defense_sprites
from engine.battle.move_progression import (
    SkillProgressionConfig,
    highest_normal_difficulty,
    initial_difficulty_level,
    normal_difficulty_levels,
    resolve_combat_move,
    tutorial_records_skill,
)
from engine.errors import BattleConfigError


PLAYER_PATTERN_TYPES = QTE_TYPES | set(QTE_TYPE_ALIASES)
TIMELINE_ACTIONS = {"spawn", "spawn_repeated", "spawn_radial", "spawn_sweep", "spawn_rotating", "dialogue"}
DIALOGUE_TRIGGERS = {
    "battle_start", "before_player_action", "after_player_action",
    "before_enemy_pattern", "after_enemy_pattern", "phase_transition",
    "player_hit", "player_low_health", "enemy_low_health", "move_used", "item_used",
    "turn_start", "victory", "defeat",
}
# ``environment`` is a passive, single-line typewriter caption shown while
# the player is deciding what to do.  Unlike the other dialogue types, it
# does not put the battle into the DIALOGUE state.
DIALOGUE_TYPES = {"modal", "remark", "opponent", "environment"}
PLAYER_AUGMENT_FIELDS = {
    "base_power_add", "base_power_multiplier", "timing_window_multiplier",
    "perfect_threshold", "minimum_multiplier", "maximum_multiplier",
    "qte_speed_multiplier",
}
ENEMY_AUGMENT_FIELDS = {
    "projectile_speed_multiplier", "spawn_interval_multiplier",
    "projectile_count_add", "projectile_count_multiplier", "projectile_size_multiplier",
    "damage_add", "damage_multiplier", "duration_add", "duration_multiplier",
    "player_speed_multiplier", "arena_size_multiplier",
    "telegraph_duration_add", "telegraph_duration_multiplier",
}
PHASE_ACTIONS = {
    "add_enemy_move", "remove_enemy_move", "set_enemy_weight", "add_player_move",
    "remove_player_move", "replace_player_move", "augment_player_move",
    "augment_enemy_pattern", "augment_defense_sequence", "set_arena", "set_background", "set_enemy_sprite", "set_fight_flag",
}


@dataclass(frozen=True)
class EnemyDefinition:
    name: str
    hp: int
    attack: int
    defense: int
    sprite: str | None = None


@dataclass(frozen=True)
class GameOverConfig:
    """Presentation settings for a controller-owned final-loss sequence."""

    music: str
    text: str


@dataclass(frozen=True)
class OnLoseConfig:
    """Optional, controller-owned sequence that replaces a normal defeat."""

    type: str
    dialogue: tuple[str, ...] = ()
    enemy_message: str | None = None
    dialog_sound: str = "dialog_loud.wav"
    next_phase: str | None = None
    revived_hp: int | None = None
    repeatable: bool = False
    game_over: GameOverConfig | None = None


@dataclass(frozen=True)
class BattleConfig:
    id: str
    enemy: EnemyDefinition
    player_moves: dict[str, dict[str, Any]]
    player_move_levels: dict[str, tuple[int, ...]]
    skill_progression: SkillProgressionConfig
    enemy_moves: dict[str, dict[str, Any]]
    enemy_patterns: dict[str, dict[str, Any]]
    initial_player_moves: list[str]
    initial_enemy_moves: list[str]
    enemy_sequence: list[str]
    arena: dict[str, Any]
    dialogue: list[dict[str, Any]]
    phases: list[dict[str, Any]]
    escape_enabled: bool
    background: str | None
    music: str | None
    victory: dict[str, Any]
    defeat: dict[str, Any]
    on_lose: OnLoseConfig | None
    test_sequences_restore_hp: bool
    source: str
    legacy: bool = False

    @property
    def defense_sequences(self) -> dict[str, dict[str, Any]]:
        """Canonical name for enemy dodge sequences.

        ``enemy_patterns`` remains the stored field to preserve older Python
        integrations and phase-action syntax.
        """
        return self.enemy_patterns


def _error(source: str, message: str) -> BattleConfigError:
    return BattleConfigError(f"Battle config {source}: {message}")


def _require_mapping(value: Any, source: str, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _error(source, f"{field} must be a mapping")
    return value


def _positive_number(value: Any, source: str, field: str, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or (not allow_zero and value == 0):
        qualifier = "non-negative" if allow_zero else "positive"
        raise _error(source, f"{field} must be a {qualifier} number")
    return float(value)


def _unique_id_list(entries: Any, source: str, field: str) -> dict[str, dict[str, Any]]:
    if not isinstance(entries, list):
        raise _error(source, f"{field} must be a list")
    result: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(entries):
        item = _require_mapping(entry, source, f"{field}[{index}]")
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise _error(source, f"{field}[{index}].id must be a non-empty string")
        if identifier in result:
            raise _error(source, f"duplicate {field} id {identifier!r}")
        result[identifier] = deepcopy(item)
    return result


def _cutscene_dialogue_lines(value: Any, source: str, field: str, *, single: bool = False) -> tuple[str, ...]:
    """Validate the small dialogue shape used by battle cutscenes.

    Cutscene lines deliberately use the existing battle-dialogue ``text``
    convention.  ``speaker`` is retained as authoring metadata, ready for a
    renderer that displays speaker names, but the current dialogue panel is
    text-only just like regular modal battle dialogue.
    """
    if value is None:
        return ()
    entries = [value] if single else value
    if not isinstance(entries, list):
        expected = "a dialogue mapping" if single else "a list of dialogue mappings"
        raise _error(source, f"{field} must be {expected}")
    lines: list[str] = []
    for index, entry in enumerate(entries):
        item = _require_mapping(entry, source, f"{field}[{index}]")
        text = item.get("text")
        if not isinstance(text, str):
            raise _error(source, f"{field}[{index}].text must be a string")
        if "speaker" in item and (not isinstance(item["speaker"], str) or not item["speaker"]):
            raise _error(source, f"{field}[{index}].speaker must be a non-empty string")
        if text:
            lines.append(text)
    return tuple(lines)


def _load_on_lose(value: Any, source: str) -> OnLoseConfig | None:
    if value is None:
        return None
    data = _require_mapping(value, source, "on_lose")
    sequence_type = data.get("type")
    if sequence_type not in {"determined_revival", "game_over"}:
        raise _error(source, "on_lose.type must be 'determined_revival' or 'game_over'")
    game_over_data = data if sequence_type == "game_over" else data.get("game_over")
    game_over: GameOverConfig | None = None
    if game_over_data is not None:
        game_over_mapping = _require_mapping(game_over_data, source, "on_lose.game_over")
        music = game_over_mapping.get("music", "game_over.ogg")
        text = game_over_mapping.get("text", "Game over")
        if not isinstance(music, str) or not music:
            raise _error(source, "on_lose.game_over.music must be a non-empty asset filename")
        if not isinstance(text, str) or not text:
            raise _error(source, "on_lose.game_over.text must be a non-empty string")
        game_over = GameOverConfig(music, text)
    if sequence_type == "game_over":
        assert game_over is not None
        return OnLoseConfig(sequence_type, game_over=game_over)
    next_phase = data.get("next_phase")
    if not isinstance(next_phase, str) or not next_phase:
        raise _error(source, "on_lose.next_phase must be a non-empty phase id")
    revived_hp = data.get("revived_hp")
    if isinstance(revived_hp, bool) or not isinstance(revived_hp, int) or revived_hp <= 0:
        raise _error(source, "on_lose.revived_hp must be a positive integer")
    repeatable = data.get("repeatable", False)
    if not isinstance(repeatable, bool):
        raise _error(source, "on_lose.repeatable must be true or false")
    dialogue = _cutscene_dialogue_lines(data.get("dialogue", []), source, "on_lose.dialogue")
    enemy_lines = _cutscene_dialogue_lines(data.get("enemy_message"), source, "on_lose.enemy_message", single=True)
    dialog_sound = data.get("dialog_sound", "dialog_loud.wav")
    if not isinstance(dialog_sound, str) or not dialog_sound:
        raise _error(source, "on_lose.dialog_sound must be a non-empty asset filename")
    return OnLoseConfig(sequence_type, dialogue, enemy_lines[0] if enemy_lines else None, dialog_sound,
                        next_phase, revived_hp, repeatable, game_over)


def _normalise_legacy(data: dict[str, Any], source: str) -> dict[str, Any]:
    """Adapt the original attack/flee battle shape to the state machine.

    Old enemy moves keep their immediate range damage only in this adapter;
    newly authored patterns always use the interactive defense runtime.
    """
    result = deepcopy(data)
    if "player_moves" in result or "enemy_moves" in result:
        return result
    enemy = _require_mapping(result.get("enemy"), source, "enemy")
    legacy_moves = enemy.get("moves", [])
    if not isinstance(legacy_moves, list):
        raise _error(source, "enemy.moves must be a list")
    result["player_moves"] = [{
        "id": "basic_attack", "name": "Attack", "pattern": "timing_bar", "base_power": 1,
        "scoring": {"minimum_multiplier": 0.5, "maximum_multiplier": 1.0},
        "pattern_config": {"duration": 1.2, "target_position": 0.5, "perfect_window": 0.08, "good_window": 0.24},
    }]
    result["enemy_moves"] = []
    for index, move in enumerate(legacy_moves):
        move_data = _require_mapping(move, source, f"enemy.moves[{index}]")
        result["enemy_moves"].append({
            "id": f"legacy_enemy_move_{index}",
            "name": str(move_data.get("name", "Attack")), "weight": move_data.get("weight", 1),
            "legacy_damage": move_data.get("damage"), "legacy_effect": move_data.get("effect"),
        })
    result.setdefault("enemy_patterns", [])
    result["_legacy_adapter"] = True
    return result


def _normalise_defense_aliases(data: dict[str, Any], source: str) -> dict[str, Any]:
    """Accept clearer defend-system names without breaking old battle YAML."""
    result = deepcopy(data)
    canonical = result.get("defense_sequences")
    legacy = result.get("enemy_patterns")
    if canonical is not None and legacy is not None:
        raise _error(source, "use either defense_sequences or enemy_patterns, not both")
    if canonical is not None:
        result["enemy_patterns"] = canonical
    moves = result.get("enemy_moves")
    if isinstance(moves, list):
        normalised_moves: list[Any] = []
        for index, raw_move in enumerate(moves):
            if not isinstance(raw_move, dict):
                normalised_moves.append(raw_move)
                continue
            move = deepcopy(raw_move)
            defense_sequence = move.get("defense_sequence")
            if defense_sequence is not None:
                if "pattern" in move and move["pattern"] != defense_sequence:
                    raise _error(source, f"enemy_moves[{index}] pattern and defense_sequence disagree")
                move["pattern"] = defense_sequence
            normalised_moves.append(move)
        result["enemy_moves"] = normalised_moves
    return result


def _validate_items(items: dict[str, Any], source: str) -> None:
    for item_id, raw_item in items.items():
        item = _require_mapping(raw_item, source, f"items.{item_id}")
        combat = item.get("combat")
        if combat is None:
            continue
        combat = _require_mapping(combat, source, f"items.{item_id}.combat")
        if "usable" in combat and not isinstance(combat["usable"], bool):
            raise _error(source, f"items.{item_id}.combat.usable must be true or false")
        for effect_index, effect in enumerate(combat.get("effects", [])):
            effect = _require_mapping(effect, source, f"items.{item_id}.combat.effects[{effect_index}]")
            if len(effect) != 1:
                raise _error(source, f"items.{item_id}.combat.effects[{effect_index}] must have one effect")
            name, value = next(iter(effect.items()))
            if name not in {"heal", "damage_enemy", "set_fight_flag", "apply_effect", "remove_effect"}:
                raise _error(source, f"items.{item_id}.combat.effects[{effect_index}] has invalid effect {name!r}")
            if name in {"heal", "damage_enemy"}:
                _positive_number(value, source, f"items.{item_id}.combat.effects[{effect_index}].{name}", allow_zero=True)
            if name == "set_fight_flag" and not isinstance(value, dict):
                raise _error(source, f"items.{item_id}.combat.effects[{effect_index}].set_fight_flag must be a mapping")


def _validate_pattern(pattern_id: str, pattern: dict[str, Any], source: str) -> None:
    has_modern_patterns = "patterns" in pattern or "pattern_groups" in pattern
    if "duration" in pattern:
        duration = _positive_number(pattern.get("duration"), source, f"enemy_patterns.{pattern_id}.duration", allow_zero=True)
    elif has_modern_patterns:
        duration = 0.0
    else:
        duration = _positive_number(pattern.get("duration"), source, f"enemy_patterns.{pattern_id}.duration", allow_zero=True)
    if "attack_delay" in pattern:
        _positive_number(pattern["attack_delay"], source, f"enemy_patterns.{pattern_id}.attack_delay", allow_zero=True)
    arena = pattern.get("arena", {})
    if arena:
        arena = _require_mapping(arena, source, f"enemy_patterns.{pattern_id}.arena")
        for key in ("width", "height"):
            if key in arena:
                _positive_number(arena[key], source, f"enemy_patterns.{pattern_id}.arena.{key}")
    timeline = pattern.get("timeline", [])
    if not isinstance(timeline, list):
        raise _error(source, f"enemy_patterns.{pattern_id}.timeline must be a list")
    for index, event in enumerate(timeline):
        event = _require_mapping(event, source, f"enemy_patterns.{pattern_id}.timeline[{index}]")
        _positive_number(event.get("at", 0), source, f"enemy_patterns.{pattern_id}.timeline[{index}].at", allow_zero=True)
        action = event.get("action")
        if action not in TIMELINE_ACTIONS:
            raise _error(source, f"enemy_patterns.{pattern_id}.timeline[{index}].action {action!r} is unsupported")
        if action in {"spawn", "spawn_repeated", "spawn_radial", "spawn_sweep", "spawn_rotating"} and not isinstance(event.get("projectile"), dict):
            raise _error(source, f"enemy_patterns.{pattern_id}.timeline[{index}] needs a projectile mapping")
        projectile = event.get("projectile", {})
        if isinstance(projectile, dict):
            for numeric_key in ("lifetime", "delay", "damage"):
                if numeric_key in projectile:
                    _positive_number(projectile[numeric_key], source, f"enemy_patterns.{pattern_id}.timeline[{index}].projectile.{numeric_key}", allow_zero=True)
            if "size" in projectile and not isinstance(projectile["size"], (int, float, list, tuple)):
                raise _error(source, f"enemy_patterns.{pattern_id}.timeline[{index}].projectile.size must be numeric or [width, height]")
        if action in {"spawn_repeated", "spawn_sweep", "spawn_rotating"}:
            repeat = _require_mapping(event.get("repeat"), source, f"enemy_patterns.{pattern_id}.timeline[{index}].repeat")
            count = repeat.get("count")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise _error(source, f"enemy_patterns.{pattern_id}.timeline[{index}].repeat.count must be non-negative")
            _positive_number(repeat.get("interval"), source, f"enemy_patterns.{pattern_id}.timeline[{index}].repeat.interval", allow_zero=True)
        if action == "spawn_radial":
            count = event.get("count")
            if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
                raise _error(source, f"enemy_patterns.{pattern_id}.timeline[{index}].count must be positive")
    if duration < 0:  # Keeps the local variable meaningful for static readers.
        raise _error(source, f"enemy_patterns.{pattern_id}.duration is invalid")
    try:
        validate_defense_sequence(pattern, f"enemy_patterns.{pattern_id}")
    except DefenseConfigError as exc:
        raise _error(source, str(exc)) from exc


def _qte_number(value: Any, source: str, field: str, minimum: float | None = None,
                maximum: float | None = None, allow_zero: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _error(source, f"{field} must be a number")
    number = float(value)
    if (not allow_zero and number == 0) or (minimum is not None and number < minimum) or (maximum is not None and number > maximum):
        if minimum is not None and maximum is not None:
            expected = f"between {minimum} and {maximum}"
        elif minimum is not None:
            expected = f"at least {minimum}"
        else:
            expected = f"at most {maximum}"
        raise _error(source, f"{field} must be {expected}")
    return number


def _validate_qte(move_id: str, move: dict[str, Any], source: str) -> None:
    """Validate modern ``qte`` blocks and legacy pattern aliases alike."""
    qte_raw = move.get("qte")
    modern = qte_raw is not None
    if modern:
        qte = _require_mapping(qte_raw, source, f"player_moves.{move_id}.qte")
        raw_type = qte.get("type")
        type_field = f"player_moves.{move_id}.qte.type"
        if not isinstance(raw_type, str) or not raw_type:
            raise _error(source, f"{type_field} must be a non-empty string")
        qte_type = canonical_qte_type(raw_type)
        parameters = _require_mapping(qte.get("parameters", {}), source, f"player_moves.{move_id}.qte.parameters")
        # Concise QTE data may put type-specific fields beside ``parameters``.
        reserved = {"type", "duration", "difficulty", "thresholds", "damage_multipliers", "label", "sound", "animation", "allowed_inputs", "parameters"}
        parameters = {**parameters, **{key: value for key, value in qte.items() if key not in reserved}}
        duration = qte.get("duration", parameters.get("duration"))
        prefix = f"player_moves.{move_id}.qte"
        thresholds = qte.get("thresholds", {})
        multipliers = qte.get("damage_multipliers", {})
    else:
        raw_type = move.get("pattern")
        type_field = f"player_moves.{move_id}.pattern"
        qte_type = canonical_qte_type(raw_type) if isinstance(raw_type, str) else ""
        parameters = _require_mapping(move.get("pattern_config", {}), source, f"player_moves.{move_id}.pattern_config")
        duration = parameters.get("duration")
        prefix = f"player_moves.{move_id}.pattern_config"
        thresholds = move.get("thresholds", {})
        multipliers = move.get("damage_multipliers", {})
    if qte_type not in QTE_TYPES:
        raise _error(source, f"{type_field} {raw_type!r} is unknown")
    if not modern and raw_type == "timing_sequence":
        stages = parameters.get("stages")
        if not isinstance(stages, list):
            raise _error(source, f"{prefix}.stages must be a list")
        for stage_index, stage in enumerate(stages):
            stage = _require_mapping(stage, source, f"{prefix}.stages[{stage_index}]")
            _positive_number(stage.get("duration", 1), source, f"{prefix}.stages[{stage_index}].duration")
    if not modern and raw_type == "timing_bar" and duration is None:
        # Preserve the original schema's explicit timing-bar duration rule.
        _positive_number(duration, source, f"{prefix}.duration")
    if duration is not None:
        _positive_number(duration, source, f"{prefix}.duration")
    difficulty = (qte_raw or {}).get("difficulty", move.get("difficulty", "normal")) if modern else move.get("difficulty", "normal")
    if difficulty not in {"easy", "normal", "hard"}:
        raise _error(source, f"{prefix}.difficulty must be easy, normal, or hard")
    for field_name, value in (("label", (qte_raw or {}).get("label") if modern else move.get("qte_label")),
                              ("sound", (qte_raw or {}).get("sound") if modern else move.get("qte_sound")),
                              ("animation", (qte_raw or {}).get("animation") if modern else move.get("qte_animation"))):
        if value is not None and not isinstance(value, str):
            raise _error(source, f"{prefix}.{field_name} must be a string")
    if modern and "allowed_inputs" in qte_raw:
        allowed = qte_raw["allowed_inputs"]
        if not isinstance(allowed, list) or any(value not in {"SELECT", "UP", "DOWN", "LEFT", "RIGHT"} for value in allowed):
            raise _error(source, f"{prefix}.allowed_inputs must contain supported engine actions")
    thresholds = _require_mapping(thresholds, source, f"{prefix}.thresholds")
    threshold_values: dict[str, float] = {}
    for name in ("weak", "strong", "critical"):
        if name in thresholds:
            threshold_values[name] = _qte_number(thresholds[name], source, f"{prefix}.thresholds.{name}", 0, 1)
    merged = {"weak": 0.25, "strong": 0.70, "critical": 0.95}
    merged.update(threshold_values)
    if not (merged["weak"] < merged["strong"] < merged["critical"]):
        raise _error(source, f"{prefix}.thresholds must satisfy weak < strong < critical")
    multipliers = _require_mapping(multipliers, source, f"{prefix}.damage_multipliers")
    for tier, value in multipliers.items():
        if tier not in {"miss", "weak", "strong", "critical"}:
            raise _error(source, f"{prefix}.damage_multipliers.{tier} is not a result tier")
        _qte_number(value, source, f"{prefix}.damage_multipliers.{tier}", 0)

    def bounded(name: str, low: float = 0, high: float = 1, zero: bool = True) -> None:
        if name in parameters:
            _qte_number(parameters[name], source, f"{prefix}.{name}", low, high, zero)

    def positive(name: str) -> None:
        if name in parameters:
            _positive_number(parameters[name], source, f"{prefix}.{name}")

    if qte_type == "precision_bar":
        bounded("target_position")
        if "target_position_range" in parameters:
            target_range = parameters["target_position_range"]
            if (not isinstance(target_range, list) or len(target_range) != 2
                    or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1
                           for value in target_range)
                    or target_range[0] > target_range[1]):
                raise _error(source, f"{prefix}.target_position_range must be [minimum, maximum] within 0..1")
        for name in ("critical_window", "strong_window", "weak_window"):
            bounded(name)
        windows = [parameters.get(name) for name in ("critical_window", "strong_window", "weak_window") if name in parameters]
        if len(windows) == 3 and not windows[0] <= windows[1] <= windows[2]:
            raise _error(source, f"{prefix}.critical_window, strong_window, and weak_window must be ascending")
        positive("speed_multiplier")
    elif qte_type == "charge_release":
        for name in ("charge_step_degrees", "charge_step_decrement_degrees", "minimum_charge_step_degrees",
                     "arc_start_min_degrees", "arc_start_max_degrees",
                     "weak_arc_width_degrees", "strong_arc_width_degrees", "critical_arc_width_degrees"):
            if name in parameters:
                _qte_number(parameters[name], source, f"{prefix}.{name}", 0, 180, False)
        positive("release_delay_seconds")
        positive("swing_duration_seconds")
        positive("charge_tween_duration_seconds")
        for name in ("release_strike_arc_start_degrees", "release_strike_arc_end_degrees"):
            if name in parameters:
                _qte_number(parameters[name], source, f"{prefix}.{name}", 0, 180)
        if ("charge_step_degrees" in parameters and "minimum_charge_step_degrees" in parameters
                and parameters["minimum_charge_step_degrees"] > parameters["charge_step_degrees"]):
            raise _error(source, f"{prefix}.minimum_charge_step_degrees must not exceed charge_step_degrees")
        if ("release_strike_arc_start_degrees" in parameters and "release_strike_arc_end_degrees" in parameters
                and parameters["release_strike_arc_start_degrees"] > parameters["release_strike_arc_end_degrees"]):
            raise _error(source, f"{prefix}.release_strike_arc_start_degrees must not exceed release_strike_arc_end_degrees")
        arc_start_min = float(parameters.get("arc_start_min_degrees", 100))
        arc_start_max = float(parameters.get("arc_start_max_degrees", 130))
        arc_width = sum(float(parameters.get(name, default)) for name, default in (
            ("weak_arc_width_degrees", 30), ("strong_arc_width_degrees", 10),
            ("critical_arc_width_degrees", 5),
        ))
        if arc_start_min > arc_start_max:
            raise _error(source, f"{prefix}.arc_start_min_degrees must not exceed arc_start_max_degrees")
        if arc_start_max + arc_width > 180:
            raise _error(source, f"{prefix}.scoring arcs must fit within the 180-degree meter")
    elif qte_type == "shrinking_ring":
        positive("starting_radius")
        positive("target_radius")
        for name in ("target_x", "target_y", "ring_x", "ring_y", "target_x_variance", "target_y_variance"):
            bounded(name)
        positive("ring_min_distance")
        positive("movement_speed")
        if "collapse_hold" in parameters:
            _positive_number(parameters["collapse_hold"], source, f"{prefix}.collapse_hold", allow_zero=True)
        for name in ("critical_tolerance", "strong_tolerance", "weak_tolerance"):
            positive(name)
        if "contraction_curve" in parameters and parameters["contraction_curve"] != "linear":
            raise _error(source, f"{prefix}.contraction_curve must be linear")
    elif qte_type == "rotating_strike":
        bounded("target_angle", 0, 360)
        if "target_angle_range" in parameters:
            target_range = parameters["target_angle_range"]
            if (not isinstance(target_range, list) or len(target_range) != 2
                    or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 360
                           for value in target_range)
                    or target_range[0] > target_range[1]):
                raise _error(source, f"{prefix}.target_angle_range must be [minimum, maximum] within 0..360")
        positive("rotations")
        for name in ("critical_window", "strong_window", "weak_window"):
            bounded(name, 0, 180, False)
    elif qte_type == "directional_combo":
        # ``prompts``/``prompt_count``/``response_window`` remain accepted
        # for older move files.  The current pattern uses a moving target and
        # does not interpret them as an ordered key sequence.
        if "prompts" in parameters:
            prompts = parameters["prompts"]
            if not isinstance(prompts, list) or not prompts or any(prompt not in {"UP", "DOWN", "LEFT", "RIGHT"} for prompt in prompts):
                raise _error(source, f"{prefix}.prompts must be a non-empty list of directional actions")
        if "prompt_count" in parameters and (isinstance(parameters["prompt_count"], bool) or not isinstance(parameters["prompt_count"], int) or parameters["prompt_count"] <= 0):
            raise _error(source, f"{prefix}.prompt_count must be a positive integer")
        positive("response_window")
        if "required_hits" in parameters and (isinstance(parameters["required_hits"], bool) or not isinstance(parameters["required_hits"], int) or parameters["required_hits"] < 1):
            raise _error(source, f"{prefix}.required_hits must be a positive integer")
        positive("initial_speed")
        if "speed_increase" in parameters:
            _qte_number(parameters["speed_increase"], source, f"{prefix}.speed_increase", 0)
        if "max_speed_multiplier" in parameters:
            _qte_number(parameters["max_speed_multiplier"], source, f"{prefix}.max_speed_multiplier", 1)
        if "strong_threshold_ratio" in parameters:
            _qte_number(parameters["strong_threshold_ratio"], source, f"{prefix}.strong_threshold_ratio", 0, 1, False)
        positive("striking_region_size")
        if "striking_region_inset" in parameters:
            _qte_number(parameters["striking_region_inset"], source, f"{prefix}.striking_region_inset", 0, .5, True)
        positive("target_radius")
        for name in ("strike_flash_duration", "final_critical_pause"):
            if name in parameters:
                _qte_number(parameters[name], source, f"{prefix}.{name}", 0)
        if {"striking_region_size", "striking_region_inset", "target_radius"} & set(parameters):
            region_size = float(parameters.get("striking_region_size", .18))
            inset = float(parameters.get("striking_region_inset", .07))
            radius = float(parameters.get("target_radius", .025))
            maximum_size = min((.5 - inset) / 1.5, .5 - inset - radius)
            if maximum_size <= 0 or region_size > maximum_size:
                raise _error(source, f"{prefix}.striking_region_size does not fit the inset and target radius without overlapping regions")
    elif qte_type == "rapid_slash":
        if "block_count" in parameters and (isinstance(parameters["block_count"], bool) or not isinstance(parameters["block_count"], int) or parameters["block_count"] <= 0):
            raise _error(source, f"{prefix}.block_count must be a positive integer")
        positive("block_fall_speed")
        bounded("block_height", 0, 1, False)
        bounded("block_width", 0, 1, False)
        positive("minimum_half_height")
        if "block_spacing" in parameters:
            spacing = parameters["block_spacing"]
            spacing_values = spacing if isinstance(spacing, list) else [spacing]
            if not spacing_values:
                raise _error(source, f"{prefix}.block_spacing must be a non-empty number or list of numbers")
            for index, value in enumerate(spacing_values):
                suffix = f"[{index}]" if isinstance(spacing, list) else ""
                _qte_number(value, source, f"{prefix}.block_spacing{suffix}", 0)
        for name in ("block_horizontal_offset", "half_separation_speed", "cut_gravity", "cut_horizontal_speed"):
            if name in parameters:
                _qte_number(parameters[name], source, f"{prefix}.{name}", 0)
        if "slash_animation_duration" in parameters:
            _positive_number(parameters["slash_animation_duration"], source, f"{prefix}.slash_animation_duration", allow_zero=True)
        positive("slash_region_height")
        bounded("slash_region_vertical_position")
        if {"slash_region_height", "slash_region_vertical_position"} & set(parameters):
            region_height = float(parameters.get("slash_region_height", .12))
            region_position = float(parameters.get("slash_region_vertical_position", .72))
            if region_height > 1 or region_position - region_height / 2 < 0 or region_position + region_height / 2 > 1:
                raise _error(source, f"{prefix}.slash_region must fit within the attack window")
        if "strong_threshold" in parameters and (isinstance(parameters["strong_threshold"], bool) or not isinstance(parameters["strong_threshold"], int) or parameters["strong_threshold"] <= 0):
            raise _error(source, f"{prefix}.strong_threshold must be a positive integer")
        if ("strong_threshold" in parameters and "block_count" in parameters
                and parameters["strong_threshold"] > parameters["block_count"]):
            raise _error(source, f"{prefix}.strong_threshold must not exceed block_count")
        if ("minimum_half_height" in parameters and "block_height" in parameters
                and parameters["minimum_half_height"] * 2 > parameters["block_height"]):
            raise _error(source, f"{prefix}.minimum_half_height must fit twice within block_height")
        for name in ("hit_sound_pitch_progression", "hit_sound_pitch_progression_enabled"):
            if name in parameters and not isinstance(parameters[name], bool):
                raise _error(source, f"{prefix}.{name} must be a boolean")
    elif qte_type == "rhythm_combo":
        if "beats" in parameters:
            beats = parameters["beats"]
            if not isinstance(beats, list) or not beats:
                raise _error(source, f"{prefix}.beats must be a non-empty list of seconds")
            for index, beat in enumerate(beats):
                _positive_number(beat, source, f"{prefix}.beats[{index}]")
        if "beat_count" in parameters and (isinstance(parameters["beat_count"], bool) or not isinstance(parameters["beat_count"], int) or parameters["beat_count"] <= 0):
            raise _error(source, f"{prefix}.beat_count must be a positive integer")
        positive("tolerance")
        positive("approach_speed")
        positive("fade_duration")
    elif qte_type == "moving_weak_point":
        for name in ("target_x", "target_y", "target_radius", "reticle_x", "reticle_y", "launch_x", "launch_y",
                     "target_y_variance", "launch_x_variance"):
            bounded(name)
        for name in ("critical_radius", "strong_radius"):
            if name in parameters:
                _qte_number(parameters[name], source, f"{prefix}.{name}", 0, 1, False)
        if "critical_radius" in parameters and "strong_radius" in parameters and parameters["critical_radius"] > parameters["strong_radius"]:
            raise _error(source, f"{prefix}.critical_radius must not exceed strong_radius")
        if "strong_radius" in parameters and "target_radius" in parameters and parameters["strong_radius"] > parameters["target_radius"]:
            raise _error(source, f"{prefix}.strong_radius must not exceed target_radius")
        if "critical_radius" in parameters and "target_radius" in parameters and parameters["critical_radius"] > parameters["target_radius"]:
            raise _error(source, f"{prefix}.critical_radius must not exceed target_radius")
        positive("speed")
        positive("target_speed")
        positive("aim_speed")
        positive("arrow_speed")
        if "aim_angle" in parameters:
            _qte_number(parameters["aim_angle"], source, f"{prefix}.aim_angle", -180, 0)
        if "impact_hold" in parameters:
            _positive_number(parameters["impact_hold"], source, f"{prefix}.impact_hold", allow_zero=True)
    elif qte_type == "stability":
        positive("force")
        positive("correction_speed")
        bounded("center_width", 0, 1, False)


def _validate_phase(phase: dict[str, Any], index: int, config: "BattleConfig", items: dict[str, Any]) -> None:
    source = config.source
    phase_id = phase.get("id", f"phase[{index}]")
    when = _require_mapping(phase.get("when"), source, f"phases[{index}].when")
    allowed_when = {"enemy_hp_below", "enemy_hp_ratio_lte", "player_hp_below", "turn_at_least", "move_used", "item_used", "fight_flag", "previous_phase"}
    invalid = set(when) - allowed_when
    if invalid:
        raise _error(source, f"phases[{index}] ({phase_id}) has unsupported condition {sorted(invalid)[0]!r}")
    if "enemy_hp_ratio_lte" in when and not 0 <= float(when["enemy_hp_ratio_lte"]) <= 1:
        raise _error(source, f"phases[{index}].when.enemy_hp_ratio_lte must be between 0 and 1")
    if "move_used" in when and when["move_used"] not in config.player_moves:
        raise _error(source, f"phases[{index}].when.move_used references missing move {when['move_used']!r}")
    if "item_used" in when and when["item_used"] not in items:
        raise _error(source, f"phases[{index}].when.item_used references missing item {when['item_used']!r}")
    actions = phase.get("actions", [])
    if not isinstance(actions, list):
        raise _error(source, f"phases[{index}].actions must be a list")
    for action_index, action in enumerate(actions):
        action = _require_mapping(action, source, f"phases[{index}].actions[{action_index}]")
        if len(action) != 1:
            raise _error(source, f"phases[{index}].actions[{action_index}] must have one action")
        name, value = next(iter(action.items()))
        if name not in PHASE_ACTIONS:
            raise _error(source, f"phases[{index}].actions[{action_index}] unknown action {name!r}")
        if name in {"add_enemy_move", "remove_enemy_move"} and value not in config.enemy_moves:
            raise _error(source, f"phases[{index}].actions[{action_index}] references missing enemy move {value!r}")
        if name in {"add_player_move", "remove_player_move"} and value not in config.player_moves:
            raise _error(source, f"phases[{index}].actions[{action_index}] references missing player move {value!r}")
        if name == "replace_player_move":
            value = _require_mapping(value, source, f"phases[{index}].actions[{action_index}].replace_player_move")
            if value.get("old") not in config.player_moves or value.get("new") not in config.player_moves:
                raise _error(source, f"phases[{index}].actions[{action_index}].replace_player_move references missing move")
        if name == "set_enemy_weight":
            value = _require_mapping(value, source, f"phases[{index}].actions[{action_index}].set_enemy_weight")
            if value.get("move") not in config.enemy_moves:
                raise _error(source, f"phases[{index}].actions[{action_index}].set_enemy_weight references missing enemy move")
            _positive_number(value.get("weight"), source, f"phases[{index}].actions[{action_index}].set_enemy_weight.weight", allow_zero=True)
        if name == "set_arena":
            value = _require_mapping(value, source, f"phases[{index}].actions[{action_index}].set_arena")
            for dimension in ("width", "height"):
                if dimension in value:
                    _positive_number(value[dimension], source, f"phases[{index}].actions[{action_index}].set_arena.{dimension}")
        if name in {"set_background", "set_enemy_sprite"} and (not isinstance(value, str) or not value):
            raise _error(source, f"phases[{index}].actions[{action_index}].{name} must be a non-empty asset filename")
        if name in {"augment_player_move", "augment_enemy_pattern", "augment_defense_sequence"}:
            value = _require_mapping(value, source, f"phases[{index}].actions[{action_index}].{name}")
            target_key = "move" if name == "augment_player_move" else ("sequence" if name == "augment_defense_sequence" else "pattern")
            collection = config.player_moves if name == "augment_player_move" else config.enemy_patterns
            allowed_fields = PLAYER_AUGMENT_FIELDS if name == "augment_player_move" else ENEMY_AUGMENT_FIELDS
            if value.get(target_key) not in collection:
                raise _error(source, f"phases[{index}].actions[{action_index}] references missing {target_key} {value.get(target_key)!r}")
            fields = _require_mapping(value.get("fields"), source, f"phases[{index}].actions[{action_index}].fields")
            unknown = set(fields) - allowed_fields
            if unknown:
                raise _error(source, f"phases[{index}].actions[{action_index}] unsupported augmentation field {sorted(unknown)[0]!r}")


def load_battle_config(data: dict[str, Any], items: dict[str, Any] | None = None, source: str = "<battle>",
                       moves: list[dict[str, Any]] | dict[str, Any] | None = None,
                       skill_progression: dict[str, Any] | None = None,
                       sprite_exists: Callable[[str], bool] | None = None) -> BattleConfig:
    """Validate YAML data and return the runtime-friendly battle definition."""
    if not isinstance(data, dict):
        raise _error(source, "root must be a mapping")
    raw = _normalise_defense_aliases(_normalise_legacy(data, source), source)
    item_data = items or {}
    _validate_items(item_data, source)
    identifier = raw.get("id")
    if not isinstance(identifier, str) or not identifier:
        raise _error(source, "id must be a non-empty string")
    enemy_raw = _require_mapping(raw.get("enemy"), source, "enemy")
    name, hp = enemy_raw.get("name"), enemy_raw.get("hp")
    if not isinstance(name, str) or not name:
        raise _error(source, "enemy.name must be a non-empty string")
    _positive_number(hp, source, "enemy.hp")
    sprite = enemy_raw.get("sprite")
    if sprite is not None and (not isinstance(sprite, str) or not sprite):
        raise _error(source, "enemy.sprite must be a non-empty string")
    enemy = EnemyDefinition(name, int(hp), int(enemy_raw.get("attack", 1)), int(enemy_raw.get("defense", 0)), sprite)
    move_entries = moves
    if isinstance(moves, dict) and "moves" in moves:
        move_entries = moves.get("moves")
        if skill_progression is None:
            skill_progression = moves.get("skill_progression")
    if moves is not None and not raw.get("_legacy_adapter") and "player_moves" in raw:
        raise _error(source, "player_moves belong in the story moves/ folder, not a battle file")
    if moves is not None and not raw.get("_legacy_adapter") and "initial_player_moves" in raw:
        raise _error(source, "initial_player_moves belong in player.yaml, not a battle file")
    player_move_entries = raw.get("player_moves", []) if moves is None or raw.get("_legacy_adapter") else move_entries
    player_moves = _unique_id_list(player_move_entries, source, "player_moves")
    if not player_moves:
        raise _error(source, "requires at least one player move")
    player_move_levels: dict[str, tuple[int, ...]] = {}
    for move_id, move in player_moves.items():
        try:
            levels = normal_difficulty_levels(move)
            # Validate every authored level after it is resolved through the
            # one shared merge path.  This makes broken nested overrides name
            # both their move and exact difficulty level.
            for level in sorted({*levels, *({0} if "difficulty_levels" in move and 0 in move["difficulty_levels"] else set())}):
                try:
                    resolved = resolve_combat_move(move, level)
                    _positive_number(resolved.get("base_power", 0), source,
                                     f"player_moves.{move_id}.difficulty_levels.{level}.base_power", allow_zero=True)
                    _validate_qte(move_id, resolved, source)
                except BattleConfigError as error:
                    raise _error(source, f"combat move {move_id!r} difficulty level {level} is invalid: {error}") from error
            initial_difficulty_level(move)
            tutorial_records_skill(move)
        except BattleConfigError as error:
            raise _error(source, f"combat move {move_id!r} difficulty configuration is invalid: {error}") from error
        player_move_levels[move_id] = levels
        availability = move.get("availability", move.get("common", {}).get("availability", {}))
        if not isinstance(availability, dict):
            raise _error(source, f"player_moves.{move_id}.availability must be a mapping")
        if "weapons" in availability:
            raise _error(source, f"player_moves.{move_id}.availability.weapons is no longer supported; list moves in a weapon's combat.move_grants instead")
    for move_id, move in player_moves.items():
        availability = move.get("availability", move.get("common", {}).get("availability", {}))
        requirements = availability.get("requires_moves_used", [])
        if not isinstance(requirements, list):
            raise _error(source, f"player_moves.{move_id}.availability.requires_moves_used must be a list")
        for required_move in requirements:
            if required_move not in player_moves:
                raise _error(source, f"player_moves.{move_id} references missing previous move {required_move!r}")
    if moves is not None and not raw.get("_legacy_adapter"):
        for item_id, item in item_data.items():
            grants = item.get("combat", {}).get("move_grants")
            if grants is None:
                continue
            if not isinstance(grants, list) or not all(isinstance(move_id, str) for move_id in grants):
                raise _error(source, f"items.{item_id}.combat.move_grants must be a list of move ids")
            for move_id in grants:
                if move_id not in player_moves:
                    raise _error(source, f"items.{item_id}.combat.move_grants references missing move {move_id!r}")
    enemy_patterns = _unique_id_list(raw.get("enemy_patterns", []), source, "enemy_patterns")
    for pattern_id, pattern in enemy_patterns.items():
        _validate_pattern(pattern_id, pattern, source)
        if sprite_exists is not None:
            try:
                validate_defense_sprites(pattern, sprite_exists, f"enemy_patterns.{pattern_id}")
            except DefenseConfigError as exc:
                raise _error(source, str(exc)) from exc
    enemy_moves = _unique_id_list(raw.get("enemy_moves", []), source, "enemy_moves")
    if not enemy_moves:
        raise _error(source, "requires at least one enemy move")
    for move_id, move in enemy_moves.items():
        _positive_number(move.get("weight", 1), source, f"enemy_moves.{move_id}.weight", allow_zero=True)
        cooldown = move.get("cooldown", 0)
        if isinstance(cooldown, bool) or not isinstance(cooldown, int) or cooldown < 0:
            raise _error(source, f"enemy_moves.{move_id}.cooldown must be a non-negative integer")
        availability = move.get("availability", {})
        if not isinstance(availability, dict):
            raise _error(source, f"enemy_moves.{move_id}.availability must be a mapping")
        if "phases" in availability and not isinstance(availability["phases"], list):
            raise _error(source, f"enemy_moves.{move_id}.availability.phases must be a list")
        if "pattern" in move and move["pattern"] not in enemy_patterns:
            raise _error(source, f"enemy_moves.{move_id}.pattern references missing pattern {move['pattern']!r}")
        if "pattern" not in move and "legacy_damage" not in move and "legacy_effect" not in move:
            raise _error(source, f"enemy_moves.{move_id} requires a pattern")
        if "defense_difficulty" in move and (isinstance(move["defense_difficulty"], bool)
                                                or not isinstance(move["defense_difficulty"], (str, int))):
            raise _error(source, f"enemy_moves.{move_id}.defense_difficulty must be a string or integer")
    arena = deepcopy(raw.get("arena", {"x": 210, "y": 145, "width": 220, "height": 110, "player_speed": 120}))
    arena = _require_mapping(arena, source, "arena")
    for key in ("width", "height"):
        _positive_number(arena.get(key), source, f"arena.{key}")
    _positive_number(arena.get("player_speed", 120), source, "arena.player_speed")
    initial_player = list(raw.get("initial_player_moves", player_moves.keys()))
    initial_enemy = list(raw.get("initial_enemy_moves", enemy_moves.keys()))
    enemy_sequence = raw.get("enemy_sequence", [])
    if not isinstance(enemy_sequence, list):
        raise _error(source, "enemy_sequence must be a list")
    for move_id in initial_player:
        if move_id not in player_moves:
            raise _error(source, f"initial_player_moves references missing move {move_id!r}")
    for move_id in initial_enemy:
        if move_id not in enemy_moves:
            raise _error(source, f"initial_enemy_moves references missing move {move_id!r}")
    for move_id in enemy_sequence:
        if move_id not in enemy_moves:
            raise _error(source, f"enemy_sequence references missing move {move_id!r}")
    dialogue = raw.get("dialogue", [])
    if not isinstance(dialogue, list):
        raise _error(source, "dialogue must be a list")
    for index, entry in enumerate(dialogue):
        entry = _require_mapping(entry, source, f"dialogue[{index}]")
        if entry.get("trigger") not in DIALOGUE_TRIGGERS:
            raise _error(source, f"dialogue[{index}].trigger {entry.get('trigger')!r} is invalid")
        if not isinstance(entry.get("text", entry.get("pool")), (str, list)):
            raise _error(source, f"dialogue[{index}] needs text or pool")
        dialogue_type = entry.get("type", "modal")
        if dialogue_type not in DIALOGUE_TYPES:
            raise _error(source, f"dialogue[{index}].type {dialogue_type!r} is invalid")
        if "pause" in entry:
            _positive_number(entry["pause"], source, f"dialogue[{index}].pause", allow_zero=True)
    escape_data = raw.get("escape", {})
    if isinstance(escape_data, bool):
        escape_enabled = escape_data
    elif isinstance(escape_data, dict):
        escape_enabled = bool(escape_data.get("enabled", False))
    else:
        raise _error(source, "escape must be a mapping or boolean")
    on_lose = _load_on_lose(raw.get("on_lose"), source)
    test_sequences_restore_hp = raw.get("test_sequences_restore_hp", True)
    if not isinstance(test_sequences_restore_hp, bool):
        raise _error(source, "test_sequences_restore_hp must be a boolean")
    progression = SkillProgressionConfig.from_data(skill_progression, source)
    for move_id, levels in player_move_levels.items():
        if progression.minimum_level not in levels:
            raise _error(source, f"combat move {move_id!r} does not define configured minimum_level {progression.minimum_level}")
    config = BattleConfig(identifier, enemy, player_moves, player_move_levels, progression, enemy_moves, enemy_patterns, initial_player, initial_enemy, list(enemy_sequence),
                          arena, deepcopy(dialogue), deepcopy(raw.get("phases", [])), escape_enabled,
                          raw.get("background"), raw.get("music"), deepcopy(raw.get("victory", {})), deepcopy(raw.get("defeat", {})),
                          on_lose, test_sequences_restore_hp, source, bool(raw.get("_legacy_adapter")))
    if not isinstance(config.phases, list):
        raise _error(source, "phases must be a list")
    seen_phases: set[str] = set()
    for index, phase in enumerate(config.phases):
        phase = _require_mapping(phase, source, f"phases[{index}]")
        phase_id = phase.get("id", f"phase_{index}")
        if phase_id in seen_phases:
            raise _error(source, f"duplicate phase id {phase_id!r}")
        seen_phases.add(phase_id)
        _validate_phase(phase, index, config, item_data)
    if (config.on_lose is not None and config.on_lose.type == "determined_revival"
            and config.on_lose.next_phase not in seen_phases):
        raise _error(source, f"on_lose.next_phase references missing phase {config.on_lose.next_phase!r}")
    return config
