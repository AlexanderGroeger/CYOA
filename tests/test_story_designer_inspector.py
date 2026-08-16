from __future__ import annotations

import os
from pathlib import Path

import pytest

from engine.story_core import ContentKind, FieldSpec, Schema, TypeSpec
from story_core_fixture import write_fixture_story
from story_designer.models import DefinitionSelection, ProjectSession, PropertyDescriptor

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QDoubleSpinBox,
        QLineEdit,
        QPlainTextEdit,
        QSpinBox,
    )
    from story_designer.widgets import InspectorWidget
    from story_designer.widgets.property_editors import AssetPathEditor, PropertyEditorFactory, ReferenceComboBox
    from shiboken6 import isValid
except ImportError:  # pragma: no cover - retained for minimal Core-only environments
    QApplication = None  # type: ignore[assignment]


@pytest.fixture(scope="module")
def qapp():
    if QApplication is None:
        pytest.skip("PySide6 is not installed")
    return QApplication.instance() or QApplication([])


def _session(tmp_path: Path) -> ProjectSession:
    story_root, shared_root = write_fixture_story(tmp_path)
    return ProjectSession.from_path(story_root, shared_root)


def _selection(session: ProjectSession, kind: ContentKind, identifier: str) -> DefinitionSelection:
    assert session.project is not None and session.project.index is not None
    entry = session.project.index.entry(kind, identifier)
    assert entry is not None
    return DefinitionSelection(kind, identifier, entry.source)


def _descriptor(kind: str, *, minimum=None, maximum=None, allowed=(), effective=None) -> PropertyDescriptor:
    selection = DefinitionSelection(ContentKind.SCENE, "scene")
    return PropertyDescriptor(
        selection=selection,
        path=("value",),
        key="value",
        display_name="Value",
        description="Test value",
        type_spec=TypeSpec(kind) if kind not in {"enum", "reference", "asset"} else (
            TypeSpec.enum(allowed) if kind == "enum" else
            TypeSpec.reference("scene") if kind == "reference" else
            TypeSpec.asset("items")
        ),
        required=False,
        effective_value=effective if effective is not None else (allowed[0] if allowed else None),
        minimum=minimum,
        maximum=maximum,
        allowed_values=tuple(allowed),
        reference_candidates=("intro", "ending") if kind == "reference" else (),
        asset_kind="items" if kind == "asset" else None,
    )


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_factory_maps_common_schema_types_to_native_widgets(qapp) -> None:
    factory = PropertyEditorFactory()
    assert isinstance(factory.create(_descriptor("string")), QLineEdit)
    assert isinstance(factory.create(_descriptor("multiline_string")), QPlainTextEdit)
    assert isinstance(factory.create(_descriptor("integer", minimum=0, maximum=9)), QSpinBox)
    assert isinstance(factory.create(_descriptor("float", minimum=0, maximum=1)), QDoubleSpinBox)
    assert isinstance(factory.create(_descriptor("boolean")), QCheckBox)
    assert isinstance(factory.create(_descriptor("enum", allowed=("easy", "hard"))), QComboBox)
    assert isinstance(factory.create(_descriptor("reference")), ReferenceComboBox)
    assert isinstance(factory.create(_descriptor("asset")), AssetPathEditor)


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_factory_respects_numeric_bounds_and_enum_data(qapp) -> None:
    factory = PropertyEditorFactory()
    integer = factory.create(_descriptor("integer", minimum=2, maximum=7))
    floating = factory.create(_descriptor("float", minimum=0.25, maximum=0.75))
    enum = factory.create(_descriptor("enum", allowed=("easy", "hard")))
    assert integer.minimum() == 2 and integer.maximum() == 7
    assert floating.minimum() == pytest.approx(0.25)
    assert floating.maximum() == pytest.approx(0.75)
    assert [enum.itemData(index) for index in range(enum.count())] == ["easy", "hard"]


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_inspector_initialization_does_not_author_defaults_and_edit_routes_command(qapp, tmp_path: Path) -> None:
    session = _session(tmp_path)
    selection = _selection(session, ContentKind.SCENE, "intro")
    session.select(selection)
    inspector = InspectorWidget(session)
    inspector.set_selection(session.project, selection, session.definition(), session.diagnostics)

    assert not session.is_dirty
    checkpoint = inspector._rows[("checkpoint",)]
    assert checkpoint.authored_value.text() == "default"
    assert "checkpoint" not in session.working_mapping(selection)

    text = inspector._rows[("text",)].editor
    assert isinstance(text, QPlainTextEdit)
    text.value_edited.emit("Edited through Inspector")
    assert session.working_mapping(selection)["text"] == "Edited through Inspector"
    assert session.project.scene("intro").text == "Welcome."
    assert inspector.header.text().endswith("*")


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_invalid_edit_is_inline_and_does_not_mutate_working_or_core_state(qapp, tmp_path: Path) -> None:
    session = _session(tmp_path)
    selection = _selection(session, ContentKind.MOVE, "intro")
    session.select(selection)
    inspector = InspectorWidget(session)
    inspector.set_selection(session.project, selection, session.definition(), session.diagnostics)

    row = inspector._rows[("initial_level",)]
    row.editor.value_edited.emit(-1)
    assert "initial_level" not in session.working_mapping(selection)
    assert inspector._rows[("initial_level",)].descriptor.effective_value == 1
    assert session.project.move("intro").to_mapping().get("initial_level") is None
    assert not row.error.isHidden()
    assert "at least" in row.error.toolTip()


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_optional_reset_returns_to_unauthored_default_and_selection_persists(qapp, tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = _selection(session, ContentKind.SCENE, "intro")
    item = _selection(session, ContentKind.ITEM, "intro")
    session.select(scene)
    inspector = InspectorWidget(session)
    inspector.set_selection(session.project, scene, session.definition(), session.diagnostics)

    checkpoint = inspector._rows[("checkpoint",)]
    checkpoint.editor.value_edited.emit(True)
    assert session.working_mapping(scene)["checkpoint"] is True
    checkpoint = inspector._rows[("checkpoint",)]
    checkpoint.reset_button.click()
    assert "checkpoint" not in session.working_mapping(scene)
    assert not session.is_dirty

    inspector._rows[("text",)].editor.value_edited.emit("Pending scene edit")
    session.select(item)
    inspector.set_selection(session.project, item, session.definition(), session.diagnostics)
    session.select(scene)
    inspector.set_selection(session.project, scene, session.definition(), session.diagnostics)
    assert inspector._rows[("text",)].descriptor.effective_value == "Pending scene edit"


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_unresolved_reference_remains_representable(qapp) -> None:
    factory = PropertyEditorFactory()
    descriptor = _descriptor("reference", effective="missing_scene")
    editor = factory.create(descriptor)
    assert editor.findData("missing_scene") >= 0
    index = editor.findData("missing_scene")
    assert "⚠" in editor.itemText(index)


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_nested_object_scalar_uses_full_path_and_routes_through_session(qapp, tmp_path: Path) -> None:
    session = _session(tmp_path)
    assert session.project is not None and session.project.schema_registry is not None
    scene_schema = session.project.schema_registry.require("scene")
    session.project.schema_registry.register(
        Schema("inspector_nested", (FieldSpec("value", type=TypeSpec.integer(), default=0),)),
        replace=True,
    )
    session.project.schema_registry.register(
        Schema(
            "scene",
            (*scene_schema.fields, FieldSpec("inspector_settings", type=TypeSpec.object("inspector_nested"), default={})),
        ),
        replace=True,
    )
    selection = _selection(session, ContentKind.SCENE, "intro")
    session.select(selection)
    inspector = InspectorWidget(session)
    inspector.set_selection(session.project, selection, session.definition(), session.diagnostics)

    assert ("inspector_settings", "value") in inspector._rows
    inspector._rows[("inspector_settings", "value")].editor.value_edited.emit(4)
    assert session.working_mapping(selection)["inspector_settings"] == {"value": 4}


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_persistent_inspector_sections_survive_repeated_selection_refreshes(qapp, tmp_path: Path) -> None:
    session = _session(tmp_path)
    inspector = InspectorWidget(session)
    geometry_box = inspector.scene_geometry_box
    condition_box = inspector.scene_condition_box
    selections = (
        _selection(session, ContentKind.SCENE, "intro"),
        _selection(session, ContentKind.ITEM, "intro"),
        _selection(session, ContentKind.BATTLE, "intro"),
        _selection(session, ContentKind.MOVE, "intro"),
    )

    for _ in range(20):
        for selection in selections:
            session.select(selection)
            inspector.set_selection(session.project, selection, session.definition(), session.diagnostics)
            assert isValid(geometry_box)
            assert isValid(condition_box)
            geometry_box.hide()
            geometry_box.show()
            condition_box.hide()
            condition_box.show()

    qapp.processEvents()
    assert isValid(geometry_box)
    assert isValid(condition_box)
    geometry_box.hide()
    geometry_box.show()


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_scene_element_dynamic_controls_are_rebuilt_inside_persistent_geometry_box(
    qapp, tmp_path: Path
) -> None:
    from story_designer.models import SceneElementSelection

    session = _session(tmp_path)
    selection = _selection(session, ContentKind.SCENE, "intro")
    session.select(selection)
    inspector = InspectorWidget(session)
    inspector.set_selection(session.project, selection, session.definition(), session.diagnostics)
    geometry_box = inspector.scene_geometry_box

    object_ref = SceneElementSelection("intro", "object", "lamp")
    look_ref = SceneElementSelection("intro", "look_region", "desk")
    inspector.set_scene_element(object_ref, {"id": "lamp", "position": [10, 20], "sprite": "lamp.png"})
    first_editor = inspector._scene_geometry_fields["x"]
    inspector.set_scene_element(look_ref, {"id": "desk", "rect": [1, 2, 30, 20]})
    qapp.processEvents()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(first_editor)
    assert isValid(geometry_box)
    assert inspector._scene_geometry_fields["x"] is not first_editor
    inspector.clear_scene_element()
    qapp.processEvents()
    assert isValid(geometry_box)
    geometry_box.hide()
    geometry_box.show()


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_look_region_context_authors_interaction_event_actions_and_safe_rename(qapp, tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    (story_root / "scenes" / "intro.yaml").write_text(
        "id: intro\n"
        "exploration:\n"
        "  look_regions:\n"
        "    - id: desk\n"
        "      rect: [1, 2, 30, 20]\n"
        "      interaction: inspect\n"
        "      event: examine_desk\n"
        "      mystery_field: preserve-me\n"
        "  look_events:\n"
        "    examine_desk:\n"
        "      actions:\n"
        "        - type: dialog\n"
        "          dialog: desk_sequence\n"
        "  dialogue_sequences:\n"
        "    desk_sequence: Desk.\n",
        encoding="utf-8",
    )
    session = ProjectSession.from_path(story_root, shared_root)
    selection = _selection(session, ContentKind.SCENE, "intro")
    session.select(selection)
    inspector = InspectorWidget(session)
    inspector.set_selection(session.project, selection, session.definition(), session.diagnostics)
    from story_designer.models import SceneElementSelection
    ref = SceneElementSelection("intro", "look_region", "desk")
    inspector.set_scene_element(ref, session.working_mapping(selection)["exploration"]["look_regions"][0])

    assert inspector.look_region_identity.text() == "desk"
    assert inspector.look_region_interaction_combo.currentData() == "inspect"
    assert inspector.look_region_event_combo.currentData() == "examine_desk"
    assert inspector.look_region_actions.count() == 1
    assert not inspector.look_region_unknown_box.isHidden()

    inspector.look_region_interaction_combo.setCurrentIndex(inspector.look_region_interaction_combo.findData("action"))
    assert session.working_mapping(selection)["exploration"]["look_regions"][0]["interaction"] == "action"

    inspector.look_region_action_type.setCurrentIndex(inspector.look_region_action_type.findData("set_flag"))
    inspector.look_region_add_action.click()
    flag_editor = inspector._look_region_action_fields["flag"]
    assert isinstance(flag_editor, QLineEdit)
    flag_editor.setText("opened")
    flag_editor.editingFinished.emit()
    actions = session.working_mapping(selection)["exploration"]["look_events"]["examine_desk"]["actions"]
    assert actions[-1]["type"] == "set_flag"
    assert actions[-1]["flag"] == "opened"
    assert session.working_mapping(selection)["exploration"]["look_regions"][0]["mystery_field"] == "preserve-me"

    from story_designer.models import RenameSceneElementCommand
    session.apply_command(RenameSceneElementCommand(selection, ref, "desk_new"))
    assert session.working_mapping(selection)["exploration"]["look_regions"][0]["id"] == "desk_new"
    session.undo()
    assert session.working_mapping(selection)["exploration"]["look_regions"][0]["id"] == "desk"
    session.redo()
    assert session.working_mapping(selection)["exploration"]["look_regions"][0]["id"] == "desk_new"
    assert session.save_all()
    reloaded = ProjectSession.from_path(story_root, shared_root)
    reloaded_selection = _selection(reloaded, ContentKind.SCENE, "intro")
    reloaded_region = reloaded.working_mapping(reloaded_selection)["exploration"]["look_regions"][0]
    assert reloaded_region["id"] == "desk_new"
    assert reloaded_region["interaction"] == "action"
    assert reloaded.working_mapping(reloaded_selection)["exploration"]["look_events"]["examine_desk"]["actions"][-1]["flag"] == "opened"
