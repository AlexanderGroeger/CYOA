from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from engine.story_core import LegacyProjectView, Reference, load_story_project, serialize_project, write_serialized_project
from engine.story_core.diagnostics import DiagnosticSeverity
from engine.story_core.index import AmbiguousReferenceError
from story_core_fixture import write_fixture_story


def test_validation_collects_source_qualified_reference_and_condition_errors(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    manifest_path = story_root / "story.yaml"
    scene_path = story_root / "scenes" / "intro.yaml"
    events_path = story_root / "events" / "intro.yaml"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace(
            "start_scene: intro",
            "start_scene: missing_start",
        ),
        encoding="utf-8",
    )
    scene_path.write_text(
        scene_path.read_text(encoding="utf-8")
        + "  - text: Missing destination\n"
        + "    condition: flags.door ==\n"
        + "    goto: missing_scene\n",
        encoding="utf-8",
    )
    events_path.write_text(
        events_path.read_text(encoding="utf-8").replace("id: ending", "id: missing_event_scene"),
        encoding="utf-8",
    )

    diagnostics = load_story_project(story_root, shared_root).validate()
    errors = [item for item in diagnostics if item.severity is DiagnosticSeverity.ERROR]

    assert diagnostics.has_errors is True
    assert any(
        item.code == "unknown_scene_reference"
        and item.source == manifest_path
        and item.path == ("start_scene",)
        for item in errors
    )
    assert any(
        item.code == "unknown_scene_reference"
        and item.source == scene_path
        and item.path == ("choices", 3, "goto")
        for item in errors
    )
    assert any(
        item.code == "invalid_condition"
        and item.source == scene_path
        and item.path == ("choices", 3, "condition")
        for item in errors
    )
    assert any(
        item.code == "unknown_scene_reference"
        and item.source == events_path
        and item.path == ("events", 0, "id")
        for item in errors
    )


def test_scene_validation_matches_legacy_choice_conditions_and_transition_precedence(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    scene_path = story_root / "scenes" / "intro.yaml"
    scene_path.write_text(
        scene_path.read_text(encoding="utf-8")
        + "  - text: Structured condition is not a legacy choice condition\n"
        + "    condition: {flag: visited}\n"
        + "    goto: ending\n"
        + "  - text: Empty legacy mapping remains always available\n"
        + "    condition: {}\n"
        + "    goto: ending\n"
        + "  - text: Battle has runtime priority\n"
        + "    battle: intro\n"
        + "    goto: stale_missing_scene\n",
        encoding="utf-8",
    )

    diagnostics = load_story_project(story_root, shared_root).validate()
    errors = [item for item in diagnostics if item.severity is DiagnosticSeverity.ERROR]

    assert any(item.code == "invalid_condition" and item.path == ("choices", 3, "condition") for item in errors)
    assert not any(item.path == ("choices", 5, "goto") for item in errors)


def test_validation_keeps_malformed_exploration_and_display_visible(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    manifest_path = story_root / "story.yaml"
    scene_path = story_root / "scenes" / "intro.yaml"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace(
            "display: {width: 320, height: 180}",
            "display: {width: nope, height: 0}",
        ),
        encoding="utf-8",
    )
    scene_path.write_text(scene_path.read_text(encoding="utf-8") + "exploration: nope\n", encoding="utf-8")

    diagnostics = load_story_project(story_root, shared_root).validate()
    errors = [item for item in diagnostics if item.severity is DiagnosticSeverity.ERROR]

    assert any(item.code == "invalid_display_config" and item.path == ("display", "width") for item in errors)
    assert any(item.code == "invalid_display_config" and item.path == ("display", "height") for item in errors)
    assert any(item.code == "invalid_exploration_scene" and item.source == scene_path and item.path == ("exploration",) for item in errors)


def test_index_retains_duplicate_scene_candidates_and_refuses_ambiguous_lookup(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    duplicate = story_root / "scenes" / "nested" / "intro.yaml"
    duplicate.parent.mkdir(parents=True)
    duplicate.write_text("text: Other intro\n", encoding="utf-8")

    project = load_story_project(story_root, shared_root)

    assert len(project.index.candidates(Reference.scene("intro"))) == 2
    with pytest.raises(AmbiguousReferenceError):
        project.resolve(Reference.scene("intro"))
    with pytest.raises(AmbiguousReferenceError):
        project.scene("intro")
    with pytest.raises(AmbiguousReferenceError):
        LegacyProjectView(project).load_scene("intro")


def test_serialization_preserves_parseable_invalid_optional_documents(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    (story_root / "audio.yaml").write_text("not a mapping\n", encoding="utf-8")
    (story_root / "scenes" / "intro.yaml").write_text("- not a scene mapping\n", encoding="utf-8")

    project = load_story_project(story_root, shared_root)
    serialized = serialize_project(project)

    assert serialized["audio.yaml"] == "not a mapping"
    assert serialized["scenes/intro.yaml"] == ["not a scene mapping"]

    destination = tmp_path / "serialized"
    write_serialized_project(serialized, destination)
    assert yaml.safe_load((destination / "audio.yaml").read_text(encoding="utf-8")) == "not a mapping"
    assert yaml.safe_load((destination / "scenes" / "intro.yaml").read_text(encoding="utf-8")) == ["not a scene mapping"]


def test_validation_reports_raw_event_and_animation_numeric_fields(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    events_path = story_root / "events" / "intro.yaml"
    animation_path = story_root / "assets" / "animations" / "intro" / "anim.yaml"
    events_path.write_text(
        events_path.read_text(encoding="utf-8").replace("chance: 1", "chance: nope").replace("weight: 1", "weight: nope"),
        encoding="utf-8",
    )
    animation_path.write_text(
        animation_path.read_text(encoding="utf-8").replace("frame_delay_ms: 100", "frame_delay_ms: nope"),
        encoding="utf-8",
    )

    errors = [item.code for item in load_story_project(story_root, shared_root).validate() if item.severity is DiagnosticSeverity.ERROR]

    assert "invalid_event_chance" in errors
    assert "invalid_event_weight" in errors
    assert "invalid_animation_frame_delay" in errors


def test_validation_covers_audio_exploration_dialogue_and_move_availability_conditions(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    (story_root / "audio.yaml").write_text("master_volume: nope\n", encoding="utf-8")
    moves_path = story_root / "moves" / "moves.yaml"
    moves_path.write_text(
        moves_path.read_text(encoding="utf-8") + "    availability: {condition: 'flags.gate =='}\n",
        encoding="utf-8",
    )
    (story_root / "scenes" / "explore.yaml").write_text(
        "id: explore\n"
        "exploration:\n"
        "  dialogue_sequences:\n"
        "    intro:\n"
        "      text: Hello\n"
        "      actions:\n"
        "        - {type: unknown_event_action}\n"
        "  dialog:\n"
        "    - sequence: intro\n"
        "  navigation: []\n"
        "  objects: []\n"
        "  look_regions: []\n"
        "  look_events: {}\n",
        encoding="utf-8",
    )

    diagnostics = load_story_project(story_root, shared_root).validate()
    errors = [item for item in diagnostics if item.severity is DiagnosticSeverity.ERROR]

    assert any(item.code == "invalid_audio_volume" and item.path == ("master_volume",) for item in errors)
    assert any(item.code == "invalid_condition" and item.path == ("availability", "condition") for item in errors)
    assert any(
        item.code == "invalid_exploration_action"
        and item.path == ("exploration", "dialogue_sequences", "intro", "actions", 0)
        for item in errors
    )


def test_unknown_legacy_story_actions_are_source_qualified_errors(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    scene_path = story_root / "scenes" / "intro.yaml"
    scene_path.write_text(
        scene_path.read_text(encoding="utf-8").replace(
            "  - add_item: intro\nchoices:",
            "  - add_item: intro\n  - impossible_runtime_action: true\nchoices:",
        ),
        encoding="utf-8",
    )

    errors = [item for item in load_story_project(story_root, shared_root).validate() if item.severity is DiagnosticSeverity.ERROR]

    assert any(item.code == "invalid_story_action" and item.source == scene_path and item.path == ("actions",) for item in errors)


def test_dialogue_follow_up_actions_receive_exploration_asset_and_animation_checks(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    scene_path = story_root / "scenes" / "explore.yaml"
    scene_path.write_text(
        "id: explore\n"
        "exploration:\n"
        "  dialogue_sequences:\n"
        "    cue:\n"
        "      text: Cue\n"
        "      actions:\n"
        "        - {type: sound, file: missing_cue.wav}\n"
        "        - {type: music, file: missing_cue.mp3}\n"
        "        - {type: animation, target: statue, animation: missing_cue_animation}\n"
        "        - {type: change_sprite, target: statue, sprite: missing_cue.png}\n"
        "  dialog:\n"
        "    - sequence: cue\n"
        "  navigation: []\n"
        "  objects:\n"
        "    - id: statue\n"
        "  look_regions: []\n"
        "  look_events: {}\n",
        encoding="utf-8",
    )

    errors = [item for item in load_story_project(story_root, shared_root).validate() if item.severity is DiagnosticSeverity.ERROR]
    action_path = ("exploration", "dialogue_sequences", "cue", "actions")

    assert any(item.code == "missing_asset" and item.path == (*action_path, 0, "file") for item in errors)
    assert any(item.code == "missing_asset" and item.path == (*action_path, 1, "file") for item in errors)
    assert any(item.code == "unknown_animation_reference" and item.path == (*action_path, 2, "animation") for item in errors)
    assert any(item.code == "missing_asset" and item.path == (*action_path, 3, "sprite") for item in errors)


def test_exploration_look_state_conditions_are_indexed_and_source_qualified(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    scene_path = story_root / "scenes" / "explore.yaml"
    authored = (
        "id: explore\n"
        "exploration:\n"
        "  visit_flag: explore_seen\n"
        "  navigation: []\n"
        "  objects:\n"
        "    - id: statue\n"
        "      interaction: inspect\n"
        "      hitbox: [0, 0, 8, 8]\n"
        "      look:\n"
        "        event: inspect_statue\n"
        "        states:\n"
        "          - conditions: {flag: statue_awake}\n"
        "  look_regions:\n"
        "    - id: mural\n"
        "      rect: [10, 0, 8, 8]\n"
        "      interaction: inspect\n"
        "      event: inspect_mural\n"
        "      states:\n"
        "        - conditions: {variable: mural_count, equals: 1}\n"
        "  look_events:\n"
        "    inspect_statue: {actions: []}\n"
        "    inspect_mural: {actions: []}\n"
    )
    scene_path.write_text(authored, encoding="utf-8")

    project = load_story_project(story_root, shared_root)
    assert "statue_awake" in project.symbols.referenced_flags
    assert "mural_count" in project.symbols.referenced_variables
    assert "explore_seen" in project.symbols.declared_flags

    scene_path.write_text(
        authored.replace("{flag: statue_awake}", "{unknown: invalid_object}").replace(
            "{variable: mural_count, equals: 1}", "{unknown: invalid_region}",
        ),
        encoding="utf-8",
    )
    errors = [item for item in load_story_project(story_root, shared_root).validate() if item.severity is DiagnosticSeverity.ERROR]

    assert any(
        item.code == "invalid_condition"
        and item.source == scene_path
        and item.path == ("exploration", "objects", 0, "look", "states", 0, "conditions")
        for item in errors
    )
    assert any(
        item.code == "invalid_condition"
        and item.source == scene_path
        and item.path == ("exploration", "look_regions", 0, "states", 0, "conditions")
        for item in errors
    )


def test_root_alias_and_inline_dialogue_actions_are_recursively_checked(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    scene_path = story_root / "scenes" / "explore.yaml"
    scene_path.write_text(
        "id: explore\n"
        "exploration: true\n"
        "dialogue_sequences:\n"
        "  root_cue:\n"
        "    text: Root cue\n"
        "    actions:\n"
        "      - {type: set_flag, flag: root_alias_flag}\n"
        "      - {type: sound, file: missing_root_cue.wav}\n"
        "dialog:\n"
        "  - text: Opening\n"
        "    actions:\n"
        "      - type: dialog\n"
        "        dialog:\n"
        "          text: Nested opening\n"
        "          actions:\n"
        "            - {type: set_flag, flag: nested_opening_flag}\n"
        "            - {type: change_sprite, target: statue, sprite: missing_opening_sprite.png}\n"
        "navigation: []\n"
        "objects:\n"
        "  - id: statue\n"
        "look_regions: []\n"
        "look_events:\n"
        "  inspect:\n"
        "    actions:\n"
        "      - type: dialog\n"
        "        dialog:\n"
        "          text: Nested event\n"
        "          actions:\n"
        "            - {type: animation, target: statue, animation: missing_event_animation}\n"
        "            - {type: sound, file: missing_event_dialog.wav}\n",
        encoding="utf-8",
    )

    project = load_story_project(story_root, shared_root)
    errors = [item for item in project.validate() if item.severity is DiagnosticSeverity.ERROR]

    assert "root_alias_flag" in project.symbols.declared_flags
    assert "nested_opening_flag" in project.symbols.declared_flags
    assert not any(item.code == "invalid_story_action" for item in errors)
    assert any(
        item.code == "missing_asset"
        and item.path == ("dialogue_sequences", "root_cue", "actions", 1, "file")
        for item in errors
    )
    assert any(
        item.code == "missing_asset"
        and item.path == ("dialog", 0, "actions", 0, "dialog", "actions", 1, "sprite")
        for item in errors
    )
    assert any(
        item.code == "unknown_animation_reference"
        and item.path == ("look_events", "inspect", "actions", 0, "dialog", "actions", 0, "animation")
        for item in errors
    )
    assert any(
        item.code == "missing_asset"
        and item.path == ("look_events", "inspect", "actions", 0, "dialog", "actions", 1, "file")
        for item in errors
    )


def test_malformed_known_story_action_payloads_are_source_qualified_errors(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    scene_path = story_root / "scenes" / "intro.yaml"
    scene_path.write_text(
        scene_path.read_text(encoding="utf-8").replace(
            "  - add_item: intro\nchoices:",
            "  - add_item: intro\n  - set_flag: nope\n  - equip_item: nope\nchoices:",
        ),
        encoding="utf-8",
    )

    errors = [item for item in load_story_project(story_root, shared_root).validate() if item.severity is DiagnosticSeverity.ERROR]

    # One list-level diagnostic is enough to make the malformed entry actions
    # unambiguously invalid; the interpreter would stop at the first one too.
    assert any(item.code == "invalid_story_action" and item.source == scene_path for item in errors)


def test_battle_availability_and_item_effect_symbols_are_scoped_and_validated(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    moves_path = story_root / "moves" / "moves.yaml"
    moves_path.write_text(
        moves_path.read_text(encoding="utf-8")
        + "    availability:\n"
        + "      requires_flags: {persistent_gate: true}\n",
        encoding="utf-8",
    )
    (story_root / "items" / "items.yaml").write_text(
        "intro:\n"
        "  name: Intro Token\n"
        "  type: key\n"
        "  combat:\n"
        "    effects:\n"
        "      - set_fight_flag: {item_triggered_flag: true}\n",
        encoding="utf-8",
    )
    (story_root / "battles" / "intro.yaml").write_text(
        "id: intro\n"
        "enemy: {name: Training Dummy, hp: 1, attack: 0, defense: 0}\n"
        "enemy_moves:\n"
        "  - id: enemy\n"
        "    availability: {requires_fight_flags: {enemy_ready: true}}\n"
        "phases:\n"
        "  - when: {fight_flag: phase_ready}\n"
        "    actions: []\n",
        encoding="utf-8",
    )

    project = load_story_project(story_root, shared_root)

    assert "persistent_gate" in project.symbols.referenced_flags
    assert {"enemy_ready", "phase_ready", "item_triggered_flag"} <= project.symbols.fight_flags

    moves_path.write_text(
        moves_path.read_text(encoding="utf-8").replace(
            "requires_flags: {persistent_gate: true}", "requires_flags: nope"
        ),
        encoding="utf-8",
    )
    errors = [item for item in load_story_project(story_root, shared_root).validate() if item.severity is DiagnosticSeverity.ERROR]
    assert any(
        item.code == "invalid_availability_requirement"
        and item.source == moves_path
        and item.path == ("availability", "requires_flags")
        for item in errors
    )
