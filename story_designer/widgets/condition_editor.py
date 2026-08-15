"""Reusable condition representation and recursive Qt form editor."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Sequence

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from engine.story_core.conditions import ConditionError, parse_condition
from engine.story_core.schema import MISSING

from ..models.condition_editor import (
    ConditionEditorModel,
    ConditionNode,
    GROUP_TYPES,
    LEAF_TYPES,
    NODE_TYPES,
    PARAMETER_TYPES,
    condition_mode,
)


_TYPE_LABELS = {
    "all": "ALL",
    "any": "ANY",
    "not": "NOT",
    "flag": "Flag",
    "variable": "Variable",
    "var": "Variable (var alias)",
    "has_item": "Has Item",
}


class ConditionEditorWidget(QWidget):
    """Edit absent, string, and structured conditions through one component.

    The widget emits complete semantic values.  Owners decide which existing
    ProjectSession command should receive the value, so this widget has no
    knowledge of navigation, dialogue, or scene paths.
    """

    condition_changed = Signal(object)
    validation_changed = Signal(str)

    def __init__(self, project: Any | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project = project
        self.symbols = getattr(project, "symbols", None)
        self.model: ConditionEditorModel | None = None
        self._updating = False

        self.mode = QComboBox()
        self.condition_mode = self.mode
        self.mode.addItem("Always", "absent")
        self.mode.addItem("String Expression", "string")
        self.mode.addItem("Structured Condition", "structured")
        self.mode.currentIndexChanged.connect(self._mode_changed)

        self.string_editor = QLineEdit()
        self.condition_text = self.string_editor
        self.string_editor.setPlaceholderText("flags.door_open and has_item('key')")
        self.string_editor.editingFinished.connect(self._string_finished)

        self.structured_box = QWidget()
        structured_layout = QVBoxLayout(self.structured_box)
        structured_layout.setContentsMargins(0, 0, 0, 0)
        self.builder = QWidget()
        self.builder_layout = QVBoxLayout(self.builder)
        self.builder_layout.setContentsMargins(0, 0, 0, 0)
        structured_layout.addWidget(self.builder)

        add_row = QHBoxLayout()
        self.new_type = QComboBox()
        self._add_type_items(self.new_type)
        self.add_condition_button = QPushButton("Add Condition")
        self.add_condition_button.clicked.connect(self._add_root_condition)
        add_row.addWidget(self.new_type, 1)
        add_row.addWidget(self.add_condition_button)
        structured_layout.addLayout(add_row)

        self.condition_json = QPlainTextEdit()
        self.condition_json.setPlaceholderText('{"flag": "door_open"}')
        self.condition_json.setMaximumHeight(100)
        self.condition_json.hide()
        self.raw_apply_button = QPushButton("Apply Advanced JSON")
        self.raw_apply_button.clicked.connect(self._apply_raw_json)
        self.raw_apply_button.hide()
        structured_layout.addWidget(self.condition_json)
        structured_layout.addWidget(self.raw_apply_button)

        self.status = QLabel()
        self.condition_status = self.status
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #b45309;")

        layout = QFormLayout(self)
        layout.addRow("Condition", self.mode)
        layout.addRow(self.string_editor)
        layout.addRow(self.structured_box)
        layout.addRow(self.status)
        self.set_condition(MISSING, project=project)

    def set_project(self, project: Any | None) -> None:
        self.project = project
        self.symbols = getattr(project, "symbols", None)
        if self.model is not None:
            self.model.symbols = self.symbols
            self._render_builder()

    def set_condition(self, value: Any, *, project: Any | None = None) -> None:
        if project is not None:
            self.project = project
            self.symbols = getattr(project, "symbols", None)
        self._updating = True
        try:
            mode = condition_mode(value)
            self.mode.setCurrentIndex(max(0, self.mode.findData(mode)))
            self.string_editor.setText(value if mode == "string" else "")
            if mode == "structured":
                self.model = ConditionEditorModel.from_value(value, self.symbols)
                self.condition_json.setPlainText(_json_text(value))
            else:
                self.model = None
                self.condition_json.clear()
            self._render_builder()
        finally:
            self._updating = False
        self._refresh_visibility()
        self._show_validation(value)

    def value(self) -> Any:
        mode = self.mode.currentData()
        if mode == "absent":
            return MISSING
        if mode == "string":
            return self.string_editor.text()
        return self.model.value() if self.model is not None else MISSING

    def set_initializing(self, value: bool) -> None:
        self._updating = bool(value)

    def _mode_changed(self, _index: int) -> None:
        if self._updating:
            return
        self._refresh_visibility()
        mode = self.mode.currentData()
        if mode == "absent":
            self.condition_changed.emit(MISSING)
        elif mode == "structured" and self.model is None:
            self._render_builder()

    def _string_finished(self) -> None:
        if self._updating or self.mode.currentData() != "string":
            return
        value = self.string_editor.text()
        try:
            parse_condition(value)
        except (ConditionError, TypeError, ValueError) as exc:
            self._show_error(str(exc))
            return
        self._clear_error()
        self.condition_changed.emit(value)

    def _add_root_condition(self) -> None:
        if self._updating or self.mode.currentData() != "structured":
            return
        kind = str(self.new_type.currentData() or "flag")
        self.model = ConditionEditorModel.new(kind, self.symbols)
        self._emit_model()

    def _emit_model(self) -> None:
        if self.model is None:
            return
        try:
            self.model.validate()
        except (ConditionError, TypeError, ValueError) as exc:
            self._show_error(str(exc))
            return
        value = self.model.value()
        self.condition_json.setPlainText(_json_text(value))
        self._clear_error()
        self._render_builder()
        self.condition_changed.emit(value)

    def _apply_model_operation(self, operation, *args: Any) -> None:
        if self.model is None:
            return
        try:
            operation(*args)
        except (ConditionError, TypeError, ValueError, IndexError) as exc:
            self._show_error(str(exc))
            return
        self._emit_model()

    def _apply_raw_json(self) -> None:
        try:
            value = json.loads(self.condition_json.toPlainText())
            if isinstance(value, str):
                raise ValueError("Advanced structured JSON must be a mapping or list, not a string")
            parse_condition(value)
        except (json.JSONDecodeError, ConditionError, TypeError, ValueError) as exc:
            self._show_error(f"Invalid JSON condition: {exc}")
            return
        self.model = ConditionEditorModel.from_value(value, self.symbols)
        self._clear_error()
        self._render_builder()
        self.condition_changed.emit(value)

    def _render_builder(self) -> None:
        while self.builder_layout.count():
            item = self.builder_layout.takeAt(0)
            child = item.widget()
            if child is not None:
                child.deleteLater()
        if self.model is None:
            label = QLabel("Choose Structured Condition, then Add Condition.")
            label.setWordWrap(True)
            self.builder_layout.addWidget(label)
            self.condition_json.hide()
            self.raw_apply_button.hide()
            return
        if not self.model.supported:
            warning = QLabel("This condition contains unsupported fields. It is preserved unchanged; use Advanced JSON to edit it.")
            warning.setWordWrap(True)
            warning.setStyleSheet("color: #b45309;")
            self.builder_layout.addWidget(warning)
            self.condition_json.show()
            self.raw_apply_button.show()
            return
        self.condition_json.hide()
        self.raw_apply_button.hide()
        self.builder_layout.addWidget(self._node_widget((), self.model.root))

    def _node_widget(self, path: tuple[int, ...], node: ConditionNode) -> QWidget:
        if node.kind == "unsupported":
            label = QLabel("Unsupported child; preserved in Advanced JSON.")
            label.setStyleSheet("color: #b45309;")
            return label
        if node.kind == "empty":
            box = QGroupBox("Explicit structured {} (evaluates as Always)")
            layout = QHBoxLayout(box)
            layout.addWidget(QLabel("Change to"))
            type_box = QComboBox()
            self._add_type_items(type_box)
            type_box.currentIndexChanged.connect(
                lambda _index, p=path, combo=type_box: self._change_type(p, str(combo.currentData()))
            )
            layout.addWidget(type_box, 1)
            return box
        if node.kind == "list":
            group = QGroupBox("ALL (list form)")
            layout = QVBoxLayout(group)
            for index, child in enumerate(node.children):
                layout.addWidget(self._node_widget(path + (index,), child))
            return group
        if node.is_group:
            group = QGroupBox(node.label)
            layout = QVBoxLayout(group)
            type_box = QComboBox()
            self._add_type_items(type_box)
            type_box.setCurrentIndex(max(0, type_box.findData(node.kind)))
            type_box.currentIndexChanged.connect(
                lambda _index, p=path, combo=type_box: self._change_type(p, str(combo.currentData()))
            )
            layout.addWidget(type_box)
            if node.kind == "not":
                if node.children:
                    layout.addWidget(self._node_widget(path + (0,), node.children[0]))
                else:
                    layout.addWidget(QLabel("NOT requires one child."))
                return group
            for index, child in enumerate(node.children):
                child_box = QWidget()
                child_layout = QVBoxLayout(child_box)
                child_layout.setContentsMargins(8, 2, 0, 2)
                child_layout.addWidget(self._node_widget(path + (index,), child))
                buttons = QHBoxLayout()
                remove = QPushButton("Remove")
                remove.clicked.connect(lambda _checked=False, p=path + (index,): self._apply_model_operation(self.model.remove_child, p))
                up = QPushButton("Move Up")
                up.clicked.connect(lambda _checked=False, p=path, i=index: self._apply_model_operation(self.model.move_child, p, i, -1))
                down = QPushButton("Move Down")
                down.clicked.connect(lambda _checked=False, p=path, i=index: self._apply_model_operation(self.model.move_child, p, i, 1))
                up.setEnabled(index > 0)
                down.setEnabled(index < len(node.children) - 1)
                buttons.addWidget(remove)
                buttons.addWidget(up)
                buttons.addWidget(down)
                buttons.addStretch(1)
                child_layout.addLayout(buttons)
                layout.addWidget(child_box)
            add = QHBoxLayout()
            type_box = QComboBox()
            self._add_type_items(type_box)
            add_button = QPushButton("Add Condition")
            add_button.clicked.connect(lambda _checked=False, p=path, combo=type_box: self._apply_model_operation(
                self.model.add_child, p, str(combo.currentData() or "flag"), None))
            add.addWidget(type_box, 1)
            add.addWidget(add_button)
            layout.addLayout(add)
            return group
        return self._leaf_widget(path, node)

    def _leaf_widget(self, path: tuple[int, ...], node: ConditionNode) -> QWidget:
        group = QGroupBox(node.label)
        form = QFormLayout(group)
        type_box = QComboBox()
        self._add_type_items(type_box, include_groups=True)
        type_box.setCurrentIndex(max(0, type_box.findData(node.kind)))
        type_box.currentIndexChanged.connect(lambda _index, p=path, combo=type_box: self._change_type(p, str(combo.currentData())))
        form.addRow("Type", type_box)

        name_box = QComboBox()
        name_box.setEditable(True)
        suggestions = self._suggestions(node.kind)
        for value in suggestions:
            name_box.addItem(value, value)
        name_box.setCurrentText(node.name or "")
        name_box.lineEdit().editingFinished.connect(lambda p=path, combo=name_box: self._set_name(p, combo.currentText()))
        form.addRow("Name" if node.kind != "has_item" else "Item", name_box)

        comparison = QComboBox()
        comparison.addItem("Truthy / default", "")
        comparison.addItem("Equals", "equals")
        comparison.addItem("Not equals", "not_equals")
        comparison.addItem("Exists", "exists")
        authored_comparison = next((key for key in ("equals", "not_equals", "exists") if key in node.parameters), "")
        comparison.setCurrentIndex(max(0, comparison.findData(authored_comparison)))
        comparison.currentIndexChanged.connect(lambda _index, p=path, combo=comparison: self._comparison_changed(p, str(combo.currentData() or "")))
        form.addRow("Test", comparison)

        if authored_comparison in {"equals", "not_equals"}:
            value_editor = QLineEdit(_scalar_text(node.parameters.get(authored_comparison)))
            value_editor.editingFinished.connect(lambda p=path, key=authored_comparison, editor=value_editor: self._set_scalar(p, key, editor.text()))
            form.addRow("Expected", value_editor)
        elif authored_comparison == "exists":
            exists = QComboBox()
            exists.addItem("true", True)
            exists.addItem("false", False)
            exists.setCurrentIndex(0 if bool(node.parameters.get("exists")) else 1)
            exists.currentIndexChanged.connect(lambda _index, p=path, editor=exists: self._apply_model_operation(self.model.set_parameter, p, "exists", bool(editor.currentData())))
            form.addRow("Exists", exists)

        if node.kind == "has_item":
            quantity_box = QCheckBox("Require quantity")
            has_quantity = "quantity" in node.parameters
            quantity_box.setChecked(has_quantity)
            quantity_box.toggled.connect(lambda checked, p=path: self._apply_model_operation(
                self.model.set_parameter, p, "quantity", 1 if checked else MISSING))
            form.addRow(quantity_box)
            if has_quantity:
                quantity = QSpinBox()
                quantity.setRange(1, 2_147_483_647)
                quantity.setValue(int(node.parameters["quantity"]))
                quantity.editingFinished.connect(lambda p=path, editor=quantity: self._apply_model_operation(self.model.set_parameter, p, "quantity", int(editor.value())))
                form.addRow("Quantity", quantity)
        return group

    def _change_type(self, path: tuple[int, ...], kind: str) -> None:
        if self._updating or not kind:
            return
        self._apply_model_operation(self.model.change_type, path, kind)

    def _set_name(self, path: tuple[int, ...], value: str) -> None:
        self._apply_model_operation(self.model.set_leaf_name, path, value)

    def _comparison_changed(self, path: tuple[int, ...], key: str) -> None:
        self._apply_model_operation(self.model.set_comparison, path, key, True)

    def _set_scalar(self, path: tuple[int, ...], key: str, text: str) -> None:
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            value = text
        self._apply_model_operation(self.model.set_parameter, path, key, value)

    def _suggestions(self, kind: str) -> list[str]:
        if self.symbols is None:
            values: list[str] = []
        elif kind == "flag":
            values = list(getattr(self.symbols, "declared_flags", ())) + list(getattr(self.symbols, "referenced_flags", ()))
        elif kind in {"variable", "var"}:
            values = list(getattr(self.symbols, "declared_variables", ())) + list(getattr(self.symbols, "referenced_variables", ()))
        else:
            values = list(getattr(self.symbols, "referenced_items", ()))
            project_items = getattr(self.project, "items", {})
            if isinstance(project_items, Mapping):
                values.extend(str(value) for value in project_items)
        return list(dict.fromkeys(sorted(str(value) for value in values)))

    @staticmethod
    def _add_type_items(combo: QComboBox, *, include_groups: bool = True) -> None:
        for kind in NODE_TYPES if include_groups else NODE_TYPES:
            combo.addItem(_TYPE_LABELS.get(kind, kind), kind)

    def _refresh_visibility(self) -> None:
        mode = self.mode.currentData()
        self.string_editor.setVisible(mode == "string")
        self.structured_box.setVisible(mode == "structured")
        self.add_condition_button.setVisible(mode == "structured" and self.model is None)

    def _show_validation(self, value: Any) -> None:
        if value is MISSING:
            self._clear_error()
            return
        try:
            parse_condition(value)
        except (ConditionError, TypeError, ValueError) as exc:
            self._show_error(str(exc))
        else:
            self._clear_error()

    def _show_error(self, message: str) -> None:
        self.status.setText(str(message))
        self.validation_changed.emit(str(message))

    def _clear_error(self) -> None:
        self.status.clear()
        self.validation_changed.emit("")


def _scalar_text(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True) if not isinstance(value, str) else value


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, indent=2, sort_keys=False)
    except (TypeError, ValueError):
        return repr(value)


__all__ = ["ConditionEditorWidget"]
