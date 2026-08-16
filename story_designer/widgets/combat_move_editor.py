"""Dedicated authoring workspace for global combat moves."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
    QCheckBox,
)

from engine.story_core import ContentKind, Diagnostics, StoryProject
from engine.story_core.schema import MISSING

from ..models import (
    AddDifficultyLevelCommand,
    CombatMoveDocumentModel,
    DefinitionSelection,
    DeleteDifficultyLevelCommand,
    DuplicateDifficultyLevelCommand,
    EditValidationError,
    ProjectSession,
    ReplaceQTETypeCommand,
    SetCombatMoveFieldCommand,
    SetPropertyCommand,
    SetSourcePropertyCommand,
)
from .property_editors import AssetPathEditor


class _ValueEditor(QWidget):
    value_edited = Signal(object)

    def __init__(self, kind: str, value: Any, *, enum_values: tuple[Any, ...] = (), minimum: Any = None, maximum: Any = None, asset_kind: str | None = None, asset_label: str | None = None, project: Any = None, story_root: Path | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self._initializing = True
        self.control: QWidget
        if kind == "boolean":
            control = QCheckBox(self)
            control.setChecked(bool(value) if value is not MISSING else False)
            control.toggled.connect(lambda checked: self._emit(bool(checked)))
        elif kind == "integer":
            control = QSpinBox(self)
            control.setRange(int(minimum if minimum is not None else -2_147_483_648), int(maximum if maximum is not None else 2_147_483_647))
            control.setValue(int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0)
            control.valueChanged.connect(lambda changed: self._emit(int(changed)))
        elif kind in {"float", "number"}:
            control = QDoubleSpinBox(self)
            control.setRange(float(minimum if minimum is not None else -1_000_000_000), float(maximum if maximum is not None else 1_000_000_000))
            control.setDecimals(5)
            control.setValue(float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0)
            control.valueChanged.connect(lambda changed: self._emit(float(changed)))
        elif kind == "enum":
            control = QComboBox(self)
            for item in enum_values:
                control.addItem(str(item).replace("_", " ").title(), item)
            index = control.findData(value)
            control.setCurrentIndex(index if index >= 0 else 0)
            control.currentIndexChanged.connect(lambda index: self._emit(control.itemData(index)))
        elif kind == "asset":
            control = AssetPathEditor(story_root=story_root, asset_kind=asset_kind, asset_label=asset_label, project=project, parent=self)
            control.setText("" if value is MISSING or value is None else str(value))
            control.value_edited.connect(self._emit)
        else:
            control = QLineEdit(self)
            if value is not MISSING:
                control.setText(json.dumps(value) if kind in {"list", "mapping"} else str(value))
            control.editingFinished.connect(lambda: self._emit(self._text_value(control.text())))
        self.control = control
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(control)
        self._initializing = False

    def _emit(self, value: Any) -> None:
        if not self._initializing:
            self.value_edited.emit(value)

    def _text_value(self, text: str) -> Any:
        if self.kind in {"list", "mapping"}:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
        return text


class CombatMoveEditorWidget(QWidget):
    """Generated, registry-backed editor for a global move definition."""

    changed = Signal(object)
    section_selected = Signal(object)
    level_selected = Signal(int)
    test_requested = Signal()

    def __init__(self, session: ProjectSession | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.model: CombatMoveDocumentModel | None = None
        self._selection: DefinitionSelection | None = None
        self._levels: tuple[int, ...] = ()
        self._fields: dict[tuple[str | int, ...], _ValueEditor] = {}
        self._sections: dict[str, Any] = {}

        self.title = QLabel("Combat Move Editor")
        self.title.setStyleSheet("font-size: 18px; font-weight: bold;")
        self.validation = QLabel()
        self.validation.setWordWrap(True)
        self.sections = QListWidget()
        self.sections.currentRowChanged.connect(self._section_changed)
        self.levels = QListWidget()
        self.levels.currentRowChanged.connect(self._level_changed)
        self.levels.setMinimumWidth(130)
        self.qte_type_combo = QComboBox()
        self.qte_type_combo.currentIndexChanged.connect(self._qte_type_changed)
        self.test_difficulty_combo = QComboBox()
        self.test_difficulty_combo.currentIndexChanged.connect(self._test_difficulty_changed)
        self.test_move_button = QPushButton("▶ Test Move")
        self.test_button = self.test_move_button
        self.test_move_button.clicked.connect(self.test_requested)
        self.fields = QWidget()
        self.fields_form = QFormLayout(self.fields)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.progression = QWidget()
        self.progression_form = QFormLayout(self.progression)
        self.references = QPlainTextEdit()
        self.references.setReadOnly(True)
        self.add_level_button = QPushButton("Add Level")
        self.duplicate_level_button = QPushButton("Duplicate")
        self.delete_level_button = QPushButton("Delete")
        self.add_level_button.clicked.connect(self._add_level)
        self.duplicate_level_button.clicked.connect(self._duplicate_level)
        self.delete_level_button.clicked.connect(self._delete_level)
        self.status = QLabel()
        self.status.setWordWrap(True)

        level_controls = QHBoxLayout()
        level_controls.addWidget(self.add_level_button)
        level_controls.addWidget(self.duplicate_level_button)
        level_controls.addWidget(self.delete_level_button)
        level_page = QWidget()
        level_layout = QVBoxLayout(level_page)
        level_layout.addLayout(level_controls)
        level_layout.addWidget(self.levels)
        qte_page = QWidget()
        qte_layout = QVBoxLayout(qte_page)
        qte_layout.addWidget(QLabel("QTE type"))
        qte_layout.addWidget(self.qte_type_combo)
        qte_layout.addWidget(self.fields, 1)
        detail_page = QWidget()
        detail_layout = QVBoxLayout(detail_page)
        detail_layout.addWidget(self.details, 1)
        detail_layout.addWidget(self.progression)
        detail_layout.addWidget(self.references)
        splitter = QSplitter()
        splitter.addWidget(self.sections)
        splitter.addWidget(level_page)
        splitter.addWidget(qte_page)
        splitter.addWidget(detail_page)
        splitter.setStretchFactor(3, 1)
        layout = QVBoxLayout(self)
        heading = QHBoxLayout()
        heading.addWidget(self.title)
        heading.addStretch(1)
        heading.addWidget(QLabel("Test Difficulty"))
        heading.addWidget(self.test_difficulty_combo)
        heading.addWidget(self.test_move_button)
        layout.addLayout(heading)
        layout.addWidget(self.validation)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.status)
        self.clear()

    def clear(self) -> None:
        self.model = None
        self._selection = None
        self._sections.clear()
        self._levels = ()
        self.title.setText("Combat Move Editor")
        self.validation.clear()
        self.sections.clear()
        self.levels.clear()
        self.qte_type_combo.clear()
        self.test_difficulty_combo.clear()
        self._clear_form(self.fields_form)
        self._clear_form(self.progression_form)
        self.details.clear()
        self.references.clear()
        self._set_buttons(False)

    def set_state(self, project: StoryProject | None, selection: DefinitionSelection | None, definition: Any | None, diagnostics: Diagnostics) -> None:
        if project is None or selection is None or selection.kind is not ContentKind.MOVE or definition is None:
            self.clear()
            return
        mapping = self.session.working_mapping(selection) if self.session is not None else None
        if mapping is None and hasattr(definition, "to_mapping"):
            mapping = definition.to_mapping()
        if mapping is None:
            self.clear()
            return
        progression = self._progression_mapping(definition)
        previous_level = self.current_level
        self._selection = selection
        self.model = CombatMoveDocumentModel(selection.id, mapping, project, diagnostics, skill_progression=progression)
        self._levels = self.model.levels
        self._set_buttons(not self.model.is_legacy)
        self._populate_qte_types()
        self.title.setText(f"Combat Move: {selection.id}")
        relevant = tuple(item for item in diagnostics if item.source == getattr(definition, "source", selection.source))
        self.validation.setText(f"Validation: {sum(item.is_error for item in relevant)} error(s), {sum(item.is_warning for item in relevant)} warning(s)" if relevant else "Validation: no diagnostics")
        blocker = QSignalBlocker(self.sections)
        self.sections.clear()
        self._sections = {section.id: section for section in self.model.sections}
        for section in self.model.sections:
            item = QListWidgetItem(section.label)
            item.setData(32, section.id)
            self.sections.addItem(item)
        del blocker
        self._populate_levels(previous_level)
        self._populate_test_difficulties()
        self.sections.setCurrentRow(0)
        self._show_section(self.model.section("overview"))

    @property
    def current_level(self) -> int:
        item = self.levels.currentItem()
        return int(item.data(32)) if item is not None else (self._levels[0] if self._levels else 1)

    @property
    def current_section(self):
        item = self.sections.currentItem()
        return self._sections.get(item.data(32)) if item is not None else None

    def _progression_mapping(self, definition: Any) -> dict[str, Any]:
        if self.session is None or self.session.project is None:
            return {}
        relative = self._progression_source_path(definition)
        if relative is None:
            return dict(getattr(self.session.project, "move_skill_progression", {}) or {})
        document = self.session.source_working_mapping(relative) or {}
        value = document.get("skill_progression") if isinstance(document, dict) else None
        return dict(value) if isinstance(value, dict) else {}

    def _progression_source_path(self, definition: Any) -> str | None:
        if self.session is None or self.session.project is None:
            return None
        try:
            selected = Path(definition.source).relative_to(self.session.project.story_root).as_posix()
        except (AttributeError, ValueError):
            selected = None
        documents = self.session.semantic_documents(include_source_copies=False)
        if selected is not None and isinstance(documents.get(selected), dict) and "skill_progression" in documents[selected]:
            return selected
        return next((path for path, value in documents.items() if isinstance(value, dict) and "skill_progression" in value), selected)

    def _populate_levels(self, previous: int) -> None:
        blocker = QSignalBlocker(self.levels)
        self.levels.clear()
        for level in self._levels:
            label = f"{level} — Tutorial" if level == 0 else str(level)
            item = QListWidgetItem(label)
            item.setData(32, level)
            self.levels.addItem(item)
        del blocker
        row = next((index for index, level in enumerate(self._levels) if level == previous), 0)
        self.levels.setCurrentRow(row)

    def _populate_test_difficulties(self) -> None:
        blocker = QSignalBlocker(self.test_difficulty_combo)
        self.test_difficulty_combo.clear()
        for level in self._levels:
            self.test_difficulty_combo.addItem(
                f"{level} — Tutorial" if level == 0 else str(level), level
            )
        index = self.test_difficulty_combo.findData(self.current_level)
        self.test_difficulty_combo.setCurrentIndex(index if index >= 0 else 0)
        del blocker

    def _test_difficulty_changed(self, index: int) -> None:
        if not 0 <= index < len(self._levels):
            return
        level = int(self.test_difficulty_combo.itemData(index))
        row = self._levels.index(level)
        blocker = QSignalBlocker(self.levels)
        self.levels.setCurrentRow(row)
        del blocker
        if self.current_section is not None:
            self._show_section(self.current_section)
        self.level_selected.emit(level)

    def test_context(self) -> tuple[str, int] | None:
        """Return the current move and actual authored/effective test level."""

        if self.model is None or self._selection is None:
            return None
        return self.model.move_id, self.current_level

    def _section_changed(self, row: int) -> None:
        section = self.model.sections[row] if self.model is not None and 0 <= row < len(self.model.sections) else None
        self._show_section(section)
        if section is not None:
            self.section_selected.emit(section)

    def _level_changed(self, row: int) -> None:
        if self.model is None or not 0 <= row < len(self._levels):
            return
        self.level_selected.emit(self.current_level)
        blocker = QSignalBlocker(self.test_difficulty_combo)
        index = self.test_difficulty_combo.findData(self.current_level)
        self.test_difficulty_combo.setCurrentIndex(index)
        del blocker
        if self.current_section is not None:
            self._show_section(self.current_section)

    def _show_section(self, section: Any) -> None:
        self._clear_form(self.fields_form)
        self._clear_form(self.progression_form)
        self._fields.clear()
        self.details.clear()
        self.references.clear()
        if section is None or self.model is None:
            return
        if section.id == "overview":
            self.details.setPlainText(self.model.section("overview").summary)
            self._show_overview_fields()
        elif section.id == "difficulty_levels":
            self.details.setPlainText(self.model.effective_preview(self.current_level))
        elif section.id == "qte":
            self._show_qte_fields()
        elif section.id == "skill_progression":
            self._show_progression_fields()
        elif section.id == "references":
            usages = self.model.references()
            self.references.setPlainText("\n".join(f"{usage.kind}: {usage.identifier} ({usage.label})\n  {usage.source} :: {usage.path}" for usage in usages) or "No discovered references.")
        else:
            self.details.setPlainText(json.dumps(self.model.mapping, indent=2, sort_keys=True, default=str))

    def _show_overview_fields(self) -> None:
        if self.session is None or self._selection is None:
            return
        property_model = self.session.property_model(self._selection)
        if property_model is None:
            return
        for key in ("name", "description", "initial_level", "tutorial_records_skill", "availability"):
            try:
                descriptor = property_model.descriptor((key,))
            except KeyError:
                continue
            if not descriptor.is_editable or not descriptor.supported:
                continue
            from .property_editors import PropertyEditorFactory
            editor = PropertyEditorFactory().create(descriptor, story_root=self.session.story_root, project=self.session.project, parent=self)
            self._set_generic_value(editor, descriptor.effective_value)
            if hasattr(editor, "value_edited"):
                editor.value_edited.connect(lambda value, key=key: self._set_property((key,), value))
            self.fields_form.addRow(descriptor.display_name, editor)

    def _show_qte_fields(self) -> None:
        if self.model is None:
            return
        spec = self.model.qte_spec
        self._populate_qte_types()
        for field in self.model.qte_fields(self.current_level):
            if not field.supported or field.spec is None:
                continue
            editor = _ValueEditor(field.spec.value_type, field.effective_value, enum_values=field.spec.enum_values, minimum=field.spec.minimum, maximum=field.spec.maximum, asset_kind=field.spec.asset_kind, asset_label=field.label, project=self.session.project if self.session else None, story_root=self.session.story_root if self.session else None, parent=self)
            editor.value_edited.connect(lambda value, path=field.path: self._set_qte_field(path, value))
            self._fields[field.path] = editor
            label = f"{field.label}{'' if field.is_authored else ' (inherited)'}"
            self.fields_form.addRow(label, editor)
        self.details.setPlainText("Unknown or legacy QTE fields remain in Advanced and are never dropped by generated edits.")

    def _populate_qte_types(self) -> None:
        if self.model is None:
            return
        self.qte_type_combo.blockSignals(True)
        self.qte_type_combo.clear()
        from engine.battle.qte import qte_editor_specs
        for item in qte_editor_specs():
            self.qte_type_combo.addItem(item.display_name, item.type)
        index = self.qte_type_combo.findData(self.model.qte_type)
        self.qte_type_combo.setCurrentIndex(index if index >= 0 else -1)
        self.qte_type_combo.blockSignals(False)

    def _show_progression_fields(self) -> None:
        if self.model is None:
            return
        authored, effective = self.model.progression_values()
        for key in ("evaluation_attempts", "promotion_average", "demotion_average", "minimum_level"):
            value = effective[key]
            kind = "integer" if key in {"evaluation_attempts", "minimum_level"} else "float"
            editor = _ValueEditor(kind, value, minimum=1 if key in {"evaluation_attempts", "minimum_level"} else 0, maximum=None if key in {"evaluation_attempts", "minimum_level"} else 3, parent=self)
            editor.value_edited.connect(lambda changed, key=key: self._set_progression(key, changed))
            self.progression_form.addRow(key.replace("_", " ").title() + (" (authored)" if key in authored else " (default)"), editor)
        self.details.setPlainText(f"Authored: {json.dumps(authored, sort_keys=True)}\nEffective: {json.dumps(effective, sort_keys=True)}")

    def _set_property(self, path: tuple[str, ...], value: Any) -> None:
        self._apply(SetPropertyCommand(self._selection, path, value))

    def _set_qte_field(self, path: tuple[str | int, ...], value: Any) -> None:
        self._apply(SetCombatMoveFieldCommand(self._selection, path, value))

    def _set_progression(self, key: str, value: Any) -> None:
        if self.session is None or self._selection is None or self.model is None:
            return
        source = self._selection.source
        if source is None or self.session.project is None:
            return
        relative = self._progression_source_path(self.session.definition(self._selection))
        if relative is None:
            return
        document = self.session.source_working_mapping(relative)
        if isinstance(document, dict) and "skill_progression" not in document and "moves" not in document:
            self.status.setText("This legacy/direct move root has no file-level skill_progression section; its shape is preserved.")
            return
        self._apply(SetSourcePropertyCommand(self._selection, relative, ("skill_progression", key), value))

    def _qte_type_changed(self, index: int) -> None:
        if index < 0 or self._selection is None:
            return
        type_name = self.qte_type_combo.itemData(index)
        if type_name and self.model is not None and type_name != self.model.qte_type:
            self._apply(ReplaceQTETypeCommand(self._selection, type_name))

    def _add_level(self) -> None:
        self._apply(AddDifficultyLevelCommand(self._selection))

    def _duplicate_level(self) -> None:
        self._apply(DuplicateDifficultyLevelCommand(self._selection, self.current_level))

    def _delete_level(self) -> None:
        self._apply(DeleteDifficultyLevelCommand(self._selection, self.current_level))

    def _apply(self, command: Any) -> None:
        if self.session is None or self._selection is None:
            return
        try:
            self.session.apply_command(command)
        except (EditValidationError, ValueError, KeyError, TypeError) as exc:
            self.status.setText(str(exc))
            return
        self.status.setText("Edit applied; it is undoable and remains in the working copy until Save.")
        self.changed.emit(command)
        definition = self.session.definition(self._selection)
        self.set_state(self.session.project, self._selection, definition, self.session.diagnostics)

    def _set_buttons(self, enabled: bool) -> None:
        for button in (self.add_level_button, self.duplicate_level_button, self.delete_level_button):
            button.setEnabled(enabled)
        self.test_move_button.setEnabled(self.model is not None)
        self.test_difficulty_combo.setEnabled(self.model is not None)

    @staticmethod
    def _clear_form(form: QFormLayout) -> None:
        while form.rowCount():
            form.removeRow(0)

    @staticmethod
    def _set_generic_value(editor: QWidget, value: Any) -> None:
        if hasattr(editor, "setText"):
            editor.setText("" if value is MISSING else str(value))
        elif hasattr(editor, "setValue") and value is not MISSING:
            editor.setValue(value)
        elif hasattr(editor, "setChecked"):
            editor.setChecked(bool(value))


__all__ = ["CombatMoveEditorWidget"]
