"""Main window and application-level coordination for the Story Designer."""

from __future__ import annotations

from pathlib import Path
import tempfile

from PySide6.QtCore import QProcess, QSettings, QTimer, Qt
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

from engine.story_core import ContentKind, StoryCoreError, StoryProjectLoadError

from .models import (
    DefinitionSelection,
    ExternalChangeConflict,
    PersistenceError,
    ProjectValidationError,
    ProjectSession,
    normalize_story_root,
)
from .widgets import AssetBrowserWidget, DiagnosticsWidget, InspectorWidget, ProjectBrowser, WorkspaceWidget
from .models import SceneGraphEdge
from .widgets.test_state import TestStateDialog
from .services.runtime_test import SceneTestConfiguration, SceneTestLaunch, resolve_scene_id
from engine.core.developer_test import DeveloperTestConfigError


class MainWindow(QMainWindow):
    """Story/Core project browser and schema-driven editor shell."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Story Designer")
        self.resize(1280, 760)
        self.settings = QSettings("CYOA", "Story Designer")
        self.session = ProjectSession()
        self.test_process: QProcess | None = None
        self._test_scene_id: str | None = None
        self._test_output: list[str] = []
        self._test_error_reported = False
        self._test_configuration = SceneTestConfiguration()
        self._test_config_path: Path | None = None
        self._test_context_valid = False
        self._pending_restart = False
        self._termination_for_restart = False
        self._test_stop_was_requested = False
        self._closing = False

        self.browser = ProjectBrowser()
        self.asset_browser = AssetBrowserWidget()
        self.inspector = InspectorWidget(self.session)
        self.workspace = WorkspaceWidget(self.session)
        self.diagnostics = DiagnosticsWidget()
        self.setCentralWidget(self.workspace)
        self._create_docks()
        self._create_menus()
        self.browser.selection_changed.connect(self._on_browser_selection)
        self.workspace.scene_element_selected.connect(self._on_scene_element_selection)
        self.workspace.scene_editor.geometry_committed.connect(lambda _ref: self._refresh_views())
        self.workspace.scene_editor.structure_changed.connect(lambda _ref: self._refresh_views())
        self.workspace.scene_editor.navigation_changed.connect(lambda _ref: self._refresh_views())
        self.workspace.scene_editor.open_destination_scene.connect(self._open_destination_scene)
        self.workspace.dialogue_changed.connect(lambda _ref: self._refresh_views())
        self.workspace.dialogue_entry_selected.connect(lambda _ref: self.inspector.clear_scene_element())
        self.workspace.graph_scene_selected.connect(self._on_graph_selection)
        self.workspace.graph_scene_open_requested.connect(self._open_graph_scene)
        self.workspace.graph_open_navigation.connect(self._open_graph_navigation)
        self.workspace.scene_editor.geometry_error.connect(self.statusBar().showMessage)
        self.inspector.scene_geometry_edited.connect(self._on_scene_geometry_edit)
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

        self.assets_dock = QDockWidget("Assets", self)
        self.assets_dock.setObjectName("AssetsDock")
        self.assets_dock.setWidget(self.asset_browser)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.assets_dock)
        self.tabifyDockWidget(self.diagnostics_dock, self.assets_dock)

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
        view_menu.addAction(self.assets_dock.toggleViewAction())
        self.scene_graph_action = QAction("Scene Graph", self)
        self.scene_graph_action.setShortcut(QKeySequence("Ctrl+Shift+G"))
        self.scene_graph_action.triggered.connect(self.workspace.show_scene_graph)
        view_menu.addSeparator()
        view_menu.addAction(self.scene_graph_action)

        story_menu = self.menuBar().addMenu("Story")
        validate_action = QAction("Validate Story", self)
        validate_action.triggered.connect(self.validate_story)
        story_menu.addAction(validate_action)
        scene_menu = self.menuBar().addMenu("Scene")
        scene_menu.addAction(self.workspace.scene_editor.add_object_action)
        scene_menu.addAction(self.workspace.scene_editor.add_look_region_action)
        scene_menu.addSeparator()
        scene_menu.addAction(self.workspace.scene_editor.duplicate_action)
        scene_menu.addAction(self.workspace.scene_editor.delete_action)
        test_menu = self.menuBar().addMenu("Test")
        self.configure_test_state_action = QAction("Configure Test State...", self)
        self.configure_test_state_action.triggered.connect(self.configure_test_state)
        test_menu.addAction(self.configure_test_state_action)
        test_menu.addSeparator()
        self.test_current_scene_action = QAction("Test Current Scene", self)
        self.test_current_scene_action.setStatusTip("Launch the pygame runtime in the selected scene")
        self.test_current_scene_action.triggered.connect(self.test_current_scene)
        test_menu.addAction(self.test_current_scene_action)
        self.restart_test_action = QAction("Restart Test", self)
        self.restart_test_action.triggered.connect(self.restart_test)
        test_menu.addAction(self.restart_test_action)
        self.stop_test_action = QAction("Stop Test", self)
        self.stop_test_action.triggered.connect(self.stop_test)
        test_menu.addAction(self.stop_test_action)
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
        self._test_configuration = SceneTestConfiguration()
        self._test_context_valid = False
        self.settings.setValue("lastStoryPath", str(root))
        self._remember_recent(root)
        self._refresh_views()
        self.statusBar().showMessage(f"Opened {root.name}")
        return True

    def close_story(self) -> bool:
        if not self._confirm_unsaved_changes("close this story"):
            return False
        self.session.close()
        self._test_configuration = SceneTestConfiguration()
        self._test_context_valid = False
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

    def current_scene_id(self) -> str | None:
        """Return the scene represented by the current editor context."""

        project = self.session.project
        if project is None:
            return None
        selection = self.session.selection
        scene_id = resolve_scene_id(selection, project)
        if scene_id is not None:
            return scene_id
        # A non-scene top-level selection must not inherit stale graphical
        # context from a previously displayed scene.  Current scene-local
        # selections already keep the parent scene in ``session.selection``;
        # the fallbacks below are only for editor-local selections when the
        # top-level selection is absent.
        if selection is not None:
            return None
        # Scene-local selections are intentionally editor-only and normally
        # leave ``session.selection`` on the parent scene.  These fallbacks
        # also keep the command useful if a future editor changes that rule.
        editor = self.workspace.scene_editor
        for local_selection in (
            getattr(editor, "selected_element", None),
            getattr(getattr(editor, "navigation_panel", None), "selected_entry", None),
            getattr(self.workspace.dialogue_editor, "selected_entry", None),
        ):
            scene_id = resolve_scene_id(local_selection, project)
            if scene_id is not None:
                return scene_id
        return None

    @property
    def test_process_running(self) -> bool:
        return self.test_process is not None and self.test_process.state() != QProcess.ProcessState.NotRunning

    def configure_test_state(self) -> bool:
        """Edit the reusable, ephemeral launch-time test state."""

        if self.session.project is None:
            self.statusBar().showMessage("Open a story before configuring test state")
            return False
        dialog = TestStateDialog(self.session.project, self._test_configuration, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        try:
            self._test_configuration = dialog.configuration()
        except DeveloperTestConfigError as exc:
            QMessageBox.warning(self, "Invalid Test State", str(exc))
            return False
        self.statusBar().showMessage("Test state updated")
        self._update_action_state()
        return True

    def _prepare_test_launch(self, scene_id: str) -> bool:
        if self.session.story_root is None:
            return False
        if self.session.is_dirty and not self.save_story():
            return False
        self.session.validate()
        if self.session.diagnostics.errors:
            answer = QMessageBox.warning(
                self,
                "Story validation errors",
                "The story contains validation errors. Test the scene anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        return True

    def test_current_scene(self) -> bool:
        """Save the authored project and launch one independent pygame test."""

        scene_id = self.current_scene_id()
        if scene_id is None or self.session.story_root is None:
            self.statusBar().showMessage("Select a scene to test")
            return False
        if self.test_process_running:
            self.statusBar().showMessage(f"Already testing: {self._test_scene_id or scene_id}")
            return False

        if not self._prepare_test_launch(scene_id):
            return False
        return self._launch_test(scene_id)

    def _launch_test(self, scene_id: str) -> bool:
        """Create one temporary config and start the child process."""
        if self.session.story_root is None or self.test_process_running:
            return False
        try:
            temporary = tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".json", prefix="cyoa-test-", delete=False
            )
            temporary.close()
            self._test_config_path = Path(temporary.name)
            self._test_configuration.write_json(self._test_config_path)
        except (OSError, DeveloperTestConfigError) as exc:
            if self._test_config_path is not None:
                self._cleanup_test_config()
            QMessageBox.critical(self, "Could not prepare test state", str(exc))
            return False
        launch = SceneTestLaunch(
            story_root=self.session.story_root,
            scene_id=scene_id,
            shared_assets_root=self.session.shared_assets_root,
            test_config_path=self._test_config_path,
        )
        program, arguments, working_directory = launch.command()
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.readyReadStandardOutput.connect(self._read_test_stdout)
        process.readyReadStandardError.connect(self._read_test_stderr)
        process.errorOccurred.connect(self._on_test_process_error)
        process.finished.connect(self._on_test_process_finished)
        self.test_process = process
        self._test_scene_id = scene_id
        self._test_output = []
        self._test_error_reported = False
        self._test_context_valid = True
        process.start(program, arguments)
        self.statusBar().showMessage(f"Testing: {scene_id}")
        self._update_action_state()
        return True

    def restart_test(self) -> bool:
        """Restart asynchronously with the selected scene and current state."""

        scene_id = self.current_scene_id()
        if scene_id is None or self.session.story_root is None:
            self.statusBar().showMessage("Select a scene to restart")
            return False
        if not self._test_context_valid and not self.test_process_running:
            return self.test_current_scene()
        if not self._prepare_test_launch(scene_id):
            return False
        self._pending_restart = True
        if self.test_process_running:
            self._request_test_termination(for_restart=True)
            self.statusBar().showMessage("Restarting test...")
            return True
        self._pending_restart = False
        return self._launch_test(scene_id)

    def stop_test(self) -> bool:
        if not self.test_process_running:
            return False
        self._pending_restart = False
        self._request_test_termination(for_restart=False)
        self.statusBar().showMessage("Stopping test...")
        return True

    def _request_test_termination(self, *, for_restart: bool) -> None:
        process = self.test_process
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return
        self._termination_for_restart = for_restart
        self._test_stop_was_requested = not for_restart
        process.terminate()
        QTimer.singleShot(1500, lambda: self._force_kill_test_process(process))

    def _force_kill_test_process(self, process: QProcess) -> None:
        if self.test_process is process and process.state() != QProcess.ProcessState.NotRunning:
            process.kill()

    def _read_test_stdout(self) -> None:
        if self.test_process is None:
            return
        data = bytes(self.test_process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if data:
            self._test_output.append(data)

    def _read_test_stderr(self) -> None:
        if self.test_process is None:
            return
        data = bytes(self.test_process.readAllStandardError()).decode("utf-8", errors="replace")
        if data:
            self._test_output.append(data)

    def _on_test_process_error(self, error: QProcess.ProcessError) -> None:
        if self._closing or self._test_error_reported:
            return
        if error != QProcess.ProcessError.FailedToStart:
            # Crashes and other non-zero exits are reported by ``finished``
            # together with captured stdout/stderr.
            return
        self._test_error_reported = True
        detail = self.test_process.errorString() if self.test_process is not None else str(error)
        QMessageBox.critical(self, "Could not launch test runtime", detail)
        self.statusBar().showMessage("Test runtime failed to launch")
        if self.test_process is None or self.test_process.state() == QProcess.ProcessState.NotRunning:
            self._cleanup_test_config()

    def _on_test_process_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        self._read_test_stdout()
        self._read_test_stderr()
        scene_id = self._test_scene_id or "scene"
        diagnostics = "".join(self._test_output).strip()
        failed = exit_code != 0
        restarting = self._pending_restart and not self._closing
        stopping = self._test_stop_was_requested
        self.test_process = None
        self._test_scene_id = None
        self._cleanup_test_config()
        self._termination_for_restart = False
        self._pending_restart = False if restarting else self._pending_restart
        self._update_action_state()
        if self._closing:
            return
        if restarting:
            next_scene = self.current_scene_id()
            if next_scene is not None and self._prepare_test_launch(next_scene):
                self._launch_test(next_scene)
            else:
                self.statusBar().showMessage("Test restart cancelled")
            return
        if stopping:
            self._test_stop_was_requested = False
            self.statusBar().showMessage("Test stopped")
            return
        if failed and not self._test_error_reported:
            detail = f"The test runtime exited with code {exit_code}."
            if diagnostics:
                detail += f"\n\n{diagnostics}"
            QMessageBox.critical(self, "Test runtime failed", detail)
            self.statusBar().showMessage("Test runtime failed")
        elif failed:
            self.statusBar().showMessage("Test runtime failed to launch")
        else:
            self.statusBar().showMessage(f"Test finished: {scene_id}")

    def _on_browser_selection(self, selection: DefinitionSelection | None) -> None:
        self.session.select(selection)
        self._refresh_views()

    def _on_graph_selection(self, selection: DefinitionSelection | None) -> None:
        """Route graph node selection through the normal project selection."""
        self.session.select(selection)
        self._refresh_views()

    def _open_graph_scene(self, scene_id: str) -> None:
        project = self.session.project
        if project is None or project.index is None:
            return
        entry = project.index.entry(ContentKind.SCENE, scene_id)
        if entry is None:
            return
        self.session.select(DefinitionSelection(ContentKind.SCENE, scene_id, entry.source))
        self.workspace.open_scene_editor()
        self._refresh_views()
        self.statusBar().showMessage(f"Opened scene {scene_id}")

    def _open_graph_navigation(self, edge: SceneGraphEdge) -> None:
        project = self.session.project
        if project is None or project.index is None:
            return
        entry = project.index.entry(ContentKind.SCENE, edge.source_scene_id)
        if entry is None:
            return
        self.session.select(DefinitionSelection(ContentKind.SCENE, edge.source_scene_id, entry.source))
        self.workspace.open_scene_editor()
        self._refresh_views()
        self.workspace.scene_editor.focus_navigation_path(edge.source_path)

    def _open_destination_scene(self, scene_id: str) -> None:
        project = self.session.project
        if project is None or project.index is None:
            return
        entry = project.index.entry("scene", scene_id)
        if entry is None:
            return
        self.session.select(DefinitionSelection("scene", scene_id, entry.source))
        self._refresh_views()
        self.statusBar().showMessage(f"Opened destination scene {scene_id}")

    def _on_scene_element_selection(self, selection: object) -> None:
        """Keep graphical scene-local identity visible in the Inspector."""

        if selection is None:
            self.inspector.clear_scene_element()
            return
        editor = self.workspace.scene_editor
        if editor.presentation is None:
            return
        ref = selection
        item = editor._item_for_ref(ref) if hasattr(ref, "kind") else None
        if item is None:
            return
        element_data = {"id": ref.id, "kind": ref.kind}
        if ref.kind == "object":
            for value in editor.presentation.objects:
                if value.id == ref.id:
                    element_data.update(value.authored)
                    break
        elif ref.kind == "look_region":
            for value in editor.presentation.look_regions:
                if value.id == ref.id:
                    element_data.update(value.authored)
                    break
        self.inspector.set_scene_element(ref, element_data)

    def _on_scene_geometry_edit(self, selection: object, geometry: object) -> None:
        editor = self.workspace.scene_editor
        if not hasattr(selection, "scene_id") or not isinstance(geometry, tuple):
            return
        editor.commit_geometry(selection, geometry)

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
        self.asset_browser.set_source(project.source if project is not None else None)
        if selection is not None:
            self.browser.select(selection)
        self.inspector.set_selection(project, selection, definition, self.session.diagnostics)
        self.workspace.set_state(project, selection, definition, self.session.diagnostics)
        active_scene_element = self.workspace.scene_editor.selected_element
        if active_scene_element is not None:
            self._on_scene_element_selection(active_scene_element)
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
        self.test_current_scene_action.setEnabled(
            has_project and self.current_scene_id() is not None and not self.test_process_running
        )
        self.configure_test_state_action.setEnabled(has_project)
        self.restart_test_action.setEnabled(
            has_project and (self._test_context_valid or self.test_process_running)
        )
        self.stop_test_action.setEnabled(self.test_process_running)

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
        self._closing = True
        self._stop_test_process()
        self.settings.setValue("windowGeometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())
        event.accept()

    def _stop_test_process(self) -> None:
        process = self.test_process
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return
        process.terminate()
        if not process.waitForFinished(1500):
            process.kill()
            process.waitForFinished(1500)
        self.test_process = None
        self._test_scene_id = None
        self._cleanup_test_config()

    def _cleanup_test_config(self) -> None:
        path = self._test_config_path
        self._test_config_path = None
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


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
