from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from engine.story_core import SaveCompatibilityAdapter, load_story_project
from engine.story_core.diagnostics import DiagnosticSeverity
from story_core_fixture import write_fixture_story


def test_save_reference_adapter_keeps_removed_definition_ids_advisory_and_read_only(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    project = load_story_project(story_root, shared_root)
    payload = {
        "save_format_version": 1,
        "story_id": "fixture_story",
        "state": {
            "current_scene": "removed_scene",
            "inventory": {"removed_item": 2},
            # Equipment ownership is deliberately not required. A known item
            # may be equipped even when absent from the saved inventory.
            "equipment": {"weapon": "intro"},
            "known_moves": ["intro", "removed_move"],
            "known_combat_moves": {"removed_move": {"current_level": 1}},
            "history": ["intro", "removed_scene"],
        },
    }
    before = deepcopy(payload)
    source = tmp_path / "slot1.json"

    diagnostics = SaveCompatibilityAdapter(project).validate(payload, source=source)

    assert payload == before
    assert diagnostics.has_errors is False
    assert all(item.severity is DiagnosticSeverity.WARNING for item in diagnostics)
    assert {(item.code, item.path) for item in diagnostics} >= {
        ("unknown_saved_scene", ("state", "current_scene")),
        ("unknown_saved_item", ("state", "inventory", "removed_item")),
        ("unknown_saved_move", ("state", "known_moves", 1)),
        ("unknown_saved_combat_move", ("state", "known_combat_moves", "removed_move")),
        ("unknown_saved_history_scene", ("state", "history", 1)),
    }
    assert all(item.source == source for item in diagnostics)
    assert not any(item.code == "unknown_saved_equipment_item" for item in diagnostics)
