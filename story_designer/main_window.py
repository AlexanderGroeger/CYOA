"""Main window and application-level coordination for the Story Designer."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QKeySequence
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

from .models import (
    DefinitionSelection,
    ExternalChangeConflict,
    PersistenceError,
    ProjectValidationError,
    ProjectSession,
    normalize_story_root,
)
from .widgets import DiagnosticsWidget, InspectorWidget, ProjectBrowser, WorkspaceWidget


class MainWindow(QMainWindow):
    """Story/Core project browser and schema-driven editor shell."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Story Designer")
        self.resize(1280, 760)
        self.settings = QSettings("CYOA", "Story Designer")
        self.session = ProjectSession()

        self.browser = ProjectBrowser()
        self.inspector = InspectorWidget(self.session)
        self.workspace = WorkspaceWidget()
        self.diagnostics = DiagnosticsWidget()
        self.setCentralWidget(self.workspace)
        self._create_docks()
        self._create_menus()
        self.browser.selection_changed.connect(self._on_browser_selection)
        self.inspector.state_changed.connect(self._on_inspector_state_changed)
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
        self.save_action = QAction("Save", self)
        self.save_action.setShortcut(QKeySequence.StandardKey.Save)
        self.save_action.triggered.connect(self.save_story)
        file_menu.insertAction(self.close_action, self.save_action)
        self.save_all_action = QAction("Save All", self)
        self.save_all_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.save_all_action.triggered.connect(self.save_all_story)
        file_menu.insertAction(self.close_action, self.save_all_action)
        self.exit_action = QAction("Exit", self)
        self.exit_action.triggered.connect(self.close)
        file_menu.addAction(self.exit_action)

        edit_menu = self.menuBar().addMenu("Edit")
        self.undo_action = QAction("Undo", self)
        self.undo_action.setShortcuts([QKeySequence.StandardKey.Undo, QKeySequence("Ctrl+Z")])
        self.undo_action.triggered.connect(self.undo)
        edit_menu.addAction(self.undo_action)
        self.redo_action = QAction("Redo", self)
        self.redo_action.setShortcuts([QKeySequence.StandardKey.Redo, QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        self.redo_action.triggered.connect(self.redo)
        edit_menu.addAction(self.redo_action)
        edit_menu.addSeparator()
        self.revert_action = QAction("Revert Selected Definition", self)
        self.revert_action.triggered.connect(self.revert_selected)
        edit_menu.addAction(self.revert_action)

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
        if not self._confirm_unsaved_changes("open another story"):
            return False
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

    def close_story(self) -> bool:
        if not self._confirm_unsaved_changes("close this story"):
            return False
        self.session.close()
        self._refresh_views()
        self.statusBar().showMessage("No story loaded")
        return True

    def reload_story(self) -> bool:
        if self.session.story_root is None:
            return False
        if not self._confirm_unsaved_changes("reload this story"):
            return False
        try:
            self.session.reload()
        except (StoryProjectLoadError, StoryCoreError, OSError, ValueError, TypeError) as exc:
            QMessageBox.critical(self, "Could not reload story", str(exc))
            return False
        self._refresh_views()
        self.statusBar().showMessage("Story reloaded")
        return True

    def save_story(self) -> bool:
        return self._save(overwrite_external=False)

    def save_all_story(self) -> bool:
        return self._save(overwrite_external=False)

    def undo(self) -> bool:
        if not self.session.can_undo:
            return False
        self.session.undo()
        self._refresh_views()
        self.statusBar().showMessage("Edit undone")
        return True

    def redo(self) -> bool:
        if not self.session.can_redo:
            return False
        self.session.redo()
        self._refresh_views()
        self.statusBar().showMessage("Edit redone")
        return True

    def revert_selected(self) -> bool:
        if self.session.selection is None or not self.session.is_definition_dirty(self.session.selection):
            return False
        changed = self.session.revert_definition(self.session.selection)
        if changed:
            self._refresh_views()
            self.statusBar().showMessage("Selected definition reverted")
        return changed

    def _save(self, *, overwrite_external: bool, allow_validation_errors: bool = False) -> bool:
        try:
            result = self.session.save_all(
                overwrite_external=overwrite_external,
                allow_validation_errors=allow_validation_errors,
            )
        except ExternalChangeConflict as exc:
            return self._resolve_external_conflict(exc)
        except ProjectValidationError as exc:
            detail = "\n".join(item.format() for item in exc.diagnostics.errors)
            answer = QMessageBox.warning(
                self,
                "Story validation errors",
                "The projected story contains validation errors. Save anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                return self._save(
                    overwrite_external=overwrite_external,
                    allow_validation_errors=True,
                )
            self.statusBar().showMessage(detail or "Save cancelled because validation failed")
            return False
        except (PersistenceError, StoryProjectLoadError, StoryCoreError, OSError, ValueError, TypeError) as exc:
            QMessageBox.critical(self, "Could not save story", str(exc))
            self._refresh_views()
            return False
        if result:
            self._refresh_views()
            self.statusBar().showMessage("Story saved")
        return result

    def _resolve_external_conflict(self, conflict: ExternalChangeConflict) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Story changed on disk")
        box.setText("A source file changed on disk since this story was opened.")
        box.setInformativeText("Choose Reload to discard your edits, or Overwrite anyway to replace the external file.")
        reload_button = box.addButton("Reload and discard edits", QMessageBox.ButtonRole.AcceptRole)
        cancel_button = box.addButton(QMessageBox.StandardButton.Cancel)
        overwrite_button = box.addButton("Overwrite anyway", QMessageBox.ButtonRole.DestructiveRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is reload_button:
            try:
                self.session.reload()
            except (StoryProjectLoadError, StoryCoreError, OSError, ValueError, TypeError) as exc:
                QMessageBox.critical(self, "Could not reload story", str(exc))
                return False
            self._refresh_views()
            self.statusBar().showMessage("External changes loaded; edits discarded")
            return False
        if clicked is overwrite_button:
            return self._save(overwrite_external=True)
        return clicked is cancel_button and False

    def _confirm_unsaved_changes(self, action: str) -> bool:
        if not self.session.is_dirty:
            return True
        answer = QMessageBox.warning(
            self,
            "Unsaved changes",
            f"Save changes before you {action}?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            # A failed save deliberately keeps the action cancelled.  The
            # user must explicitly choose Discard in a later prompt.
            return self.save_story()
        if answer == QMessageBox.StandardButton.Discard:
            return True
        return False

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

    def _on_inspector_state_changed(self) -> None:
        """Refresh shell chrome without rebuilding the active editor form."""

        project = self.session.project
        if project is not None:
            self.workspace.set_state(
                project,
                self.session.selection,
                self.session.definition(),
                self.session.diagnostics,
            )
        self._update_status()
        self._update_action_state()

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
        self._update_action_state()

    def _update_status(self) -> None:
        project = self.session.project
        if project is None:
            self.setWindowTitle("Story Designer")
            self.statusBar().showMessage("No story loaded")
            return
        diagnostics = self.session.diagnostics
        modified = " *" if self.session.is_dirty else ""
        self.setWindowTitle(f"Story Designer — {project.manifest.id}{modified}")
        self.statusBar().showMessage(
            f"{project.manifest.id} — {len(project.scenes)} scenes — "
            f"{len(diagnostics.errors)} errors, {len(diagnostics.warnings)} warnings"
            f"{' — Modified' if self.session.is_dirty else ''}"
        )

    def _update_action_state(self) -> None:
        has_project = self.session.project is not None
        self.save_action.setEnabled(has_project and self.session.is_dirty)
        self.save_all_action.setEnabled(has_project and self.session.is_dirty)
        self.close_action.setEnabled(has_project)
        self.reload_action.setEnabled(has_project)
        self.undo_action.setEnabled(has_project and self.session.can_undo)
        self.redo_action.setEnabled(has_project and self.session.can_redo)
        self.revert_action.setEnabled(
            has_project
            and self.session.selection is not None
            and self.session.is_definition_dirty(self.session.selection)
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
        QMessageBox.about(self, "Story Designer", "A schema-driven Story/Core project browser and Inspector.")

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API name
        if not self._confirm_unsaved_changes("exit"):
            event.ignore()
            return
        self.settings.setValue("windowGeometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())
        event.accept()


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
