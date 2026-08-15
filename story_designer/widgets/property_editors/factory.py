"""Native Qt editors generated from :class:`PropertyDescriptor` metadata.

The widgets in this module only report user intent.  They do not know about a
working copy or a ``StoryProject``; the Inspector is responsible for turning
their signals into edit commands.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...models import PropertyDescriptor
from engine.story_core.schema import MISSING
from ..condition_editor import ConditionEditorWidget


def display_value(value: Any) -> str:
    """Render semantic values compactly for read-only generic fields."""

    if value is MISSING:
        return ""
    if isinstance(value, dict):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return repr(value)
    return str(value)


class _IntentMixin:
    """Common signal and initialization guard for concrete editor widgets."""

    value_edited = Signal(object)

    def _init_intent(self) -> None:
        self._editor_initializing = False

    def _emit_intent(self, value: Any) -> None:
        if not getattr(self, "_editor_initializing", False):
            self.value_edited.emit(value)

    def set_initializing(self, initializing: bool) -> None:
        self._editor_initializing = initializing


class StringEditor(_IntentMixin, QLineEdit):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._init_intent()
        self.editingFinished.connect(lambda: self._emit_intent(self.text()))


class MultilineEditor(_IntentMixin, QPlainTextEdit):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._init_intent()
        self.setTabChangesFocus(True)
        self._text_at_focus = ""

    def focusInEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt API name
        self._text_at_focus = self.toPlainText()
        super().focusInEvent(event)

    def focusOutEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt API name
        super().focusOutEvent(event)
        if self.toPlainText() != self._text_at_focus:
            self._emit_intent(self.toPlainText())


class IntegerEditor(_IntentMixin, QSpinBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._init_intent()
        self.valueChanged.connect(lambda value: self._emit_intent(int(value)))


class FloatEditor(_IntentMixin, QDoubleSpinBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._init_intent()
        self.valueChanged.connect(lambda value: self._emit_intent(float(value)))


class BooleanEditor(_IntentMixin, QCheckBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._init_intent()
        self.toggled.connect(lambda value: self._emit_intent(bool(value)))


class EnumEditor(_IntentMixin, QComboBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._init_intent()
        self.currentIndexChanged.connect(self._on_index_changed)

    def _on_index_changed(self, index: int) -> None:
        self._emit_intent(self.itemData(index))


class ReferenceComboBox(EnumEditor):
    """Combo box whose item data remains the serialized reference ID."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("reference_editor", True)


class AssetPathEditor(_IntentMixin, QWidget):
    """Portable asset editor with a Story/Core-backed picker."""

    def __init__(
        self,
        *,
        story_root: Path | None = None,
        asset_kind: str | None = None,
        project: Any | None = None,
        source: Any | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._init_intent()
        self.story_root = story_root
        self.asset_kind = asset_kind
        self.project = project
        self.source = source or getattr(project, "source", None)
        self.line_edit = QLineEdit(self)
        self.browse_button = QToolButton(self)
        self.browse_button.setText("Choose Asset…")
        self.browse_button.setToolTip("Choose a known story or shared asset.")
        self.browse_button.clicked.connect(self._browse)
        self.file_button = QToolButton(self)
        self.file_button.setText("Files…")
        self.file_button.setToolTip("Choose an existing file; external files cannot be authored until imported.")
        self.file_button.clicked.connect(self._browse_files)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.line_edit, 1)
        layout.addWidget(self.browse_button)
        layout.addWidget(self.file_button)
        self.line_edit.editingFinished.connect(lambda: self._emit_intent(self.line_edit.text()))

    def text(self) -> str:
        return self.line_edit.text()

    def setText(self, value: str) -> None:  # noqa: N802 - QLineEdit-compatible API
        self.line_edit.setText(value)

    def _browse(self) -> None:
        from ..asset_browser import AssetBrowserDialog

        dialog = AssetBrowserDialog(
            self.source or self.project,
            expected_kind=self.asset_kind,
            current_reference=self.text(),
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted or not dialog.selected_reference:
            return
        self.line_edit.setText(dialog.selected_reference)
        self._emit_intent(dialog.selected_reference)

    def _browse_files(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "Choose asset")
        if not selected:
            return
        candidate = Path(selected)
        source = self.source or getattr(self.project, "source", None)
        if source is not None:
            reference = source.authored_asset_reference(candidate, self.asset_kind)
            if reference is None:
                QMessageBox.warning(
                    self,
                    "External asset",
                    "This file is outside the known story/shared asset roots.\n\n"
                    "Copy or import it into a supported asset root before using it.",
                )
                return
        elif self.story_root is None:
            self.line_edit.setToolTip("No story source is loaded; enter a portable authored path.")
            return
        else:
            try:
                reference = candidate.resolve().relative_to(self.story_root.resolve()).as_posix()
            except ValueError:
                QMessageBox.warning(self, "External asset", "External files must be copied/imported before use.")
                return
        self.line_edit.setText(reference)
        self._emit_intent(self.line_edit.text())


class ReadOnlyEditor(QPlainTextEdit):
    """Graceful fallback for collections and complex union-like values."""

    def __init__(self, value: Any, reason: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumHeight(64)
        self.setPlainText(display_value(value))
        self.setToolTip(reason)
        self.setPlaceholderText("Read-only in generic Inspector")


class PropertyEditorFactory:
    """Map schema type metadata to native Qt controls."""

    def create(
        self,
        descriptor: PropertyDescriptor,
        *,
        story_root: Path | None = None,
        project: Any | None = None,
        parent: QWidget | None = None,
    ) -> QWidget:
        type_spec = descriptor.type_spec
        kind = type_spec.kind if type_spec is not None else "unknown"

        if not descriptor.is_editable or not descriptor.supported:
            return ReadOnlyEditor(
                descriptor.effective_value,
                descriptor.unsupported_reason or "This property is read-only.",
                parent,
            )
        if kind == "string":
            return StringEditor(parent)
        if kind == "multiline_string":
            return MultilineEditor(parent)
        if kind == "integer":
            return self._integer(descriptor, parent)
        if kind in {"float", "number"}:
            return self._float(descriptor, parent)
        if kind == "boolean":
            return BooleanEditor(parent)
        if kind == "enum":
            return self._enum(descriptor, parent)
        if kind == "reference":
            return self._reference(descriptor, parent)
        if kind == "asset":
            return AssetPathEditor(
                story_root=story_root,
                asset_kind=descriptor.asset_kind,
                project=project,
                source=getattr(project, "source", None),
                parent=parent,
            )
        if kind == "condition":
            return ConditionEditorWidget(project=project, parent=parent)
        return ReadOnlyEditor(
            descriptor.effective_value,
            "Read-only in generic Inspector; use a specialized editor for this type.",
            parent,
        )

    @staticmethod
    def _integer(descriptor: PropertyDescriptor, parent: QWidget | None) -> IntegerEditor:
        editor = IntegerEditor(parent)
        minimum = descriptor.minimum if descriptor.minimum is not None else -2_147_483_648
        maximum = descriptor.maximum if descriptor.maximum is not None else 2_147_483_647
        editor.setRange(max(-2_147_483_648, int(minimum)), min(2_147_483_647, int(maximum)))
        editor.setSingleStep(1)
        return editor

    @staticmethod
    def _float(descriptor: PropertyDescriptor, parent: QWidget | None) -> FloatEditor:
        editor = FloatEditor(parent)
        minimum = descriptor.minimum if descriptor.minimum is not None else -1_000_000_000.0
        maximum = descriptor.maximum if descriptor.maximum is not None else 1_000_000_000.0
        editor.setRange(max(-1_000_000_000.0, float(minimum)), min(1_000_000_000.0, float(maximum)))
        decimals = descriptor.type_spec.options.get("decimals") if descriptor.type_spec is not None else None
        editor.setDecimals(max(0, min(6, int(decimals))) if isinstance(decimals, int) else 3)
        editor.setSingleStep(0.1 if editor.decimals() else 1.0)
        return editor

    @staticmethod
    def _enum(descriptor: PropertyDescriptor, parent: QWidget | None) -> EnumEditor:
        editor = EnumEditor(parent)
        values = descriptor.allowed_values
        if not values and descriptor.type_spec is not None:
            values = descriptor.type_spec.enum_values
        for value in values:
            editor.addItem(str(value).replace("_", " ").title(), value)
        return editor

    @staticmethod
    def _reference(descriptor: PropertyDescriptor, parent: QWidget | None) -> ReferenceComboBox:
        editor = ReferenceComboBox(parent)
        if not descriptor.required:
            editor.addItem("— (none)", None)
        current = descriptor.effective_value
        values = list(descriptor.reference_candidates)
        if isinstance(current, str) and current and current not in values:
            editor.addItem(f"{current}  ⚠", current)
        for identifier in values:
            editor.addItem(identifier, identifier)
        return editor


def create_property_editor(
    descriptor: PropertyDescriptor,
    *,
    story_root: Path | None = None,
    project: Any | None = None,
    parent: QWidget | None = None,
) -> QWidget:
    """Convenience function for callers that do not need a factory instance."""

    return PropertyEditorFactory().create(descriptor, story_root=story_root, project=project, parent=parent)


__all__ = [
    "AssetPathEditor",
    "BooleanEditor",
    "EnumEditor",
    "FloatEditor",
    "IntegerEditor",
    "MultilineEditor",
    "PropertyEditorFactory",
    "ReadOnlyEditor",
    "ReferenceComboBox",
    "StringEditor",
    "create_property_editor",
]
