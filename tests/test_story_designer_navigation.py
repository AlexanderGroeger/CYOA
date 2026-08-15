from __future__ import annotations

import os
from pathlib import Path

import pytest

from engine.story_core import ContentKind
from engine.story_core.schema import MISSING
from story_core_fixture import write_fixture_story
from story_designer.models import (
    DefinitionSelection,
    InsertNavigationEntryCommand,
    NavigationEntrySelection,
    ProjectSession,
    RemoveNavigationEntryCommand,
    SetNavigationConditionCommand,
    SetNavigationDestinationCommand,
    navigation_collection_path,
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication
    from story_designer.widgets import NavigationPanel, SceneEditorWidget
except ImportError:  # pragma: no cover
    QApplication = None  # type: ignore[assignment]


@pytest.fixture(scope="module")
def qapp():
    if QApplication is None:
        pytest.skip("PySide6 is not installed")
    return QApplication.instance() or QApplication([])


def _session(tmp_path: Path, scene_text: str) -> tuple[ProjectSession, DefinitionSelection]:
    story_root, shared_root = write_fixture_story(tmp_path)
    (story_root / "scenes" / "intro.yaml").write_text(scene_text, encoding="utf-8")
    session = ProjectSession.from_path(story_root, shared_root)
    entry = session.project.index.entry(ContentKind.SCENE, "intro")  # type: ignore[union-attr]
    assert entry is not None
    selection = DefinitionSelection(ContentKind.SCENE, "intro", entry.source)
    session.select(selection)
    return session, selection


def test_navigation_commands_preserve_paths_unknown_fields_and_authored_absence(tmp_path: Path) -> None:
    session, selection = _session(
        tmp_path,
        "id: intro\n"
        "exploration:\n"
        "  navigation:\n"
        "    - scene: ending\n"
        "      label: Keep me\n"
        "      future: {preserve: true}\n",
    )
    mapping = session.working_mapping(selection)
    assert navigation_collection_path(mapping) == ("exploration", "navigation")
    original = session.definition(selection).to_mapping()

    session.apply_command(SetNavigationDestinationCommand(selection, ("exploration", "navigation"), 0, "intro"))
    assert session.working_mapping(selection)["exploration"]["navigation"][0] == {
        "scene": "intro", "label": "Keep me", "future": {"preserve": True},
    }
    session.undo()
    assert session.working_mapping(selection) == original
    session.redo()

    session.apply_command(SetNavigationConditionCommand(
        selection, ("exploration", "navigation"), 0, {"flag": "visited"},
    ))
    assert session.working_mapping(selection)["exploration"]["navigation"][0]["conditions"] == {"flag": "visited"}
    session.undo()
    assert "conditions" not in session.working_mapping(selection)["exploration"]["navigation"][0]
    assert session.definition(selection).to_mapping() == original


def test_navigation_add_remove_undo_redo_and_save_reload(tmp_path: Path) -> None:
    session, selection = _session(tmp_path, "id: intro\nexploration: true\n")
    path = ("exploration", "navigation")
    command = InsertNavigationEntryCommand(selection, path, {"scene": "ending"})
    session.apply_command(command)
    assert session.working_mapping(selection)["exploration"]["navigation"] == [{"scene": "ending"}]
    assert len(session._history) == 1
    session.undo()
    assert session.working_mapping(selection)["exploration"] is True
    session.redo()
    session.apply_command(RemoveNavigationEntryCommand(selection, path, 0))
    assert session.working_mapping(selection)["exploration"]["navigation"] == []
    session.undo()
    assert session.working_mapping(selection)["exploration"]["navigation"] == [{"scene": "ending"}]
    session.save_all()

    reloaded = ProjectSession.from_path(session.story_root, session.shared_assets_root)
    entry = reloaded.project.index.entry(ContentKind.SCENE, "intro")  # type: ignore[union-attr]
    assert entry is not None
    reloaded_selection = DefinitionSelection(ContentKind.SCENE, "intro", entry.source)
    assert reloaded.working_mapping(reloaded_selection)["exploration"]["navigation"] == [{"scene": "ending"}]


def test_root_navigation_alias_and_missing_reference_remain_visible(tmp_path: Path) -> None:
    session, selection = _session(
        tmp_path,
        "id: intro\n"
        "exploration: true\n"
        "navigation:\n"
        "  - scene: missing_scene\n"
        "    future: yes\n",
    )
    mapping = session.working_mapping(selection)
    assert navigation_collection_path(mapping) == ("navigation",)
    presentation = __import__("story_designer.models", fromlist=["build_scene_presentation"]).build_scene_presentation(
        session.project, "intro", mapping,
    )
    assert presentation.navigation[0].destination == "missing_scene"
    assert presentation.navigation[0].resolves is False
    session.apply_command(SetNavigationDestinationCommand(selection, ("navigation",), 0, "ending"))
    assert session.working_mapping(selection)["navigation"][0]["future"] is True


def test_invalid_condition_is_rejected_without_mutating_working_state(tmp_path: Path) -> None:
    session, selection = _session(
        tmp_path,
        "id: intro\nexploration:\n  navigation:\n    - scene: ending\n      conditions: flags.open ==\n",
    )
    before = session.working_mapping(selection)
    with pytest.raises(ValueError):
        session.apply_command(SetNavigationConditionCommand(selection, ("exploration", "navigation"), 0, "flags.open =="))
    assert session.working_mapping(selection) == before
    assert session.definition(selection).to_mapping()["exploration"]["navigation"][0]["conditions"] == "flags.open =="


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_navigation_panel_reads_working_state_and_edits_condition(qapp, tmp_path: Path) -> None:
    session, selection = _session(
        tmp_path,
        "id: intro\nexploration:\n  navigation:\n    - scene: ending\n",
    )
    panel = NavigationPanel(session)
    panel.set_scene(session.project, "intro", session.working_mapping(selection))  # type: ignore[arg-type]
    assert panel.entries.count() == 1
    assert panel.condition_mode.currentData() == "absent"
    assert session.working_mapping(selection)["exploration"]["navigation"][0] == {"scene": "ending"}

    panel.condition_mode.setCurrentIndex(panel.condition_mode.findData("string"))
    panel.condition_text.setText("flags.visited")
    panel.condition_text.editingFinished.emit()
    assert session.working_mapping(selection)["exploration"]["navigation"][0]["conditions"] == "flags.visited"
    session.undo()
    assert "conditions" not in session.working_mapping(selection)["exploration"]["navigation"][0]

    editor = SceneEditorWidget(session)
    editor.set_scene(session.project, "intro", session.working_mapping(selection))  # type: ignore[arg-type]
    assert "ending" in editor.navigation_summary.text()

    opened: list[str] = []
    panel.open_destination_scene.connect(opened.append)
    panel._on_entry_double_clicked(panel.entries.item(0))
    assert opened == ["ending"]
