from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from engine.story_core import ContentKind, Diagnostic, DiagnosticSeverity, Diagnostics, load_story_project
from story_core_fixture import write_fixture_story
from story_designer.models import DefinitionSelection, ProjectSession


def test_project_session_open_selection_validation_and_close(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)

    session = ProjectSession.from_path(story_root, shared_root)

    assert session.project is not None
    assert session.story_root == story_root.resolve()
    assert session.diagnostics.has_errors is False
    selection = DefinitionSelection(ContentKind.SCENE, "intro", story_root / "scenes" / "intro.yaml")
    assert session.select(selection) is session.project.scene("intro")
    assert session.selection == selection
    assert session.definition() is session.project.scene("intro")

    session.close()

    assert session.project is None
    assert session.story_root is None
    assert session.selection is None
    assert len(session.diagnostics) == 0


def test_project_session_reload_replaces_project_and_drops_stale_selection(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    session = ProjectSession.from_path(story_root, shared_root)
    selection = DefinitionSelection(ContentKind.SCENE, "intro", story_root / "scenes" / "intro.yaml")
    session.select(selection)
    old_project = session.project

    (story_root / "scenes" / "intro.yaml").unlink()
    session.reload(shared_root)

    assert session.project is not old_project
    assert session.selection is None
    assert "intro" not in session.project.scenes


def test_designer_session_and_core_imports_do_not_load_qt_or_pygame() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import engine.story_core; import story_designer.models; "
            "assert 'PySide6' not in sys.modules; assert 'pygame' not in sys.modules",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    )
    assert result.returncode == 0, result.stderr


def test_structured_diagnostics_can_be_consumed_without_string_parsing(tmp_path: Path) -> None:
    source = tmp_path / "scenes" / "intro.yaml"
    diagnostics = Diagnostics(
        [
            Diagnostic(source, ("choices", 0, "goto"), "unknown_scene", DiagnosticSeverity.ERROR, "Unknown scene."),
            Diagnostic(source, (), "advice", DiagnosticSeverity.ADVISORY, "Consider adding a title."),
        ]
    )

    assert diagnostics[0].severity is DiagnosticSeverity.ERROR
    assert diagnostics[0].source == source
    assert diagnostics[0].path_text == "choices[0].goto"
    assert diagnostics[1].severity is DiagnosticSeverity.ADVISORY


try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from story_designer.widgets import DiagnosticsWidget, ProjectBrowser
except ImportError:  # PySide6 is an optional local dependency in this environment.
    QApplication = None  # type: ignore[assignment]
    DiagnosticsWidget = None  # type: ignore[assignment]
    ProjectBrowser = None  # type: ignore[assignment]


@pytest.fixture(scope="module")
def qapp():
    if QApplication is None:
        pytest.skip("PySide6 is not installed")
    app = QApplication.instance() or QApplication([])
    return app


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_project_browser_populates_core_categories(qapp, tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    project = load_story_project(story_root, shared_root)
    browser = ProjectBrowser()
    browser.set_project(project)

    categories = {
        browser.tree.topLevelItem(0).child(index).text(0)
        for index in range(browser.tree.topLevelItem(0).childCount())
    }
    assert {"Manifest", "Player", "Scenes", "Items", "Battles", "Combat Moves", "Event Pools", "Animations", "Audio Configuration"} <= categories


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_diagnostics_widget_represents_structured_fields(qapp, tmp_path: Path) -> None:
    source = tmp_path / "story.yaml"
    diagnostics = Diagnostics([
        Diagnostic(source, ("start_scene",), "unknown_scene", DiagnosticSeverity.ERROR, "Unknown scene."),
    ])
    widget = DiagnosticsWidget()
    widget.set_diagnostics(diagnostics)

    assert widget.model.rowCount() == 1
    assert widget.model.data(widget.model.index(0, 0)) == "error"
    assert widget.model.data(widget.model.index(0, 2)) == "start_scene"
    assert widget.model.data(widget.model.index(0, 3)) == "unknown_scene"
