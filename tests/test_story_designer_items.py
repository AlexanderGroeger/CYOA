"""Focused headless coverage for the dedicated Items tool."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.story_core import ContentKind
from story_designer.models import DefinitionSelection, ProjectSession
from story_designer.models.editing import SetPropertyCommand
from story_designer.widgets.item_editor import ItemPreviewWidget, SquarePreviewWidget
from story_designer.widgets.workspace import WorkspaceWidget
from story_core_fixture import write_fixture_story

try:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPixmap
    from PySide6.QtWidgets import QApplication
    import shiboken6
except ImportError:  # pragma: no cover
    QApplication = None  # type: ignore[assignment]
    shiboken6 = None  # type: ignore[assignment]


@pytest.fixture(scope="module")
def qapp():
    if QApplication is None:
        pytest.skip("PySide6 is not installed")
    return QApplication.instance() or QApplication([])


def _item_session(tmp_path: Path) -> tuple[ProjectSession, DefinitionSelection]:
    root, shared = write_fixture_story(tmp_path)
    session = ProjectSession.from_path(root, shared)
    entry = session.project.index.entry(ContentKind.ITEM, "intro")
    assert entry is not None
    selection = DefinitionSelection(ContentKind.ITEM, "intro", entry.source)
    session.select(selection)
    return session, selection


def _multi_item_session(tmp_path: Path) -> tuple[ProjectSession, dict[str, DefinitionSelection]]:
    root, shared = write_fixture_story(tmp_path)
    (root / "items" / "items.yaml").write_text(
        "alpha:\n"
        "  name: Alpha\n"
        "  type: consumable\n"
        "  description: Alpha description\n"
        "beta:\n"
        "  name: Beta\n"
        "  type: equipment\n"
        "  equipment_slot: head\n"
        "  combat:\n"
        "    move_grants: [intro]\n"
        "gamma:\n"
        "  name: Gamma\n"
        "  type: legacy\n"
        "  future_item_extension: {preserves: true}\n",
        encoding="utf-8",
    )
    session = ProjectSession.from_path(root, shared)
    selections = {
        item_id: DefinitionSelection(ContentKind.ITEM, item_id, session.project.index.entry(ContentKind.ITEM, item_id).source)
        for item_id in ("alpha", "beta", "gamma")
    }
    return session, selections


def _activate_preview(preview: ItemPreviewWidget, qapp: QApplication, width: int, height: int) -> None:
    preview.resize(width, height)
    preview.show()
    qapp.processEvents()
    preview.layout().activate()
    preview.card_layout.activate()
    qapp.processEvents()


def _assert_preview_geometry_is_separated(preview: ItemPreviewWidget) -> None:
    viewport = preview.sprite_viewport
    text = preview.text_container
    assert viewport.parent() is preview.preview_card
    assert text.parent() is preview.preview_card
    assert not viewport.geometry().intersects(text.geometry())
    assert text.geometry().top() >= viewport.geometry().bottom() + 17  # 16 px gap, inclusive QRect bottom
    assert viewport.contentsRect().contains(preview.sprite_label.geometry())
    assert viewport.width() == viewport.height()


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_items_tool_is_filtered_and_selection_drives_preview_and_context(qapp, tmp_path: Path) -> None:
    session, selection = _item_session(tmp_path)
    workspace = WorkspaceWidget(session)
    workspace.set_state(session.project, selection, session.definition(), session.diagnostics)

    assert "Items" in [workspace.tabs.tabText(index) for index in range(workspace.tabs.count())]
    assert workspace.item_shell.splitter.count() == 3
    visible = [item for item in workspace.item_navigator._items() if not item.isHidden()]
    assert visible
    assert all(
        item.data(0, workspace.item_navigator._SELECTION_ROLE).kind is ContentKind.ITEM
        for item in visible
        if item.data(0, workspace.item_navigator._SELECTION_ROLE) is not None
    )
    workspace.item_navigator.search.setText("intro token")
    assert all(
        "intro token" in item.text(0).casefold()
        for item in workspace.item_navigator._items()
        if not item.isHidden() and item.data(0, workspace.item_navigator._SELECTION_ROLE) is not None
    )
    assert workspace.item_preview.name_value.text() == "Intro Token"
    assert workspace.item_properties.id_value.text() == "intro"


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_item_name_description_stats_and_undo_redo_update_preview_incrementally(qapp, tmp_path: Path) -> None:
    session, selection = _item_session(tmp_path)
    session.apply_command(SetPropertyCommand(selection, ("description",), "A token description."))
    workspace = WorkspaceWidget(session)
    workspace.set_state(session.project, selection, session.definition(), session.diagnostics)
    properties = workspace.item_properties

    assert properties._editors[("description",)].toPlainText() == "A token description."
    assert workspace.item_preview.description_value.text() == "A token description."

    name_editor = properties._editors[("name",)]
    name_editor.setText("Renamed Token")
    name_editor.editingFinished.emit()
    assert session.working_mapping(selection)["name"] == "Renamed Token"
    assert workspace.item_preview.name_value.text() == "Renamed Token"

    description_editor = properties._editors[("description",)]
    description_editor.setPlainText("A changed description.")
    description_editor.focusOutEvent  # keep the concrete editor alive for the signal path
    description_editor.value_edited.emit("A changed description.")
    assert workspace.item_preview.description_value.text() == "A changed description."

    properties._stat_spins["attack"].setValue(4)
    assert session.working_mapping(selection)["stats"]["attack"] == 4
    assert "ATK +4" in workspace.item_preview.stats_value.text()

    session.undo()
    workspace.item_properties.set_state(session.project, selection, session.definition(), session.diagnostics)
    workspace.item_preview.update_from_mapping(session.working_mapping(selection) or {})
    assert workspace.item_preview.stats_value.text() == ""
    assert not workspace.item_preview.stats_value.isVisible()
    session.redo()
    workspace.item_properties.set_state(session.project, selection, session.definition(), session.diagnostics)
    workspace.item_preview.update_from_mapping(session.working_mapping(selection) or {})
    assert "ATK +4" in workspace.item_preview.stats_value.text()


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_item_asset_context_move_grants_and_missing_sprite_are_safe(qapp, tmp_path: Path) -> None:
    session, selection = _item_session(tmp_path)
    workspace = WorkspaceWidget(session)
    workspace.set_state(session.project, selection, session.definition(), session.diagnostics)
    properties = workspace.item_properties

    assert properties._editors[("icon",)].asset_kind == "items"
    assert isinstance(workspace.item_preview, ItemPreviewWidget)
    assert "No sprite" in workspace.item_preview.sprite.text()

    properties.move_grants_combo.setCurrentText("intro")
    properties.move_grants_add.click()
    assert session.working_mapping(selection)["combat"]["move_grants"] == ["intro"]
    assert workspace.item_preview.moves_value.text() == "Moves: Intro Strike"
    assert "Grants Move" not in workspace.item_preview.capabilities_value.text()


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_item_preview_presentation_rules(qapp, tmp_path: Path) -> None:
    session, selection = _item_session(tmp_path)
    preview = ItemPreviewWidget(session)
    preview.set_state(
        session.project,
        selection,
        {
            "name": "Battle Charm",
            "type": "weapon",
            "description": "A charm with uneven bonuses.",
            "stats": {"hp": 0, "attack": -3, "defense": 0},
            "combat": {"move_grants": ["intro", "missing_move", "intro", "missing_two"]},
        },
    )

    assert preview.stats_value.text() == "ATK -3"
    assert not preview.stats_value.isHidden()
    assert preview.type_value.font().italic()
    default_size = qapp.font().pointSizeF()
    assert preview.name_value.font().pointSizeF() >= 20
    assert preview.name_value.font().pointSizeF() > default_size
    assert preview.type_value.font().pointSizeF() >= 14
    assert preview.type_value.font().pointSizeF() > default_size
    assert preview.description_value.font().pointSizeF() >= 13
    assert preview.stats_value.font().pointSizeF() >= 12
    assert preview.capabilities_value.font().pointSizeF() >= 12
    assert preview.moves_value.font().pointSizeF() >= 12
    assert preview.sprite.alignment() & Qt.AlignmentFlag.AlignCenter == Qt.AlignmentFlag.AlignCenter
    assert preview.preview_card.parent() is preview.preview_scroll.viewport()
    assert preview.sprite_viewport.parent() is preview.preview_card
    assert preview.text_container.parent() is preview.preview_card
    assert preview.card_layout.indexOf(preview.sprite_frame) >= 0
    assert preview.text_layout.indexOf(preview.name_value) >= 0
    assert "Equippable" not in preview.capabilities_value.text()
    assert preview.moves_value.text() == "Moves: Intro Strike · missing_move · Intro Strike · +1"

    preview.resize(600, 900)
    preview.show()
    qapp.processEvents()
    assert preview.sprite_frame.geometry().bottom() < preview.text_stack.geometry().top()
    assert preview.name_value.geometry().bottom() < preview.type_value.geometry().top()
    assert preview.type_value.geometry().bottom() < preview.description_value.geometry().top()

    preview.update_from_mapping({"name": "Empty Charm", "type": "key", "stats": {"hp": 0, "attack": 0, "defense": 0}})
    assert preview.stats_value.text() == ""
    assert preview.stats_value.isHidden()
    assert preview.moves_value.isHidden()


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_item_preview_font_hierarchy_survives_global_label_stylesheet(qapp, tmp_path: Path) -> None:
    session, selection = _item_session(tmp_path)
    previous_stylesheet = qapp.styleSheet()
    qapp.setStyleSheet("QLabel { font-size: 8px; }")
    try:
        preview = ItemPreviewWidget(session)
        preview.set_state(session.project, selection, {"name": "Styled Item", "type": "key", "description": "Readable"})
        qapp.processEvents()
        assert preview.name_value.font().pointSizeF() >= 20
        assert preview.type_value.font().pointSizeF() >= 14
        assert preview.type_value.font().italic()
        assert preview.description_value.font().pointSizeF() >= 13
    finally:
        qapp.setStyleSheet(previous_stylesheet)


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_item_preview_runtime_geometry_never_overlaps_at_realistic_sizes(qapp, tmp_path: Path) -> None:
    session, selection = _item_session(tmp_path)
    preview = ItemPreviewWidget(session)
    preview.set_state(
        session.project,
        selection,
        {
            "name": "Field Ration",
            "type": "consumable",
            "description": "A wrapped meal that restores a little health.",
            "actions": ["use"],
            "combat": {"effects": [{"kind": "heal", "amount": 2}]},
        },
    )

    for width, height in ((500, 500), (800, 550), (1200, 700)):
        _activate_preview(preview, qapp, width, height)
        _assert_preview_geometry_is_separated(preview)


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_item_preview_long_description_grows_downward_and_shapes_stay_contained(qapp, tmp_path: Path) -> None:
    session, selection = _item_session(tmp_path)
    preview = ItemPreviewWidget(session)
    preview.set_state(
        session.project,
        selection,
        {
            "name": "Field Ration",
            "type": "consumable",
            "description": "A wrapped meal that restores a little health. " * 20,
            "actions": ["use"],
        },
    )

    for width, height in ((500, 500), (800, 550), (1200, 700)):
        _activate_preview(preview, qapp, width, height)
        _assert_preview_geometry_is_separated(preview)
        assert preview.text_container.geometry().top() > preview.sprite_viewport.geometry().bottom()
        assert preview.description_value.height() > preview.description_value.fontMetrics().height()

    for image_width, image_height in ((32, 32), (16, 48), (48, 16), (2, 2)):
        image = QImage(image_width, image_height, QImage.Format.Format_ARGB32)
        image.fill(0xFFFF0000)
        preview.sprite.setPixmap(QPixmap.fromImage(image))
        _activate_preview(preview, qapp, 500, 500)
        _assert_preview_geometry_is_separated(preview)
        label_contents = preview.sprite_label.contentsRect()
        displayed = preview.sprite_label.pixmap()
        assert displayed is not None and not displayed.isNull()
        assert displayed.width() <= label_contents.width()
        assert displayed.height() <= label_contents.height()


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_optional_equippable_and_square_sprite_surface(qapp, tmp_path: Path) -> None:
    session, selection = _item_session(tmp_path)
    preview = ItemPreviewWidget(session)
    preview.set_state(
        session.project,
        selection,
        {"name": "Optional Gear", "type": "key", "equipment_slot": "head", "stats": {"defense": 2}},
    )
    assert "Equippable" in preview.capabilities_value.text()

    assert isinstance(preview.sprite, SquarePreviewWidget)
    assert preview.sprite.minimumWidth() == preview.sprite.minimumHeight()
    assert preview.sprite.sizeHint().width() == preview.sprite.sizeHint().height()
    assert preview.sprite.heightForWidth(220) == 220
    preview.resize(600, 900)
    preview.show()
    qapp.processEvents()
    assert preview.sprite.width() == preview.sprite.height()


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_small_sprite_scales_sharply_inside_square_and_missing_stays_square(qapp, tmp_path: Path) -> None:
    session, selection = _item_session(tmp_path)
    image_path = session.project.story_root / "assets" / "items" / "tiny.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(8, 4, QImage.Format.Format_ARGB32)
    image.fill(0xFFFF0000)
    assert image.save(str(image_path))

    preview = ItemPreviewWidget(session)
    preview.set_state(session.project, selection, {"name": "Tiny", "icon": "tiny.png"})
    preview.resize(600, 900)
    preview.show()
    qapp.processEvents()
    assert not preview.sprite.pixmap().isNull()
    assert preview.sprite.pixmap().width() > 8
    assert preview.sprite.pixmap().height() < preview.sprite.pixmap().width()
    assert preview.sprite.width() == preview.sprite.height()
    contents = preview.sprite.contentsRect()
    assert preview.sprite.pixmap().width() <= contents.width()
    assert preview.sprite.pixmap().height() <= contents.height()
    assert contents.height() - preview.sprite.pixmap().height() > 0
    assert preview.sprite.alignment() & Qt.AlignmentFlag.AlignCenter == Qt.AlignmentFlag.AlignCenter
    assert preview.sprite.geometry().center().x() == preview.preview_card.rect().center().x()

    preview.update_from_mapping({"name": "Missing", "icon": "missing.png"})
    assert "Missing item image" in preview.sprite.text()
    assert preview.sprite.width() == preview.sprite.height()
    assert preview.sprite.alignment() & Qt.AlignmentFlag.AlignCenter == Qt.AlignmentFlag.AlignCenter


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_items_empty_collection_and_save_reload_preserve_authored_extensions(qapp, tmp_path: Path) -> None:
    session, selection = _item_session(tmp_path)
    workspace = WorkspaceWidget(session)
    workspace.set_state(session.project, selection, session.definition(), session.diagnostics)
    session.apply_command(SetPropertyCommand(selection, ("description",), "Saved description"))
    assert session.save()
    assert session.project is not None
    reloaded = session.working_mapping(selection)
    assert reloaded["description"] == "Saved description"
    assert reloaded["future_item_extension"] == {"preserves": True}

    empty_root, shared = write_fixture_story(tmp_path / "empty")
    (empty_root / "items" / "items.yaml").write_text("{}\n", encoding="utf-8")
    empty_session = ProjectSession.from_path(empty_root, shared)
    empty_workspace = WorkspaceWidget(empty_session)
    empty_workspace.set_state(empty_session.project, None, None, empty_session.diagnostics)
    assert not empty_workspace.item_navigator.empty_state.isHidden()
    assert empty_workspace.item_properties.status.text() == "No item selected."


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_reselecting_items_keeps_persistent_controls_valid(qapp, tmp_path: Path) -> None:
    session, selections = _multi_item_session(tmp_path)
    workspace = WorkspaceWidget(session)
    initial = selections["alpha"]
    session.select(initial)
    workspace.set_state(session.project, initial, session.definition(initial), session.diagnostics)
    properties = workspace.item_properties
    captured = (
        properties.status,
        properties._editors.get(("name",)),
        properties._editors.get(("type",)),
        properties._editors.get(("icon",)),
        properties.move_grants,
        properties.move_grants_combo,
    )

    for item_id in ("alpha", "beta", "alpha", "gamma", "beta", "alpha") * 25:
        selection = selections[item_id]
        session.select(selection)
        workspace.set_state(session.project, selection, session.definition(selection), session.diagnostics)
        qapp.processEvents()
        assert workspace.item_preview.name_value.text() == item_id.title()
        assert properties.id_value.text() == item_id
        assert all(shiboken6.isValid(widget) for widget in captured if widget is not None)
        assert shiboken6.isValid(properties.status)

    assert properties._editors[("name",)] is captured[1]
    assert properties._editors[("type",)] is captured[2]
    assert properties._editors[("icon",)] is captured[3]


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_item_edit_survives_switching_and_returning(qapp, tmp_path: Path) -> None:
    session, selections = _multi_item_session(tmp_path)
    workspace = WorkspaceWidget(session)
    properties = workspace.item_properties

    alpha = selections["alpha"]
    beta = selections["beta"]
    session.select(alpha)
    workspace.set_state(session.project, alpha, session.definition(alpha), session.diagnostics)
    properties._editors[("name",)].setText("Edited Alpha")
    properties._editors[("name",)].editingFinished.emit()

    session.select(beta)
    workspace.set_state(session.project, beta, session.definition(beta), session.diagnostics)
    session.select(alpha)
    workspace.set_state(session.project, alpha, session.definition(alpha), session.diagnostics)
    qapp.processEvents()

    assert workspace.item_preview.name_value.text() == "Edited Alpha"
    assert properties._editors[("name",)].text() == "Edited Alpha"
    assert shiboken6.isValid(properties.status)
