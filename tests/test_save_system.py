import pytest

from engine.core.game_state import GameState
from engine.errors import SaveVersionError
from engine.save.save_system import list_saves, load_game, save_game


@pytest.fixture
def state():
    return GameState(current_scene="forest_path", flags={"met_wolf": True}, variables={"gold": 42},
                      inventory={"torch": 1}, stats={"hp": 15, "max_hp": 20}, known_moves=["jab"])


def test_save_and_load_round_trip(tmp_path, state):
    save_dir = tmp_path / "saves"
    save_game(state, story_id="demo_story", story_version="1.0", save_dir=save_dir, slot="slot1")
    loaded = load_game(save_dir, "slot1", story_id="demo_story")
    assert loaded.current_scene == "forest_path"
    assert loaded.get_flag("met_wolf")
    assert loaded.get_var("gold") == 42
    assert loaded.has_item("torch")
    assert loaded.get_stat("hp") == 15
    assert loaded.knows_move("jab")


def test_load_rejects_wrong_story_id(tmp_path, state):
    save_dir = tmp_path / "saves"
    save_game(state, story_id="demo_story", story_version="1.0", save_dir=save_dir, slot="slot1")
    with pytest.raises(SaveVersionError):
        load_game(save_dir, "slot1", story_id="different_story")


def test_load_missing_slot_raises(tmp_path):
    with pytest.raises(SaveVersionError):
        load_game(tmp_path / "saves", "nope", story_id="demo_story")


def test_list_saves(tmp_path, state):
    save_dir = tmp_path / "saves"
    save_game(state, story_id="demo_story", story_version="1.0", save_dir=save_dir, slot="slot1")
    save_game(state, story_id="demo_story", story_version="1.0", save_dir=save_dir, slot="slot2")
    saves = list_saves(save_dir)
    assert {s["slot"] for s in saves} == {"slot1", "slot2"}


def test_list_saves_skips_corrupt_files(tmp_path, state):
    save_dir = tmp_path / "saves"
    save_game(state, story_id="demo_story", story_version="1.0", save_dir=save_dir, slot="slot1")
    (save_dir / "corrupt.json").write_text("{not valid json")
    saves = list_saves(save_dir)
    assert len(saves) == 1


def test_v1_save_without_new_inventory_equipment_fields_gets_safe_defaults(tmp_path):
    """Exploration is additive, so an old valid v1 state remains loadable."""
    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    (save_dir / "slot1.json").write_text(
        '{"save_format_version": 1, "story_id": "demo_story", "story_version": "1.0", '
        '"timestamp": 0, "state": {"current_scene": "intro", "flags": {"met_guard": true}}}'
    )

    loaded = load_game(save_dir, "slot1", "demo_story")

    assert loaded.current_scene == "intro"
    assert loaded.get_flag("met_guard")
    assert loaded.inventory == {}
    assert loaded.equipment == {}
    assert loaded.known_moves == []
