"""Main window and application-level coordination for the Story Designer."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from engine.story_core import StoryCoreError, StoryProjectLoadError

from .models import DefinitionSelection, ProjectSession, normalize_story_root
from .widgets import DiagnosticsWidget, InspectorWidget, ProjectBrowser, WorkspaceWidget


class MainWindow(QMainWindow):
    """Read-only Story/Core project browser shell."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Story Designer")
        self.resize(1280, 760)
        self.settings = QSettings("CYOA", "Story Designer")
        self.session = ProjectSession()

        self.browser = ProjectBrowser()
        self.inspector = InspectorWidget()
        self.workspace = WorkspaceWidget()
        self.diagnostics = DiagnosticsWidget()
        self.setCentralWidget(self.workspace)
        self._create_docks()
        self._create_menus()
        self.browser.selection_changed.connect(self._on_browser_selection)
        self._restore_window_state()
        self._refresh_views()

    def _create_docks(self) -> None:
        self.project_dock = QDockWidget("Project", self)
        self.project_dock.setObjectName("ProjectDock")
        self.project_dock.setWidget(self.browser)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.project_dock)

        self.inspector_dock = QDockWidget("Inspector", self)
        self.inspector_dock.setObjectName("InspectorDock")
        self.inspector_dock.setWidget(self.inspector)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.inspector_dock)

        self.diagnostics_dock = QDockWidget("Diagnostics", self)
        self.diagnostics_dock.setObjectName("DiagnosticsDock")
        self.diagnostics_dock.setWidget(self.diagnostics)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.diagnostics_dock)

    def _create_menus(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        self.open_action = QAction("Open Story...", self)
        self.open_action.triggered.connect(self.open_story)
        file_menu.addAction(self.open_action)
        self.open_directory_action = QAction("Open Story Directory...", self)
        self.open_directory_action.triggered.connect(self.open_story_directory)
        file_menu.addAction(self.open_directory_action)
        self.recent_menu = QMenu("Recent Stories", self)
        file_menu.addMenu(self.recent_menu)
        self.close_action = QAction("Close Story", self)
        self.close_action.triggered.connect(self.close_story)
        file_menu.addAction(self.close_action)
        self.reload_action = QAction("Reload Story", self)
        self.reload_action.triggered.connect(self.reload_story)
        file_menu.addAction(self.reload_action)
        file_menu.addSeparator()
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        edit_menu = self.menuBar().addMenu("Edit")

        view_menu = self.menuBar().addMenu("View")
        view_menu.addAction(self.project_dock.toggleViewAction())
        view_menu.addAction(self.inspector_dock.toggleViewAction())
        view_menu.addAction(self.diagnostics_dock.toggleViewAction())

        story_menu = self.menuBar().addMenu("Story")
        validate_action = QAction("Validate Story", self)
        validate_action.triggered.connect(self.validate_story)
        story_menu.addAction(validate_action)
        self.menuBar().addMenu("Test")
        help_menu = self.menuBar().addMenu("Help")
        about_action = QAction("About Story Designer", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)
        self._update_recent_menu()

    def open_story(self) -> None:
        choice = StorySelectionDialog(self).exec()
        if choice == QDialog.DialogCode.Accepted:
            path = StorySelectionDialog.selected_path(self)
            if path is not None:
                self.open_story_path(path)

    def open_story_directory(self) -> None:
        initial = self.settings.value("lastStoryPath", "")
        path = QFileDialog.getExistingDirectory(self, "Open Story Directory", str(initial))
        if path:
            self.open_story_path(Path(path))

    def open_story_path(self, path: str | Path) -> bool:
        root = normalize_story_root(path)
        try:
            self.session.load(root)
        except (StoryProjectLoadError, StoryCoreError, OSError, ValueError, TypeError) as exc:
            diagnostics = getattr(exc, "diagnostics", None)
            detail = str(exc)
            if diagnostics:
                detail += "\n\n" + "\n".join(item.format() for item in diagnostics)
            QMessageBox.critical(self, "Could not open story", detail)
            return False
        self.settings.setValue("lastStoryPath", str(root))
        self._remember_recent(root)
        self._refresh_views()
        self.statusBar().showMessage(f"Opened {root.name}")
        return True

    def close_story(self) -> None:
        self.session.close()
        self._refresh_views()
        self.statusBar().showMessage("No story loaded")

    def reload_story(self) -> bool:
        if self.session.story_root is None:
            return False
        try:
            self.session.reload()
        except (StoryProjectLoadError, StoryCoreError, OSError, ValueError, TypeError) as exc:
            QMessageBox.critical(self, "Could not reload story", str(exc))
            return False
        self._refresh_views()
        self.statusBar().showMessage("Story reloaded")
        return True

    def validate_story(self) -> None:
        if self.session.project is None:
            self.statusBar().showMessage("No story loaded")
            return
        self.session.validate()
        self._refresh_views()
        self.statusBar().showMessage("Story validation refreshed")

    def _on_browser_selection(self, selection: DefinitionSelection | None) -> None:
        self.session.select(selection)
        self._refresh_views()

    def _refresh_views(self) -> None:
        project = self.session.project
        selection = self.session.selection
        definition = self.session.definition()
        self.browser.set_project(project)
        if selection is not None:
            self.browser.select(selection)
        self.inspector.set_selection(project, selection, definition, self.session.diagnostics)
        self.workspace.set_state(project, selection, definition, self.session.diagnostics)
        self.diagnostics.set_diagnostics(self.session.diagnostics)
        self._update_status()

    def _update_status(self) -> None:
        project = self.session.project
        if project is None:
            self.statusBar().showMessage("No story loaded")
            return
        diagnostics = self.session.diagnostics
        self.statusBar().showMessage(
            f"{project.manifest.id} — {len(project.scenes)} scenes — "
            f"{len(diagnostics.errors)} errors, {len(diagnostics.warnings)} warnings"
        )

    def _restore_window_state(self) -> None:
        geometry = self.settings.value("windowGeometry")
        state = self.settings.value("windowState")
        if geometry:
            self.restoreGeometry(geometry)
        if state:
            self.restoreState(state)

    def _remember_recent(self, root: Path) -> None:
        recent = [str(root), *self._recent_paths()]
        unique = list(dict.fromkeys(recent))[:8]
        self.settings.setValue("recentStories", unique)
        self._update_recent_menu()

    def _recent_paths(self) -> list[str]:
        value = self.settings.value("recentStories", [])
        return [str(item) for item in value] if isinstance(value, (list, tuple)) else []

    def _update_recent_menu(self) -> None:
        self.recent_menu.clear()
        paths = self._recent_paths()
        if not paths:
            empty = QAction("(none)", self)
            empty.setEnabled(False)
            self.recent_menu.addAction(empty)
            return
        for path in paths:
            action = QAction(path, self)
            action.triggered.connect(lambda checked=False, value=path: self.open_story_path(value))
            self.recent_menu.addAction(action)

    def _show_about(self) -> None:
        QMessageBox.about(self, "Story Designer", "A read-only Story/Core project browser.")

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API name
        self.settings.setValue("windowGeometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())
        super().closeEvent(event)


class StorySelectionDialog(QDialog):
    """Small standard-dialog wrapper allowing file or directory selection."""

    _paths: dict[int, Path | None] = {}

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Open Story")
        self.setModal(True)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Choose a story.yaml file or a story directory."))
        file_button = QPushButton("Choose story.yaml...")
        directory_button = QPushButton("Choose story directory...")
        layout.addWidget(file_button)
        layout.addWidget(directory_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)
        file_button.clicked.connect(self._choose_file)
        directory_button.clicked.connect(self._choose_directory)
        buttons.rejected.connect(self.reject)
        self._selected: Path | None = None

    @classmethod
    def selected_path(cls, parent: QWidget) -> Path | None:
        return cls._paths.pop(id(parent), None)

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open story.yaml", "", "Story manifest (story.yaml)")
        if path:
            self._selected = Path(path)
            self._store_selection()
            self.accept()

    def _choose_directory(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Open Story Directory")
        if path:
            self._selected = Path(path)
            self._store_selection()
            self.accept()

    def _store_selection(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self._paths[id(parent)] = self._selected
