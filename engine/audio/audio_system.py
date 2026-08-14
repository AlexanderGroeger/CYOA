"""
engine/audio/audio_system.py

Wraps pygame-ce's mixer (audio only -- we never init pygame's display).
Fails soft in two independent ways, each logged once rather than crashing
or spamming:
    1. pygame-ce isn't installed at all.
    2. pygame-ce is installed but there's no usable audio device (CI,
       headless containers, some SSH sessions).
Either way, play_music/play_sfx become no-ops rather than raising, so a
story with sound never breaks on a machine that can't play it.
"""

from __future__ import annotations

import sys
from pathlib import Path

from engine.core.asset_loader import AssetLoader
from engine.errors import AssetNotFoundError

try:
    import pygame

    _PYGAME_AVAILABLE = True
except ImportError:
    _PYGAME_AVAILABLE = False


class AudioSystem:
    def __init__(
        self,
        assets: AssetLoader,
        master_volume: float = 0.8,
        music_volume: float = 1.0,
        effects_volume: float = 1.0,
    ):
        self.assets = assets
        self.master_volume = self._clamp_volume(master_volume)
        self.music_volume = self._clamp_volume(music_volume)
        self.effects_volume = self._clamp_volume(effects_volume)
        self._enabled = False
        self._current_music: str | None = None
        self._current_music_base_volume = 1.0
        self._sfx_cache: dict[str, "pygame.mixer.Sound"] = {}
        self._sfx_base_volumes: dict[str, float] = {}
        self._pitched_sfx_cache: dict[tuple[str, float], "pygame.mixer.Sound"] = {}
        self._pitched_sfx_base_volumes: dict[tuple[str, float], float] = {}
        self._warned_messages: set[str] = set()

        if not _PYGAME_AVAILABLE:
            self._warn("pygame-ce is not installed -- running without audio.")
            return
        try:
            pygame.mixer.init()
            self._enabled = True
        except pygame.error as e:
            self._warn(f"No usable audio device ({e}) -- running without audio.")

    def _warn(self, message: str) -> None:
        if message not in self._warned_messages:
            print(f"[audio] {message}", file=sys.stderr)
            self._warned_messages.add(message)

    @staticmethod
    def _clamp_volume(volume: float) -> float:
        return max(0.0, min(1.0, float(volume)))

    def _music_output_volume(self) -> float:
        return self.master_volume * self.music_volume * self._current_music_base_volume

    def _effects_output_volume(self, base_volume: float) -> float:
        return self.master_volume * self.effects_volume * base_volume

    def _resolve_music_path(self, filename: str) -> Path:
        """Resolve music, retaining support for older misplaced shared tracks."""
        try:
            return self.assets.resolve_asset_path("music", filename)
        except AssetNotFoundError:
            return self.assets.resolve_asset_path("sfx", filename)

    def play_music(self, filename: str | None, loop: bool = True, volume: float | None = None,
                   fade_in: float = 0.0) -> None:
        if not filename or filename == self._current_music:
            return
        self._current_music = filename
        self._current_music_base_volume = self._clamp_volume(1.0 if volume is None else volume)
        if not self._enabled:
            return
        try:
            path = self._resolve_music_path(filename)
            pygame.mixer.music.load(str(path))
            pygame.mixer.music.set_volume(self._music_output_volume())
            pygame.mixer.music.play(loops=-1 if loop else 0,
                                    fade_ms=max(0, round(float(fade_in) * 1000)))
        except Exception as e:  # noqa: BLE001 -- audio must never crash the game
            self._warn(f"Couldn't play music '{filename}': {e}")

    def play_music_sequence(self, intro_filename: str, loop_filename: str,
                            volume: float | None = None) -> None:
        """Play one intro followed gaplessly by an indefinitely looping track."""
        sequence_key = f"{intro_filename}\0{loop_filename}"
        if not intro_filename or not loop_filename or sequence_key == self._current_music:
            return
        self._current_music = sequence_key
        self._current_music_base_volume = self._clamp_volume(1.0 if volume is None else volume)
        if not self._enabled:
            return
        try:
            intro_path = self._resolve_music_path(intro_filename)
            loop_path = self._resolve_music_path(loop_filename)
            pygame.mixer.music.load(str(intro_path))
            pygame.mixer.music.set_volume(self._music_output_volume())
            # Queue before starting the intro so SDL_mixer owns the handoff;
            # polling for an end event would introduce a frame-sized gap.
            pygame.mixer.music.queue(str(loop_path), loops=-1)
            pygame.mixer.music.play(loops=0)
        except Exception as e:  # noqa: BLE001 -- audio must never crash the game
            self._warn(f"Couldn't play music sequence '{intro_filename}' -> '{loop_filename}': {e}")

    def stop_music(self) -> None:
        self._current_music = None
        if self._enabled:
            try:
                pygame.mixer.music.stop()
            except Exception:
                pass

    def fadeout_music(self, duration: float) -> None:
        """Fade the current music channel to silence without blocking a frame."""
        milliseconds = max(0, round(float(duration) * 1000))
        self._current_music = None
        if not self._enabled:
            return
        try:
            pygame.mixer.music.fadeout(milliseconds)
        except Exception as e:  # noqa: BLE001 -- audio must never crash the game
            self._warn(f"Couldn't fade music: {e}")

    def play_sfx(self, filename: str, volume: float | None = None, pitch: float = 1.0) -> None:
        """Play an effect, optionally resampling a cached copy for its pitch."""
        if not self._enabled:
            return
        try:
            sound = self._sfx_cache.get(filename)
            if sound is None:
                path = self.assets.resolve_asset_path("sfx", filename)
                sound = pygame.mixer.Sound(str(path))
                self._sfx_cache[filename] = sound
            pitch = max(0.01, float(pitch))
            if pitch != 1.0:
                sound = self._pitched_sound(filename, sound, pitch)
            base_volume = self._clamp_volume(1.0 if volume is None else volume)
            self._sfx_base_volumes[filename] = base_volume
            if pitch != 1.0:
                pitched_volumes = getattr(self, "_pitched_sfx_base_volumes", None)
                if pitched_volumes is None:
                    pitched_volumes = self._pitched_sfx_base_volumes = {}
                pitched_volumes[(filename, pitch)] = base_volume
            sound.set_volume(self._effects_output_volume(base_volume))
            sound.play()
        except Exception as e:  # noqa: BLE001
            self._warn(f"Couldn't play sfx '{filename}': {e}")

    def _pitched_sound(self, filename: str, sound: "pygame.mixer.Sound", pitch: float) -> "pygame.mixer.Sound":
        """Return a rate-scaled mixer-format copy, cached by source and pitch."""
        cache = getattr(self, "_pitched_sfx_cache", None)
        if cache is None:
            cache = self._pitched_sfx_cache = {}
        key = (filename, pitch)
        if key in cache:
            return cache[key]
        _frequency, sample_format, channels = pygame.mixer.get_init()
        frame_size = max(1, abs(sample_format) // 8 * channels)
        raw = sound.get_raw()
        frame_count = len(raw) // frame_size
        scaled = b"".join(
            raw[int(index * pitch) * frame_size:(int(index * pitch) + 1) * frame_size]
            for index in range(int(frame_count / pitch))
        )
        cache[key] = pygame.mixer.Sound(buffer=scaled)
        return cache[key]

    def preload_sfx(self, *filenames: str) -> None:
        """Decode effects ahead of time so input-triggered cues start promptly."""
        if not self._enabled:
            return
        for filename in filenames:
            if filename in self._sfx_cache:
                continue
            try:
                path = self.assets.resolve_asset_path("sfx", filename)
                self._sfx_cache[filename] = pygame.mixer.Sound(str(path))
            except Exception as e:  # noqa: BLE001 -- preloading must remain optional
                self._warn(f"Couldn't preload sfx '{filename}': {e}")

    def set_master_volume(self, volume: float) -> None:
        self.master_volume = self._clamp_volume(volume)
        self._refresh_output_volumes()

    def set_music_volume(self, volume: float) -> None:
        self.music_volume = self._clamp_volume(volume)
        self._refresh_output_volumes()

    def set_effects_volume(self, volume: float) -> None:
        self.effects_volume = self._clamp_volume(volume)
        self._refresh_output_volumes()

    def _refresh_output_volumes(self) -> None:
        """Apply preference changes to music and every cached sound effect."""
        if self._enabled:
            try:
                pygame.mixer.music.set_volume(self._music_output_volume())
            except Exception:
                pass
            for filename, sound in self._sfx_cache.items():
                try:
                    sound.set_volume(
                        self._effects_output_volume(self._sfx_base_volumes.get(filename, 1.0))
                    )
                except Exception:
                    pass
            for key, sound in getattr(self, "_pitched_sfx_cache", {}).items():
                try:
                    sound.set_volume(self._effects_output_volume(
                        getattr(self, "_pitched_sfx_base_volumes", {}).get(key, 1.0)
                    ))
                except Exception:
                    pass
