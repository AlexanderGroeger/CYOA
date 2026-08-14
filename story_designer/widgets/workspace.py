"""Central workspace placeholder designed for future editor tabs."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QLabel, QPlainTextEdit, QTabWidget, QVBoxLayout, QWidget

from engine.story_core import Diagnostics, StoryProject

from ..models import DefinitionSelection


class WorkspaceWidget(QWidget):
    """A tab-based center area with a read-only overview for this phase."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tabs = QTabWidget()
        self.overview_title = QLabel("Welcome to Story Designer")
        self.overview_title.setStyleSheet("font-size: 18px; font-weight: bold;")
        self.overview = QPlainTextEdit()
        self.overview.setReadOnly(True)
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.addWidget(self.overview_title)
        page_layout.addWidget(self.overview)
        self.tabs.addTab(page, "Overview")
        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        self.clear()

    def clear(self) -> None:
        self.overview_title.setText("Welcome to Story Designer")
        self.overview.setPlainText("Open a story project to browse its authored content.")

    def set_state(
        self,
        project: StoryProject | None,
        selection: DefinitionSelection | None,
        definition: Any | None,
        diagnostics: Diagnostics,
    ) -> None:
        if project is None:
            self.clear()
            return
        if selection is None or definition is None:
            self.overview_title.setText(project.manifest.title or project.manifest.id)
            self.overview.setPlainText(_project_summary(project, diagnostics))
            return
        self.overview_title.setText(f"{selection.kind.value.replace('_', ' ').title()}: {selection.id}")
        authored = getattr(definition, "authored", definition)
        self.overview.setPlainText(
            f"Source: {getattr(definition, 'source', selection.source)}\n\n"
            f"Authored fields: {len(authored) if hasattr(authored, '__len__') else 'n/a'}\n\n"
            "This workspace is read-only in Step 4. Future editors can add tabs here."
        )


def _project_summary(project: StoryProject, diagnostics: Diagnostics) -> str:
    counts = (
        f"Scenes: {len(project.scenes)}\n"
        f"Items: {len(project.items)}\n"
        f"Battles: {len(project.battles)}\n"
        f"Combat moves: {len(project.moves)}\n"
        f"Event pools: {len(project.event_pools)}\n"
        f"Animations: {len(project.animations)}\n"
    )
    return (
        f"Story root: {project.story_root}\n"
        f"Version: {project.manifest.version}\n"
        f"Start scene: {project.manifest.start_scene or '—'}\n\n"
        f"{counts}\n"
        f"Diagnostics: {len(diagnostics)} ({len(diagnostics.errors)} errors, "
        f"{len(diagnostics.warnings)} warnings)"
    )
