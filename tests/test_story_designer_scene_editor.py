from __future__ import annotations

import os
from pathlib import Path

import pytest

from engine.story_core import ContentKind
from story_core_fixture import write_fixture_story
from story_designer.models import DefinitionSelection, ProjectSession, SceneElementSelection, SetPropertyCommand

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QGraphicsScene
    from story_designer.main_window import MainWindow
    from story_designer.widgets import SceneEditorWidget, SceneGraphicsItem, WorkspaceWidget
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
def test_scene_editor_renders_background_and_object_pixmaps_headlessly(qapp, tmp_path: Path) -> None:
    session, selection = _scene_session(tmp_path)
    sprite = QImage(24, 16, QImage.Format.Format_RGBA8888)
    sprite.fill(QColor("#d1495b"))
    assert sprite.save(str(session.story_root / "assets" / "sprites" / "lamp.png"))
    editor = SceneEditorWidget(session)
    editor.set_scene(session.project, selection.id, session.working_mapping(selection))  # type: ignore[arg-type]

    rendered = QImage(320, 180, QImage.Format.Format_RGBA8888)
    rendered.fill(Qt.GlobalColor.transparent)
    painter = QPainter(rendered)
    try:
        editor.scene.render(painter, QRectF(0, 0, 320, 180), QRectF(0, 0, 320, 180))
    finally:
        painter.end()

    assert rendered.pixelColor(10, 10) == QColor("#355070")
    object_pixmap = editor.object_items["lamp"].pixmap
    assert object_pixmap is not None and not object_pixmap.isNull()

    sprite_scene = QGraphicsScene()
    sprite_scene.addItem(SceneGraphicsItem((0, 0, 24, 16), pixmap=object_pixmap))
    sprite_rendered = QImage(24, 16, QImage.Format.Format_RGBA8888)
    sprite_rendered.fill(Qt.GlobalColor.transparent)
    sprite_painter = QPainter(sprite_rendered)
    try:
        sprite_scene.render(sprite_painter, QRectF(0, 0, 24, 16), QRectF(0, 0, 24, 16))
    finally:
        sprite_painter.end()
    assert sprite_rendered.pixelColor(12, 8) == QColor("#d1495b")


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_scene_graphics_item_scales_pixmap_to_rectf_target(qapp) -> None:
    source = QImage(2, 3, QImage.Format.Format_RGBA8888)
    source.fill(QColor("#d1495b"))
    item = SceneGraphicsItem((1, 2, 17, 11), pixmap=QPixmap.fromImage(source))
    scene = QGraphicsScene()
    scene.addItem(item)

    rendered = QImage(20, 16, QImage.Format.Format_RGBA8888)
    rendered.fill(Qt.GlobalColor.transparent)
    painter = QPainter(rendered)
    try:
        scene.render(painter, QRectF(0, 0, 20, 16), QRectF(0, 0, 20, 16))
    finally:
        painter.end()

    assert rendered.pixelColor(8, 7) == QColor("#d1495b")


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

    replacement = QImage(24, 16, QImage.Format.Format_RGBA8888)
    replacement.fill(QColor("#4f772d"))
    assert replacement.save(str(session.story_root / "assets" / "backgrounds" / "new_bg.png"))
    session.apply_command(SetPropertyCommand(selection, ("background",), "new_bg.png"))
    workspace.set_state(session.project, selection, session.definition(), session.diagnostics)
    assert workspace.scene_editor.presentation is not None
    assert workspace.scene_editor.presentation.background == "new_bg.png"
    assert session.working_mapping(selection)["background"] == "new_bg.png"

    background = next(
        item for item in workspace.scene_editor.scene.items()
        if isinstance(item, SceneGraphicsItem) and item.zValue() == -10000
    )
    assert background.pixmap is not None and not background.pixmap.isNull()
    rendered = QImage(320, 180, QImage.Format.Format_RGBA8888)
    rendered.fill(Qt.GlobalColor.transparent)
    painter = QPainter(rendered)
    try:
        workspace.scene_editor.scene.render(painter, QRectF(0, 0, 320, 180), QRectF(0, 0, 320, 180))
    finally:
        painter.end()
    assert rendered.pixelColor(10, 10) == QColor("#4f772d")


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


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_qt_corner_handle_drag_previews_and_commits_one_resize(qapp, tmp_path: Path) -> None:
    session, selection = _scene_session(tmp_path)
    editor = SceneEditorWidget(session)
    editor.resize(760, 520)
    editor.set_scene(session.project, selection.id, session.working_mapping(selection))  # type: ignore[arg-type]
    ref = SceneElementSelection("intro", "look_region", "desk")
    editor.select_element(ref)
    editor.show()
    qapp.processEvents()

    owner = editor.look_region_items["desk"]
    handle = editor.resize_handles["bottom_right"]
    start = editor.view.viewport().mapFrom(editor.view, editor.view.mapFromScene(handle.sceneBoundingRect().center()))
    end = editor.view.viewport().mapFrom(editor.view, editor.view.mapFromScene(QPointF(140, 90)))
    QTest.mousePress(editor.view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(editor.view.viewport(), end)
    qapp.processEvents()
    assert owner.rect().width() > 90
    assert owner.rect().height() > 45
    assert not session.can_undo
    preview_geometry = (
        round(owner.scenePos().x()),
        round(owner.scenePos().y()),
        round(owner.rect().width()),
        round(owner.rect().height()),
    )

    QTest.mouseRelease(editor.view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    qapp.processEvents()
    assert session.working_mapping(selection)["exploration"]["look_regions"][0]["rect"] == list(preview_geometry)
    assert len(session._history) == 1


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_look_region_spinbox_step_is_immediate_and_contextual(qapp, tmp_path: Path) -> None:
    session, selection = _scene_session(tmp_path)
    window = MainWindow()
    try:
        window.session.load(session.story_root, session.shared_assets_root)
        window.session.select(selection)
        window._refresh_views()
        ref = SceneElementSelection("intro", "look_region", "desk")
        window.workspace.scene_editor.select_element(ref)
        qapp.processEvents()

        assert window.inspector.context_tabs.currentWidget() is window.inspector.look_region_context_page
        assert window.inspector.context_tabs.isTabVisible(1)
        width = window.inspector._scene_geometry_fields["width"]
        width.stepUp()
        qapp.processEvents()

        assert window.session.working_mapping(selection)["exploration"]["look_regions"][0]["rect"][2] == 91
        assert window.workspace.scene_editor.look_region_items["desk"].rect().width() == 91
        assert window.workspace.scene_editor.selected_element == ref
        assert len(window.session._history) == 1
        assert not window.inspector._scene_geometry_fields["width"].isWindow()

        height = window.inspector._scene_geometry_fields["height"]
        height.lineEdit().selectAll()
        QTest.keyClicks(height.lineEdit(), "120")
        assert window.session.working_mapping(selection)["exploration"]["look_regions"][0]["rect"][3] == 45
        QTest.keyClick(height.lineEdit(), Qt.Key.Key_Return)
        qapp.processEvents()
        assert window.session.working_mapping(selection)["exploration"]["look_regions"][0]["rect"][3] == 120
        assert len(window.session._history) == 2

        before = {id(widget) for widget in qapp.topLevelWidgets()}
        assert window.workspace.scene_editor.add_look_region()
        qapp.processEvents()
        after = {id(widget) for widget in qapp.topLevelWidgets()}
        unexpected_visible = [
            widget for widget in qapp.topLevelWidgets()
            if id(widget) in after - before and widget.isVisible() and widget is not window
        ]
        assert not unexpected_visible
        created_ref = window.workspace.scene_editor.selected_element
        assert created_ref is not None and created_ref.kind == "look_region"
        assert window.inspector.context_tabs.currentWidget() is window.inspector.look_region_context_page
        assert len(window.workspace.scene_editor.resize_handles) == 4
        window.workspace.scene_editor.select_element(None)
        assert not window.inspector.context_tabs.isTabVisible(1)
        assert window.inspector.context_tabs.currentWidget() is window.inspector.scene_context_page
    finally:
        window.session.revert_all()
        window.close()


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_object_context_tab_edits_identity_transform_and_z_order(qapp, tmp_path: Path) -> None:
    session, selection = _scene_session(tmp_path)
    window = MainWindow()
    try:
        window.session.load(session.story_root, session.shared_assets_root)
        window.session.select(selection)
        window._refresh_views()
        ref = SceneElementSelection("intro", "object", "lamp")
        window.workspace.scene_editor.select_element(ref)
        qapp.processEvents()

        assert window.inspector.context_tabs.currentWidget() is window.inspector.object_context_page
        assert window.inspector.context_tabs.isTabVisible(2)
        assert window.inspector.object_identity_id.text() == "lamp"
        assert window.inspector._scene_geometry_fields["x"].value() == 40
        assert window.inspector._scene_geometry_fields["z"].value() == 7

        window.inspector.object_name_edit.setText("Reading Lamp")
        window.inspector.object_name_edit.editingFinished.emit()
        window.inspector._scene_geometry_fields["rotation"].setValue(25)
        window.inspector._scene_geometry_fields["z"].setValue(12)
        qapp.processEvents()

        object_mapping = window.session.working_mapping(selection)["exploration"]["objects"][0]
        assert object_mapping["name"] == "Reading Lamp"
        assert object_mapping["rotation"] == 25.0
        assert object_mapping["z"] == 12
        assert window.workspace.scene_editor.object_items["lamp"].rotation() == 25
        assert window.workspace.scene_editor.object_items["lamp"].zValue() == 112
    finally:
        window.session.revert_all()
        window.close()


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_region_selection_overlay_switch_uses_one_complete_scene_rect(qapp, tmp_path: Path) -> None:
    session, selection = _scene_session(tmp_path)
    editor = SceneEditorWidget(session)
    editor.set_scene(session.project, selection.id, session.working_mapping(selection))  # type: ignore[arg-type]

    desk = SceneElementSelection("intro", "look_region", "desk")
    assert editor.add_look_region()
    stream = editor.selected_element
    assert stream is not None and stream.kind == "look_region"
    assert editor.commit_geometry(stream, (180, 8, 120, 18))

    editor.select_element(desk)
    editor.select_element(stream)
    item = editor.look_region_items[stream.id]
    assert item.scene_rect().getRect() == pytest.approx((180, 8, 120, 18))
    expected_corners = {
        "top_left": QPointF(180, 8),
        "top_right": QPointF(300, 8),
        "bottom_left": QPointF(180, 26),
        "bottom_right": QPointF(300, 26),
    }
    for corner, expected in expected_corners.items():
        assert editor.resize_handles[corner].sceneBoundingRect().center() == expected


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_region_selection_overlay_switches_both_directions_and_cycles_radically_different_regions(
    qapp, tmp_path: Path
) -> None:
    session, selection = _scene_session(tmp_path)
    editor = SceneEditorWidget(session)
    editor.set_scene(session.project, selection.id, session.working_mapping(selection))  # type: ignore[arg-type]

    refs = [SceneElementSelection("intro", "look_region", "desk")]
    geometries = [(250, 10, 20, 20), (10, 110, 280, 40), (150, 35, 30, 120)]
    assert editor.commit_geometry(refs[0], geometries[0])
    for geometry in geometries[1:]:
        assert editor.add_look_region()
        ref = editor.selected_element
        assert ref is not None and ref.kind == "look_region"
        refs.append(ref)
        assert editor.commit_geometry(ref, geometry)

    def assert_overlay(ref: SceneElementSelection, geometry: tuple[int, int, int, int]) -> None:
        editor.select_element(ref)
        item = editor.look_region_items[ref.id]
        assert item.scene_rect().getRect() == pytest.approx(geometry)
        x, y, width, height = geometry
        expected = {
            "top_left": QPointF(x, y),
            "top_right": QPointF(x + width, y),
            "bottom_left": QPointF(x, y + height),
            "bottom_right": QPointF(x + width, y + height),
        }
        for corner, point in expected.items():
            handle = editor.resize_handles[corner]
            assert handle.owner is item
            assert handle.parentItem() is item
            assert handle.sceneBoundingRect().center() == point

    assert_overlay(refs[0], geometries[0])
    assert_overlay(refs[1], geometries[1])
    assert_overlay(refs[0], geometries[0])
    for _ in range(100):
        for ref, geometry in zip(refs, geometries):
            assert_overlay(ref, geometry)


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_region_resize_after_cross_selection_uses_current_semantic_geometry_and_undo_redo_refreshes(
    qapp, tmp_path: Path
) -> None:
    session, selection = _scene_session(tmp_path)
    editor = SceneEditorWidget(session)
    editor.set_scene(session.project, selection.id, session.working_mapping(selection))  # type: ignore[arg-type]
    acorn = SceneElementSelection("intro", "look_region", "desk")
    assert editor.commit_geometry(acorn, (250, 10, 20, 20))
    assert editor.add_look_region()
    stream = editor.selected_element
    assert stream is not None and stream.kind == "look_region"
    assert editor.commit_geometry(stream, (10, 110, 280, 40))

    editor.select_element(acorn)
    stream_item = editor.look_region_items[stream.id]
    # Recreate the observed split: the stream item is at the right identity,
    # but its view has retained the previous region's dimensions.
    stream_item.set_scene_rect(QRectF(10, 110, 20, 20))
    editor._begin_resize(stream_item, "bottom_right", QPointF(30, 130))
    editor._preview_resize(stream_item, "bottom_right", QPointF(310, 160))
    assert stream_item.scene_rect().getRect() == pytest.approx((10, 110, 300, 50))
    editor._finish_resize(stream_item, "bottom_right", QPointF(310, 160))
    assert session.working_mapping(selection)["exploration"]["look_regions"][1]["rect"] == [10, 110, 300, 50]

    editor.select_element(stream)
    assert editor.commit_geometry(stream, (10, 110, 280, 60))
    session.undo()
    editor._refresh_authoritative(stream)
    assert editor.scene_element_bounds(stream).getRect() == pytest.approx((10, 110, 300, 50))
    session.redo()
    editor._refresh_authoritative(stream)
    assert editor.scene_element_bounds(stream).getRect() == pytest.approx((10, 110, 280, 60))


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_object_selection_overlay_also_refreshes_from_current_working_geometry(qapp, tmp_path: Path) -> None:
    session, selection = _scene_session(tmp_path)
    editor = SceneEditorWidget(session)
    editor.set_scene(session.project, selection.id, session.working_mapping(selection))  # type: ignore[arg-type]
    ref = SceneElementSelection("intro", "object", "lamp")
    item = editor.object_items[ref.id]
    item.setPos(200, 140)
    editor.select_element(ref)
    assert item.scene_rect().getRect() == pytest.approx((40, 30, 24, 16))


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_completed_overlap_clicks_cycle_region_and_object_without_handle_pollution(qapp, tmp_path: Path) -> None:
    session, selection = _scene_session(tmp_path)
    editor = SceneEditorWidget(session)
    editor.resize(760, 520)
    editor.set_scene(session.project, selection.id, session.working_mapping(selection))  # type: ignore[arg-type]
    editor.show()
    qapp.processEvents()

    region = SceneElementSelection("intro", "look_region", "desk")
    object_ref = SceneElementSelection("intro", "object", "lamp")
    editor.select_element(region)
    point = editor.view.viewport().mapFrom(editor.view, editor.view.mapFromScene(QPointF(50, 35)))
    QTest.mouseClick(editor.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    qapp.processEvents()
    assert editor.selected_element == object_ref
    QTest.mouseClick(editor.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    qapp.processEvents()
    assert editor.selected_element == region


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_region_geometry_commands_constrain_bounds_but_objects_remain_unbounded(qapp, tmp_path: Path) -> None:
    session, selection = _scene_session(tmp_path)
    editor = SceneEditorWidget(session)
    editor.set_scene(session.project, selection.id, session.working_mapping(selection))  # type: ignore[arg-type]
    region = SceneElementSelection("intro", "look_region", "desk")

    assert editor.commit_geometry(region, (300, 170, 90, 45))
    assert session.working_mapping(selection)["exploration"]["look_regions"][0]["rect"] == [230, 135, 90, 45]

    object_ref = SceneElementSelection("intro", "object", "lamp")
    assert editor.commit_geometry(object_ref, (400, 250))
    assert session.working_mapping(selection)["exploration"]["objects"][0]["position"] == [400, 250]
