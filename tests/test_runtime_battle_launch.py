"""Step 12C coverage for direct developer battle launches."""

from __future__ import annotations

import os
import json
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.core import game_engine as game_engine_module
from engine.core.developer_test import (
    BattleTestConfiguration,
    DeveloperTestConfigError,
    load_developer_test_configuration,
)
from engine.core.game_engine import GameEngine
from engine.story_core import ContentKind
from story_core_fixture import write_fixture_story
from story_designer.models import (
    BattleElementSelection,
    DefinitionSelection,
)
from story_designer.services.runtime_test import (
    BattleTestLaunch,
    build_battle_runtime_command,
    resolve_battle_id,
)

try:
    from PySide6.QtCore import QProcess as RealQProcess
    from PySide6.QtWidgets import QApplication
    from story_designer.main_window import MainWindow
    from story_designer.models import ProjectSession, SetPropertyCommand
except ImportError:  # pragma: no cover - Core-only environments
    RealQProcess = None  # type: ignore[assignment]
    QApplication = None  # type: ignore[assignment]
    MainWindow = None  # type: ignore[assignment]

import main as game_main


@pytest.fixture(scope="module")
def qapp():
    if QApplication is None:
        pytest.skip("PySide6 is not installed")
    return QApplication.instance() or QApplication([])


def test_battle_configuration_round_trips_existing_test_state(tmp_path: Path) -> None:
    original = BattleTestConfiguration(
        battle_id="intro",
        flags={"ready": True},
        variables={"coins": 4},
        inventory={"intro": 2},
        stats={"hp": 3, "max_hp": 9},
    )
    path = tmp_path / "battle-test.json"
    original.write_json(path)

    loaded = load_developer_test_configuration(path)
    assert isinstance(loaded, BattleTestConfiguration)
    assert loaded.to_dict() == original.to_dict()


def test_cli_rejects_scene_and_battle_but_normal_defaults_remain_unchanged() -> None:
    normal = game_main.parse_args(["--story", "stories/demo_story"])
    assert normal.battle is None and normal.scene is None and normal.developer is False
    battle = game_main.parse_args(["--story", "stories/demo_story", "--developer", "--battle", "wolf_fight"])
    assert battle.battle == "wolf_fight"
    with pytest.raises(SystemExit):
        game_main.parse_args(["--story", "x", "--developer", "--scene", "s", "--battle", "b"])


def test_battle_process_command_uses_shell_free_battle_arguments(tmp_path: Path) -> None:
    program, arguments, cwd = build_battle_runtime_command(
        tmp_path / "story root", "boss fight",
        shared_assets_root=tmp_path / "shared assets",
        python_executable="C:/python/python.exe",
        runtime_script=tmp_path / "main.py",
        working_directory=tmp_path / "runtime",
        test_config_path=tmp_path / "state.json",
    )
    assert program == "C:/python/python.exe"
    assert arguments[-3:] == ["--developer", "--battle", "boss fight"]
    assert arguments[arguments.index("--shared-assets") : arguments.index("--shared-assets") + 2] == [
        "--shared-assets", str((tmp_path / "shared assets").resolve())
    ]
    assert arguments[arguments.index("--developer-test-config") : arguments.index("--developer-test-config") + 2] == [
        "--developer-test-config", str((tmp_path / "state.json").resolve())
    ]
    assert cwd == str((tmp_path / "runtime").resolve())
    assert BattleTestLaunch(tmp_path / "story", "boss").command()[1][-3:] == ["--developer", "--battle", "boss"]


def test_nested_battle_editor_elements_resolve_to_parent() -> None:
    project = type("Project", (), {"battles": {"boss": object()}})()
    assert resolve_battle_id(DefinitionSelection("battle", "boss"), project) == "boss"
    for kind in ("defense_pattern", "phase", "enemy_move", "dialogue"):
        assert resolve_battle_id(BattleElementSelection("boss", kind, (kind, 0)), project) == "boss"
    assert resolve_battle_id(DefinitionSelection("battle", "missing"), project) is None


def test_direct_battle_start_preserves_fresh_state_then_applies_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)

    class _Renderer:
        def __init__(self, *_args):
            pass

        def shutdown(self):
            pass

    class _Audio:
        def __init__(self, *_args, **_kwargs):
            pass

        def preload_sfx(self, _filename: str):
            pass

        def stop_music(self):
            pass

    monkeypatch.setattr(game_engine_module, "Renderer", _Renderer)
    monkeypatch.setattr(game_engine_module, "AudioSystem", _Audio)
    engine = GameEngine(
        str(story_root), str(shared_root), developer_mode=True,
        start_battle_override="intro",
        developer_test_config=BattleTestConfiguration(
            battle_id="intro", stats={"hp": 2}, variables={"coins": 8}
        ),
    )
    assert engine.state.current_scene == "intro"
    assert engine.state.inventory == {"intro": 1}
    assert engine.state.stats["max_hp"] == 10
    assert engine.state.stats["hp"] == 2
    assert engine.state.variables["coins"] == 8
    assert engine._developer_battle_id == "intro"

    started: list[str] = []
    engine._start_battle = lambda transition: (started.append(transition.battle_id or ""), setattr(engine, "running", False))  # type: ignore[method-assign]
    engine._render = lambda: None  # type: ignore[method-assign]
    engine.run()
    assert started == ["intro"]
    assert engine.state.history == []


def test_battle_configuration_rejects_scene_mixing() -> None:
    with pytest.raises(DeveloperTestConfigError, match="cannot also specify a scene"):
        BattleTestConfiguration(battle_id="boss", scene_id="intro")


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_designer_battle_save_failure_prevents_launch(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    window = MainWindow()
    try:
        assert window.open_story_path(story_root)
        entry = window.session.project.index.entry(ContentKind.BATTLE, "intro")  # type: ignore[union-attr]
        selection = DefinitionSelection("battle", "intro", entry.source)  # type: ignore[union-attr]
        window.session.select(selection)
        window._refresh_views()
        window.session.apply_command(SetPropertyCommand(selection, ("enemy", "hp"), 2))
        monkeypatch.setattr(window, "save_story", lambda: False)
        assert window.test_current_battle() is False
        assert window.test_process is None
    finally:
        window.session.revert_all()
        window.close()


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_designer_battle_launch_reuses_state_file_and_process_controls(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Signal:
        def connect(self, callback):
            self.callback = callback

    class _FakeProcess:
        ProcessState = RealQProcess.ProcessState
        ProcessChannelMode = RealQProcess.ProcessChannelMode
        ProcessError = RealQProcess.ProcessError

        def __init__(self, parent=None):
            self._state = self.ProcessState.NotRunning
            self.started = None
            self.readyReadStandardOutput = _Signal()
            self.readyReadStandardError = _Signal()
            self.errorOccurred = _Signal()
            self.finished = _Signal()

        def setProcessChannelMode(self, mode):
            self.channel_mode = mode

        def start(self, program, arguments):
            self.started = (program, list(arguments))
            self._state = self.ProcessState.Running

        def state(self):
            return self._state

        def readAllStandardOutput(self):
            return b""

        def readAllStandardError(self):
            return b""

        def terminate(self):
            self._state = self.ProcessState.NotRunning

        def waitForFinished(self, timeout):
            return True

    story_root, shared_root = write_fixture_story(tmp_path)
    monkeypatch.setattr("story_designer.main_window.QProcess", _FakeProcess)
    window = MainWindow()
    try:
        assert window.open_story_path(story_root)
        entry = window.session.project.index.entry(ContentKind.BATTLE, "intro")  # type: ignore[union-attr]
        selection = DefinitionSelection("battle", "intro", entry.source)  # type: ignore[union-attr]
        window.session.select(selection)
        window._refresh_views()
        window._test_configuration = window._test_configuration.__class__(stats={"hp": 4})
        assert window.test_current_battle()
        process = window.test_process
        assert process is not None and process.started is not None
        assert process.started[1][-3:] == ["--developer", "--battle", "intro"]
        config_path = window._test_config_path
        assert config_path is not None
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        assert payload["battle"] == "intro"
        assert payload["stats"] == {"hp": 4}
        assert window.stop_test()
        window._on_test_process_finished(0, _FakeProcess.ProcessState.NotRunning)
        assert window.test_process is None
    finally:
        window.close()
