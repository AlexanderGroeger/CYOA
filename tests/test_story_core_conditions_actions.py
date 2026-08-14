from __future__ import annotations

import pytest

from engine.story_core.actions import (
    ActionError,
    ActionForm,
    ActionScope,
    action_references,
    parse_action,
    parse_actions,
)
from engine.story_core.conditions import (
    ConditionDialect,
    ConditionError,
    evaluate_condition,
    parse_condition,
)


@pytest.fixture
def headless_state() -> dict[str, object]:
    return {
        "flags": {"gate_open": True, "locked": False},
        "variables": {"gold": 10, "chapter": 2},
        "inventory": {"key": 2, "torch": 1},
    }


def test_condition_envelopes_preserve_legacy_and_structured_dialects(
    headless_state: dict[str, object],
) -> None:
    legacy = parse_condition("flags.gate_open and vars.gold >= 10 and has_item('key', 2)")
    structured = parse_condition(
        {
            "all": [
                {"flag": "gate_open"},
                {"variable": "gold", "equals": 10},
                {"has_item": "torch"},
            ]
        }
    )

    assert legacy.dialect is ConditionDialect.LEGACY
    assert legacy.symbols.flags == frozenset({"gate_open"})
    assert legacy.symbols.variables == frozenset({"gold"})
    assert legacy.symbols.items == frozenset({"key"})
    assert legacy.evaluate(headless_state) is True

    assert structured.dialect is ConditionDialect.STRUCTURED
    assert structured.symbols.flags == frozenset({"gate_open"})
    assert structured.symbols.variables == frozenset({"gold"})
    assert structured.symbols.items == frozenset({"torch"})
    assert structured.evaluate(headless_state) is True


def test_structured_condition_empty_and_leaf_operator_semantics(
    headless_state: dict[str, object],
) -> None:
    assert evaluate_condition(None, headless_state) is True
    assert evaluate_condition({}, headless_state) is True
    assert evaluate_condition({"all": []}, headless_state) is True
    assert evaluate_condition({"any": []}, headless_state) is False

    # The existing structured evaluator gives equals precedence over the
    # other leaf operators when more than one is authored.
    assert evaluate_condition(
        {"variable": "gold", "equals": 10, "not_equals": 5, "exists": False},
        headless_state,
    ) is True
    assert evaluate_condition({"flag": "missing", "exists": False}, headless_state) is True
    assert evaluate_condition({"not": {"flag": "gate_open"}}, headless_state) is False

    # Plain mapping state is a headless adapter for GameState, so missing
    # variables and non-boolean stored flags retain GameState semantics.
    assert evaluate_condition({"variable": "missing", "equals": 0}, {"variables": {}}) is True
    assert evaluate_condition("vars.missing == null", {"variables": {}}, dialect=ConditionDialect.LEGACY) is False
    assert evaluate_condition("flags.enabled == true", {"flags": {"enabled": 2}}, dialect=ConditionDialect.LEGACY) is True
    assert parse_condition({}, dialect=ConditionDialect.LEGACY).evaluate(headless_state) is True


def test_legacy_condition_rejects_unsafe_names_and_invalid_expressions(
    headless_state: dict[str, object],
) -> None:
    with pytest.raises(ConditionError):
        parse_condition("flags._private", dialect=ConditionDialect.LEGACY)
    with pytest.raises(ConditionError):
        evaluate_condition("__import__('os')", headless_state, dialect=ConditionDialect.LEGACY)
    with pytest.raises(ConditionError):
        evaluate_condition("flags.gate_open ==", headless_state, dialect=ConditionDialect.LEGACY)


def test_action_adapters_keep_legacy_and_typed_forms_and_scope_separate() -> None:
    legacy = parse_action({"add_item": "torch"}, ActionScope.STORY)
    typed = parse_action(
        {"type": "give_item", "item": "key", "quantity": 2},
        ActionScope.EXPLORATION,
    )
    battle = parse_action(
        {"set_fight_flag": {"enemy_enraged": True}},
        ActionScope.BATTLE,
    )

    assert legacy.form is ActionForm.LEGACY
    assert legacy.scope is ActionScope.STORY
    assert legacy.to_mapping() == {"add_item": "torch"}
    assert action_references(legacy).items == frozenset({"torch"})

    assert typed.form is ActionForm.TYPED
    assert typed.scope is ActionScope.EXPLORATION
    assert typed.payload == {"item": "key", "quantity": 2}
    assert action_references(typed).items == frozenset({"key"})

    # Fight flags are battle-local and must not be presented as persistent
    # story flag references to a future editor.
    references = action_references(battle)
    assert references.fight_flags == frozenset({"enemy_enraged"})
    assert references.flags == frozenset()


def test_action_list_adapter_preserves_order_and_rejects_ambiguous_legacy_forms() -> None:
    actions = parse_actions(
        [{"set_flag": {"visited": True}}, {"add_item": "key"}],
        ActionScope.STORY,
    )
    assert [action.action_type for action in actions] == ["set_flag", "add_item"]

    with pytest.raises(ActionError):
        parse_action({"set_flag": "a", "clear_flag": "b"}, ActionScope.STORY)
    with pytest.raises(ActionError):
        parse_action({"type": "give_item", "item": "key"}, ActionScope.STORY)


def test_story_action_adapter_rejects_payloads_the_legacy_runtime_cannot_execute() -> None:
    for action in (
        {"set_flag": "visited"},
        {"set_variable": []},
        {"add_variable": None},
        {"equip_item": "key"},
        {"equip_item": {"weapon": 3}},
        {"add_item": {"not": "an item id"}},
        {"play_sfx": None},
    ):
        with pytest.raises(ActionError):
            parse_action(action, ActionScope.STORY)


def test_action_and_condition_envelopes_are_immutable_and_accept_frozen_authored_data() -> None:
    raw_condition = {"flag": "gate_open"}
    condition = parse_condition(raw_condition)
    raw_condition["flag"] = "changed"
    assert condition.raw["flag"] == "gate_open"
    assert condition.to_value() == {"flag": "gate_open"}

    raw_action = {"set_flag": {"visited": True}}
    action = parse_action(raw_action, ActionScope.STORY)
    raw_action["set_flag"]["visited"] = False
    assert action.to_mapping() == {"set_flag": {"visited": True}}

    from engine.story_core.models import SceneDefinition

    scene = SceneDefinition.from_mapping({"actions": [{"set_flag": {"visited": True}}]}, "scene.yaml")
    frozen_action = parse_action(scene.authored["actions"][0], ActionScope.STORY)
    assert frozen_action.to_mapping() == {"set_flag": {"visited": True}}


def test_exploration_aliases_remain_form_specific() -> None:
    typed = parse_action({"type": "add_item", "item": "key"}, ActionScope.EXPLORATION)
    legacy = parse_action({"play_sound": "click.wav"}, ActionScope.EXPLORATION)

    # Runtime aliases typed play_* forms, but typed add_item and legacy
    # play_sound are not aliases and remain visibly invalid/unknown to the
    # specialized exploration validator.
    assert typed.action_type == "add_item"
    assert legacy.action_type == "play_sound"
