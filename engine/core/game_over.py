"""Timed presentation state for YAML-authored battle game-over sequences.

The state is deliberately pygame-free.  The game engine owns save loading and
exit decisions, while the renderer only observes the heart/shard data here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import random

from engine.battle.controller import DeathAnimation, create_death_animation


class GameOverStage(Enum):
    INTRO = "intro"
    MENU = "menu"
    GET_UP_DELAY = "get_up_delay"
    GET_UP_RESTORED = "get_up_restored"
    GET_UP_FADE = "get_up_fade"
    LOAD_SAVE = "load_save"
    DIE_DELAY = "die_delay"
    DIE_SHATTER = "die_shatter"


@dataclass
class GameOverPresentation:
    """The fixed menu timeline following one game-over cutscene."""

    x: float
    y: float
    rng: random.Random
    text: str = "Game over"
    stage: GameOverStage = GameOverStage.INTRO
    stage_elapsed: float = 0.0
    visible_characters: int = 0
    text_character_elapsed: float = 0.0
    word_letter_count: int = 0
    menu_delay_elapsed: float = 0.0
    death_animation: DeathAnimation | None = None
    audio_events: list[str] = field(default_factory=list)
    load_ready: bool = False
    finished: bool = False

    MENU_DELAY = 1.0
    GET_UP_DELAY_DURATION = 1.0
    # This matches the heart-shake duration just before the heart splits.
    GET_UP_RESTORE_DURATION = 0.25
    GET_UP_FADE_DURATION = 1.0
    GET_UP_LOAD_DELAY = 1.0
    DIE_DELAY_DURATION = 0.25
    DIE_SHATTER_DURATION = 5.0
    TEXT_CHARACTER_DELAY = 0.1
    DIALOGUE_BLIP_EVERY_LETTERS = 2
    DIALOGUE_BLIP_SFX = "dialog_blip.wav"
    MENU_AFTER_TEXT_DELAY = 1.0

    # Match the established battle-loss timing so the shared renderer draws
    # its existing shard presentation without another asset format.
    DEATH_INITIAL_PAUSE = 0.7
    DEATH_HEART_SHAKE_DURATION = 0.25
    DEATH_BREAK_1_AT = DEATH_INITIAL_PAUSE + DEATH_HEART_SHAKE_DURATION
    DEATH_BREAK_2_AT = DEATH_BREAK_1_AT + 0.5
    DEATH_DEBRIS_AT = DEATH_BREAK_2_AT + 0.375

    @property
    def show_menu(self) -> bool:
        return (self.stage == GameOverStage.MENU and self.visible_characters >= len(self.text)
                and self.menu_delay_elapsed + 1e-9 >= self.MENU_AFTER_TEXT_DELAY)

    @property
    def show_heart(self) -> bool:
        return self.stage != GameOverStage.LOAD_SAVE

    @property
    def heart_sprite(self) -> str:
        return "heart.png" if self.stage in {
            GameOverStage.INTRO, GameOverStage.GET_UP_RESTORED, GameOverStage.GET_UP_FADE,
        } else "heart_break.png"

    @property
    def heart_shaking(self) -> bool:
        """Whether the restored heart uses the loss transition's shake."""
        return self.stage == GameOverStage.GET_UP_RESTORED

    def consume_audio_events(self) -> list[str]:
        events, self.audio_events = self.audio_events, []
        return events

    @property
    def visible_text(self) -> str:
        """Game-over text always types on, independent of scene settings."""
        return self.text[:self.visible_characters]

    @property
    def heart_alpha(self) -> int:
        if self.stage != GameOverStage.GET_UP_FADE:
            return 255
        return max(0, min(255, round(255 * (1 - self.stage_elapsed / self.GET_UP_FADE_DURATION))))

    def choose_get_up(self) -> bool:
        if not self.show_menu:
            return False
        self.stage = GameOverStage.GET_UP_DELAY
        self.stage_elapsed = 0.0
        return True

    def choose_die(self) -> bool:
        if not self.show_menu:
            return False
        self.stage = GameOverStage.DIE_DELAY
        self.stage_elapsed = 0.0
        return True

    def update(self, dt: float) -> tuple[bool, bool]:
        """Advance the timeline; return ``(changed, request_music_fade)``."""
        remaining = max(0.0, dt)
        changed = False
        request_music_fade = False
        if self.stage == GameOverStage.MENU:
            while remaining > 1e-9 and self.visible_characters < len(self.text):
                step = min(remaining, self.TEXT_CHARACTER_DELAY - self.text_character_elapsed)
                self.text_character_elapsed += step
                remaining -= step
                changed |= step > 0
                if self.text_character_elapsed + 1e-9 >= self.TEXT_CHARACTER_DELAY:
                    character = self.text[self.visible_characters]
                    self.visible_characters += 1
                    self.text_character_elapsed = 0.0
                    if character.isalpha():
                        self.word_letter_count += 1
                        if self.word_letter_count % self.DIALOGUE_BLIP_EVERY_LETTERS == 0:
                            self.audio_events.append(self.DIALOGUE_BLIP_SFX)
                    else:
                        self.word_letter_count = 0
            if self.visible_characters >= len(self.text) and remaining > 1e-9:
                step = min(remaining, self.MENU_AFTER_TEXT_DELAY - self.menu_delay_elapsed)
                self.menu_delay_elapsed += step
                changed |= step > 0
            return changed, False
        while remaining > 1e-9:
            durations = {
                GameOverStage.INTRO: self.MENU_DELAY,
                GameOverStage.GET_UP_DELAY: self.GET_UP_DELAY_DURATION,
                GameOverStage.GET_UP_RESTORED: self.GET_UP_RESTORE_DURATION,
                GameOverStage.GET_UP_FADE: self.GET_UP_FADE_DURATION,
                GameOverStage.LOAD_SAVE: self.GET_UP_LOAD_DELAY,
                GameOverStage.DIE_DELAY: self.DIE_DELAY_DURATION,
                GameOverStage.DIE_SHATTER: self.DIE_SHATTER_DURATION,
            }
            duration = durations.get(self.stage)
            if duration is None:
                break
            step = min(remaining, duration - self.stage_elapsed)
            self.stage_elapsed += step
            remaining -= step
            changed |= step > 0
            if self.stage_elapsed + 1e-9 < duration:
                break
            self.stage_elapsed = 0.0
            if self.stage == GameOverStage.INTRO:
                self.stage = GameOverStage.MENU
            elif self.stage == GameOverStage.GET_UP_DELAY:
                self.stage = GameOverStage.GET_UP_RESTORED
                self.audio_events.append("heal.wav")
            elif self.stage == GameOverStage.GET_UP_RESTORED:
                self.stage = GameOverStage.GET_UP_FADE
                request_music_fade = True
            elif self.stage == GameOverStage.GET_UP_FADE:
                self.stage = GameOverStage.LOAD_SAVE
            elif self.stage == GameOverStage.DIE_DELAY:
                self.death_animation = create_death_animation(
                    self.x, self.y, self.rng,
                    heart_shake_start=self.DEATH_INITIAL_PAUSE,
                    heart_shake_duration=self.DEATH_HEART_SHAKE_DURATION,
                    break1_at=self.DEATH_BREAK_1_AT,
                    break2_at=self.DEATH_BREAK_2_AT,
                    debris_at=self.DEATH_DEBRIS_AT,
                )
                # The heart is already split; begin directly at the shared
                # debris portion and hold it for the authored five seconds.
                self.death_animation.elapsed = self.death_animation.debris_at
                self.death_animation.break1_played = self.death_animation.break2_played = True
                self.audio_events.append("break2.wav")
                self.stage = GameOverStage.DIE_SHATTER
            elif self.stage == GameOverStage.DIE_SHATTER:
                self.finished = True
                break
            elif self.stage == GameOverStage.LOAD_SAVE:
                self.load_ready = True
                break
        if self.stage == GameOverStage.DIE_SHATTER and self.death_animation is not None:
            self.death_animation.elapsed = self.death_animation.debris_at + self.stage_elapsed
        return changed, request_music_fade
