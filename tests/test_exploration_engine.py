"""Game-loop integration checks for the opt-in exploration state machine."""

from __future__ import annotations

from pathlib import Path

from engine.core.asset_loader import AssetLoader
from engine.core.game_engine import GameEngine
from engine.core.game_state import GameState
from engine.core.inventory import InventoryGrid, InventoryLayout, InventoryService
from engine.core.story_interpreter import StoryInterpreter
from engine.render.terminal_input import BACK, RIGHT, SELECT, SELECT_RELEASE


DEMO_STORY = Path(__file__).resolve().parent.parent / "stories" / "demo_story"


class _Time:
    ticks = 10_000

    @staticmethod
    def get_ticks() -> int:
        return _Time.ticks


class _Pygame:
    time = _Time()


class _PressedKeys:
    def __init__(self, pressed):
        self.pressed = pressed

    def __getitem__(self, key):
        return key in self.pressed


class _Keyboard:
    def __init__(self, pressed):
        self.pressed = pressed

    def get_pressed(self):
        return _PressedKeys(self.pressed)


class _HeldPygame(_Pygame):
    K_d, K_RIGHT, K_a, K_LEFT = 1, 2, 3, 4
    K_s, K_DOWN, K_w, K_UP = 5, 6, 7, 8
    K_RETURN, K_KP_ENTER = 9, 10

    def __init__(self, pressed):
        self.key = _Keyboard(pressed)


class _Config:
    width = 640
    height = 360


class _Renderer:
    pygame = _Pygame()
    config = _Config()

    def __init__(self):
        self.views = []

    @staticmethod
    def paginate_text(text, _font_size):
        return [text]

    @staticmethod
    def animation_changed(_scene):
        return False

    def render_exploration(self, _scene, view):
        self.views.append(view)

    @staticmethod
    def render(*_args, **_kwargs):
        pass


class _Audio:
    def __init__(self):
        self.sounds = []

    def play_sfx(self, filename, **_kwargs):
        self.sounds.append(filename)

    @staticmethod
    def play_music(_filename):
        pass

    @staticmethod
    def stop_music():
        pass


def _engine() -> GameEngine:
    assets = AssetLoader(str(DEMO_STORY), "shared_assets")
    state = GameState(
        current_scene="exploration_study",
        flags={"study_token_found": False, "archive_route_open": False},
        stats={"hp": 10, "max_hp": 10, "attack": 4, "defense": 2},
    )
    engine = GameEngine.__new__(GameEngine)
    engine.assets = assets
    engine.state = state
    engine.interpreter = StoryInterpreter(assets, state)
    engine.manifest = {"render": {}, "navigation": {}}
    engine.renderer = _Renderer()
    engine.audio = _Audio()
    engine.items = assets.load_items()
    engine.combat_move_config = assets.load_combat_move_config()
    engine.inventory = InventoryService(engine.items)
    engine.inventory_layout = InventoryLayout(columns=4, rows=3)
    engine.inventory_grid = InventoryGrid(engine.inventory_layout)
    engine.game_over = None
    engine._game_over_text = "Game over"
    engine.battle = None
    engine._scene_history = []
    engine._pending_selection = None
    engine._dialogue_input_unlock_ms = 0
    engine.running = True
    engine.message = None
    engine._battle_dt = 0.0
    engine.ending = False
    engine._enter_scene("exploration_study")
    return engine


def _complete_dialogue(engine: GameEngine) -> None:
    # First A/Enter completes typewriter text; the second confirms its page.
    assert engine.handle_action(SELECT)
    assert engine.handle_action(SELECT)


def test_scene_dialogue_enters_root_menu_and_move_rechecks_flag_gates():
    engine = _engine()
    _complete_dialogue(engine)

    assert engine._exploration_mode.name == "EXPLORATION_MENU"
    engine.handle_action(SELECT)  # root Move
    assert engine._exploration_mode.name == "MOVE_MENU"
    assert [entry["scene"] for entry in engine._exploration_render_view()["destinations"]] == ["exploration_hall"]

    engine.state.set_flag("archive_route_open", True)
    assert [entry["scene"] for entry in engine._exploration_render_view()["destinations"]] == [
        "exploration_hall", "exploration_archive",
    ]
    assert engine.handle_action(BACK)
    assert engine._exploration_mode.name == "EXPLORATION_MENU"


def test_move_menu_can_start_a_battle_and_carries_outcome_destinations():
    engine = _engine()
    _complete_dialogue(engine)
    engine.scene["exploration"]["navigation"] = [{
        "battle": "wolf_fight", "label": "Confront the wolf",
        "on_win": "cave_entrance", "on_lose": "forest_clearing",
    }]

    assert engine.handle_action(SELECT)
    assert engine.handle_action(SELECT)
    assert engine.battle is not None
    assert engine.battle.config.id == "wolf_fight"
    assert engine._battle_on_win == "cave_entrance"
    assert engine._battle_on_lose == "forest_clearing"


def test_look_event_dialogue_returns_to_look_and_persists_flagged_pickup_once():
    engine = _engine()
    _complete_dialogue(engine)
    engine.handle_action(RIGHT)  # Look
    engine.handle_action(SELECT)
    assert engine._exploration_mode.name == "LOOK_MODE"

    engine._exploration_cursor_x, engine._exploration_cursor_y = 317, 155
    assert engine.handle_action(SELECT)
    assert engine.handle_action(SELECT_RELEASE)
    assert engine._exploration_dialogue_active
    _complete_dialogue(engine)

    assert engine._exploration_mode.name == "LOOK_MODE"
    assert engine.state.has_item("ember_seal")
    assert engine.state.get_flag("study_token_found") is True
    # The same coordinate no longer resolves to the hidden pickup object.
    target = engine._exploration_render_view()["cursor"]
    assert target["interaction"] is None


def test_look_requires_a_complete_press_and_release_within_the_same_target():
    engine = _engine()
    _complete_dialogue(engine)
    engine.handle_action(RIGHT)
    engine.handle_action(SELECT)
    engine._exploration_cursor_x, engine._exploration_cursor_y = 317, 155

    assert engine.handle_action(SELECT)
    assert engine._exploration_render_view()["cursor"]["pressed"] is True
    assert not engine._exploration_dialogue_active
    assert engine.handle_action(SELECT_RELEASE)
    assert engine._exploration_dialogue_active

    outside_press = _engine()
    _complete_dialogue(outside_press)
    outside_press.handle_action(RIGHT)
    outside_press.handle_action(SELECT)
    assert outside_press.handle_action(SELECT)
    outside_press._exploration_cursor_x, outside_press._exploration_cursor_y = 317, 155
    assert outside_press.handle_action(SELECT_RELEASE)
    assert not outside_press._exploration_dialogue_active

    outside_release = _engine()
    _complete_dialogue(outside_release)
    outside_release.handle_action(RIGHT)
    outside_release.handle_action(SELECT)
    outside_release._exploration_cursor_x, outside_release._exploration_cursor_y = 317, 155
    assert outside_release.handle_action(SELECT)
    outside_release._exploration_cursor_x, outside_release._exploration_cursor_y = 0, 0
    assert outside_release.handle_action(SELECT_RELEASE)
    assert not outside_release._exploration_dialogue_active


def test_look_cursor_moves_while_a_direction_is_held_and_stays_on_canvas():
    engine = _engine()
    _complete_dialogue(engine)
    engine.handle_action(RIGHT)
    engine.handle_action(SELECT)
    engine.renderer.pygame = _HeldPygame({_HeldPygame.K_RIGHT, _HeldPygame.K_DOWN})
    engine._battle_dt = 1.0
    engine._exploration_cursor_x, engine._exploration_cursor_y = 630, 350
    engine._exploration_cursor_x_float, engine._exploration_cursor_y_float = 630.0, 350.0

    assert engine._update_exploration()
    assert (engine._exploration_cursor_x, engine._exploration_cursor_y) == (639, 359)


def test_bag_equips_and_backtracks_one_modal_level_at_a_time():
    engine = _engine()
    _complete_dialogue(engine)
    engine.state.add_item("archivist_blade")

    engine.handle_action(RIGHT)
    engine.handle_action(RIGHT)
    engine.handle_action(SELECT)
    assert engine._exploration_mode.name == "BAG"
    assert engine.handle_action(SELECT)
    assert engine._exploration_mode.name == "ITEM_ACTION_MENU"
    assert engine._exploration_item_actions == ("equip", "toss")
    assert engine.handle_action(SELECT)
    assert engine.state.get_equipped("weapon") == "archivist_blade"
    assert engine._exploration_mode.name == "BAG"

    assert engine.handle_action(SELECT)
    assert engine._exploration_item_actions == ("unequip", "toss")
    assert engine.handle_action(BACK)
    assert engine._exploration_mode.name == "BAG"
    assert engine.handle_action(BACK)
    assert engine._exploration_mode.name == "EXPLORATION_MENU"


def test_bag_use_runs_item_actions_in_the_same_nonblocking_event_pipeline():
    engine = _engine()
    _complete_dialogue(engine)
    engine.state.set_stat("hp", 3)
    engine.state.add_item("restorative_tea")

    engine.handle_action(RIGHT)
    engine.handle_action(RIGHT)
    engine.handle_action(SELECT)
    engine.handle_action(SELECT)
    assert engine._exploration_item_actions == ("use", "toss")
    assert engine.handle_action(SELECT)

    assert engine.state.get_stat("hp") == 10
    assert not engine.state.has_item("restorative_tea")
    assert engine._exploration_mode.name == "BAG"
