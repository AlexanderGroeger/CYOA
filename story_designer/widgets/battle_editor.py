"""Dedicated high-level Battle Editor workspace."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from engine.story_core import ContentKind, Diagnostics, StoryProject

from ..models import (
    BattleDocumentModel,
    BattleElementPresentation,
    BattleElementSelection,
    BattleSection,
    DefinitionSelection,
    EditValidationError,
    ProjectSession,
    SetPropertyCommand,
)
from engine.story_core.schema import MISSING
from .property_editors import PropertyEditorFactory
from .defense_pattern_editor import DefensePatternEditorWidget


class BattleEditorWidget(QWidget):
    """Authoring-only overview and structural navigator for one battle."""

    section_selected = Signal(object)
    element_selected = Signal(object)
    changed = Signal(object)
    test_requested = Signal()
    open_move_requested = Signal(str)

    def __init__(self, session: ProjectSession | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.model: BattleDocumentModel | None = None
        self._selection: DefinitionSelection | None = None
        self._element: BattleElementPresentation | None = None
        self._sections_by_id: dict[str, BattleSection] = {}
        self._elements_by_row: dict[int, BattleElementPresentation] = {}

        self.title = QLabel("Battle Editor")
        self.title.setStyleSheet("font-size: 18px; font-weight: bold;")
        self.test_button = QPushButton("▶ Test Battle")
        self.test_button.setToolTip("Save and launch this battle in the pygame runtime")
        self.test_button.clicked.connect(self.test_requested)
        self.validation = QLabel()
        self.validation.setWordWrap(True)
        self.sections = QListWidget()
        self.sections.setMinimumWidth(170)
        self.sections.currentRowChanged.connect(self._section_changed)
        self.elements = QListWidget()
        self.elements.setMinimumWidth(230)
        self.elements.currentRowChanged.connect(self._element_changed)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setPlaceholderText("Select a battle section.")
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlaceholderText("Select a configured battle element.")
        self.dialogue_editor = QPlainTextEdit()
        self.dialogue_editor.setPlaceholderText("Battle dialogue text")
        self.dialogue_editor.hide()
        self.save_dialogue_button = QPushButton("Save Dialogue Text")
        self.save_dialogue_button.clicked.connect(self._save_dialogue_text)
        self.save_dialogue_button.hide()
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.open_move_button = QPushButton("Open Move Definition")
        self.open_move_button.clicked.connect(self._open_referenced_move)
        self.open_move_button.hide()
        self.defense_editor = DefensePatternEditorWidget(session, self)
        self.defense_editor.changed.connect(self._defense_changed)
        self.element_fields = QWidget()
        self.element_fields_form = QFormLayout(self.element_fields)
        self.element_fields.hide()
        self._element_editors: dict[tuple[str | int, ...], QWidget] = {}
        self._element_paths: set[tuple[str | int, ...]] = set()
        self._factory = PropertyEditorFactory()

        detail_panel = QWidget()
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.addWidget(self.summary, 1)
        detail_layout.addWidget(self.details, 1)
        detail_layout.addWidget(self.element_fields)
        detail_layout.addWidget(self.defense_editor)
        detail_layout.addWidget(self.dialogue_editor)
        detail_layout.addWidget(self.save_dialogue_button)
        detail_layout.addWidget(self.status)
        detail_layout.addWidget(self.open_move_button)

        splitter = QSplitter()
        splitter.addWidget(self.sections)
        splitter.addWidget(self.elements)
        splitter.addWidget(detail_panel)
        splitter.setStretchFactor(2, 1)
        layout = QVBoxLayout(self)
        heading = QHBoxLayout()
        heading.addWidget(self.title)
        heading.addStretch(1)
        heading.addWidget(self.test_button)
        layout.addLayout(heading)
        layout.addWidget(self.validation)
        layout.addWidget(splitter, 1)
        self.clear()

    @property
    def current_section(self) -> BattleSection | None:
        item = self.sections.currentItem()
        return self._sections_by_id.get(item.data(32)) if item is not None else None

    @property
    def current_element(self) -> BattleElementSelection | None:
        return self._element.selection if self._element is not None else None

    def clear(self) -> None:
        self.model = None
        self._selection = None
        self._element = None
        self._sections_by_id.clear()
        self._elements_by_row.clear()
        self.title.setText("Battle Editor")
        self.test_button.setEnabled(False)
        self.validation.clear()
        self.sections.clear()
        self.elements.clear()
        self.summary.clear()
        self.details.clear()
        self._hide_dialogue_editor()
        self._clear_element_fields()
        self.defense_editor.clear()
        self.open_move_button.hide()

    def set_state(
        self,
        project: StoryProject | None,
        selection: DefinitionSelection | None,
        definition: Any | None,
        diagnostics: Diagnostics,
    ) -> None:
        if project is None or selection is None or selection.kind is not ContentKind.BATTLE or definition is None:
            self.clear()
            return
        mapping = self.session.working_mapping(selection) if self.session is not None else None
        if mapping is None and hasattr(definition, "to_mapping"):
            mapping = definition.to_mapping()
        if mapping is None:
            self.clear()
            return
        previous = self.current_section.id if self.current_section is not None else "overview"
        self._selection = selection
        self.test_button.setEnabled(True)
        self.model = BattleDocumentModel(selection.id, mapping, project, diagnostics)
        self.defense_editor.set_state(project, selection, mapping, diagnostics)
        self._sections_by_id = {section.id: section for section in self.model.sections}
        self.title.setText(f"Battle: {selection.id}")
        relevant = tuple(item for item in diagnostics if item.source == getattr(definition, "source", selection.source))
        errors = sum(item.is_error for item in relevant)
        warnings = sum(item.is_warning for item in relevant)
        self.validation.setText(f"Validation: {errors} error(s), {warnings} warning(s)" if relevant else "Validation: no diagnostics")
        blocker = QSignalBlocker(self.sections)
        self.sections.clear()
        for section in self.model.sections:
            item = QListWidgetItem(f"{section.label}  ({len(section.elements)})" if section.elements else section.label)
            item.setData(32, section.id)
            self.sections.addItem(item)
        del blocker
        row = next((index for index, section in enumerate(self.model.sections) if section.id == previous), 0)
        self.sections.setCurrentRow(row)
        self._show_section(self.model.sections[row] if self.model.sections else None)

    def _section_changed(self, row: int) -> None:
        section = self.model.sections[row] if self.model is not None and 0 <= row < len(self.model.sections) else None
        self._show_section(section)
        if section is not None:
            self.section_selected.emit(section)

    def _show_section(self, section: BattleSection | None) -> None:
        self._element = None
        self._elements_by_row.clear()
        self.elements.clear()
        self.details.clear()
        self._hide_dialogue_editor()
        self._clear_element_fields()
        self.open_move_button.hide()
        self.defense_editor.hide()
        self.elements.show()
        if section is None:
            self.summary.clear()
            return
        section_summary = section.summary
        if section.diagnostics:
            section_summary = f"Diagnostics: {len(section.diagnostics)}\n\n" + section_summary
        self.summary.setPlainText(section_summary)
        if section.id == "defense":
            self.elements.hide()
            self.details.hide()
            self.summary.hide()
            self.defense_editor.show()
            return
        self.summary.show()
        self.details.show()
        for index, element in enumerate(section.elements):
            item = QListWidgetItem(element.label)
            item.setToolTip(element.summary)
            item.setData(32, index)
            self.elements.addItem(item)
            self._elements_by_row[index] = element
        if section.elements:
            self.elements.setCurrentRow(0)
        else:
            self.details.setPlainText("No nested authored entries. Use the Inspector for supported scalar properties; complex payloads remain visible in the semantic snapshot.")

    def _element_changed(self, row: int) -> None:
        element = self._elements_by_row.get(row)
        self._element = element
        if element is None:
            self.details.clear()
            self._hide_dialogue_editor()
            return
        details = element.summary
        if not element.supported:
            details = f"Unsupported / opaque payload\n\n{element.unsupported_reason}\n\n{details}"
        elif not element.editable:
            details = "Read-only structural presentation. Complex authored fields are preserved.\n\n" + details
        if element.diagnostics:
            details = f"Diagnostics: {len(element.diagnostics)}\n\n" + details
        self.details.setPlainText(details)
        if element.selection.kind == "player_move":
            move_id = element.selection.identifier or (str(element.authored) if isinstance(element.authored, str) else None)
            if move_id and self.model is not None and move_id in self.model.global_move_ids:
                self.open_move_button.setText(f"Open Move Definition: {move_id}")
                self.open_move_button.setProperty("move_id", move_id)
                self.open_move_button.show()
        self._show_element_fields(element)
        self._show_dialogue_editor(element)
        self.element_selected.emit(element.selection)

    def _open_referenced_move(self) -> None:
        move_id = self.open_move_button.property("move_id")
        if isinstance(move_id, str) and move_id:
            self.open_move_requested.emit(move_id)

    def _show_dialogue_editor(self, element: BattleElementPresentation) -> None:
        self._hide_dialogue_editor()
        if element.selection.kind != "dialogue" or not isinstance(element.authored, dict) or "text" not in element.authored:
            return
        self.dialogue_editor.setPlainText(str(element.authored.get("text", "")))
        self.dialogue_editor.show()
        self.save_dialogue_button.show()

    def _hide_dialogue_editor(self) -> None:
        self.dialogue_editor.hide()
        self.save_dialogue_button.hide()

    def _clear_element_fields(self) -> None:
        while self.element_fields_form.rowCount():
            self.element_fields_form.removeRow(0)
        self._element_editors.clear()
        self._element_paths.clear()
        self.element_fields.hide()

    def _show_element_fields(self, element: BattleElementPresentation) -> None:
        self._clear_element_fields()
        if self.session is None or self._selection is None:
            return
        field_names = {
            # IDs are stable authored references and are intentionally not
            # renamed by this foundation editor.
            "enemy_move": ("name", "pattern", "weight", "cooldown", "telegraph_duration"),
            "phase": ("name",),
            "dialogue": ("trigger", "type", "once", "pause"),
        }.get(element.selection.kind, ())
        if not field_names:
            return
        model = self.session.property_model(self._selection)
        if model is None:
            return
        for key in field_names:
            path = element.selection.path + (key,)
            try:
                descriptor = model.descriptor(path)
            except KeyError:
                continue
            if not descriptor.is_editable or not descriptor.supported:
                continue
            editor = self._factory.create(
                descriptor,
                story_root=self.session.story_root,
                project=self.session.project,
            )
            self._set_editor_value(editor, descriptor)
            if hasattr(editor, "value_edited"):
                editor.value_edited.connect(lambda value, path=path, widget=editor: self._commit_element_property(path, value, widget))
            self._element_editors[path] = editor
            self._element_paths.add(path)
            self.element_fields_form.addRow(descriptor.display_name, editor)
        if self._element_editors:
            self.element_fields.show()

    @staticmethod
    def _set_editor_value(editor: QWidget, descriptor: Any) -> None:
        blocker = QSignalBlocker(editor)
        if hasattr(editor, "set_initializing"):
            editor.set_initializing(True)
        try:
            value = descriptor.effective_value
            kind = descriptor.type_spec.kind if descriptor.type_spec is not None else None
            if kind in {"string", "multiline_string"}:
                if kind == "multiline_string" and hasattr(editor, "setPlainText"):
                    editor.setPlainText("" if value is MISSING else str(value))
                elif hasattr(editor, "setText"):
                    editor.setText("" if value is MISSING else str(value))
            elif kind == "integer" and hasattr(editor, "setValue"):
                editor.setValue(0 if value is MISSING else int(value))
            elif kind in {"float", "number"} and hasattr(editor, "setValue"):
                editor.setValue(0.0 if value is MISSING else float(value))
            elif kind == "boolean" and hasattr(editor, "setChecked"):
                editor.setChecked(False if value is MISSING else bool(value))
            elif kind in {"enum", "reference"} and hasattr(editor, "findData"):
                index = editor.findData(None if value is MISSING else value)
                editor.setCurrentIndex(index if index >= 0 else -1)
        finally:
            if hasattr(editor, "set_initializing"):
                editor.set_initializing(False)
            del blocker

    def _commit_element_property(self, path: tuple[str | int, ...], value: Any, editor: QWidget) -> None:
        if self.session is None or self._selection is None or path not in self._element_paths:
            return
        try:
            self.session.apply_command(SetPropertyCommand(self._selection, path, value))
        except EditValidationError as exc:
            self.status.setText(exc.message)
            descriptor = self.session.property_model(self._selection).descriptor(path)
            self._set_editor_value(editor, descriptor)
            return
        self.status.setText(f"Updated {path[-1]}; the edit is undoable and remains in the working copy until Save.")
        self.changed.emit(self._element.selection if self._element is not None else None)

    def _defense_changed(self, reference: Any) -> None:
        self.status.setText("Defense edit applied. The working battle mapping is dirty until Save.")
        self.changed.emit(reference)

    def _save_dialogue_text(self) -> None:
        if self.session is None or self._selection is None or self._element is None:
            return
        if self._element.selection.kind != "dialogue":
            return
        path = self._element.selection.path + ("text",)
        try:
            self.session.apply_command(SetPropertyCommand(self._selection, path, self.dialogue_editor.toPlainText()))
        except EditValidationError as exc:
            self.status.setText(exc.message)
            return
        self.status.setText("Dialogue text updated. The change is undoable and remains in the working copy until Save.")
        self.changed.emit(self._element.selection)


__all__ = ["BattleEditorWidget"]
