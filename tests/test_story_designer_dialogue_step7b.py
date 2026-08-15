from __future__ import annotations

from pathlib import Path

import pytest

from engine.story_core import ActionForm, ActionScope, parse_action
from engine.story_core import ContentKind
from story_core_fixture import write_fixture_story
from story_designer.models import (
    DefinitionSelection,
    DialogueDocumentModel,
    DuplicateDialogueActionCommand,
    DuplicateNamedDialogueSequenceCommand,
    InsertDialogueActionCommand,
    InsertNamedDialogueSequenceCommand,
    MoveDialogueActionCommand,
    ProjectSession,
    RemoveDialogueActionCommand,
    RemoveNamedDialogueSequenceCommand,
    RenameNamedDialogueSequenceCommand,
    SetDialogueActionParameterCommand,
    dialogue_sequence_references,
    present_dialogue_actions,
)


def _session(tmp_path: Path, scene_text: str) -> tuple[ProjectSession, DefinitionSelection]:
    story_root, shared_root = write_fixture_story(tmp_path)
    (story_root / "scenes" / "intro.yaml").write_text(scene_text, encoding="utf-8")
    session = ProjectSession.from_path(story_root, shared_root)
    entry = session.project.index.entry(ContentKind.SCENE, "intro")  # type: ignore[union-attr]
    assert entry is not None
    selection = DefinitionSelection(ContentKind.SCENE, "intro", entry.source)
    session.select(selection)
    return session, selection


def test_named_sequences_add_duplicate_delete_and_undo_preserve_payload(tmp_path: Path) -> None:
    session, selection = _session(
        tmp_path,
        "id: intro\nexploration:\n  dialogue_sequences:\n    first:\n      text: Hello\n      actions: [{type: set_flag, flag: old, value: true, future: keep}]\n",
    )
    path = ("exploration", "dialogue_sequences")
    add = InsertNamedDialogueSequenceCommand(selection, path)
    session.apply_command(add)
    assert add.sequence_id == "dialogue"
    duplicate = DuplicateNamedDialogueSequenceCommand(selection, path, "first")
    session.apply_command(duplicate)
    mapping = session.working_mapping(selection)
    assert mapping["exploration"]["dialogue_sequences"][duplicate.duplicate_id]["actions"][0]["future"] == "keep"
    session.undo()
    assert duplicate.duplicate_id not in session.working_mapping(selection)["exploration"]["dialogue_sequences"]
    session.redo()
    session.apply_command(RemoveNamedDialogueSequenceCommand(selection, path, "dialogue"))
    session.undo()
    assert "dialogue" in session.working_mapping(selection)["exploration"]["dialogue_sequences"]


def test_referenced_sequence_delete_is_blocked_and_context_is_visible(tmp_path: Path) -> None:
    session, selection = _session(
        tmp_path,
        "id: intro\nexploration:\n"
        "  dialog: [{sequence: first}]\n"
        "  dialogue_sequences: {first: [Hello]}\n"
        "  look_events: {inspect: {actions: [{type: dialog, dialog: first}]}}\n",
    )
    mapping = session.working_mapping(selection)
    assert dialogue_sequence_references(mapping, "first") == ("scene entry 1", "look event: inspect")
    with pytest.raises(ValueError, match="Cannot delete referenced"):
        session.apply_command(RemoveNamedDialogueSequenceCommand(selection, ("exploration", "dialogue_sequences"), "first"))


def test_sequence_rename_updates_known_local_references_atomically(tmp_path: Path) -> None:
    session, selection = _session(
        tmp_path,
        "id: intro\nexploration:\n"
        "  dialog: [{sequence: first}]\n"
        "  dialogue_sequences: {first: [Hello]}\n"
        "  look_events: {inspect: {actions: [{type: dialog, dialog: first}]}}\n",
    )
    session.apply_command(RenameNamedDialogueSequenceCommand(
        selection, ("exploration", "dialogue_sequences"), "first", "renamed",
    ))
    mapping = session.working_mapping(selection)
    assert "renamed" in mapping["exploration"]["dialogue_sequences"]
    assert mapping["exploration"]["dialog"][0]["sequence"] == "renamed"
    assert mapping["exploration"]["look_events"]["inspect"]["actions"][0]["dialog"] == "renamed"
    session.undo()
    assert "first" in session.working_mapping(selection)["exploration"]["dialogue_sequences"]


def test_action_commands_preserve_unknown_fields_and_order_through_history(tmp_path: Path) -> None:
    session, selection = _session(
        tmp_path,
        "id: intro\nexploration:\n  dialogue_sequences:\n    first:\n      text: Hello\n      actions:\n        - type: set_flag\n          flag: door_open\n          value: false\n          future: {keep: true}\n        - type: wait\n          seconds: 1\n",
    )
    actions_path = ("exploration", "dialogue_sequences", "first", "actions")
    session.apply_command(InsertDialogueActionCommand(
        selection, actions_path, {"type": "give_item", "item": "key", "quantity": 1},
    ))
    session.apply_command(SetDialogueActionParameterCommand(
        selection, actions_path + (0,), "value", True,
    ))
    values = session.working_mapping(selection)["exploration"]["dialogue_sequences"]["first"]["actions"]
    assert values[0]["future"] == {"keep": True}
    assert values[0]["value"] is True
    session.apply_command(MoveDialogueActionCommand(selection, actions_path, 2, 0))
    values = session.working_mapping(selection)["exploration"]["dialogue_sequences"]["first"]["actions"]
    assert values[0]["type"] == "give_item"
    session.undo()
    assert session.working_mapping(selection)["exploration"]["dialogue_sequences"]["first"]["actions"][0]["type"] == "set_flag"
    session.redo()
    session.apply_command(RemoveDialogueActionCommand(selection, actions_path, 1))
    session.undo()
    assert len(session.working_mapping(selection)["exploration"]["dialogue_sequences"]["first"]["actions"]) == 3


def test_action_metadata_discovers_supported_and_legacy_forms_without_rewriting(tmp_path: Path) -> None:
    session, selection = _session(
        tmp_path,
        "id: intro\nexploration:\n  dialogue_sequences:\n    first:\n      text: Hello\n      actions: [{set_flag: {door_open: true}}, {type: set_flag, flag: modern, value: true}]\n",
    )
    mapping = session.working_mapping(selection)
    document = DialogueDocumentModel("intro", mapping)
    entry = document.source("sequence:first").entries[0]  # type: ignore[union-attr]
    assert entry.actions[0].editable is False
    assert entry.actions[1].editable is True
    assert parse_action(entry.actions[0].raw, ActionScope.EXPLORATION).form is ActionForm.LEGACY
    assert len(present_dialogue_actions(entry.metadata.get("actions"), entry.selection.path + ("actions",))) == 2


def test_sequence_and_action_changes_save_and_reload(tmp_path: Path) -> None:
    session, selection = _session(tmp_path, "id: intro\nexploration:\n  dialogue_sequences: {}\n")
    sequence_path = ("exploration", "dialogue_sequences")
    add = InsertNamedDialogueSequenceCommand(selection, sequence_path, "persisted", {"text": "Hello"})
    session.apply_command(add)
    actions_path = sequence_path + ("persisted", "actions")
    session.apply_command(InsertDialogueActionCommand(
        selection, actions_path, {"type": "set_flag", "flag": "visited", "value": True},
    ))
    assert session.save_all()
    reloaded = ProjectSession.from_path(session.story_root, session.shared_assets_root)
    entry = reloaded.project.index.entry(ContentKind.SCENE, "intro")  # type: ignore[union-attr]
    assert entry is not None
    reloaded_selection = DefinitionSelection(ContentKind.SCENE, "intro", entry.source)
    sequence = reloaded.working_mapping(reloaded_selection)["exploration"]["dialogue_sequences"]["persisted"]
    assert sequence["text"] == "Hello"
    assert sequence["actions"][0]["flag"] == "visited"
