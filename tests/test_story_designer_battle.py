from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.story_core import ContentKind
from story_core_fixture import write_fixture_story
from story_designer.models import (
    BattleDocumentModel,
    BattleElementSelection,
    DefinitionSelection,
    ProjectSession,
    SetPropertyCommand,
)


def _selection(session: ProjectSession) -> DefinitionSelection:
    assert session.project is not None and session.project.index is not None
    entry = session.project.index.entry(ContentKind.BATTLE, "intro")
    assert entry is not None
    selection = DefinitionSelection(ContentKind.BATTLE, "intro", entry.source)
    assert session.select(selection) is not None
    return selection


def test_battle_model_discovers_modern_sections_and_keeps_authored_paths() -> None:
    mapping = {
        "id": "boss_1",
        "enemy": {"name": "Warden", "hp": 80, "future_enemy": {"keep": True}},
        "arena": {"width": 220, "height": 110},
        "initial_player_moves": ["jab", "missing_move"],
        "initial_enemy_moves": ["slash"],
        "enemy_patterns": [{"id": "wall", "patterns": [{"type": "future_pattern"}]}],
        "enemy_moves": [{"id": "slash", "pattern": "wall", "future_move": 3}],
        "phases": [{"id": "final", "when": {"turn": 4}, "actions": [{"future": True}]}],
        "dialogue": [{"trigger": "battle_start", "text": "Begin.", "future": "preserve"}],
        "victory": {"rewards": {"items": ["missing_item"], "future_reward": True}},
        "escape": {"enabled": True, "chance": 0.5},
        "on_lose": {"type": "game_over", "future_lose": True},
        "future_battle_extension": {"preserves": True},
    }
    model = BattleDocumentModel("boss_1", mapping)

    assert [section.id for section in model.sections] == [
        "overview", "enemy", "player_moves", "enemy_moves", "defense",
        "phases", "dialogue", "rewards", "escape", "lose",
    ]
    assert model.section("defense").path == ("enemy_patterns",)  # type: ignore[union-attr]
    assert model.section("enemy_moves").elements[0].selection.identifier == "slash"  # type: ignore[union-attr]
    assert "Phases: 1" in model.overview().summary
    assert "missing_move" in model.section("player_moves").elements[1].summary  # type: ignore[union-attr]
    assert mapping["future_battle_extension"] == {"preserves": True}


def test_legacy_battle_model_uses_enemy_moves_without_rewriting_shape(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    session = ProjectSession.from_path(story_root, shared_root)
    selection = _selection(session)
    original = session.definition(selection).to_mapping()
    model = BattleDocumentModel("intro", session.working_mapping(selection) or {})

    assert model.section("enemy_moves") is not None
    assert model.section("enemy_moves").path == ("enemy", "moves")  # type: ignore[union-attr]
    session.apply_command(SetPropertyCommand(selection, ("enemy", "hp"), 7))
    working = session.working_mapping(selection)
    assert working["enemy"]["hp"] == 7
    assert "moves" in working["enemy"]
    assert working["future_battle_extension"] == original["future_battle_extension"]
    assert session.definition(selection).to_mapping() == original
    assert session.dirty
    session.undo()
    assert session.working_mapping(selection)["enemy"]["hp"] == original["enemy"]["hp"]
    session.redo()
    assert session.working_mapping(selection)["enemy"]["hp"] == 7


def test_battle_safe_edit_saves_and_reloads_without_losing_unknown_fields(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    session = ProjectSession.from_path(story_root, shared_root)
    selection = _selection(session)
    session.apply_command(SetPropertyCommand(selection, ("enemy", "name"), "Edited Dummy"))
    assert session.save_all()

    reloaded = ProjectSession.from_path(story_root, shared_root)
    reloaded_selection = _selection(reloaded)
    mapping = reloaded.working_mapping(reloaded_selection)
    assert mapping["enemy"]["name"] == "Edited Dummy"
    assert mapping["future_battle_extension"] == {"preserves": True}


def test_battle_workspace_opens_for_battle_selection(tmp_path: Path) -> None:
    QApplication = pytest.importorskip("PySide6.QtWidgets").QApplication
    from story_designer.widgets import WorkspaceWidget

    app = QApplication.instance() or QApplication([])
    story_root, shared_root = write_fixture_story(tmp_path)
    session = ProjectSession.from_path(story_root, shared_root)
    selection = _selection(session)
    workspace = WorkspaceWidget(session)
    workspace.set_state(session.project, selection, session.definition(selection), session.diagnostics)

    assert workspace.tabs.currentWidget() is workspace.battle_editor
    assert workspace.battle_editor.model is not None
    assert workspace.battle_editor.current_section.id == "overview"
    assert app is not None
