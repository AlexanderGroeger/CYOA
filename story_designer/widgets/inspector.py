"""Read-only metadata inspector for Step 4."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from engine.story_core import ContentKind, DiagnosticSeverity, Diagnostics, StoryProject

from ..models import DefinitionSelection


class InspectorWidget(QWidget):
    """Display provenance and basic authored metadata without editing it."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.type_value = QLabel("—")
        self.id_value = QLabel("—")
        self.source_value = QLabel("—")
        self.definition_value = QLabel("—")
        self.validation_value = QLabel("—")
        for label in (self.type_value, self.id_value, self.source_value, self.definition_value, self.validation_value):
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form = QFormLayout()
        form.addRow("Type", self.type_value)
        form.addRow("ID", self.id_value)
        form.addRow("Source", self.source_value)
        form.addRow("Definition", self.definition_value)
        form.addRow("Validation", self.validation_value)

        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setPlaceholderText("Select a definition to inspect it.")
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.summary)
        self.clear()

    def clear(self) -> None:
        self.type_value.setText("—")
        self.id_value.setText("—")
        self.source_value.setText("—")
        self.definition_value.setText("—")
        self.validation_value.setText("—")
        self.summary.clear()

    def set_selection(
        self,
        project: StoryProject | None,
        selection: DefinitionSelection | None,
        definition: Any | None,
        diagnostics: Diagnostics,
    ) -> None:
        if project is None or selection is None or definition is None:
            self.clear()
            return
        self.type_value.setText(_display_kind(selection.kind))
        self.id_value.setText(selection.id)
        source = getattr(definition, "source", selection.source)
        self.source_value.setText(_relative_source(project.story_root, source))
        self.definition_value.setText(type(definition).__name__)
        relevant = [item for item in diagnostics if item.source == source]
        if any(item.severity is DiagnosticSeverity.ERROR for item in relevant):
            status = "Error"
        elif any(item.severity is DiagnosticSeverity.WARNING for item in relevant):
            status = "Warning"
        elif relevant:
            status = "Advisory"
        else:
            status = "Valid"
        self.validation_value.setText(status)
        authored = getattr(definition, "authored", definition)
        self.summary.setPlainText(_compact_mapping(authored))


def _display_kind(kind: ContentKind) -> str:
    return {
        ContentKind.EVENT_POOL: "Event Pool",
        ContentKind.MOVE: "Combat Move",
        ContentKind.AUDIO: "Audio Configuration",
    }.get(kind, kind.value.replace("_", " ").title())


def _relative_source(root: Path, source: Path | None) -> str:
    if source is None:
        return "<project>"
    try:
        return source.relative_to(root).as_posix()
    except ValueError:
        return str(source)


def _compact_mapping(value: Any) -> str:
    if not isinstance(value, Mapping):
        return repr(value)
    lines = []
    for key, item in value.items():
        rendered = repr(item)
        if len(rendered) > 180:
            rendered = rendered[:177] + "..."
        lines.append(f"{key}: {rendered}")
    return "\n".join(lines) or "(empty authored mapping)"
