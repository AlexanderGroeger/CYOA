"""Reusable Story Designer asset browser and picker."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from engine.story_core import AssetRecord, StorySource

from ..models.assets import AssetBrowserModel


class AssetBrowserWidget(QWidget):
    """Discovery-focused browser that can also operate as a picker."""

    asset_selected = Signal(object)
    reference_selected = Signal(str)

    def __init__(
        self,
        source: StorySource | Any | None = None,
        *,
        expected_kind: str | None = None,
        picker_mode: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.model = AssetBrowserModel(_as_source(source))
        self.expected_kind = expected_kind
        self.picker_mode = picker_mode
        self.selected_asset: AssetRecord | None = None

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search filename or authored reference")
        self.search.textChanged.connect(self._refresh_list)
        self.type_filter = QComboBox()
        self.type_filter.addItem("All", "all")
        self.type_filter.currentIndexChanged.connect(self._refresh_list)
        self.source_filter = QComboBox()
        self.source_filter.addItems(("All", "Story", "Shared"))
        self.source_filter.currentIndexChanged.connect(self._refresh_list)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Search:"))
        controls.addWidget(self.search, 1)
        controls.addWidget(QLabel("Type:"))
        controls.addWidget(self.type_filter)
        controls.addWidget(QLabel("Source:"))
        controls.addWidget(self.source_filter)
        controls.addWidget(self.refresh_button)

        self.asset_list = QListWidget()
        self.asset_list.currentItemChanged.connect(self._item_changed)
        self.asset_list.itemDoubleClicked.connect(self._double_clicked)
        self.preview = QLabel("Select an asset")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(220, 150)
        self.preview.setWordWrap(True)
        self.reference_value = self._detail_label()
        self.resolved_value = self._detail_label()
        self.source_value = self._detail_label()
        self.category_value = self._detail_label()
        self.metadata_value = self._detail_label()
        details = QFormLayout()
        details.addRow("Reference", self.reference_value)
        details.addRow("Resolved path", self.resolved_value)
        details.addRow("Source", self.source_value)
        details.addRow("Category", self.category_value)
        details.addRow("Metadata", self.metadata_value)
        detail_panel = QWidget()
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.addWidget(self.preview, 1)
        detail_layout.addLayout(details)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.asset_list)
        splitter.addWidget(detail_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(splitter, 1)
        self.refresh()

    @staticmethod
    def _detail_label() -> QLabel:
        label = QLabel("—")
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    @property
    def records(self) -> tuple[AssetRecord, ...]:
        return self.model.records

    def set_source(self, source: StorySource | Any | None) -> None:
        self.model.set_source(_as_source(source))
        self.refresh()

    def set_expected_kind(self, expected_kind: str | None) -> None:
        self.expected_kind = expected_kind
        self._refresh_list()

    def refresh(self) -> None:
        self.model.refresh()
        existing = {self.type_filter.itemData(index) for index in range(self.type_filter.count())}
        for record in self.records:
            if record.asset_kind not in existing:
                self.type_filter.addItem(record.asset_kind.title(), record.asset_kind)
        self._refresh_list()

    def set_current_reference(self, reference: str, expected_kind: str | None = None) -> None:
        """Keep a missing authored reference visible in picker details."""

        if expected_kind is not None:
            self.expected_kind = expected_kind
        if not reference:
            return
        for index in range(self.asset_list.count()):
            item = self.asset_list.item(index)
            record = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(record, AssetRecord) and record.reference == reference:
                self.asset_list.setCurrentItem(item)
                return
        if self.model.source is not None:
            record = self.model.record_for_reference(reference, self.expected_kind)
            if record is not None and not record.exists:
                item = QListWidgetItem(f"{record.reference}  [missing]")
                item.setData(Qt.ItemDataRole.UserRole, record)
                self.asset_list.insertItem(0, item)
                self.asset_list.setCurrentItem(item)

    def filtered_records(self) -> tuple[AssetRecord, ...]:
        source = self.source_filter.currentData()
        return self.model.filtered(
            self.search.text(),
            asset_kind=self.type_filter.currentData(),
            source_kind=source,
            expected_kind=self.expected_kind,
        )

    def choose_current(self) -> AssetRecord | None:
        item = self.asset_list.currentItem()
        record = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not isinstance(record, AssetRecord):
            return None
        self.selected_asset = record
        self.asset_selected.emit(record)
        self.reference_selected.emit(record.reference)
        return record

    def _refresh_list(self) -> None:
        current_reference = self.selected_asset.reference if self.selected_asset is not None else None
        self.asset_list.blockSignals(True)
        self.asset_list.clear()
        for record in self.filtered_records():
            label = f"{record.reference}  [{record.source_kind}]"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, record)
            self.asset_list.addItem(item)
            if current_reference == record.reference:
                self.asset_list.setCurrentItem(item)
        self.asset_list.blockSignals(False)
        if self.asset_list.currentItem() is None and self.asset_list.count():
            self.asset_list.setCurrentRow(0)
        elif not self.asset_list.count():
            self._show_record(None)

    def _item_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        record = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        self._show_record(record if isinstance(record, AssetRecord) else None)

    def _show_record(self, record: AssetRecord | None) -> None:
        self.selected_asset = record
        if record is None:
            self.preview.setText("No matching assets")
            self.preview.setPixmap(QPixmap())
            for label in (self.reference_value, self.resolved_value, self.source_value, self.category_value, self.metadata_value):
                label.setText("—")
            return
        self.reference_value.setText(record.reference)
        self.resolved_value.setText(str(record.resolved_path) if record.resolved_path is not None else "Missing / unresolved")
        self.source_value.setText(record.source_kind)
        self.category_value.setText(record.asset_kind)
        metadata = dict(record.metadata)
        self.metadata_value.setText(", ".join(f"{key}: {value}" for key, value in metadata.items()) or "—")
        self._show_preview(record)

    def _show_preview(self, record: AssetRecord) -> None:
        path = _preview_path(record)
        if path is not None and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".gif"}:
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                self.preview.setPixmap(pixmap.scaled(self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                self.preview.setText("")
                return
            self.preview.setText("Unreadable image\n" + record.reference)
        elif record.asset_kind == "animation":
            self.preview.setText(f"Animation: {record.reference}\n{record.metadata.get('frame_count', 0)} frame(s)")
        elif record.resolved_path is None:
            self.preview.setText("Missing asset\n" + record.reference)
        else:
            self.preview.setText(f"{record.asset_kind.title()}\n{record.reference}")
        self.preview.setPixmap(QPixmap())

    def _double_clicked(self, _item: QListWidgetItem) -> None:
        if self.picker_mode:
            self.choose_current()


class AssetBrowserDialog(QDialog):
    """Modal wrapper around :class:`AssetBrowserWidget` for picker use."""

    def __init__(
        self,
        source: StorySource | Any | None = None,
        *,
        expected_kind: str | None = None,
        current_reference: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose Asset")
        self.resize(900, 560)
        self.browser = AssetBrowserWidget(source, expected_kind=expected_kind, picker_mode=True, parent=self)
        self.browser.asset_selected.connect(lambda _record: self.accept())
        if current_reference:
            self.browser.set_current_reference(current_reference, expected_kind)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.browser)
        layout.addWidget(buttons)

    @property
    def selected_reference(self) -> str | None:
        return self.browser.selected_asset.reference if self.browser.selected_asset is not None else None

    def _accept(self) -> None:
        if self.browser.choose_current() is not None:
            self.accept()


def _as_source(value: StorySource | Any | None) -> StorySource | None:
    if isinstance(value, StorySource):
        return value
    source = getattr(value, "source", None)
    return source if isinstance(source, StorySource) else None


def _preview_path(record: AssetRecord) -> Path | None:
    if record.resolved_path is None:
        return None
    if record.asset_kind != "animation":
        return record.resolved_path
    try:
        import yaml
        data = yaml.safe_load(record.resolved_path.read_text(encoding="utf-8"))
        frames = data.get("frames") if isinstance(data, dict) else None
        if isinstance(frames, list) and frames and isinstance(frames[0], str):
            candidate = record.resolved_path.parent / frames[0]
            return candidate if candidate.is_file() else None
    except (OSError, UnicodeError, ValueError):
        pass
    return None


__all__ = ["AssetBrowserDialog", "AssetBrowserWidget"]
