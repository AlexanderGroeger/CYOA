from __future__ import annotations

import os
from pathlib import Path

import pytest

from engine.core.condition_eval import evaluate_condition as evaluate_legacy
from engine.story_core import ContentKind
from engine.story_core.conditions import evaluate_structured_condition, parse_condition
from engine.story_core.schema import MISSING
from story_core_fixture import write_fixture_story
from story_designer.models import (
    ConditionEditorModel,
    ConditionTreeModel,
    DefinitionSelection,
    ProjectSession,
    SetNavigationConditionCommand,
    SetSceneElementConditionCommand,
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication
    from story_designer.widgets import ConditionEditorWidget
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


def test_story_core_structured_vocabulary_and_compatibility_semantics() -> None:
    value = {
        "all": [
            {"flag": "door_open", "equals": True},
            {"any": [{"has_item": "rusty_key"}, {"variable": "trust", "not_equals": 3}]},
            {"not": {"variable": "blocked", "exists": True}},
        ]
    }
    parsed = parse_condition(value)
    assert parsed.dialect.value == "structured"
    assert parsed.symbols.flags == {"door_open"}
    assert parsed.symbols.variables == {"trust", "blocked"}
    assert parsed.symbols.items == {"rusty_key"}
    assert evaluate_structured_condition({"all": []}, {}) is True
    assert evaluate_structured_condition({"any": []}, {}) is False
    assert evaluate_legacy("flags.open", {}) is False


def test_condition_model_round_trips_nested_data_and_edits_atomically() -> None:
    original = {
        "all": [
            {"flag": "door_open", "equals": True},
            {"any": [{"has_item": "rusty_key"}, {"variable": "trust", "not_equals": 3}]},
        ]
    }
    model = ConditionTreeModel.from_value(original)
    assert model.supported
    assert model.value() == original

    model.move_child((), 1, -1)
    assert model.value()["all"][0]["any"]
    model.change_type((0,), "has_item")
    model.set_leaf_name((0,), "rusty_key")
    assert model.value()["all"][0] == {"has_item": "rusty_key"}
    model.remove_child((1,))
    assert model.value() == {"all": [{"has_item": "rusty_key"}]}


def test_condition_model_preserves_unknown_structure_and_aliases() -> None:
    unsupported = {"future_operator": {"name": "keep"}, "future_value": [1, 2]}
    model = ConditionEditorModel.from_value(unsupported)
    assert not model.supported
    assert model.value() == unsupported

    alias = ConditionTreeModel.from_value({"var": "trust", "exists": True})
    assert alias.supported
    assert alias.value() == {"var": "trust", "exists": True}


def test_condition_widget_distinguishes_absence_and_adds_structured_value(qapp) -> None:
    widget = ConditionEditorWidget()
    emitted: list[object] = []
    widget.condition_changed.connect(emitted.append)
    widget.set_condition(MISSING)
    assert widget.condition_mode.currentData() == "absent"
    assert widget.model is None
    assert emitted == []

    widget.condition_mode.setCurrentIndex(widget.condition_mode.findData("structured"))
    widget.new_type.setCurrentIndex(widget.new_type.findData("flag"))
    widget.add_condition_button.click()
    assert emitted[-1] == {"flag": "flag_name"}
    assert widget.model is not None and widget.model.supported


def test_navigation_structured_condition_uses_shared_semantics_and_alias_on_save(tmp_path: Path) -> None:
    session, selection = _session(
        tmp_path,
        "id: intro\n"
        "exploration:\n"
        "  navigation:\n"
        "    - scene: ending\n"
        "      condition:\n"
        "        all:\n"
        "          - flag: visited\n"
        "            equals: true\n",
    )
    from story_designer.widgets import NavigationPanel

    panel = NavigationPanel(session)
    panel.set_scene(session.project, "intro", session.working_mapping(selection))  # type: ignore[arg-type]
    assert panel.condition_mode.currentData() == "structured"
    assert panel.condition_editor.model is not None
    assert panel.condition_editor.model.value() == {"all": [{"flag": "visited", "equals": True}]}
    panel.condition_editor.model.set_leaf_name((0,), "changed")
    panel.condition_editor._render_builder()
    panel.condition_editor._emit_model()
    assert session.working_mapping(selection)["exploration"]["navigation"][0]["condition"]["all"][0]["flag"] == "changed"
    assert "conditions" not in session.working_mapping(selection)["exploration"]["navigation"][0]
    session.save_all()
    reloaded = ProjectSession.from_path(session.story_root, session.shared_assets_root)
    reloaded_entry = reloaded.project.index.entry(ContentKind.SCENE, "intro")  # type: ignore[union-attr]
    assert reloaded_entry is not None
    reloaded_selection = DefinitionSelection(ContentKind.SCENE, "intro", reloaded_entry.source)
    assert reloaded.working_mapping(reloaded_selection)["exploration"]["navigation"][0]["condition"] == {
        "all": [{"flag": "changed", "equals": True}]
    }


def test_scene_object_condition_command_preserves_visible_when_alias_and_undo(tmp_path: Path) -> None:
    session, selection = _session(
        tmp_path,
        "id: intro\n"
        "exploration:\n"
        "  objects:\n"
        "    - id: torch\n"
        "      position: [1, 2]\n"
        "      visible_when: {flag: visited}\n",
    )
    element = type("Element", (), {"kind": "object", "id": "torch"})()
    session.apply_command(SetSceneElementConditionCommand(selection, element, {"has_item": "intro"}))
    mapping = session.working_mapping(selection)
    assert mapping["exploration"]["objects"][0]["visible_when"] == {"has_item": "intro"}
    session.undo()
    assert session.working_mapping(selection)["exploration"]["objects"][0]["visible_when"] == {"flag": "visited"}


def test_scene_element_inspector_uses_condition_builder(qapp, tmp_path: Path) -> None:
    session, selection = _session(
        tmp_path,
        "id: intro\n"
        "exploration:\n"
        "  look_regions:\n"
        "    - id: desk\n"
        "      rect: [1, 2, 30, 20]\n"
        "      conditions: {flag: visited}\n",
    )
    from story_designer.models import SceneElementSelection
    from story_designer.widgets import InspectorWidget

    inspector = InspectorWidget(session)
    inspector.set_selection(session.project, selection, session.definition(selection), session.diagnostics)  # type: ignore[arg-type]
    element = SceneElementSelection("intro", "look_region", "desk")
    inspector.set_scene_element(element, {"id": "desk", "rect": [1, 2, 30, 20], "conditions": {"flag": "visited"}})
    assert not inspector.scene_condition_box.isHidden()
    builder = inspector.scene_condition_editor
    assert builder.model is not None and builder.model.value() == {"flag": "visited"}
    builder.model.set_leaf_name((), "changed")
    builder._render_builder()
    builder._emit_model()
    assert session.working_mapping(selection)["exploration"]["look_regions"][0]["conditions"] == {"flag": "changed"}


def test_string_condition_stays_string_when_structured_builder_opens(tmp_path: Path) -> None:
    session, selection = _session(
        tmp_path,
        "id: intro\n"
        "exploration:\n"
        "  navigation:\n"
        "    - scene: ending\n"
        "      conditions: \"flags.visited and has_item('intro')\"\n",
    )
    from story_designer.widgets import NavigationPanel

    panel = NavigationPanel(session)
    panel.set_scene(session.project, "intro", session.working_mapping(selection))  # type: ignore[arg-type]
    assert panel.condition_mode.currentData() == "string"
    assert panel.condition_text.text() == "flags.visited and has_item('intro')"
    assert panel.condition_editor.model is None
