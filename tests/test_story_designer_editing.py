from __future__ import annotations

from pathlib import Path

import pytest

from engine.story_core import ContentKind, FieldSpec, Schema, TypeSpec
from engine.story_core.schema import MISSING, default_schema_registry
from story_core_fixture import write_fixture_story
from story_designer.models import (
    DefinitionSelection,
    EditValidationError,
    ProjectSession,
    RemovePropertyCommand,
    SetPropertyCommand,
)


def _session(tmp_path: Path) -> ProjectSession:
    story_root, shared_root = write_fixture_story(tmp_path)
    return ProjectSession.from_path(story_root, shared_root)


def _selection(session: ProjectSession, kind: ContentKind, identifier: str) -> DefinitionSelection:
    assert session.project is not None and session.project.index is not None
    entry = session.project.index.entry(kind, identifier)
    assert entry is not None
    return DefinitionSelection(kind, identifier, entry.source)


def test_schema_resolution_uses_the_project_registry_for_all_principal_types(tmp_path: Path) -> None:
    session = _session(tmp_path)
    for kind, identifier, schema_name in (
        (ContentKind.SCENE, "intro", "scene"),
        (ContentKind.ITEM, "intro", "item"),
        (ContentKind.BATTLE, "intro", "battle"),
        (ContentKind.MOVE, "intro", "move"),
        (ContentKind.EVENT_POOL, "intro", "event_pool"),
    ):
        selection = _selection(session, kind, identifier)
        assert session.schema_for(selection).name == schema_name


def test_descriptors_expose_authored_effective_default_and_reference_metadata(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = _selection(session, ContentKind.SCENE, "intro")
    model = session.property_model(scene)
    assert model is not None

    checkpoint = model.descriptor(("checkpoint",))
    assert checkpoint.is_authored is False
    assert checkpoint.authored_value is MISSING
    assert checkpoint.effective_value is False
    assert checkpoint.default is False
    assert checkpoint.display_name == "Checkpoint"

    manifest = _selection(session, ContentKind.MANIFEST, "fixture_story")
    manifest_model = session.property_model(manifest)
    assert manifest_model is not None
    reference = manifest_model.descriptor(("start_scene",))
    assert reference.reference_target == "scene"
    assert reference.effective_value == "intro"
    assert set(reference.reference_candidates) >= {"intro", "ending"}


def test_scalar_commands_validate_and_keep_core_snapshot_immutable(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = _selection(session, ContentKind.SCENE, "intro")
    assert session.project is not None

    session.apply_command(SetPropertyCommand(scene, ("text",), "Edited text"))
    assert session.working_mapping(scene)["text"] == "Edited text"
    assert session.project.scene("intro").text == "Welcome."
    assert session.is_dirty
    assert session.is_definition_dirty(scene)

    move = _selection(session, ContentKind.MOVE, "intro")
    session.apply_command(SetPropertyCommand(move, ("initial_level",), 4))
    assert session.working_mapping(move)["initial_level"] == 4
    assert session.project.move("intro").to_mapping().get("initial_level") is None

    with pytest.raises(EditValidationError, match="integer"):
        session.apply_command(SetPropertyCommand(move, ("initial_level",), "four"))
    with pytest.raises(EditValidationError, match="at least"):
        session.apply_command(SetPropertyCommand(move, ("initial_level",), -1))


def test_float_boolean_and_enum_constraints_are_applied_before_mutation(tmp_path: Path) -> None:
    session = _session(tmp_path)
    audio = DefinitionSelection(ContentKind.AUDIO, "audio", session.story_root / "audio.yaml")
    session.apply_command(SetPropertyCommand(audio, ("master_volume",), 0.25))
    session.apply_command(SetPropertyCommand(audio, ("effects_volume",), 0))
    with pytest.raises(EditValidationError, match="at most"):
        session.apply_command(SetPropertyCommand(audio, ("master_volume",), 1.5))
    with pytest.raises(EditValidationError, match="number"):
        session.apply_command(SetPropertyCommand(audio, ("master_volume",), True))

    assert session.project is not None and session.project.schema_registry is not None
    scene_schema = session.project.schema_registry.require("scene")
    session.project.schema_registry.register(
        Schema(
            "scene",
            (*scene_schema.fields, FieldSpec("mode", type=TypeSpec.enum(("easy", "hard")), default="easy")),
        ),
        replace=True,
    )
    scene = _selection(session, ContentKind.SCENE, "intro")
    session.apply_command(SetPropertyCommand(scene, ("mode",), "hard"))
    with pytest.raises(EditValidationError, match="allowed enum"):
        session.apply_command(SetPropertyCommand(scene, ("mode",), "expert"))


def test_optional_field_addition_does_not_materialize_other_defaults(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = _selection(session, ContentKind.SCENE, "intro")
    session.select(scene)
    before = session.working_mapping(scene)
    assert "music" not in before
    assert "background" not in before

    session.apply_command(SetPropertyCommand(scene, ("music",), "calm.ogg"))
    after = session.working_mapping(scene)
    assert after["music"] == "calm.ogg"
    assert "background" not in after
    assert "checkpoint" not in after


def test_remove_authored_value_restores_default_without_writing_it(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = _selection(session, ContentKind.SCENE, "intro")
    session.apply_command(SetPropertyCommand(scene, ("checkpoint",), True))
    assert session.property_model(scene).descriptor(("checkpoint",)).effective_value is True  # type: ignore[union-attr]
    session.apply_command(RemovePropertyCommand(scene, ("checkpoint",)))
    descriptor = session.property_model(scene).descriptor(("checkpoint",))  # type: ignore[union-attr]
    assert descriptor.is_authored is False
    assert descriptor.effective_value is False
    assert "checkpoint" not in session.working_mapping(scene)


def test_nested_paths_create_only_the_requested_parent_and_are_addressable(tmp_path: Path) -> None:
    session = _session(tmp_path)
    item = _selection(session, ContentKind.ITEM, "intro")
    session.apply_command(SetPropertyCommand(item, ("stats", "attack"), 5))
    model = session.property_model(item)
    assert model is not None
    nested = model.descriptor(("stats", "attack"))
    assert nested.path == ("stats", "attack")
    assert nested.effective_value == 5
    assert nested.type_spec is not None and nested.type_spec.kind == "integer"
    assert session.working_mapping(item)["stats"] == {"attack": 5}


def test_unknown_authored_fields_survive_known_field_edits_and_semantic_projection(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = _selection(session, ContentKind.SCENE, "intro")
    session.apply_command(SetPropertyCommand(scene, ("text",), "Changed"))

    working = session.working_mapping(scene)
    assert working["future_scene_extension"] == {"preserves": True}
    assert session.project is not None
    assert session.project.scene("intro").to_mapping()["text"] == "Welcome."
    assert session.semantic_documents()["scenes/intro.yaml"]["future_scene_extension"] == {"preserves": True}
    assert session.semantic_documents()["scenes/intro.yaml"]["text"] == "Changed"


def test_revert_and_reload_discard_pending_editor_state_explicitly(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = _selection(session, ContentKind.SCENE, "intro")
    session.select(scene)
    session.apply_command(SetPropertyCommand(scene, ("text",), "Pending"))
    assert session.dirty
    session.revert_definition(scene)
    assert not session.dirty
    assert session.working_mapping(scene)["text"] == "Welcome."

    session.apply_command(SetPropertyCommand(scene, ("text",), "Discard me"))
    session.reload(session.project.shared_assets_root if session.project is not None else None)
    assert not session.dirty
    assert session.selection == scene
    assert session.working_mapping(scene)["text"] == "Welcome."


def test_command_contains_before_after_information_for_future_undo(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = _selection(session, ContentKind.SCENE, "intro")
    command = SetPropertyCommand(scene, ("text",), "After")
    session.apply_command(command)
    assert command.old_authored_value == "Welcome."
    assert command.new_authored_value == "After"
