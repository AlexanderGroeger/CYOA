from __future__ import annotations

import os
from pathlib import Path

import pytest

from engine.story_core import ContentKind, FieldSpec, Schema, TypeSpec
from story_core_fixture import write_fixture_story
from story_designer.models import DefinitionSelection, ProjectSession, PropertyDescriptor

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
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
