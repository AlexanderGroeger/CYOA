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
    QComboBox,
    QCheckBox,
    QDoubleSpinBox,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from engine.story_core import (
    ActionScope,
    ContentKind,
    DiagnosticSeverity,
    Diagnostics,
    StoryProject,
    action_editor_spec,
    action_editor_specs,
    minimal_authored_action,
)
from engine.story_core.schema import MISSING

from ..models import (
    DefinitionSelection,
    EditValidationError,
    ProjectSession,
    PropertyDescriptor,
    RemovePropertyCommand,
    SetPropertyCommand,
    SetSceneElementConditionCommand,
    SetSceneElementPropertyCommand,
    RenameSceneElementCommand,
    CreateLookEventCommand,
    InsertDialogueActionCommand,
    RemoveDialogueActionCommand,
    SetDialogueActionParameterCommand,
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
    scene_element_renamed = Signal(object, str)
    open_dialogue_sequence = Signal(str)

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
        self._scene_asset_fields: dict[str, AssetPathEditor] = {}
        self._look_region_action_fields: dict[str, QWidget] = {}
        self._look_region_event_id: str | None = None
        self._look_region_actions_path: tuple[str | int, ...] | None = None
        self._updating_scene_geometry = False
        self._updating_scene_element = False

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
        self.dynamic_fields_container = QWidget()
        self.dynamic_fields_layout = QVBoxLayout(self.dynamic_fields_container)
        self.dynamic_fields_layout.setContentsMargins(0, 0, 0, 0)
        self.dynamic_fields_layout.addLayout(self.fields_form)
        self.fields_layout.addWidget(self.dynamic_fields_container)
        self._retired_dynamic_controls = QWidget(self.dynamic_fields_container)
        self._retired_dynamic_controls.hide()
        self.scene_geometry_box = QGroupBox("Geometry")
        self.scene_geometry_form = QFormLayout(self.scene_geometry_box)
        self._retired_scene_controls = QWidget(self.scene_geometry_box)
        self._retired_scene_controls.hide()
        self.scene_geometry_box.hide()
        self.scene_condition_box = QGroupBox("Condition")
        scene_condition_layout = QVBoxLayout(self.scene_condition_box)
        self.scene_condition_editor = ConditionEditorWidget(parent=self.scene_condition_box)
        self.scene_condition_editor.condition_changed.connect(self._scene_condition_changed)
        scene_condition_layout.addWidget(self.scene_condition_editor)
        self.scene_condition_box.hide()

        self.look_region_identity_box = QGroupBox("Identity")
        identity_form = QFormLayout(self.look_region_identity_box)
        self.look_region_identity = self._metadata_label()
        self.look_region_identity.setText("Selected Look Region")
        self.look_region_rename_button = QPushButton("Rename…")
        self.look_region_rename_button.clicked.connect(self._rename_look_region)
        identity_action = QWidget(self.look_region_identity_box)
        identity_layout = QHBoxLayout(identity_action)
        identity_layout.setContentsMargins(0, 0, 0, 0)
        identity_layout.addWidget(self.look_region_identity, 1)
        identity_layout.addWidget(self.look_region_rename_button)
        identity_form.addRow("ID", identity_action)
        self.look_region_identity_box.hide()

        self.look_region_interaction_box = QGroupBox("Interaction")
        interaction_form = QFormLayout(self.look_region_interaction_box)
        self.look_region_interaction_combo = QComboBox()
        self.look_region_interaction_combo.addItem("Inspect", "inspect")
        self.look_region_interaction_combo.addItem("Action", "action")
        self.look_region_interaction_combo.currentIndexChanged.connect(self._look_region_interaction_changed)
        interaction_form.addRow("Type", self.look_region_interaction_combo)
        self.look_region_event_combo = QComboBox()
        self.look_region_event_combo.setEditable(True)
        self.look_region_event_combo.lineEdit().editingFinished.connect(self._look_region_event_text_finished)
        self.look_region_event_combo.currentIndexChanged.connect(self._look_region_event_changed)
        interaction_form.addRow("Look Event", self.look_region_event_combo)
        self.look_region_new_event = QPushButton("New Event…")
        self.look_region_new_event.clicked.connect(self._create_look_region_event)
        interaction_form.addRow(self.look_region_new_event)
        self.look_region_priority = QSpinBox()
        self.look_region_priority.setRange(-2_147_483_648, 2_147_483_647)
        self.look_region_priority.valueChanged.connect(self._look_region_priority_changed)
        interaction_form.addRow("Priority", self.look_region_priority)
        self.look_region_open_dialogue = QPushButton("Open Dialogue")
        self.look_region_open_dialogue.clicked.connect(self._open_look_region_dialogue)
        interaction_form.addRow(self.look_region_open_dialogue)
        self.look_region_interaction_box.hide()

        self.look_region_behavior_box = QGroupBox("Effect / Behavior")
        behavior_layout = QVBoxLayout(self.look_region_behavior_box)
        self.look_region_actions = QListWidget()
        self.look_region_actions.setMinimumHeight(80)
        self.look_region_actions.currentRowChanged.connect(self._look_region_action_row_changed)
        behavior_layout.addWidget(self.look_region_actions)
        action_buttons = QHBoxLayout()
        self.look_region_add_action = QPushButton("+ Add Action")
        self.look_region_remove_action = QPushButton("Remove Action")
        self.look_region_add_action.clicked.connect(self._add_look_region_action)
        self.look_region_remove_action.clicked.connect(self._remove_look_region_action)
        action_buttons.addWidget(self.look_region_add_action)
        action_buttons.addWidget(self.look_region_remove_action)
        behavior_layout.addLayout(action_buttons)
        self.look_region_action_type = QComboBox()
        for spec in action_editor_specs(ActionScope.EXPLORATION):
            self.look_region_action_type.addItem(spec.display_name, spec.type)
        behavior_layout.addWidget(self.look_region_action_type)
        self.look_region_action_fields_widget = QWidget(self.look_region_behavior_box)
        self.look_region_action_fields = QFormLayout(self.look_region_action_fields_widget)
        behavior_layout.addWidget(self.look_region_action_fields_widget)
        self.look_region_action_status = QLabel()
        self.look_region_action_status.setWordWrap(True)
        self.look_region_action_status.setStyleSheet("color: #b45309;")
        behavior_layout.addWidget(self.look_region_action_status)
        self.look_region_behavior_box.hide()

        self.property_scroll = QScrollArea(self)
        self.property_scroll.setWidgetResizable(True)
        self.property_scroll.setWidget(self.fields_container)

        # Definition properties and scene-element properties are separate
        # contexts.  The latter is intentionally a real page so selecting a
        # Look Region does not make its controls look like Scene fields.
        self.context_tabs = QTabWidget()
        self.scene_context_page = QWidget()
        scene_context_layout = QVBoxLayout(self.scene_context_page)
        scene_context_layout.setContentsMargins(0, 0, 0, 0)
        scene_context_layout.addWidget(self.revert_button)
        scene_context_layout.addWidget(self.property_scroll)

        self.look_region_context_page = QWidget()
        look_context_layout = QVBoxLayout(self.look_region_context_page)
        look_context_layout.setContentsMargins(0, 0, 0, 0)
        look_context_layout.addWidget(self.look_region_identity_box)
        look_context_layout.addWidget(self.look_region_interaction_box)
        look_context_layout.addWidget(self.scene_geometry_box)
        look_context_layout.addWidget(self.scene_condition_box)
        look_context_layout.addWidget(self.look_region_behavior_box)
        self.look_region_unknown_box = QGroupBox("Unknown / Legacy Fields")
        self.look_region_unknown_text = QPlainTextEdit()
        self.look_region_unknown_text.setReadOnly(True)
        self.look_region_unknown_box_layout = QVBoxLayout(self.look_region_unknown_box)
        self.look_region_unknown_box_layout.addWidget(QLabel("Preserved, but not interpreted by this page."))
        self.look_region_unknown_box_layout.addWidget(self.look_region_unknown_text)
        self.look_region_unknown_box.hide()
        look_context_layout.addWidget(self.look_region_unknown_box)
        look_context_layout.addStretch(1)
        self.context_tabs.addTab(self.scene_context_page, "Scene")
        self.context_tabs.addTab(self.look_region_context_page, "Look Region")
        self._set_look_region_context_visible(False)

        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMaximumHeight(110)
        self.summary.setPlaceholderText("Select a definition to inspect it.")

        layout = QVBoxLayout(self)
        layout.addWidget(self.header)
        layout.addLayout(metadata)
        layout.addWidget(self.context_tabs, 1)
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
        self.look_region_identity.setText("Selected Look Region")
        self.revert_button.setEnabled(False)
        self.property_scroll.setEnabled(True)
        self.scene_geometry_box.hide()
        self.scene_condition_box.hide()
        self.look_region_identity_box.hide()
        self.look_region_interaction_box.hide()
        self.look_region_behavior_box.hide()
        self.look_region_unknown_box.hide()
        self._clear_scene_geometry_form()
        self._scene_geometry_fields.clear()
        self._scene_asset_fields.clear()
        self._set_look_region_context_visible(False)
        self._clear_dynamic_property_editors()

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
        self._set_look_region_context_visible(False)
        self.context_tabs.setCurrentWidget(self.scene_context_page)
        self.scene_geometry_box.hide()
        self.scene_condition_box.hide()
        self.look_region_identity_box.hide()
        self.look_region_interaction_box.hide()
        self.look_region_behavior_box.hide()
        self.look_region_unknown_box.hide()
        self._clear_scene_geometry_form()
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
        kind = getattr(selection, "kind", "element")
        if kind == "look_region":
            self._set_look_region_context_visible(True)
            self.context_tabs.setCurrentWidget(self.look_region_context_page)
        else:
            self._set_look_region_context_visible(False)
            self.context_tabs.setCurrentWidget(self.scene_context_page)
            self.look_region_identity_box.hide()
            self.look_region_interaction_box.hide()
            self.look_region_behavior_box.hide()
            self.look_region_unknown_box.hide()
        self._build_scene_geometry(selection, authored)
        self._build_scene_asset(selection, authored)
        display_kind = kind.replace("_", " ").title()
        identifier = getattr(selection, "id", "")
        self.header.setText(f"Scene {display_kind}: {identifier}")
        self.type_value.setText(f"Scene {display_kind}")
        self.id_value.setText(identifier)
        self.definition_value.setText("Authored scene element (read-only preview)")
        self.validation_value.setText("Conditional" if "visible_when" in authored or "conditions" in authored else "Authored")
        self.summary.setPlainText(_compact_mapping(authored))
        self.look_region_identity.setText(identifier if kind == "look_region" else "Selected Look Region")
        if kind == "look_region":
            self._build_look_region_context(authored)
        if getattr(selection, "kind", "") in {"object", "look_region"}:
            condition = authored.get("visible_when", authored.get("conditions", MISSING))
            self._updating_scene_element = True
            try:
                self.scene_condition_editor.set_condition(condition, project=self._project)
            finally:
                self._updating_scene_element = False
            self.scene_condition_editor.setEnabled(True)
            self.scene_condition_box.show()

    def _scene_condition_changed(self, value: Any) -> None:
        if self._updating_scene_element or self.session is None or self._selection is None or self._scene_element is None:
            return
        try:
            self.session.apply_command(SetSceneElementConditionCommand(self._selection, self._scene_element, value))
        except EditValidationError as exc:
            self.scene_condition_editor.status.setText(exc.message)
            return
        self.summary.setPlainText(_compact_mapping(self._scene_element_mapping()))
        self.validation_value.setText("Conditional" if value is not MISSING else "Authored")
        self.state_changed.emit()

    def _build_look_region_context(self, authored: Mapping[str, Any]) -> None:
        """Populate the schema-faithful controls for one selected region."""

        look = authored.get("look") if isinstance(authored.get("look"), Mapping) else authored
        interaction = look.get("interaction", authored.get("interaction", "inspect"))
        event_id = look.get("event", authored.get("event", ""))
        priority = look.get("priority", authored.get("priority", 0))
        self._updating_scene_element = True
        try:
            index = self.look_region_interaction_combo.findData(interaction)
            self.look_region_interaction_combo.setCurrentIndex(index if index >= 0 else 0)
            self._populate_look_event_combo(str(event_id) if isinstance(event_id, str) else "")
            self.look_region_priority.setValue(int(priority) if isinstance(priority, int) and not isinstance(priority, bool) else 0)
        finally:
            self._updating_scene_element = False
        self.look_region_identity_box.show()
        self.look_region_interaction_box.show()
        self.look_region_rename_button.setEnabled(self.session is not None)
        self.look_region_new_event.setEnabled(self.session is not None)
        self._render_look_region_actions()
        self.look_region_open_dialogue.setEnabled(self._look_region_dialogue_reference() is not None)
        known = {"id", "rect", "hitbox", "interaction", "event", "priority", "visible_when", "conditions", "states", "z"}
        unknown = {key: value for key, value in authored.items() if key not in known}
        nested = authored.get("look")
        if isinstance(nested, Mapping):
            unknown["look"] = {key: value for key, value in nested.items() if key not in {"rect", "hitbox", "interaction", "event", "priority", "states"}}
            if not unknown["look"]:
                unknown.pop("look")
        if unknown:
            self.look_region_unknown_text.setPlainText(_compact_mapping(unknown))
            self.look_region_unknown_box.show()
        else:
            self.look_region_unknown_box.hide()

    def _populate_look_event_combo(self, current: str) -> None:
        self.look_region_event_combo.blockSignals(True)
        try:
            self.look_region_event_combo.clear()
            self.look_region_event_combo.addItem("(unresolved)", "")
            for event_id in self._look_event_ids():
                self.look_region_event_combo.addItem(event_id, event_id)
            if current and self.look_region_event_combo.findData(current) < 0:
                self.look_region_event_combo.addItem(f"{current} ⚠", current)
            index = self.look_region_event_combo.findData(current)
            self.look_region_event_combo.setCurrentIndex(index if index >= 0 else 0)
            self.look_region_event_combo.setEditText(current)
        finally:
            self.look_region_event_combo.blockSignals(False)

    def _look_event_ids(self) -> tuple[str, ...]:
        mapping = self.session.working_mapping(self._selection) if self.session is not None and self._selection is not None else {}
        sources = []
        if isinstance(mapping, Mapping):
            exploration = mapping.get("exploration")
            if isinstance(exploration, Mapping):
                sources.append(exploration)
            sources.append(mapping)
        result: list[str] = []
        for source in sources:
            events = source.get("look_events")
            if isinstance(events, Mapping):
                result.extend(str(key) for key in events)
        return tuple(dict.fromkeys(result))

    def _look_region_interaction_changed(self, _index: int) -> None:
        if self._updating_scene_element or self._scene_element is None or getattr(self._scene_element, "kind", "") != "look_region":
            return
        self._set_look_region_property("interaction", self.look_region_interaction_combo.currentData())

    def _look_region_event_changed(self, _index: int) -> None:
        if self._updating_scene_element or self._scene_element is None or getattr(self._scene_element, "kind", "") != "look_region":
            return
        value = self.look_region_event_combo.currentData()
        if not isinstance(value, str):
            value = self.look_region_event_combo.currentText().strip()
        self._set_look_region_property("event", value)

    def _look_region_event_text_finished(self) -> None:
        self._look_region_event_changed(self.look_region_event_combo.currentIndex())

    def _look_region_priority_changed(self, value: int) -> None:
        if self._updating_scene_element or self._scene_element is None:
            return
        self._set_look_region_property("priority", int(value))

    def _create_look_region_event(self) -> None:
        if self.session is None or self._selection is None or self._scene_element is None:
            return
        event_id, accepted = QInputDialog.getText(
            self, "New Look Event", "Event ID:", QLineEdit.EchoMode.Normal,
            f"{getattr(self._scene_element, 'id', 'look')}_event",
        )
        if not accepted:
            return
        try:
            self.session.apply_command(CreateLookEventCommand(self._selection, self._scene_element, event_id))
        except EditValidationError as exc:
            self.look_region_action_status.setText(exc.message)
            return
        self._refresh_look_region_context()
        self.state_changed.emit()

    def _set_look_region_property(self, key: str, value: Any) -> None:
        if self.session is None or self._selection is None or self._scene_element is None:
            return
        try:
            self.session.apply_command(SetSceneElementPropertyCommand(self._selection, self._scene_element, key, value))
        except EditValidationError as exc:
            self.look_region_action_status.setText(exc.message)
            return
        self._refresh_look_region_context()
        self.state_changed.emit()

    def _refresh_look_region_context(self) -> None:
        mapping = self._scene_element_mapping()
        if self._scene_element is not None and isinstance(mapping, Mapping):
            self.set_scene_element(self._scene_element, mapping)

    def _look_event_actions_path(self) -> tuple[str | int, ...] | None:
        event_id = self.look_region_event_combo.currentData()
        event_id = event_id if isinstance(event_id, str) and event_id else self.look_region_event_combo.currentText().strip()
        if not event_id:
            return None
        mapping = self.session.working_mapping(self._selection) if self.session is not None and self._selection is not None else {}
        if not isinstance(mapping, Mapping):
            return None
        exploration = mapping.get("exploration")
        if isinstance(exploration, Mapping) and isinstance(exploration.get("look_events"), Mapping):
            return ("exploration", "look_events", event_id, "actions")
        if isinstance(mapping.get("look_events"), Mapping):
            return ("look_events", event_id, "actions")
        return None

    def _look_event_actions(self) -> tuple[tuple[str | int, ...] | None, list[Any] | None]:
        path = self._look_event_actions_path()
        if path is None or self.session is None or self._selection is None:
            return path, None
        mapping = self.session.working_mapping(self._selection) or {}
        current: Any = mapping
        for component in path:
            if not isinstance(current, Mapping) or component not in current:
                return path, None
            current = current[component]
        return path, current if isinstance(current, list) else None

    def _render_look_region_actions(self) -> None:
        self.look_region_actions.clear()
        self._clear_look_region_action_fields()
        path, actions = self._look_event_actions()
        self._look_region_actions_path = path
        self.look_region_behavior_box.show()
        if actions is None:
            self.look_region_action_status.setText("Choose an existing look event to edit its action payload.")
            self.look_region_add_action.setEnabled(False)
            self.look_region_remove_action.setEnabled(False)
            return
        self.look_region_action_status.clear()
        self.look_region_add_action.setEnabled(True)
        for action in actions:
            if isinstance(action, Mapping) and isinstance(action.get("type"), str):
                spec = action_editor_spec(action["type"], ActionScope.EXPLORATION)
                self.look_region_actions.addItem(spec.display_name if spec is not None else str(action["type"]))
            else:
                self.look_region_actions.addItem("Legacy / unsupported action")
        self.look_region_remove_action.setEnabled(self.look_region_actions.currentRow() >= 0)
        if actions:
            self.look_region_actions.setCurrentRow(0)

    def _clear_look_region_action_fields(self) -> None:
        self._look_region_action_fields.clear()
        while self.look_region_action_fields.rowCount():
            row = self.look_region_action_fields.takeRow(0)
            for item in (row.labelItem, row.fieldItem):
                widget = item.widget() if item is not None else None
                if widget is not None:
                    widget.deleteLater()

    def _look_region_action_row_changed(self, row: int) -> None:
        self._clear_look_region_action_fields()
        _path, actions = self._look_event_actions()
        if actions is None or row < 0 or row >= len(actions):
            self.look_region_remove_action.setEnabled(False)
            return
        self.look_region_remove_action.setEnabled(True)
        action = actions[row]
        if not isinstance(action, Mapping) or not isinstance(action.get("type"), str):
            self.look_region_action_status.setText("This action is preserved as raw authored data and has no safe editor.")
            return
        spec = action_editor_spec(action["type"], ActionScope.EXPLORATION)
        if spec is None:
            self.look_region_action_status.setText("This action is preserved as raw authored data and has no safe editor.")
            return
        self.look_region_action_status.clear()
        for field in spec.fields:
            widget = self._make_look_region_action_field(field, action.get(field.key, field.default), row)
            self._look_region_action_fields[field.key] = widget
            self.look_region_action_fields.addRow(field.display_name, widget)

    def _make_look_region_action_field(self, field: Any, value: Any, row: int) -> QWidget:
        if field.asset_kind:
            widget = AssetPathEditor(
                story_root=self._project.story_root if self._project is not None else None,
                source=self._project.source if self._project is not None else None,
                project=self._project,
                asset_kind=field.asset_kind,
                asset_label=field.display_name,
                parent=self.look_region_action_fields_widget,
            )
            widget.setText("" if value is None else str(value))
            widget.value_edited.connect(lambda edited, key=field.key, index=row: self._commit_look_region_action_field(index, key, edited))
            return widget
        if field.kind == "boolean":
            widget = QCheckBox()
            widget.setChecked(bool(value))
            widget.toggled.connect(lambda checked, key=field.key, index=row: self._commit_look_region_action_field(index, key, bool(checked)))
            return widget
        if field.kind == "integer":
            widget = QSpinBox()
            widget.setRange(-2_147_483_648, 2_147_483_647)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                widget.setValue(int(value))
            widget.valueChanged.connect(lambda number, key=field.key, index=row: self._commit_look_region_action_field(index, key, int(number)))
            return widget
        if field.kind == "number":
            widget = QDoubleSpinBox()
            widget.setRange(-1_000_000_000.0, 1_000_000_000.0)
            widget.setDecimals(3)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                widget.setValue(float(value))
            widget.valueChanged.connect(lambda number, key=field.key, index=row: self._commit_look_region_action_field(index, key, float(number)))
            return widget
        if field.kind == "reference":
            widget = QComboBox()
            current = value if isinstance(value, str) else ""
            candidates = self._action_reference_candidates(field.reference_target)
            if current and current not in candidates:
                widget.addItem(f"{current} ⚠", current)
            widget.addItem("(unresolved)", "")
            for candidate in candidates:
                widget.addItem(candidate, candidate)
            widget.setCurrentIndex(max(0, widget.findData(current)))
            widget.currentIndexChanged.connect(lambda _index, combo=widget, key=field.key, index=row: self._commit_look_region_action_field(index, key, combo.currentData()))
            return widget
        widget = QLineEdit()
        widget.setText("" if value is None else str(value))
        widget.editingFinished.connect(lambda key=field.key, index=row, line=widget: self._commit_look_region_action_field(index, key, line.text()))
        return widget

    def _action_reference_candidates(self, target: str | None) -> tuple[str, ...]:
        if target == "scene_object":
            mapping = self.session.working_mapping(self._selection) if self.session is not None and self._selection is not None else {}
            config = mapping.get("exploration", {}) if isinstance(mapping, Mapping) and isinstance(mapping.get("exploration"), Mapping) else mapping
            values: list[str] = []
            for key in ("objects", "look_regions"):
                entries = config.get(key, []) if isinstance(config, Mapping) else []
                if isinstance(entries, list):
                    values.extend(str(entry["id"]) for entry in entries if isinstance(entry, Mapping) and isinstance(entry.get("id"), str))
            return tuple(dict.fromkeys(values))
        if self._project is None or self._project.index is None or target is None:
            return ()
        try:
            return tuple(reference.identifier for reference in self._project.index.references(ContentKind.coerce(target)))
        except (TypeError, ValueError):
            return ()

    def _commit_look_region_action_field(self, row: int, key: str, value: Any) -> None:
        if self._updating_scene_element or self.session is None or self._selection is None or self._look_region_actions_path is None:
            return
        try:
            self.session.apply_command(SetDialogueActionParameterCommand(
                self._selection, self._look_region_actions_path + (row,), key, value,
            ))
        except EditValidationError as exc:
            self.look_region_action_status.setText(exc.message)
            return
        self._refresh_look_region_context()
        self.state_changed.emit()

    def _add_look_region_action(self) -> None:
        if self.session is None or self._selection is None or self._look_region_actions_path is None:
            return
        try:
            command = InsertDialogueActionCommand(
                self._selection, self._look_region_actions_path,
                minimal_authored_action(str(self.look_region_action_type.currentData()), ActionScope.EXPLORATION),
            )
            self.session.apply_command(command)
        except (EditValidationError, KeyError, TypeError, ValueError) as exc:
            self.look_region_action_status.setText(str(exc))
            return
        self._refresh_look_region_context()
        if command.index is not None:
            self.look_region_actions.setCurrentRow(command.index)
        self.state_changed.emit()

    def _remove_look_region_action(self) -> None:
        row = self.look_region_actions.currentRow()
        if self.session is None or self._selection is None or self._look_region_actions_path is None or row < 0:
            return
        try:
            self.session.apply_command(RemoveDialogueActionCommand(self._selection, self._look_region_actions_path, row))
        except (EditValidationError, KeyError, TypeError, ValueError) as exc:
            self.look_region_action_status.setText(str(exc))
            return
        self._refresh_look_region_context()
        self.state_changed.emit()

    def _open_look_region_dialogue(self) -> None:
        reference = self._look_region_dialogue_reference()
        if reference:
            self.open_dialogue_sequence.emit(reference)

    def _look_region_dialogue_reference(self) -> str | None:
        _path, actions = self._look_event_actions()
        for action in actions or ():
            if isinstance(action, Mapping):
                value = action.get("dialog", action.get("sequence"))
                if isinstance(value, str):
                    return value
        return None

    def _rename_look_region(self) -> None:
        if self.session is None or self._selection is None or self._scene_element is None:
            return
        current = str(getattr(self._scene_element, "id", ""))
        new_id, accepted = QInputDialog.getText(self, "Rename Look Region", "New ID:", QLineEdit.EchoMode.Normal, current)
        if not accepted or new_id.strip() == current:
            return
        try:
            self.session.apply_command(RenameSceneElementCommand(self._selection, self._scene_element, new_id))
        except EditValidationError as exc:
            self.look_region_action_status.setText(exc.message)
            return
        self.scene_element_renamed.emit(self._scene_element, new_id.strip())

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
        self._updating_scene_geometry = True
        try:
            self._clear_scene_geometry_form()
            self._scene_geometry_fields.clear()
            self._build_scene_geometry_fields(selection, authored)
        finally:
            self._updating_scene_geometry = False

    def _build_scene_geometry_fields(self, selection: object, authored: Mapping[str, Any]) -> None:
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
            # Parent at construction time.  These controls are repeatedly
            # rebuilt, and a parentless QWidget can briefly be treated as a
            # native top-level window while Qt reparents it into the form.
            editor = QSpinBox(self.scene_geometry_box)
            editor.setRange(-1_000_000, 1_000_000)
            if name in {"width", "height"}:
                editor.setMinimum(1)
            editor.setKeyboardTracking(False)
            editor.setValue(int(values[index]))
            editor.valueChanged.connect(lambda _value, ref=selection: self._scene_geometry_value_changed(ref))
            self._scene_geometry_fields[name] = editor
            self.scene_geometry_form.addRow(name.title(), editor)
        self.scene_geometry_box.show()

    def _build_scene_asset(self, selection: object, authored: Mapping[str, Any]) -> None:
        self._scene_asset_fields.clear()
        if getattr(selection, "kind", "") != "object":
            return
        editor = AssetPathEditor(
            story_root=self._project.story_root if self._project is not None else None,
            source=self._project.source if self._project is not None else None,
            project=self._project,
            asset_kind="sprites",
            asset_label="Sprite",
            parent=self.scene_geometry_box,
        )
        editor.setText(str(authored.get("sprite", "")))
        editor.value_edited.connect(lambda value, ref=selection: self._scene_asset_changed(ref, value))
        self._scene_asset_fields["sprite"] = editor
        self.scene_geometry_form.addRow("Sprite", editor)
        self.scene_geometry_box.show()

    def _scene_asset_changed(self, selection: object, value: Any) -> None:
        if self.session is None or self._selection is None or selection is not self._scene_element:
            return
        try:
            self.session.apply_command(SetSceneElementPropertyCommand(self._selection, selection, "sprite", value))
        except EditValidationError as exc:
            self._show_error((), exc.message)
            return
        self.summary.setPlainText(_compact_mapping(self._scene_element_mapping()))
        self._update_header()
        self.state_changed.emit()

    def _emit_scene_geometry(self, selection: object) -> None:
        if not self._scene_geometry_fields or selection is not self._scene_element:
            return
        values = tuple(self._scene_geometry_fields[name].value() for name in self._scene_geometry_fields)
        self.scene_geometry_edited.emit(selection, values)

    def _scene_geometry_value_changed(self, selection: object) -> None:
        """Commit arrow/step changes immediately without per-keystroke edits."""

        if self._updating_scene_geometry:
            return
        self._emit_scene_geometry(selection)

    def clear_scene_element(self) -> None:
        """Return the Inspector to the selected top-level definition."""

        if self._scene_element is None:
            return
        self._scene_element = None
        self.property_scroll.setEnabled(True)
        self._set_look_region_context_visible(False)
        self.context_tabs.setCurrentWidget(self.scene_context_page)
        self.scene_geometry_box.hide()
        self.scene_condition_box.hide()
        self.look_region_identity_box.hide()
        self.look_region_interaction_box.hide()
        self.look_region_behavior_box.hide()
        self.look_region_unknown_box.hide()
        self._clear_scene_geometry_form()
        self._scene_geometry_fields.clear()
        self._scene_asset_fields.clear()
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

    def _set_look_region_context_visible(self, visible: bool) -> None:
        """Show the contextual page only while a Look Region is selected."""

        self.context_tabs.setTabVisible(1, visible)
        if not visible:
            self.context_tabs.setCurrentWidget(self.scene_context_page)

    def _build_form(self) -> None:
        self._clear_dynamic_property_editors()
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
        self.dynamic_fields_layout.addWidget(group)
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
            parent=self.dynamic_fields_container,
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
        self.dynamic_fields_layout.addWidget(box)

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

    def _clear_scene_geometry_form(self) -> None:
        """Dispose only the dynamic controls inside the persistent geometry box."""

        while self.scene_geometry_form.rowCount():
            row = self.scene_geometry_form.takeRow(0)
            for item in (row.labelItem, row.fieldItem):
                widget = item.widget() if item is not None else None
                if widget is not None:
                    widget.setParent(self._retired_scene_controls)
                    widget.deleteLater()

    def _clear_dynamic_property_editors(self) -> None:
        """Clear generated property editors without touching persistent sections."""

        self._rows.clear()
        self._object_groups.clear()
        while self.fields_form.rowCount():
            row = self.fields_form.takeRow(0)
            for item in (row.labelItem, row.fieldItem):
                widget = item.widget() if item is not None else None
                if widget is not None:
                    widget.setParent(self._retired_dynamic_controls)
                    widget.deleteLater()
        while self.dynamic_fields_layout.count() > 1:
            item = self.dynamic_fields_layout.takeAt(1)
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
