"""Step 8A coverage for developer scene startup and Designer launch seams."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.core import game_engine as game_engine_module
from engine.core.game_engine import GameEngine
from engine.story_core import ContentKind
from story_core_fixture import write_fixture_story
from story_designer.models import (
    DefinitionSelection,
    DialogueEntrySelection,
    NavigationEntrySelection,
    ProjectSession,
    SetPropertyCommand,
    SceneElementSelection,
)
from story_designer.services.runtime_test import (
    SceneTestLaunch,
    build_runtime_command,
    resolve_scene_id,
)

import main as game_main

try:
    from PySide6.QtCore import QProcess as RealQProcess
    from PySide6.QtWidgets import QApplication
    from story_designer.main_window import MainWindow
except ImportError:  # pragma: no cover - Core-only environments
    RealQProcess = None  # type: ignore[assignment]
    QApplication = None  # type: ignore[assignment]
    MainWindow = None  # type: ignore[assignment]


@pytest.fixture(scope="module")
def qapp():
    if QApplication is None:
        pytest.skip("PySide6 is not installed")
    return QApplication.instance() or QApplication([])


class _Renderer:
    def __init__(self, assets, display_config, render_config):
        self.assets = assets


class _Audio:
    def __init__(self, assets, **kwargs):
        self.assets = assets

    def preload_sfx(self, filename: str) -> None:
        pass


def test_cli_parses_developer_scene_and_preserves_normal_defaults() -> None:
    normal = game_main.parse_args(["--story", "stories/demo_story"])
    assert normal.story == "stories/demo_story"
    assert normal.developer is False
    assert normal.scene is None

    developer = game_main.parse_args(
        ["--story", "stories/demo_story", "--developer", "--scene", "library"]
    )
    assert developer.developer is True
    assert developer.scene == "library"

    with pytest.raises(SystemExit):
        game_main.parse_args(["--story", "stories/demo_story", "--scene", "library"])


def test_game_engine_scene_override_keeps_normal_fresh_state_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    monkeypatch.setattr(game_engine_module, "Renderer", _Renderer)
    monkeypatch.setattr(game_engine_module, "AudioSystem", _Audio)

    normal = GameEngine(str(story_root), str(shared_root))
    assert normal.state.current_scene == "intro"

    engine = GameEngine(
        str(story_root),
        str(shared_root),
        developer_mode=True,
        start_scene_override="ending",
    )

    assert engine.manifest["start_scene"] == "intro"
    assert engine.state.current_scene == "ending"
    assert engine.state.flags == {"visited": False}
    assert engine.state.stats["max_hp"] == 10


def test_runtime_command_uses_separate_argument_values_and_explicit_cwd(tmp_path: Path) -> None:
    story = tmp_path / "story root"
    shared = tmp_path / "shared assets"
    script = tmp_path / "main.py"
    cwd = tmp_path / "runtime"
    cwd.mkdir()

    program, arguments, working_directory = build_runtime_command(
        story,
        "library",
        shared_assets_root=shared,
        python_executable="C:/python/python.exe",
        runtime_script=script,
        working_directory=cwd,
    )

    assert program == "C:/python/python.exe"
    assert arguments == [
        str(script.resolve()),
        "--story",
        str(story.resolve()),
        "--shared-assets",
        str(shared.resolve()),
        "--developer",
        "--scene",
        "library",
    ]
    assert working_directory == str(cwd.resolve())


def test_scene_context_resolution_covers_scene_and_scene_local_entries(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    session = ProjectSession.from_path(story_root, shared_root)
    project = session.project
    assert project is not None
    scene_entry = project.index.entry(ContentKind.SCENE, "intro")  # type: ignore[union-attr]
    assert scene_entry is not None
    scene = DefinitionSelection(ContentKind.SCENE, "intro", scene_entry.source)

    selections = [
        scene,
        SceneElementSelection("intro", "object", "lamp"),
        NavigationEntrySelection("intro", ("exploration", "navigation", 0)),
        DialogueEntrySelection("intro", "scene_entry", ("dialog",), 0),
    ]
    assert all(resolve_scene_id(selection, project) == "intro" for selection in selections)
    assert resolve_scene_id(DefinitionSelection(ContentKind.ITEM, "intro"), project) is None


@pytest.mark.skipif(not hasattr(game_engine_module, "GameEngine"), reason="runtime unavailable")
def test_scene_test_launch_defaults_to_runtime_script() -> None:
    launch = SceneTestLaunch(Path("stories/demo_story"), "intro")
    assert launch.script.name == "main.py"
    assert launch.command()[1][-3:] == ["--developer", "--scene", "intro"]


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_designer_save_failure_prevents_runtime_process(qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    story_root, _shared_root = write_fixture_story(tmp_path)
    window = MainWindow()
    try:
        assert window.open_story_path(story_root)
        entry = window.session.project.index.entry(ContentKind.SCENE, "intro")  # type: ignore[union-attr]
        assert entry is not None
        scene = DefinitionSelection(ContentKind.SCENE, "intro", entry.source)
        window.session.select(scene)
        window._refresh_views()
        window.session.apply_command(SetPropertyCommand(scene, ("text",), "Unsaved"))
        window._on_inspector_state_changed()

        monkeypatch.setattr(window, "save_story", lambda: False)
        assert window.test_current_scene() is False
        assert window.test_process is None
    finally:
        window.session.revert_all()
        window.close()


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_designer_owns_one_process_and_reports_nonzero_exit(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
            self.started: tuple[str, list[str]] | None = None
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
            return b"runtime exploded"

        def terminate(self):
            self._state = self.ProcessState.NotRunning

        def waitForFinished(self, timeout):
            return True

    story_root, _shared_root = write_fixture_story(tmp_path)
    window = MainWindow()
    critical_messages: list[str] = []
    monkeypatch.setattr("story_designer.main_window.QProcess", _FakeProcess)
    monkeypatch.setattr(
        "story_designer.main_window.QMessageBox.critical",
        staticmethod(lambda _parent, _title, detail: critical_messages.append(detail)),
    )
    try:
        assert window.open_story_path(story_root)
        entry = window.session.project.index.entry(ContentKind.SCENE, "ending")  # type: ignore[union-attr]
        assert entry is not None
        window.session.select(DefinitionSelection(ContentKind.SCENE, "ending", entry.source))
        window._refresh_views()

        assert window.test_current_scene()
        process = window.test_process
        assert process is not None
        assert process.started is not None
        assert process.started[0]
        assert process.started[1][-3:] == ["--developer", "--scene", "ending"]
        assert window.test_current_scene() is False

        process._state = process.ProcessState.NotRunning
        window._on_test_process_finished(3, process.ProcessState.NotRunning)
        assert critical_messages and "runtime exploded" in critical_messages[-1]
        assert window.test_process is None
    finally:
        window.close()
