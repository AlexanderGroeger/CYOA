from __future__ import annotations

import os
from pathlib import Path

import pytest

from engine.story_core import ContentKind
from story_core_fixture import write_fixture_story
from story_designer.models import DefinitionSelection, ProjectSession

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QComboBox, QDoubleSpinBox
    from shiboken6 import isValid
    from story_designer.widgets import InspectorWidget
except ImportError:  # pragma: no cover
    QApplication = None  # type: ignore[assignment]


@pytest.fixture(scope="module")
def qapp():
    if QApplication is None:
        pytest.skip("PySide6 is not installed")
    return QApplication.instance() or QApplication([])


def _session(tmp_path: Path) -> tuple[ProjectSession, DefinitionSelection]:
    story_root, shared_root = write_fixture_story(tmp_path)
    (story_root / "scenes" / "intro.yaml").write_text(
        "id: intro\n"
        "exploration:\n"
        "  objects:\n"
        "    - {id: lamp, position: [1, 2]}\n"
        "    - {id: vase, position: [3, 4]}\n"
        "  look_regions:\n"
        "    - {id: desk, rect: [1, 2, 30, 20], interaction: action, event: examine}\n"
        "  look_events:\n"
        "    examine:\n"
        "      actions:\n"
        "        - {type: move_object, target: lamp, position: [5, 6], duration: 1.0}\n"
        "        - {type: set_flag, flag: opened, value: false}\n",
        encoding="utf-8",
    )
    session = ProjectSession.from_path(story_root, shared_root)
    entry = session.project.index.entry(ContentKind.SCENE, "intro")  # type: ignore[union-attr]
    assert entry is not None
    selection = DefinitionSelection(ContentKind.SCENE, "intro", entry.source)
    session.select(selection)
    return session, selection


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_value_edit_keeps_action_editor_and_selection_alive(qapp, tmp_path: Path) -> None:
    session, selection = _session(tmp_path)
    inspector = InspectorWidget(session)
    inspector.set_selection(session.project, selection, session.definition(), session.diagnostics)
    from story_designer.models import SceneElementSelection

    inspector.set_scene_element(
        SceneElementSelection("intro", "look_region", "desk"),
        session.working_mapping(selection)["exploration"]["look_regions"][0],
    )
    inspector.look_region_actions.setCurrentRow(0)
    duration = inspector._look_region_action_fields["duration"]
    target = inspector._look_region_action_fields["target"]
    assert isinstance(duration, QDoubleSpinBox)
    assert isinstance(target, QComboBox)

    history_before = len(session._history)
    duration.setValue(2.5)
    assert session.working_mapping(selection)["exploration"]["look_events"]["examine"]["actions"][0]["duration"] == 2.5
    assert inspector.look_region_actions.currentRow() == 0
    assert inspector._look_region_action_fields["duration"] is duration
    assert inspector._look_region_action_fields["target"] is target
    assert isValid(duration)
    assert isValid(target)
    assert len(session._history) == history_before + 1

    target.setCurrentIndex(target.findData("vase"))
    assert session.working_mapping(selection)["exploration"]["look_events"]["examine"]["actions"][0]["target"] == "vase"
    assert inspector._look_region_action_fields["target"] is target
    assert inspector.look_region_actions.currentRow() == 0
    assert len(session._history) == history_before + 2


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_dialogue_action_insert_is_deferred_until_event_completion(qapp, tmp_path: Path) -> None:
    session, selection = _session(tmp_path)
    inspector = InspectorWidget(session)
    inspector.set_selection(session.project, selection, session.definition(), session.diagnostics)
    from story_designer.models import SceneElementSelection

    inspector.set_scene_element(
        SceneElementSelection("intro", "look_region", "desk"),
        session.working_mapping(selection)["exploration"]["look_regions"][0],
    )
    inspector.look_region_action_type.setCurrentIndex(inspector.look_region_action_type.findData("dialog"))
    count_before = inspector.look_region_actions.count()
    history_before = len(session._history)
    inspector.look_region_add_action.click()
    assert len(session._history) == history_before
    qapp.processEvents()
    assert len(session._history) == history_before + 1
    assert inspector.look_region_actions.count() == count_before + 1
    assert inspector.look_region_actions.currentRow() == count_before


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_repeated_value_edits_do_not_leave_stale_action_widgets(qapp, tmp_path: Path) -> None:
    session, selection = _session(tmp_path)
    inspector = InspectorWidget(session)
    inspector.set_selection(session.project, selection, session.definition(), session.diagnostics)
    from story_designer.models import SceneElementSelection

    inspector.set_scene_element(
        SceneElementSelection("intro", "look_region", "desk"),
        session.working_mapping(selection)["exploration"]["look_regions"][0],
    )
    inspector.look_region_actions.setCurrentRow(0)
    duration = inspector._look_region_action_fields["duration"]
    target = inspector._look_region_action_fields["target"]
    for index in range(120):
        duration.setValue((index % 17) / 4.0)
        target.setCurrentIndex(target.findData("vase" if index % 2 else "lamp"))
        assert inspector._look_region_action_fields["duration"] is duration
        assert inspector._look_region_action_fields["target"] is target
        assert isValid(duration)
        assert isValid(target)
        assert inspector.look_region_actions.currentRow() == 0
