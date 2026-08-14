"""Explicit, testable battle state machine.

The controller owns combat rules and menu state but has no pygame imports.
The frontend supplies discrete actions and a held movement vector, while the
renderer observes this object to draw the current state.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import Enum, auto
import math
import random
from typing import Any, Callable

from engine.battle.animations import AnimationQueue
from engine.battle.config import BattleConfig
from engine.battle.controls import BattleInput
from engine.battle.move_progression import (
    CombatMoveSkillTracker,
    initial_difficulty_level,
    resolve_combat_move,
    tutorial_records_skill,
)
from engine.battle.defense import DefenseSequence
from engine.battle.patterns import calculate_player_damage
from engine.battle.qte import AttackQTE, create_attack_qte
from engine.core.condition_eval import evaluate_condition
from engine.core.game_state import GameState
from engine.core.inventory import effective_stats as inventory_effective_stats, item_stat_bonuses
from engine.errors import BattleConfigError


class BattleState(Enum):
    INTRO = auto()
    DIALOGUE = auto()
    COMMAND = auto()
    MOVE_MENU = auto()
    INVENTORY_MENU = auto()
    ITEM_MENU = auto()
    GEAR = auto()
    PLAYER_ATTACK = auto()
    PLAYER_RESOLVE = auto()
    ENEMY_SELECT = auto()
    ENEMY_TELEGRAPH = auto()
    DEFENSE_OPENING = auto()
    DEFENSE = auto()
    DEFENSE_CLOSING = auto()
    ENEMY_RESOLVE = auto()
    VICTORY = auto()
    VICTORY_ANIMATION = auto()
    DEFEAT = auto()
    ESCAPE = auto()
    DEFEAT_ANIMATION = auto()
    REVIVAL_CUTSCENE = auto()
    GAME_OVER_CUTSCENE = auto()


class RevivalStage(Enum):
    """The non-blocking determined-revival presentation timeline."""

    HEART_SPLIT = "heart_split"
    SPLIT_PAUSE = "split_pause"
    REVIVAL_DIALOGUE_DELAY = "revival_dialogue_delay"
    REVIVAL_DIALOGUE = "revival_dialogue"
    MUSIC_FADE = "music_fade"
    HEART_RECOMBINE = "heart_recombine"
    POST_RECOMBINE_PAUSE = "post_recombine_pause"
    HERO_MUSIC_PAUSE = "hero_music_pause"
    HEART_FADE = "heart_fade"
    BACKGROUND_FADE_DELAY = "background_fade_delay"
    BACKGROUND_FADE = "background_fade"
    ENEMY_DIALOGUE = "enemy_dialogue"
    PHASE_TRANSITION = "phase_transition"
    COMPLETE = "complete"


class GameOverCutsceneStage(Enum):
    """Timed stages before handing a final-loss menu to the game engine."""

    HEART_SPLIT = "heart_split"
    MUSIC_DELAY = "music_delay"
    MENU_DELAY = "menu_delay"


@dataclass
class GameOverCutscene:
    x: float
    y: float
    stage: GameOverCutsceneStage = GameOverCutsceneStage.HEART_SPLIT
    stage_elapsed: float = 0.0
    heart_elapsed: float = 0.0

    @property
    def stage_name(self) -> str:
        return self.stage.value


@dataclass
class RuntimeEnemy:
    name: str
    hp: int
    max_hp: int
    attack: int
    defense: int


@dataclass(frozen=True)
class DeathShard:
    """One fixed-velocity fragment in the player-loss presentation."""

    sprite: str
    velocity_x: float
    velocity_y: float


@dataclass
class DeathAnimation:
    """Timeline data for the heart-break loss sequence, in logical pixels."""

    x: float
    y: float
    shards: tuple[DeathShard, ...]
    elapsed: float = 0.0
    break1_played: bool = False
    break2_played: bool = False
    heart_shake_start: float = 1.0
    heart_shake_duration: float = 0.25
    break1_at: float = 1.25
    break2_at: float = 1.75
    debris_at: float = 2.0

    @property
    def phase(self) -> str:
        if self.elapsed < self.break1_at:
            return "heart"
        if self.elapsed < self.debris_at:
            return "broken_heart"
        return "shards"

    @property
    def heart_shaking(self) -> bool:
        return self.heart_shake_start <= self.elapsed < self.heart_shake_start + self.heart_shake_duration

    @property
    def shard_elapsed(self) -> float:
        return max(0.0, self.elapsed - self.debris_at)


def create_death_animation(x: float, y: float, rng: random.Random, *,
                           heart_shake_start: float, heart_shake_duration: float,
                           break1_at: float, break2_at: float, debris_at: float) -> DeathAnimation:
    """Create the shared, reproducible seven-shard heart burst."""
    shard_count = 7
    shard_sprites = ("heart_shard1.png", "heart_shard2.png", "heart_shard3.png")
    full_sets, remainder = divmod(shard_count, len(shard_sprites))
    sprites = list(shard_sprites) * full_sets
    sprites.extend(rng.sample(shard_sprites, remainder))
    rng.shuffle(sprites)
    starting_angle = rng.uniform(-180, 180)
    angle_step = 360 / shard_count
    shards = tuple(
        DeathShard(
            sprites[index],
            math.cos(math.radians(angle)) * (144 + index % 2 * 24),
            math.sin(math.radians(angle)) * (144 + index % 2 * 24),
        )
        for index in range(shard_count)
        for angle in (starting_angle + angle_step * index + rng.uniform(-20, 20),)
    )
    return DeathAnimation(x, y, shards, heart_shake_start=heart_shake_start,
                          heart_shake_duration=heart_shake_duration,
                          break1_at=break1_at, break2_at=break2_at, debris_at=debris_at)


@dataclass
class RevivalCutscene:
    """Presentation-only data for a configured determined revival."""

    x: float
    y: float
    stage: RevivalStage = RevivalStage.HEART_SPLIT
    stage_elapsed: float = 0.0
    heart_elapsed: float = 0.0

    @property
    def stage_name(self) -> str:
        return self.stage.value


@dataclass
class VictoryAnimation:
    """Timeline data for the desaturate-then-vaporize enemy presentation."""

    elapsed: float = 0.0
    vaporize_played: bool = False

    @property
    def enemy_alpha(self) -> int:
        if self.elapsed <= 1.0:
            return 255
        return max(0, min(255, round(255 * (1 - (self.elapsed - 1.0) / 1.0))))


class BattleController:
    """Runtime state for one configured fight.

    State transitions live in named methods rather than a collection of
    flags.  The two public drivers are :meth:`handle_action` for KEYDOWN
    actions and :meth:`update` for elapsed time/held movement.
    """

    OPPONENT_DIALOGUE_PAUSE = 1.25
    OPPONENT_DIALOGUE_START_PAUSE = 0.25
    OPPONENT_DIALOGUE_CHARACTER_DELAY = 0.025
    REVIVAL_DIALOGUE_CHARACTER_DELAY = OPPONENT_DIALOGUE_CHARACTER_DELAY * 4
    OPPONENT_DIALOGUE_BLIP_EVERY_LETTERS = 2
    OPPONENT_DIALOGUE_BLIP_SFX = "dialog_blip.wav"
    MOVING_WEAK_POINT_FIRE_SFX = "arrow.wav"
    RHYTHM_COMBO_HIT_SFX = "hit.wav"
    RHYTHM_COMBO_PENALTY_SFX = "swallow.wav"
    DIRECTIONAL_COMBO_HIT_SFX = "hit.wav"
    RAPID_SLASH_HIT_SFX = "slash.wav"
    RAPID_SLASH_MISS_SFX = "arrow.wav"
    RAPID_SLASH_MISS_PITCH = 2.0
    CHARGE_RELEASE_RELEASE_SFX = "shadow.wav"
    DEFENSE_TRANSITION_DURATION = 0.25
    DEATH_INITIAL_PAUSE = 0.7
    DEATH_HEART_SHAKE_DURATION = 0.25
    DEATH_BREAK_1_AT = DEATH_INITIAL_PAUSE + DEATH_HEART_SHAKE_DURATION
    DEATH_BREAK_2_AT = DEATH_BREAK_1_AT + 0.5
    DEATH_DEBRIS_DELAY = 0.375
    DEATH_DEBRIS_AT = DEATH_BREAK_2_AT + DEATH_DEBRIS_DELAY
    DEATH_SHARD_HOLD_DURATION = 3.0
    REVIVAL_SPLIT_PAUSE_DURATION = 2.0
    REVIVAL_DIALOGUE_DELAY_DURATION = 1.0
    REVIVAL_DIALOGUE_FADE_DURATION = 0.5
    REVIVAL_DIALOGUE_NEXT_LINE_DELAY_DURATION = 0.5
    REVIVAL_MUSIC_FADE_DURATION = 1.0
    REVIVAL_HEART_RECOMBINE_DURATION = 0.45
    REVIVAL_RECOMBINE_SHAKE_DURATION = 0.20
    REVIVAL_POST_RECOMBINE_PAUSE_DURATION = 1.0
    REVIVAL_HERO_MUSIC_PAUSE_DURATION = 3.0
    REVIVAL_HEART_FADE_DURATION = 1.0
    REVIVAL_BACKGROUND_FADE_DELAY_DURATION = 1.0
    REVIVAL_BACKGROUND_FADE_DURATION = 1.0
    GAME_OVER_MUSIC_DELAY_DURATION = 1.0
    GAME_OVER_MENU_DELAY_DURATION = 1.0
    REVIVAL_MUSIC = "refused_to_die.ogg"
    HERO_MUSIC_INTRO = "true_hero_intro.ogg"
    HERO_MUSIC_LOOP = "true_hero_loop.ogg"
    VICTORY_VAPORIZE_AT = 1.0
    VICTORY_DIALOGUE_AT = 2.0

    def __init__(self, config: BattleConfig, state: GameState, items: dict[str, Any] | None = None,
                 rng: random.Random | None = None):
        self.config = config
        self.game_state = state
        self.items = items or {}
        self.rng = rng or random.Random()
        self.skill_tracker = CombatMoveSkillTracker(
            state,
            config.skill_progression,
            config.player_move_levels,
            {move_id: initial_difficulty_level(move) for move_id, move in config.player_moves.items()},
        )
        self.enemy = RuntimeEnemy(config.enemy.name, config.enemy.hp, config.enemy.hp, config.enemy.attack, config.enemy.defense)
        self.state = BattleState.INTRO
        self.selected = 0
        self.turn = 0
        self.phase_id: str | None = None
        self.phase_ids: set[str] = set()
        self.fight_flags: dict[str, bool] = {}
        self.player_move_ids = list(config.initial_player_moves)
        self.enemy_move_ids = list(config.initial_enemy_moves)
        self.enemy_sequence_index = 0
        self.used_player_moves: set[str] = set()
        self.player_augments: dict[str, list[dict[str, Any]]] = {}
        self.pattern_augments: dict[str, list[dict[str, Any]]] = {}
        self.enemy_weights: dict[str, float] = {}
        self.enemy_cooldowns: dict[str, int] = {}
        self.last_enemy_move: str | None = None
        self.last_player_move: str | None = None
        self.last_item: str | None = None
        self.active_attack: AttackQTE | None = None
        self.active_defense: DefenseSequence | None = None
        self.defense_opening_elapsed = 0.0
        self.defense_attack_delay = 0.0
        self.defense_closing_remaining = 0.0
        self.active_player_move: str | None = None
        self.active_player_level: int | None = None
        self.active_player_is_tutorial = False
        self.active_player_resolved_move: dict[str, Any] | None = None
        self._active_attempt_recorded = False
        self.active_enemy_move: str | None = None
        self.telegraph_remaining = 0.0
        self._dialogue_lines: list[str] = []
        self._dialogue_resume: BattleState | None = None
        self._dialogue_complete_callback: Callable[[], None] | None = None
        self._dialogue_typewriter = False
        self._dialogue_typewriter_sound: str | None = None
        self._dialogue_typewriter_character_delay = self.OPPONENT_DIALOGUE_CHARACTER_DELAY
        self._revival_dialogue_fade_remaining = 0.0
        self._revival_dialogue_next_line_delay_remaining = 0.0
        self._dialogue_type = "modal"
        self._dialogue_pause_remaining: float | None = None
        self._opponent_dialogue_pause = self.OPPONENT_DIALOGUE_PAUSE
        self._visible_opponent_dialogue_characters = 0
        self._opponent_dialogue_character_elapsed = 0.0
        self._opponent_dialogue_word_letter_count = 0
        self._opponent_dialogue_prepared = False
        self._opponent_wait_for_attack_animation = False
        self._opponent_dialogue_start_pause_remaining = 0.0
        self.remark_text: str | None = None
        self.environment_text: str | None = None
        self._visible_environment_characters = 0
        self._environment_dialogue_character_elapsed = 0.0
        self._environment_dialogue_word_letter_count = 0
        self._used_dialogue: set[str] = set()
        self.logs: list[str] = [f"A {self.enemy.name} appears!"]
        self.animations = AnimationQueue()
        self.active_effects: dict[str, dict[str, Any]] = {}
        self.outcome: str | None = None
        self.finished = False
        self.defeat_position: tuple[float, float] | None = None
        self.death_animation: DeathAnimation | None = None
        self.revival_cutscene: RevivalCutscene | None = None
        self.game_over_cutscene: GameOverCutscene | None = None
        self.game_over_menu_ready = False
        self.revival_uses = 0
        self.last_revival_stage: RevivalStage | None = None
        self.victory_animation: VictoryAnimation | None = None
        self._audio_events: list[tuple[str, str | None] | tuple[str, str | None, float]] = []
        self.debug_enabled = False
        self._arena_overrides: dict[str, Any] = {}
        self._background_override: str | None = None
        self._enemy_sprite_override: str | None = None
        # An optional, developer-facing one-shot sequence.  This is only
        # activated explicitly by a story transition and leaves normal battle
        # menu flow unchanged.
        self.test_sequence: dict[str, Any] | None = None
        self.test_result: dict[str, Any] | None = None
        self._test_player_hp_before = 0
        self._sync_health_animation()

    # -- presentation data -------------------------------------------------
    @property
    def dialogue_text(self) -> str | None:
        return self._dialogue_lines[0] if self._dialogue_lines else None

    @property
    def dialogue_type(self) -> str:
        return self._dialogue_type

    @property
    def visible_dialogue_text(self) -> str | None:
        """Return the portion of an opponent line that has typed so far."""
        text = self.dialogue_text
        if text is None or (self._dialogue_type != "opponent" and not self._dialogue_typewriter):
            return text
        return text[:self._visible_opponent_dialogue_characters]

    @property
    def dialogue_typewriter(self) -> bool:
        return self._dialogue_typewriter

    @property
    def visible_environment_text(self) -> str | None:
        """Return the typed portion of the passive environment caption."""
        if self.environment_text is None:
            return None
        return self.environment_text[:self._visible_environment_characters]

    @property
    def opponent_dialogue_started(self) -> bool:
        return self._dialogue_type != "opponent" or self._visible_opponent_dialogue_characters > 0

    def prepare_opponent_dialogue(self, text: str) -> None:
        """Accept renderer-provided line breaks before typewriter timing begins."""
        if self._dialogue_type != "opponent" or self._opponent_dialogue_prepared or not self._dialogue_lines:
            return
        self._dialogue_lines[0] = text
        self._opponent_dialogue_prepared = True

    @property
    def background(self) -> str | None:
        return self._background_override or self.config.background

    @property
    def enemy_sprite(self) -> str | None:
        return self._enemy_sprite_override or self.config.enemy.sprite

    @property
    def revival_stage(self) -> str | None:
        """Current/last revival stage for presentation and focused tests."""
        if self.revival_cutscene is not None:
            return self.revival_cutscene.stage_name
        return self.last_revival_stage.value if self.last_revival_stage is not None else None

    @property
    def visible_revival_dialogue_text(self) -> str | None:
        """Backward-compatible alias for the shared dialogue typewriter."""
        return self.visible_dialogue_text

    @property
    def revival_dialogue_alpha(self) -> float:
        """Current opacity of the special, centered revival narration."""
        if self._revival_dialogue_next_line_delay_remaining > 0:
            return 0.0
        if self._revival_dialogue_fade_remaining > 0:
            return self._revival_dialogue_fade_remaining / self.REVIVAL_DIALOGUE_FADE_DURATION
        return 1.0

    def consume_audio_events(self) -> list[tuple[str, str | None] | tuple[str, str | None, float]]:
        """Return presentation audio requests once, in timeline order."""
        events, self._audio_events = self._audio_events, []
        return events

    @property
    def arena(self) -> dict[str, Any]:
        result = dict(self.config.arena)
        result.update(self._arena_overrides)
        return result

    @property
    def defense_window_scale(self) -> float:
        """The vertically visible fraction of the clipped defense surface."""
        if self.state == BattleState.DEFENSE_OPENING:
            return min(1.0, self.defense_opening_elapsed / self.DEFENSE_TRANSITION_DURATION)
        if self.state == BattleState.DEFENSE_CLOSING:
            return max(0.0, self.defense_closing_remaining / self.DEFENSE_TRANSITION_DURATION)
        return 1.0

    def menu_entries(self) -> list[str]:
        if self.state == BattleState.COMMAND:
            entries = ["Fight", "Inventory"]
            if self.config.escape_enabled:
                entries.append("Escape")
            return entries
        if self.state == BattleState.MOVE_MENU:
            return [self._effective_player_move(move_id)["name"] for move_id in self.available_player_moves()]
        if self.state == BattleState.INVENTORY_MENU:
            return ["Use Item", "Check Gear"]
        if self.state == BattleState.ITEM_MENU:
            return [self.items[item_id].get("name", item_id) for item_id in self.combat_item_ids()]
        return []

    def selected_item_id(self) -> str | None:
        item_ids = self.combat_item_ids()
        return item_ids[self.selected] if item_ids and self.selected < len(item_ids) else None

    def item_detail(self, item_id: str | None = None) -> dict[str, Any] | None:
        item_id = item_id or self.selected_item_id()
        if item_id is None:
            return None
        data = self.items.get(item_id, {})
        combat = data.get("combat", {})
        return {
            "id": item_id, "name": data.get("name", item_id), "description": data.get("description", ""),
            "quantity": self.game_state.inventory.get(item_id, 0), "effects": combat.get("effects", []),
            "consume_turn": combat.get("consume_turn", True), "restrictions": combat.get("restrictions", []),
        }

    def gear_data(self) -> dict[str, Any]:
        stats = self.combat_stats()
        equipment = []
        for slot, item_id in self.game_state.equipment.items():
            item = self.items.get(item_id, {})
            equipment.append({"slot": slot, "id": item_id, "name": item.get("name", item_id), "bonuses": self._equipment_bonuses(item)})
        weapon = self.game_state.get_equipped("weapon")
        granted_ids = self.items.get(weapon or "", {}).get("combat", {}).get("move_grants", [])
        granted = [self.config.player_moves[move_id].get("name", move_id) for move_id in self.available_player_moves()
                   if move_id in granted_ids]
        return {"hp": self.current_player_hp(), "max_hp": self.maximum_player_hp(), "stats": stats,
                "equipment": equipment, "weapon": weapon, "weapon_moves": granted}

    def debug_data(self) -> list[str]:
        if not self.debug_enabled:
            return []
        projectile_count = len(self.active_defense.projectiles) if self.active_defense else 0
        invuln = self.active_defense.player_invulnerable_for if self.active_defense else 0.0
        move_level = (f"{self.active_player_move}@{self.active_player_level}" if self.active_player_move and self.active_player_level is not None
                      else "-")
        return [f"{self.state.name} T:{self.turn} P:{self.phase_id or '-'}", f"enemy:{self.active_enemy_move or '-'} proj:{projectile_count}",
                f"move:{move_level} invuln:{invuln:.2f} score:{self.active_attack.result.score if self.active_attack else 0:.2f}"]

    # -- input -------------------------------------------------------------
    def handle_action(self, action: str) -> bool:
        """Consume a single discrete engine action without allowing it to cascade."""
        if action == "UNKNOWN":
            return False
        if self.state == BattleState.DEFEAT_ANIMATION:
            return False
        if self.state in {BattleState.VICTORY, BattleState.DEFEAT, BattleState.ESCAPE}:
            if action == "SELECT" and self._test_sequence_victory():
                self._repeat_test_sequence()
                return True
            if action == "SELECT" or (action == "BACK" and self._test_sequence_victory()):
                self.finished = True
                return True
            return False
        if self.state == BattleState.DIALOGUE:
            if action == "SELECT" and self._dialogue_type == "modal":
                if self._revival_dialogue_fade_remaining > 0 or self._revival_dialogue_next_line_delay_remaining > 0:
                    return False
                if (self._dialogue_typewriter
                        and self._visible_opponent_dialogue_characters < len(self.dialogue_text or "")):
                    self._visible_opponent_dialogue_characters = len(self.dialogue_text or "")
                    self._opponent_dialogue_character_elapsed = 0.0
                    return True
                if (self.revival_cutscene is not None
                        and self.revival_cutscene.stage == RevivalStage.REVIVAL_DIALOGUE):
                    self._revival_dialogue_fade_remaining = self.REVIVAL_DIALOGUE_FADE_DURATION
                    return True
                self._advance_dialogue()
                return True
            return False
        if self.state == BattleState.PLAYER_ATTACK:
            if self.active_attack:
                previous_successes = getattr(self.active_attack, "achieved_stage", -1)
                previous_rhythm_hits = getattr(self.active_attack, "cleared", 0)
                previous_rhythm_penalties = getattr(self.active_attack, "penalties", 0)
                previous_directional_hits = getattr(self.active_attack, "hits", 0)
                handled = self.active_attack.handle_action(action)
                if handled and self.active_attack.qte_type == "moving_weak_point":
                    self._audio_events.append(("sfx", self.MOVING_WEAK_POINT_FIRE_SFX))
                if handled and self.active_attack.qte_type == "rhythm_combo":
                    if getattr(self.active_attack, "cleared", 0) > previous_rhythm_hits:
                        pitch = getattr(self.active_attack, "last_hit_pitch", 1.0)
                        event: tuple[str, str | None] | tuple[str, str | None, float] = (
                            ("sfx", self.RHYTHM_COMBO_HIT_SFX, pitch)
                            if pitch != 1.0 else ("sfx", self.RHYTHM_COMBO_HIT_SFX)
                        )
                        self._audio_events.append(event)
                    elif getattr(self.active_attack, "penalties", 0) > previous_rhythm_penalties:
                        self._audio_events.append(("sfx", self.RHYTHM_COMBO_PENALTY_SFX))
                if (handled and self.active_attack.qte_type == "directional_combo"
                        and getattr(self.active_attack, "hits", 0) > previous_directional_hits):
                    self._audio_events.append(("sfx", self.DIRECTIONAL_COMBO_HIT_SFX))
                if handled and self.active_attack.qte_type == "rapid_slash":
                    if getattr(self.active_attack, "last_slash_hit", False):
                        pitch = getattr(self.active_attack, "last_hit_pitch", 1.0)
                        event: tuple[str, str | None] | tuple[str, str | None, float] = (
                            ("sfx", self.RAPID_SLASH_HIT_SFX, pitch)
                            if pitch != 1.0 else ("sfx", self.RAPID_SLASH_HIT_SFX)
                        )
                        self._audio_events.append(event)
                    else:
                        # AudioSystem pitch resampling also sets playback
                        # speed, giving empty slashes a brisk arrow whoosh.
                        self._audio_events.append(("sfx", self.RAPID_SLASH_MISS_SFX, self.RAPID_SLASH_MISS_PITCH))
                # Rotating strike has three escalating arc opportunities. The
                # first two land immediately, so play their impact cue before
                # the eventual overall attack-result sound is dispatched.
                if (handled and self.active_attack.qte_type == "rotating_strike"
                        and getattr(self.active_attack, "achieved_stage", -1) in {0, 1}
                        and getattr(self.active_attack, "achieved_stage", -1) > previous_successes):
                    self._audio_events.append(("sfx", "hit.wav"))
                if handled:
                    # QTE actions are consumed within this state. In particular,
                    # a charge press cannot leak into the following battle menu.
                    if self.active_attack.done:
                        self._set_state(BattleState.PLAYER_RESOLVE)
                        self._resolve_player_attack()
                    return True
            return False
        if self.state == BattleState.GEAR:
            if action == "BACK":
                self._set_state(BattleState.INVENTORY_MENU)
                return True
            return False
        if self.state not in {BattleState.COMMAND, BattleState.MOVE_MENU, BattleState.INVENTORY_MENU, BattleState.ITEM_MENU}:
            return False
        entries = self.menu_entries()
        if action in {"UP", "LEFT"} and entries:
            previous = self.selected
            self.selected = max(0, self.selected - 1)
            return previous != self.selected
        if action in {"DOWN", "RIGHT"} and entries:
            previous = self.selected
            self.selected = min(len(entries) - 1, self.selected + 1)
            return previous != self.selected
        if action == "BACK":
            return self._go_back()
        if action == "SELECT" and entries:
            return self._confirm_menu()
        return False

    def _go_back(self) -> bool:
        if self.state == BattleState.MOVE_MENU:
            self._set_state(BattleState.COMMAND)
        elif self.state == BattleState.INVENTORY_MENU:
            self._set_state(BattleState.COMMAND)
        elif self.state == BattleState.ITEM_MENU:
            self._set_state(BattleState.INVENTORY_MENU)
        else:
            return False
        return True

    def _confirm_menu(self) -> bool:
        if self.state == BattleState.COMMAND:
            choice = self.menu_entries()[self.selected]
            if choice == "Fight":
                self.remark_text = None
                self._set_state(BattleState.MOVE_MENU)
            elif choice == "Inventory":
                self._set_state(BattleState.INVENTORY_MENU)
            else:
                self._set_outcome("escape")
            return True
        if self.state == BattleState.MOVE_MENU:
            moves = self.available_player_moves()
            if not moves:
                return False
            self.active_player_move = moves[self.selected]
            self.active_player_is_tutorial = False
            if not self._request_dialogue("before_player_action", BattleState.PLAYER_ATTACK, {"move": self.active_player_move}):
                self._start_player_attack()
            return True
        if self.state == BattleState.INVENTORY_MENU:
            if self.selected == 0:
                self._set_state(BattleState.ITEM_MENU)
            else:
                self._set_state(BattleState.GEAR)
            return True
        item_id = self.selected_item_id()
        return bool(item_id and self._use_item(item_id))

    # -- delta-time update -------------------------------------------------
    def update(self, dt: float, movement: BattleInput = BattleInput()) -> bool:
        if self.game_over_cutscene is not None:
            return self._update_game_over_cutscene(dt)
        if self.revival_cutscene is not None:
            return self._update_revival_cutscene(dt)
        if self.death_animation is not None:
            return self._update_death_animation(dt)
        if self.state == BattleState.VICTORY_ANIMATION:
            # A killing blow queues the normal hit flash immediately before
            # entering this state.  Keep that presentation queue advancing,
            # but do not begin vaporizing until the flash has fully cleared.
            # The renderer still sees ``victory_animation`` right away, so
            # desaturation is immediate.
            attack_flash_active = any(animation.kind == "flash" for animation in self.animations.active)
            self.animations.update(dt)
            if attack_flash_active:
                return True
            return self._update_victory_animation(dt)
        changed = self.animations.update(dt)
        changed |= self._update_environment_dialogue(max(0.0, dt))
        if self.state == BattleState.INTRO:
            if not self._request_dialogue("battle_start", BattleState.COMMAND, {}):
                self._set_state(BattleState.COMMAND)
            return True
        if self.state == BattleState.DIALOGUE and self._dialogue_type == "opponent":
            self._update_opponent_dialogue(max(0.0, dt))
            return True
        if self.state == BattleState.PLAYER_ATTACK and self.active_attack:
            before = self.active_attack.done
            impact_before = getattr(self.active_attack, "impact_remaining", None) is not None
            charge_release_started = getattr(self.active_attack, "release_started_at", None)
            self.active_attack.update(dt, movement)
            if (self.active_attack.qte_type == "charge_release" and charge_release_started is None
                    and getattr(self.active_attack, "release_started_at", None) is not None):
                self._audio_events.append(("sfx", self.CHARGE_RELEASE_RELEASE_SFX))
            if (self.active_attack.qte_type == "moving_weak_point" and not impact_before
                    and getattr(self.active_attack, "impact_remaining", None) is not None):
                self._audio_events.append(("sfx", "hit.wav"))
            if self.active_attack.done and not before:
                self._set_state(BattleState.PLAYER_RESOLVE)
                self._resolve_player_attack()
            return True
        if self.state == BattleState.ENEMY_SELECT:
            self._select_enemy_move()
            return True
        if self.state == BattleState.ENEMY_RESOLVE:
            self._complete_enemy_turn()
            return True
        if self.state == BattleState.ENEMY_TELEGRAPH:
            self.telegraph_remaining -= max(0.0, dt)
            if self.telegraph_remaining <= 0:
                self._start_defense()
            return True
        if self.state == BattleState.DEFENSE_OPENING:
            opening_dt = max(0.0, dt)
            self.defense_opening_elapsed += opening_dt
            if self.defense_opening_elapsed >= self.defense_attack_delay:
                overflow = self.defense_opening_elapsed - self.defense_attack_delay
                self._set_state(BattleState.DEFENSE)
                # Do not throw away elapsed time when a long frame crosses
                # from the visual opening into active dodge simulation.
                if overflow > 1e-9:
                    return self.update(overflow, movement)
            return True
        if self.state == BattleState.DEFENSE and self.active_defense:
            result = self.active_defense.update(dt, movement, self._take_player_damage)
            if self.finished:
                return True
            if (self.death_animation is not None or self.revival_cutscene is not None
                    or self.game_over_cutscene is not None):
                return True
            if result.dialogue:
                # Timeline dialogue is retained in history but never interrupts
                # player movement or projectile timing during defense.
                self.logs.extend(line for line in result.dialogue if line)
            if result.completed:
                self.defense_closing_remaining = self.DEFENSE_TRANSITION_DURATION
                self._set_state(BattleState.DEFENSE_CLOSING)
            return True
        if self.state == BattleState.DEFENSE_CLOSING:
            # The sequence is intentionally not updated here: no projectile can
            # collide while the arena collapses.
            self.defense_closing_remaining -= max(0.0, dt)
            if self.defense_closing_remaining <= 1e-6:
                self.active_defense = None
                self._set_state(BattleState.ENEMY_RESOLVE)
                self._resolve_enemy_attack()
            return True
        return changed

    # -- player turn -------------------------------------------------------
    def _test_sequence_victory(self) -> bool:
        return bool(self.state == BattleState.VICTORY and self.test_sequence)

    @property
    def test_sequence_victory(self) -> bool:
        """Whether this developer test victory offers Select to repeat it."""
        return self._test_sequence_victory()

    @property
    def test_attack_victory(self) -> bool:
        """Backward-compatible alias for developer test victory menus."""
        return self._test_sequence_victory()

    def _repeat_test_sequence(self) -> None:
        assert self.test_sequence is not None
        mode = self.test_sequence["mode"]
        self.test_result = None
        self.outcome = None
        self.finished = False
        if mode == "player_attack":
            self.active_player_move = str(self.test_sequence["move"])
            self.active_player_is_tutorial = self.test_sequence.get("difficulty") == 0
            self._start_player_attack()
            return
        if self.config.test_sequences_restore_hp:
            self.game_state.set_stat("hp", self.maximum_player_hp())
        self._test_player_hp_before = self.current_player_hp()
        self.active_enemy_move = str(self.test_sequence["move"])
        self._begin_telegraph(self.config.enemy_moves[self.active_enemy_move])

    def start_test_sequence(self, sequence: dict[str, Any]) -> bool:
        """Start one selected QTE or defense pattern without the command menu.

        Test sequences are deliberately non-progression attempts: they are a
        repeatable authoring harness, not a way to alter a save's adaptive
        combat levels.  ``mode`` is ``player_attack`` or ``enemy_attack``.
        """
        mode = sequence.get("mode")
        if mode == "player_attack":
            move_id = sequence.get("move")
            level = sequence.get("difficulty", 1)
            if (not isinstance(move_id, str) or move_id not in self.config.player_moves
                    or isinstance(level, bool) or not isinstance(level, int)):
                return False
            try:
                resolve_combat_move(self.config.player_moves[move_id], level)
            except BattleConfigError:
                return False
            self.test_sequence = {"mode": mode, "move": move_id, "difficulty": level}
            self.active_player_move = move_id
            self.active_player_is_tutorial = level == 0
            self._start_player_attack()
            return True
        if mode == "enemy_attack":
            move_id = sequence.get("move")
            difficulty = sequence.get("difficulty", 1)
            if (not isinstance(move_id, str) or move_id not in self.config.enemy_moves
                    or isinstance(difficulty, bool) or not isinstance(difficulty, (str, int))):
                return False
            self.test_sequence = {"mode": mode, "move": move_id, "difficulty": difficulty}
            # Most authoring harnesses begin each defense run at full health.
            # Stories can opt out when repeated tests are meant to form one
            # continuous survival challenge.
            if self.config.test_sequences_restore_hp:
                self.game_state.set_stat("hp", self.maximum_player_hp())
            self._test_player_hp_before = self.current_player_hp()
            self.active_enemy_move = move_id
            self._begin_telegraph(self.config.enemy_moves[move_id])
            return True
        return False

    def start_move_tutorial(self, move_id: str) -> bool:
        """Explicitly begin a move's optional level-0 tutorial attempt.

        The standard battle menu deliberately never selects tutorials.  A
        story/tutorial screen can call this method when it wants to run the
        authored level-0 version without replacing the saved adaptive level.
        """
        if move_id not in self.config.player_moves:
            return False
        try:
            resolve_combat_move(self.config.player_moves[move_id], 0)
        except BattleConfigError:
            return False
        self.active_player_move = move_id
        self.active_player_is_tutorial = True
        self._start_player_attack()
        return True

    def _start_player_attack(self) -> None:
        assert self.active_player_move is not None
        self._clear_environment_dialogue()
        level = (self.test_sequence["difficulty"] if self.test_sequence and self.test_sequence["mode"] == "player_attack"
                 else 0 if self.active_player_is_tutorial else self.skill_tracker.current_level(self.active_player_move))
        self.active_player_level = level
        # Keep this exact resolved definition for resolution too: a promotion
        # from this result only affects the next QTE, never its damage/QTE.
        self.active_player_resolved_move = self._effective_player_move(self.active_player_move, level)
        self._active_attempt_recorded = False
        self.active_attack = create_attack_qte(self.active_player_resolved_move, self.rng)
        self._set_state(BattleState.PLAYER_ATTACK)

    def _resolve_player_attack(self) -> None:
        assert self.active_attack is not None and self.active_player_move is not None
        assert self.active_player_resolved_move is not None
        move = self.active_player_resolved_move
        result = self.active_attack.result
        if not self._active_attempt_recorded and self.test_sequence is None:
            self.skill_tracker.record_result(
                self.active_player_move,
                result.tier,
                tutorial=self.active_player_is_tutorial,
                tutorial_records_skill=tutorial_records_skill(self.config.player_moves[self.active_player_move]),
            )
            self._active_attempt_recorded = True
        damage = calculate_player_damage(move, self.combat_stats()["attack"], self.enemy.defense, result)
        self.enemy.hp = max(0, self.enemy.hp - damage)
        self.last_player_move = self.active_player_move
        self.used_player_moves.add(self.active_player_move)
        label = result.label
        self.logs.append(f"{move['name']}: {label} for {damage} damage.")
        sound = self.active_attack.sound
        if sound is None and not getattr(self.active_attack, "suppress_default_result_sound", False):
            sound = {"Miss": "fall.wav", "Weak": "damage.wav", "Strong": "damage.wav", "Critical": "slash.wav"}[label]
        if sound:
            self._audio_events.append(("sfx", sound))
        if damage > 0:
            self.animations.enemy_shake()
        feedback_colors = {
            "Miss": (155, 155, 155),
            "Weak": (255, 230, 90),
            "Strong": (90, 220, 120),
            "Critical": (235, 75, 75),
        }
        self.animations.feedback(f"{label} {damage}", feedback_colors[label])
        self.animations.flash()
        self._sync_health_animation()
        if self.test_sequence is not None:
            self.test_result = {
                "mode": "player_attack", "subject": move["name"], "difficulty": self.active_player_level,
                "performance": label, "damage": damage, "damage_taken": 0,
            }
            self._set_outcome("win")
            return
        if self.enemy.hp <= 0:
            self._begin_victory_animation()
            return
        if self.enemy.hp * 2 <= self.enemy.max_hp and self._request_dialogue("enemy_low_health", BattleState.ENEMY_SELECT, {"move": self.last_player_move}):
            self.turn += 1
            return
        self._finish_player_turn({"move": self.last_player_move})

    def _finish_player_turn(self, context: dict[str, Any]) -> None:
        self.turn += 1
        if self._check_phases(BattleState.ENEMY_SELECT):
            return
        if context.get("move") and self._request_dialogue("move_used", BattleState.ENEMY_SELECT, context):
            return
        if not self._request_dialogue("after_player_action", BattleState.ENEMY_SELECT, context):
            self._set_state(BattleState.ENEMY_SELECT)

    # -- enemy turn --------------------------------------------------------
    def _select_enemy_move(self) -> None:
        move_id = self.select_enemy_move()
        self.active_enemy_move = move_id
        move = self.config.enemy_moves[move_id]
        if not self._request_dialogue("before_enemy_pattern", BattleState.ENEMY_TELEGRAPH, {"move": move_id}):
            self._begin_telegraph(move)

    def _begin_telegraph(self, move: dict[str, Any] | None = None) -> None:
        move = move or self.config.enemy_moves[self.active_enemy_move or ""]
        self.telegraph_remaining = float(move.get("telegraph_duration", 0.55))
        text = move.get("telegraph", f"{self.enemy.name} prepares {move.get('name', 'an attack')}!")
        self.logs.append(str(text))
        self._set_state(BattleState.ENEMY_TELEGRAPH)

    def _start_defense(self) -> None:
        assert self.active_enemy_move is not None
        move = self.config.enemy_moves[self.active_enemy_move]
        if "pattern" not in move:  # Legacy adapter only.
            self._set_state(BattleState.ENEMY_RESOLVE)
            self._resolve_enemy_attack()
            return
        pattern = self._effective_enemy_pattern(move["pattern"])
        # A move may select a named/numbered override while the underlying
        # sequence remains one reusable YAML definition.
        difficulty = (self.test_sequence["difficulty"] if self.test_sequence and self.test_sequence["mode"] == "enemy_attack"
                      else move.get("defense_difficulty"))
        self.active_defense = DefenseSequence(pattern, self.arena, self.rng, difficulty)
        self.defense_opening_elapsed = 0.0
        self.defense_attack_delay = max(self.DEFENSE_TRANSITION_DURATION, float(pattern.get("attack_delay", self.DEFENSE_TRANSITION_DURATION)))
        self.defense_closing_remaining = 0.0
        self._set_state(BattleState.DEFENSE_OPENING)

    def _resolve_enemy_attack(self) -> None:
        assert self.active_enemy_move is not None
        move = self.config.enemy_moves[self.active_enemy_move]
        if "legacy_damage" in move and move["legacy_damage"]:
            damage_range = move["legacy_damage"]
            raw = self.rng.randint(int(damage_range[0]), int(damage_range[1])) - self.combat_stats()["defense"]
            self._take_player_damage(max(1, raw))
            self.logs.append(f"{self.enemy.name} uses {move.get('name', 'Attack')} for {max(1, raw)} damage.")
        elif move.get("legacy_effect") == "buff_attack":
            self.enemy.attack += 1
            self.logs.append(f"{self.enemy.name} grows stronger!")
        if self.death_animation is not None or self.revival_cutscene is not None:
            return
        if self.test_sequence is not None:
            self.test_result = {
                "mode": "enemy_attack", "subject": move.get("name", self.active_enemy_move),
                "difficulty": self.test_sequence["difficulty"], "performance": "Complete",
                "damage": 0, "damage_taken": self._test_player_hp_before - self.current_player_hp(),
            }
            self._set_outcome("win")
            return
        self._set_enemy_cooldown(self.active_enemy_move)
        self._decrement_effects()
        if self._check_phases(BattleState.ENEMY_RESOLVE):
            return
        if not self._request_dialogue("after_enemy_pattern", BattleState.ENEMY_RESOLVE, {"move": self.active_enemy_move}):
            self._complete_enemy_turn()

    def _complete_enemy_turn(self) -> None:
        """Enter the next command turn, allowing a paused dialogue to resume first."""
        if not self._request_dialogue("turn_start", BattleState.COMMAND, {"turn": self.turn + 1, "phase": self.phase_id}):
            self._set_state(BattleState.COMMAND)

    def select_enemy_move(self) -> str:
        """Weighted, injectable-RNG selection honoring cooldown/no-repeat."""
        while self.enemy_sequence_index < len(self.config.enemy_sequence):
            scripted = self.config.enemy_sequence[self.enemy_sequence_index]
            self.enemy_sequence_index += 1
            if scripted in self.enemy_move_ids and self._enemy_move_available(scripted):
                self.last_enemy_move = scripted
                return scripted
        candidates = [move_id for move_id in self.enemy_move_ids if self.enemy_cooldowns.get(move_id, 0) <= 0 and self._enemy_move_available(move_id)]
        no_repeat = [move_id for move_id in candidates if not (self.config.enemy_moves[move_id].get("no_immediate_repeat") and move_id == self.last_enemy_move)]
        candidates = no_repeat or candidates or [move_id for move_id in self.enemy_move_ids if self._enemy_move_available(move_id)]
        if not candidates:
            raise RuntimeError(f"Battle {self.config.id!r} has no currently available enemy moves")
        weights = [max(0.0, float(self.enemy_weights.get(move_id, self.config.enemy_moves[move_id].get("weight", 1)))) for move_id in candidates]
        total = sum(weights)
        if total <= 0:
            return candidates[0]
        roll, cumulative = self.rng.random() * total, 0.0
        for move_id, weight in zip(candidates, weights):
            cumulative += weight
            if roll <= cumulative:
                self.last_enemy_move = move_id
                return move_id
        self.last_enemy_move = candidates[-1]
        return candidates[-1]

    def _enemy_move_available(self, move_id: str) -> bool:
        availability = self.config.enemy_moves[move_id].get("availability", {})
        if "min_turn" in availability and self.turn < int(availability["min_turn"]):
            return False
        if "max_turn" in availability and self.turn > int(availability["max_turn"]):
            return False
        phases = availability.get("phases")
        if phases and self.phase_id not in phases:
            return False
        for flag, wanted in availability.get("requires_fight_flags", {}).items():
            if self.fight_flags.get(flag, False) != bool(wanted):
                return False
        condition = availability.get("condition")
        return evaluate_condition(condition, self.game_state) if condition else True

    # -- inventory and stats ----------------------------------------------
    def combat_item_ids(self) -> list[str]:
        result = []
        for item_id in sorted(self.game_state.inventory):
            item = self.items.get(item_id, {})
            combat = item.get("combat", {}) if isinstance(item, dict) else {}
            if self.game_state.inventory.get(item_id, 0) > 0 and item.get("type") not in {"weapon", "armor", "equipment"} and combat.get("usable"):
                result.append(item_id)
        return result

    def _use_item(self, item_id: str) -> bool:
        if item_id not in self.combat_item_ids():
            return False
        item, combat = self.items[item_id], self.items[item_id]["combat"]
        for effect in combat.get("effects", []):
            name, value = next(iter(effect.items()))
            if name == "heal":
                before = self.current_player_hp()
                self.game_state.set_stat("hp", min(self.maximum_player_hp(), before + int(value)))
                recovered = self.current_player_hp() - before
                self.logs.append(f"Recovered {recovered} HP.")
                if recovered > 0:
                    self._audio_events.append(("sfx", "heal.wav"))
            elif name == "damage_enemy":
                before = self.enemy.hp
                self.enemy.hp = max(0, self.enemy.hp - int(value))
                self.logs.append(f"{item.get('name', item_id)} deals {int(value)} damage.")
                if self.enemy.hp < before:
                    self.animations.enemy_shake()
            elif name == "set_fight_flag":
                for key, flag_value in value.items():
                    self.fight_flags[key] = bool(flag_value)
            elif name == "apply_effect":
                effect_id = str(value.get("id"))
                self.active_effects[effect_id] = dict(value)
            elif name == "remove_effect":
                self.active_effects.pop(str(value), None)
        self.game_state.remove_item(item_id)
        self.last_item = item_id
        self.animations.feedback(item.get("name", item_id), (130, 230, 175))
        self._sync_health_animation()
        if self.enemy.hp <= 0:
            self._begin_victory_animation()
            return True
        if combat.get("consume_turn", True):
            if self._request_dialogue("item_used", BattleState.ENEMY_SELECT, {"item": item_id}):
                self.turn += 1
                return True
            self._finish_player_turn({"item": item_id})
        else:
            self._set_state(BattleState.ITEM_MENU)
        return True

    def combat_stats(self) -> dict[str, int]:
        derived = inventory_effective_stats(self.game_state, self.items)
        result = {"attack": derived["attack"], "defense": derived["defense"]}
        for effect in self.active_effects.values():
            for name, value in effect.get("stat_modifiers", {}).items():
                if name in result:
                    result[name] += int(value)
        return result

    def _equipment_bonuses(self, item: dict[str, Any]) -> dict[str, Any]:
        """Expose legacy bonus names while accepting modern ``stats`` items."""
        equipment = item.get("equipment", {}) if isinstance(item, dict) else {}
        legacy = equipment.get("bonuses", item.get("bonuses", {})) if isinstance(equipment, dict) else {}
        result = dict(legacy) if isinstance(legacy, dict) else {}
        normalized = item_stat_bonuses(item)
        result.update({
            "attack": normalized["attack"],
            "defense": normalized["defense"],
            "max_hp": normalized["hp"],
        })
        return result

    def current_player_hp(self) -> int:
        return max(0, min(int(self.game_state.get_stat("hp", 0)), self.maximum_player_hp()))

    def maximum_player_hp(self) -> int:
        return inventory_effective_stats(self.game_state, self.items)["max_hp"]

    def _take_player_damage(self, damage: int) -> None:
        # A defense sequence can report several collisions in one frame.  A
        # lethal first hit owns the loss flow, so all later queued callbacks
        # in that frame must be harmless.
        if self.finished or self.death_animation is not None or self.revival_cutscene is not None:
            return
        damage = max(0, int(damage))
        before = self.current_player_hp()
        self.game_state.set_stat("hp", max(0, before - damage))
        lost = before - self.current_player_hp()
        if lost:
            self.logs.append(f"You take {lost} damage.")
            self._audio_events.append(("sfx", "hurt.wav"))
            self.animations.feedback(f"-{lost}", (255, 115, 115))
            self.animations.shake()
        self._sync_health_animation()
        if lost and self.current_player_hp() <= 0:
            self._begin_defeat_animation()

    def _begin_defeat_animation(self) -> None:
        """Freeze combat at the last defense position and start the loss timeline."""
        if (self.death_animation is not None or self.revival_cutscene is not None
                or self.game_over_cutscene is not None):
            return
        on_lose = self.config.on_lose
        if on_lose is not None and on_lose.type == "determined_revival" and (on_lose.repeatable or self.revival_uses == 0):
            self._begin_determined_revival()
            return
        if on_lose is not None and on_lose.game_over is not None:
            self._begin_game_over_cutscene()
            return
        if self.active_defense is not None:
            x, y = self.active_defense.player_x, self.active_defense.player_y
        else:
            arena = self.arena
            x = float(arena.get("x", 0)) + float(arena.get("width", 0)) / 2
            y = float(arena.get("y", 0)) + float(arena.get("height", 0)) / 2
        self.death_animation = create_death_animation(
            x, y, self.rng, heart_shake_start=self.DEATH_INITIAL_PAUSE,
            heart_shake_duration=self.DEATH_HEART_SHAKE_DURATION,
            break1_at=self.DEATH_BREAK_1_AT, break2_at=self.DEATH_BREAK_2_AT,
            debris_at=self.DEATH_DEBRIS_AT,
        )
        self.active_defense = None
        self.active_attack = None
        self.outcome = "lose"
        extra = self.config.defeat.get("text")
        if extra:
            self.logs.append(str(extra))
        self._audio_events.append(("stop_music", None))
        self._set_state(BattleState.DEFEAT_ANIMATION)

    # -- game-over cutscene ----------------------------------------------
    def _begin_game_over_cutscene(self) -> None:
        """Run the final-loss heart timeline before opening the game-over menu."""
        if self.active_defense is not None:
            x, y = self.active_defense.player_x, self.active_defense.player_y
        else:
            arena = self.arena
            x = float(arena.get("x", 0)) + float(arena.get("width", 0)) / 2
            y = float(arena.get("y", 0)) + float(arena.get("height", 0)) / 2
        self.defeat_position = (x, y)
        self.game_over_cutscene = GameOverCutscene(x, y)
        self.active_defense = None
        self.active_attack = None
        self.active_enemy_move = None
        self.telegraph_remaining = 0.0
        self.defense_opening_elapsed = 0.0
        self.defense_closing_remaining = 0.0
        self.animations.active.clear()
        self.outcome = "lose"
        self._audio_events.append(("stop_music", None))
        self._set_state(BattleState.GAME_OVER_CUTSCENE)

    def _set_game_over_stage(self, stage: GameOverCutsceneStage) -> None:
        cutscene = self.game_over_cutscene
        assert cutscene is not None
        cutscene.stage = stage
        cutscene.stage_elapsed = 0.0

    def _update_game_over_cutscene(self, dt: float) -> bool:
        """Play break1, then wait one second for music and two for the menu."""
        remaining = max(0.0, dt)
        for _ in range(4):
            cutscene = self.game_over_cutscene
            if cutscene is None or self.game_over_menu_ready:
                break
            durations = {
                GameOverCutsceneStage.HEART_SPLIT: self.DEATH_BREAK_1_AT,
                GameOverCutsceneStage.MUSIC_DELAY: self.GAME_OVER_MUSIC_DELAY_DURATION,
                GameOverCutsceneStage.MENU_DELAY: self.GAME_OVER_MENU_DELAY_DURATION,
            }
            duration = durations[cutscene.stage]
            step = min(remaining, max(0.0, duration - cutscene.stage_elapsed))
            cutscene.stage_elapsed += step
            if cutscene.stage == GameOverCutsceneStage.HEART_SPLIT:
                cutscene.heart_elapsed = cutscene.stage_elapsed
            remaining -= step
            if cutscene.stage_elapsed + 1e-9 < duration:
                break
            if cutscene.stage == GameOverCutsceneStage.HEART_SPLIT:
                self._audio_events.append(("sfx", "break1.wav"))
                self._set_game_over_stage(GameOverCutsceneStage.MUSIC_DELAY)
            elif cutscene.stage == GameOverCutsceneStage.MUSIC_DELAY:
                on_lose = self.config.on_lose
                assert on_lose is not None and on_lose.game_over is not None
                self._audio_events.append(("music", on_lose.game_over.music, 0.5))
                self._set_game_over_stage(GameOverCutsceneStage.MENU_DELAY)
            else:
                self.game_over_menu_ready = True
                break
            if remaining <= 1e-9:
                break
        return True

    def _update_death_animation(self, dt: float) -> bool:
        death = self.death_animation
        assert death is not None
        before = death.elapsed
        death.elapsed += max(0.0, dt)
        if not death.break1_played and before < self.DEATH_BREAK_1_AT <= death.elapsed:
            death.break1_played = True
            self._audio_events.append(("sfx", "break1.wav"))
        if not death.break2_played and before < self.DEATH_DEBRIS_AT <= death.elapsed:
            death.break2_played = True
            self._audio_events.append(("sfx", "break2.wav"))
        if death.elapsed >= self.DEATH_DEBRIS_AT + self.DEATH_SHARD_HOLD_DURATION:
            self.finished = True
        return True

    # -- determined-revival cutscene --------------------------------------
    def _begin_determined_revival(self) -> None:
        """Replace a configured loss with a frozen, non-blocking cutscene."""
        if self.active_defense is not None:
            x, y = self.active_defense.player_x, self.active_defense.player_y
        else:
            arena = self.arena
            x = float(arena.get("x", 0)) + float(arena.get("width", 0)) / 2
            y = float(arena.get("y", 0)) + float(arena.get("height", 0)) / 2
        self.revival_uses += 1
        self.last_revival_stage = None
        self.revival_cutscene = RevivalCutscene(x, y)
        # Nothing from the lethal defense turn may continue while the
        # cutscene is showing.  In particular, discard projectiles/timers
        # rather than merely hiding them behind the black presentation.
        self.active_defense = None
        self.active_attack = None
        self.active_enemy_move = None
        self.telegraph_remaining = 0.0
        self.defense_opening_elapsed = 0.0
        self.defense_closing_remaining = 0.0
        self.animations.active.clear()
        self.outcome = None
        self._audio_events.append(("stop_music", None))
        self._set_state(BattleState.REVIVAL_CUTSCENE)

    def _set_revival_stage(self, stage: RevivalStage) -> None:
        cutscene = self.revival_cutscene
        assert cutscene is not None
        cutscene.stage = stage
        cutscene.stage_elapsed = 0.0

    def _begin_revival_music_fade(self) -> None:
        self._set_revival_stage(RevivalStage.MUSIC_FADE)
        self._audio_events.append(("fade_music", None, self.REVIVAL_MUSIC_FADE_DURATION))
        self._set_state(BattleState.REVIVAL_CUTSCENE)

    def _update_revival_cutscene(self, dt: float) -> bool:
        """Advance timed revival stages while dialogue is handled normally.

        Carrying residual delta time into the following timed stage keeps
        every configured constant accurate even on a slow frame, without a
        blocking sleep or a separate timer thread.
        """
        if self.state == BattleState.DIALOGUE:
            if self._update_revival_dialogue_transition(max(0.0, dt)):
                return True
            self._update_modal_typewriter(max(0.0, dt))
            return True
        remaining = max(0.0, dt)
        for _ in range(12):  # One bounded pass through every timed stage.
            cutscene = self.revival_cutscene
            if cutscene is None:
                break
            durations = {
                RevivalStage.HEART_SPLIT: self.DEATH_BREAK_1_AT,
                RevivalStage.SPLIT_PAUSE: self.REVIVAL_SPLIT_PAUSE_DURATION,
                RevivalStage.REVIVAL_DIALOGUE_DELAY: self.REVIVAL_DIALOGUE_DELAY_DURATION,
                RevivalStage.MUSIC_FADE: self.REVIVAL_MUSIC_FADE_DURATION,
                RevivalStage.HEART_RECOMBINE: self.REVIVAL_HEART_RECOMBINE_DURATION,
                RevivalStage.POST_RECOMBINE_PAUSE: self.REVIVAL_POST_RECOMBINE_PAUSE_DURATION,
                RevivalStage.HERO_MUSIC_PAUSE: self.REVIVAL_HERO_MUSIC_PAUSE_DURATION,
                RevivalStage.HEART_FADE: self.REVIVAL_HEART_FADE_DURATION,
                RevivalStage.BACKGROUND_FADE_DELAY: self.REVIVAL_BACKGROUND_FADE_DELAY_DURATION,
                RevivalStage.BACKGROUND_FADE: self.REVIVAL_BACKGROUND_FADE_DURATION,
            }
            duration = durations.get(cutscene.stage)
            if duration is None:
                break
            before = cutscene.stage_elapsed
            step = min(remaining, max(0.0, duration - before))
            cutscene.stage_elapsed = before + step
            if cutscene.stage == RevivalStage.HEART_SPLIT:
                cutscene.heart_elapsed = cutscene.stage_elapsed
            remaining -= step
            if cutscene.stage_elapsed + 1e-9 < duration:
                break
            if cutscene.stage == RevivalStage.HEART_SPLIT:
                self._audio_events.append(("sfx", "break1.wav"))
                self._set_revival_stage(RevivalStage.SPLIT_PAUSE)
            elif cutscene.stage == RevivalStage.SPLIT_PAUSE:
                self._audio_events.append(("music", self.REVIVAL_MUSIC, 0.5))
                on_lose = self.config.on_lose
                assert on_lose is not None
                if on_lose.dialogue:
                    self._set_revival_stage(RevivalStage.REVIVAL_DIALOGUE_DELAY)
                else:
                    self._begin_revival_music_fade()
            elif cutscene.stage == RevivalStage.REVIVAL_DIALOGUE_DELAY:
                on_lose = self.config.on_lose
                assert on_lose is not None
                self._start_revival_dialogue(list(on_lose.dialogue), RevivalStage.REVIVAL_DIALOGUE,
                                              self._begin_revival_music_fade)
                break
            elif cutscene.stage == RevivalStage.MUSIC_FADE:
                self._set_revival_stage(RevivalStage.HEART_RECOMBINE)
                self._audio_events.append(("sfx", "heal.wav"))
            elif cutscene.stage == RevivalStage.HEART_RECOMBINE:
                self._set_revival_stage(RevivalStage.POST_RECOMBINE_PAUSE)
            elif cutscene.stage == RevivalStage.POST_RECOMBINE_PAUSE:
                self._set_revival_stage(RevivalStage.HERO_MUSIC_PAUSE)
                self._audio_events.append(("music_sequence", self.HERO_MUSIC_INTRO, self.HERO_MUSIC_LOOP))
            elif cutscene.stage == RevivalStage.HERO_MUSIC_PAUSE:
                # Reveal the restored health with the revived scene.  Phase
                # initialization remains deferred until the enemy's
                # post-revival line is dismissed.
                on_lose = self.config.on_lose
                assert on_lose is not None
                self.game_state.set_stat("hp", min(self.maximum_player_hp(), on_lose.revived_hp))
                self._sync_health_animation(snap_player=True)
                self._apply_phase_presentation_by_id(on_lose.next_phase)
                self._set_revival_stage(RevivalStage.HEART_FADE)
            elif cutscene.stage == RevivalStage.HEART_FADE:
                self._set_revival_stage(RevivalStage.BACKGROUND_FADE_DELAY)
            elif cutscene.stage == RevivalStage.BACKGROUND_FADE_DELAY:
                self._set_revival_stage(RevivalStage.BACKGROUND_FADE)
            elif cutscene.stage == RevivalStage.BACKGROUND_FADE:
                on_lose = self.config.on_lose
                assert on_lose is not None
                self._set_revival_stage(RevivalStage.ENEMY_DIALOGUE)
                if on_lose.enemy_message:
                    self._start_revival_dialogue([on_lose.enemy_message], RevivalStage.ENEMY_DIALOGUE,
                                                 self._complete_determined_revival)
                    break
                self._complete_determined_revival()
                break
            if remaining <= 1e-9:
                break
        return True

    def _start_revival_dialogue(self, lines: list[str], stage: RevivalStage,
                                on_complete: Callable[[], None]) -> None:
        self._set_revival_stage(stage)
        self._revival_dialogue_fade_remaining = 0.0
        self._revival_dialogue_next_line_delay_remaining = 0.0
        on_lose = self.config.on_lose
        assert on_lose is not None
        character_delay = (self.REVIVAL_DIALOGUE_CHARACTER_DELAY
                           if stage == RevivalStage.REVIVAL_DIALOGUE
                           else self.OPPONENT_DIALOGUE_CHARACTER_DELAY)
        self._show_dialogue(lines, BattleState.REVIVAL_CUTSCENE, on_complete=on_complete, typewriter=True,
                            typewriter_sound=on_lose.dialog_sound, typewriter_character_delay=character_delay)

    def _update_revival_dialogue_transition(self, dt: float) -> bool:
        """Fade and pause between centered revival narration lines."""
        if self._revival_dialogue_fade_remaining <= 0 and self._revival_dialogue_next_line_delay_remaining <= 0:
            return False
        remaining = dt
        if self._revival_dialogue_fade_remaining > 0:
            step = min(remaining, self._revival_dialogue_fade_remaining)
            self._revival_dialogue_fade_remaining -= step
            remaining -= step
            if self._revival_dialogue_fade_remaining > 1e-9:
                return True
            self._revival_dialogue_fade_remaining = 0.0
            self._revival_dialogue_next_line_delay_remaining = self.REVIVAL_DIALOGUE_NEXT_LINE_DELAY_DURATION
        if self._revival_dialogue_next_line_delay_remaining > 0:
            step = min(remaining, self._revival_dialogue_next_line_delay_remaining)
            self._revival_dialogue_next_line_delay_remaining -= step
            if self._revival_dialogue_next_line_delay_remaining > 1e-9:
                return True
            self._revival_dialogue_next_line_delay_remaining = 0.0
            self._advance_dialogue()
        return True

    def _update_modal_typewriter(self, dt: float) -> None:
        """Shared modal dialogue reveal used by cutscenes without a new UI."""
        if not self._dialogue_typewriter:
            return
        text = self.dialogue_text or ""
        if self._visible_opponent_dialogue_characters >= len(text):
            return
        self._opponent_dialogue_character_elapsed += dt
        while (self._visible_opponent_dialogue_characters < len(text)
               and self._opponent_dialogue_character_elapsed >= self._dialogue_typewriter_character_delay):
            char = text[self._visible_opponent_dialogue_characters]
            self._visible_opponent_dialogue_characters += 1
            self._opponent_dialogue_character_elapsed -= self._dialogue_typewriter_character_delay
            if char.isalpha():
                self._opponent_dialogue_word_letter_count += 1
                if self._opponent_dialogue_word_letter_count % self.OPPONENT_DIALOGUE_BLIP_EVERY_LETTERS == 0:
                    assert self._dialogue_typewriter_sound is not None
                    self._audio_events.append(("sfx", self._dialogue_typewriter_sound))
            else:
                self._opponent_dialogue_word_letter_count = 0

    def _complete_determined_revival(self) -> None:
        """Restore combat only after the final cutscene dialogue closes."""
        cutscene = self.revival_cutscene
        if cutscene is None:
            return
        on_lose = self.config.on_lose
        assert on_lose is not None
        self._set_revival_stage(RevivalStage.PHASE_TRANSITION)
        self.active_defense = None
        self.active_attack = None
        self.active_enemy_move = None
        self.telegraph_remaining = 0.0
        self.defense_opening_elapsed = 0.0
        self.defense_closing_remaining = 0.0
        self.animations.active.clear()
        self._sync_health_animation()
        self._activate_phase_by_id(on_lose.next_phase)
        self._set_revival_stage(RevivalStage.COMPLETE)
        self.last_revival_stage = RevivalStage.COMPLETE
        self.revival_cutscene = None
        self._set_state(BattleState.COMMAND)

    def _begin_victory_animation(self) -> None:
        """Hold the combat result while the opponent desaturates and fades."""
        if self.victory_animation is not None:
            return
        self.outcome = "win"
        self.active_attack = None
        self.victory_animation = VictoryAnimation()
        self._set_state(BattleState.VICTORY_ANIMATION)

    def _update_victory_animation(self, dt: float) -> bool:
        victory = self.victory_animation
        assert victory is not None
        before = victory.elapsed
        victory.elapsed += max(0.0, dt)
        if not victory.vaporize_played and before < self.VICTORY_VAPORIZE_AT <= victory.elapsed:
            victory.vaporize_played = True
            self._audio_events.append(("sfx", "vaporized.wav"))
        if before < self.VICTORY_DIALOGUE_AT <= victory.elapsed:
            self._set_outcome("win")
        return True

    # -- availability, phase, dialogue ------------------------------------
    def available_player_moves(self) -> list[str]:
        return [move_id for move_id in self.player_move_ids
                if self.game_state.knows_move(move_id) and self._move_available(self._effective_player_move(move_id))]

    def _move_available(self, move: dict[str, Any]) -> bool:
        available = move.get("availability", {})
        if not self.game_state.knows_move(str(move.get("id", ""))):
            return False
        weapon_id = self.game_state.get_equipped("weapon")
        grants = self.items.get(weapon_id or "", {}).get("combat", {}).get("move_grants", [])
        if weapon_id and move.get("id") not in grants:
            return False
        if self.current_player_hp() < int(available.get("min_player_hp", 0)):
            return False
        if "max_enemy_hp" in available and self.enemy.hp > int(available["max_enemy_hp"]):
            return False
        for flag, wanted in available.get("requires_flags", {}).items():
            if self.game_state.get_flag(flag) != bool(wanted):
                return False
        if any(move_id not in self.used_player_moves for move_id in available.get("requires_moves_used", [])):
            return False
        condition = available.get("condition")
        return evaluate_condition(condition, self.game_state) if condition else True

    def _check_phases(self, resume: BattleState) -> bool:
        changed = False
        for index, phase in enumerate(self.config.phases):
            phase_id = str(phase.get("id", f"phase_{index}"))
            if phase_id in self.phase_ids or not self._phase_matches(phase.get("when", {})):
                continue
            changed |= self._activate_phase(phase, phase_id)
        if changed:
            return self._request_dialogue("phase_transition", resume, {"phase": self.phase_id})
        return False

    def _activate_phase(self, phase: dict[str, Any], phase_id: str) -> bool:
        """Run one phase's normal initialization actions exactly once."""
        if phase_id in self.phase_ids:
            self.phase_id = phase_id
            return False
        self.phase_ids.add(phase_id)
        self.phase_id = phase_id
        for action in phase.get("actions", []):
            self._apply_phase_action(action)
        self.logs.append(f"Phase: {phase.get('name', phase_id)}")
        return True

    def _activate_phase_by_id(self, wanted_phase_id: str) -> bool:
        """Enter a validated, config-selected phase outside condition checks."""
        for index, phase in enumerate(self.config.phases):
            phase_id = str(phase.get("id", f"phase_{index}"))
            if phase_id == wanted_phase_id:
                return self._activate_phase(phase, phase_id)
        # This is defensive; load_battle_config rejects this before battle
        # creation, but keeping a useful runtime error protects hand-built
        # configs in integrations and tests.
        raise RuntimeError(f"Battle {self.config.id!r} revival references missing phase {wanted_phase_id!r}")

    def _apply_phase_presentation_by_id(self, wanted_phase_id: str) -> None:
        """Stage visual phase overrides behind the revival's black curtain."""
        for index, phase in enumerate(self.config.phases):
            phase_id = str(phase.get("id", f"phase_{index}"))
            if phase_id != wanted_phase_id:
                continue
            for action in phase.get("actions", []):
                name, _value = next(iter(action.items()))
                if name in {"set_background", "set_enemy_sprite"}:
                    self._apply_phase_action(action)
            return
        raise RuntimeError(f"Battle {self.config.id!r} revival references missing phase {wanted_phase_id!r}")

    def _phase_matches(self, when: dict[str, Any]) -> bool:
        for name, value in when.items():
            if name == "enemy_hp_below" and not self.enemy.hp < int(value):
                return False
            if name == "enemy_hp_ratio_lte" and not self.enemy.hp / self.enemy.max_hp <= float(value):
                return False
            if name == "player_hp_below" and not self.current_player_hp() < int(value):
                return False
            if name == "turn_at_least" and not self.turn >= int(value):
                return False
            if name == "move_used" and self.last_player_move != value:
                return False
            if name == "item_used" and self.last_item != value:
                return False
            if name == "fight_flag" and not self.fight_flags.get(str(value), False):
                return False
            if name == "previous_phase" and value not in self.phase_ids:
                return False
        return True

    def _apply_phase_action(self, action: dict[str, Any]) -> None:
        name, value = next(iter(action.items()))
        if name == "add_enemy_move" and value not in self.enemy_move_ids:
            self.enemy_move_ids.append(value)
        elif name == "remove_enemy_move" and value in self.enemy_move_ids:
            self.enemy_move_ids.remove(value)
        elif name == "set_enemy_weight":
            self.enemy_weights[str(value["move"])] = float(value["weight"])
        elif name == "add_player_move" and value not in self.player_move_ids:
            self.player_move_ids.append(value)
        elif name == "remove_player_move" and value in self.player_move_ids:
            self.player_move_ids.remove(value)
        elif name == "replace_player_move":
            if value["old"] in self.player_move_ids:
                self.player_move_ids[self.player_move_ids.index(value["old"])] = value["new"]
        elif name == "augment_player_move":
            self.player_augments.setdefault(value["move"], []).append(dict(value["fields"]))
        elif name == "augment_enemy_pattern":
            self.pattern_augments.setdefault(value["pattern"], []).append(dict(value["fields"]))
        elif name == "augment_defense_sequence":
            self.pattern_augments.setdefault(value["sequence"], []).append(dict(value["fields"]))
        elif name == "set_arena":
            self._arena_overrides.update(value)
        elif name == "set_background":
            self._background_override = str(value)
        elif name == "set_enemy_sprite":
            self._enemy_sprite_override = str(value)
        elif name == "set_fight_flag":
            self.fight_flags.update({key: bool(flag_value) for key, flag_value in value.items()})

    def _request_dialogue(self, trigger: str, resume: BattleState, context: dict[str, Any]) -> bool:
        lines: list[str] = []
        dialogue_type = "modal"
        pause = self.OPPONENT_DIALOGUE_PAUSE
        for index, entry in enumerate(self.config.dialogue):
            if entry.get("trigger") != trigger or not self._dialogue_matches(entry, context):
                continue
            key = str(entry.get("id", f"{trigger}:{index}"))
            if entry.get("once", not entry.get("repeatable", False)) and key in self._used_dialogue:
                continue
            self._used_dialogue.add(key)
            pool = entry.get("pool")
            text = str(self.rng.choice(pool) if isinstance(pool, list) else entry.get("text", ""))
            entry_type = entry.get("type", "modal")
            if entry_type == "remark":
                self.remark_text = text or None
                continue
            if entry_type == "environment":
                self._show_environment_dialogue(text)
                continue
            dialogue_type = str(entry_type)
            pause = float(entry.get("pause", self.OPPONENT_DIALOGUE_PAUSE))
            lines.append(text)
            # An opponent speech window is an automatic, single line between
            # turns.  Do not queue a second one for the same transition.
            if dialogue_type == "opponent":
                break
        if lines:
            self._show_dialogue(lines, resume, dialogue_type, pause)
            return True
        return False

    def _show_environment_dialogue(self, text: str) -> None:
        """Start a non-blocking, single-line environmental description."""
        self.environment_text = " ".join(text.split()) or None
        self._visible_environment_characters = 0
        self._environment_dialogue_character_elapsed = 0.0
        self._environment_dialogue_word_letter_count = 0

    def _clear_environment_dialogue(self) -> None:
        self.environment_text = None
        self._visible_environment_characters = 0
        self._environment_dialogue_character_elapsed = 0.0
        self._environment_dialogue_word_letter_count = 0

    def _update_environment_dialogue(self, dt: float) -> bool:
        """Animate the passive caption and report whether it changed a frame."""
        text = self.environment_text
        if not text or self._visible_environment_characters >= len(text):
            return False
        self._environment_dialogue_character_elapsed += dt
        changed = False
        while (self._visible_environment_characters < len(text)
               and self._environment_dialogue_character_elapsed >= self.OPPONENT_DIALOGUE_CHARACTER_DELAY):
            char = text[self._visible_environment_characters]
            self._visible_environment_characters += 1
            self._environment_dialogue_character_elapsed -= self.OPPONENT_DIALOGUE_CHARACTER_DELAY
            changed = True
            if char.isalpha():
                self._environment_dialogue_word_letter_count += 1
                if self._environment_dialogue_word_letter_count % self.OPPONENT_DIALOGUE_BLIP_EVERY_LETTERS == 0:
                    self._audio_events.append(("sfx", self.OPPONENT_DIALOGUE_BLIP_SFX))
            else:
                self._environment_dialogue_word_letter_count = 0
        return changed

    def _dialogue_matches(self, entry: dict[str, Any], context: dict[str, Any]) -> bool:
        when = entry.get("when", {})
        if not isinstance(when, dict):
            return False
        if "move" in when and context.get("move") != when["move"]:
            return False
        if "item" in when and context.get("item") != when["item"]:
            return False
        if "phase" in when and context.get("phase") != when["phase"]:
            return False
        if "enemy_hp_below" in when and not self.enemy.hp < int(when["enemy_hp_below"]):
            return False
        if "player_hp_below" in when and not self.current_player_hp() < int(when["player_hp_below"]):
            return False
        return True

    def _show_dialogue(self, lines: list[str], resume: BattleState, dialogue_type: str = "modal",
                       pause: float | None = None, on_complete: Callable[[], None] | None = None,
                       typewriter: bool = False, typewriter_sound: str | None = None,
                       typewriter_character_delay: float | None = None) -> None:
        if self.state == BattleState.DIALOGUE:
            self._dialogue_lines.extend(line for line in lines if line)
            return
        self._dialogue_lines = [line for line in lines if line]
        self._dialogue_resume = resume
        self._dialogue_complete_callback = on_complete
        self._dialogue_typewriter = typewriter
        self._dialogue_typewriter_sound = typewriter_sound
        self._dialogue_typewriter_character_delay = float(
            typewriter_character_delay if typewriter_character_delay is not None
            else self.OPPONENT_DIALOGUE_CHARACTER_DELAY
        )
        self._dialogue_type = dialogue_type
        self._dialogue_pause_remaining = None
        self._opponent_dialogue_pause = float(pause if pause is not None else self.OPPONENT_DIALOGUE_PAUSE)
        self._visible_opponent_dialogue_characters = 0
        self._opponent_dialogue_character_elapsed = 0.0
        self._opponent_dialogue_word_letter_count = 0
        self._opponent_dialogue_prepared = False
        self._opponent_wait_for_attack_animation = dialogue_type == "opponent" and resume == BattleState.ENEMY_SELECT
        self._opponent_dialogue_start_pause_remaining = 0.0
        self._set_state(BattleState.DIALOGUE, reset_selection=False)

    def _update_opponent_dialogue(self, dt: float) -> None:
        """Type one opponent line, then hold it before the enemy acts."""
        if self._opponent_wait_for_attack_animation:
            if self.animations.active:
                return
            self._opponent_wait_for_attack_animation = False
            self._opponent_dialogue_start_pause_remaining = self.OPPONENT_DIALOGUE_START_PAUSE
            return
        if self._opponent_dialogue_start_pause_remaining > 0:
            self._opponent_dialogue_start_pause_remaining = max(0.0, self._opponent_dialogue_start_pause_remaining - dt)
            return
        text = self.dialogue_text or ""
        if self._visible_opponent_dialogue_characters < len(text):
            self._opponent_dialogue_character_elapsed += dt
            while (self._visible_opponent_dialogue_characters < len(text)
                   and self._opponent_dialogue_character_elapsed >= self.OPPONENT_DIALOGUE_CHARACTER_DELAY):
                char = text[self._visible_opponent_dialogue_characters]
                self._visible_opponent_dialogue_characters += 1
                self._opponent_dialogue_character_elapsed -= self.OPPONENT_DIALOGUE_CHARACTER_DELAY
                if char.isalpha():
                    self._opponent_dialogue_word_letter_count += 1
                    if self._opponent_dialogue_word_letter_count % self.OPPONENT_DIALOGUE_BLIP_EVERY_LETTERS == 0:
                        self._audio_events.append(("sfx", self.OPPONENT_DIALOGUE_BLIP_SFX))
                else:
                    self._opponent_dialogue_word_letter_count = 0
            return
        if self._dialogue_pause_remaining is None:
            # The configured pause begins only after the final character is
            # visible, so text length never shortens the reading time.
            self._dialogue_pause_remaining = self._opponent_dialogue_pause
            return
        self._dialogue_pause_remaining -= dt
        if self._dialogue_pause_remaining <= 1e-6:
            self._advance_dialogue()

    def _advance_dialogue(self) -> None:
        if self._dialogue_lines:
            self._dialogue_lines.pop(0)
        if self._dialogue_lines and self._dialogue_typewriter:
            self._visible_opponent_dialogue_characters = 0
            self._opponent_dialogue_character_elapsed = 0.0
            self._opponent_dialogue_word_letter_count = 0
            return
        if not self._dialogue_lines:
            resume, self._dialogue_resume = self._dialogue_resume or BattleState.COMMAND, None
            callback, self._dialogue_complete_callback = self._dialogue_complete_callback, None
            self._dialogue_type = "modal"
            self._dialogue_typewriter = False
            self._dialogue_typewriter_sound = None
            self._revival_dialogue_fade_remaining = 0.0
            self._revival_dialogue_next_line_delay_remaining = 0.0
            self._dialogue_pause_remaining = None
            self._visible_opponent_dialogue_characters = 0
            self._opponent_dialogue_character_elapsed = 0.0
            self._opponent_dialogue_word_letter_count = 0
            self._opponent_dialogue_prepared = False
            self._opponent_wait_for_attack_animation = False
            self._opponent_dialogue_start_pause_remaining = 0.0
            if callback is not None:
                callback()
                return
            if resume == BattleState.PLAYER_ATTACK:
                self._start_player_attack()
            elif resume == BattleState.ENEMY_TELEGRAPH:
                self._begin_telegraph()
            else:
                self._set_state(resume)

    # -- controlled configuration augmentation ---------------------------
    def _effective_player_move(self, move_id: str, level: int | None = None) -> dict[str, Any]:
        if level is None:
            level = self.skill_tracker.current_level(move_id)
        move = resolve_combat_move(self.config.player_moves[move_id], level)
        for fields in self.player_augments.get(move_id, []):
            move["base_power"] = (float(move.get("base_power", 0)) + float(fields.get("base_power_add", 0))) * float(fields.get("base_power_multiplier", 1))
            scoring = move.setdefault("scoring", {})
            for key in ("perfect_threshold", "minimum_multiplier", "maximum_multiplier"):
                if key in fields:
                    scoring[key] = fields[key]
            cfg = move.setdefault("pattern_config", {})
            if isinstance(move.get("qte"), dict):
                cfg = move["qte"].setdefault("parameters", {})
            if "timing_window_multiplier" in fields:
                for key in ("perfect_window", "good_window", "target_radius"):
                    if key in cfg:
                        cfg[key] = float(cfg[key]) * float(fields["timing_window_multiplier"])
            if "qte_speed_multiplier" in fields:
                cfg["speed_multiplier"] = float(cfg.get("speed_multiplier", 1)) * float(fields["qte_speed_multiplier"])
        return move

    def _effective_enemy_pattern(self, pattern_id: str) -> dict[str, Any]:
        pattern = deepcopy(self.config.enemy_patterns[pattern_id])
        for fields in self.pattern_augments.get(pattern_id, []):
            pattern["duration"] = (float(pattern.get("duration", 0)) + float(fields.get("duration_add", 0))) * float(fields.get("duration_multiplier", 1))
            arena = pattern.setdefault("arena", {})
            if "player_speed_multiplier" in fields:
                arena["player_speed"] = float(arena.get("player_speed", self.arena.get("player_speed", 120))) * float(fields["player_speed_multiplier"])
            if "arena_size_multiplier" in fields:
                for key in ("width", "height"):
                    arena[key] = float(arena.get(key, self.arena[key])) * float(fields["arena_size_multiplier"])
            for event in pattern.get("timeline", []):
                if event.get("action") in {"spawn_repeated", "spawn_sweep", "spawn_rotating"} and "spawn_interval_multiplier" in fields:
                    event["repeat"]["interval"] = float(event["repeat"]["interval"]) * float(fields["spawn_interval_multiplier"])
                if event.get("action") in {"spawn_repeated", "spawn_sweep", "spawn_rotating"} and "projectile_count_add" in fields:
                    event["repeat"]["count"] = max(0, int(event["repeat"]["count"]) + int(fields["projectile_count_add"]))
                if event.get("action") in {"spawn_repeated", "spawn_sweep", "spawn_rotating"} and "projectile_count_multiplier" in fields:
                    event["repeat"]["count"] = max(0, round(int(event["repeat"]["count"]) * float(fields["projectile_count_multiplier"])))
                projectile = event.get("projectile")
                if not isinstance(projectile, dict):
                    continue
                speed_multiplier = float(fields.get("projectile_speed_multiplier", 1))
                if isinstance(projectile.get("velocity"), dict):
                    projectile["velocity"] = {
                        "x": float(projectile["velocity"].get("x", 0)) * speed_multiplier,
                        "y": float(projectile["velocity"].get("y", 0)) * speed_multiplier,
                    }
                else:
                    projectile["speed"] = float(projectile.get("speed", 1)) * speed_multiplier
                projectile["damage"] = (float(projectile.get("damage", 1)) + float(fields.get("damage_add", 0))) * float(fields.get("damage_multiplier", 1))
                if "projectile_size_multiplier" in fields:
                    size = projectile.get("size", 8)
                    projectile["size"] = [float(v) * float(fields["projectile_size_multiplier"]) for v in size] if isinstance(size, list) else float(size) * float(fields["projectile_size_multiplier"])
                if event.get("action") == "spawn_radial" and "projectile_count_add" in fields:
                    event["count"] = max(1, int(event["count"]) + int(fields["projectile_count_add"]))
                if event.get("action") == "spawn_radial" and "projectile_count_multiplier" in fields:
                    event["count"] = max(1, round(int(event["count"]) * float(fields["projectile_count_multiplier"])))
            self._augment_modern_defense_patterns(pattern, fields)
        return pattern

    @staticmethod
    def _scale_defense_value(value: Any, multiplier: float, addition: float = 0.0) -> Any:
        """Scale a number or an authored ``{min,max}`` tuning range."""
        if isinstance(value, dict) and "min" in value and "max" in value:
            result = dict(value)
            result["min"] = (float(result["min"]) + addition) * multiplier
            result["max"] = (float(result["max"]) + addition) * multiplier
            return result
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return (float(value) + addition) * multiplier
        return value

    def _augment_modern_defense_patterns(self, pattern: dict[str, Any], fields: dict[str, Any]) -> None:
        """Apply existing phase tuning knobs to YAML ``patterns`` entries.

        Old timeline augments remain above.  This small recursive visitor
        keeps the same gameplay-phase feature useful for the generic defense
        library without coupling the controller to individual pattern types.
        """
        speed_multiplier = float(fields.get("projectile_speed_multiplier", 1))
        interval_multiplier = float(fields.get("spawn_interval_multiplier", 1))
        count_add = int(fields.get("projectile_count_add", 0))
        count_multiplier = float(fields.get("projectile_count_multiplier", 1))
        size_multiplier = float(fields.get("projectile_size_multiplier", 1))
        damage_add = float(fields.get("damage_add", 0))
        damage_multiplier = float(fields.get("damage_multiplier", 1))
        telegraph_add = float(fields.get("telegraph_duration_add", 0))
        telegraph_multiplier = float(fields.get("telegraph_duration_multiplier", 1))

        def tune_projectile(projectile: dict[str, Any]) -> None:
            if "speed" in projectile:
                projectile["speed"] = self._scale_defense_value(projectile["speed"], speed_multiplier)
            if "damage" in projectile:
                projectile["damage"] = self._scale_defense_value(projectile["damage"], damage_multiplier, damage_add)
            elif damage_add or damage_multiplier != 1:
                projectile["damage"] = (1 + damage_add) * damage_multiplier
            for key in ("size", "radius", "collision_radius"):
                if key in projectile:
                    value = projectile[key]
                    projectile[key] = [self._scale_defense_value(item, size_multiplier) for item in value] if isinstance(value, list) else self._scale_defense_value(value, size_multiplier)

        def tune_entry(entry: Any) -> None:
            if not isinstance(entry, dict):
                return
            for key in ("fire_interval", "spawn_interval", "interval", "burst_interval", "wall_interval", "placement_interval", "lane_interval"):
                if key in entry:
                    entry[key] = self._scale_defense_value(entry[key], interval_multiplier)
            for key in ("projectile_count", "count", "arms", "region_count", "number_of_chasers"):
                if key in entry and isinstance(entry[key], (int, float)) and not isinstance(entry[key], bool):
                    entry[key] = max(1, round((float(entry[key]) + count_add) * count_multiplier))
            for key in ("speed", "wall_speed", "expansion_speed", "movement_speed"):
                if key in entry:
                    entry[key] = self._scale_defense_value(entry[key], speed_multiplier)
            if "damage" in entry:
                entry["damage"] = self._scale_defense_value(entry["damage"], damage_multiplier, damage_add)
            if "warning_duration" in entry:
                entry["warning_duration"] = self._scale_defense_value(entry["warning_duration"], telegraph_multiplier, telegraph_add)
            telegraph = entry.get("telegraph")
            if isinstance(telegraph, dict) and "duration" in telegraph:
                telegraph["duration"] = self._scale_defense_value(telegraph["duration"], telegraph_multiplier, telegraph_add)
            projectile = entry.get("projectile")
            if isinstance(projectile, dict):
                tune_projectile(projectile)
            repeat = entry.get("repeat")
            if isinstance(repeat, dict) and "interval" in repeat:
                repeat["interval"] = self._scale_defense_value(repeat["interval"], interval_multiplier)

        for entry in pattern.get("patterns", []):
            tune_entry(entry)
        for entries in pattern.get("pattern_groups", {}).values() if isinstance(pattern.get("pattern_groups"), dict) else []:
            for entry in entries if isinstance(entries, list) else []:
                tune_entry(entry)

    def _set_enemy_cooldown(self, move_id: str) -> None:
        for active_id in list(self.enemy_cooldowns):
            self.enemy_cooldowns[active_id] = max(0, self.enemy_cooldowns[active_id] - 1)
        self.enemy_cooldowns[move_id] = int(self.config.enemy_moves[move_id].get("cooldown", 0))

    def _decrement_effects(self) -> None:
        for effect_id, effect in list(self.active_effects.items()):
            if "turns" in effect:
                effect["turns"] = int(effect["turns"]) - 1
                if effect["turns"] <= 0:
                    self.active_effects.pop(effect_id)

    def _set_outcome(self, outcome: str) -> None:
        self.outcome = outcome
        target = {"win": BattleState.VICTORY, "lose": BattleState.DEFEAT, "escape": BattleState.ESCAPE}[outcome]
        extra = self.config.victory.get("text") if outcome == "win" else self.config.defeat.get("text") if outcome == "lose" else "You escape."
        if extra:
            self.logs.append(str(extra))
        trigger = {"win": "victory", "lose": "defeat", "escape": "after_enemy_pattern"}[outcome]
        if not self._request_dialogue(trigger, target, {}):
            self._set_state(target)

    def _sync_health_animation(self, *, snap_player: bool = False) -> None:
        self.animations.set_health("player", self.current_player_hp(), self.maximum_player_hp(),
                                   immediate=snap_player)
        self.animations.set_health("enemy", self.enemy.hp, self.enemy.max_hp)

    def _set_state(self, state: BattleState, reset_selection: bool = True) -> None:
        self.state = state
        if reset_selection:
            self.selected = 0
