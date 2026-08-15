"""Small modal editor for ephemeral runtime-test state."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from engine.core.developer_test import DeveloperTestConfigError, SceneTestConfiguration


class TestStateDialog(QDialog):
    """Edit flags, scalar variables, inventory quantities, and simple stats."""

    __test__ = False  # pytest should not treat this Qt widget as a test class.

    def __init__(
        self,
        project: Any | None = None,
        configuration: SceneTestConfiguration | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Test State")
        self.setModal(True)
        self.resize(560, 500)
        self.project = project
        self.flag_name_suggestions = self._symbol_names("flags", "declared_flags", "referenced_flags")
        self.item_id_suggestions = sorted(getattr(project, "items", {}) or {})
        self._build_ui()
        self.set_configuration(configuration or SceneTestConfiguration())

    def _symbol_names(self, *names: str) -> list[str]:
        symbols = getattr(self.project, "symbols", None)
        values: set[str] = set()
        for name in names:
            candidate = getattr(symbols, name, ())
            values.update(value for value in candidate if isinstance(value, str))
        return sorted(values)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Overrides apply only to the next fresh developer runtime launch."))
        self.flags_table = self._table(("Flag", "Value"))
        layout.addWidget(self._section("Flags", self.flags_table, self._add_flag_row, self._remove_flag_row))
        self.variables_table = self._table(("Variable", "Type", "Value"))
        layout.addWidget(self._section("Variables", self.variables_table, self._add_variable_row, self._remove_variable_row))
        self.inventory_table = self._table(("Item ID", "Quantity"))
        layout.addWidget(self._section("Inventory", self.inventory_table, self._add_inventory_row, self._remove_inventory_row))
        self.stats_table = self._table(("Stat", "Value"))
        layout.addWidget(self._section("Stats", self.stats_table, self._add_stat_row, self._remove_stat_row))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        apply_button = buttons.addButton("Apply", QDialogButtonBox.ButtonRole.ApplyRole)
        apply_button.clicked.connect(self._accept_configuration)
        reset = buttons.addButton("Reset", QDialogButtonBox.ButtonRole.ResetRole)
        reset.clicked.connect(self.reset_configuration)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _table(headers: tuple[str, ...]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setStretchLastSection(True)
        table.setMinimumHeight(55)
        return table

    @staticmethod
    def _section(
        title: str,
        table: QTableWidget,
        add_callback: Any,
        remove_callback: Any,
    ) -> QGroupBox:
        box = QGroupBox(title)
        layout = QVBoxLayout(box)
        layout.addWidget(table)
        controls = QHBoxLayout()
        add_button = QPushButton("+")
        add_button.setToolTip(f"Add {title.lower()} override")
        add_button.clicked.connect(add_callback)
        remove_button = QPushButton("-")
        remove_button.setToolTip(f"Remove selected {title.lower()} override")
        remove_button.clicked.connect(remove_callback)
        controls.addWidget(add_button)
        controls.addWidget(remove_button)
        controls.addStretch(1)
        layout.addLayout(controls)
        return box

    def _add_flag_row(self, name: str = "", value: bool = False) -> None:
        row = self.flags_table.rowCount()
        self.flags_table.insertRow(row)
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems(self.flag_name_suggestions)
        combo.setCurrentText(name)
        self.flags_table.setCellWidget(row, 0, combo)
        value_combo = QComboBox()
        value_combo.addItems(["false", "true"])
        value_combo.setCurrentText("true" if value else "false")
        self.flags_table.setCellWidget(row, 1, value_combo)

    def _add_variable_row(self, name: str = "", value: Any = None) -> None:
        row = self.variables_table.rowCount()
        self.variables_table.insertRow(row)
        self.variables_table.setCellWidget(row, 0, QLineEdit(name))
        type_combo = QComboBox()
        type_combo.addItems(["string", "integer", "float", "boolean", "null"])
        type_combo.currentTextChanged.connect(lambda _value, r=row: self._update_variable_editor(r))
        self.variables_table.setCellWidget(row, 1, type_combo)
        self.variables_table.setCellWidget(row, 2, QLineEdit("" if value is None else str(value)))
        self._update_variable_editor(row)

    def _update_variable_editor(self, row: int) -> None:
        if row < 0 or row >= self.variables_table.rowCount():
            return
        combo = self.variables_table.cellWidget(row, 1)
        editor = self.variables_table.cellWidget(row, 2)
        if not isinstance(combo, QComboBox) or not isinstance(editor, QLineEdit):
            return
        if combo.currentText() == "null":
            editor.setText("")
            editor.setEnabled(False)
        else:
            editor.setEnabled(True)

    def _add_inventory_row(self, item_id: str = "", quantity: int = 1) -> None:
        row = self.inventory_table.rowCount()
        self.inventory_table.insertRow(row)
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems(self.item_id_suggestions)
        combo.setCurrentText(item_id)
        self.inventory_table.setCellWidget(row, 0, combo)
        spin = QSpinBox()
        spin.setRange(0, 999999)
        spin.setValue(quantity)
        self.inventory_table.setCellWidget(row, 1, spin)

    def _add_stat_row(self, name: str = "", value: int | float = 0) -> None:
        row = self.stats_table.rowCount()
        self.stats_table.insertRow(row)
        self.stats_table.setCellWidget(row, 0, QLineEdit(name))
        self.stats_table.setCellWidget(row, 1, QLineEdit(str(value)))

    @staticmethod
    def _remove_row(table: QTableWidget) -> None:
        row = table.currentRow()
        if row >= 0:
            table.removeRow(row)

    def _remove_flag_row(self) -> None:
        self._remove_row(self.flags_table)

    def _remove_variable_row(self) -> None:
        self._remove_row(self.variables_table)

    def _remove_inventory_row(self) -> None:
        self._remove_row(self.inventory_table)

    def _remove_stat_row(self) -> None:
        self._remove_row(self.stats_table)

    def reset_configuration(self) -> None:
        for table in (self.flags_table, self.variables_table, self.inventory_table, self.stats_table):
            table.setRowCount(0)

    def set_configuration(self, configuration: SceneTestConfiguration) -> None:
        self.reset_configuration()
        for name, value in configuration.flags.items():
            self._add_flag_row(name, value)
        for name, value in configuration.variables.items():
            kind = "null" if value is None else "boolean" if isinstance(value, bool) else "integer" if isinstance(value, int) else "float" if isinstance(value, float) else "string"
            self._add_variable_row(name, value)
            self.variables_table.cellWidget(self.variables_table.rowCount() - 1, 1).setCurrentText(kind)
        for item_id, quantity in configuration.inventory.items():
            self._add_inventory_row(item_id, quantity)
        for name, value in configuration.stats.items():
            self._add_stat_row(name, value)

    @staticmethod
    def _names(values: list[str], section: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for name in values:
            name = name.strip()
            if not name:
                continue
            if name in result:
                raise DeveloperTestConfigError(f"Duplicate {section} name: {name}")
            result[name] = name
        return result

    def _read_configuration(self) -> SceneTestConfiguration:
        flags: dict[str, bool] = {}
        seen: set[str] = set()
        for row in range(self.flags_table.rowCount()):
            name_widget = self.flags_table.cellWidget(row, 0)
            value_widget = self.flags_table.cellWidget(row, 1)
            name = name_widget.currentText().strip() if isinstance(name_widget, QComboBox) else ""
            if not name:
                continue
            if name in seen:
                raise DeveloperTestConfigError(f"Duplicate flags name: {name}")
            seen.add(name)
            flags[name] = value_widget.currentText() == "true" if isinstance(value_widget, QComboBox) else False
        variables: dict[str, Any] = {}
        seen.clear()
        for row in range(self.variables_table.rowCount()):
            name_widget = self.variables_table.cellWidget(row, 0)
            type_widget = self.variables_table.cellWidget(row, 1)
            value_widget = self.variables_table.cellWidget(row, 2)
            name = name_widget.text().strip() if isinstance(name_widget, QLineEdit) else ""
            if not name:
                continue
            if name in seen:
                raise DeveloperTestConfigError(f"Duplicate variables name: {name}")
            seen.add(name)
            kind = type_widget.currentText() if isinstance(type_widget, QComboBox) else "string"
            raw = value_widget.text() if isinstance(value_widget, QLineEdit) else ""
            try:
                value: Any = None if kind == "null" else True if kind == "boolean" and raw.lower() == "true" else False if kind == "boolean" else int(raw) if kind == "integer" else float(raw) if kind == "float" else raw
            except ValueError as exc:
                raise DeveloperTestConfigError(f"Variable {name!r} is not a valid {kind}") from exc
            if kind == "boolean" and raw.lower() not in {"true", "false"}:
                raise DeveloperTestConfigError(f"Variable {name!r} must be true or false")
            variables[name] = value
        inventory: dict[str, int] = {}
        seen.clear()
        for row in range(self.inventory_table.rowCount()):
            item_widget = self.inventory_table.cellWidget(row, 0)
            quantity_widget = self.inventory_table.cellWidget(row, 1)
            item_id = item_widget.currentText().strip() if isinstance(item_widget, QComboBox) else ""
            if not item_id:
                continue
            if item_id in seen:
                raise DeveloperTestConfigError(f"Duplicate inventory item: {item_id}")
            seen.add(item_id)
            inventory[item_id] = quantity_widget.value() if isinstance(quantity_widget, QSpinBox) else 0
        stats: dict[str, int | float] = {}
        seen.clear()
        for row in range(self.stats_table.rowCount()):
            name_widget = self.stats_table.cellWidget(row, 0)
            value_widget = self.stats_table.cellWidget(row, 1)
            name = name_widget.text().strip() if isinstance(name_widget, QLineEdit) else ""
            if not name:
                continue
            if name in seen:
                raise DeveloperTestConfigError(f"Duplicate stats name: {name}")
            seen.add(name)
            raw = value_widget.text().strip() if isinstance(value_widget, QLineEdit) else ""
            try:
                value = float(raw) if "." in raw else int(raw)
            except ValueError as exc:
                raise DeveloperTestConfigError(f"Stat {name!r} must be an integer or float") from exc
            stats[name] = value
        return SceneTestConfiguration(flags=flags, variables=variables, inventory=inventory, stats=stats)

    def configuration(self) -> SceneTestConfiguration:
        return self._read_configuration()

    def _accept_configuration(self) -> None:
        try:
            self._read_configuration()
        except DeveloperTestConfigError as exc:
            QMessageBox.warning(self, "Invalid Test State", str(exc))
            return
        self.accept()


__all__ = ["TestStateDialog"]
