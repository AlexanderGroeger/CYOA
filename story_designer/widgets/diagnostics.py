"""Structured Story/Core diagnostics table."""

from __future__ import annotations

from engine.story_core import Diagnostic, Diagnostics
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import QTableView, QVBoxLayout, QWidget


class DiagnosticsModel(QAbstractTableModel):
    HEADERS = ("Severity", "Source", "Path", "Code", "Message")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._diagnostics = Diagnostics()

    @property
    def diagnostics(self) -> Diagnostics:
        return self._diagnostics

    def set_diagnostics(self, diagnostics: Diagnostics) -> None:
        self.beginResetModel()
        self._diagnostics = diagnostics.copy()
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._diagnostics)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._diagnostics)):
            return None
        diagnostic = self._diagnostics[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            values = (
                diagnostic.severity.value,
                str(diagnostic.source) if diagnostic.source is not None else "<project>",
                diagnostic.path_text,
                diagnostic.code,
                diagnostic.message,
            )
            return values[index.column()]
        if role == Qt.ItemDataRole.ToolTipRole:
            return diagnostic.format()
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None


class DiagnosticsWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = DiagnosticsModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(self.table)

    def set_diagnostics(self, diagnostics: Diagnostics) -> None:
        self.model.set_diagnostics(diagnostics)

    def clear(self) -> None:
        self.set_diagnostics(Diagnostics())
