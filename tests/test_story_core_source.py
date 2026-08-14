from __future__ import annotations

from pathlib import Path

import pytest

from engine.story_core.source import StorySource, StorySourceError


def _write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


def test_story_source_isolates_core_reads_but_retains_legacy_cache_identity(tmp_path: Path) -> None:
    story_root = tmp_path / "story"
    shared_root = tmp_path / "shared"
    _write(story_root / "story.yaml", "nested:\n  value: original\n")
    source = StorySource(story_root, shared_root)

    first_core_read = source.load_yaml("story.yaml")
    first_core_read["nested"]["value"] = "changed only in caller copy"

    assert source.load_yaml("story.yaml")["nested"]["value"] == "original"

    first_legacy_read = source.load_yaml_legacy("story.yaml")
    second_legacy_read = source.load_yaml_legacy("story.yaml")
    assert first_legacy_read is second_legacy_read

    first_legacy_read["nested"]["value"] = "legacy cache mutation"
    assert source.load_yaml("story.yaml")["nested"]["value"] == "legacy cache mutation"
    assert first_core_read["nested"]["value"] == "changed only in caller copy"


def test_story_source_discovers_recursively_and_records_document_provenance(tmp_path: Path) -> None:
    story_root = tmp_path / "story"
    _write(story_root / "scenes" / "z_last.yaml", "id: z_last\n")
    _write(story_root / "scenes" / "nested" / "a_first.yaml", "id: a_first\n")
    source = StorySource(story_root, tmp_path / "shared")

    found = source.discover_yaml("scenes")
    assert [path.relative_to(story_root).as_posix() for path in found] == [
        "scenes/nested/a_first.yaml",
        "scenes/z_last.yaml",
    ]

    document = source.load_document("scenes/nested/a_first.yaml")
    assert document.path == story_root / "scenes" / "nested" / "a_first.yaml"
    assert document.relative_path == "scenes/nested/a_first.yaml"
    assert document.root == "story"
    assert document.data == {"id": "a_first"}


def test_story_source_prefers_story_assets_then_falls_back_to_shared_assets(tmp_path: Path) -> None:
    story_root = tmp_path / "story"
    shared_root = tmp_path / "shared"
    local_sound = story_root / "assets" / "sfx" / "ping.wav"
    shared_sound = shared_root / "sfx" / "ping.wav"
    shared_only_sound = shared_root / "sfx" / "shared_only.wav"
    _write(local_sound, "local")
    _write(shared_sound, "shared")
    _write(shared_only_sound, "shared only")
    source = StorySource(story_root, shared_root)

    assert source.resolve_asset_path("sfx", "ping.wav") == local_sound
    assert source.resolve_asset_path("sfx", "shared_only.wav") == shared_only_sound


def test_story_source_reports_machine_readable_missing_source_errors(tmp_path: Path) -> None:
    source = StorySource(tmp_path / "story", tmp_path / "shared")

    with pytest.raises(StorySourceError) as caught:
        source.load_yaml("missing.yaml")

    assert caught.value.code == "source_not_found"
    assert caught.value.path == tmp_path / "story" / "missing.yaml"
