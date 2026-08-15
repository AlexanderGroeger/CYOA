"""Focused editor for scene-local narrative dialogue."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from engine.story_core import (
    ActionScope,
    ContentKind,
    StoryProject,
    action_editor_spec,
    action_editor_specs,
    minimal_authored_action,
)
from engine.story_core.schema import MISSING

from ..models import (
    DefinitionSelection,
    DialogueDocumentModel,
    DuplicateDialogueActionCommand,
    DuplicateNamedDialogueSequenceCommand,
    DialogueEntrySelection,
    DuplicateDialogueEntryCommand,
    EditValidationError,
    InsertDialogueEntryCommand,
    MoveDialogueEntryCommand,
    MoveDialogueActionCommand,
    ProjectSession,
    RemoveDialogueEntryCommand,
    RemoveDialogueActionCommand,
    RemoveNamedDialogueSequenceCommand,
    InsertDialogueActionCommand,
    InsertNamedDialogueSequenceCommand,
    SetDialogueConditionCommand,
    SetDialogueMetadataCommand,
    SetDialogueActionParameterCommand,
    SetDialogueTextCommand,
    present_dialogue_actions,
)
from .condition_editor import ConditionEditorWidget
from .property_editors import AssetPathEditor


class _CommitPlainTextEdit(QPlainTextEdit):
    commit_requested = Signal()
    focused = Signal()

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt API name
        self.focused.emit()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt API name
        super().focusOutEvent(event)
        self.commit_requested.emit()


class DialogueEditorWidget(QWidget):
    """List/detail authoring surface backed by ``ProjectSession`` commands."""

    entry_selected = Signal(object)
    dialogue_changed = Signal(object)
    open_dialogue_sequence = Signal(str)

    def __init__(self, session: ProjectSession | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.project: StoryProject | None = None
        self.scene_id: str | None = None
        self.document: DialogueDocumentModel | None = None
        self.selected_source_id: str | None = None
        self.selected_entry: DialogueEntrySelection | None = None
        self.selected_action_index: int | None = None
        self._updating = False
        self._editors: dict[DialogueEntrySelection, _CommitPlainTextEdit] = {}
        self._metadata_path: tuple[str | int, ...] | None = None

        self.title = QLabel("Dialogue")
        self.title.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.sources = QListWidget()
        self.source_list = self.sources
        self.sources.setMinimumWidth(210)
        self.sources.currentItemChanged.connect(self._source_changed)

        self.add_button = QPushButton("+ Dialogue")
        self.remove_button = QPushButton("Remove")
        self.duplicate_button = QPushButton("Duplicate")
        self.move_up_button = QPushButton("Move Up")
        self.move_down_button = QPushButton("Move Down")
        self.add_button.clicked.connect(self.add_dialogue)
        self.remove_button.clicked.connect(self.remove_dialogue)
        self.duplicate_button.clicked.connect(self.duplicate_dialogue)
        self.move_up_button.clicked.connect(lambda: self.move_dialogue(-1))
        self.move_down_button.clicked.connect(lambda: self.move_dialogue(1))

        self.new_sequence_button = QPushButton("+ New Sequence")
        self.duplicate_sequence_button = QPushButton("Duplicate Sequence")
        self.delete_sequence_button = QPushButton("Delete Sequence")
        self.new_sequence_button.clicked.connect(self.add_sequence)
        self.duplicate_sequence_button.clicked.connect(self.duplicate_sequence)
        self.delete_sequence_button.clicked.connect(self.remove_sequence)

        self.entries_container = QWidget()
        self.entries_layout = QVBoxLayout(self.entries_container)
        self.entries_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.entries_scroll = QScrollArea()
        self.entries_scroll.setWidgetResizable(True)
        self.entries_scroll.setWidget(self.entries_container)
        self.empty_label = QLabel("No dialogue in this scene.\n\nUse + Dialogue to add a scene-entry line.")
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.hide()

        self.reference_label = QLabel()
        self.reference_label.setWordWrap(True)
        self.reference_label.setStyleSheet("color: #667085;")
        self.open_reference_button = QPushButton("Open Referenced Sequence")
        self.open_reference_button.clicked.connect(self._open_referenced_sequence)

        self.condition_editor = ConditionEditorWidget(parent=self)
        self.condition_mode = self.condition_editor.condition_mode
        self.condition_text = self.condition_editor.condition_text
        self.condition_json = self.condition_editor.condition_json
        self.condition_status = self.condition_editor.condition_status
        self.condition_editor.condition_changed.connect(self._condition_changed)
        self.once_check = QCheckBox("Play once")
        self.once_check.toggled.connect(self._once_changed)
        self.metadata_summary = QLabel()
        self.metadata_summary.setWordWrap(True)

        self.actions_list = QListWidget()
        self.actions_list.setMinimumHeight(90)
        self.actions_list.currentRowChanged.connect(self._action_row_changed)
        self.add_action_button = QPushButton("+ Add Action")
        self.remove_action_button = QPushButton("Remove Action")
        self.duplicate_action_button = QPushButton("Duplicate Action")
        self.move_action_up_button = QPushButton("Move Up")
        self.move_action_down_button = QPushButton("Move Down")
        self.add_action_button.clicked.connect(self.add_action)
        self.remove_action_button.clicked.connect(self.remove_action)
        self.duplicate_action_button.clicked.connect(self.duplicate_action)
        self.move_action_up_button.clicked.connect(lambda: self.move_action(-1))
        self.move_action_down_button.clicked.connect(lambda: self.move_action(1))
        self.action_type_combo = QComboBox()
        for spec in action_editor_specs(ActionScope.EXPLORATION):
            self.action_type_combo.addItem(spec.display_name, spec.type)
        self.action_fields = QWidget()
        self.action_fields_layout = QFormLayout(self.action_fields)
        self._action_field_widgets: dict[str, QWidget] = {}
        self.action_status = QLabel()
        self.action_status.setWordWrap(True)
        self.action_status.setStyleSheet("color: #b45309;")
        actions_box = QGroupBox("Actions")
        actions_layout = QVBoxLayout(actions_box)
        actions_layout.addWidget(self.actions_list)
        action_buttons = QHBoxLayout()
        for button in (self.add_action_button, self.remove_action_button, self.duplicate_action_button,
                       self.move_action_up_button, self.move_action_down_button):
            action_buttons.addWidget(button)
        actions_layout.addLayout(action_buttons)
        actions_layout.addWidget(self.action_type_combo)
        actions_layout.addWidget(self.action_fields)
        actions_layout.addWidget(self.action_status)

        metadata = QGroupBox("Entry metadata")
        metadata_form = QFormLayout(metadata)
        metadata_form.addRow(self.condition_editor)
        metadata_form.addRow(self.once_check)
        metadata_form.addRow(self.metadata_summary)

        buttons = QHBoxLayout()
        for button in (self.add_button, self.remove_button, self.duplicate_button, self.move_up_button, self.move_down_button):
            buttons.addWidget(button)
        buttons.addStretch(1)

        sequence_buttons = QHBoxLayout()
        for button in (self.new_sequence_button, self.duplicate_sequence_button, self.delete_sequence_button):
            sequence_buttons.addWidget(button)
        sequence_buttons.addStretch(1)

        detail = QVBoxLayout()
        detail.addWidget(self.title)
        detail.addLayout(sequence_buttons)
        detail.addLayout(buttons)
        detail.addWidget(self.reference_label)
        detail.addWidget(self.open_reference_button)
        detail.addWidget(self.entries_scroll, 1)
        detail.addWidget(self.empty_label)
        detail.addWidget(metadata)
        detail.addWidget(actions_box)

        body = QHBoxLayout()
        body.addWidget(self.sources)
        content = QWidget()
        content.setLayout(detail)
        body.addWidget(content, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(body)
        self.clear()

    def clear(self) -> None:
        self._updating = True
        self.project = None
        self.scene_id = None
        self.document = None
        self.selected_source_id = None
        self.selected_entry = None
        self.selected_action_index = None
        self.sources.clear()
        self._clear_cards()
        self.reference_label.clear()
        self.open_reference_button.hide()
        self.empty_label.setText("No dialogue in this scene.")
        self.empty_label.show()
        self._clear_metadata()
        self._clear_action_editor()
        self._updating = False
        self._update_enabled_state()

    def set_scene(self, project: StoryProject | None, scene_id: str | None, mapping: Mapping[str, Any] | None) -> None:
        if project is None or scene_id is None or mapping is None:
            self.clear()
            return
        previous_source = self.selected_source_id
        previous_entry = self.selected_entry
        self.project = project
        self.scene_id = str(scene_id)
        self.condition_editor.set_project(project)
        self.document = DialogueDocumentModel(self.scene_id, mapping)
        self._updating = True
        self.sources.clear()
        for source in self.document.sources:
            label = source.label
            if source.referenced_by:
                label += f"  ({len(source.referenced_by)} refs)"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, source.id)
            self.sources.addItem(item)
        wanted_source = previous_source if previous_source and self.document.source(previous_source) else None
        row = self._source_row(wanted_source) if wanted_source else 0
        if self.sources.count():
            self.sources.setCurrentRow(max(0, row if row is not None else 0))
            current = self.sources.currentItem()
            self.selected_source_id = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        self._updating = False
        selected = previous_entry if previous_entry and self._entry_exists(previous_entry) else None
        self._render_source(selected)
        self._update_enabled_state()

    def select_source(self, source_id: str) -> bool:
        row = self._source_row(source_id)
        if row is None:
            return False
        self.sources.setCurrentRow(row)
        return True

    def select_entry(self, selection: DialogueEntrySelection | None) -> bool:
        if selection is None:
            self.selected_entry = None
            self._populate_metadata()
            return True
        if self.document is None or self.document.source(selection.source_id) is None:
            return False
        self.selected_source_id = selection.source_id
        row = self._source_row(selection.source_id)
        if row is not None:
            self.sources.setCurrentRow(row)
        if not self._entry_exists(selection):
            return False
        self.selected_entry = selection
        self._render_source(selection)
        self.entry_selected.emit(selection)
        return True

    def add_dialogue(self) -> bool:
        if self.session is None or self.scene_id is None or self.document is None:
            return False
        source = self._current_source()
        if source is None:
            return False
        collection_path = self._entry_collection_path(source)
        if collection_path is None:
            self._show_error("This dialogue source is a scalar; preserve its authored shape before adding entries.")
            return False
        selection = self._scene_selection()
        existing = _path_value(self.session.working_mapping(selection) or {}, collection_path)
        index = len(existing) if isinstance(existing, list) else None
        try:
            entry_value = {"text": ""} if source.kind == "scene_entry" else ""
            command = InsertDialogueEntryCommand(selection, collection_path, entry_value, index=index)
            self.session.apply_command(command)
        except (EditValidationError, KeyError, TypeError, ValueError) as exc:
            self._show_error(str(exc))
            return False
        new_selection = DialogueEntrySelection(self.scene_id, source.id, collection_path + (command.index or 0,), command.index or 0)
        self._after_change(new_selection)
        return True

    def _sequences_path(self) -> tuple[str | int, ...]:
        if self.document is not None:
            for source in self.document.sources:
                if source.kind == "sequence":
                    return source.collection_path[:-1]
        mapping = self.session.working_mapping(self._scene_selection()) if self.session is not None else {}
        if isinstance(mapping, Mapping) and isinstance(mapping.get("exploration"), Mapping):
            return ("exploration", "dialogue_sequences")
        return ("dialogue_sequences",)

    def add_sequence(self) -> bool:
        if self.session is None or self.scene_id is None:
            return False
        try:
            command = InsertNamedDialogueSequenceCommand(
                self._scene_selection(), self._sequences_path(), sequence={"text": ""},
            )
            self.session.apply_command(command)
        except (EditValidationError, KeyError, TypeError, ValueError) as exc:
            self._show_error(str(exc))
            return False
        self._refresh_after_sequence_change(f"sequence:{command.sequence_id}")
        return True

    def duplicate_sequence(self) -> bool:
        source = self._current_source()
        if self.session is None or source is None or source.kind != "sequence":
            return False
        try:
            command = DuplicateNamedDialogueSequenceCommand(
                self._scene_selection(), self._sequences_path(), source.id.split(":", 1)[1],
            )
            self.session.apply_command(command)
        except (EditValidationError, KeyError, TypeError, ValueError) as exc:
            self._show_error(str(exc))
            return False
        self._refresh_after_sequence_change(f"sequence:{command.duplicate_id}")
        return True

    def remove_sequence(self) -> bool:
        source = self._current_source()
        if self.session is None or source is None or source.kind != "sequence":
            return False
        try:
            command = RemoveNamedDialogueSequenceCommand(
                self._scene_selection(), self._sequences_path(), source.id.split(":", 1)[1],
            )
            self.session.apply_command(command)
        except (EditValidationError, KeyError, TypeError, ValueError) as exc:
            self._show_error(str(exc))
            return False
        self._refresh_after_sequence_change("scene_entry")
        return True

    def _refresh_after_sequence_change(self, source_id: str | None) -> None:
        mapping = self.session.working_mapping(self._scene_selection()) if self.session is not None else None
        self.set_scene(self.project, self.scene_id, mapping)
        if source_id and self.document is not None and self.document.source(source_id) is not None:
            self.select_source(source_id)

    def remove_dialogue(self) -> bool:
        context = self._structural_context()
        if context is None:
            return False
        selection, source, entry_index, collection_path = context
        try:
            self.session.apply_command(RemoveDialogueEntryCommand(selection, collection_path, entry_index))
        except (EditValidationError, KeyError, TypeError, ValueError) as exc:
            self._show_error(str(exc))
            return False
        mapping = self.session.working_mapping(selection) or {}
        entries = _path_value(mapping, collection_path)
        next_selection = None
        if isinstance(entries, list) and entries:
            index = min(entry_index, len(entries) - 1)
            next_selection = DialogueEntrySelection(self.scene_id or "", source.id, collection_path + (index,), index)
        self._after_change(next_selection)
        return True

    def duplicate_dialogue(self) -> bool:
        context = self._structural_context()
        if context is None:
            return False
        selection, source, entry_index, collection_path = context
        try:
            command = DuplicateDialogueEntryCommand(selection, collection_path, entry_index)
            self.session.apply_command(command)
        except (EditValidationError, KeyError, TypeError, ValueError) as exc:
            self._show_error(str(exc))
            return False
        index = command.duplicate_index if command.duplicate_index is not None else entry_index + 1
        self._after_change(DialogueEntrySelection(self.scene_id or "", source.id, collection_path + (index,), index))
        return True

    def move_dialogue(self, delta: int) -> bool:
        context = self._structural_context()
        if context is None:
            return False
        selection, source, entry_index, collection_path = context
        mapping = self.session.working_mapping(selection) or {}
        entries = _path_value(mapping, collection_path)
        if not isinstance(entries, list):
            return False
        new_index = max(0, min(len(entries) - 1, entry_index + int(delta)))
        if new_index == entry_index:
            return False
        try:
            self.session.apply_command(MoveDialogueEntryCommand(selection, collection_path, entry_index, new_index))
        except (EditValidationError, KeyError, TypeError, ValueError) as exc:
            self._show_error(str(exc))
            return False
        self._after_change(DialogueEntrySelection(self.scene_id or "", source.id, collection_path + (new_index,), new_index))
        return True

    def _commit_text(self, selection: DialogueEntrySelection, editor: _CommitPlainTextEdit) -> None:
        if self._updating or self.session is None or self.scene_id is None or self.document is None:
            return
        entry = self._entry(selection)
        if entry is None or entry.text_path is None or editor.toPlainText() == entry.text:
            return
        try:
            self.session.apply_command(SetDialogueTextCommand(self._scene_selection(), entry.text_path, editor.toPlainText()))
        except (EditValidationError, KeyError, TypeError, ValueError) as exc:
            self._show_error(str(exc))
            return
        self._after_change(selection)

    def _source_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if self._updating:
            return
        self.selected_source_id = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        self.selected_entry = None
        self._render_source(None)

    def _render_source(self, preferred: DialogueEntrySelection | None) -> None:
        if self.document is None:
            return
        source = self._current_source()
        self.selected_source_id = source.id if source is not None else self.selected_source_id
        self._clear_cards()
        self._editors.clear()
        self.reference_label.clear()
        self.open_reference_button.hide()
        entries = source.entries if source is not None else ()
        self.empty_label.setVisible(not entries)
        if source is not None and source.kind == "sequence" and source.referenced_by:
            self.reference_label.setText("Referenced by: " + ", ".join(source.referenced_by))
        if not entries:
            self.empty_label.setText("No dialogue in this scene.\n\nUse + Dialogue to add a scene-entry line.")
        selected = preferred if preferred and any(item.selection == preferred for item in entries) else (entries[0].selection if entries else None)
        self.selected_entry = selected
        self.selected_action_index = None
        for number, entry in enumerate(entries, 1):
            card = QGroupBox(f"{number}")
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            card_layout = QVBoxLayout(card)
            if entry.supported and entry.text_path is not None:
                editor = _CommitPlainTextEdit()
                editor.setPlainText(entry.text or "")
                editor.setMinimumHeight(78)
                editor.setMaximumHeight(160)
                editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
                editor.focused.connect(lambda item=entry.selection: self._select_card(item))
                editor.commit_requested.connect(lambda item=entry.selection, widget=editor: self._commit_text(item, widget))
                card_layout.addWidget(editor)
                self._editors[entry.selection] = editor
            else:
                summary = QPlainTextEdit(_safe_summary(entry.authored))
                summary.setReadOnly(True)
                summary.setMinimumHeight(78)
                card_layout.addWidget(summary)
                reference = _entry_sequence_reference(entry.authored)
                if reference is not None:
                    button = QPushButton(f"Open sequence: {reference}")
                    button.clicked.connect(lambda _checked=False, value=reference: self.open_dialogue_sequence.emit(value))
                    card_layout.addWidget(button)
                    if self.document.source(f"sequence:{reference}") is not None:
                        self.open_reference_button.show()
            if entry.unsupported_reason:
                warning = QLabel(entry.unsupported_reason)
                warning.setWordWrap(True)
                warning.setStyleSheet("color: #b45309;")
                card_layout.addWidget(warning)
            self.entries_layout.addWidget(card)
        self._populate_metadata()
        self._update_card_styles()
        self._update_enabled_state()

    def _select_card(self, selection: DialogueEntrySelection) -> None:
        if self._updating:
            return
        self.selected_entry = selection
        self._populate_metadata()
        self._update_card_styles()
        self.entry_selected.emit(selection)

    def _populate_metadata(self) -> None:
        self._updating = True
        self._metadata_path = None
        entry = self._entry(self.selected_entry)
        raw = _path_value(self.session.working_mapping(self._scene_selection()) or {}, self._metadata_path_for(entry)) if entry is not None and self.session is not None else None
        self._metadata_path = self._metadata_path_for(entry)
        if not isinstance(raw, Mapping):
            self._clear_metadata()
            self._clear_action_editor()
            self._updating = False
            return
        condition = raw.get("conditions", raw.get("condition", MISSING))
        self.condition_editor.set_condition(condition, project=self.project)
        self.once_check.setChecked(bool(raw.get("once", False)))
        actions = raw.get("actions")
        self.metadata_summary.setText(
            f"Actions preserved: {len(actions)}" if isinstance(actions, list) else "Unknown authored fields are preserved."
        )
        self._updating = False
        self._populate_actions(raw.get("actions"), self._metadata_path + ("actions",) if self._metadata_path is not None else None)
        self._update_enabled_state()

    def _clear_metadata(self) -> None:
        self.condition_editor.set_condition(MISSING, project=self.project)
        self.once_check.setChecked(False)
        self.metadata_summary.clear()

    def _clear_action_editor(self) -> None:
        self.actions_list.clear()
        self.selected_action_index = None
        self.action_status.clear()
        while self.action_fields_layout.count():
            item = self.action_fields_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._action_field_widgets.clear()

    def _populate_actions(self, raw: Any, actions_path: tuple[str | int, ...] | None) -> None:
        self._updating = True
        self.actions_list.clear()
        self.selected_action_index = None
        if actions_path is None:
            self._clear_action_editor()
            self._updating = False
            return
        for action in present_dialogue_actions(raw, actions_path):
            label = f"{action.index + 1}. {action.display_name}"
            if action.summary:
                label += f" — {action.summary}"
            if not action.supported:
                label += " [read-only]"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, action.index)
            self.actions_list.addItem(item)
        self._updating = False
        if self.actions_list.count():
            self.actions_list.setCurrentRow(min(self.selected_action_index or 0, self.actions_list.count() - 1))
        else:
            self._render_action_fields(None)

    def _action_context(self):
        if self.session is None or self.selected_entry is None:
            return None
        entry = self._entry(self.selected_entry)
        metadata_path = self._metadata_path_for(entry)
        if metadata_path is None:
            return None
        actions = _path_value(self.session.working_mapping(self._scene_selection()) or {}, metadata_path + ("actions",))
        if actions is MISSING:
            actions = []
        return metadata_path + ("actions",), actions

    def _action_row_changed(self, row: int) -> None:
        if self._updating:
            return
        self.selected_action_index = row if row >= 0 else None
        context = self._action_context()
        action = context[1][row] if context and isinstance(context[1], list) and 0 <= row < len(context[1]) else None
        self._render_action_fields(action)
        self._update_enabled_state()

    def _render_action_fields(self, action: Any) -> None:
        while self.action_fields_layout.count():
            item = self.action_fields_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._action_field_widgets.clear()
        if not isinstance(action, Mapping) or not isinstance(action.get("type"), str):
            self.action_status.setText("Select a supported typed action to edit its parameters.") if action is not None else self.action_status.clear()
            return
        spec = action_editor_spec(action["type"], ActionScope.EXPLORATION)
        if spec is None:
            self.action_status.setText("This action is preserved as raw authored data and has no safe editor.")
            return
        for field in spec.fields:
            widget = self._make_action_field(field, action.get(field.key, field.default))
            self._action_field_widgets[field.key] = widget
            self.action_fields_layout.addRow(field.display_name, widget)
        self.action_status.clear()

    def _make_action_field(self, field, value: Any) -> QWidget:
        if field.asset_kind:
            widget = AssetPathEditor(
                story_root=self.project.story_root if self.project is not None else None,
                source=self.project.source if self.project is not None else None,
                project=self.project,
                asset_kind=field.asset_kind,
            )
            widget.setText("" if value is None else str(value))
            widget.value_edited.connect(lambda edited, key=field.key: self._commit_action_field(key, edited))
            return widget
        if field.kind == "boolean":
            widget = QCheckBox()
            widget.setChecked(bool(value))
            widget.toggled.connect(lambda checked, key=field.key: self._commit_action_field(key, bool(checked)))
            return widget
        if field.kind == "integer":
            from PySide6.QtWidgets import QSpinBox
            widget = QSpinBox()
            widget.setRange(-2_147_483_648, 2_147_483_647)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                widget.setValue(int(value))
            widget.valueChanged.connect(lambda number, key=field.key: self._commit_action_field(key, int(number)))
            return widget
        if field.kind == "number":
            from PySide6.QtWidgets import QDoubleSpinBox
            widget = QDoubleSpinBox()
            widget.setRange(-1_000_000_000.0, 1_000_000_000.0)
            widget.setDecimals(3)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                widget.setValue(float(value))
            widget.valueChanged.connect(lambda number, key=field.key: self._commit_action_field(key, float(number)))
            return widget
        if field.kind == "reference":
            widget = QComboBox()
            candidates = self._reference_candidates(field.reference_target)
            current = value if isinstance(value, str) else ""
            if current and current not in candidates:
                widget.addItem(f"{current} ⚠", current)
            widget.addItem("(unresolved)", "")
            for candidate in candidates:
                widget.addItem(candidate, candidate)
            widget.setCurrentIndex(max(0, widget.findData(current)))
            widget.currentIndexChanged.connect(lambda _index, key=field.key, combo=widget: self._commit_action_field(key, combo.currentData()))
            return widget
        widget = QLineEdit()
        widget.setText("" if value is None else str(value))
        widget.editingFinished.connect(lambda key=field.key, line=widget: self._commit_action_field(key, line.text()))
        return widget

    def _reference_candidates(self, target: str | None) -> tuple[str, ...]:
        if target == "scene_object":
            mapping = self.session.working_mapping(self._scene_selection()) if self.session is not None else {}
            config = mapping.get("exploration", {}) if isinstance(mapping, Mapping) and isinstance(mapping.get("exploration"), Mapping) else mapping
            result: list[str] = []
            for key in ("objects", "look_regions"):
                values = config.get(key, []) if isinstance(config, Mapping) else []
                if isinstance(values, list):
                    result.extend(str(value["id"]) for value in values if isinstance(value, Mapping) and isinstance(value.get("id"), str))
            return tuple(dict.fromkeys(result))
        if self.project is None or self.project.index is None or target is None:
            return ()
        try:
            return tuple(reference.identifier for reference in self.project.index.references(ContentKind.coerce(target)))
        except (TypeError, ValueError):
            return ()

    def _commit_action_field(self, key: str, value: Any) -> None:
        if self._updating or self.selected_action_index is None:
            return
        context = self._action_context()
        if context is None:
            return
        actions_path, actions = context
        if not isinstance(actions, list) or not 0 <= self.selected_action_index < len(actions):
            return
        try:
            self.session.apply_command(SetDialogueActionParameterCommand(
                self._scene_selection(), actions_path + (self.selected_action_index,), key, value,
            ))
        except (EditValidationError, KeyError, TypeError, ValueError) as exc:
            self.action_status.setText(str(exc))
            return
        self._after_change(self.selected_entry)

    def add_action(self) -> bool:
        if self.session is None:
            return False
        context = self._action_context()
        if context is None:
            self._show_error("This dialogue shape has no editable action list.")
            return False
        actions_path, actions = context
        action_type = self.action_type_combo.currentData()
        try:
            command = InsertDialogueActionCommand(self._scene_selection(), actions_path, minimal_authored_action(action_type))
            self.session.apply_command(command)
        except (EditValidationError, KeyError, TypeError, ValueError) as exc:
            self._show_error(str(exc))
            return False
        self.selected_action_index = command.index
        self._after_change(self.selected_entry)
        return True

    def remove_action(self) -> bool:
        context = self._action_context()
        if context is None or self.selected_action_index is None:
            return False
        actions_path, _actions = context
        try:
            self.session.apply_command(RemoveDialogueActionCommand(self._scene_selection(), actions_path, self.selected_action_index))
        except (EditValidationError, KeyError, TypeError, ValueError) as exc:
            self._show_error(str(exc))
            return False
        self._after_change(self.selected_entry)
        return True

    def duplicate_action(self) -> bool:
        context = self._action_context()
        if context is None or self.selected_action_index is None:
            return False
        actions_path, _actions = context
        try:
            command = DuplicateDialogueActionCommand(self._scene_selection(), actions_path, self.selected_action_index)
            self.session.apply_command(command)
        except (EditValidationError, KeyError, TypeError, ValueError) as exc:
            self._show_error(str(exc))
            return False
        self.selected_action_index = command.duplicate_index
        self._after_change(self.selected_entry)
        return True

    def move_action(self, delta: int) -> bool:
        context = self._action_context()
        if context is None or self.selected_action_index is None or not isinstance(context[1], list):
            return False
        actions_path, actions = context
        new_index = max(0, min(len(actions) - 1, self.selected_action_index + int(delta)))
        if new_index == self.selected_action_index:
            return False
        try:
            self.session.apply_command(MoveDialogueActionCommand(self._scene_selection(), actions_path, self.selected_action_index, new_index))
        except (EditValidationError, KeyError, TypeError, ValueError) as exc:
            self._show_error(str(exc))
            return False
        self.selected_action_index = new_index
        self._after_change(self.selected_entry)
        return True

    def _condition_changed(self, value: Any) -> None:
        if self._updating:
            return
        self._commit_condition(value)

    def _commit_condition(self, value: Any) -> None:
        if self.session is None or self._metadata_path is None:
            return
        try:
            self.session.apply_command(SetDialogueConditionCommand(self._scene_selection(), self._metadata_path, value))
        except (EditValidationError, KeyError, TypeError, ValueError) as exc:
            self._show_error(str(exc))
            return
        self._after_change(self.selected_entry)

    def _once_changed(self, value: bool) -> None:
        if self._updating or self.session is None or self._metadata_path is None:
            return
        try:
            self.session.apply_command(SetDialogueMetadataCommand(self._scene_selection(), self._metadata_path, "once", bool(value)))
        except (EditValidationError, KeyError, TypeError, ValueError) as exc:
            self._show_error(str(exc))
            return
        self._after_change(self.selected_entry)

    def _after_change(self, selected: DialogueEntrySelection | None) -> None:
        if self.project is None or self.scene_id is None or self.session is None:
            return
        self.selected_entry = selected
        mapping = self.session.working_mapping(self._scene_selection())
        self.set_scene(self.project, self.scene_id, mapping)
        self.dialogue_changed.emit(selected)

    def _structural_context(self):
        if self.session is None or self.scene_id is None or self.document is None or self.selected_entry is None:
            return None
        source = self._current_source()
        collection_path = self._entry_collection_path(source) if source is not None else None
        if source is None or collection_path is None:
            return None
        component = self.selected_entry.path[-1]
        if not isinstance(component, int):
            return None
        return self._scene_selection(), source, component, collection_path

    def _current_source(self):
        return self.document.source(self.selected_source_id) if self.document is not None and self.selected_source_id else None

    def _entry(self, selection: DialogueEntrySelection | None):
        source = self.document.source_for_selection(selection) if self.document is not None else None
        if source is None or selection is None:
            return None
        return next((entry for entry in source.entries if entry.selection == selection), None)

    def _entry_exists(self, selection: DialogueEntrySelection) -> bool:
        return self._entry(selection) is not None

    def _entry_collection_path(self, source) -> tuple[str | int, ...] | None:
        if source is None:
            return None
        mapping = self.session.working_mapping(self._scene_selection()) if self.session is not None else None
        raw = _path_value(mapping or {}, source.collection_path)
        if isinstance(raw, list):
            return source.collection_path
        if raw is MISSING and source.kind == "scene_entry":
            return source.collection_path
        for entry in source.entries:
            path = entry.text_path
            if path is not None and isinstance(_path_value(mapping or {}, path[:-1]), list):
                return path[:-1]
        return None

    def _metadata_path_for(self, entry) -> tuple[str | int, ...] | None:
        if entry is None:
            return None
        mapping = self.session.working_mapping(self._scene_selection()) if self.session is not None else None
        if isinstance(_path_value(mapping or {}, entry.selection.path), Mapping):
            return entry.selection.path
        path = entry.selection.path
        if len(path) >= 2 and isinstance(path[-1], int) and isinstance(_path_value(mapping or {}, path[:-1]), list):
            candidate = path[:-1]
            return candidate if isinstance(_path_value(mapping or {}, candidate), Mapping) else None
        return None

    def _scene_selection(self) -> DefinitionSelection:
        current = self.session.selection if self.session is not None else None
        return current if current is not None else DefinitionSelection("scene", self.scene_id or "")

    def _source_row(self, source_id: str | None) -> int | None:
        if source_id is None:
            return None
        for row in range(self.sources.count()):
            if self.sources.item(row).data(Qt.ItemDataRole.UserRole) == source_id:
                return row
        return None

    def _clear_cards(self) -> None:
        while self.entries_layout.count():
            item = self.entries_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _update_card_styles(self) -> None:
        for row in range(self.entries_layout.count()):
            card = self.entries_layout.itemAt(row).widget()
            if card is None:
                continue
            selection = self._current_source().entries[row].selection if self._current_source() and row < len(self._current_source().entries) else None
            card.setStyleSheet("QGroupBox { border: 2px solid #4f8cff; }" if selection == self.selected_entry else "")

    def _update_enabled_state(self) -> None:
        source = self._current_source()
        selected = self.selected_entry is not None
        can_structure = bool(source is not None and self._entry_collection_path(source) is not None and selected)
        self.add_button.setEnabled(source is not None and self._entry_collection_path(source) is not None)
        self.remove_button.setEnabled(can_structure)
        self.duplicate_button.setEnabled(can_structure)
        self.move_up_button.setEnabled(can_structure and self.selected_entry is not None and self.selected_entry.index > 0)
        self.move_down_button.setEnabled(can_structure and self.selected_entry is not None and source is not None and self.selected_entry.index < len(source.entries) - 1)
        has_metadata = self._metadata_path is not None
        self.condition_mode.setEnabled(has_metadata)
        self.condition_editor.setEnabled(has_metadata)
        self.once_check.setEnabled(has_metadata)
        is_sequence = bool(source is not None and source.kind == "sequence")
        self.duplicate_sequence_button.setEnabled(is_sequence)
        self.delete_sequence_button.setEnabled(is_sequence)
        action_context = self._action_context()
        has_actions = bool(action_context is not None)
        has_selected_action = has_actions and self.selected_action_index is not None
        self.add_action_button.setEnabled(has_actions)
        self.remove_action_button.setEnabled(has_selected_action)
        self.duplicate_action_button.setEnabled(has_selected_action)
        action_count = len(action_context[1]) if action_context is not None and isinstance(action_context[1], list) else 0
        self.move_action_up_button.setEnabled(has_selected_action and bool(self.selected_action_index and self.selected_action_index > 0))
        self.move_action_down_button.setEnabled(has_selected_action and bool(self.selected_action_index is not None and self.selected_action_index < action_count - 1))

    def _open_referenced_sequence(self) -> None:
        entry = self._entry(self.selected_entry)
        reference = _entry_sequence_reference(entry.authored if entry else None)
        if reference is not None:
            self.open_dialogue_sequence.emit(reference)

    def _show_error(self, message: str) -> None:
        self.condition_status.setText(str(message))


def _path_value(mapping: Any, path: tuple[str | int, ...] | None) -> Any:
    if path is None:
        return MISSING
    current = mapping
    for component in path:
        if isinstance(current, Mapping) and component in current:
            current = current[component]
        elif isinstance(current, list) and isinstance(component, int) and 0 <= component < len(current):
            current = current[component]
        else:
            return MISSING
    return current


def _safe_summary(value: Any) -> str:
    try:
        return json.dumps(value, indent=2, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(value)


def _entry_sequence_reference(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    for key in ("sequence", "dialog"):
        if isinstance(value.get(key), str):
            return value[key]
    return None


__all__ = ["DialogueEditorWidget"]
