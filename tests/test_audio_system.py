"""Audio preference loading and mixer volume scaling."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.audio.audio_system import AudioSystem
from engine.core.asset_loader import AssetLoader


class _Music:
    def __init__(self):
        self.volumes: list[float] = []
        self.fadeouts: list[int] = []
        self.queued: list[tuple[str, int]] = []
        self.play_calls: list[int] = []

    def load(self, _path: str) -> None:
        pass

    def set_volume(self, volume: float) -> None:
        self.volumes.append(volume)

    def play(self, *, loops: int, fade_ms: int = 0) -> None:
        self.play_calls.append((loops, fade_ms))

    def queue(self, path: str, *, loops: int) -> None:
        self.queued.append((path, loops))

    def fadeout(self, milliseconds: int) -> None:
        self.fadeouts.append(milliseconds)


class _Sound:
    def __init__(self):
        self.volumes: list[float] = []
        self.play_count = 0

    def set_volume(self, volume: float) -> None:
        self.volumes.append(volume)

    def play(self) -> None:
        self.play_count += 1


def test_audio_yaml_is_optional_and_demo_preferences_are_loaded():
    assert AssetLoader("stories/demo_story").load_audio_config() == {
        "master_volume": 0.7,
        "music_volume": 1.0,
        "effects_volume": 1.0,
    }


def test_master_and_category_volumes_scale_mixer_output(monkeypatch):
    from engine.audio import audio_system

    music = _Music()
    sound = _Sound()

    class _Mixer:
        @staticmethod
        def Sound(_path: str) -> _Sound:
            return sound

    _Mixer.music = music

    class _Pygame:
        mixer = _Mixer

    class _Assets:
        @staticmethod
        def resolve_asset_path(_category: str, filename: str) -> Path:
            return Path(filename)

    monkeypatch.setattr(audio_system, "pygame", _Pygame)
    audio = AudioSystem.__new__(AudioSystem)
    audio.assets = _Assets()
    audio.master_volume = 0.7
    audio.music_volume = 0.4
    audio.effects_volume = 0.3
    audio._enabled = True
    audio._current_music = None
    audio._current_music_base_volume = 1.0
    audio._sfx_cache = {}
    audio._sfx_base_volumes = {}
    audio._warned_messages = set()

    audio.play_music("theme.wav", volume=0.5)
    audio.play_sfx("hit.wav", volume=0.5)

    assert music.volumes[-1] == pytest.approx(0.14)
    assert sound.volumes[-1] == pytest.approx(0.105)

    audio.set_master_volume(0.6)

    assert music.volumes[-1] == pytest.approx(0.12)
    assert sound.volumes[-1] == pytest.approx(0.09)


def test_music_fadeout_is_non_blocking_and_releases_the_current_track(monkeypatch):
    from engine.audio import audio_system

    music = _Music()

    class _Mixer:
        pass

    _Mixer.music = music

    class _Pygame:
        mixer = _Mixer

    monkeypatch.setattr(audio_system, "pygame", _Pygame)
    audio = AudioSystem.__new__(AudioSystem)
    audio._enabled = True
    audio._current_music = "refused_to_die.ogg"
    audio._warned_messages = set()

    audio.fadeout_music(1.0)

    assert music.fadeouts == [1000]
    assert audio._current_music is None


def test_music_can_fade_in_when_started(monkeypatch):
    from engine.audio import audio_system

    music = _Music()

    class _Mixer:
        pass

    _Mixer.music = music

    class _Pygame:
        mixer = _Mixer

    class _Assets:
        @staticmethod
        def resolve_asset_path(_category: str, filename: str) -> Path:
            return Path(filename)

    monkeypatch.setattr(audio_system, "pygame", _Pygame)
    audio = AudioSystem.__new__(AudioSystem)
    audio.assets = _Assets()
    audio.master_volume = audio.music_volume = 1.0
    audio._enabled = True
    audio._current_music = None
    audio._current_music_base_volume = 1.0
    audio._warned_messages = set()

    audio.play_music("refused_to_die.ogg", fade_in=0.5)

    assert music.play_calls == [(-1, 500)]


def test_music_sequence_queues_the_loop_before_starting_the_one_shot_intro(monkeypatch):
    from engine.audio import audio_system

    music = _Music()

    class _Mixer:
        pass

    _Mixer.music = music

    class _Pygame:
        mixer = _Mixer

    class _Assets:
        @staticmethod
        def resolve_asset_path(_category: str, filename: str) -> Path:
            return Path(filename)

    monkeypatch.setattr(audio_system, "pygame", _Pygame)
    audio = AudioSystem.__new__(AudioSystem)
    audio.assets = _Assets()
    audio.master_volume = audio.music_volume = 1.0
    audio._enabled = True
    audio._current_music = None
    audio._current_music_base_volume = 1.0
    audio._warned_messages = set()

    audio.play_music_sequence("true_hero_intro.ogg", "true_hero_loop.ogg")

    assert music.queued == [("true_hero_loop.ogg", -1)]
    assert music.play_calls == [(0, 0)]


def test_preloaded_effect_is_reused_when_played(monkeypatch):
    from engine.audio import audio_system

    sound = _Sound()

    class _Mixer:
        sound_loads = 0

        @classmethod
        def Sound(cls, _path: str) -> _Sound:
            cls.sound_loads += 1
            return sound

    class _Pygame:
        mixer = _Mixer

    class _Assets:
        @staticmethod
        def resolve_asset_path(_category: str, filename: str) -> Path:
            return Path(filename)

    monkeypatch.setattr(audio_system, "pygame", _Pygame)
    audio = AudioSystem.__new__(AudioSystem)
    audio.assets = _Assets()
    audio.master_volume = audio.music_volume = audio.effects_volume = 1.0
    audio._enabled = True
    audio._sfx_cache = {}
    audio._sfx_base_volumes = {}
    audio._warned_messages = set()

    audio.preload_sfx("hit.wav")
    audio.play_sfx("hit.wav")

    assert _Mixer.sound_loads == 1
    assert sound.play_count == 1


def test_pitched_effect_uses_a_shorter_cached_mixer_buffer(monkeypatch):
    from engine.audio import audio_system

    source = _Sound()
    source.get_raw = lambda: b"0123456789abcdef"  # type: ignore[attr-defined]
    pitched: list[tuple[bytes | None, _Sound]] = []

    class _Mixer:
        @staticmethod
        def Sound(path: str | None = None, *, buffer: bytes | None = None) -> _Sound:
            if buffer is None:
                return source
            scaled = _Sound()
            pitched.append((buffer, scaled))
            return scaled

        @staticmethod
        def get_init() -> tuple[int, int, int]:
            return (44_100, -16, 1)

    class _Pygame:
        mixer = _Mixer

    class _Assets:
        @staticmethod
        def resolve_asset_path(_category: str, filename: str) -> Path:
            return Path(filename)

    monkeypatch.setattr(audio_system, "pygame", _Pygame)
    audio = AudioSystem.__new__(AudioSystem)
    audio.assets = _Assets()
    audio.master_volume = audio.music_volume = audio.effects_volume = 1.0
    audio._enabled = True
    audio._sfx_cache = {}
    audio._sfx_base_volumes = {}
    audio._warned_messages = set()

    audio.play_sfx("hit.wav", pitch=2.0)
    audio.play_sfx("hit.wav", pitch=2.0)

    assert pitched[0][0] == b"0145" + b"89cd"
    assert len(pitched) == 1
    assert pitched[0][1].play_count == 2
