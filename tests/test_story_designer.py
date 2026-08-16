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


def _child(item, label: str):
    return next(item.child(index) for index in range(item.childCount()) if item.child(index).text(0) == label)


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_project_browser_nested_folders_names_and_single_column(qapp, tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    (story_root / "scenes/deer").mkdir(parents=True)
    (story_root / "scenes/deer/deer_pet.yaml").write_text(
        "id: deer_pet\nname: Pet the Deer\ntext: Pet.\n", encoding="utf-8"
    )
    (story_root / "scenes/deer/deer_run.yaml").write_text(
        "id: deer_run\nname: Deer Run\ntext: Run.\n", encoding="utf-8"
    )
    (story_root / "scenes/chapter/forest/deer").mkdir(parents=True)
    (story_root / "scenes/chapter/forest/deer/deep.yaml").write_text(
        "id: deep\nname: Deep Scene\ntext: Deep.\n", encoding="utf-8"
    )

    browser = ProjectBrowser()
    browser.set_project(load_story_project(story_root, shared_root))
    root = browser.tree.topLevelItem(0)
    scenes = _child(root, "Scenes")
    deer = _child(scenes, "deer")
    assert browser.tree.columnCount() == 1
    assert browser.tree.headerItem().text(0) == "Project"
    assert [deer.child(index).text(0) for index in range(deer.childCount())] == ["Deer Run", "Pet the Deer"]

    chapter = _child(scenes, "chapter")
    forest = _child(chapter, "forest")
    assert _child(forest, "deer").child(0).text(0) == "Deep Scene"
    assert scenes.font(0).bold()
    assert not deer.font(0).bold()

    pet = _child(deer, "Pet the Deer")
    selection = pet.data(0, browser._SELECTION_ROLE)
    assert selection == DefinitionSelection(ContentKind.SCENE, "deer_pet", story_root / "scenes/deer/deer_pet.yaml")
    assert "ID: deer_pet" in pet.toolTip(0)
    assert "Source: scenes/deer/deer_pet.yaml" in pet.toolTip(0)

    ending = _child(scenes, "ending")
    assert ending.text(0) == "ending"


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_project_browser_search_is_recursive_whitelist_and_reactive(qapp, tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    (story_root / "scenes/deer").mkdir(parents=True)
    (story_root / "scenes/deer/deer_pet.yaml").write_text(
        "id: deer_pet\nname: Pet the Deer\ntext: Pet.\n", encoding="utf-8"
    )
    (story_root / "scenes/deer/deer_run.yaml").write_text(
        "id: deer_run\nname: Deer Run\ntext: Run.\n", encoding="utf-8"
    )
    (story_root / "scenes/meadow").mkdir(parents=True)
    (story_root / "scenes/meadow/quiet.yaml").write_text(
        "id: quiet\nname: Quiet Meadow\ntext: Quiet.\n", encoding="utf-8"
    )
    browser = ProjectBrowser()
    browser.set_project(load_story_project(story_root, shared_root))
    root = browser.tree.topLevelItem(0)
    scenes = _child(root, "Scenes")
    deer = _child(scenes, "deer")
    pet = _child(deer, "Pet the Deer")
    run = _child(deer, "Deer Run")
    meadow = _child(scenes, "meadow")

    browser.search.setText("pet")
    assert not root.isHidden() and not scenes.isHidden() and not deer.isHidden() and not pet.isHidden()
    assert run.isHidden() and meadow.isHidden()
    assert _child(root, "Items").isHidden()

    browser.search.setText("MEADOW")
    assert not scenes.isHidden() and not meadow.isHidden() and not _child(meadow, "Quiet Meadow").isHidden()
    assert deer.isHidden()

    browser.search.clear()
    assert not run.isHidden() and not meadow.isHidden() and not _child(root, "Items").isHidden()


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_project_browser_duplicate_names_keep_independent_selections_and_refresh_search(qapp, tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    for folder in ("one", "two"):
        target = story_root / f"scenes/{folder}/same.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("id: same\nname: Same Name\ntext: Same.\n", encoding="utf-8")
    browser = ProjectBrowser()
    browser.search.setText("Same")
    browser.set_project(load_story_project(story_root, shared_root))

    same_items = [item for item in browser._items() if item.text(0) == "Same Name"]
    assert len(same_items) == 2
    selections = [item.data(0, browser._SELECTION_ROLE) for item in same_items]
    assert {selection.id for selection in selections} == {"same"}
    assert {selection.source for selection in selections} == {
        story_root / "scenes/one/same.yaml",
        story_root / "scenes/two/same.yaml",
    }
    assert all(browser.select(selection) for selection in selections)

    (story_root / "scenes/one/new.yaml").write_text(
        "id: new\nname: New Name\ntext: New.\n", encoding="utf-8"
    )
    browser.set_project(load_story_project(story_root, shared_root))
    assert browser.search.text() == "Same"
    assert any(item.text(0) == "Same Name" and not item.isHidden() for item in browser._items())
    assert all(item.isHidden() for item in browser._items() if item.text(0) == "New Name")


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
