"""Reusable Story Designer asset browser and picker."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
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
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from engine.story_core import AssetRecord, StorySource, asset_category_label, canonical_asset_category
from engine.story_core.source import IMAGE_EXTENSIONS

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
        picker_category: str | None = None,
        allowed_categories: set[str] | tuple[str, ...] | list[str] | None = None,
        picker_mode: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.model = AssetBrowserModel(_as_source(source))
        requested = picker_category if picker_category is not None else expected_kind
        self.expected_kind = canonical_asset_category(requested)
        self.allowed_categories = {
            canonical_asset_category(value) or str(value).strip().casefold()
            for value in (allowed_categories or ())
        }
        if self.expected_kind:
            self.allowed_categories = {self.expected_kind}
        self.picker_mode = picker_mode
        self.selected_asset: AssetRecord | None = None
        self._preview_record: AssetRecord | None = None
        self._audio_record_key: tuple[str, str, str] | None = None

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search filename or authored reference")
        self.search.textChanged.connect(self._refresh_list)
        self.type_filter = QComboBox()
        self.type_filter.currentIndexChanged.connect(self._refresh_list)
        self.source_filter = QComboBox()
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
        self.audio_player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_player.setAudioOutput(self.audio_output)
        self.audio_player.positionChanged.connect(self._audio_position_changed)
        self.audio_player.durationChanged.connect(self._audio_duration_changed)
        self.audio_player.playbackStateChanged.connect(self._audio_state_changed)
        self.audio_player.errorOccurred.connect(self._audio_error)
        self.audio_play_pause = QPushButton("▶ Play")
        self.audio_play_pause.clicked.connect(self._toggle_audio)
        self.audio_slider = QSlider(Qt.Orientation.Horizontal)
        self.audio_slider.setRange(0, 0)
        self.audio_slider.sliderReleased.connect(self._seek_audio)
        self.audio_elapsed = QLabel("0:00")
        self.audio_total = QLabel("--:--")
        audio_controls = QHBoxLayout()
        audio_controls.addWidget(self.audio_play_pause)
        audio_controls.addWidget(self.audio_elapsed)
        audio_controls.addWidget(self.audio_slider, 1)
        audio_controls.addWidget(self.audio_total)
        self.audio_error_value = QLabel()
        self.audio_error_value.setWordWrap(True)
        self.audio_controls = QWidget()
        audio_layout = QVBoxLayout(self.audio_controls)
        audio_layout.setContentsMargins(0, 0, 0, 0)
        audio_layout.addLayout(audio_controls)
        audio_layout.addWidget(self.audio_error_value)
        self.audio_controls.hide()
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
        detail_layout.addWidget(self.audio_controls)
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
        self.expected_kind = canonical_asset_category(expected_kind)
        self.allowed_categories = {self.expected_kind} if self.expected_kind else set()
        self._rebuild_filters()
        self._refresh_list()

    def refresh(self) -> None:
        self.model.refresh()
        self._rebuild_filters()
        self._refresh_list()

    def _rebuild_filters(self) -> None:
        current_type = self.type_filter.currentData()
        current_source = self.source_filter.currentData()
        self.type_filter.blockSignals(True)
        self.source_filter.blockSignals(True)
        self.type_filter.clear()
        if self.expected_kind or self.allowed_categories:
            categories = tuple(self.allowed_categories)
        else:
            self.type_filter.addItem("All", "all")
            categories = self.model.available_categories()
        for category in categories:
            self.type_filter.addItem(asset_category_label(category), category)
        if not self.type_filter.count():
            self.type_filter.addItem("All", "all")
        if self.expected_kind or len(self.allowed_categories) == 1:
            locked = self.expected_kind or next(iter(self.allowed_categories), "all")
            self.type_filter.setCurrentIndex(max(0, self.type_filter.findData(locked)))
            self.type_filter.setEnabled(False)
        else:
            self.type_filter.setEnabled(True)
            index = self.type_filter.findData(current_type)
            self.type_filter.setCurrentIndex(index if index >= 0 else 0)
        self.source_filter.clear()
        self.source_filter.addItem("All Sources", "all")
        for source_kind in self.model.available_sources():
            self.source_filter.addItem(source_kind, source_kind)
        source_index = self.source_filter.findData(current_source)
        self.source_filter.setCurrentIndex(source_index if source_index >= 0 else 0)
        self.type_filter.blockSignals(False)
        self.source_filter.blockSignals(False)

    def set_current_reference(self, reference: str, expected_kind: str | None = None) -> None:
        """Keep a missing authored reference visible in picker details."""

        if expected_kind is not None:
            self.expected_kind = canonical_asset_category(expected_kind)
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
            categories=self.allowed_categories,
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
        current_key = _record_key(self.selected_asset)
        self.asset_list.blockSignals(True)
        self.asset_list.clear()
        for record in self.filtered_records():
            label = f"{record.reference}  [{record.source_kind}]"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, record)
            self.asset_list.addItem(item)
            if current_key == _record_key(record):
                self.asset_list.setCurrentItem(item)
        self.asset_list.blockSignals(False)
        if self.asset_list.currentItem() is None:
            # Opening or changing filters must not implicitly select the first
            # record.  A preview (especially audio) starts only after the user
            # explicitly selects an item, or a current authored reference is
            # restored by set_current_reference().
            self._show_record(None)
        elif current_key is not None and self.asset_list.currentItem() is not None:
            current = self.asset_list.currentItem().data(Qt.ItemDataRole.UserRole)
            if isinstance(current, AssetRecord) and _record_key(current) == current_key:
                self._show_record(current)

    def _item_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        record = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        self._show_record(record if isinstance(record, AssetRecord) else None)

    def _show_record(self, record: AssetRecord | None) -> None:
        if _record_key(record) != self._audio_record_key:
            self.stop_preview()
        self.selected_asset = record
        if record is None:
            self._preview_record = None
            self.preview.setText("No matching assets")
            self.preview.setPixmap(QPixmap())
            self.audio_controls.hide()
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
        if record.is_audio:
            if _record_key(record) != self._audio_record_key:
                self._start_audio_preview(record)
            else:
                self.audio_controls.show()
        else:
            self.audio_controls.hide()

    def _show_preview(self, record: AssetRecord) -> None:
        self._preview_record = record
        path = _preview_path(record)
        if path is not None and path.suffix.lower() in IMAGE_EXTENSIONS:
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                self.preview.setPixmap(pixmap.scaled(self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation))
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

    def _start_audio_preview(self, record: AssetRecord) -> None:
        self.audio_controls.show()
        self.audio_error_value.clear()
        self.audio_slider.setValue(0)
        self.audio_elapsed.setText("0:00")
        self.audio_total.setText("--:--")
        self._audio_record_key = _record_key(record)
        if record.resolved_path is None:
            self.audio_error_value.setText("Missing audio asset")
            return
        self.audio_player.setSource(QUrl.fromLocalFile(str(record.resolved_path)))
        self.audio_player.play()

    def stop_preview(self) -> None:
        """Stop and clear Qt media so no preview survives the browser."""

        self.audio_player.stop()
        self.audio_player.setSource(QUrl())
        self._audio_record_key = None
        self.audio_slider.setRange(0, 0)
        self.audio_slider.setValue(0)
        self.audio_elapsed.setText("0:00")
        self.audio_total.setText("--:--")
        self.audio_play_pause.setText("▶ Play")

    def _toggle_audio(self) -> None:
        if self.audio_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.audio_player.pause()
        elif self._audio_record_key is not None:
            self.audio_player.play()

    def _audio_position_changed(self, position: int) -> None:
        self.audio_elapsed.setText(_format_time(position))
        if not self.audio_slider.isSliderDown():
            self.audio_slider.setValue(position)

    def _audio_duration_changed(self, duration: int) -> None:
        self.audio_slider.setRange(0, max(0, duration))
        self.audio_total.setText(_format_time(duration) if duration > 0 else "--:--")

    def _audio_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        self.audio_play_pause.setText("⏸ Pause" if state == QMediaPlayer.PlaybackState.PlayingState else "▶ Play")

    def _seek_audio(self) -> None:
        self.audio_player.setPosition(self.audio_slider.value())

    def _audio_error(self, _error: QMediaPlayer.Error) -> None:
        message = self.audio_player.errorString() or "Qt Multimedia could not decode this preview."
        self.audio_error_value.setText(f"Preview unavailable: {message}")

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt API name
        super().resizeEvent(event)
        if self._preview_record is not None and self._preview_record.is_image:
            self._show_preview(self._preview_record)

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt API name
        self.stop_preview()
        super().closeEvent(event)

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
        picker_category: str | None = None,
        allowed_categories: set[str] | tuple[str, ...] | list[str] | None = None,
        title: str | None = None,
        current_reference: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        requested = picker_category if picker_category is not None else expected_kind
        self.setWindowTitle(title or _picker_title(requested, allowed_categories))
        self.resize(900, 560)
        self.browser = AssetBrowserWidget(
            source,
            expected_kind=expected_kind,
            picker_category=picker_category,
            allowed_categories=allowed_categories,
            picker_mode=True,
            parent=self,
        )
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

    def accept(self) -> None:
        self.browser.stop_preview()
        super().accept()

    def reject(self) -> None:
        self.browser.stop_preview()
        super().reject()

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt API name
        self.browser.stop_preview()
        super().closeEvent(event)


def _as_source(value: StorySource | Any | None) -> StorySource | None:
    if isinstance(value, StorySource):
        return value
    source = getattr(value, "source", None)
    return source if isinstance(source, StorySource) else None


def _record_key(record: AssetRecord | None) -> tuple[str, str, str] | None:
    if record is None:
        return None
    return (record.source_kind.casefold(), record.asset_kind.casefold(), record.reference.casefold())


def _format_time(milliseconds: int) -> str:
    total_seconds = max(0, int(milliseconds)) // 1000
    seconds = total_seconds % 60
    minutes = (total_seconds // 60) % 60
    hours = total_seconds // 3600
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def _picker_title(category: str | None, allowed_categories: set[str] | tuple[str, ...] | list[str] | None) -> str:
    categories = {
        canonical_asset_category(value) or str(value).casefold()
        for value in (allowed_categories or ())
    }
    if category:
        categories.add(canonical_asset_category(category) or str(category).casefold())
    if len(categories) == 1:
        label = next(iter(categories))
        singular = {
            "backgrounds": "Background",
            "sprites": "Sprite",
            "items": "Item Image",
            "music": "Music",
            "sfx": "Sound Effect",
            "fonts": "Font",
            "animation": "Animation",
        }.get(label, asset_category_label(label).rstrip("s"))
        return f"Select {singular}"
    return "Select Asset"


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
