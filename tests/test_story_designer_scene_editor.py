from __future__ import annotations

import os
from pathlib import Path

import pytest

from engine.story_core import ContentKind
from story_core_fixture import write_fixture_story
from story_designer.models import DefinitionSelection, ProjectSession, SceneElementSelection, SetPropertyCommand

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication
    from story_designer.main_window import MainWindow
    from story_designer.widgets import SceneEditorWidget, WorkspaceWidget
except ImportError:  # pragma: no cover - retained for minimal Core-only environments
    QApplication = None  # type: ignore[assignment]


@pytest.fixture(scope="module")
def qapp():
    if QApplication is None:
        pytest.skip("PySide6 is not installed")
    return QApplication.instance() or QApplication([])


def _scene_session(tmp_path: Path) -> tuple[ProjectSession, DefinitionSelection]:
    story_root, shared_root = write_fixture_story(tmp_path)
    (story_root / "assets" / "backgrounds").mkdir(parents=True)
    (story_root / "assets" / "sprites").mkdir(parents=True)
    image = QImage(24, 16, QImage.Format.Format_RGBA8888)
    image.fill(QColor("#355070"))
    assert image.save(str(story_root / "assets" / "backgrounds" / "bg.png"))
    assert image.save(str(story_root / "assets" / "sprites" / "lamp.png"))
    (story_root / "scenes" / "intro.yaml").write_text(
        "id: intro\n"
        "background: bg.png\n"
        "exploration:\n"
        "  objects:\n"
        "    - id: lamp\n"
        "      sprite: lamp.png\n"
        "      position: [40, 30]\n"
        "      size: [24, 16]\n"
        "      z: 7\n"
        "      visible_when: {flag: lamp_on}\n"
        "    - id: missing\n"
        "      sprite: absent.png\n"
        "      position: [120, 80]\n"
        "      size: [30, 20]\n"
        "  look_regions:\n"
        "    - id: desk\n"
    "      rect: [20, 20, 90, 45]\n"
        "      interaction: inspect\n"
        "      priority: 2\n",
        encoding="utf-8",
    )
    session = ProjectSession.from_path(story_root, shared_root)
    entry = session.project.index.entry(ContentKind.SCENE, "intro")  # type: ignore[union-attr]
    assert entry is not None
    selection = DefinitionSelection(ContentKind.SCENE, "intro", entry.source)
    session.select(selection)
    return session, selection


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_scene_canvas_uses_logical_dimensions_and_authored_geometry(qapp, tmp_path: Path) -> None:
    session, selection = _scene_session(tmp_path)
    editor = SceneEditorWidget(session)
    editor.set_scene(session.project, selection.id, session.working_mapping(selection))  # type: ignore[arg-type]

    assert editor.scene.sceneRect().width() == 320
    assert editor.scene.sceneRect().height() == 180
    assert editor.presentation is not None and editor.presentation.background_path is not None
    assert editor.object_items["lamp"].pos().x() == 40
    assert editor.object_items["lamp"].pos().y() == 30
    assert editor.object_items["lamp"].rect().width() == 24
    assert editor.object_items["lamp"].rect().height() == 16
    assert editor.look_region_items["desk"].rect().width() == 90
    assert editor.look_region_items["desk"].rect().height() == 45


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_scene_selection_and_missing_conditional_content_are_explicit(qapp, tmp_path: Path) -> None:
    session, selection = _scene_session(tmp_path)
    editor = SceneEditorWidget(session)
    editor.set_scene(session.project, selection.id, session.working_mapping(selection))  # type: ignore[arg-type]
    selected: list[SceneElementSelection | None] = []
    editor.element_selected.connect(selected.append)

    editor.select_element(SceneElementSelection("intro", "look_region", "desk"))
    assert editor.selected_element == SceneElementSelection("intro", "look_region", "desk")
    assert selected[-1] == SceneElementSelection("intro", "look_region", "desk")
    assert "conditional" in editor.object_items["lamp"].toolTip()
    assert "absent.png" in editor.object_items["missing"].toolTip()
    assert editor.object_items["missing"].missing is not None

    original_rect = editor.scene.sceneRect()
    editor.set_zoom(2.0)
    assert editor.scene.sceneRect() == original_rect


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_workspace_renders_current_scene_working_mapping_and_inspector_context(qapp, tmp_path: Path) -> None:
    session, selection = _scene_session(tmp_path)
    workspace = WorkspaceWidget(session)
    workspace.set_state(session.project, selection, session.definition(), session.diagnostics)
    assert workspace.tabs.currentWidget() is workspace.scene_editor

    session.apply_command(SetPropertyCommand(selection, ("background",), "new_bg.png"))
    workspace.set_state(session.project, selection, session.definition(), session.diagnostics)
    assert workspace.scene_editor.presentation is not None
    assert workspace.scene_editor.presentation.background == "new_bg.png"


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_graphical_selection_updates_main_window_inspector(qapp, tmp_path: Path) -> None:
    session, selection = _scene_session(tmp_path)
    window = MainWindow()
    try:
        window.session.load(session.story_root, session.shared_assets_root)
        window.session.select(selection)
        window._refresh_views()
        window.workspace.scene_editor.select_element(SceneElementSelection("intro", "object", "lamp"))
        assert window.inspector.header.text().startswith("Scene Object: lamp")
        assert window.inspector.summary.toPlainText().find("sprite") >= 0
    finally:
        window.session.revert_all()
        window.close()


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_object_geometry_commit_is_one_logical_undoable_edit_and_keeps_core_immutable(qapp, tmp_path: Path) -> None:
    session, selection = _scene_session(tmp_path)
    editor = SceneEditorWidget(session)
    editor.set_scene(session.project, selection.id, session.working_mapping(selection))  # type: ignore[arg-type]
    ref = SceneElementSelection("intro", "object", "lamp")
    core_before = session.definition(selection).to_mapping()

    editor.select_element(ref)
    item = editor.object_items["lamp"]
    item.setPos(115, 72)
    editor._finish_item_drag(item, item.pos(), item.pos())

    assert session.working_mapping(selection)["exploration"]["objects"][0]["position"] == [115, 72]
    assert session.definition(selection).to_mapping() == core_before
    assert len(session._history) == 1
    assert session.is_dirty
    assert editor.object_items["lamp"].pos().x() == 115
    assert editor.object_items["lamp"].pos().y() == 72

    session.undo()
    assert session.working_mapping(selection)["exploration"]["objects"][0]["position"] == [40, 30]
    session.redo()
    assert session.working_mapping(selection)["exploration"]["objects"][0]["position"] == [115, 72]


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_noop_object_drag_and_zoom_do_not_change_history_or_authored_coordinates(qapp, tmp_path: Path) -> None:
    session, selection = _scene_session(tmp_path)
    editor = SceneEditorWidget(session)
    editor.set_scene(session.project, selection.id, session.working_mapping(selection))  # type: ignore[arg-type]
    editor.set_zoom(2.5)
    ref = SceneElementSelection("intro", "object", "lamp")
    item = editor.object_items["lamp"]
    editor._finish_item_drag(item, item.pos(), item.pos())

    assert not session.is_dirty
    assert not session.can_undo
    assert session.working_mapping(selection)["exploration"]["objects"][0]["position"] == [40, 30]
    assert editor.view.transform().m11() == pytest.approx(2.5)


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_look_region_move_resize_handles_and_invalid_resize(qapp, tmp_path: Path) -> None:
    session, selection = _scene_session(tmp_path)
    editor = SceneEditorWidget(session)
    editor.set_scene(session.project, selection.id, session.working_mapping(selection))  # type: ignore[arg-type]
    ref = SceneElementSelection("intro", "look_region", "desk")
    editor.select_element(ref)
    assert len(editor.resize_handles) == 4
    assert all(handle.isVisible() for handle in editor.resize_handles.values())

    assert editor.commit_geometry(ref, (30, 35, 90, 45))
    assert session.working_mapping(selection)["exploration"]["look_regions"][0]["rect"] == [30, 35, 90, 45]
    assert len(session._history) == 1
    owner = editor.look_region_items["desk"]
    editor._begin_resize(owner, "top_left", QPointF(10, 10))
    editor._preview_resize(owner, "top_left", QPointF(10, 10))
    editor._finish_resize(owner, "top_left", QPointF(10, 10))
    assert session.working_mapping(selection)["exploration"]["look_regions"][0]["rect"] == [10, 10, 110, 70]
    assert len(session._history) == 2
    assert editor.commit_geometry(ref, (10, 10, 60, 25))
    assert session.working_mapping(selection)["exploration"]["look_regions"][0]["rect"] == [10, 10, 60, 25]
    assert len(session._history) == 3

    assert not editor.commit_geometry(ref, (10, 10, 0, 25))
    assert session.working_mapping(selection)["exploration"]["look_regions"][0]["rect"] == [10, 10, 60, 25]

    editor.select_element(None)
    assert not any(handle.isVisible() for handle in editor.resize_handles.values())


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_graphical_geometry_updates_inspector_and_inspector_updates_graphics(qapp, tmp_path: Path) -> None:
    session, selection = _scene_session(tmp_path)
    window = MainWindow()
    try:
        window.session.load(session.story_root, session.shared_assets_root)
        window.session.select(selection)
        window._refresh_views()
        ref = SceneElementSelection("intro", "object", "lamp")
        window.workspace.scene_editor.select_element(ref)
        assert window.inspector._scene_geometry_fields["x"].value() == 40

        window.inspector._scene_geometry_fields["x"].setValue(155)
        window.inspector._scene_geometry_fields["x"].editingFinished.emit()
        assert window.session.working_mapping(selection)["exploration"]["objects"][0]["position"] == [155, 30]
        assert window.workspace.scene_editor.object_items["lamp"].pos().x() == 155
        assert window.inspector._scene_geometry_fields["x"].value() == 155
        assert window.undo()
        assert window.workspace.scene_editor.object_items["lamp"].pos().x() == 40
        assert window.workspace.scene_editor.selected_element == ref
        assert window.inspector._scene_geometry_fields["x"].value() == 40
        assert window.redo()
        assert window.workspace.scene_editor.object_items["lamp"].pos().x() == 155
    finally:
        window.session.revert_all()
        window.close()


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_graphical_geometry_save_reload_and_missing_asset_object(qapp, tmp_path: Path) -> None:
    session, selection = _scene_session(tmp_path)
    editor = SceneEditorWidget(session)
    editor.set_scene(session.project, selection.id, session.working_mapping(selection))  # type: ignore[arg-type]
    missing = SceneElementSelection("intro", "object", "missing")
    assert editor.commit_geometry(missing, (210, 90))
    assert editor.object_items["missing"].missing is not None
    assert editor.object_items["missing"].pos().x() == 210
    assert editor.commit_geometry(SceneElementSelection("intro", "object", "lamp"), (205, 90))
    # The fixture intentionally contains an unresolved placeholder asset; use
    # the existing persistence override so this test can still verify the
    # semantic geometry round trip.
    assert session.save_all(allow_validation_errors=True)

    reloaded = ProjectSession.from_path(session.story_root, session.shared_assets_root)
    entry = reloaded.project.index.entry(ContentKind.SCENE, "intro")  # type: ignore[union-attr]
    assert entry is not None
    reloaded_selection = DefinitionSelection(ContentKind.SCENE, "intro", entry.source)
    assert reloaded.working_mapping(reloaded_selection)["exploration"]["objects"][0]["position"] == [205, 90]
