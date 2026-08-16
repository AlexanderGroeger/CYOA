from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from engine.story_core import ContentKind, migrate_legacy_object_interactions
from story_designer.models import DefinitionSelection, ProjectSession
from story_core_fixture import write_fixture_story


def test_legacy_object_interaction_migrates_geometry_and_unknown_actions() -> None:
    scene = {
        "id": "study",
        "exploration": {
            "objects": [{
                "id": "cabinet",
                "position": [100, 50],
                "size": [80, 40],
                "look": {"interaction": "action", "rect": [1, 2, 3, 4]},
                "actions": [
                    {"type": "set_flag", "flag": "opened", "value": True, "future": {"keep": 1}},
                    {"type": "unknown_action", "opaque": [1, 2, 3]},
                ],
            }],
            "look_regions": [{"id": "cabinet_interaction", "rect": [0, 0, 1, 1]}],
        },
    }
    original = deepcopy(scene)

    migrated = migrate_legacy_object_interactions(scene)
    assert scene == original
    object_mapping = migrated["exploration"]["objects"][0]
    assert "look" not in object_mapping and "actions" not in object_mapping
    region = migrated["exploration"]["look_regions"][1]
    assert region["id"] == "cabinet_interaction_2"
    assert region["rect"] == [100, 50, 80, 40]
    assert region["interaction"] == "action"
    event = migrated["exploration"]["look_events"][region["event"]]
    assert event["actions"][0]["future"] == {"keep": 1}
    assert event["actions"][1]["opaque"] == [1, 2, 3]


def test_legacy_look_event_is_copied_and_inspect_semantics_survive() -> None:
    scene = {
        "exploration": {
            "objects": [{
                "id": "painting",
                "position": [4, 5],
                "look": {"interaction": "inspect", "event": "inspect_painting"},
            }],
            "look_events": {
                "inspect_painting": {"actions": [{"type": "dialog", "dialog": "painting_text"}]},
            },
        },
    }
    migrated = migrate_legacy_object_interactions(scene)
    region = migrated["exploration"]["look_regions"][0]
    assert region["interaction"] == "inspect"
    assert region["rect"] == [4, 5, 48, 36]
    assert migrated["exploration"]["look_events"][region["event"]]["actions"] == [
        {"type": "dialog", "dialog": "painting_text"}
    ]


def test_designer_migrates_in_working_state_and_saves_canonical_scene(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    (story_root / "scenes" / "intro.yaml").write_text(
        "id: intro\n"
        "exploration:\n"
        "  objects:\n"
        "    - id: cabinet\n"
        "      position: [10, 20]\n"
        "      size: [30, 25]\n"
        "      actions:\n"
        "        - type: set_flag\n"
        "          flag: opened\n"
        "          value: true\n",
        encoding="utf-8",
    )
    session = ProjectSession.from_path(story_root, shared_root)
    selection = DefinitionSelection(ContentKind.SCENE, "intro")
    session.select(selection)
    mapping = session.working_mapping(selection)
    assert mapping is not None
    assert session.is_definition_dirty(selection)
    assert "actions" not in mapping["exploration"]["objects"][0]
    assert mapping["exploration"]["look_regions"][0]["rect"] == [10, 20, 30, 25]
    assert session.save_all()

    reloaded = ProjectSession.from_path(story_root, shared_root)
    reloaded_mapping = reloaded.working_mapping(DefinitionSelection(ContentKind.SCENE, "intro"))
    assert reloaded_mapping is not None
    assert "actions" not in reloaded_mapping["exploration"]["objects"][0]
    assert reloaded_mapping["exploration"]["look_regions"][0]["interaction"] == "action"
