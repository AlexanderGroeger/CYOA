"""Tool-centric central workspace for the Story Designer."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from engine.story_core import ContentKind, Diagnostics, StoryProject

from ..models import DefinitionSelection, ProjectSession, SceneGraphEdge
from .asset_browser import AssetBrowserWidget
from .battle_editor import BattleEditorWidget
from .combat_move_editor import CombatMoveEditorWidget
from .dialogue_editor import DialogueEditorWidget
from .inspector import InspectorWidget
from .item_editor import ItemNavigator, ItemPreviewWidget, ItemPropertiesWidget
from .navigation_panel import NavigationPanel
from .project_browser import ProjectBrowser
from .scene_editor import SceneEditorWidget
from .scene_graph import SceneGraphWidget


class ToolShell(QWidget):
    """Stable navigator/editor/context layout for one authoring tool."""

    def __init__(
        self,
        navigator: QWidget,
        editor: QWidget,
        context: QWidget,
        *,
        object_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.navigator = navigator
        self.editor = editor
        self.context = context
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setObjectName(f"{object_name}Splitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(navigator)
        self.splitter.addWidget(editor)
        self.splitter.addWidget(context)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([240, 700, 340])
        navigator.setMinimumWidth(180)
        context.setMinimumWidth(260)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.splitter)


class ContextSummary(QWidget):
    """Singular context surface for editors with detail UI of their own."""

    def __init__(self, title: str, value: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title = QLabel(title)
        self.title.setStyleSheet("font-size: 15px; font-weight: bold;")
        self.value = QPlainTextEdit()
        self.value.setReadOnly(True)
        self.value.setPlaceholderText("Select an item to inspect its context.")
        if value:
            self.value.setPlainText(value)
        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addWidget(self.value, 1)

    def set_context(self, title: str, value: str = "") -> None:
        self.title.setText(title)
        self.value.setPlainText(value)


class WorkspaceTabs(QTabWidget):
    """Tab widget with a backwards-compatible editor identity query.

    Existing extensions used ``currentWidget()`` to reach the center editor.
    The visible page is still the complete ToolShell; ``currentPage()`` is the
    unambiguous shell-oriented API used by the new workspace code.
    """

    def currentPage(self) -> QWidget | None:  # noqa: N802 - Qt-style API
        return super().currentWidget()

    def currentWidget(self) -> QWidget | None:  # noqa: N802 - compatibility API
        page = super().currentWidget()
        return getattr(page, "editor", page)


class WorkspaceWidget(QWidget):
    """Top-level focused authoring tools."""

    scene_element_selected = Signal(object)
    dialogue_entry_selected = Signal(object)
    dialogue_changed = Signal(object)
    graph_scene_selected = Signal(object)
    graph_scene_open_requested = Signal(str)
    graph_open_navigation = Signal(object)
    battle_section_selected = Signal(object)
    battle_element_selected = Signal(object)
    battle_changed = Signal(object)
    combat_move_section_selected = Signal(object)
    combat_move_changed = Signal(object)
    new_story_requested = Signal()
    open_story_requested = Signal()
    scene_navigator_selected = Signal(object)
    graph_navigator_selected = Signal(object)
    item_navigator_selected = Signal(object)
    item_changed = Signal(object)
    new_item_requested = Signal()
    item_open_move_requested = Signal(str)

    def __init__(self, session: ProjectSession | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.tabs = WorkspaceTabs()
        self.tabs.setDocumentMode(True)

        self.overview_browser = ProjectBrowser(title="Project", search_placeholder="Search project...")
        self.overview_title = QLabel("Welcome to Story Designer")
        self.overview_title.setStyleSheet("font-size: 18px; font-weight: bold;")
        self.overview = QPlainTextEdit()
        self.overview.setReadOnly(True)
        self.welcome_new_button = QPushButton("New Story")
        self.welcome_open_button = QPushButton("Open Story")
        self.welcome_new_button.clicked.connect(self.new_story_requested)
        self.welcome_open_button.clicked.connect(self.open_story_requested)
        welcome_buttons = QHBoxLayout()
        welcome_buttons.addWidget(self.welcome_new_button)
        welcome_buttons.addWidget(self.welcome_open_button)
        welcome_buttons.addStretch(1)
        overview_detail = QWidget()
        overview_layout = QVBoxLayout(overview_detail)
        overview_layout.addWidget(self.overview_title)
        overview_layout.addLayout(welcome_buttons)
        overview_layout.addWidget(self.overview)
        overview_shell = ToolShell(self.overview_browser, overview_detail, ContextSummary("Project"), object_name="project")
        self.tabs.addTab(overview_shell, "Project")

        scene_kinds = {ContentKind.SCENE}
        self.scene_navigator = ProjectBrowser(title="Scenes", allowed_kinds=scene_kinds, search_placeholder="Search scenes...")
        self.scene_editor = SceneEditorWidget(session)
        self.inspector = InspectorWidget(session)
        self.inspector.set_tool_context_mode(True)
        self.scene_shell = ToolShell(self.scene_navigator, self.scene_editor, self.inspector, object_name="scenes")
        self.tabs.addTab(self.scene_shell, "Scenes")
        self.scene_navigator.selection_changed.connect(self.scene_navigator_selected)
        self.scene_editor.element_selected.connect(self.scene_element_selected)

        self.dialogue_navigator = ProjectBrowser(title="Scenes", allowed_kinds=scene_kinds, search_placeholder="Search dialogue scenes...")
        self.dialogue_editor = DialogueEditorWidget(session)
        self.dialogue_context = ContextSummary("Dialogue Context")
        self.dialogue_shell = ToolShell(self.dialogue_navigator, self.dialogue_editor, self.dialogue_context, object_name="dialogue")
        self.tabs.addTab(self.dialogue_shell, "Dialogue")
        self.dialogue_navigator.selection_changed.connect(self._dialogue_scene_selected)
        self.dialogue_editor.entry_selected.connect(self._on_dialogue_entry)
        self.dialogue_editor.dialogue_changed.connect(self.dialogue_changed)
        self.scene_editor.open_dialogue_sequence.connect(self.open_dialogue_sequence)

        self.graph_navigator = ProjectBrowser(title="Scenes", allowed_kinds=scene_kinds, search_placeholder="Search graph scenes...")
        self.scene_graph = SceneGraphWidget(session)
        self.graph_navigation = NavigationPanel(session)
        self.graph_shell = ToolShell(self.graph_navigator, self.scene_graph, self.graph_navigation, object_name="sceneGraph")
        self.tabs.addTab(self.graph_shell, "Scene Graph")
        self.graph_navigator.selection_changed.connect(self.graph_navigator_selected)
        self.scene_graph.scene_selected.connect(self.graph_scene_selected)
        self.scene_graph.scene_open_requested.connect(self.graph_scene_open_requested)
        self.scene_graph.open_navigation_entry.connect(self.graph_open_navigation)

        self.battle_navigator = ProjectBrowser(title="Battles", allowed_kinds={ContentKind.BATTLE}, search_placeholder="Search battles...")
        self.battle_editor = BattleEditorWidget(session)
        self.battle_context = ContextSummary("Battle Context")
        self.battle_shell = ToolShell(self.battle_navigator, self.battle_editor, self.battle_context, object_name="battles")
        self.tabs.addTab(self.battle_shell, "Battles")
        self.battle_navigator.selection_changed.connect(self._battle_selected)
        self.battle_editor.section_selected.connect(self.battle_section_selected)
        self.battle_editor.element_selected.connect(self.battle_element_selected)
        self.battle_editor.changed.connect(self.battle_changed)

        self.move_navigator = ProjectBrowser(title="Combat Moves", allowed_kinds={ContentKind.MOVE}, search_placeholder="Search combat moves...")
        self.combat_move_editor = CombatMoveEditorWidget(session)
        self.combat_move_context = ContextSummary("Combat Move Context")
        self.combat_move_shell = ToolShell(self.move_navigator, self.combat_move_editor, self.combat_move_context, object_name="combatMoves")
        self.tabs.addTab(self.combat_move_shell, "Combat Moves")
        self.move_navigator.selection_changed.connect(self._move_selected)
        self.combat_move_editor.section_selected.connect(self.combat_move_section_selected)
        self.combat_move_editor.changed.connect(self.combat_move_changed)

        self.asset_browser = AssetBrowserWidget()
        asset_nav = ContextSummary("Asset Filters", "Use the asset browser search and type filters to narrow this tool.")
        self.asset_shell = ToolShell(asset_nav, self.asset_browser, ContextSummary("Asset Preview"), object_name="assets")
        self.tabs.addTab(self.asset_shell, "Assets")

        self.item_navigator = ItemNavigator()
        self.item_preview = ItemPreviewWidget(session)
        self.item_properties = ItemPropertiesWidget(session)
        self.item_shell = ToolShell(self.item_navigator, self.item_preview, self.item_properties, object_name="items")
        self.tabs.addTab(self.item_shell, "Items")
        self.item_navigator.selection_changed.connect(self.item_navigator_selected)
        self.item_navigator.new_item_requested.connect(self.new_item_requested)
        self.item_properties.state_changed.connect(self._item_properties_changed)
        self.item_properties.open_move_requested.connect(self.item_open_move_requested)

        self._shells = {
            "project": overview_shell,
            "scenes": self.scene_shell,
            "dialogue": self.dialogue_shell,
            "sceneGraph": self.graph_shell,
            "battles": self.battle_shell,
            "combatMoves": self.combat_move_shell,
            "assets": self.asset_shell,
            "items": self.item_shell,
        }
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tabs)
        self.clear()

    def clear(self) -> None:
        self.overview_title.setText("Welcome to Story Designer")
        self.overview.setPlainText("Open a story project to browse its authored content.")
        self.welcome_new_button.setVisible(True)
        self.welcome_open_button.setVisible(True)
        self.tabs.setCurrentIndex(0)
        for browser in (self.overview_browser, self.scene_navigator, self.dialogue_navigator, self.graph_navigator, self.battle_navigator, self.move_navigator, self.item_navigator):
            browser.clear_project()
        self.scene_editor.clear()
        self.dialogue_editor.clear()
        self.scene_graph.clear()
        self.graph_navigation.clear()
        self.battle_editor.clear()
        self.combat_move_editor.clear()
        self.inspector.clear()
        self.item_preview.clear()
        self.item_properties.clear()

    def set_state(self, project: StoryProject | None, selection: DefinitionSelection | None, definition: Any | None, diagnostics: Diagnostics) -> None:
        if project is None:
            self.clear()
            return
        self.welcome_new_button.setVisible(False)
        self.welcome_open_button.setVisible(False)
        self.overview_browser.set_project(project)
        for browser in (self.scene_navigator, self.dialogue_navigator, self.graph_navigator, self.battle_navigator, self.move_navigator, self.item_navigator):
            browser.set_project(project)
        item_selection = selection if selection is not None and selection.kind is ContentKind.ITEM else None
        item_definition = definition if item_selection is not None else None
        item_mapping = self.session.working_mapping(item_selection) if self.session is not None and item_selection is not None else None
        self.item_properties.set_state(project, item_selection, item_definition, diagnostics)
        self.item_preview.set_state(project, item_selection, item_mapping)
        self.scene_graph.set_state(project, selection, definition, diagnostics)
        self.battle_editor.set_state(project, selection, definition, diagnostics)
        self.combat_move_editor.set_state(project, selection, definition, diagnostics)
        if selection is None or definition is None:
            self.overview_title.setText(project.manifest.title or project.manifest.id)
            self.overview.setPlainText(_project_summary(project, diagnostics))
            self.inspector.set_selection(project, selection, definition, diagnostics)
            return
        if selection.kind is ContentKind.SCENE:
            mapping = self.session.working_mapping(selection) if self.session is not None else None
            if mapping is None and hasattr(definition, "to_mapping"):
                mapping = definition.to_mapping()
            self.scene_editor.set_scene(project, selection.id, mapping)
            self.dialogue_editor.set_scene(project, selection.id, mapping)
            self.graph_navigation.set_scene(project, selection.id, mapping)
            self.inspector.set_selection(project, selection, definition, diagnostics)
            self.scene_navigator.select(selection)
            self.dialogue_navigator.select(selection)
            self.graph_navigator.select(selection)
            if self.tabs.currentPage() is self.graph_shell:
                self.scene_graph.select_scene(selection.id, emit=False)
            else:
                self.open_scene_editor()
            return
        if selection.kind is ContentKind.ITEM:
            self.item_navigator.select(selection)
            self.open_items_tool()
            return
        self.inspector.set_selection(project, selection, definition, diagnostics)
        if selection.kind is ContentKind.BATTLE:
            self.open_battle_editor()
        elif selection.kind is ContentKind.MOVE:
            self.open_combat_move_editor()
        else:
            self.tabs.setCurrentIndex(0)
            self.overview_title.setText(f"{selection.kind.value.replace('_', ' ').title()}: {selection.id}")
            authored = getattr(definition, "authored", definition)
            self.overview.setPlainText(
                f"Source: {getattr(definition, 'source', selection.source)}\n\n"
                f"Authored fields: {len(authored) if hasattr(authored, '__len__') else 'n/a'}\n\n"
                "Edit supported properties in the active context editor. Changes remain in memory until you save the story."
            )

    def refresh_value_dependencies(self) -> None:
        self.scene_editor.refresh_value_dependencies()
        if self.session is not None and self.session.selection is not None and self.session.selection.kind is ContentKind.ITEM:
            selection = self.session.selection
            self.item_properties.set_state(self.session.project, selection, self.session.definition(selection), self.session.diagnostics)
            self.item_preview.set_state(self.session.project, selection, self.session.working_mapping(selection))

    def open_dialogue_sequence(self, sequence_id: str) -> None:
        if self.dialogue_editor.select_source(f"sequence:{sequence_id}"):
            self.tabs.setCurrentWidget(self.dialogue_shell)

    def open_scene_editor(self) -> None:
        self.tabs.setCurrentWidget(self.scene_shell)

    def open_battle_editor(self) -> None:
        self.tabs.setCurrentWidget(self.battle_shell)

    def open_combat_move_editor(self) -> None:
        self.tabs.setCurrentWidget(self.combat_move_shell)

    def open_items_tool(self) -> None:
        self.tabs.setCurrentWidget(self.item_shell)

    def show_scene_graph(self) -> None:
        self.tabs.setCurrentWidget(self.graph_shell)

    def focus_definition(self, selection: DefinitionSelection | None) -> None:
        if selection is None:
            return
        if selection.kind is ContentKind.SCENE:
            self.open_scene_editor()
        elif selection.kind is ContentKind.BATTLE:
            self.open_battle_editor()
        elif selection.kind is ContentKind.MOVE:
            self.open_combat_move_editor()
        elif selection.kind is ContentKind.ITEM:
            self.open_items_tool()
        elif selection.kind in {ContentKind.ANIMATION, ContentKind.AUDIO}:
            self.tabs.setCurrentWidget(self.asset_shell)
        else:
            self.tabs.setCurrentIndex(0)

    def focus_graph_navigation(self, edge: SceneGraphEdge) -> None:
        self.show_scene_graph()
        self.graph_navigation.focus_path(edge.source_path)

    def save_layout(self, settings: Any) -> None:
        for key, shell in self._shells.items():
            settings.setValue(f"toolSplitter/{key}", shell.splitter.saveState())

    def restore_layout(self, settings: Any) -> None:
        for key, shell in self._shells.items():
            state = settings.value(f"toolSplitter/{key}")
            if state:
                shell.splitter.restoreState(state)

    def _dialogue_scene_selected(self, selection: object) -> None:
        if isinstance(selection, DefinitionSelection):
            self.scene_navigator_selected.emit(selection)
            self.tabs.setCurrentWidget(self.dialogue_shell)

    def _battle_selected(self, selection: object) -> None:
        if isinstance(selection, DefinitionSelection):
            self.scene_navigator_selected.emit(selection)
            self.tabs.setCurrentWidget(self.battle_shell)

    def _move_selected(self, selection: object) -> None:
        if isinstance(selection, DefinitionSelection):
            self.scene_navigator_selected.emit(selection)
            self.tabs.setCurrentWidget(self.combat_move_shell)

    def _item_properties_changed(self) -> None:
        if self.session is None or self.session.selection is None or self.session.selection.kind is not ContentKind.ITEM:
            return
        mapping = self.session.working_mapping(self.session.selection)
        self.item_preview.update_from_mapping(mapping or {})
        self.item_changed.emit(self.session.selection)

    def _on_dialogue_entry(self, selection: object) -> None:
        self.dialogue_entry_selected.emit(selection)
        self.dialogue_context.set_context("Dialogue Entry" if selection is not None else "Dialogue Context", str(selection or "Select a dialogue entry."))


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


__all__ = ["ContextSummary", "ToolShell", "WorkspaceWidget"]
