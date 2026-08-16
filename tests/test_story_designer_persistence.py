"""Step 5C coverage for editor history and conservative persistence."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.story_core import ContentKind, NewStorySpec, create_story_project
from story_core_fixture import write_fixture_story
from story_designer.models import (
    DefinitionSelection,
    ExternalChangeConflict,
    PersistenceError,
    ProjectSession,
    RemovePropertyCommand,
    SetPropertyCommand,
)

try:
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
    from shiboken6 import isValid
    from story_designer.main_window import MainWindow
except ImportError:  # pragma: no cover - Core-only environments
    QApplication = None  # type: ignore[assignment]
    QMessageBox = None  # type: ignore[assignment]
    QFileDialog = None  # type: ignore[assignment]
    isValid = None  # type: ignore[assignment]
    MainWindow = None  # type: ignore[assignment]


def _session(tmp_path: Path) -> ProjectSession:
    story_root, shared_root = write_fixture_story(tmp_path)
    return ProjectSession.from_path(story_root, shared_root)


def _selection(session: ProjectSession, kind: ContentKind, identifier: str) -> DefinitionSelection:
    assert session.project is not None and session.project.index is not None
    entry = session.project.index.entry(kind, identifier)
    assert entry is not None
    return DefinitionSelection(kind, identifier, entry.source)


def test_undo_redo_restores_authored_absence_and_clears_divergent_redo(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = _selection(session, ContentKind.SCENE, "intro")

    session.apply_command(SetPropertyCommand(scene, ("checkpoint",), True))
    assert session.is_dirty and session.can_undo
    session.undo()
    assert not session.is_dirty
    assert "checkpoint" not in session.working_mapping(scene)
    assert session.can_redo
    session.redo()
    assert session.working_mapping(scene)["checkpoint"] is True

    session.undo()
    session.apply_command(SetPropertyCommand(scene, ("text",), "Diverged"))
    assert not session.can_redo


def test_nested_undo_restores_missing_parent_exactly(tmp_path: Path) -> None:
    session = _session(tmp_path)
    item = _selection(session, ContentKind.ITEM, "intro")
    before = session.working_mapping(item)
    session.apply_command(SetPropertyCommand(item, ("stats", "attack"), 5))
    session.undo()
    assert session.working_mapping(item) == before
    assert not session.is_dirty


def test_save_reloads_new_project_and_preserves_unknown_and_sibling_data(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = _selection(session, ContentKind.SCENE, "intro")
    original_project = session.project
    session.apply_command(SetPropertyCommand(scene, ("text",), "Saved text"))
    session.apply_command(SetPropertyCommand(scene, ("checkpoint",), True))

    assert session.save()
    assert session.project is not original_project
    assert not session.is_dirty
    assert session.project is not None
    assert session.project.scene("intro").text == "Saved text"
    saved = yaml.safe_load((session.story_root / "scenes/intro.yaml").read_text(encoding="utf-8"))
    sibling = yaml.safe_load((session.story_root / "scenes/ending.yaml").read_text(encoding="utf-8"))
    assert saved["future_scene_extension"] == {"preserves": True}
    assert saved["checkpoint"] is True
    assert sibling["id"] == "ending"


def test_shared_registry_save_preserves_other_entries(tmp_path: Path) -> None:
    session = _session(tmp_path)
    items_path = session.story_root / "items/items.yaml"
    items = yaml.safe_load(items_path.read_text(encoding="utf-8"))
    items["other"] = {"name": "Other", "type": "key"}
    items_path.write_text(yaml.safe_dump(items, sort_keys=False), encoding="utf-8")
    session.reload(session.shared_assets_root)

    intro = _selection(session, ContentKind.ITEM, "intro")
    session.apply_command(SetPropertyCommand(intro, ("name",), "Changed token"))
    session.save_all()
    saved = yaml.safe_load(items_path.read_text(encoding="utf-8"))
    assert saved["intro"]["name"] == "Changed token"
    assert saved["intro"]["future_item_extension"] == {"preserves": True}
    assert saved["other"] == {"name": "Other", "type": "key"}


def test_external_change_blocks_save_without_overwrite(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = _selection(session, ContentKind.SCENE, "intro")
    source = session.story_root / "scenes/intro.yaml"
    session.apply_command(SetPropertyCommand(scene, ("text",), "Local edit"))
    external = yaml.safe_load(source.read_text(encoding="utf-8"))
    external["text"] = "External edit"
    source.write_text(yaml.safe_dump(external, sort_keys=False), encoding="utf-8")

    with pytest.raises(ExternalChangeConflict):
        session.save()
    assert yaml.safe_load(source.read_text(encoding="utf-8"))["text"] == "External edit"
    assert session.is_dirty


def test_atomic_write_failure_keeps_original_and_dirty_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(tmp_path)
    scene = _selection(session, ContentKind.SCENE, "intro")
    source = session.story_root / "scenes/intro.yaml"
    original = source.read_bytes()
    session.apply_command(SetPropertyCommand(scene, ("text",), "Will fail"))

    def fail_replace(_source, _target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("story_designer.models.persistence.os.replace", fail_replace)
    with pytest.raises(PersistenceError):
        session.save()
    assert source.read_bytes() == original
    assert session.is_dirty


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_open_story_uses_directory_selection_only(qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    story_root, _ = write_fixture_story(tmp_path)
    window = MainWindow()
    selected: list[Path] = []
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda _parent, title, _initial: (selected.append(story_root) or str(story_root)))
    monkeypatch.setattr(window, "open_story_path", lambda path: selected.append(Path(path)) or True)

    window.open_story()

    assert selected == [story_root, story_root]
    window.close()


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_recent_story_manifest_paths_are_migrated_to_directories(qapp, tmp_path: Path) -> None:
    story_root, _ = write_fixture_story(tmp_path)
    window = MainWindow()
    values = {"recentStories": [str(story_root / "story.yaml")]}

    class MemorySettings:
        def value(self, key, default=None):
            return values.get(key, default)

        def setValue(self, key, value):
            values[key] = value

    window.settings = MemorySettings()

    assert window._recent_paths() == [str(story_root)]
    assert values["recentStories"] == [str(story_root)]
    window.close()


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_main_window_actions_and_inspector_refresh_follow_history(qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    window = MainWindow()
    assert window.open_story_path(story_root)
    scene = _selection(window.session, ContentKind.SCENE, "intro")
    window.session.select(scene)
    window._refresh_views()
    assert not window.save_action.isEnabled()
    assert not window.undo_action.isEnabled()

    window.session.apply_command(SetPropertyCommand(scene, ("text",), "Through window"))
    window._on_inspector_state_changed()
    assert window.save_action.isEnabled()
    assert window.undo_action.isEnabled()
    assert window.windowTitle().endswith("*")

    assert window.undo()
    assert not window.session.is_dirty
    assert not window.save_action.isEnabled()
    assert window.inspector._rows[("text",)].descriptor.effective_value == "Welcome."

    window.session.apply_command(SetPropertyCommand(scene, ("text",), "Unsaved"))
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Discard),
    )
    assert window._confirm_unsaved_changes("close this story")
    window.close()


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_new_open_reload_close_story_lifecycle_keeps_inspector_widgets_alive(qapp, tmp_path: Path) -> None:
    first_root = create_story_project(
        NewStorySpec("First Story", "first_story", tmp_path),
        shared_assets_root=tmp_path / "shared_assets",
    )
    second_root = create_story_project(
        NewStorySpec("Second Story", "second_story", tmp_path),
        shared_assets_root=tmp_path / "shared_assets",
    )
    window = MainWindow()
    geometry_box = window.inspector.scene_geometry_box
    try:
        assert window.open_story_path(first_root)
        start = _selection(window.session, ContentKind.SCENE, "start")
        window.session.select(start)
        window._refresh_views()
        assert isValid(geometry_box)

        assert window.reload_story()
        assert isValid(geometry_box)
        assert window.close_story()
        assert isValid(geometry_box)

        assert window.open_story_path(second_root)
        start = _selection(window.session, ContentKind.SCENE, "start")
        window.session.select(start)
        window._refresh_views()
        assert isValid(geometry_box)
        geometry_box.hide()
        geometry_box.show()
    finally:
        window.close()


@pytest.fixture(scope="module")
def qapp():
    if QApplication is None:
        pytest.skip("PySide6 is not installed")
    return QApplication.instance() or QApplication([])


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_project_dock_has_resizable_wider_default(qapp) -> None:
    window = MainWindow()
    try:
        assert window.project_dock.minimumWidth() >= 280
        assert window.project_dock.width() >= window.project_dock.minimumWidth()
        assert window.project_dock.maximumWidth() > window.project_dock.minimumWidth()
    finally:
        window.close()
