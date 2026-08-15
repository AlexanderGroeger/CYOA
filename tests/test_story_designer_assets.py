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
    _touch(shared_root / "sfx" / "click.wav")
    _touch(story_root / "assets" / "animations" / "sparkle" / "anim.yaml", "frames: [one.txt, two.txt]\n")
    source = StorySource(story_root, shared_root)

    records = source.discover_assets()
    by_reference = {(record.asset_kind, record.reference): record for record in records}
    assert by_reference[("backgrounds", "local.png")].source_kind == "Story"
    assert by_reference[("backgrounds", "shared.png")].source_kind == "Shared"
    assert by_reference[("backgrounds", "shadow.png")].source_kind == "Story"
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


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
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
