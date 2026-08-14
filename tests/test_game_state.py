from engine.core.game_state import GameState


def test_flags():
    s = GameState()
    assert s.get_flag("nope") is False
    s.set_flag("met_wolf", True)
    assert s.get_flag("met_wolf") is True


def test_variables():
    s = GameState()
    s.add_var("gold", 10)
    s.add_var("gold", 5)
    assert s.get_var("gold") == 15
    assert s.get_var("undefined", default=0) == 0


def test_inventory():
    s = GameState()
    s.add_item("torch")
    s.add_item("sword", 2)
    assert s.has_item("torch")
    assert s.has_item("sword", 2)
    assert not s.has_item("sword", 3)
    s.remove_item("sword", 1)
    assert s.has_item("sword", 1)
    assert not s.has_item("sword", 2)
    s.remove_item("sword", 1)
    assert "sword" not in s.inventory  # fully removed, not left at 0


def test_serialization_round_trip():
    s = GameState(current_scene="x", flags={"a": True}, variables={"gold": 3},
                  inventory={"torch": 1}, stats={"hp": 10})
    d = s.to_dict()
    s2 = GameState.from_dict(d)
    assert s2.to_dict() == d


def test_equipment_round_trips_and_manifest_can_initialize_it():
    s = GameState()
    s.equip_item("weapon", "trail_sword")
    assert GameState.from_dict(s.to_dict()).get_equipped("weapon") == "trail_sword"
    initialized = GameState.new_from_manifest({"starting_equipment": {"weapon": "trail_sword"}})
    assert initialized.get_equipped("weapon") == "trail_sword"


def test_learned_moves_round_trip_and_player_profile_initializes_them():
    state = GameState()
    state.learn_move("hammer_crush")
    state.learn_move("hammer_crush")
    assert state.known_moves == ["hammer_crush"]
    assert GameState.from_dict(state.to_dict()).knows_move("hammer_crush")

    initialized = GameState.new_from_manifest(
        {"start_scene": "intro"},
        {"stats": {"hp": 20}, "equipment": {"weapon": "iron_hammer"}, "known_moves": ["hammer_crush"]},
    )
    assert initialized.get_stat("hp") == 20
    assert initialized.get_equipped("weapon") == "iron_hammer"
    assert initialized.knows_move("hammer_crush")


def test_new_from_manifest():
    manifest = {
        "start_scene": "intro",
        "starting_stats": {"hp": 20},
        "starting_inventory": ["torch"],
        "starting_flags": {"a": True},
        "starting_variables": {"gold": 3},
    }
    s = GameState.new_from_manifest(manifest)
    assert s.current_scene == "intro"
    assert s.get_stat("hp") == 20
    assert s.has_item("torch")
    assert s.get_flag("a")
    assert s.get_var("gold") == 3


def test_profile_inventory_layout_mapping_does_not_become_fake_item_records():
    state = GameState.new_from_manifest(
        {"start_scene": "intro"},
        {"inventory": {"columns": 3, "rows": 2, "items": {"apple": 2}}},
    )
    assert state.inventory == {"apple": 2}
