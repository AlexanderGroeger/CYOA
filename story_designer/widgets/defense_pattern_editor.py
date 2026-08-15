"""Generated, authoring-focused editor for battle defense patterns."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from engine.story_core import Diagnostics, StoryProject
from engine.story_core.schema import MISSING

from ..models import (
    BattleElementSelection,
    DefensePatternEditorModel,
    DefensePatternPresentation,
    DefinitionSelection,
    EditValidationError,
    InsertDefensePatternCommand,
    InsertDefenseSequenceCommand,
    DuplicateDefensePatternCommand,
    MoveDefensePatternCommand,
    MoveDefenseSequenceCommand,
    ProjectSession,
    RemoveDefensePatternCommand,
    SetDefensePatternParameterCommand,
    SetDefensePatternTypeCommand,
)
from ..models.editing import PropertyDescriptor
from .property_editors import PropertyEditorFactory


class DefensePatternEditorWidget(QWidget):
    """A small generated form over the runtime defense registry.

    The widget intentionally exposes semantic JSON for groups, legacy timeline
    entries, and fields without metadata. Those values are never rewritten by
    the generated controls.
    """

    changed = Signal(object)

    def __init__(self, session: ProjectSession | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.project: StoryProject | None = None
        self.selection: DefinitionSelection | None = None
        self.model: DefensePatternEditorModel | None = None
        self._pattern: DefensePatternPresentation | None = None
        self._editors: dict[tuple[str | int, ...], QWidget] = {}
        self._initializing = False
        self._factory = PropertyEditorFactory()

        self.title = QLabel("Defense Patterns")
        self.title.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.sequence_list = QListWidget()
        self.sequence_list.setMinimumWidth(145)
        self.sequence_list.currentRowChanged.connect(self._sequence_changed)
        self.pattern_list = QListWidget()
        self.pattern_list.setMinimumWidth(190)
        self.pattern_list.currentRowChanged.connect(self._pattern_changed)
        self.type_combo = QComboBox()
        self.type_combo.currentIndexChanged.connect(self._type_changed)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMaximumHeight(130)
        self.fields = QWidget()
        self.form = QFormLayout(self.fields)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.status = QLabel()
        self.status.setWordWrap(True)

        self.add_sequence_button = QPushButton("+ Sequence")
        self.sequence_up_button = QPushButton("Sequence Up")
        self.sequence_down_button = QPushButton("Sequence Down")
        self.add_pattern_button = QPushButton("+ Add Pattern")
        self.duplicate_button = QPushButton("Duplicate")
        self.delete_button = QPushButton("Delete")
        self.up_button = QPushButton("Move Up")
        self.down_button = QPushButton("Move Down")
        self.add_sequence_button.clicked.connect(self._add_sequence_dialog)
        self.sequence_up_button.clicked.connect(lambda: self.move_sequence(-1))
        self.sequence_down_button.clicked.connect(lambda: self.move_sequence(1))
        self.add_pattern_button.clicked.connect(self._add_pattern_dialog)
        self.duplicate_button.clicked.connect(self.duplicate_pattern)
        self.delete_button.clicked.connect(self.delete_pattern)
        self.up_button.clicked.connect(lambda: self.move_pattern(-1))
        self.down_button.clicked.connect(lambda: self.move_pattern(1))

        sequence_buttons = QHBoxLayout()
        sequence_buttons.addWidget(self.add_sequence_button)
        sequence_buttons.addWidget(self.sequence_up_button)
        sequence_buttons.addWidget(self.sequence_down_button)
        pattern_buttons = QHBoxLayout()
        for button in (self.add_pattern_button, self.duplicate_button, self.delete_button, self.up_button, self.down_button):
            pattern_buttons.addWidget(button)

        detail = QVBoxLayout()
        detail.addWidget(QLabel("Pattern Type"))
        detail.addWidget(self.type_combo)
        detail.addWidget(self.summary)
        detail.addWidget(self.fields)
        detail.addWidget(self.status)

        lists = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(QLabel("Sequences"))
        left.addWidget(self.sequence_list, 1)
        left.addLayout(sequence_buttons)
        middle = QVBoxLayout()
        middle.addWidget(QLabel("Execution Order"))
        middle.addWidget(self.pattern_list, 1)
        middle.addLayout(pattern_buttons)
        lists.addLayout(left, 1)
        lists.addLayout(middle, 2)
        lists.addLayout(detail, 3)
        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addLayout(lists, 1)
        self.clear()

    @property
    def current_sequence(self):
        if self.model is None:
            return None
        row = self.sequence_list.currentRow()
        return self.model.sequences[row] if 0 <= row < len(self.model.sequences) else None

    @property
    def current_pattern(self) -> DefensePatternPresentation | None:
        return self._pattern

    def clear(self) -> None:
        self.model = None
        self.project = None
        self.selection = None
        self._pattern = None
        self.title.setText("Defense Patterns")
        self.sequence_list.clear()
        self.pattern_list.clear()
        self.type_combo.clear()
        self.summary.clear()
        self._clear_fields()
        self.status.clear()

    def set_state(
        self,
        project: StoryProject | None,
        selection: DefinitionSelection | None,
        mapping: dict[str, Any] | None,
        diagnostics: Diagnostics | None = None,
    ) -> None:
        if project is None or selection is None or mapping is None:
            self.clear()
            return
        previous_sequence = self.current_sequence.sequence_id if self.current_sequence is not None else None
        previous_pattern_path = self._pattern.selection.path if self._pattern is not None else None
        self.project, self.selection = project, selection
        self.model = DefensePatternEditorModel(selection.id, mapping, diagnostics or ())
        self.title.setText("Defense Patterns")
        self._populate_sequences(previous_sequence, previous_pattern_path)

    def _populate_sequences(self, previous_id: str | None, previous_pattern_path: tuple[str | int, ...] | None) -> None:
        blocker = QSignalBlocker(self.sequence_list)
        self.sequence_list.clear()
        for sequence in self.model.sequences if self.model is not None else ():
            item = QListWidgetItem(sequence.label)
            item.setToolTip(sequence.summary)
            item.setData(32, sequence.selection)
            self.sequence_list.addItem(item)
        del blocker
        row = next((index for index, item in enumerate(self.model.sequences if self.model is not None else ()) if item.sequence_id == previous_id), 0)
        self.sequence_list.setCurrentRow(row if self.sequence_list.count() else -1)
        self._show_sequence(previous_pattern_path)

    def _sequence_changed(self, row: int) -> None:
        self._show_sequence(None)

    def _show_sequence(self, preferred_path: tuple[str | int, ...] | None) -> None:
        sequence = self.current_sequence
        blocker = QSignalBlocker(self.pattern_list)
        self.pattern_list.clear()
        self._pattern = None
        if sequence is not None:
            for pattern in sequence.patterns:
                item = QListWidgetItem(pattern.label)
                item.setToolTip(pattern.display_name)
                item.setData(32, pattern.selection)
                self.pattern_list.addItem(item)
        del blocker
        row = next((index for index, pattern in enumerate(sequence.patterns) if pattern.selection.path == preferred_path), 0) if sequence else -1
        self.pattern_list.setCurrentRow(row if self.pattern_list.count() else -1)
        if row >= 0 and sequence:
            self._show_pattern(sequence.patterns[row])
        else:
            self.summary.setPlainText(sequence.summary if sequence is not None else "Select a defense sequence.")
            self._clear_fields()

    def _pattern_changed(self, row: int) -> None:
        sequence = self.current_sequence
        pattern = sequence.patterns[row] if sequence is not None and 0 <= row < len(sequence.patterns) else None
        self._show_pattern(pattern)

    def _show_pattern(self, pattern: DefensePatternPresentation | None) -> None:
        self._pattern = pattern
        self._clear_fields()
        self._populate_type_combo(pattern)
        if pattern is None:
            self.summary.clear()
            return
        text = pattern.summary
        if not pattern.supported:
            text = f"Read-only / preserved payload\n\n{pattern.unsupported_reason}\n\n{text}"
        if pattern.diagnostics:
            text = f"Diagnostics: {len(pattern.diagnostics)}\n\n{text}"
        self.summary.setPlainText(text)
        if self.model is None or self.selection is None or not pattern.supported:
            return
        for field, path, value in self.model.field_entries(pattern.selection):
            descriptor = PropertyDescriptor(
                self.selection, path, field.key, field.field.display_name, field.field.description,
                field.field.type, field.field.required, field.field.default_value(), value, value, value is not MISSING,
                field.editor_hint != "read_only" and not field.field.read_only,
                field.editor_hint != "read_only" and not field.field.read_only,
                None if field.editor_hint != "read_only" else "Complex authored payload is preserved read-only.",
                field.field.minimum, field.field.maximum, tuple(field.field.allowed_values),
                field.field.reference_target, (), field.field.asset_kind, field.field.type.nullable, field.field,
            )
            editor = self._factory.create(descriptor, story_root=self.session.story_root if self.session else None, project=self.project)
            self._set_editor_value(editor, value)
            if hasattr(editor, "value_edited") and descriptor.is_editable:
                editor.value_edited.connect(lambda new_value, path=path, widget=editor: self._commit_field(path, new_value, widget))
            self._editors[path] = editor
            self.form.addRow(f"{field.group} · {field.field.display_name}", editor)

    def _populate_type_combo(self, pattern: DefensePatternPresentation | None) -> None:
        blocker = QSignalBlocker(self.type_combo)
        self.type_combo.clear()
        specs = self.model.registered_specs if self.model is not None else {}
        for type_name, spec in specs.items():
            if spec.supported:
                self.type_combo.addItem(spec.display_name + (f"  [{type_name}]" if type_name != spec.type else ""), type_name)
        if pattern is not None and pattern.type_name and self.type_combo.findData(pattern.type_name) < 0:
            self.type_combo.addItem(f"{pattern.type_name}  [unsupported]", pattern.type_name)
        if pattern is not None and pattern.type_name:
            self.type_combo.setCurrentIndex(self.type_combo.findData(pattern.type_name))
        # An unknown/legacy type may be replaced only through this explicit
        # action; loading and saving never normalizes it implicitly.
        self.type_combo.setEnabled(bool(pattern is not None and pattern.type_name is not None))
        del blocker

    @staticmethod
    def _set_editor_value(editor: QWidget, value: Any) -> None:
        blocker = QSignalBlocker(editor)
        if hasattr(editor, "set_initializing"):
            editor.set_initializing(True)
        try:
            if value is MISSING:
                return
            if hasattr(editor, "setText") and isinstance(value, str):
                editor.setText(value)
            elif hasattr(editor, "setValue") and isinstance(value, (int, float)) and not isinstance(value, bool):
                editor.setValue(value)
            elif hasattr(editor, "setChecked") and isinstance(value, bool):
                editor.setChecked(value)
            elif hasattr(editor, "findData"):
                index = editor.findData(value)
                if index >= 0:
                    editor.setCurrentIndex(index)
        finally:
            if hasattr(editor, "set_initializing"):
                editor.set_initializing(False)
            del blocker

    def _commit_field(self, path: tuple[str | int, ...], value: Any, editor: QWidget) -> None:
        if self.session is None or self.selection is None or self._pattern is None:
            return
        relative = path[len(self._pattern.selection.path):]
        try:
            self.session.apply_command(SetDefensePatternParameterCommand(self.selection, self._pattern.selection.path, relative, value))
        except EditValidationError as exc:
            self.status.setText(exc.message)
            self._set_editor_value(editor, self.model.mapping if self.model else MISSING)
            return
        self.status.setText("Defense parameter updated. The edit is undoable and remains pending until Save.")
        self.changed.emit(self._pattern.selection)

    def _type_changed(self, index: int) -> None:
        if self._initializing or index < 0 or self.session is None or self.selection is None or self._pattern is None:
            return
        type_name = self.type_combo.itemData(index)
        if not type_name or type_name == self._pattern.type_name:
            return
        try:
            self.session.apply_command(SetDefensePatternTypeCommand(self.selection, self._pattern.selection.path, type_name))
        except EditValidationError as exc:
            self.status.setText(exc.message)
            return
        self.status.setText(f"Pattern type changed to {type_name}.")
        self.changed.emit(self._pattern.selection)

    def add_pattern(self, type_name: str) -> bool:
        if self.session is None or self.selection is None or self.model is None or self.current_sequence is None:
            self.status.setText("Add or select a defense sequence first.")
            return False
        try:
            from engine.battle.defense_metadata import minimal_defense_pattern
            command = InsertDefensePatternCommand(self.selection, self.current_sequence.path, minimal_defense_pattern(type_name))
            self.session.apply_command(command)
        except (EditValidationError, KeyError, ValueError) as exc:
            self.status.setText(str(exc))
            return False
        self.status.setText(f"Added {type_name}.")
        self.changed.emit(self.current_sequence.selection)
        return True

    def duplicate_pattern(self) -> bool:
        if self.session is None or self.selection is None or self._pattern is None:
            return False
        sequence = self.current_sequence
        if sequence is None:
            return False
        try:
            self.session.apply_command(DuplicateDefensePatternCommand(self.selection, sequence.path, self.pattern_list.currentRow()))
        except EditValidationError as exc:
            self.status.setText(exc.message)
            return False
        self.status.setText("Pattern duplicated.")
        self.changed.emit(self._pattern.selection)
        return True

    def delete_pattern(self) -> bool:
        if self.session is None or self.selection is None or self._pattern is None:
            return False
        sequence = self.current_sequence
        if sequence is None:
            return False
        try:
            self.session.apply_command(RemoveDefensePatternCommand(self.selection, sequence.path, self.pattern_list.currentRow()))
        except EditValidationError as exc:
            self.status.setText(exc.message)
            return False
        self.status.setText("Pattern deleted.")
        self.changed.emit(sequence.selection)
        return True

    def move_pattern(self, offset: int) -> bool:
        if self.session is None or self.selection is None or self._pattern is None:
            return False
        sequence = self.current_sequence
        row = self.pattern_list.currentRow()
        if sequence is None or row < 0:
            return False
        try:
            self.session.apply_command(MoveDefensePatternCommand(self.selection, sequence.path, row, row + offset))
        except EditValidationError as exc:
            self.status.setText(exc.message)
            return False
        self.status.setText("Pattern order updated.")
        self.changed.emit(sequence.selection)
        return True

    def add_sequence(self, sequence_id: str = "defense") -> bool:
        if self.session is None or self.selection is None or self.model is None or not self.model.collection_path:
            return False
        try:
            self.session.apply_command(InsertDefenseSequenceCommand(self.selection, self.model.collection_path, {"id": sequence_id, "patterns": []}))
        except EditValidationError as exc:
            self.status.setText(exc.message)
            return False
        self.status.setText("Defense sequence added.")
        self.changed.emit(None)
        return True

    def move_sequence(self, offset: int) -> bool:
        if self.session is None or self.selection is None or self.model is None:
            return False
        row = self.sequence_list.currentRow()
        if row < 0 or not self.model.collection_path:
            return False
        try:
            self.session.apply_command(MoveDefenseSequenceCommand(self.selection, self.model.collection_path, row, row + offset))
        except EditValidationError as exc:
            self.status.setText(exc.message)
            return False
        self.status.setText("Defense sequence order updated.")
        self.changed.emit(None)
        return True

    def _add_pattern_dialog(self) -> None:
        if self.model is None:
            return
        values = [name for name, spec in self.model.registered_specs.items() if spec.supported]
        selected, accepted = QInputDialog.getItem(self, "Add Defense Pattern", "Pattern type", values, 0, False)
        if accepted and selected:
            self.add_pattern(selected)

    def _add_sequence_dialog(self) -> None:
        value, accepted = QInputDialog.getText(self, "Add Defense Sequence", "Sequence ID", text="defense")
        if accepted and value.strip():
            self.add_sequence(value.strip())

    def _clear_fields(self) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self._editors.clear()


__all__ = ["DefensePatternEditorWidget"]
