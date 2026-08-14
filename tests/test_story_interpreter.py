import pytest
import yaml

from engine.core.asset_loader import AssetLoader
from engine.core.game_state import GameState
from engine.core.story_interpreter import StoryInterpreter
from engine.errors import StoryValidationError


@pytest.fixture
def interp(tmp_path):
    story_dir = tmp_path / "story"
    (story_dir / "scenes").mkdir(parents=True)
    (story_dir / "story.yaml").write_text(yaml.dump({"start_scene": "a"}))

    scenes = {
        "a": {
            "id": "a", "text": "Scene A",
            "actions": [{"set_flag": {"entered_a": True}}, {"play_sfx": "door.wav"}, {"add_item": "torch"}],
            "choices": [
                {"text": "go to locked room", "condition": "has_item('key')", "goto": "locked_room"},
                {"text": "go to b", "goto": "b"},
                {"text": "fight", "battle": "wolf_fight", "on_win": "b", "on_lose": "ending_lose"},
            ],
        },
        "b": {"id": "b", "text": "Scene B, no choices", "choices": []},
        "locked_room": {"id": "locked_room", "text": "unreachable without key", "choices": []},
        "ending_lose": {"id": "ending_lose", "text": "You lost.", "ending": True},
    }
    for sid, data in scenes.items():
        (story_dir / "scenes" / f"{sid}.yaml").write_text(yaml.dump(data))

    loader = AssetLoader(str(story_dir), "shared_assets")
    state = GameState(current_scene="a")
    return StoryInterpreter(loader, state)


def test_scene_entry_runs_actions(interp):
    scene, sfx = interp.enter_scene("a")
    assert interp.state.get_flag("entered_a") is True
    assert interp.state.has_item("torch")
    assert sfx == ["door.wav"]
    assert interp.state.history == ["a"]


def test_condition_filters_choices(interp):
    scene, _ = interp.enter_scene("a")
    choices = interp.available_choices(scene)
    assert len(choices) == 2  # locked-room choice hidden without the key
    interp.state.add_item("key")
    choices2 = interp.available_choices(scene)
    assert len(choices2) == 3


def test_goto_transition(interp):
    scene, _ = interp.enter_scene("a")
    choice = next(c for c in scene["choices"] if c["text"] == "go to b")
    t = interp.resolve_choice(choice)
    assert t.kind == "goto" and t.scene_id == "b"


def test_battle_transition(interp):
    scene, _ = interp.enter_scene("a")
    choice = next(c for c in scene["choices"] if c["text"] == "fight")
    t = interp.resolve_choice(choice)
    assert t.kind == "battle" and t.battle_id == "wolf_fight"
    assert t.on_win == "b" and t.on_lose == "ending_lose"


def test_ending_detection(interp):
    scene_b, _ = interp.enter_scene("b")
    assert interp.is_ending(scene_b)  # no choices at all
    scene_end, _ = interp.enter_scene("ending_lose")
    assert interp.is_ending(scene_end)  # explicit ending: true
    scene_a, _ = interp.enter_scene("a")
    assert not interp.is_ending(scene_a)


def test_apply_rewards():
    from engine.core.asset_loader import AssetLoader as AL
    state = GameState()
    interp = StoryInterpreter(AL(".", "shared_assets"), state)
    interp.apply_rewards({"variables": {"gold": 5}, "items": ["wolf_pelt"]})
    assert state.get_var("gold") == 5
    assert state.has_item("wolf_pelt")
    interp.apply_rewards(None)  # must not raise


def test_unknown_action_type_rejected(interp):
    with pytest.raises(StoryValidationError):
        interp.run_actions([{"unknown_action": "x"}])


def test_choice_with_no_transition_rejected(interp):
    with pytest.raises(StoryValidationError):
        interp.resolve_choice({"text": "broken"})


def test_project_view_supplies_scene_lookup_without_reloading_or_exposing_core_data(tmp_path, monkeypatch):
    story_dir = tmp_path / "story"
    (story_dir / "scenes").mkdir(parents=True)
    (story_dir / "story.yaml").write_text(yaml.dump({"start_scene": "intro"}))
    (story_dir / "scenes" / "intro.yaml").write_text(
        yaml.dump({
            "id": "intro",
            "text": "From StoryProject",
            "choices": [{"text": "Continue", "goto": "intro"}],
        })
    )

    loader = AssetLoader(str(story_dir), "shared_assets")
    project = loader.load_project()
    project_view = project.legacy_view()
    monkeypatch.setattr(loader, "load_project", lambda: pytest.fail("interpreter must not load a project"))
    monkeypatch.setattr(loader, "load_scene", lambda _scene_id: pytest.fail("interpreter must use project view"))

    interpreter = StoryInterpreter(
        loader,
        GameState(current_scene="intro"),
        project_view=project_view,
    )
    scene, _ = interpreter.enter_scene()

    assert scene["text"] == "From StoryProject"
    scene["choices"][0]["text"] = "Runtime mutation"
    assert project.scene("intro").to_mapping()["choices"][0]["text"] == "Continue"
    assert project_view.load_scene("intro")["choices"][0]["text"] == "Continue"
