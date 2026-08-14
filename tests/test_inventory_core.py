"""Headless coverage for exploration-facing inventory rules."""

from __future__ import annotations

import pytest

from engine.core.game_state import GameState
from engine.core.inventory import (
    InventoryActionError,
    InventoryGrid,
    InventoryLayout,
    InventorySchemaError,
    InventoryService,
    effective_stats,
    normalize_item_definition,
)


ITEMS = {
    "iron_sword": {
        "name": "Iron Sword",
        "type": "weapon",
        "description": "A dependable sword.",
        "stats": {"hp": 0, "attack": 5, "defense": 1},
        "equipment_slot": "weapon",
        "actions": ["equip", "toss"],
    },
    "oak_staff": {
        "name": "Oak Staff",
        "type": "weapon",
        "stats": {"attack": 2},
        "equipment_slot": "weapon",
        "actions": ["equip", "toss"],
    },
    "apple": {
        "name": "Apple",
        "type": "consumable",
        "description": "Restores health.",
        "actions": ["use", "toss"],
        "use": {"actions": [{"type": "heal", "amount": 8}, {"type": "set_flag", "flag": "ate_apple"}]},
    },
    "silver_key": {
        "name": "Silver Key",
        "type": "key",
        "description": "Not disposable.",
        "actions": [],
    },
}


def test_new_item_schema_is_normalized_without_forcing_an_icon():
    item = normalize_item_definition("iron_sword", ITEMS["iron_sword"])

    assert item.name == "Iron Sword"
    assert item.icon is None  # legacy/no-icon content gets a renderer fallback
    assert dict(item.stats) == {"hp": 0, "attack": 5, "defense": 1}
    assert item.equipment_slot == "weapon"
    assert item.actions == ("equip", "toss")


@pytest.mark.parametrize(
    "item",
    [
        {"actions": ["teleport"]},
        {"actions": ["equip"]},
        {"type": "weapon", "actions": ["equip"]},
        {"actions": ["toss", "toss"]},
        {"icon": ""},
        {"actions": ["use"], "use": {"actions": [{"type": "heal", "amount": -1}]}},
    ],
)
def test_invalid_new_item_fields_fail_with_a_content_error(item):
    with pytest.raises(InventorySchemaError):
        normalize_item_definition("bad_item", item)


def test_legacy_equipment_and_combat_item_shape_remain_usable():
    legacy_weapon = {
        "name": "Legacy Wand",
        "type": "weapon",
        "equipment": {"bonuses": {"max_hp": 4, "attack": 2}},
    }
    legacy_ration = {
        "name": "Legacy Ration",
        "type": "consumable",
        "combat": {"usable": True, "effects": [{"heal": 7}]},
    }
    service = InventoryService({"wand": legacy_weapon, "ration": legacy_ration})

    wand = service.definition("wand")
    ration = service.definition("ration")
    assert wand.equipment_slot == "weapon"
    assert wand.actions == ("equip",)
    assert dict(wand.stats) == {"hp": 4, "attack": 2, "defense": 0}
    assert ration.actions == ("use",)
    assert ration.use_actions == ({"type": "heal", "amount": 7},)


def test_grid_cursor_navigates_visible_pages_without_wrapping():
    grid = InventoryGrid(InventoryLayout(columns=2, rows=2))
    item_ids = [f"item_{index}" for index in range(6)]

    assert grid.visible_item_ids(item_ids) == item_ids[:4]
    assert grid.move("DOWN", len(item_ids))
    assert grid.selected == 2 and grid.page == 0
    assert grid.move("DOWN", len(item_ids))
    assert grid.selected == 4 and grid.page == 1
    assert grid.visible_item_ids(item_ids) == item_ids[4:]
    assert grid.move("RIGHT", len(item_ids))
    assert grid.selected == 5
    assert not grid.move("RIGHT", len(item_ids))
    assert grid.move("UP", len(item_ids))
    assert grid.selected == 3 and grid.page == 0


def test_inventory_ui_settings_do_not_conflict_with_old_inventory_lists():
    assert InventoryLayout.from_profile({"inventory": ["apple"]}) == InventoryLayout()
    assert InventoryLayout.from_profile({"inventory_ui": {"columns": 5, "rows": 2}}) == InventoryLayout(5, 2)
    assert InventoryLayout.from_profile({"inventory": {"columns": 3, "rows": 4}}) == InventoryLayout(3, 4)


def test_contextual_actions_replace_equip_with_unequip_and_preserve_order():
    service = InventoryService(ITEMS)
    state = GameState(inventory={"iron_sword": 1, "apple": 1, "silver_key": 1})

    assert service.available_actions(state, "iron_sword") == ("equip", "toss")
    assert service.available_actions(state, "apple") == ("use", "toss")
    assert service.available_actions(state, "silver_key") == ()
    service.equip(state, "iron_sword")
    assert service.available_actions(state, "iron_sword") == ("unequip", "toss")


def test_equipment_replaces_same_slot_and_derives_stats_without_accumulation():
    service = InventoryService(ITEMS)
    state = GameState(
        inventory={"iron_sword": 1, "oak_staff": 1},
        stats={"hp": 10, "max_hp": 10, "attack": 4, "defense": 2},
    )

    first = service.equip(state, "iron_sword")
    assert first.previous_item_id is None
    assert service.effective_stats(state) == {"hp": 10, "max_hp": 10, "attack": 9, "defense": 3}
    second = service.equip(state, "oak_staff")
    assert second.previous_item_id == "iron_sword"
    assert state.has_item("iron_sword")  # equipped items are not duplicated or removed
    assert service.effective_stats(state) == {"hp": 10, "max_hp": 10, "attack": 6, "defense": 2}
    service.unequip(state, "oak_staff")
    assert service.effective_stats(state) == {"hp": 10, "max_hp": 10, "attack": 4, "defense": 2}
    assert effective_stats(state, service.definitions)["attack"] == 4


def test_use_heals_clamps_to_effective_maximum_and_removes_zero_quantity():
    service = InventoryService(ITEMS)
    state = GameState(inventory={"apple": 1}, stats={"hp": 6, "max_hp": 10})

    result = service.use(state, "apple")

    assert result.healed == 4
    assert state.get_stat("hp") == 10
    assert state.get_flag("ate_apple") is True
    assert "apple" not in state.inventory


def test_key_item_never_offers_or_allows_toss():
    service = InventoryService(ITEMS)
    state = GameState(inventory={"silver_key": 1})

    assert service.available_actions(state, "silver_key") == ()
    with pytest.raises(InventoryActionError):
        service.toss(state, "silver_key")
    assert state.has_item("silver_key")


def test_tossing_the_last_equipped_item_clears_its_slot():
    service = InventoryService(ITEMS)
    state = GameState(inventory={"iron_sword": 1}, stats={"hp": 10, "max_hp": 10})
    service.equip(state, "iron_sword")

    result = service.toss(state, "iron_sword")

    assert result.remaining_quantity == 0
    assert result.unequipped_slots == ("weapon",)
    assert state.get_equipped("weapon") is None


def test_unknown_legacy_save_item_is_visible_but_inert():
    service = InventoryService(ITEMS)
    state = GameState(inventory={"removed_mod_item": 2, "apple": 0})

    assert service.owned_item_ids(state) == ["removed_mod_item"]
    assert service.definition("removed_mod_item").name == "removed_mod_item"
    assert service.available_actions(state, "removed_mod_item") == ()
