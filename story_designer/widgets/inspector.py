"""Schema-driven editable Inspector for Story Designer definitions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from engine.story_core import ContentKind, DiagnosticSeverity, Diagnostics, StoryProject
from engine.story_core.schema import MISSING

from ..models import (
    DefinitionSelection,
    EditValidationError,
    ProjectSession,
    PropertyDescriptor,
    RemovePropertyCommand,
    SetPropertyCommand,
    SetSceneElementConditionCommand,
)
from .property_editors import AssetPathEditor, PropertyEditorFactory
from .condition_editor import ConditionEditorWidget


@dataclass
class _PropertyRow:
    descriptor: PropertyDescriptor
    editor: QWidget
    authored_value: QLabel
    reset_button: QToolButton
    error: QLabel


class InspectorWidget(QWidget):
    """Generate an editor form from the selected definition's schema."""

    state_changed = Signal()
    scene_geometry_edited = Signal(object, object)

    def __init__(
        self,
        session: ProjectSession | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.session = session
        self.factory = PropertyEditorFactory()
        self._project: StoryProject | None = None
        self._selection: DefinitionSelection | None = None
        self._definition: Any | None = None
        self._diagnostics = Diagnostics()
        self._scene_element = None
        self._rows: dict[tuple[str | int, ...], _PropertyRow] = {}
        self._object_groups: dict[str, QFormLayout] = {}
        self._scene_geometry_fields: dict[str, QSpinBox] = {}

        self.header = QLabel("Inspector")
        self.header.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.header.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.type_value = self._metadata_label()
        self.id_value = self._metadata_label()
        self.source_value = self._metadata_label()
        self.definition_value = self._metadata_label()
        self.validation_value = self._metadata_label()
        metadata = QFormLayout()
        metadata.addRow("Type", self.type_value)
        metadata.addRow("ID", self.id_value)
        metadata.addRow("Source", self.source_value)
        metadata.addRow("Definition", self.definition_value)
        metadata.addRow("Validation", self.validation_value)

        self.revert_button = QPushButton("Revert Selected Definition")
        self.revert_button.clicked.connect(self._revert_selected)

        self.fields_form = QFormLayout()
        self.fields_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.fields_container = QWidget()
        self.fields_layout = QVBoxLayout(self.fields_container)
        self.fields_layout.setContentsMargins(0, 0, 0, 0)
        self.fields_layout.addLayout(self.fields_form)
        self.scene_geometry_box = QGroupBox("Geometry")
        self.scene_geometry_form = QFormLayout(self.scene_geometry_box)
        self.fields_layout.addWidget(self.scene_geometry_box)
        self.scene_geometry_box.hide()
        self.scene_condition_box = QGroupBox("Condition")
        scene_condition_layout = QVBoxLayout(self.scene_condition_box)
        self.scene_condition_editor = ConditionEditorWidget(parent=self.scene_condition_box)
        self.scene_condition_editor.condition_changed.connect(self._scene_condition_changed)
        scene_condition_layout.addWidget(self.scene_condition_editor)
        self.fields_layout.addWidget(self.scene_condition_box)
        self.scene_condition_box.hide()

        self.property_scroll = QScrollArea()
        self.property_scroll.setWidgetResizable(True)
        self.property_scroll.setWidget(self.fields_container)

        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMaximumHeight(110)
        self.summary.setPlaceholderText("Select a definition to inspect it.")

        layout = QVBoxLayout(self)
        layout.addWidget(self.header)
        layout.addLayout(metadata)
        layout.addWidget(self.revert_button)
        layout.addWidget(self.property_scroll, 1)
        layout.addWidget(QLabel("Authored semantic snapshot"))
        layout.addWidget(self.summary)
        self.clear()

    @staticmethod
    def _metadata_label() -> QLabel:
        label = QLabel("—")
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setWordWrap(True)
        return label

    def set_session(self, session: ProjectSession | None) -> None:
        self.session = session
        self._update_header()

    def clear(self) -> None:
        self._project = None
        self._selection = None
        self._definition = None
        self._scene_element = None
        self.header.setText("Inspector")
        self.type_value.setText("—")
        self.id_value.setText("—")
        self.source_value.setText("—")
        self.definition_value.setText("—")
        self.validation_value.setText("—")
        self.summary.clear()
        self.revert_button.setEnabled(False)
        self.property_scroll.setEnabled(True)
        self.scene_geometry_box.hide()
        self.scene_condition_box.hide()
        self._scene_geometry_fields.clear()
        self._clear_form()

    def set_selection(
        self,
        project: StoryProject | None,
        selection: DefinitionSelection | None,
        definition: Any | None,
        diagnostics: Diagnostics,
    ) -> None:
        self._project = project
        self._selection = selection
        self._definition = definition
        self._diagnostics = diagnostics
        self._scene_element = None
        self.property_scroll.setEnabled(True)
        self.scene_geometry_box.hide()
        self.scene_condition_box.hide()
        if project is None or selection is None or definition is None:
            self.clear()
            return

        self.type_value.setText(_display_kind(selection.kind))
        self.id_value.setText(selection.id)
        source = getattr(definition, "source", selection.source)
        self.source_value.setText(_relative_source(project.story_root, source))
        self.definition_value.setText(type(definition).__name__)
        relevant = [item for item in diagnostics if item.source == source]
        if any(item.severity is DiagnosticSeverity.ERROR for item in relevant):
            status = "Error"
        elif any(item.severity is DiagnosticSeverity.WARNING for item in relevant):
            status = "Warning"
        elif relevant:
            status = "Advisory"
        else:
            status = "Valid"
        self.validation_value.setText(status)
        self._update_snapshot()
        self._build_form()
        self._update_header()

    def set_scene_element(self, selection: object, authored: Mapping[str, Any]) -> None:
        """Show one nested scene element and its editable geometry controls."""

        self._scene_element = selection
        self.property_scroll.setEnabled(True)
        self._build_scene_geometry(selection, authored)
        kind = getattr(selection, "kind", "element").replace("_", " ").title()
        identifier = getattr(selection, "id", "")
        self.header.setText(f"Scene {kind}: {identifier}")
        self.type_value.setText(f"Scene {kind}")
        self.id_value.setText(identifier)
        self.definition_value.setText("Authored scene element (read-only preview)")
        self.validation_value.setText("Conditional" if "visible_when" in authored or "conditions" in authored else "Authored")
        self.summary.setPlainText(_compact_mapping(authored))
        if getattr(selection, "kind", "") in {"object", "look_region"}:
            condition = authored.get("visible_when", authored.get("conditions", MISSING))
            self.scene_condition_editor.set_condition(condition, project=self._project)
            self.scene_condition_editor.setEnabled(True)
            self.scene_condition_box.show()

    def _scene_condition_changed(self, value: Any) -> None:
        if self.session is None or self._selection is None or self._scene_element is None:
            return
        try:
            self.session.apply_command(SetSceneElementConditionCommand(self._selection, self._scene_element, value))
        except EditValidationError as exc:
            self.scene_condition_editor.status.setText(exc.message)
            return
        self.summary.setPlainText(_compact_mapping(self._scene_element_mapping()))
        self.validation_value.setText("Conditional" if value is not MISSING else "Authored")
        self.state_changed.emit()

    def _scene_element_mapping(self) -> Mapping[str, Any]:
        if self.session is None or self._selection is None or self._scene_element is None:
            return {}
        mapping = self.session.working_mapping(self._selection) or {}
        kind = getattr(self._scene_element, "kind", "")
        key = "objects" if kind == "object" else "look_regions"
        exploration = mapping.get("exploration")
        collection = exploration.get(key) if isinstance(exploration, Mapping) and key in exploration else mapping.get(key)
        if isinstance(collection, list):
            for value in collection:
                if isinstance(value, Mapping) and value.get("id") == getattr(self._scene_element, "id", None):
                    return value
        return {}

    def _build_scene_geometry(self, selection: object, authored: Mapping[str, Any]) -> None:
        while self.scene_geometry_form.rowCount():
            self.scene_geometry_form.removeRow(0)
        self._scene_geometry_fields.clear()
        kind = getattr(selection, "kind", "")
        if kind == "object":
            raw = authored.get("position", (0, 0))
            values = list(raw) if isinstance(raw, (list, tuple)) and len(raw) == 2 else [0, 0]
            names = ("x", "y")
        elif kind == "look_region":
            raw = authored.get("rect", authored.get("hitbox"))
            look = authored.get("look")
            if raw is None and isinstance(look, Mapping):
                raw = look.get("rect", look.get("hitbox"))
            values = list(raw) if isinstance(raw, (list, tuple)) and len(raw) == 4 else [0, 0, 1, 1]
            names = ("x", "y", "width", "height")
        else:
            self.scene_geometry_form.addRow(QLabel("This element has no graphical geometry editor."))
            self.scene_geometry_box.show()
            return
        for index, name in enumerate(names):
            editor = QSpinBox()
            editor.setRange(-1_000_000, 1_000_000)
            if name in {"width", "height"}:
                editor.setMinimum(1)
            editor.setValue(int(values[index]))
            editor.editingFinished.connect(lambda ref=selection: self._emit_scene_geometry(ref))
            self._scene_geometry_fields[name] = editor
            self.scene_geometry_form.addRow(name.title(), editor)
        self.scene_geometry_box.show()

    def _emit_scene_geometry(self, selection: object) -> None:
        if not self._scene_geometry_fields or selection is not self._scene_element:
            return
        values = tuple(self._scene_geometry_fields[name].value() for name in self._scene_geometry_fields)
        self.scene_geometry_edited.emit(selection, values)

    def clear_scene_element(self) -> None:
        """Return the Inspector to the selected top-level definition."""

        if self._scene_element is None:
            return
        self._scene_element = None
        self.property_scroll.setEnabled(True)
        self.scene_geometry_box.hide()
        self.scene_condition_box.hide()
        self._scene_geometry_fields.clear()
        if self._project is None or self._selection is None or self._definition is None:
            self.clear()
            return
        self.type_value.setText(_display_kind(self._selection.kind))
        self.id_value.setText(self._selection.id)
        source = getattr(self._definition, "source", self._selection.source)
        self.source_value.setText(_relative_source(self._project.story_root, source))
        self.definition_value.setText(type(self._definition).__name__)
        self._update_snapshot()
        self._update_header()

    def _build_form(self) -> None:
        self._clear_form()
        self._rows.clear()
        self._object_groups.clear()
        session = self.session
        selection = self._selection
        project = self._project
        if session is None or project is None or selection is None:
            self.fields_form.addRow(QLabel("Schema editing is unavailable."))
            return
        model = session.property_model(selection)
        if model is None:
            self.fields_form.addRow(QLabel("No schema is available for this definition."))
            return

        descriptors = model.properties(include_nested=True)
        if not descriptors:
            self.fields_form.addRow(QLabel("No schema properties are available."))
            return
        root_types = {
            descriptor.path[0]: descriptor.type_spec.kind
            for descriptor in descriptors
            if len(descriptor.path) == 1 and descriptor.type_spec is not None
        }
        for descriptor in descriptors:
            path = descriptor.path
            if len(path) > 1:
                root = path[0]
                if root_types.get(root) != "object" or not isinstance(root, str):
                    continue
                if descriptor.type_spec is not None and descriptor.type_spec.kind == "object":
                    self._add_nested_read_only(descriptor, root)
                else:
                    self._add_property(descriptor, self._object_groups.get(root))
                continue
            kind = descriptor.type_spec.kind if descriptor.type_spec is not None else None
            if kind == "object":
                self._add_object_group(descriptor)
            else:
                self._add_property(descriptor)

        self._add_unknown_fields(model)

    def _add_object_group(self, descriptor: PropertyDescriptor) -> None:
        group = QGroupBox(descriptor.display_name)
        if descriptor.description:
            group.setToolTip(descriptor.description)
        form = QFormLayout(group)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.fields_layout.addWidget(group)
        self._object_groups[str(descriptor.key)] = form

    def _add_nested_read_only(self, descriptor: PropertyDescriptor, root: str) -> None:
        form = self._object_groups.get(root)
        if form is not None:
            self._add_property(descriptor, form, force_read_only=True)

    def _add_property(
        self,
        descriptor: PropertyDescriptor,
        form: QFormLayout | None = None,
        *,
        force_read_only: bool = False,
    ) -> None:
        editor = self.factory.create(
            descriptor,
            story_root=self._project.story_root if self._project is not None else None,
            project=self._project,
        )
        if force_read_only:
            editor.setEnabled(False)
        self._set_editor_value(editor, descriptor)
        if descriptor.description:
            editor.setToolTip(descriptor.description)

        value_box = QWidget()
        value_layout = QHBoxLayout(value_box)
        value_layout.setContentsMargins(0, 0, 0, 0)
        value_layout.addWidget(editor, 1)
        authored = QLabel()
        authored.setStyleSheet("color: palette(mid); font-size: smaller;")
        value_layout.addWidget(authored)
        reset = QToolButton()
        reset.setText("↶")
        reset.setToolTip("Remove authored value and use the schema default.")
        reset.clicked.connect(lambda checked=False, path=descriptor.path: self._remove_property(path))
        value_layout.addWidget(reset)
        error = QLabel()
        error.setStyleSheet("color: #b00020;")
        error.setWordWrap(True)
        error.setVisible(False)
        value_layout.addWidget(error)

        row = _PropertyRow(descriptor, editor, authored, reset, error)
        self._rows[descriptor.path] = row
        self._update_row_state(row, descriptor)
        if hasattr(editor, "value_edited") and not force_read_only:
            editor.value_edited.connect(
                lambda value, path=descriptor.path, widget=editor: self._set_property(path, value, widget)
            )
        target_form = form or self.fields_form
        label = QLabel(descriptor.display_name)
        label.setToolTip(descriptor.description)
        target_form.addRow(label, value_box)

    def _add_unknown_fields(self, model: Any) -> None:
        working = model.working_copy.to_mapping()
        schema = model.schema
        known = {key for field in schema.fields for key in field.serialized_keys} if schema is not None else set()
        unknown = {key: value for key, value in working.items() if key not in known}
        if not unknown:
            return
        box = QGroupBox("Unknown / Legacy Fields")
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(_compact_mapping(unknown))
        box_layout = QVBoxLayout(box)
        box_layout.addWidget(QLabel("Preserved, but not editable by the generic Inspector."))
        box_layout.addWidget(text)
        self.fields_layout.addWidget(box)

    def _set_property(self, path: tuple[str | int, ...], value: Any, editor: QWidget) -> None:
        session = self.session
        selection = self._selection
        if session is None or selection is None:
            return
        descriptor = self._descriptor(path)
        if descriptor is None:
            return
        type_kind = descriptor.type_spec.kind if descriptor.type_spec is not None else None
        if (value is MISSING or value is None or (value == "" and type_kind == "asset")) and not descriptor.required:
            command: Any = RemovePropertyCommand(selection, path)
        else:
            command = SetPropertyCommand(selection, path, value)
        try:
            session.apply_command(command)
        except EditValidationError as exc:
            self._show_error(path, exc.message)
            latest = self._descriptor(path)
            if latest is not None:
                self._set_editor_value(editor, latest)
            return
        self._clear_error(path)
        latest = self._descriptor(path)
        if latest is not None:
            row = self._rows.get(path)
            if row is not None:
                row.descriptor = latest
                self._update_row_state(row, latest)
        self._update_snapshot()
        self._update_header()
        self.state_changed.emit()

    def _remove_property(self, path: tuple[str | int, ...]) -> None:
        session = self.session
        selection = self._selection
        if session is None or selection is None:
            return
        try:
            session.apply_command(RemovePropertyCommand(selection, path))
        except EditValidationError as exc:
            self._show_error(path, exc.message)
            return
        self._update_snapshot()
        self._build_form()
        self._update_header()
        self.state_changed.emit()

    def _revert_selected(self) -> None:
        if self.session is None or self._selection is None:
            return
        if not self.session.revert_definition(self._selection):
            return
        self._update_snapshot()
        self._build_form()
        self._update_header()
        self.state_changed.emit()

    def _descriptor(self, path: tuple[str | int, ...]) -> PropertyDescriptor | None:
        if self.session is None or self._selection is None:
            return None
        model = self.session.property_model(self._selection)
        if model is None:
            return None
        try:
            return model.descriptor(path)
        except KeyError:
            return None

    @staticmethod
    def _set_editor_value(editor: QWidget, descriptor: PropertyDescriptor) -> None:
        value = descriptor.effective_value
        blocker = QSignalBlocker(editor)
        if hasattr(editor, "set_initializing"):
            editor.set_initializing(True)
        try:
            kind = descriptor.type_spec.kind if descriptor.type_spec is not None else None
            if kind == "string" and hasattr(editor, "setText"):
                editor.setText("" if value is MISSING else str(value))
            elif kind == "multiline_string" and isinstance(editor, QPlainTextEdit):
                editor.setPlainText("" if value is MISSING else str(value))
            elif kind == "integer" and hasattr(editor, "setValue"):
                editor.setValue(0 if value is MISSING else int(value))
            elif kind in {"float", "number"} and hasattr(editor, "setValue"):
                editor.setValue(0.0 if value is MISSING else float(value))
            elif kind == "boolean" and hasattr(editor, "setChecked"):
                editor.setChecked(False if value is MISSING else bool(value))
            elif kind in {"enum", "reference"} and hasattr(editor, "findData"):
                index = editor.findData(None if value is MISSING else value)
                editor.setCurrentIndex(index if index >= 0 else -1)
            elif kind == "asset" and isinstance(editor, AssetPathEditor):
                editor.setText("" if value is MISSING else str(value))
            elif kind == "condition" and isinstance(editor, ConditionEditorWidget):
                editor.set_condition(value, project=None)
        finally:
            if hasattr(editor, "set_initializing"):
                editor.set_initializing(False)
            del blocker

    @staticmethod
    def _update_row_state(row: _PropertyRow, descriptor: PropertyDescriptor) -> None:
        if descriptor.is_authored:
            row.authored_value.setText("authored")
        elif descriptor.has_default:
            row.authored_value.setText("default")
        else:
            row.authored_value.setText("")
        removable = descriptor.is_authored and not descriptor.required and descriptor.is_editable
        row.reset_button.setVisible(removable)
        row.reset_button.setEnabled(removable)

    def _show_error(self, path: tuple[str | int, ...], message: str) -> None:
        row = self._rows.get(path)
        if row is None:
            return
        row.error.setText(f"⚠ {message}")
        row.error.setToolTip(message)
        row.error.setVisible(True)

    def _clear_error(self, path: tuple[str | int, ...]) -> None:
        row = self._rows.get(path)
        if row is None:
            return
        row.error.clear()
        row.error.setVisible(False)

    def _update_snapshot(self) -> None:
        if self.session is None or self._selection is None:
            self.summary.clear()
            return
        mapping = self.session.working_mapping(self._selection)
        self.summary.setPlainText(_compact_mapping(mapping or {}))

    def _update_header(self) -> None:
        if self._selection is None:
            self.header.setText("Inspector")
            self.revert_button.setEnabled(False)
            return
        dirty = self.session is not None and self.session.is_definition_dirty(self._selection)
        if self._scene_element is not None:
            kind = getattr(self._scene_element, "kind", "element").replace("_", " ").title()
            identifier = getattr(self._scene_element, "id", "")
            self.header.setText(f"Scene {kind}: {identifier}{' *' if dirty else ''}")
        else:
            self.header.setText(f"{_display_kind(self._selection.kind)}: {self._selection.id}{' *' if dirty else ''}")
        self.revert_button.setEnabled(bool(dirty))

    def _clear_form(self) -> None:
        self._rows.clear()
        self._object_groups.clear()
        while self.fields_form.rowCount():
            self.fields_form.removeRow(0)
        while self.fields_layout.count() > 1:
            item = self.fields_layout.takeAt(1)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()


def _display_kind(kind: ContentKind) -> str:
    return {
        ContentKind.EVENT_POOL: "Event Pool",
        ContentKind.MOVE: "Combat Move",
        ContentKind.AUDIO: "Audio Configuration",
    }.get(kind, kind.value.replace("_", " ").title())


def _relative_source(root: Path, source: Path | None) -> str:
    if source is None:
        return "<project>"
    try:
        return source.relative_to(root).as_posix()
    except ValueError:
        return str(source)


def _compact_mapping(value: Any) -> str:
    if not isinstance(value, Mapping):
        return repr(value)
    lines = []
    for key, item in value.items():
        rendered = repr(item)
        if len(rendered) > 180:
            rendered = rendered[:177] + "..."
        lines.append(f"{key}: {rendered}")
    return "\n".join(lines) or "(empty authored mapping)"
