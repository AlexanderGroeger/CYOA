from __future__ import annotations

from pathlib import Path

import pytest

from engine.story_core import ContentKind
from story_core_fixture import write_fixture_story
from story_designer.models import (
    DefinitionSelection,
    ProjectSession,
    SceneGraphModel,
    SetNavigationDestinationCommand,
)


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _graph_session(tmp_path: Path) -> ProjectSession:
    root, shared = write_fixture_story(tmp_path)
    (root / "scenes" / "intro.yaml").write_text(
        "id: intro\n"
        "exploration:\n"
        "  navigation:\n"
        "    - scene: middle\n"
        "      condition: flags.open\n"
        "choices:\n"
        "  - text: Finish\n"
        "    goto: ending\n",
        encoding="utf-8",
    )
    (root / "scenes" / "middle.yaml").write_text(
        "id: middle\n"
        "exploration:\n"
        "  navigation:\n"
        "    - scene: missing_room\n",
        encoding="utf-8",
    )
    (root / "scenes" / "orphan.yaml").write_text("id: orphan\nending: true\n", encoding="utf-8")
    return ProjectSession.from_path(root, shared)


def test_scene_graph_model_represents_edges_analysis_and_missing_targets(tmp_path: Path) -> None:
    session = _graph_session(tmp_path)
    model = SceneGraphModel.from_session(session)

    assert {node.scene_id for node in model.nodes} == {"intro", "middle", "ending", "orphan"}
    navigation = next(edge for edge in model.edges if edge.kind == "navigation")
    assert navigation.source_scene_id == "intro"
    assert navigation.target_scene_id == "middle"
    assert navigation.conditional is True
    assert navigation.condition_summary == "flags.open"
    missing = next(edge for edge in model.edges if edge.target_scene_id == "missing_room")
    assert missing.unresolved is True
    assert [node.scene_id for node in model.missing_nodes] == ["missing_room"]
    assert model.reachable_scene_ids == {"intro", "middle", "ending"}
    assert model.unreachable_scene_ids == {"orphan"}
    assert model.positions["intro"] != model.positions["middle"]


def test_scene_graph_model_reads_pending_navigation_working_copy(tmp_path: Path) -> None:
    session = _graph_session(tmp_path)
    project = session.project
    assert project is not None and project.index is not None
    entry = project.index.entry(ContentKind.SCENE, "middle")
    assert entry is not None
    selection = DefinitionSelection(ContentKind.SCENE, "middle", entry.source)
    session.select(selection)
    session.apply_command(SetNavigationDestinationCommand(
        selection, ("exploration", "navigation"), 0, "orphan",
    ))

    model = SceneGraphModel.from_session(session)
    assert any(edge.source_scene_id == "middle" and edge.target_scene_id == "orphan" for edge in model.edges)
    assert model.node("middle").dirty is True  # type: ignore[union-attr]


def test_scene_graph_model_cycles_terminate(tmp_path: Path) -> None:
    session = _graph_session(tmp_path)
    project = session.project
    assert project is not None
    (project.story_root / "scenes" / "middle.yaml").write_text(  # type: ignore[union-attr]
        "id: middle\nexploration:\n  navigation:\n    - scene: intro\n", encoding="utf-8"
    )
    session.reload()
    model = SceneGraphModel.from_session(session)
    assert model.reachable_scene_ids >= {"intro", "middle"}
    assert all(scene_id in model.positions for scene_id in {"intro", "middle", "ending", "orphan"})


@pytest.mark.skipif(__import__("importlib").util.find_spec("PySide6") is None, reason="PySide6 is not installed")
def test_scene_graph_widget_selection_details_and_controls(qapp, tmp_path: Path) -> None:
    from story_designer.widgets import SceneGraphWidget

    session = _graph_session(tmp_path)
    widget = SceneGraphWidget(session)
    widget.set_state(session.project, None, session.diagnostics)
    selected = []
    opened = []
    widget.scene_selected.connect(selected.append)
    widget.scene_open_requested.connect(opened.append)

    assert widget.select_scene("intro") is True
    assert selected[-1].id == "intro"
    assert "Incoming" in widget.details.toPlainText()
    widget._node_items["intro"]._double_click("intro")
    assert opened == ["intro"]
    edge = next(iter(widget._edge_items.values()))
    edge.setSelected(True)
    qapp.processEvents()
    assert "Source:" in widget.details.toPlainText()
    widget.fit_graph()
    widget.actual_size()
