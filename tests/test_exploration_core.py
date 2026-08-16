"""Headless rules coverage for opt-in scene exploration."""

from __future__ import annotations

import pytest

from engine.core.exploration import (
    EventRunner,
    SceneRuntime,
    available_navigation,
    evaluate_conditions,
    look_target_at,
    resolve_dialogue,
    resolve_look_targets,
    resolve_scene_objects,
    validate_exploration_scene,
)
from engine.core.game_state import GameState
from engine.errors import StoryValidationError


SCENE = {
    "id": "study",
    "exploration": {
        "dialogue_sequences": {
            "first": "First visit.",
            "repeat": "Returned.",
            "drawer": "You find a key.",
        },
        "dialog": [
            {"conditions": {"all": [{"flag": "visited", "equals": False}]}, "sequence": "first"},
            {"conditions": {"any": [{"flag": "visited", "equals": True}, {"flag": "shortcut", "equals": True}]}, "sequence": "repeat"},
        ],
        "navigation": [
            {"scene": "hall", "label": "Hall"},
            {"scene": "archive", "label": "Archive", "conditions": {"all": [{"flag": "open", "equals": True}]}},
        ],
        "objects": [
            {
                "id": "drawer", "sprite": "drawer.png", "position": [20, 30], "size": [32, 20], "z": 3,
                "visible_when": {"flag": "drawer_hidden", "equals": False},
                "look": {
                    "interaction": "inspect", "rect": [20, 30, 32, 20],
                    "states": [
                        {"conditions": {"flag": "key_found", "equals": False}, "event": "find_key"},
                        {"conditions": {"flag": "key_found", "equals": True}, "event": "empty_drawer"},
                    ],
                },
            },
        ],
        "look_regions": [
            {"id": "wall", "rect": [0, 0, 80, 80], "interaction": "inspect", "event": "wall", "priority": 1},
            {"id": "switch", "rect": [20, 30, 10, 10], "interaction": "action", "event": "switch", "priority": 9},
        ],
        "look_events": {
            "find_key": {
                "actions": [
                    {"type": "dialog", "dialog": "drawer"},
                    {"type": "give_item", "item": "silver_key"},
                    {"type": "set_flag", "flag": "key_found", "value": True},
                ],
            },
            "empty_drawer": {"actions": [{"type": "dialog", "dialog": "repeat"}]},
            "wall": {"actions": []},
            "switch": {"actions": [{"type": "set_flag", "flag": "open", "value": True}]},
        },
    },
}


def test_structured_all_any_conditions_and_scene_dialogue_change_with_flags():
    state = GameState(flags={"visited": False, "shortcut": False})
    assert evaluate_conditions({"all": [{"flag": "visited", "equals": False}]}, state)
    assert not evaluate_conditions({"any": [{"flag": "visited", "equals": True}, {"flag": "shortcut", "equals": True}]}, state)
    assert resolve_dialogue(SCENE, state).identifier == "first"
    state.set_flag("visited", True)
    assert resolve_dialogue(SCENE, state).identifier == "repeat"


def test_once_dialogue_and_named_visit_flag_support_nonrepeating_intro_content():
    scene = {
        "id": "library",
        "exploration": {
            "visit_flag": "visited_library",
            "dialogue_sequences": {"first": "First.", "repeat": "Again."},
            "dialog": [{"once": True, "sequence": "first"}, {"sequence": "repeat"}],
        },
    }
    state = GameState()
    first = resolve_dialogue(scene, state)
    assert first.identifier == "first" and first.seen_flag
    state.set_flag(first.seen_flag, True)
    assert resolve_dialogue(scene, state).identifier == "repeat"


def test_navigation_filters_locked_destination_then_unlocks():
    state = GameState(flags={"open": False})
    assert [entry["scene"] for entry in available_navigation(SCENE, state)] == ["hall"]
    state.set_flag("open", True)
    assert [entry["scene"] for entry in available_navigation(SCENE, state)] == ["hall", "archive"]


def test_validator_accepts_battle_navigation_and_checks_its_targets():
    scene = {"id": "study", "exploration": {"navigation": [{
        "battle": "wolf_fight", "label": "Wolf", "on_win": "hall", "on_lose": "study",
    }]}}
    validate_exploration_scene(scene, "study", known_scene_ids={"study", "hall"}, known_battle_ids={"wolf_fight"})

    with pytest.raises(StoryValidationError, match="exactly one of scene or battle"):
        validate_exploration_scene({"id": "study", "exploration": {"navigation": [{"scene": "hall", "battle": "wolf_fight"}]}},
                                   "study", known_scene_ids={"hall"}, known_battle_ids={"wolf_fight"})
    with pytest.raises(StoryValidationError, match="nonexistent battle"):
        validate_exploration_scene(scene, "study", known_scene_ids={"study", "hall"}, known_battle_ids=set())
    with pytest.raises(StoryValidationError, match="nonexistent scene"):
        validate_exploration_scene({"id": "study", "exploration": {"navigation": [{"battle": "wolf_fight", "on_win": "missing"}]}},
                                   "study", known_scene_ids={"study"}, known_battle_ids={"wolf_fight"})


def test_visible_objects_and_look_targets_use_conditions_and_deterministic_priority():
    state = GameState(flags={"drawer_hidden": False, "key_found": False})
    objects = resolve_scene_objects(SCENE, state)
    assert [obj["id"] for obj in objects] == ["drawer"]
    targets = resolve_look_targets(SCENE, state)
    # The switch wins over both its background region and the drawer because
    # priority is the first deterministic tie-breaker.
    assert look_target_at(targets, 22, 32).id == "switch"
    drawer = next(target for target in targets if target.id == "drawer")
    assert drawer.event == "find_key"
    state.set_flag("key_found", True)
    drawer = next(target for target in resolve_look_targets(SCENE, state) if target.id == "drawer")
    assert drawer.event == "empty_drawer"
    state.set_flag("drawer_hidden", True)
    assert resolve_scene_objects(SCENE, state) == []


def test_event_runner_pauses_for_dialogue_then_applies_one_time_pickup_once_guarded_by_flags():
    state = GameState(flags={"key_found": False})
    runtime = SceneRuntime()
    actions = SCENE["exploration"]["look_events"]["find_key"]["actions"]
    runner = EventRunner(actions, state, runtime, item_ids={"silver_key"})
    signals = runner.advance(100)
    assert [(signal.kind, signal.data["dialog"]) for signal in signals] == [("dialog", "drawer")]
    assert not state.has_item("silver_key")
    runner.resume_dialogue(101)
    assert runner.finished
    assert state.has_item("silver_key") and state.get_flag("key_found")
    next_target = next(target for target in resolve_look_targets(SCENE, state) if target.id == "drawer")
    assert next_target.event == "empty_drawer"


def test_runtime_object_changes_are_nonpersistent_scene_presentation_state():
    state = GameState()
    runtime = SceneRuntime()
    runner = EventRunner([
        {"type": "change_sprite", "target": "drawer", "sprite": "open.png"},
        {"type": "hide_object", "target": "drawer"},
        {"type": "show_object", "target": "drawer"},
        {"type": "animation", "target": "drawer", "animation": "open"},
    ], state, runtime)
    runner.advance()
    assert runtime.sprite_overrides == {"drawer": "open.png"}
    assert runtime.object_animations == {"drawer": "open"}
    assert "drawer" in runtime.shown_objects and "drawer" not in runtime.hidden_objects


def test_canonical_object_actions_update_runtime_state_and_destroy_only_the_runtime_copy():
    scene = {
        "id": "study",
        "exploration": {
            "objects": [{"id": "drawer", "position": [10, 20], "size": [30, 15], "sprite": "closed.png"}],
        },
    }
    authored = scene["exploration"]["objects"][0].copy()
    runtime = SceneRuntime()
    runner = EventRunner([
        {"type": "move_object", "object": "drawer", "position": [40, 50]},
        {"type": "rotate_object", "object": "drawer", "angle": 30},
        {"type": "change_object_sprite", "object": "drawer", "sprite": "open.png"},
        {"type": "play_object_animation", "object": "drawer", "animation": "open"},
        {"type": "destroy_object", "object": "drawer"},
    ], GameState(), runtime)
    runner.advance()

    state = runtime.object_states["drawer"]
    assert (state.x, state.y, state.rotation, state.sprite, state.animation, state.destroyed) == (
        40, 50, 30.0, "open.png", "open", True,
    )
    assert resolve_scene_objects(scene, GameState(), runtime) == []
    assert scene["exploration"]["objects"][0] == authored


def test_move_object_duration_advances_without_rewriting_authored_object():
    runtime = SceneRuntime()
    runtime.state_for("drawer").x = 0
    runtime.state_for("drawer").y = 0
    runner = EventRunner([
        {"type": "move_object", "target": "drawer", "x": 100, "y": 40, "duration": 1.0},
    ], GameState(), runtime)
    runner.advance(0)
    assert not runner.finished
    runner.advance(500)
    assert runtime.object_states["drawer"].x == 50
    runner.advance(1000)
    assert runner.finished and runtime.object_states["drawer"].x == 100


def test_validator_reports_bad_references_and_schema_fields_with_context():
    broken = {"id": "study", "exploration": {"navigation": [{"scene": "missing"}], "look_regions": [
        {"id": "bad", "rect": [0, 0, 0, 1], "event": "missing", "interaction": "inspect"},
    ], "look_events": {}}}
    with pytest.raises(StoryValidationError, match="navigation"):
        validate_exploration_scene(broken, "study", known_scene_ids={"study"})

    bad_rect = {"id": "study", "exploration": {"look_regions": [
        {"id": "bad", "rect": [0, 0, 0, 1], "event": "ok", "interaction": "inspect"},
    ], "look_events": {"ok": {"actions": []}}}}
    with pytest.raises(StoryValidationError, match="width and height"):
        validate_exploration_scene(bad_rect, "study", known_scene_ids={"study"})

    bad_interaction = {"id": "study", "exploration": {"look_regions": [
        {"id": "bad", "rect": [0, 0, 1, 1], "event": "ok", "interaction": "talk"},
    ], "look_events": {"ok": {"actions": []}}}}
    with pytest.raises(StoryValidationError, match="interaction"):
        validate_exploration_scene(bad_interaction, "study", known_scene_ids={"study"})
