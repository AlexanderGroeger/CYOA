"""Audio cues emitted by the game-level coordinator."""

from __future__ import annotations

from pathlib import Path

from engine.battle.controller import BattleState
from engine.core.game_engine import GameEngine


class _AudioSpy:
    def __init__(self):
        self.sounds: list[str] = []

    def play_sfx(self, filename: str) -> None:
        self.sounds.append(filename)


def test_successful_save_plays_save_sound(monkeypatch):
    engine = GameEngine.__new__(GameEngine)
    engine.state = object()
    engine.story_id = "test_story"
    engine.story_version = "1"
    engine.save_dir = Path("saves")
    engine.save_slot = "slot1"
    engine.audio = _AudioSpy()
    monkeypatch.setattr("engine.core.game_engine.save_game", lambda *args: Path("slot1.json"))

    engine._handle_save()

    assert engine.audio.sounds == ["save.wav"]
    assert engine.message == "Game saved (slot1.json)."


def test_checkpoint_uses_a_dedicated_slot_without_a_save_sound(monkeypatch):
    engine = GameEngine.__new__(GameEngine)
    engine.state = object()
    engine.story_id = "test_story"
    engine.story_version = "1"
    engine.save_dir = Path("saves")
    engine.save_slot = "slot1"
    engine.audio = _AudioSpy()
    saved_slots: list[str] = []
    monkeypatch.setattr(
        "engine.core.game_engine.save_game",
        lambda _state, _story_id, _story_version, _save_dir, slot: saved_slots.append(slot),
    )

    engine._save_checkpoint()

    assert saved_slots == ["slot1_checkpoint"]
    assert engine.audio.sounds == []


def test_game_over_recovery_loads_the_checkpoint_instead_of_the_manual_save():
    class _Presentation:
        finished = False
        load_ready = True

        def update(self, _dt: float):
            return True, False

        def consume_audio_events(self):
            return []

    engine = GameEngine.__new__(GameEngine)
    engine.game_over = _Presentation()
    restored: list[bool] = []
    engine._handle_load_checkpoint = lambda: restored.append(True) or True
    engine._handle_load = lambda: (_ for _ in ()).throw(AssertionError("manual save must not be loaded"))

    assert engine._update_game_over(0.1)
    assert restored == [True]


def test_entering_an_authored_checkpoint_creates_the_recovery_snapshot():
    class _Interpreter:
        def enter_scene(self, scene_id: str):
            assert scene_id == "checkpoint_room"
            return {"id": scene_id, "checkpoint": True, "text": "Ready."}, []

        def available_choices(self, _scene):
            return [{"text": "Continue"}]

        def is_ending(self, _scene):
            return False

    class _Pygame:
        class time:
            @staticmethod
            def get_ticks():
                return 0

    class _Renderer:
        pygame = _Pygame()

        @staticmethod
        def paginate_text(text, _font_size):
            return [text]

        @staticmethod
        def render(*_args, **_kwargs):
            pass

    engine = GameEngine.__new__(GameEngine)
    engine.game_over = None
    engine._game_over_text = "Game over"
    engine.interpreter = _Interpreter()
    engine.manifest = {"render": {}, "navigation": {}}
    engine.renderer = _Renderer()
    engine.battle = None
    engine._scene_history = []
    engine.state = object()
    checkpoints: list[bool] = []
    engine._save_checkpoint = lambda: checkpoints.append(True)

    engine._enter_scene("checkpoint_room")

    assert checkpoints == [True]


def test_quick_time_attack_confirmation_does_not_play_menu_select_sound():
    class _BattleSpy:
        state = BattleState.PLAYER_ATTACK
        finished = False

        def handle_action(self, action: str) -> bool:
            assert action == "SELECT"
            return True

        def consume_audio_events(self):
            return []

    engine = GameEngine.__new__(GameEngine)
    engine._pending_selection = None
    engine.battle = _BattleSpy()
    engine.audio = _AudioSpy()

    assert engine.handle_action("SELECT")
    assert engine.audio.sounds == []


def test_rapid_slash_direction_does_not_play_the_menu_cursor_sound():
    class _Attack:
        qte_type = "rapid_slash"

    class _BattleSpy:
        state = BattleState.PLAYER_ATTACK
        active_attack = _Attack()
        finished = False

        def handle_action(self, action: str) -> bool:
            assert action == "LEFT"
            return True

        def consume_audio_events(self):
            return []

    engine = GameEngine.__new__(GameEngine)
    engine._pending_selection = None
    engine.battle = _BattleSpy()
    engine.audio = _AudioSpy()

    assert engine.handle_action("LEFT")
    assert engine.audio.sounds == []


def test_backing_out_of_a_battle_option_menu_plays_cursor_sound():
    class _BattleSpy:
        state = BattleState.MOVE_MENU
        finished = False

        def handle_action(self, action: str) -> bool:
            assert action == "BACK"
            return True

        def consume_audio_events(self):
            return []

    engine = GameEngine.__new__(GameEngine)
    engine._pending_selection = None
    engine.battle = _BattleSpy()
    engine.audio = _AudioSpy()

    assert engine.handle_action("BACK")
    assert engine.audio.sounds == ["menu_cursor.wav"]
