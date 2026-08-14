"""Headless integrity checks for the opt-in exploration reference content."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from engine.core.asset_loader import AssetLoader


DEMO_STORY = Path(__file__).resolve().parent.parent / "stories" / "demo_story"
SCENE_IDS = ("exploration_study", "exploration_hall", "exploration_archive")
DEMO_ITEM_IDS = ("ember_seal", "restorative_tea", "archivist_blade", "archivist_cloak")


def _condition_flags(condition: dict[str, Any] | None) -> dict[str, bool]:
    """Return the simple all/any flag checks used by the reference scenes."""
    if not condition:
        return {}
    result: dict[str, bool] = {}
    for group in ("all", "any"):
        entries = condition.get(group, [])
        assert isinstance(entries, list)
        for entry in entries:
            assert isinstance(entry, dict)
            flag, equals = entry.get("flag"), entry.get("equals")
            assert isinstance(flag, str) and flag
            assert isinstance(equals, bool)
            result[flag] = equals
    return result


def test_exploration_reference_scenes_have_resolvable_graph_and_assets():
    assets = AssetLoader(str(DEMO_STORY), "shared_assets")
    scenes = {scene_id: assets.load_scene(scene_id) for scene_id in SCENE_IDS}
    items = assets.load_items()

    for scene_id, scene in scenes.items():
        assert scene["id"] == scene_id
        assets.resolve_asset_path("backgrounds", scene["background"])
        exploration = scene["exploration"]
        assert isinstance(exploration, dict)

        sequences = exploration["dialogue_sequences"]
        for dialog in exploration["dialog"]:
            _condition_flags(dialog.get("conditions"))
            assert dialog["sequence"] in sequences

        object_ids: set[str] = set()
        for obj in exploration.get("objects", []):
            object_ids.add(obj["id"])
            assets.resolve_asset_path("sprites", obj["sprite"])
            _condition_flags(obj.get("visible_when"))

        events = exploration.get("look_events", {})
        target_ids: set[str] = set()
        for target in [*exploration.get("objects", []), *exploration.get("look_regions", [])]:
            assert target["id"] not in target_ids
            target_ids.add(target["id"])
            look = target.get("look", target)
            if "event" not in look:
                continue
            assert look["event"] in events
            assert look["interaction"] in {"inspect", "action"}
            rect = look["rect"]
            assert len(rect) == 4 and all(isinstance(value, int) for value in rect)
            assert rect[2] > 0 and rect[3] > 0

        for destination in exploration.get("navigation", []):
            _condition_flags(destination.get("conditions"))
            assert destination["scene"] in scenes
            assert destination["label"]

        for event_id, event in events.items():
            assert event_id
            for action in event["actions"]:
                action_type = action["type"]
                if action_type == "dialog":
                    assert action["dialog"] in sequences
                elif action_type == "sound":
                    assets.resolve_asset_path("sfx", action["file"])
                elif action_type == "animation":
                    assert action["target"] in object_ids
                    assets.load_animation(action["animation"])
                elif action_type == "give_item":
                    assert action["item"] in items
                    assert isinstance(action.get("quantity", 1), int)
                    assert action.get("quantity", 1) > 0
                elif action_type == "set_flag":
                    assert isinstance(action["flag"], str) and action["flag"]
                    assert isinstance(action["value"], bool)
                else:
                    raise AssertionError(f"{scene_id}.{event_id} uses unexpected action {action_type!r}")


def test_exploration_reference_items_cover_the_inventory_action_variants():
    assets = AssetLoader(str(DEMO_STORY), "shared_assets")
    items = assets.load_items()
    assert assets.load_player()["inventory_ui"] == {"columns": 4, "rows": 3}

    for item_id in DEMO_ITEM_IDS:
        item = items[item_id]
        assert item["icon"] == "heart.png"
        assets.resolve_asset_path("items", item["icon"])
        assert set(item["stats"]) == {"hp", "attack", "defense"}

    assert items["ember_seal"]["actions"] == []
    assert items["restorative_tea"]["actions"] == ["use", "toss"]
    assert items["restorative_tea"]["use"]["actions"] == [{"type": "heal", "amount": 10}]
    assert items["archivist_blade"]["equipment_slot"] == "weapon"
    assert items["archivist_blade"]["actions"] == ["equip", "toss"]
    assert items["archivist_cloak"]["equipment_slot"] == "armor"
    assert items["archivist_cloak"]["actions"] == ["equip", "toss"]


def test_exploration_study_documents_stateful_navigation_and_target_priority():
    assets = AssetLoader(str(DEMO_STORY), "shared_assets")
    study = assets.load_scene("exploration_study")["exploration"]

    archive_destination = next(entry for entry in study["navigation"] if entry["scene"] == "exploration_archive")
    assert _condition_flags(archive_destination["conditions"]) == {"archive_route_open": True}

    states = {
        dialog["sequence"]: _condition_flags(dialog.get("conditions"))
        for dialog in study["dialog"]
    }
    assert states["study_unsearched"] == {"study_token_found": False}
    assert states["study_searched"] == {"study_token_found": True}

    regions = {region["id"]: region for region in study["look_regions"]}
    assert regions["lamp_switch"]["priority"] > regions["bookcase"]["priority"]
