from __future__ import annotations

from pathlib import Path

from engine.story_core import (
    load_story_project,
    serialize_project,
    write_serialized_project,
)
from story_core_fixture import write_fixture_story


def test_semantic_serialization_preserves_definition_documents_and_round_trips(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    project = load_story_project(story_root, shared_root)

    serialized = serialize_project(project)
    assert serialized == project.serialize()
    assert serialized["scenes/intro.yaml"]["future_scene_extension"] == {"preserves": True}
    assert serialized["items/items.yaml"]["intro"]["future_item_extension"] == {"preserves": True}
    assert serialized["moves/moves.yaml"]["moves"][0]["future_move_extension"] == {"preserves": True}
    assert serialized["battles/intro.yaml"]["future_battle_extension"] == {"preserves": True}

    # Serialization is an isolated YAML-compatible export, not a mutable
    # backdoor into the project's immutable authored definitions.
    serialized["scenes/intro.yaml"]["future_scene_extension"]["preserves"] = False
    assert project.scene("intro").authored["future_scene_extension"]["preserves"] is True

    output_root = tmp_path / "semantic_output"
    expected = serialize_project(project)
    write_serialized_project(project, output_root)
    reloaded = load_story_project(output_root, shared_root)

    assert reloaded.serialize() == expected
    assert reloaded.manifest.to_mapping() == project.manifest.to_mapping()
    assert reloaded.scene("intro").to_mapping() == project.scene("intro").to_mapping()
    assert reloaded.item("intro").to_mapping() == project.item("intro").to_mapping()
    assert reloaded.move("intro").to_mapping() == project.move("intro").to_mapping()
    assert reloaded.battle("intro").to_mapping() == project.battle("intro").to_mapping()
    assert reloaded.event_pool("intro").to_mapping() == project.event_pool("intro").to_mapping()
    assert reloaded.animation("intro").to_mapping() == project.animation("intro").to_mapping()
