"""Headless coverage for the tool-centric Designer shell."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.story_core import ContentKind
from story_designer.models import DefinitionSelection, ProjectSession, SceneElementSelection
from story_designer.widgets.workspace import WorkspaceWidget
from story_core_fixture import write_fixture_story

try:
    from PySide6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None  # type: ignore[assignment]


@pytest.fixture(scope="module")
def qapp():
    if QApplication is None:
        pytest.skip("PySide6 is not installed")
    return QApplication.instance() or QApplication([])


def _session(tmp_path: Path) -> tuple[ProjectSession, DefinitionSelection]:
    root, shared = write_fixture_story(tmp_path)
    session = ProjectSession.from_path(root, shared)
    assert session.project is not None and session.project.index is not None
    entry = session.project.index.entry(ContentKind.SCENE, "intro")
    assert entry is not None
    selection = DefinitionSelection(ContentKind.SCENE, "intro", entry.source)
    session.select(selection)
    return session, selection


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_scene_tool_has_filtered_navigator_canvas_and_singular_context(qapp, tmp_path: Path) -> None:
    session, selection = _session(tmp_path)
    workspace = WorkspaceWidget(session)
    workspace.set_state(session.project, selection, session.definition(), session.diagnostics)

    assert workspace.tabs.count() >= 7
    assert workspace.scene_shell.splitter.count() == 3
    assert workspace.scene_shell.navigator is workspace.scene_navigator
    assert workspace.scene_shell.editor is workspace.scene_editor
    assert workspace.scene_shell.context is workspace.inspector
    assert workspace.scene_editor.navigation_panel.isHidden()
    assert not workspace.inspector.context_tabs.tabBar().isVisible()

    scene_items = [item for item in workspace.scene_navigator._items() if not item.isHidden()]
    assert scene_items
    assert all(
        item.data(0, workspace.scene_navigator._SELECTION_ROLE).kind is ContentKind.SCENE
        for item in scene_items
        if item.data(0, workspace.scene_navigator._SELECTION_ROLE) is not None
    )
    workspace.scene_navigator.search.setText("intro")
    assert all("intro" in item.text(0).casefold() for item in workspace.scene_navigator._items() if not item.isHidden() and item.data(0, workspace.scene_navigator._SELECTION_ROLE) is not None)


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_scene_element_selection_replaces_context_without_rebuilding_shell(qapp, tmp_path: Path) -> None:
    session, selection = _session(tmp_path)
    workspace = WorkspaceWidget(session)
    workspace.set_state(session.project, selection, session.definition(), session.diagnostics)
    shell = workspace.scene_shell
    inspector = workspace.inspector

    object_ref = SceneElementSelection("intro", "object", "lamp")
    inspector.set_scene_element(object_ref, {"id": "lamp", "position": [1, 2], "sprite": "lamp.png"})
    assert inspector.context_tabs.currentWidget() is inspector.object_context_page
    inspector.set_scene_element(None, {})
    assert inspector.context_tabs.currentWidget() is inspector.scene_context_page
    assert shell.splitter is workspace.scene_shell.splitter
    assert shell.context is inspector


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_scene_graph_uses_scene_navigator_and_navigation_context(qapp, tmp_path: Path) -> None:
    session, selection = _session(tmp_path)
    workspace = WorkspaceWidget(session)
    workspace.set_state(session.project, selection, session.definition(), session.diagnostics)
    workspace.show_scene_graph()
    workspace.set_state(session.project, selection, session.definition(), session.diagnostics)

    assert workspace.graph_shell.splitter.count() == 3
    assert workspace.graph_shell.navigator is workspace.graph_navigator
    assert workspace.graph_shell.editor is workspace.scene_graph
    assert workspace.graph_shell.context is workspace.graph_navigation
    assert workspace.scene_graph._node_items
    assert workspace.graph_navigation.scene_id == "intro"
    assert all(
        item.data(0, workspace.graph_navigator._SELECTION_ROLE).kind is ContentKind.SCENE
        for item in workspace.graph_navigator._items()
        if item.data(0, workspace.graph_navigator._SELECTION_ROLE) is not None
    )
