from __future__ import annotations

import os
from pathlib import Path

import pytest

from engine.story_core import ContentKind
from story_core_fixture import write_fixture_story
from story_designer.models import (
    DefinitionSelection,
    DialogueDocumentModel,
    DuplicateDialogueEntryCommand,
    InsertDialogueEntryCommand,
    MoveDialogueEntryCommand,
    ProjectSession,
    RemoveDialogueEntryCommand,
    SetDialogueTextCommand,
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication
    from story_designer.widgets import DialogueEditorWidget
except ImportError:  # pragma: no cover
    QApplication = None  # type: ignore[assignment]


@pytest.fixture(scope="module")
def qapp():
    if QApplication is None:
        pytest.skip("PySide6 is not installed")
    return QApplication.instance() or QApplication([])


def _session(tmp_path: Path, scene_text: str) -> tuple[ProjectSession, DefinitionSelection]:
    story_root, shared_root = write_fixture_story(tmp_path)
    (story_root / "scenes" / "intro.yaml").write_text(scene_text, encoding="utf-8")
    session = ProjectSession.from_path(story_root, shared_root)
    entry = session.project.index.entry(ContentKind.SCENE, "intro")  # type: ignore[union-attr]
    assert entry is not None
    selection = DefinitionSelection(ContentKind.SCENE, "intro", entry.source)
    session.select(selection)
    return session, selection


def test_dialogue_discovery_preserves_scene_entry_and_named_sequence_shapes(tmp_path: Path) -> None:
    session, selection = _session(
        tmp_path,
        "id: intro\n"
        "exploration:\n"
        "  dialog:\n"
        "    - conditions: {flag: visited}\n"
        "      sequence: first_visit\n"
        "  dialogue_sequences:\n"
        "    first_visit:\n"
        "      - Hello\n"
        "      - Welcome\n"
        "    annotated:\n"
        "      text: Annotated line\n"
        "      actions: [{type: set_flag, flag: seen}]\n"
        "  look_events:\n"
        "    inspect:\n"
        "      actions: [{type: dialog, dialog: first_visit}]\n",
    )
    document = DialogueDocumentModel("intro", session.working_mapping(selection) or {})
    assert [source.id for source in document.sources] == [
        "scene_entry", "sequence:first_visit", "sequence:annotated",
    ]
    scene_entry = document.source("scene_entry")
    first_visit = document.source("sequence:first_visit")
    annotated = document.source("sequence:annotated")
    assert scene_entry is not None and scene_entry.entries[0].text is None
    assert first_visit is not None and [entry.text for entry in first_visit.entries] == ["Hello", "Welcome"]
    assert annotated is not None and annotated.entries[0].text == "Annotated line"
    assert "look event: inspect" in first_visit.referenced_by


def test_dialogue_text_edit_is_one_history_step_and_keeps_core_immutable(tmp_path: Path) -> None:
    session, selection = _session(
        tmp_path,
        "id: intro\nexploration:\n  dialogue_sequences:\n    first: [Original]\n",
    )
    original = session.definition(selection).to_mapping()
    path = ("exploration", "dialogue_sequences", "first", 0)
    session.apply_command(SetDialogueTextCommand(selection, path, "Edited"))
    assert session.working_mapping(selection)["exploration"]["dialogue_sequences"]["first"] == ["Edited"]
    assert session.definition(selection).to_mapping() == original
    assert len(session._history) == 1
    session.undo()
    assert session.working_mapping(selection)["exploration"]["dialogue_sequences"]["first"] == ["Original"]
    session.redo()
    assert session.working_mapping(selection)["exploration"]["dialogue_sequences"]["first"] == ["Edited"]


def test_dialogue_structural_commands_deep_copy_unknown_fields_and_reorder(tmp_path: Path) -> None:
    session, selection = _session(
        tmp_path,
        "id: intro\nexploration:\n  dialogue_sequences:\n    first:\n"
        "      - text: One\n        future: {keep: true}\n"
        "      - text: Two\n        actions: [{type: sound, file: cue.wav}]\n",
    )
    path = ("exploration", "dialogue_sequences", "first")
    session.apply_command(DuplicateDialogueEntryCommand(selection, path, 0))
    values = session.working_mapping(selection)["exploration"]["dialogue_sequences"]["first"]
    assert values[1] == values[0]
    assert values[1] is not values[0]
    session.apply_command(MoveDialogueEntryCommand(selection, path, 2, 0))
    assert [value["text"] for value in session.working_mapping(selection)["exploration"]["dialogue_sequences"]["first"]] == ["Two", "One", "One"]
    session.undo()
    session.apply_command(RemoveDialogueEntryCommand(selection, path, 1))
    assert session.working_mapping(selection)["exploration"]["dialogue_sequences"]["first"][0]["future"] == {"keep": True}
    session.undo()
    assert len(session.working_mapping(selection)["exploration"]["dialogue_sequences"]["first"]) == 3


def test_empty_scene_dialogue_can_be_added_and_saved_reloaded(tmp_path: Path) -> None:
    session, selection = _session(tmp_path, "id: intro\n")
    session.apply_command(InsertDialogueEntryCommand(selection, ("exploration", "dialog"), {"text": "New line"}))
    assert session.is_dirty
    assert session.save_all()
    reloaded = ProjectSession.from_path(session.story_root, session.shared_assets_root)
    entry = reloaded.project.index.entry(ContentKind.SCENE, "intro")  # type: ignore[union-attr]
    assert entry is not None
    reloaded_selection = DefinitionSelection("scene", "intro", entry.source)
    assert reloaded.working_mapping(reloaded_selection)["exploration"]["dialog"] == [{"text": "New line"}]


@pytest.mark.skipif(QApplication is None, reason="PySide6 is not installed")
def test_dialogue_widget_adds_empty_scene_entry_and_commits_text(qapp, tmp_path: Path) -> None:
    session, selection = _session(tmp_path, "id: intro\n")
    editor = DialogueEditorWidget(session)
    editor.set_scene(session.project, "intro", session.working_mapping(selection))  # type: ignore[arg-type]
    assert editor.add_dialogue()
    selected = editor.selected_entry
    assert selected is not None
    text_editor = editor._editors[selected]
    text_editor.setPlainText("Author this line")
    editor._commit_text(selected, text_editor)
    assert session.working_mapping(selection)["exploration"]["dialog"] == [{"text": "Author this line"}]
    assert len(session._history) == 2
