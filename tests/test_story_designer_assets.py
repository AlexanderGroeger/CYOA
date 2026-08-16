from __future__ import annotations

import os
from pathlib import Path

import pytest

from engine.story_core import StorySource
from story_core_fixture import write_fixture_story
from story_designer.models.assets import AssetBrowserModel


def _touch(path: Path, contents: str = "asset") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


def test_asset_discovery_uses_story_first_shared_fallback_and_canonical_references(tmp_path: Path) -> None:
    story_root = tmp_path / "story"
    shared_root = tmp_path / "shared"
    local = story_root / "assets" / "backgrounds" / "local.png"
    shadow_local = story_root / "assets" / "backgrounds" / "shadow.png"
    _touch(local)
    _touch(shadow_local)
    _touch(shared_root / "backgrounds" / "shared.png")
    _touch(shared_root / "backgrounds" / "shadow.png")
    _touch(story_root / "assets" / "backgrounds" / "legacy.txt")
    _touch(shared_root / "sprites" / "legacy.txt")
    _touch(shared_root / "sfx" / "click.wav")
    _touch(story_root / "assets" / "animations" / "sparkle" / "one.png")
    _touch(story_root / "assets" / "animations" / "sparkle" / "two.png")
    _touch(story_root / "assets" / "animations" / "sparkle" / "anim.yaml", "frames: [one.png, two.png]\n")
    source = StorySource(story_root, shared_root)

    records = source.discover_assets()
    by_reference = {(record.asset_kind, record.reference): record for record in records}
    assert by_reference[("backgrounds", "local.png")].source_kind == "Story"
    assert by_reference[("backgrounds", "shared.png")].source_kind == "Shared"
    assert by_reference[("backgrounds", "shadow.png")].source_kind == "Story"
    assert not any(record.reference == "legacy.txt" for record in records)
    assert by_reference[("sfx", "click.wav")].reference == "click.wav"
    assert by_reference[("animation", "sparkle")].metadata["frame_count"] == 2
    assert source.authored_asset_reference(shared_root / "backgrounds" / "shared.png", "backgrounds") == "shared.png"
    assert source.authored_asset_reference(tmp_path / "outside.png", "backgrounds") is None


def test_asset_model_search_and_expected_category_priority(tmp_path: Path) -> None:
    source = StorySource(tmp_path / "story", tmp_path / "shared")
    _touch(tmp_path / "story" / "assets" / "sprites" / "key.png")
    _touch(tmp_path / "shared" / "music" / "ambience.ogg")
    _touch(tmp_path / "shared" / "backgrounds" / "hallway.png")
    model = AssetBrowserModel(source)

    assert [record.reference for record in model.filtered("hallway")] == ["hallway.png"]
    candidates = model.filtered(expected_kind="sprites")
    assert candidates[0].asset_kind == "sprites"
    assert model.filtered("music")[0].asset_kind == "music"


def test_missing_reference_is_preserved_as_a_record(tmp_path: Path) -> None:
    source = StorySource(tmp_path / "story", tmp_path / "shared")
    missing = source.asset_record_for_reference("gone.png", "backgrounds")
    assert missing.reference == "gone.png"
    assert missing.resolved_path is None
    assert missing.source_kind == "Missing"


def test_asset_discovery_filters_by_engine_category_and_keeps_sources_isolated(tmp_path: Path) -> None:
    story_a = tmp_path / "story_a"
    story_b = tmp_path / "story_b"
    shared = tmp_path / "shared"
    _touch(story_a / "assets" / "backgrounds" / "a.png")
    _touch(story_b / "assets" / "backgrounds" / "b.png")
    _touch(shared / "backgrounds" / "shared.jpg")
    _touch(shared / "music" / "theme.ogg")
    _touch(shared / "music" / ".gitkeep")
    _touch(shared / "music" / "notes.txt")
    _touch(shared / "music" / "backup.tmp")
    _touch(shared / "fonts" / "ui.ttf")
    _touch(shared / "fonts" / "ui.txt")
    _touch(shared / "misc" / "not_an_asset.png")

    records_a = StorySource(story_a, shared).discover_assets()
    assert {(record.source_kind, record.asset_kind, record.reference) for record in records_a} == {
        ("Story", "backgrounds", "a.png"),
        ("Shared", "backgrounds", "shared.jpg"),
        ("Shared", "fonts", "ui.ttf"),
        ("Shared", "music", "theme.ogg"),
    }
    records_b = StorySource(story_b, shared).discover_assets()
    assert any(record.reference == "b.png" and record.source_kind == "Story" for record in records_b)
    assert not any(record.reference == "a.png" for record in records_b)


def test_asset_model_category_and_source_choices_are_unique(tmp_path: Path) -> None:
    story = tmp_path / "story"
    shared = tmp_path / "shared"
    _touch(story / "assets" / "backgrounds" / "local.png")
    _touch(shared / "backgrounds" / "shared.png")
    _touch(shared / "music" / "theme.ogg")
    _touch(shared / "sfx" / "click.wav")
    model = AssetBrowserModel(StorySource(story, shared))

    assert model.available_categories() == ("backgrounds", "music", "sfx")
    assert model.available_sources() == ("Story", "Shared")
    assert {record.asset_kind for record in model.filtered(categories={"backgrounds"})} == {"backgrounds"}


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import Qt
    from PySide6.QtMultimedia import QMediaPlayer
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
    from story_designer.widgets.asset_browser import AssetBrowserWidget
    from story_designer.widgets.property_editors import AssetPathEditor
except ImportError:  # pragma: no cover
    QApplication = None  # type: ignore[assignment]


@pytest.fixture(scope="module")
def qapp():
    if QApplication is None:
        pytest.skip("PySide6 is not installed")
    return QApplication.instance() or QApplication([])


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_browser_picker_search_and_external_file_safety(qapp, tmp_path: Path, monkeypatch) -> None:
    source = StorySource(tmp_path / "story", tmp_path / "shared")
    known = tmp_path / "shared" / "sfx" / "door.wav"
    _touch(known)
    browser = AssetBrowserWidget(source, expected_kind="sfx", picker_mode=True)
    browser.search.setText("door")
    assert browser.asset_list.count() == 1
    assert browser.choose_current() is not None
    assert browser.selected_asset.reference == "door.wav"

    editor = AssetPathEditor(source=source, asset_kind="sfx")
    outside = tmp_path / "outside.wav"
    _touch(outside)
    warnings: list[str] = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args, **kwargs: (str(outside), ""))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: warnings.append(str(args[1])))
    editor._browse_files()
    assert editor.text() == ""
    assert warnings
    browser.close()
    editor.close()


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_picker_context_locks_type_and_uses_category_title(qapp, tmp_path: Path) -> None:
    source = StorySource(tmp_path / "story", tmp_path / "shared")
    _touch(tmp_path / "shared" / "backgrounds" / "scene.png")
    _touch(tmp_path / "shared" / "music" / "theme.ogg")
    from story_designer.widgets.asset_browser import AssetBrowserDialog

    dialog = AssetBrowserDialog(source, picker_category="backgrounds")
    assert dialog.windowTitle() == "Select Background"
    assert dialog.browser.type_filter.isEnabled() is False
    assert dialog.browser.type_filter.count() == 1
    assert dialog.browser.type_filter.currentData() == "backgrounds"
    assert dialog.browser.asset_list.count() == 1
    assert dialog.browser.asset_list.item(0).data(Qt.ItemDataRole.UserRole).asset_kind == "backgrounds"
    dialog.reject()


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_widget_filters_react_without_rediscovery(qapp, tmp_path: Path, monkeypatch) -> None:
    source = StorySource(tmp_path / "story", tmp_path / "shared")
    _touch(tmp_path / "shared" / "backgrounds" / "scene.png")
    _touch(tmp_path / "shared" / "music" / "theme.ogg")
    browser = AssetBrowserWidget(source)
    calls = 0
    original = source.discover_assets

    def count_discovery():
        nonlocal calls
        calls += 1
        return original()

    monkeypatch.setattr(source, "discover_assets", count_discovery)
    browser.type_filter.setCurrentIndex(browser.type_filter.findData("music"))
    assert browser.asset_list.count() == 1
    browser.source_filter.setCurrentIndex(browser.source_filter.findData("Shared"))
    browser.search.setText("theme")
    assert browser.asset_list.count() == 1
    assert calls == 0
    browser.close()


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_refresh_rediscovers_external_files(qapp, tmp_path: Path) -> None:
    source = StorySource(tmp_path / "story", tmp_path / "shared")
    _touch(tmp_path / "shared" / "backgrounds" / "before.png")
    browser = AssetBrowserWidget(source)
    _touch(tmp_path / "shared" / "backgrounds" / "after.png")
    assert not any(record.reference == "after.png" for record in browser.filtered_records())
    browser.refresh()
    assert any(record.reference == "after.png" for record in browser.filtered_records())
    browser.close()


class _FakeMediaPlayer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.state = QMediaPlayer.PlaybackState.PausedState

    def stop(self) -> None:
        self.calls.append(("stop", None))
        self.state = QMediaPlayer.PlaybackState.StoppedState

    def setSource(self, source) -> None:  # noqa: N802 - Qt API name
        self.calls.append(("source", source))

    def play(self) -> None:
        self.calls.append(("play", None))
        self.state = QMediaPlayer.PlaybackState.PlayingState

    def pause(self) -> None:
        self.calls.append(("pause", None))
        self.state = QMediaPlayer.PlaybackState.PausedState

    def playbackState(self):  # noqa: N802 - Qt API name
        return self.state

    def setPosition(self, position: int) -> None:  # noqa: N802 - Qt API name
        self.calls.append(("position", position))


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_audio_preview_play_pause_seek_and_lifecycle(qapp, tmp_path: Path) -> None:
    source = StorySource(tmp_path / "story", tmp_path / "shared")
    first = tmp_path / "shared" / "music" / "first.ogg"
    second = tmp_path / "shared" / "sfx" / "second.wav"
    _touch(first)
    _touch(second)
    browser = AssetBrowserWidget(source, picker_category="music", picker_mode=True)
    fake = _FakeMediaPlayer()
    browser.audio_player = fake
    browser._audio_record_key = None
    records = browser.model.filtered(categories={"music"})
    browser._show_record(records[0])
    assert [name for name, _value in fake.calls if name == "play"] == ["play"]
    browser._toggle_audio()
    browser._toggle_audio()
    browser._audio_duration_changed(5000)
    browser.audio_slider.setValue(1234)
    browser._seek_audio()
    assert ("pause", None) in fake.calls
    assert ("position", 1234) in fake.calls
    browser._show_record(browser.model.filtered(categories={"sfx"})[0])
    stop_index = next(index for index, call in enumerate(fake.calls) if call[0] == "stop")
    play_index = max(index for index, call in enumerate(fake.calls) if call[0] == "play")
    assert stop_index < play_index
    browser.close()


def test_asset_reference_can_be_used_by_normal_editor_command(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    _touch(shared_root / "backgrounds" / "library.png")
    from engine.story_core import ContentKind
    from story_designer.models import DefinitionSelection, ProjectSession, SetPropertyCommand

    session = ProjectSession.from_path(story_root, shared_root)
    assert session.project is not None and session.project.index is not None
    entry = session.project.index.entry(ContentKind.SCENE, "intro")
    assert entry is not None
    selection = DefinitionSelection(ContentKind.SCENE, "intro", entry.source)
    session.select(selection)
    session.apply_command(SetPropertyCommand(selection, ("background",), "library.png"))
    assert session.working_mapping(selection)["background"] == "library.png"
    session.undo()
    assert "background" not in session.working_mapping(selection)
    session.redo()
    assert session.working_mapping(selection)["background"] == "library.png"
    assert session.save()
    assert not session.is_dirty
    assert session.project is not None
    assert session.project.scene("intro").to_mapping()["background"] == "library.png"
