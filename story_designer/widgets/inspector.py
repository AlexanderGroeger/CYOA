"""Schema-driven editable Inspector for Story Designer definitions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSignalBlocker, QTimer, Qt, Signal
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
    MoveDialogueActionCommand,
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


class _ActionListWidget(QListWidget):
    """A view-only action list which reports safe, same-list reorder intent.

    The list must not use ``InternalMove``: the canonical order belongs to the
    ProjectSession working copy, and Qt must not mutate its item model while a
    semantic command is also about to rebuild the view.
    """

    move_requested = Signal(int, int)
    _LIST_MIME_TYPE = "application/x-qabstractitemmodeldatalist"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        # DragDrop lets this subclass own the drop decision.  In particular,
        # QListWidget's InternalMove implementation must not reorder the view
        # before the semantic MoveDialogueActionCommand runs.
        self.setDragDropMode(QListWidget.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragDropOverwriteMode(False)
        self.setDropIndicatorShown(True)
        self._drag_source_row: int | None = None

    def startDrag(self, supported_actions: Qt.DropActions) -> None:  # noqa: N802 - Qt API name
        self._drag_source_row = self.currentRow()
        super().startDrag(supported_actions)

    def _is_internal_drag(self, event: object) -> bool:
        source = event.source()  # type: ignore[attr-defined]
        mime_data = event.mimeData()  # type: ignore[attr-defined]
        return source is self and mime_data.hasFormat(self._LIST_MIME_TYPE)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt API name
        if not self._is_internal_drag(event):
            event.ignore()
            return
        event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 - Qt API name
        if not self._is_internal_drag(event):
            event.ignore()
            return
        event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt API name
        if not self._is_internal_drag(event):
            event.ignore()
            return
        source = self._drag_source_row if self._drag_source_row is not None else self.currentRow()
        self._drag_source_row = None
        if source < 0 or source >= self.count():
            event.ignore()
            return
        item = self.itemAt(event.position().toPoint())
        target = self.count() if item is None else self.row(item)
        if item is not None:
            item_rect = self.visualItemRect(item)
            if event.position().y() > item_rect.center().y():
                target += 1
        # ``MoveDialogueActionCommand`` takes the item's final index, while
        # the drop target above is an insertion slot in the original list.
        if source < target:
            target -= 1
        target = max(0, min(target, self.count() - 1))
        if target == source:
            event.ignore()
            return
        # Emit only immutable row numbers.  The Inspector connects this signal
        # with Qt.QueuedConnection so its refresh cannot destroy list items or
        # action editors while Qt is still returning from the native drop.
        event.acceptProposedAction()
        self.move_requested.emit(source, target)


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
        self._scene_logical_size: tuple[int, int] | None = None
        self._selection: DefinitionSelection | None = None
        self._definition: Any | None = None
        self._diagnostics = Diagnostics()
        self._scene_element = None
        self._rows: dict[tuple[str | int, ...], _PropertyRow] = {}
        self._object_groups: dict[str, QFormLayout] = {}
        self._scene_geometry_fields: dict[str, QWidget] = {}
        self._scene_asset_fields: dict[str, AssetPathEditor] = {}
        self._look_region_action_fields: dict[str, QWidget] = {}
        self._look_region_event_id: str | None = None
        self._look_region_actions_path: tuple[str | int, ...] | None = None
        self._updating_scene_geometry = False
        self._updating_scene_element = False
        self._object_name_editing = False
        self._preferred_action_row = 0
        self._preserved_action_context = None
        self._action_tokens: list[int] = []
        self._action_token_counter = 0
        self._action_selection_token: int | None = None

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

        self.object_identity_box = QGroupBox("Identity")
        object_identity_form = QFormLayout(self.object_identity_box)
        self.object_identity_id = self._metadata_label()
        self.object_name_edit = QLineEdit()
        self.object_name_edit.editingFinished.connect(self._object_name_finished)
        self.object_rename_button = QPushButton("Rename…")
        self.object_rename_button.clicked.connect(self._rename_object)
        object_name_row = QWidget(self.object_identity_box)
        object_name_layout = QHBoxLayout(object_name_row)
        object_name_layout.setContentsMargins(0, 0, 0, 0)
        object_name_layout.addWidget(self.object_name_edit, 1)
        object_name_layout.addWidget(self.object_rename_button)
        object_identity_form.addRow("ID", self.object_identity_id)
        object_identity_form.addRow("Name", object_name_row)
        self.object_identity_box.hide()

        self.object_geometry_box = QGroupBox("Transform")
        self.object_geometry_form = QFormLayout(self.object_geometry_box)
        self.object_geometry_box.hide()
        self.object_appearance_box = QGroupBox("Appearance")
        object_appearance_layout = QVBoxLayout(self.object_appearance_box)
        self.object_visible = QCheckBox("Visible in authored scene")
        self.object_visible.stateChanged.connect(self._object_visible_changed)
        object_appearance_layout.addWidget(self.object_visible)
        self.object_asset_container = QWidget(self.object_appearance_box)
        self.object_asset_form = QFormLayout(self.object_asset_container)
        self.object_asset_form.setContentsMargins(0, 0, 0, 0)
        self._retired_object_asset_controls = QWidget(self.object_appearance_box)
        self._retired_object_asset_controls.hide()
        object_appearance_layout.addWidget(self.object_asset_container)
        self.object_appearance_box.hide()
        self.object_condition_box = QGroupBox("Visible When")
        object_condition_layout = QVBoxLayout(self.object_condition_box)
        self.object_condition_editor = ConditionEditorWidget(parent=self.object_condition_box)
        self.object_condition_editor.condition_changed.connect(self._object_condition_changed)
        object_condition_layout.addWidget(self.object_condition_editor)
        self.object_condition_box.hide()
        self.object_context_status = QLabel()
        self.object_context_status.setWordWrap(True)
        self.object_context_status.setStyleSheet("color: #b45309;")
        self.object_context_status.hide()

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
        self.look_region_actions = _ActionListWidget()
        self.look_region_actions.setMinimumHeight(60)
        self.look_region_actions.setMaximumHeight(150)
        self.look_region_actions.currentRowChanged.connect(self._look_region_action_row_changed)
        self.look_region_actions.move_requested.connect(
            self._move_look_region_action,
            Qt.ConnectionType.QueuedConnection,
        )
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
        look_context_content = QWidget()
        look_context_content_layout = QVBoxLayout(look_context_content)
        look_context_content_layout.setContentsMargins(0, 0, 0, 0)
        look_context_content_layout.addWidget(self.look_region_identity_box)
        look_context_content_layout.addWidget(self.look_region_interaction_box)
        look_context_content_layout.addWidget(self.scene_geometry_box)
        look_context_content_layout.addWidget(self.scene_condition_box)
        look_context_content_layout.addWidget(self.look_region_behavior_box)
        self.look_region_unknown_box = QGroupBox("Unknown / Legacy Fields")
        self.look_region_unknown_text = QPlainTextEdit()
        self.look_region_unknown_text.setReadOnly(True)
        self.look_region_unknown_box_layout = QVBoxLayout(self.look_region_unknown_box)
        self.look_region_unknown_box_layout.addWidget(QLabel("Preserved, but not interpreted by this page."))
        self.look_region_unknown_box_layout.addWidget(self.look_region_unknown_text)
        self.look_region_unknown_box.hide()
        look_context_content_layout.addWidget(self.look_region_unknown_box)
        look_context_content_layout.addStretch(1)
        look_scroll = QScrollArea()
        look_scroll.setWidgetResizable(True)
        look_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        look_scroll.setWidget(look_context_content)
        look_context_layout.addWidget(look_scroll)
        self.context_tabs.addTab(self.scene_context_page, "Scene")
        self.context_tabs.addTab(self.look_region_context_page, "Look Region")
        self.object_context_page = QWidget()
        object_context_layout = QVBoxLayout(self.object_context_page)
        object_context_layout.setContentsMargins(0, 0, 0, 0)
        object_context_content = QWidget()
        object_context_content_layout = QVBoxLayout(object_context_content)
        object_context_content_layout.setContentsMargins(0, 0, 0, 0)
        object_context_content_layout.addWidget(self.object_identity_box)
        object_context_content_layout.addWidget(self.object_geometry_box)
        object_context_content_layout.addWidget(self.object_appearance_box)
        object_context_content_layout.addWidget(self.object_condition_box)
        object_context_content_layout.addWidget(self.object_context_status)
        object_context_content_layout.addStretch(1)
        object_scroll = QScrollArea()
        object_scroll.setWidgetResizable(True)
        object_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        object_scroll.setWidget(object_context_content)
        object_context_layout.addWidget(object_scroll)
        self.context_tabs.addTab(self.object_context_page, "Object")
        self._set_look_region_context_visible(False)
        self._set_object_context_visible(False)

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

    def set_tool_context_mode(self, enabled: bool = True) -> None:
        """Present the mature context pages as one editor surface.

        The pages remain stable for lifetime safety and compatibility, but the
        tab strip is hidden when the Inspector is owned by a ToolShell.  Page
        replacement is driven by scene/object/region selection.
        """

        self.context_tabs.tabBar().setVisible(not enabled)
        self.setProperty("toolContextEditor", bool(enabled))

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
        self.object_identity_box.hide()
        self.object_geometry_box.hide()
        self.object_appearance_box.hide()
        self.object_condition_box.hide()
        self.object_context_status.hide()
        self._clear_scene_geometry_form()
        self._clear_object_geometry_form()
        self._scene_geometry_fields.clear()
        self._clear_object_asset_form()
        self._scene_asset_fields.clear()
        self._set_look_region_context_visible(False)
        self._set_object_context_visible(False)
        self._clear_dynamic_property_editors()

    def set_selection(
        self,
        project: StoryProject | None,
        selection: DefinitionSelection | None,
        definition: Any | None,
        diagnostics: Diagnostics,
    ) -> None:
        preserved_scene_element = self._scene_element if self._selection == selection else None
        if preserved_scene_element is not None:
            self._preferred_action_row = max(0, self.look_region_actions.currentRow())
        else:
            self._preferred_action_row = 0
        self._preserved_action_context = preserved_scene_element
        self._project = project
        display = getattr(project.manifest, "display", None) if project is not None else None
        if isinstance(display, Mapping):
            try:
                self._scene_logical_size = (
                    max(1, int(display.get("width", 1))),
                    max(1, int(display.get("height", 1))),
                )
            except (TypeError, ValueError):
                self._scene_logical_size = None
        else:
            self._scene_logical_size = None
        self._selection = selection
        self._definition = definition
        self._diagnostics = diagnostics
        self._scene_element = None
        self.property_scroll.setEnabled(True)
        self._set_look_region_context_visible(False)
        self._set_object_context_visible(False)
        self.context_tabs.setCurrentWidget(self.scene_context_page)
        self.scene_geometry_box.hide()
        self.scene_condition_box.hide()
        self.look_region_identity_box.hide()
        self.look_region_interaction_box.hide()
        self.look_region_behavior_box.hide()
        self.look_region_unknown_box.hide()
        self.object_identity_box.hide()
        self.object_geometry_box.hide()
        self.object_appearance_box.hide()
        self.object_condition_box.hide()
        self.object_context_status.hide()
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

        if self._scene_element == selection:
            self._update_scene_element_values(authored)
            return

        if selection != self._preserved_action_context:
            self._preferred_action_row = 0
        self._preserved_action_context = None

        self._scene_element = selection
        self.property_scroll.setEnabled(True)
        kind = getattr(selection, "kind", "element")
        if kind == "look_region":
            self._set_object_context_visible(False)
            self._set_look_region_context_visible(True)
            self.context_tabs.setCurrentWidget(self.look_region_context_page)
        elif kind == "object":
            self._set_look_region_context_visible(False)
            self._set_object_context_visible(True)
            self.context_tabs.setCurrentWidget(self.object_context_page)
        else:
            self._set_look_region_context_visible(False)
            self._set_object_context_visible(False)
            self.context_tabs.setCurrentWidget(self.scene_context_page)
            self.look_region_identity_box.hide()
            self.look_region_interaction_box.hide()
            self.look_region_behavior_box.hide()
            self.look_region_unknown_box.hide()
            self.object_identity_box.hide()
            self.object_geometry_box.hide()
            self.object_appearance_box.hide()
            self.object_condition_box.hide()
            self.object_context_status.hide()
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
        elif kind == "object":
            self._build_object_context(authored)
        if getattr(selection, "kind", "") in {"object", "look_region"}:
            condition = authored.get("visible_when", authored.get("conditions", MISSING))
            self._updating_scene_element = True
            try:
                editor = self.object_condition_editor if kind == "object" else self.scene_condition_editor
                editor.set_condition(condition, project=self._project)
            finally:
                self._updating_scene_element = False
            editor.setEnabled(True)
            (self.object_condition_box if kind == "object" else self.scene_condition_box).show()

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

    def _update_scene_element_values(self, authored: Mapping[str, Any]) -> None:
        """Update bindings for the current element without rebuilding them.

        This is the hot path for geometry/property notifications.  The
        controls that originated the edit remain owned by the same QObject;
        external model updates are applied with signals blocked.
        """

        selection = self._scene_element
        if selection is None:
            return
        kind = getattr(selection, "kind", "element")
        self._updating_scene_element = True
        self._updating_scene_geometry = True
        try:
            values, names = self._scene_geometry_values(selection, authored)
            for index, name in enumerate(names):
                editor = self._scene_geometry_fields.get(name)
                if editor is None:
                    continue
                blocker = QSignalBlocker(editor)
                try:
                    editor.setValue(values[index])  # type: ignore[attr-defined]
                finally:
                    del blocker
            if kind == "look_region":
                self._update_region_geometry_ranges()
            if kind == "object":
                self._build_object_context(authored)
                asset = self._scene_asset_fields.get("sprite")
                if asset is not None:
                    blocker = QSignalBlocker(asset)
                    try:
                        asset.setText(str(authored.get("sprite", "")))
                    finally:
                        del blocker
            elif kind == "look_region":
                previous_path = self._look_region_actions_path
                self._build_look_region_context(authored, render_actions=False)
                if previous_path != self._look_event_actions_path():
                    self._render_look_region_actions()
            if kind in {"object", "look_region"}:
                condition = authored.get("visible_when", authored.get("conditions", MISSING))
                editor = self.object_condition_editor if kind == "object" else self.scene_condition_editor
                editor.set_condition(condition, project=self._project)
        finally:
            self._updating_scene_geometry = False
            self._updating_scene_element = False
        self.summary.setPlainText(_compact_mapping(authored))
        self._update_header()

    def _build_look_region_context(self, authored: Mapping[str, Any], *, render_actions: bool = True) -> None:
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
        if render_actions:
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
        mapping = self._scene_element_mapping()
        if isinstance(mapping, Mapping):
            if key == "event":
                self._update_scene_element_values(mapping)
            else:
                self.summary.setPlainText(_compact_mapping(mapping))
                self._update_header()
        self.state_changed.emit()

    def _refresh_look_region_context(self) -> None:
        mapping = self._scene_element_mapping()
        if self._scene_element is not None and isinstance(mapping, Mapping):
            self._update_scene_element_values(mapping)
            self._render_look_region_actions()

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
        previous_path = self._look_region_actions_path
        selected_token = self._action_selection_token
        blocker = QSignalBlocker(self.look_region_actions)
        try:
            self.look_region_actions.clear()
            self._clear_look_region_action_fields()
        finally:
            del blocker
        path, actions = self._look_event_actions()
        self._look_region_actions_path = path
        self.look_region_behavior_box.show()
        if actions is None:
            self._action_tokens = []
            self._action_selection_token = None
            self.look_region_action_status.setText("Choose an existing look event to edit its action payload.")
            self.look_region_add_action.setEnabled(False)
            self.look_region_remove_action.setEnabled(False)
            return
        self.look_region_action_status.clear()
        self.look_region_add_action.setEnabled(True)
        if previous_path != path:
            self._action_tokens = [self._new_action_token() for _ in actions]
        else:
            self._action_tokens = self._action_tokens[:len(actions)]
            while len(self._action_tokens) < len(actions):
                self._action_tokens.append(self._new_action_token())
        selected_row = next(
            (index for index, token in enumerate(self._action_tokens) if token == selected_token),
            self._preferred_action_row,
        )
        blocker = QSignalBlocker(self.look_region_actions)
        try:
            for action in actions:
                if isinstance(action, Mapping) and isinstance(action.get("type"), str):
                    spec = action_editor_spec(action["type"], ActionScope.EXPLORATION)
                    self.look_region_actions.addItem(spec.display_name if spec is not None else str(action["type"]))
                else:
                    self.look_region_actions.addItem("Legacy / unsupported action")
            selected_row = min(max(0, selected_row), len(actions) - 1) if actions else -1
            self.look_region_actions.setCurrentRow(selected_row)
        finally:
            del blocker
        self._preferred_action_row = max(0, selected_row)
        self._look_region_action_row_changed(selected_row)

    def _new_action_token(self) -> int:
        self._action_token_counter += 1
        return self._action_token_counter

    def _clear_look_region_action_fields(self) -> None:
        self._look_region_action_fields.clear()
        while self.look_region_action_fields.rowCount():
            row = self.look_region_action_fields.takeRow(0)
            for item in (row.labelItem, row.fieldItem):
                widget = item.widget() if item is not None else None
                if widget is not None:
                    widget.setParent(self._retired_object_asset_controls)
                    widget.deleteLater()

    def _look_region_action_row_changed(self, row: int) -> None:
        self._preferred_action_row = max(0, row)
        self._action_selection_token = (
            self._action_tokens[row] if 0 <= row < len(self._action_tokens) else None
        )
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
        if field.kind == "point":
            widget = QLineEdit()
            point = value if isinstance(value, (list, tuple)) and len(value) == 2 else (0, 0)
            widget.setText(f"[{point[0]}, {point[1]}]")
            widget.setToolTip("Enter [x, y].")
            widget.editingFinished.connect(
                lambda key=field.key, index=row, line=widget: self._commit_point_action_field(index, key, line.text())
            )
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

    def _commit_point_action_field(self, row: int, key: str, text: str) -> None:
        raw = text.strip().strip("[]()")
        parts = [part.strip() for part in raw.split(",")]
        if len(parts) != 2:
            self.look_region_action_status.setText("Position must be entered as [x, y].")
            return
        try:
            value = [int(parts[0]), int(parts[1])]
        except ValueError:
            self.look_region_action_status.setText("Position coordinates must be integers.")
            return
        self._commit_look_region_action_field(row, key, value)

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
        # This is a value binding.  Keep the sender and its owning action
        # editor alive; only cheap derived text needs to change here.
        mapping = self._scene_element_mapping()
        self.summary.setPlainText(_compact_mapping(mapping))
        self._update_header()
        self.state_changed.emit()

    def _add_look_region_action(self) -> None:
        if self.session is None or self._selection is None or self._look_region_actions_path is None:
            return
        action_type = str(self.look_region_action_type.currentData())
        if action_type == "dialog":
            # A dialog action is commonly inserted while a type popup/menu is
            # still unwinding.  Let that native event finish before changing
            # the action list and its parameter page.
            actions_path = self._look_region_actions_path
            selection = self._selection
            QTimer.singleShot(
                0,
                lambda path=actions_path, owner=selection: self._insert_look_region_action(owner, path, action_type),
            )
            return
        self._insert_look_region_action(self._selection, self._look_region_actions_path, action_type)

    def _insert_look_region_action(
        self,
        owner: DefinitionSelection | None,
        actions_path: tuple[str | int, ...] | None,
        action_type: str,
    ) -> None:
        if self.session is None or self._selection != owner or owner is None or actions_path is None:
            return
        try:
            command = InsertDialogueActionCommand(
                owner, actions_path,
                minimal_authored_action(action_type, ActionScope.EXPLORATION),
            )
            self.session.apply_command(command)
        except (EditValidationError, KeyError, TypeError, ValueError) as exc:
            self.look_region_action_status.setText(str(exc))
            return
        if command.index is not None:
            self._action_tokens.insert(command.index, self._new_action_token())
        self._refresh_look_region_context()
        if command.index is not None:
            self.look_region_actions.setCurrentRow(command.index)
        self.state_changed.emit()

    def _remove_look_region_action(self) -> None:
        row = self.look_region_actions.currentRow()
        if self.session is None or self._selection is None or self._look_region_actions_path is None or row < 0:
            return
        actions_path = self._look_region_actions_path
        owner = self._selection
        QTimer.singleShot(
            0,
            lambda path=actions_path, index=row, selection=owner: self._remove_look_region_action_now(selection, path, index),
        )

    def _remove_look_region_action_now(
        self,
        owner: DefinitionSelection,
        actions_path: tuple[str | int, ...],
        row: int,
    ) -> None:
        if self.session is None or self._selection != owner:
            return
        try:
            self.session.apply_command(RemoveDialogueActionCommand(owner, actions_path, row))
        except (EditValidationError, KeyError, TypeError, ValueError) as exc:
            self.look_region_action_status.setText(str(exc))
            return
        if 0 <= row < len(self._action_tokens):
            removed_token = self._action_tokens.pop(row)
            if self._action_selection_token == removed_token:
                self._action_selection_token = None
        self._refresh_look_region_context()
        self.state_changed.emit()

    def _move_look_region_action(self, source: int, target: int) -> None:
        """Move an action within the selected look event's own action list."""

        if (
            self.session is None
            or self._selection is None
            or self._look_region_actions_path is None
            or source == target
        ):
            return
        selected_source = (
            0 <= source < len(self._action_tokens)
            and self._action_selection_token == self._action_tokens[source]
        )
        try:
            self.session.apply_command(MoveDialogueActionCommand(
                self._selection,
                self._look_region_actions_path,
                source,
                target,
            ))
        except (EditValidationError, KeyError, TypeError, ValueError) as exc:
            self.look_region_action_status.setText(str(exc))
            return
        if 0 <= source < len(self._action_tokens):
            token = self._action_tokens.pop(source)
            self._action_tokens.insert(max(0, min(target, len(self._action_tokens))), token)
        self._refresh_look_region_context()
        if not selected_source:
            self.look_region_actions.setCurrentRow(target)
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
            self._clear_object_geometry_form()
            self._scene_geometry_fields.clear()
            self._build_scene_geometry_fields(selection, authored)
        finally:
            self._updating_scene_geometry = False

    def _build_scene_geometry_fields(self, selection: object, authored: Mapping[str, Any]) -> None:
        values, names = self._scene_geometry_values(selection, authored)
        if not names:
            self.scene_geometry_form.addRow(QLabel("This element has no graphical geometry editor."))
            self.scene_geometry_box.show()
            return
        for index, name in enumerate(names):
            # Parent at construction time.  These controls are repeatedly
            # rebuilt, and a parentless QWidget can briefly be treated as a
            # native top-level window while Qt reparents it into the form.
            parent = self.object_geometry_box if getattr(selection, "kind", "") == "object" else self.scene_geometry_box
            form = self.object_geometry_form if getattr(selection, "kind", "") == "object" else self.scene_geometry_form
            editor = QDoubleSpinBox(parent) if name == "rotation" else QSpinBox(parent)
            editor.setRange(-1_000_000, 1_000_000)
            if name == "rotation":
                editor.setDecimals(2)
            if name in {"width", "height"}:
                editor.setMinimum(1)
            editor.setKeyboardTracking(False)
            blocker = QSignalBlocker(editor)
            try:
                editor.setValue(int(values[index]))
            finally:
                del blocker
            editor.valueChanged.connect(lambda _value, ref=selection, changed=name: self._scene_geometry_value_changed(ref, changed))
            self._scene_geometry_fields[name] = editor
            form.addRow(name.title(), editor)
        if getattr(selection, "kind", "") == "look_region":
            self._update_region_geometry_ranges()
            if self._scene_logical_size is not None:
                x, y, width, height = (int(value) for value in values[:4])
                scene_width, scene_height = self._scene_logical_size
                if x < 0 or y < 0 or width < 1 or height < 1 or x + width > scene_width or y + height > scene_height:
                    warning = QLabel("Warning: this Look Region is outside the scene bounds. Editing it will constrain it.")
                    warning.setStyleSheet("color: #f59e0b;")
                    warning.setWordWrap(True)
                    form.addRow(warning)
        (self.object_geometry_box if getattr(selection, "kind", "") == "object" else self.scene_geometry_box).show()

    def _update_region_geometry_ranges(self) -> None:
        if self._scene_logical_size is None:
            return
        fields = self._scene_geometry_fields
        if not all(name in fields for name in ("x", "y", "width", "height")):
            return
        x_field, y_field = fields["x"], fields["y"]
        width_field, height_field = fields["width"], fields["height"]
        x, y = int(x_field.value()), int(y_field.value())  # type: ignore[attr-defined]
        width, height = int(width_field.value()), int(height_field.value())  # type: ignore[attr-defined]
        scene_width, scene_height = self._scene_logical_size
        blockers = [QSignalBlocker(field) for field in (x_field, y_field, width_field, height_field)]
        try:
            x_field.setRange(min(0, x), max(x, scene_width - max(1, width)))  # type: ignore[attr-defined]
            y_field.setRange(min(0, y), max(y, scene_height - max(1, height)))  # type: ignore[attr-defined]
            width_field.setRange(1, max(width, scene_width - max(0, x)))  # type: ignore[attr-defined]
            height_field.setRange(1, max(height, scene_height - max(0, y)))  # type: ignore[attr-defined]
        finally:
            del blockers

    @staticmethod
    def _scene_geometry_values(selection: object, authored: Mapping[str, Any]) -> tuple[list[Any], list[str]]:
        kind = getattr(selection, "kind", "")
        if kind == "object":
            raw = authored.get("position", (0, 0))
            values = list(raw) if isinstance(raw, (list, tuple)) and len(raw) == 2 else [0, 0]
            names = ["x", "y"]
            if isinstance(authored.get("size"), (list, tuple)) and len(authored["size"]) == 2:
                values.extend(authored["size"])
                names.extend(("width", "height"))
            values.extend((authored.get("rotation", 0), authored.get("z", 0)))
            names.extend(("rotation", "z"))
        elif kind == "look_region":
            raw = authored.get("rect", authored.get("hitbox"))
            look = authored.get("look")
            if raw is None and isinstance(look, Mapping):
                raw = look.get("rect", look.get("hitbox"))
            values = list(raw) if isinstance(raw, (list, tuple)) and len(raw) == 4 else [0, 0, 1, 1]
            names = ["x", "y", "width", "height"]
        else:
            return [], []
        return values, names

    def _build_scene_asset(self, selection: object, authored: Mapping[str, Any]) -> None:
        self._clear_object_asset_form()
        self._scene_asset_fields.clear()
        if getattr(selection, "kind", "") != "object":
            return
        editor = AssetPathEditor(
            story_root=self._project.story_root if self._project is not None else None,
            source=self._project.source if self._project is not None else None,
            project=self._project,
            asset_kind="sprites",
            asset_label="Sprite",
            parent=self.object_asset_container,
        )
        editor.setText(str(authored.get("sprite", "")))
        editor.value_edited.connect(lambda value, ref=selection: self._scene_asset_changed(ref, value))
        self._scene_asset_fields["sprite"] = editor
        self.object_asset_form.addRow("Sprite", editor)
        self.object_appearance_box.show()

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

    def _scene_geometry_value_changed(self, selection: object, changed: str | None = None) -> None:
        """Commit arrow/step changes immediately without per-keystroke edits."""

        if self._updating_scene_geometry:
            return
        if getattr(selection, "kind", "") == "object":
            self._object_geometry_value_changed(selection, changed)
            return
        self._update_region_geometry_ranges()
        self._emit_scene_geometry(selection)

    def _object_geometry_value_changed(self, selection: object, changed: str | None = None) -> None:
        if selection is not self._scene_element:
            return
        fields = self._scene_geometry_fields
        x_field, y_field = fields.get("x"), fields.get("y")
        if x_field is None or y_field is None:
            return
        if changed in {"x", "y"}:
            self.scene_geometry_edited.emit(selection, (int(x_field.value()), int(y_field.value())))
        elif changed in {"width", "height"}:
            self._set_scene_element_property(
                "size", [int(fields["width"].value()), int(fields["height"].value())]
            )
        elif changed == "rotation":
            self._set_scene_element_property("rotation", float(fields["rotation"].value()))
        elif changed == "z":
            self._set_scene_element_property("z", int(fields["z"].value()))

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
        self.object_identity_box.hide()
        self.object_geometry_box.hide()
        self.object_appearance_box.hide()
        self.object_condition_box.hide()
        self.object_context_status.hide()
        self._clear_scene_geometry_form()
        self._clear_object_geometry_form()
        self._scene_geometry_fields.clear()
        self._clear_object_asset_form()
        self._scene_asset_fields.clear()
        self._set_object_context_visible(False)
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

    def _set_object_context_visible(self, visible: bool) -> None:
        """Show the dedicated entity page only while a Scene Object is selected."""

        self.context_tabs.setTabVisible(2, visible)
        if not visible and self.context_tabs.currentWidget() is self.object_context_page:
            self.context_tabs.setCurrentWidget(self.scene_context_page)

    def _build_object_context(self, authored: Mapping[str, Any]) -> None:
        identifier = str(getattr(self._scene_element, "id", ""))
        self.object_identity_id.setText(identifier)
        self._object_name_editing = True
        try:
            self.object_name_edit.setText(str(authored.get("name", "")))
            self.object_visible.setChecked(bool(authored.get("visible", True)))
        finally:
            self._object_name_editing = False
        self.object_identity_box.show()
        self.object_geometry_box.show()
        self.object_appearance_box.show()
        self.object_rename_button.setEnabled(self.session is not None)
        self.object_name_edit.setEnabled(self.session is not None)

    def _object_name_finished(self) -> None:
        if self._object_name_editing or self._scene_element is None or getattr(self._scene_element, "kind", "") != "object":
            return
        self._set_scene_element_property("name", self.object_name_edit.text())

    def _object_visible_changed(self, state: int) -> None:
        if self._object_name_editing or self._scene_element is None or getattr(self._scene_element, "kind", "") != "object":
            return
        self._set_scene_element_property("visible", bool(state))

    def _object_condition_changed(self, value: Any) -> None:
        if self._updating_scene_element or self.session is None or self._selection is None or self._scene_element is None:
            return
        try:
            self.session.apply_command(SetSceneElementConditionCommand(self._selection, self._scene_element, value))
        except EditValidationError as exc:
            self.object_condition_editor.status.setText(exc.message)
            return
        self.summary.setPlainText(_compact_mapping(self._scene_element_mapping()))
        self.state_changed.emit()

    def _set_scene_element_property(self, key: str, value: Any) -> None:
        if self.session is None or self._selection is None or self._scene_element is None:
            return
        try:
            self.session.apply_command(SetSceneElementPropertyCommand(self._selection, self._scene_element, key, value))
        except EditValidationError as exc:
            self.object_context_status.setText(exc.message)
            self.object_context_status.show()
            return
        self._refresh_scene_element_context()
        self.state_changed.emit()

    def _refresh_scene_element_context(self) -> None:
        mapping = self._scene_element_mapping()
        if self._scene_element is not None and isinstance(mapping, Mapping):
            self.set_scene_element(self._scene_element, mapping)

    def _rename_object(self) -> None:
        if self.session is None or self._selection is None or self._scene_element is None:
            return
        current = str(getattr(self._scene_element, "id", ""))
        new_id, accepted = QInputDialog.getText(self, "Rename Object", "New ID:", QLineEdit.EchoMode.Normal, current)
        if not accepted or new_id.strip() == current:
            return
        try:
            self.session.apply_command(RenameSceneElementCommand(self._selection, self._scene_element, new_id))
        except EditValidationError as exc:
            self.object_context_status.setText(exc.message)
            self.object_context_status.show()
            return
        self.scene_element_renamed.emit(self._scene_element, new_id.strip())

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

    def _clear_object_geometry_form(self) -> None:
        """Dispose dynamic Object transform controls without touching appearance."""

        while self.object_geometry_form.rowCount():
            row = self.object_geometry_form.takeRow(0)
            for item in (row.labelItem, row.fieldItem):
                widget = item.widget() if item is not None else None
                if widget is not None:
                    widget.deleteLater()

    def _clear_object_asset_form(self) -> None:
        """Dispose the single dynamic Sprite editor in its owned container."""

        while self.object_asset_form.rowCount():
            row = self.object_asset_form.takeRow(0)
            for item in (row.labelItem, row.fieldItem):
                widget = item.widget() if item is not None else None
                if widget is not None:
                    widget.setParent(self._retired_object_asset_controls)
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
