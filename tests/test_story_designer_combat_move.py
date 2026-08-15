from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.battle.move_progression import resolve_combat_move
from engine.battle.qte import QTE_EDITOR_SPECS, QTE_REGISTRY
from engine.story_core import ContentKind, load_story_project
from story_core_fixture import write_fixture_story
from story_designer.models import (
    AddDifficultyLevelCommand,
    CombatMoveDocumentModel,
    DefinitionSelection,
    DeleteDifficultyLevelCommand,
    DuplicateDifficultyLevelCommand,
    ProjectSession,
    ReplaceQTETypeCommand,
    SetCombatMoveFieldCommand,
    SetPropertyCommand,
    SetSourcePropertyCommand,
)


def _modern_session(tmp_path: Path) -> tuple[ProjectSession, DefinitionSelection]:
    story_root, shared_root = write_fixture_story(tmp_path)
    (story_root / "moves" / "moves.yaml").write_text(
        "skill_progression: {evaluation_attempts: 2, promotion_average: 2.5, demotion_average: 1.5, minimum_level: 1}\n"
        "moves:\n"
        "  - id: intro\n"
        "    name: Intro Strike\n"
        "    common:\n"
        "      base_power: 4\n"
        "      qte: {type: precision_bar, label: Strike, pattern_parameters: {target_position: 0.5}, future_qte: {keep: true}}\n"
        "    difficulty_levels:\n"
        "      0: {qte: {duration: 2.0, tuning_parameters: {critical_window: 0.04}}}\n"
        "      1: {qte: {duration: 1.5, tuning_parameters: {critical_window: 0.03}}}\n"
        "      2: {qte: {duration: 1.2, tuning_parameters: {critical_window: 0.02}}}\n"
        "    future_move_extension: {preserves: true}\n",
        encoding="utf-8",
    )
    (story_root / "battles" / "intro.yaml").write_text(
        "id: intro\nenemy: {name: Dummy, hp: 1, attack: 0, defense: 0, moves: [{name: Bump, damage: [0, 0], weight: 1}]}\n",
        encoding="utf-8",
    )
    session = ProjectSession.from_path(story_root, shared_root)
    assert session.project is not None and session.project.index is not None
    entry = session.project.index.entry(ContentKind.MOVE, "intro")
    assert entry is not None
    return session, DefinitionSelection(ContentKind.MOVE, "intro", entry.source)


def test_every_registered_qte_has_editor_metadata() -> None:
    assert set(QTE_REGISTRY) == set(QTE_EDITOR_SPECS)
    assert all(spec.supported and spec.fields for spec in QTE_EDITOR_SPECS.values())


def test_move_model_distinguishes_authored_and_effective_levels(tmp_path: Path) -> None:
    session, selection = _modern_session(tmp_path)
    mapping = session.working_mapping(selection)
    model = CombatMoveDocumentModel("intro", mapping, session.project, session.diagnostics)  # type: ignore[arg-type]
    field = next(field for field in model.qte_fields(2) if field.key == "critical_window")
    assert field.is_authored
    assert field.effective_value == 0.02
    inherited = next(field for field in model.qte_fields(2) if field.key == "target_position")
    assert not inherited.is_authored
    assert "target_position" not in model.authored_level(2)["qte"].get("tuning_parameters", {})
    assert model.effective_level(2) == resolve_combat_move(mapping, 2)  # type: ignore[arg-type]


def test_difficulty_commands_are_atomic_and_copy_authored_payload(tmp_path: Path) -> None:
    session, selection = _modern_session(tmp_path)
    session.apply_command(AddDifficultyLevelCommand(selection))
    assert 3 in session.working_mapping(selection)["difficulty_levels"]
    session.undo()
    assert 3 not in session.working_mapping(selection)["difficulty_levels"]
    session.redo()
    assert session.working_mapping(selection)["difficulty_levels"][3] == {}
    session.apply_command(DuplicateDifficultyLevelCommand(selection, 2))
    assert session.working_mapping(selection)["difficulty_levels"][4] == session.working_mapping(selection)["difficulty_levels"][2]
    session.apply_command(DeleteDifficultyLevelCommand(selection, 0))
    assert 0 not in session.working_mapping(selection)["difficulty_levels"]
    session.undo()
    assert session.working_mapping(selection)["difficulty_levels"][0]["qte"]["duration"] == 2.0


def test_qte_replacement_and_parameter_edit_preserve_unknown_fields(tmp_path: Path) -> None:
    session, selection = _modern_session(tmp_path)
    model = CombatMoveDocumentModel("intro", session.working_mapping(selection), session.project, session.diagnostics)  # type: ignore[arg-type]
    field = next(field for field in model.qte_fields(1) if field.key == "critical_window")
    session.apply_command(SetCombatMoveFieldCommand(selection, field.path, 0.06))
    assert session.working_mapping(selection)["difficulty_levels"][1]["qte"]["tuning_parameters"]["critical_window"] == 0.06
    assert session.working_mapping(selection)["common"]["qte"]["future_qte"] == {"keep": True}
    before = session.working_mapping(selection)
    session.apply_command(ReplaceQTETypeCommand(selection, "stability"))
    assert session.working_mapping(selection)["common"]["qte"] == {"type": "stability"}
    session.undo()
    assert session.working_mapping(selection) == before


def test_file_level_progression_and_move_edit_save_without_shape_rewrite(tmp_path: Path) -> None:
    session, selection = _modern_session(tmp_path)
    source = selection.source.relative_to(session.project.story_root).as_posix()  # type: ignore[union-attr]
    session.apply_command(SetSourcePropertyCommand(selection, source, ("skill_progression", "evaluation_attempts"), 4))
    session.apply_command(SetCombatMoveFieldCommand(selection, ("common", "qte", "pattern_parameters", "target_position"), 0.6))
    assert session.project.move("intro").to_mapping()["common"]["qte"]["pattern_parameters"]["target_position"] == 0.5
    assert session.semantic_documents()[source]["moves"][0]["future_move_extension"] == {"preserves": True}
    assert session.save_all()
    reloaded = ProjectSession.from_path(session.story_root, session.shared_assets_root)
    assert reloaded.project is not None
    assert reloaded.project.move_skill_progression["evaluation_attempts"] == 4
    assert reloaded.project.move("intro").to_mapping()["common"]["qte"]["pattern_parameters"]["target_position"] == 0.6
    raw = (reloaded.story_root / source).read_text(encoding="utf-8")
    assert "moves:" in raw


def test_combat_move_workspace_opens_for_move_selection(tmp_path: Path) -> None:
    QApplication = pytest.importorskip("PySide6.QtWidgets").QApplication
    from story_designer.widgets import WorkspaceWidget

    session, selection = _modern_session(tmp_path)
    session.select(selection)
    app = QApplication.instance() or QApplication([])
    workspace = WorkspaceWidget(session)
    workspace.set_state(session.project, selection, session.definition(selection), session.diagnostics)
    assert workspace.tabs.currentWidget() is workspace.combat_move_editor
    assert workspace.combat_move_editor.model is not None
    assert workspace.combat_move_editor.qte_type_combo.count() == len(QTE_REGISTRY)
    assert app is not None


@pytest.mark.parametrize(
    "root, expected_shape",
    [
        ("id: intro\nname: Direct\nqte: {type: precision_bar}\n", "mapping"),
        ("- id: intro\n  name: List Entry\n  qte: {type: precision_bar}\n", "list"),
        ("moves:\n  - id: intro\n    name: Wrapped\n    qte: {type: precision_bar}\n", "moves"),
    ],
)
def test_move_edit_save_preserves_source_root_shape(tmp_path: Path, root: str, expected_shape: str) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    (story_root / "moves" / "moves.yaml").write_text(root, encoding="utf-8")
    session = ProjectSession.from_path(story_root, shared_root)
    assert session.project is not None and session.project.index is not None
    entry = session.project.index.entry(ContentKind.MOVE, "intro")
    assert entry is not None
    selection = DefinitionSelection(ContentKind.MOVE, "intro", entry.source)
    session.apply_command(SetPropertyCommand(selection, ("name",), "Edited"))
    assert session.save_all()
    document = session.project.source_documents["moves/moves.yaml"]
    if expected_shape == "mapping":
        assert isinstance(document, Mapping) and document.get("id") == "intro" and "moves" not in document
    elif expected_shape == "list":
        assert isinstance(document, Sequence) and not isinstance(document, (str, bytes, Mapping)) and document[0]["name"] == "Edited"
    else:
        assert isinstance(document, Mapping) and isinstance(document.get("moves"), Sequence) and document["moves"][0]["name"] == "Edited"
