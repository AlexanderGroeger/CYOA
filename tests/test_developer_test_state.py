"""Focused coverage for launch-time developer test state."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.core.developer_test import (
    DeveloperTestConfigError,
    SceneTestConfiguration,
    apply_developer_test_configuration,
)
from engine.core.game_state import GameState
from engine.core import game_engine as game_engine_module
from engine.core.game_engine import GameEngine
from story_designer.services.runtime_test import build_runtime_command
from story_core_fixture import write_fixture_story


def test_test_configuration_round_trips_json_scalars(tmp_path: Path) -> None:
    original = SceneTestConfiguration(
        flags={"door_open": True},
        variables={"trust": 3, "chapter": "night", "optional": None},
        inventory={"rusty_key": 1},
        stats={"hp": 7.5},
    )
    path = tmp_path / "developer-test.json"
    original.write_json(path)

    loaded = SceneTestConfiguration.from_json(path)
    assert loaded.to_dict() == original.to_dict()


def test_test_configuration_rejects_malformed_root_and_values(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(DeveloperTestConfigError, match="root must be an object"):
        SceneTestConfiguration.from_json(path)

    with pytest.raises(DeveloperTestConfigError, match="must be boolean"):
        SceneTestConfiguration(flags={"door_open": 1})
    with pytest.raises(DeveloperTestConfigError, match="non-negative integer"):
        SceneTestConfiguration(inventory={"key": -1})


def test_developer_overrides_apply_after_normal_fresh_state_without_replacement() -> None:
    state = GameState(flags={"door_open": False}, variables={"coins": 2}, inventory={"badge": 1})
    configuration = SceneTestConfiguration(
        flags={"door_open": True, "met_guard": False},
        variables={"coins": 9},
        inventory={"key": 1, "badge": 0},
    )

    apply_developer_test_configuration(state, configuration, known_items={"badge", "key"})

    assert state.flags == {"door_open": True, "met_guard": False}
    assert state.variables == {"coins": 9}
    assert state.inventory == {"key": 1}


def test_unknown_test_item_is_a_developer_error() -> None:
    configuration = SceneTestConfiguration(inventory={"typo_key": 1})
    with pytest.raises(DeveloperTestConfigError, match="Unknown item"):
        apply_developer_test_configuration(GameState(), configuration, known_items={"key"})


def test_game_engine_applies_config_after_manifest_and_profile_initialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)

    class _Renderer:
        def __init__(self, *_args):
            pass

    class _Audio:
        def __init__(self, *_args, **_kwargs):
            pass

        def preload_sfx(self, _filename: str) -> None:
            pass

    monkeypatch.setattr(game_engine_module, "Renderer", _Renderer)
    monkeypatch.setattr(game_engine_module, "AudioSystem", _Audio)
    engine = GameEngine(
        str(story_root),
        str(shared_root),
        developer_mode=True,
        start_scene_override="ending",
        developer_test_config=SceneTestConfiguration(
            flags={"visited": True, "new_flag": True},
            variables={"coins": 10},
            inventory={"intro": 0},
        ),
    )

    assert engine.state.current_scene == "ending"
    assert engine.state.flags == {"visited": True, "new_flag": True}
    assert engine.state.variables == {"coins": 10}
    assert engine.state.inventory == {}


def test_runtime_command_transports_structured_config_path_before_scene_suffix(tmp_path: Path) -> None:
    config = tmp_path / "test state.json"
    _program, arguments, _cwd = build_runtime_command(
        tmp_path / "story",
        "library",
        test_config_path=config,
        runtime_script=tmp_path / "main.py",
    )
    assert arguments[-3:] == ["--developer", "--scene", "library"]
    assert arguments[-5:-3] == ["--developer-test-config", str(config.resolve())]
