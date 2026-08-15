"""Focused editor for investigation-scene navigation links."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
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
    QVBoxLayout,
    QWidget,
)

from engine.story_core import StoryProject
from engine.story_core.conditions import ConditionError, parse_condition
from engine.story_core.schema import MISSING

from ..models import (
    DefinitionSelection,
    InsertNavigationEntryCommand,
    NavigationEntrySelection,
    ProjectSession,
    RemoveNavigationEntryCommand,
    SetNavigationConditionCommand,
    SetNavigationDestinationCommand,
    navigation_collection_path,
)


class NavigationPanel(QWidget):
    """A compact list/detail editor backed entirely by ``ProjectSession``."""

    navigation_selected = Signal(object)
    navigation_changed = Signal(object)
    open_destination_scene = Signal(str)

    def __init__(self, session: ProjectSession | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.project: StoryProject | None = None
        self.scene_id: str | None = None
        self.collection_path: tuple[str, ...] = ("exploration", "navigation")
        self.selected_entry: NavigationEntrySelection | None = None
        self._updating = False

        self.title = QLabel("Navigation")
        self.title.setStyleSheet("font-size: 15px; font-weight: bold;")
        self.entries = QListWidget()
        self.entries.setMinimumWidth(255)
        self.entries.setToolTip("Authored exploration Move destinations")
        self.entries.currentItemChanged.connect(self._on_entry_selected)
        self.entries.itemDoubleClicked.connect(self._on_entry_double_clicked)

        self.add_button = QPushButton("+ Add Destination")
        self.remove_button = QPushButton("Remove Destination")
        self.add_button.clicked.connect(self.add_destination)
        self.remove_button.clicked.connect(self.remove_destination)

        self.destination = QComboBox()
        self.destination.currentIndexChanged.connect(self._destination_changed)
        self.destination_label = QLabel("Destination scene")

        self.condition_mode = QComboBox()
        self.condition_mode.addItem("Always available", "absent")
        self.condition_mode.addItem("String expression", "string")
        self.condition_mode.addItem("Structured condition (JSON)", "structured")
        self.condition_mode.currentIndexChanged.connect(self._condition_mode_changed)
        self.condition_text = QLineEdit()
        self.condition_text.setPlaceholderText("flags.door_open")
        self.condition_text.editingFinished.connect(self._string_condition_changed)
        self.condition_json = QPlainTextEdit()
        self.condition_json.setPlaceholderText('{"flag": "door_open"}')
        self.condition_json.setMaximumHeight(110)
        self.apply_condition_button = QPushButton("Apply Condition")
        self.apply_condition_button.clicked.connect(self._structured_condition_changed)
        self.condition_status = QLabel()
        self.condition_status.setWordWrap(True)
        self.condition_status.setStyleSheet("color: #b45309;")

        detail = QGroupBox("Selected link")
        form = QFormLayout(detail)
        form.addRow(self.destination_label, self.destination)
        form.addRow("Condition mode", self.condition_mode)
        form.addRow(self.condition_text)
        form.addRow(self.condition_json)
        form.addRow(self.apply_condition_button)
        form.addRow(self.condition_status)

        buttons = QHBoxLayout()
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.remove_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addWidget(self.entries, 1)
        layout.addLayout(buttons)
        layout.addWidget(detail)
        self.clear()

    def clear(self) -> None:
        self._updating = True
        self.project = None
        self.scene_id = None
        self.selected_entry = None
        self.entries.clear()
        self.destination.clear()
        self.condition_text.clear()
        self.condition_json.clear()
        self.condition_status.clear()
        self._updating = False
        self._update_enabled_state()

    def set_scene(self, project: StoryProject | None, scene_id: str | None, mapping: Mapping[str, Any] | None) -> None:
        if project is None or scene_id is None or mapping is None:
            self.clear()
            return
        previous = self.selected_entry
        self.project = project
        self.scene_id = str(scene_id)
        self.collection_path = navigation_collection_path(mapping)
        raw = _path_value(mapping, self.collection_path)
        self._updating = True
        self.entries.clear()
        if not isinstance(raw, list):
            if raw not in (None, MISSING):
                item = QListWidgetItem("Navigation must be a list")
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                self.entries.addItem(item)
            self.selected_entry = None
        else:
            for index, value in enumerate(raw):
                if not isinstance(value, Mapping):
                    item = QListWidgetItem(f"Entry {index} (invalid)")
                    item.setFlags(Qt.ItemFlag.NoItemFlags)
                    self.entries.addItem(item)
                    continue
                selection = NavigationEntrySelection(self.scene_id, (*self.collection_path, index))
                item = QListWidgetItem(self._entry_label(value, index))
                item.setData(Qt.ItemDataRole.UserRole, selection)
                self.entries.addItem(item)
            wanted = previous if previous is not None and previous.scene_id == self.scene_id else None
            row = self._row_for_selection(wanted)
            if row is None and self.entries.count():
                row = 0
            if row is not None:
                self.entries.setCurrentRow(row)
                value = self.entries.item(row).data(Qt.ItemDataRole.UserRole)
                self.selected_entry = value if isinstance(value, NavigationEntrySelection) else None
            else:
                self.selected_entry = None
        self._updating = False
        self._populate_detail()
        self._update_enabled_state()

    def add_destination(self) -> bool:
        if self.session is None or self.project is None or self.scene_id is None:
            return False
        destination = next((identifier for identifier in sorted(self.project.scenes) if identifier != self.scene_id), None)
        destination = destination or (self.scene_id if self.scene_id in self.project.scenes else "missing_scene")
        selection = self._scene_selection()
        mapping = self.session.working_mapping(selection)
        if mapping is None:
            return False
        path = navigation_collection_path(mapping)
        existing = _path_value(mapping, path)
        index = len(existing) if isinstance(existing, list) else None
        command = InsertNavigationEntryCommand(selection, path, {"scene": destination}, index=index)
        try:
            self.session.apply_command(command)
        except (KeyError, TypeError, ValueError) as exc:
            self._show_error(str(exc))
            return False
        new_selection = NavigationEntrySelection(self.scene_id, (*path, command.index or 0))
        self._after_change(new_selection)
        return True

    def remove_destination(self) -> bool:
        if self.session is None or self.selected_entry is None or self.scene_id is None:
            return False
        index = _entry_index(self.selected_entry)
        selection = self._scene_selection()
        try:
            self.session.apply_command(RemoveNavigationEntryCommand(selection, self.collection_path, index))
        except (KeyError, TypeError, ValueError) as exc:
            self._show_error(str(exc))
            return False
        mapping = self.session.working_mapping(selection) or {}
        entries = _path_value(mapping, self.collection_path)
        next_selection = None
        if isinstance(entries, list) and entries:
            next_selection = NavigationEntrySelection(
                self.scene_id,
                (*self.collection_path, min(index, len(entries) - 1)),
            )
        self._after_change(next_selection)
        return True

    def _destination_changed(self, _index: int) -> None:
        if self._updating or self.session is None or self.selected_entry is None or self.scene_id is None:
            return
        value = self.destination.currentData()
        if not isinstance(value, str) or not value:
            return
        selection = self._scene_selection()
        try:
            self.session.apply_command(SetNavigationDestinationCommand(
                selection, self.collection_path, _entry_index(self.selected_entry), value,
            ))
        except (KeyError, TypeError, ValueError) as exc:
            self._show_error(str(exc))
            self._populate_detail()
            return
        self._after_change(self.selected_entry)

    def _condition_mode_changed(self, _index: int) -> None:
        if self._updating or self.selected_entry is None:
            return
        mode = self.condition_mode.currentData()
        if mode == "absent":
            self._commit_condition(MISSING)
        # Selecting a new authored mode only changes the editor controls.
        # The value is committed when the field is edited or Apply is pressed,
        # keeping one user edit equal to one history entry.

    def _string_condition_changed(self) -> None:
        if not self._updating and self.condition_mode.currentData() == "string":
            self._commit_condition(self.condition_text.text())

    def _structured_condition_changed(self) -> None:
        if self._updating or self.condition_mode.currentData() != "structured":
            return
        try:
            value = json.loads(self.condition_json.toPlainText())
        except json.JSONDecodeError as exc:
            self._show_error(f"Invalid JSON condition: {exc.msg}")
            return
        self._commit_condition(value)

    def _commit_condition(self, value: Any) -> None:
        if self.session is None or self.selected_entry is None or self.scene_id is None:
            return
        selection = self._scene_selection()
        try:
            self.session.apply_command(SetNavigationConditionCommand(
                selection, self.collection_path, _entry_index(self.selected_entry), value,
            ))
        except (KeyError, TypeError, ValueError) as exc:
            self._show_error(str(exc))
            return
        self._after_change(self.selected_entry)

    def _after_change(self, selected: NavigationEntrySelection | None) -> None:
        if self.project is None or self.scene_id is None or self.session is None:
            return
        mapping = self.session.working_mapping(self._scene_selection())
        self.selected_entry = selected
        self.set_scene(self.project, self.scene_id, mapping)
        self.navigation_changed.emit(selected)

    def _on_entry_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if self._updating:
            return
        value = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        self.selected_entry = value if isinstance(value, NavigationEntrySelection) else None
        self._populate_detail()
        self.navigation_selected.emit(self.selected_entry)

    def _on_entry_double_clicked(self, item: QListWidgetItem) -> None:
        selection = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(selection, NavigationEntrySelection) or self.project is None or self.session is None:
            return
        mapping = self.session.working_mapping(self._scene_selection()) or {}
        entry = _path_value(mapping, selection.path)
        destination = entry.get("scene") if isinstance(entry, Mapping) else None
        if isinstance(destination, str) and destination in self.project.scenes:
            self.open_destination_scene.emit(destination)

    def _populate_detail(self) -> None:
        entry = None
        if self.session is not None and self.selected_entry is not None:
            mapping = self.session.working_mapping(self._scene_selection())
            value = _path_value(mapping or {}, self.selected_entry.path)
            entry = value if isinstance(value, Mapping) else None
        self._updating = True
        self.destination.clear()
        valid = sorted(self.project.scenes) if self.project is not None else []
        destination = entry.get("scene") if entry else None
        if isinstance(destination, str) and destination not in valid:
            self.destination.addItem(f"{destination} (unresolved)", destination)
        for identifier in valid:
            self.destination.addItem(identifier, identifier)
        if isinstance(destination, str):
            self.destination.setCurrentIndex(max(0, self.destination.findData(destination)))
        condition_key = "conditions" if entry is not None and "conditions" in entry else (
            "condition" if entry is not None and "condition" in entry else None
        )
        condition = entry.get(condition_key) if condition_key else MISSING
        if condition is MISSING:
            mode = "absent"
            self.condition_text.clear()
            self.condition_json.clear()
        elif isinstance(condition, str):
            mode = "string"
            self.condition_text.setText(condition)
            self.condition_json.clear()
        else:
            mode = "structured"
            self.condition_text.clear()
            self.condition_json.setPlainText(json.dumps(condition, indent=2, sort_keys=True))
        self.condition_mode.setCurrentIndex(max(0, self.condition_mode.findData(mode)))
        self._updating = False
        self._show_condition_status(condition)
        self._update_enabled_state()

    def _show_condition_status(self, condition: Any) -> None:
        if condition is MISSING:
            self.condition_status.clear()
            return
        try:
            parse_condition(condition)
        except (ConditionError, TypeError, ValueError) as exc:
            self.condition_status.setText(f"Condition warning: {exc}")
        else:
            self.condition_status.clear()

    def _entry_label(self, entry: Mapping[str, Any], index: int) -> str:
        destination = entry.get("scene")
        if not isinstance(destination, str) or not destination:
            destination = f"[{entry.get('battle', 'invalid target')}]"
        suffix = ""
        if isinstance(entry.get("scene"), str) and self.project is not None and entry["scene"] not in self.project.scenes:
            suffix = "  [unresolved]"
        condition = entry.get("conditions", entry.get("condition", MISSING))
        if condition is not MISSING:
            suffix += "  ◇ conditional"
        return f"{destination}{suffix}"

    def _row_for_selection(self, selection: NavigationEntrySelection | None) -> int | None:
        if selection is None:
            return None
        for row in range(self.entries.count()):
            if self.entries.item(row).data(Qt.ItemDataRole.UserRole) == selection:
                return row
        return None

    def _update_enabled_state(self) -> None:
        has_entry = self.selected_entry is not None
        self.add_button.setEnabled(self.project is not None and self.scene_id is not None)
        self.remove_button.setEnabled(has_entry)
        self.destination.setEnabled(has_entry and self.destination.count() > 0)
        mode = self.condition_mode.currentData()
        self.condition_text.setVisible(has_entry and mode == "string")
        self.condition_json.setVisible(has_entry and mode == "structured")
        self.apply_condition_button.setVisible(has_entry and mode == "structured")

    def _show_error(self, message: str) -> None:
        self.condition_status.setText(str(message))

    def _scene_selection(self) -> DefinitionSelection:
        if self.session is not None and self.session.selection is not None:
            current = self.session.selection
            if current.kind.value == "scene" and current.id == self.scene_id:
                return current
        source = None
        if self.project is not None and self.project.index is not None and self.scene_id is not None:
            entry = self.project.index.entry("scene", self.scene_id)
            source = entry.source if entry is not None else None
        return DefinitionSelection("scene", self.scene_id or "", source)


def _path_value(mapping: Mapping[str, Any], path: tuple[str | int, ...]) -> Any:
    current: Any = mapping
    for component in path:
        if isinstance(current, Mapping) and component in current:
            current = current[component]
        elif isinstance(current, list) and isinstance(component, int) and 0 <= component < len(current):
            current = current[component]
        else:
            return MISSING
    return current


def _entry_index(selection: NavigationEntrySelection) -> int:
    component = selection.path[-1]
    if not isinstance(component, int):
        raise ValueError("Navigation entry path does not end in a list index")
    return component


__all__ = ["NavigationPanel"]
