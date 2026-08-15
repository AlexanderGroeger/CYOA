from __future__ import annotations

from pathlib import Path
import os

import pytest

from engine.story_core import ContentKind
from story_core_fixture import write_fixture_story
from story_designer.models import (
    DefinitionSelection,
    DuplicateSceneElementCommand,
    InsertSceneElementCommand,
    ProjectSession,
    RemoveSceneElementCommand,
    scene_collection_path,
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication
    from story_designer.models import SceneElementSelection
    from story_designer.widgets import SceneEditorWidget
except ImportError:  # pragma: no cover
    QApplication = None  # type: ignore[assignment]


@pytest.fixture(scope="module")
def qapp():
    if QApplication is None:
        pytest.skip("PySide6 is not installed")
    return QApplication.instance() or QApplication([])


def _scene_session(tmp_path: Path, scene_text: str) -> tuple[ProjectSession, DefinitionSelection]:
    story_root, shared_root = write_fixture_story(tmp_path)
    (story_root / "scenes" / "intro.yaml").write_text(scene_text, encoding="utf-8")
    session = ProjectSession.from_path(story_root, shared_root)
    entry = session.project.index.entry(ContentKind.SCENE, "intro")  # type: ignore[union-attr]
    assert entry is not None
    selection = DefinitionSelection(ContentKind.SCENE, "intro", entry.source)
    session.select(selection)
    return session, selection


def test_structural_commands_are_atomic_and_preserve_unknown_data(tmp_path: Path) -> None:
    session, selection = _scene_session(
        tmp_path,
        "id: intro\n"
        "exploration:\n"
        "  objects:\n"
        "    - id: lamp\n"
        "      position: [10, 20]\n"
        "      future: {preserve: true}\n"
        "  look_regions:\n"
        "    - id: desk\n"
        "      rect: [1, 2, 30, 40]\n"
        "      interaction: inspect\n",
    )
    original = session.definition(selection).to_mapping()
    path = scene_collection_path(session.working_mapping(selection), "objects")

    duplicate = DuplicateSceneElementCommand(selection, path, "lamp", "lamp_copy")
    session.apply_command(duplicate)
    objects = session.working_mapping(selection)["exploration"]["objects"]
    assert objects[1] == {
        "id": "lamp_copy",
        "position": [18, 28],
        "future": {"preserve": True},
    }
    assert session.definition(selection).to_mapping() == original
    assert len(session._history) == 1

    session.undo()
    assert session.working_mapping(selection) == original
    session.redo()
    assert session.working_mapping(selection)["exploration"]["objects"][1]["id"] == "lamp_copy"

    remove = RemoveSceneElementCommand(selection, path, "lamp")
    session.apply_command(remove)
    assert [item["id"] for item in session.working_mapping(selection)["exploration"]["objects"]] == ["lamp_copy"]
    session.undo()
    assert [item["id"] for item in session.working_mapping(selection)["exploration"]["objects"]] == ["lamp", "lamp_copy"]


def test_insert_preserves_root_alias_and_creates_canonical_exploration_container(tmp_path: Path) -> None:
    session, selection = _scene_session(
        tmp_path,
        "id: intro\n"
        "objects:\n"
        "  - id: legacy\n"
        "    position: [4, 5]\n",
    )
    mapping = session.working_mapping(selection)
    assert scene_collection_path(mapping, "objects") == ("objects",)
    session.apply_command(InsertSceneElementCommand(selection, ("objects",), {"id": "new", "position": [8, 9]}))
    assert "exploration" not in session.working_mapping(selection)
    assert [item["id"] for item in session.working_mapping(selection)["objects"]] == ["legacy", "new"]

    session2, selection2 = _scene_session(tmp_path / "second", "id: intro\n")
    session2.apply_command(
        InsertSceneElementCommand(selection2, ("exploration", "objects"), {"id": "new", "position": [8, 9]})
    )
    assert session2.working_mapping(selection2) == {
        "id": "intro",
        "exploration": {"objects": [{"id": "new", "position": [8, 9]}]},
    }


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_scene_editor_structural_actions_refresh_selection_and_view(qapp, tmp_path: Path) -> None:
    session, selection = _scene_session(tmp_path, "id: intro\nexploration: {}\n")
    editor = SceneEditorWidget(session)
    editor.set_scene(session.project, selection.id, session.working_mapping(selection))  # type: ignore[arg-type]
    editor.set_zoom(2.0)

    assert editor.add_object()
    object_ref = SceneElementSelection("intro", "object", "object")
    assert editor.selected_element == object_ref
    assert session.working_mapping(selection)["exploration"]["objects"][0]["position"] == [160, 90]
    assert editor.view.transform().m11() == pytest.approx(2.0)

    assert editor.duplicate_selected()
    assert editor.selected_element.id == "object_copy"
    assert session.working_mapping(selection)["exploration"]["objects"][1]["position"] == [168, 98]
    assert editor.delete_selected()
    assert editor.selected_element is None
    assert [item["id"] for item in session.working_mapping(selection)["exploration"]["objects"]] == ["object"]


def test_structural_changes_save_and_reload_through_existing_persistence(tmp_path: Path) -> None:
    session, selection = _scene_session(
        tmp_path,
        "id: intro\n"
        "exploration:\n"
        "  objects:\n"
        "    - id: original\n"
        "      position: [1, 2]\n"
        "  look_regions:\n"
        "    - id: region\n"
        "      rect: [1, 2, 10, 10]\n"
        "      interaction: inspect\n",
    )
    path = ("exploration", "objects")
    session.apply_command(InsertSceneElementCommand(selection, path, {"id": "added", "position": [5, 6]}))
    session.apply_command(DuplicateSceneElementCommand(selection, path, "added", "added_copy"))
    session.apply_command(RemoveSceneElementCommand(selection, path, "original"))
    assert session.save_all()

    reloaded = ProjectSession.from_path(session.story_root, session.shared_assets_root)
    entry = reloaded.project.index.entry(ContentKind.SCENE, "intro")  # type: ignore[union-attr]
    assert entry is not None
    reloaded_selection = DefinitionSelection(ContentKind.SCENE, "intro", entry.source)
    assert [
        item["id"] for item in reloaded.working_mapping(reloaded_selection)["exploration"]["objects"]
    ] == ["added", "added_copy"]
