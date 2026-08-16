from __future__ import annotations

import os
from pathlib import Path

import pytest

from engine.core.game_state import GameState
from engine.story_core import (
    ContentKind,
    NewStorySpec,
    ProjectCreationError,
    create_story_project,
    create_top_level_definition,
    load_story_project,
)


def test_new_story_spec_validates_dimensions_ids_and_collisions(tmp_path: Path) -> None:
    NewStorySpec("A Story", "a_story", tmp_path).validate()
    with pytest.raises(ProjectCreationError):
        NewStorySpec("A Story", "a_story", tmp_path, width=0).validate()
    with pytest.raises(ProjectCreationError):
        NewStorySpec("A Story", "a/story", tmp_path).validate()

    (tmp_path / "A Story").mkdir()
    with pytest.raises(ProjectCreationError, match="already exists"):
        NewStorySpec("A Story", "a_story", tmp_path).validate()


def test_new_story_is_minimal_valid_and_bootstraps_runtime_state(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    root = create_story_project(
        NewStorySpec("Blank Story", "blank_story", tmp_path, width=480, height=270),
        shared_assets_root=shared,
    )
    project = load_story_project(root, shared)

    assert project.validate().errors == ()
    assert project.manifest.start_scene == "start"
    assert project.scene("start").source == root / "scenes" / "start.yaml"
    assert GameState.new_from_manifest(
        project.manifest.to_mapping(), project.player_profile.to_mapping()
    ).current_scene == "start"
    assert (root / "story.yaml").is_file()
    assert (root / "player.yaml").is_file()
    assert not (root / "audio.yaml").exists()


def test_top_level_creation_uses_real_source_shapes_and_reload_provenance(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    root = create_story_project(NewStorySpec("Authoring", "authoring", tmp_path), shared_assets_root=shared)

    create_top_level_definition(root, ContentKind.SCENE, "hall", shared_assets_root=shared)
    create_top_level_definition(root, ContentKind.ITEM, "key", shared_assets_root=shared)
    create_top_level_definition(root, ContentKind.MOVE, "strike", qte_type="precision_bar", shared_assets_root=shared)
    create_top_level_definition(root, ContentKind.BATTLE, "training", shared_assets_root=shared)

    project = load_story_project(root, shared)
    assert project.validate().errors == ()
    assert "hall" in project.scenes
    assert "key" in project.items
    assert "strike" in project.moves
    assert "training" in project.battles
    assert project.scene("hall").source == root / "scenes" / "hall.yaml"
    assert project.item("key").field_path == ("key",)
    assert project.move("strike").field_path == ("moves", 0)
    assert project.battle("training").source == root / "battles" / "training.yaml"


def test_duplicate_and_unsafe_top_level_ids_never_overwrite_files(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    root = create_story_project(NewStorySpec("Safe", "safe", tmp_path), shared_assets_root=shared)
    create_top_level_definition(root, ContentKind.SCENE, "hall", shared_assets_root=shared)
    original = (root / "scenes" / "hall.yaml").read_bytes()

    with pytest.raises(ProjectCreationError):
        create_top_level_definition(root, ContentKind.SCENE, "hall", shared_assets_root=shared)
    with pytest.raises(ProjectCreationError):
        create_top_level_definition(root, ContentKind.SCENE, "..", shared_assets_root=shared)
    assert (root / "scenes" / "hall.yaml").read_bytes() == original


def test_standalone_designer_import_does_not_need_story_or_pygame() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", "import sys; import story_designer.app; assert 'pygame' not in sys.modules"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_designer_starts_with_welcome_actions_without_a_project() -> None:
    qt = pytest.importorskip("PySide6.QtWidgets")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = qt.QApplication.instance() or qt.QApplication([])
    from story_designer.main_window import MainWindow

    window = MainWindow()
    try:
        assert window.session.project is None
        assert window.workspace.welcome_new_button.text() == "New Story"
        assert window.workspace.welcome_open_button.text() == "Open Story"
        assert window.new_story_action.isEnabled()
        assert window.open_action.isEnabled()
        assert not window.close_action.isEnabled()
    finally:
        window.deleteLater()
        app.processEvents()
