"""Step 12E coverage for isolated real-QTE launch seams."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.battle.move_progression import resolve_combat_move
from engine.battle.qte import QTE_REGISTRY
from engine.battle.qte_harness import create_test_qte, resolve_test_qte
from engine.core.developer_test import (
    DeveloperTestConfigError,
    QteTestConfiguration,
    load_developer_test_configuration,
)
from engine.story_core import ContentKind
from story_core_fixture import write_fixture_story
from story_designer.models import CombatMoveElementSelection, DefinitionSelection
from story_designer.services.runtime_test import (
    QteTestLaunch,
    build_qte_runtime_command,
    resolve_qte_move_id,
)

import main as game_main


def _move_config() -> dict:
    return {
        "moves": [{
            "id": "slash",
            "common": {"base_power": 4, "qte": {"type": "precision_bar", "pattern_parameters": {"target_position": .4}}},
            "difficulty_levels": {
                0: {"qte": {"duration": 2.0}},
                1: {"qte": {"duration": 1.5, "tuning_parameters": {"critical_window": .03}}},
                2: {"qte": {"duration": 1.0, "tuning_parameters": {"critical_window": .01}}},
            },
        }]
    }


def test_qte_configuration_round_trips_without_game_state_fields(tmp_path: Path) -> None:
    config = QteTestConfiguration("slash", 2, seed=17)
    path = tmp_path / "qte.json"
    config.write_json(path)
    loaded = load_developer_test_configuration(path)
    assert isinstance(loaded, QteTestConfiguration)
    assert loaded == config
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "qte_level": 2, "qte_move": "slash", "seed": 17,
    }


def test_qte_configuration_rejects_invalid_level() -> None:
    with pytest.raises(DeveloperTestConfigError):
        QteTestConfiguration("slash", -1)


def test_cli_qte_target_is_developer_only_and_mutually_exclusive() -> None:
    normal = game_main.parse_args(["--story", "story"])
    assert normal.qte_move is None and normal.qte_level is None
    qte = game_main.parse_args(["--story", "story", "--developer", "--qte-move", "slash", "--qte-level", "2"])
    assert (qte.qte_move, qte.qte_level) == ("slash", 2)
    with pytest.raises(SystemExit):
        game_main.parse_args(["--story", "story", "--developer", "--qte-move", "slash", "--qte-level", "2", "--battle", "b"])


def test_qte_process_command_transports_move_and_level(tmp_path: Path) -> None:
    program, arguments, cwd = build_qte_runtime_command(
        tmp_path / "story root", "slash", 2,
        shared_assets_root=tmp_path / "shared assets",
        python_executable="C:/python/python.exe",
        runtime_script=tmp_path / "main.py",
        working_directory=tmp_path / "runtime",
        seed=8,
    )
    assert program == "C:/python/python.exe"
    assert arguments[-6:] == ["--qte-move", "slash", "--qte-level", "2", "--qte-seed", "8"]
    assert "--battle" not in arguments and "--scene" not in arguments
    assert cwd == str((tmp_path / "runtime").resolve())
    assert QteTestLaunch(tmp_path / "story", "slash", 2).command()[1][-4:] == ["--qte-move", "slash", "--qte-level", "2"]


def test_effective_resolution_and_factory_use_the_same_authoritative_path() -> None:
    config = _move_config()
    expected = resolve_combat_move(config["moves"][0], 2)
    assert resolve_test_qte(config, QteTestConfiguration("slash", 2)) == expected
    qte = create_test_qte(config, QteTestConfiguration("slash", 2, seed=1))
    assert qte.qte_type == "precision_bar"
    assert qte.duration == pytest.approx(1.0)
    assert qte.critical_window == pytest.approx(.01)


def test_direct_root_move_uses_implicit_level_one() -> None:
    config = {"moves": [{"id": "direct", "qte": {"type": "precision_bar", "duration": 1.0}}]}
    assert create_test_qte(config, QteTestConfiguration("direct", 1)).qte_type == "precision_bar"


def test_every_registered_qte_constructs_through_harness_factory() -> None:
    config = {"moves": [{"id": qte_type, "qte": {"type": qte_type}} for qte_type in QTE_REGISTRY]}
    for qte_type in QTE_REGISTRY:
        assert create_test_qte(config, QteTestConfiguration(qte_type, 1)).qte_type == qte_type


def test_isolated_factory_does_not_create_or_modify_progression_state(tmp_path: Path) -> None:
    story_root, _shared_root = write_fixture_story(tmp_path)
    path = story_root / "moves" / "moves.yaml"
    before = path.read_bytes()
    config = {"moves": [{"id": "direct", "qte": {"type": "precision_bar"}}]}
    create_test_qte(config, QteTestConfiguration("direct", 1))
    assert path.read_bytes() == before


def test_nested_combat_move_selection_resolves_to_parent() -> None:
    project = type("Project", (), {"moves": {"slash": object()}})()
    assert resolve_qte_move_id(DefinitionSelection("move", "slash"), project) == "slash"
    assert resolve_qte_move_id(CombatMoveElementSelection("slash", "qte", ("qte",), 2), project) == "slash"
    assert resolve_qte_move_id(DefinitionSelection("move", "missing"), project) is None


def test_designer_launches_selected_move_and_level_through_shared_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_core = pytest.importorskip("PySide6.QtCore")
    from story_designer.main_window import MainWindow

    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])

    class Signal:
        def connect(self, callback):
            self.callback = callback

    class Process:
        ProcessState = qt_core.QProcess.ProcessState
        ProcessChannelMode = qt_core.QProcess.ProcessChannelMode
        ProcessError = qt_core.QProcess.ProcessError

        def __init__(self, parent=None):
            self._state = self.ProcessState.NotRunning
            self.readyReadStandardOutput = Signal()
            self.readyReadStandardError = Signal()
            self.errorOccurred = Signal()
            self.finished = Signal()
            self.started = None

        def setProcessChannelMode(self, mode):
            self.mode = mode

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
    monkeypatch.setattr("story_designer.main_window.QProcess", Process)
    window = MainWindow()
    try:
        assert window.open_story_path(story_root)
        entry = window.session.project.index.entry(ContentKind.MOVE, "intro")  # type: ignore[union-attr]
        window.session.select(DefinitionSelection(ContentKind.MOVE, "intro", entry.source))  # type: ignore[union-attr]
        window._refresh_views()
        editor = window.workspace.combat_move_editor
        assert editor.test_context() == ("intro", 1)
        assert window.test_current_move()
        assert window.test_process is not None
        assert window.test_process.started[1][-4:] == ["--qte-move", "intro", "--qte-level", "1"]
    finally:
        window.close()
    assert app is not None and shared_root.exists()
