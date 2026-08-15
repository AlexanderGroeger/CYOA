import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.battle.defense import PATTERN_TYPES, defense_pattern_editor_specs
from engine.story_core import ContentKind
from story_core_fixture import write_fixture_story
from story_designer.models import (
    BattleElementSelection,
    DefensePatternEditorModel,
    DefinitionSelection,
    DuplicateDefensePatternCommand,
    InsertDefensePatternCommand,
    MoveDefensePatternCommand,
    ProjectSession,
    RemoveDefensePatternCommand,
    RemoveDefenseSequenceCommand,
    SetDefensePatternParameterCommand,
)


MODERN_BATTLE = """\
id: intro
enemy:
  name: Training Dummy
  hp: 10
enemy_patterns:
  - id: opening
    duration: 4
    patterns:
      - id: stream
        type: aimed_stream
        duration: 3
        fire_interval: 0.5
        projectile:
          speed: 100
          damage: 1
          future_projectile: preserve
      - id: burst
        type: radial_burst
        projectile_count: 8
        projectile: {speed: 60, damage: 2}
enemy_moves:
  - id: opening_move
    name: Opening
    pattern: opening
victory:
  rewards: {items: []}
"""


def _select_session(session: ProjectSession) -> tuple[ProjectSession, DefinitionSelection]:
    assert session.project is not None and session.project.index is not None
    entry = session.project.index.entry(ContentKind.BATTLE, "intro")
    assert entry is not None
    selection = DefinitionSelection(ContentKind.BATTLE, "intro", entry.source)
    assert session.select(selection) is not None
    return session, selection


def _session(tmp_path: Path) -> tuple[ProjectSession, DefinitionSelection]:
    story_root, shared_root = write_fixture_story(tmp_path)
    battle_path = story_root / "battles" / "intro.yaml"
    battle_path.write_text(MODERN_BATTLE, encoding="utf-8")
    return _select_session(ProjectSession.from_path(story_root, shared_root))


def test_every_registered_defense_type_has_metadata_or_explicit_fallback() -> None:
    specs = defense_pattern_editor_specs()
    assert set(PATTERN_TYPES).issubset(specs)
    assert all(specs[name].supported for name in PATTERN_TYPES)


def test_defense_model_discovers_sequences_patterns_and_unknown_payloads() -> None:
    mapping = {
        "defense_sequences": [{
            "id": "demo",
            "patterns": [
                {"id": "known", "type": "aimed_stream"},
                {"id": "future", "type": "future_pattern", "opaque": {"keep": True}},
                {"group": "pressure"},
            ],
            "pattern_groups": {"pressure": [{"type": "radial_burst"}]},
        }],
    }
    model = DefensePatternEditorModel("battle", mapping)
    assert model.sequences[0].sequence_id == "demo"
    assert model.sequences[0].patterns[0].display_name == "Aimed Stream"
    assert not model.sequences[0].patterns[1].supported
    assert not model.sequences[0].patterns[2].editable
    assert mapping["defense_sequences"][0]["patterns"][1]["opaque"] == {"keep": True}


def test_parameter_edit_preserves_unknown_fields_and_undo_redo(tmp_path: Path) -> None:
    session, selection = _session(tmp_path)
    path = ("enemy_patterns", 0, "patterns", 0)
    command = SetDefensePatternParameterCommand(selection, path, ("projectile", "speed"), 160)
    session.apply_command(command)
    working = session.working_mapping(selection)
    assert working["enemy_patterns"][0]["patterns"][0]["projectile"]["speed"] == 160
    assert working["enemy_patterns"][0]["patterns"][0]["projectile"]["future_projectile"] == "preserve"
    assert session.definition(selection).to_mapping()["enemy_patterns"][0]["patterns"][0]["projectile"]["speed"] == 100
    session.undo()
    assert session.working_mapping(selection)["enemy_patterns"][0]["patterns"][0]["projectile"]["speed"] == 100
    session.redo()
    assert session.working_mapping(selection)["enemy_patterns"][0]["patterns"][0]["projectile"]["speed"] == 160


def test_duplicate_reorder_and_referenced_delete_are_atomic(tmp_path: Path) -> None:
    session, selection = _session(tmp_path)
    sequence_path = ("enemy_patterns", 0)
    duplicate = DuplicateDefensePatternCommand(selection, sequence_path, 0)
    session.apply_command(duplicate)
    patterns = session.working_mapping(selection)["enemy_patterns"][0]["patterns"]
    assert patterns[1]["projectile"]["future_projectile"] == "preserve"
    patterns[1]["projectile"]["speed"] = 999
    assert patterns[0]["projectile"]["speed"] == 100
    session.undo()
    session.apply_command(MoveDefensePatternCommand(selection, sequence_path, 1, 0))
    assert [item["id"] for item in session.working_mapping(selection)["enemy_patterns"][0]["patterns"]] == ["burst", "stream"]
    session.undo()
    with pytest.raises(ValueError, match="referenced defense sequence"):
        session.apply_command(RemoveDefenseSequenceCommand(selection, ("enemy_patterns",), 0))


def test_invalid_defense_parameter_is_rejected_without_mutation(tmp_path: Path) -> None:
    session, selection = _session(tmp_path)
    command = SetDefensePatternParameterCommand(
        selection, ("enemy_patterns", 0, "patterns", 0), ("projectile", "speed"), -1,
    )
    with pytest.raises(ValueError, match="at least 0"):
        session.apply_command(command)
    assert session.working_mapping(selection)["enemy_patterns"][0]["patterns"][0]["projectile"]["speed"] == 100


def test_defense_save_reload_keeps_order_unknown_data_and_identity(tmp_path: Path) -> None:
    session, selection = _session(tmp_path)
    path = ("enemy_patterns", 0, "patterns", 0)
    session.apply_command(SetDefensePatternParameterCommand(selection, path, ("projectile", "damage"), 4))
    session.apply_command(MoveDefensePatternCommand(selection, ("enemy_patterns", 0), 0, 1))
    assert session.save_all()
    reloaded, reloaded_selection = _select_session(ProjectSession.from_path(
        tmp_path / "fixture_story", tmp_path / "shared_assets",
    ))
    mapping = reloaded.working_mapping(reloaded_selection)
    assert [item["id"] for item in mapping["enemy_patterns"][0]["patterns"]] == ["burst", "stream"]
    stream = mapping["enemy_patterns"][0]["patterns"][1]
    assert stream["projectile"]["damage"] == 4
    assert stream["projectile"]["future_projectile"] == "preserve"


def test_battle_editor_shows_dedicated_defense_widget_headlessly(tmp_path: Path) -> None:
    QApplication = pytest.importorskip("PySide6.QtWidgets").QApplication
    from story_designer.widgets import BattleEditorWidget

    app = QApplication.instance() or QApplication([])
    session, selection = _session(tmp_path)
    widget = BattleEditorWidget(session)
    widget.set_state(session.project, selection, session.definition(selection), session.diagnostics)
    widget.sections.setCurrentRow(next(index for index in range(widget.sections.count()) if widget.sections.item(index).data(32) == "defense"))
    assert not widget.defense_editor.isHidden()
    assert widget.defense_editor.sequence_list.count() == 1
    assert widget.defense_editor.pattern_list.count() == 2
    assert app is not None
