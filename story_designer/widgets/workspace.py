"""Central workspace placeholder designed for future editor tabs."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QPlainTextEdit, QTabWidget, QVBoxLayout, QWidget

from engine.story_core import ContentKind, Diagnostics, StoryProject

from ..models import DefinitionSelection, ProjectSession
from .scene_editor import SceneEditorWidget
from .dialogue_editor import DialogueEditorWidget
from .scene_graph import SceneGraphWidget
from .battle_editor import BattleEditorWidget
from .combat_move_editor import CombatMoveEditorWidget


class WorkspaceWidget(QWidget):
    """Central overview plus the graphical editor for authored scenes."""

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

    def __init__(self, session: ProjectSession | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
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
        self.scene_editor = SceneEditorWidget(session)
        self.tabs.addTab(self.scene_editor, "Scene")
        self.scene_editor.element_selected.connect(self.scene_element_selected)
        self.dialogue_editor = DialogueEditorWidget(session)
        self.tabs.addTab(self.dialogue_editor, "Dialogue")
        self.dialogue_editor.entry_selected.connect(self.dialogue_entry_selected)
        self.dialogue_editor.dialogue_changed.connect(self.dialogue_changed)
        self.scene_editor.open_dialogue_sequence.connect(self.open_dialogue_sequence)
        self.scene_graph = SceneGraphWidget(session)
        self.tabs.addTab(self.scene_graph, "Scene Graph")
        self.scene_graph.scene_selected.connect(self.graph_scene_selected)
        self.scene_graph.scene_open_requested.connect(self.graph_scene_open_requested)
        self.scene_graph.open_navigation_entry.connect(self.graph_open_navigation)
        self.battle_editor = BattleEditorWidget(session)
        self.tabs.addTab(self.battle_editor, "Battle")
        self.battle_editor.section_selected.connect(self.battle_section_selected)
        self.battle_editor.element_selected.connect(self.battle_element_selected)
        self.battle_editor.changed.connect(self.battle_changed)
        self.combat_move_editor = CombatMoveEditorWidget(session)
        self.tabs.addTab(self.combat_move_editor, "Combat Move")
        self.combat_move_editor.section_selected.connect(self.combat_move_section_selected)
        self.combat_move_editor.changed.connect(self.combat_move_changed)
        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        self.clear()

    def clear(self) -> None:
        self.overview_title.setText("Welcome to Story Designer")
        self.overview.setPlainText("Open a story project to browse its authored content.")
        self.tabs.setCurrentIndex(0)
        self.scene_editor.clear()
        self.dialogue_editor.clear()
        self.scene_graph.clear()
        self.battle_editor.clear()
        self.combat_move_editor.clear()

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
        self.scene_graph.set_state(project, selection, definition, diagnostics)
        self.battle_editor.set_state(project, selection, definition, diagnostics)
        self.combat_move_editor.set_state(project, selection, definition, diagnostics)
        if selection is None or definition is None:
            self.overview_title.setText(project.manifest.title or project.manifest.id)
            self.overview.setPlainText(_project_summary(project, diagnostics))
            return
        if selection.kind is ContentKind.SCENE:
            mapping = self.session.working_mapping(selection) if self.session is not None else None
            if mapping is None and hasattr(definition, "to_mapping"):
                mapping = definition.to_mapping()
            self.scene_editor.set_scene(project, selection.id, mapping)
            self.dialogue_editor.set_scene(project, selection.id, mapping)
            if self.tabs.currentWidget() is self.scene_graph:
                self.scene_graph.select_scene(selection.id, emit=False)
            else:
                self.tabs.setCurrentWidget(self.scene_editor)
            return
        if selection.kind is ContentKind.BATTLE:
            self.open_battle_editor()
            self.dialogue_editor.clear()
            return
        if selection.kind is ContentKind.MOVE:
            self.open_combat_move_editor()
            self.dialogue_editor.clear()
            return
        self.tabs.setCurrentIndex(0)
        self.dialogue_editor.clear()
        self.overview_title.setText(f"{selection.kind.value.replace('_', ' ').title()}: {selection.id}")
        authored = getattr(definition, "authored", definition)
        self.overview.setPlainText(
            f"Source: {getattr(definition, 'source', selection.source)}\n\n"
            f"Authored fields: {len(authored) if hasattr(authored, '__len__') else 'n/a'}\n\n"
            "Edit supported properties in the Inspector. Changes are kept in memory until persistence is added."
        )

    def open_dialogue_sequence(self, sequence_id: str) -> None:
        """Switch from a scene element to its local dialogue sequence."""

        if self.dialogue_editor.select_source(f"sequence:{sequence_id}"):
            self.tabs.setCurrentWidget(self.dialogue_editor)

    def open_scene_editor(self) -> None:
        """Show the existing scene editor for the current scene selection."""
        self.tabs.setCurrentWidget(self.scene_editor)

    def open_battle_editor(self) -> None:
        """Show the dedicated battle workspace for the current battle."""
        self.tabs.setCurrentWidget(self.battle_editor)

    def open_combat_move_editor(self) -> None:
        """Show the dedicated global combat-move workspace."""
        self.tabs.setCurrentWidget(self.combat_move_editor)

    def show_scene_graph(self) -> None:
        self.tabs.setCurrentWidget(self.scene_graph)


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
