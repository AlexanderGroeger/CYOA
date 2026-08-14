"""Pure inventory, equipment, and grid-navigation rules.

This module deliberately has no pygame or scene dependencies.  The game
frontend can use :class:`InventoryGrid` for its cursor state and
:class:`InventoryService` for every persistent inventory mutation, while
battle code can share :func:`effective_stats` / :func:`item_stat_bonuses`
instead of reimplementing equipment arithmetic.

Both the new item shape and the project's original ``equipment.bonuses`` /
``combat`` item fields are accepted.  New content should use the fields
documented on :class:`ItemDefinition`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from engine.core.game_state import GameState
from engine.errors import StoryValidationError


VALID_ITEM_ACTIONS = frozenset({"use", "equip", "unequip", "toss"})
_INFERRED_EQUIPMENT_TYPES = frozenset({"weapon", "armor", "accessory"})
_DISPLAY_STATS = ("hp", "attack", "defense")


class InventorySchemaError(StoryValidationError):
    """An item definition cannot be used by the inventory system."""


class InventoryActionError(ValueError):
    """A requested inventory action is unavailable for the current state."""


@dataclass(frozen=True)
class ItemDefinition:
    """Normalized, renderer-independent item data.

    ``stats.hp`` is an equipment bonus to maximum HP.  The current HP remains
    in ``GameState.stats['hp']``; this keeps an item from accidentally healing
    the player each time it is equipped.  ``actions`` is intentionally an
    ordered tuple so authored action-menu ordering is preserved.
    """

    id: str
    name: str
    item_type: str = "item"
    description: str = ""
    icon: str | None = None
    stats: Mapping[str, int] = field(default_factory=dict)
    equipment_slot: str | None = None
    actions: tuple[str, ...] = ()
    use_actions: tuple[Mapping[str, Any], ...] = ()
    legacy: bool = False

    def stat_bonus(self, name: str) -> int:
        """Return a normalized equipment bonus (``max_hp`` aliases ``hp``)."""
        if name == "max_hp":
            name = "hp"
        return int(self.stats.get(name, 0))


@dataclass(frozen=True)
class InventoryLayout:
    """Visible dimensions for an icon-grid inventory page."""

    columns: int = 4
    rows: int = 3

    def __post_init__(self) -> None:
        for field_name, value in (("columns", self.columns), ("rows", self.rows)):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise InventorySchemaError(f"Inventory {field_name} must be a positive integer")

    @property
    def page_size(self) -> int:
        return self.columns * self.rows

    @classmethod
    def from_profile(cls, profile: Mapping[str, Any] | None) -> "InventoryLayout":
        """Read new ``inventory_ui`` settings without confusing old inventory
        item mappings for UI configuration.

        ``inventory: {columns, rows}`` is also accepted when those are the
        only layout-like keys, preserving the alternate schema in the task.
        """
        profile = profile or {}
        raw = profile.get("inventory_ui")
        if raw is None:
            candidate = profile.get("inventory")
            if isinstance(candidate, Mapping) and ({"columns", "rows"} & set(candidate)):
                raw = candidate
        if raw is None:
            return cls()
        if not isinstance(raw, Mapping):
            raise InventorySchemaError("player inventory_ui must be a mapping")
        return cls(
            columns=_schema_positive_int(raw.get("columns", cls.columns), "player inventory_ui.columns"),
            rows=_schema_positive_int(raw.get("rows", cls.rows), "player inventory_ui.rows"),
        )


@dataclass
class InventoryGrid:
    """A global selection cursor over a paged icon grid.

    Pages are derived from ``selected`` rather than maintained independently,
    so removing an item cannot leave the UI on an invalid page.  Directional
    actions are deliberately clamped instead of wrapping; a Down press can
    naturally carry the cursor into the next page when a matching cell exists.
    """

    layout: InventoryLayout = field(default_factory=InventoryLayout)
    selected: int = 0

    def normalize(self, item_count: int) -> int:
        item_count = max(0, int(item_count))
        self.selected = 0 if item_count == 0 else min(max(0, self.selected), item_count - 1)
        return self.selected

    def move(self, action: str, item_count: int) -> bool:
        """Move once for a semantic input action and return whether it moved."""
        item_count = max(0, int(item_count))
        before = self.normalize(item_count)
        if item_count == 0:
            return False
        action = action.upper()
        if action == "LEFT" and self.selected % self.layout.columns:
            self.selected -= 1
        elif action == "RIGHT":
            target = self.selected + 1
            if self.selected % self.layout.columns < self.layout.columns - 1 and target < item_count:
                self.selected = target
        elif action == "UP":
            target = self.selected - self.layout.columns
            if target >= 0:
                self.selected = target
        elif action == "DOWN":
            target = self.selected + self.layout.columns
            if target < item_count:
                self.selected = target
        return self.selected != before

    @property
    def page(self) -> int:
        return self.selected // self.layout.page_size

    def page_bounds(self, item_count: int) -> tuple[int, int]:
        """Return the half-open global item-index range for the current page."""
        self.normalize(item_count)
        start = self.page * self.layout.page_size
        return start, min(max(0, int(item_count)), start + self.layout.page_size)

    def visible_item_ids(self, item_ids: Sequence[str]) -> list[str]:
        start, end = self.page_bounds(len(item_ids))
        return list(item_ids[start:end])


@dataclass(frozen=True)
class EquipmentChange:
    slot: str
    previous_item_id: str | None
    equipped_item_id: str | None


@dataclass(frozen=True)
class TossResult:
    item_id: str
    quantity: int
    remaining_quantity: int
    unequipped_slots: tuple[str, ...] = ()


@dataclass(frozen=True)
class ItemUseResult:
    item_id: str
    consumed_quantity: int
    healed: int = 0
    damaged: int = 0
    changed_stats: Mapping[str, int] = field(default_factory=dict)
    changed_flags: Mapping[str, bool] = field(default_factory=dict)


def normalize_item_definition(item_id: str, raw: Mapping[str, Any]) -> ItemDefinition:
    """Validate and normalize one current or legacy item definition.

    New fields are ``icon``, ``stats``, ``equipment_slot``, ``actions``, and
    ``use.actions``.  Legacy ``equipment.bonuses`` and ``combat.usable`` /
    ``combat.effects`` are adapted instead of discarded.
    """
    if not isinstance(item_id, str) or not item_id:
        raise InventorySchemaError("item id must be a non-empty string")
    if not isinstance(raw, Mapping):
        raise InventorySchemaError(f"items.{item_id} must be a mapping")

    source = f"items.{item_id}"
    name = raw.get("name", item_id)
    if not isinstance(name, str) or not name:
        raise InventorySchemaError(f"{source}.name must be a non-empty string")
    description = raw.get("description", "")
    if not isinstance(description, str):
        raise InventorySchemaError(f"{source}.description must be a string")
    item_type = raw.get("type", "item")
    if not isinstance(item_type, str) or not item_type:
        raise InventorySchemaError(f"{source}.type must be a non-empty string")
    icon = raw.get("icon")
    if icon is not None and (not isinstance(icon, str) or not icon):
        raise InventorySchemaError(f"{source}.icon must be a non-empty string when provided")

    stats, uses_legacy_stats = _normalise_item_stats(raw, source)
    slot, inferred_slot = _normalise_equipment_slot(raw, item_type, source)
    actions, explicit_actions = _normalise_item_actions(raw, slot, source)
    use_actions, uses_legacy_use = _normalise_use_actions(raw, source)
    if not explicit_actions:
        inferred_actions: list[str] = []
        if slot is not None:
            inferred_actions.append("equip")
        if use_actions:
            inferred_actions.append("use")
        actions = tuple(inferred_actions)

    return ItemDefinition(
        id=item_id,
        name=name,
        item_type=item_type,
        description=description,
        icon=icon,
        stats=stats,
        equipment_slot=slot,
        actions=actions,
        use_actions=use_actions,
        legacy=uses_legacy_stats or uses_legacy_use or inferred_slot or not explicit_actions,
    )


def normalize_item_definitions(raw_items: Mapping[str, Any] | None) -> dict[str, ItemDefinition]:
    """Normalize an ``items/items.yaml`` mapping, preserving its item ids."""
    if raw_items is None:
        return {}
    if not isinstance(raw_items, Mapping):
        raise InventorySchemaError("items/items.yaml must contain a mapping")
    return {
        item_id: normalize_item_definition(item_id, raw)
        for item_id, raw in raw_items.items()
    }


def item_stat_bonuses(item: ItemDefinition | Mapping[str, Any] | None) -> dict[str, int]:
    """Return canonical ``hp``, ``attack``, and ``defense`` equipment bonuses.

    ``item`` may be raw legacy YAML for battle callers that have not yet been
    migrated to an :class:`InventoryService`.
    """
    if item is None:
        return {name: 0 for name in _DISPLAY_STATS}
    if isinstance(item, ItemDefinition):
        return {name: item.stat_bonus(name) for name in _DISPLAY_STATS}
    if not isinstance(item, Mapping):
        return {name: 0 for name in _DISPLAY_STATS}
    # A synthetic id only affects diagnostics, never the returned data.
    return dict(normalize_item_definition("<item>", item).stats)


def effective_stats(state: GameState, items: Mapping[str, ItemDefinition | Mapping[str, Any]]) -> dict[str, int]:
    """Compute effective HP cap, attack, and defense from current equipment.

    Base stats in ``GameState`` are never modified.  This is therefore safe to
    call after any number of equip/unequip operations and makes repeated
    equipment changes non-accumulative by construction.
    """
    attack = _state_int(state, "attack", 0)
    defense = _state_int(state, "defense", 0)
    base_max_hp = _state_int(state, "max_hp", _state_int(state, "hp", 1))
    bonus_hp = 0
    for item_id in state.equipment.values():
        raw_item = items.get(item_id)
        if isinstance(raw_item, ItemDefinition):
            bonuses = item_stat_bonuses(raw_item)
        elif isinstance(raw_item, Mapping):
            bonuses = item_stat_bonuses(raw_item)
        else:
            # A removed item definition must not make an old save unloadable.
            bonuses = {name: 0 for name in _DISPLAY_STATS}
        attack += bonuses["attack"]
        defense += bonuses["defense"]
        bonus_hp += bonuses["hp"]
    max_hp = max(1, base_max_hp + bonus_hp)
    current_hp = max(0, min(_state_int(state, "hp", max_hp), max_hp))
    return {"hp": current_hp, "max_hp": max_hp, "attack": attack, "defense": defense}


class InventoryService:
    """State-changing inventory operations backed by normalized definitions."""

    def __init__(self, raw_items: Mapping[str, Any] | None = None):
        self._items = normalize_item_definitions(raw_items)

    @property
    def definitions(self) -> Mapping[str, ItemDefinition]:
        return self._items

    def definition(self, item_id: str) -> ItemDefinition:
        """Return an inert placeholder for a legacy save's removed item id."""
        known = self._items.get(item_id)
        if known is not None:
            return known
        return ItemDefinition(id=item_id, name=item_id, legacy=True)

    def owned_item_ids(self, state: GameState) -> list[str]:
        """Stable grid order for all positive-quantity items, including unknown ids."""
        return sorted(item_id for item_id, quantity in state.inventory.items()
                      if isinstance(item_id, str) and _quantity(quantity) > 0)

    def available_actions(self, state: GameState, item_id: str) -> tuple[str, ...]:
        """Return the contextual action-menu entries for one owned item."""
        if not state.has_item(item_id):
            return ()
        item = self.definition(item_id)
        result: list[str] = []
        is_equipped = bool(item.equipment_slot and state.get_equipped(item.equipment_slot) == item_id)
        for action in item.actions:
            if action == "equip":
                result.append("unequip" if is_equipped else "equip")
            elif action == "unequip":
                if is_equipped:
                    result.append("unequip")
            else:
                result.append(action)
        return tuple(result)

    def equip(self, state: GameState, item_id: str, *, allow_unowned: bool = False) -> EquipmentChange:
        item = self.definition(item_id)
        if "equip" not in item.actions:
            raise InventoryActionError(f"{item_id!r} cannot be equipped")
        if item.equipment_slot is None:
            raise InventoryActionError(f"{item_id!r} has no equipment slot")
        if not allow_unowned and not state.has_item(item_id):
            raise InventoryActionError(f"{item_id!r} is not in the inventory")
        previous = state.get_equipped(item.equipment_slot)
        state.equip_item(item.equipment_slot, item_id)
        self._clamp_current_hp(state)
        return EquipmentChange(item.equipment_slot, previous, item_id)

    def unequip(self, state: GameState, item_id: str) -> EquipmentChange:
        item = self.definition(item_id)
        slot = item.equipment_slot
        if slot is None or state.get_equipped(slot) != item_id:
            raise InventoryActionError(f"{item_id!r} is not equipped")
        state.unequip_item(slot)
        self._clamp_current_hp(state)
        return EquipmentChange(slot, item_id, None)

    def toss(self, state: GameState, item_id: str, quantity: int = 1) -> TossResult:
        item = self.definition(item_id)
        if "toss" not in item.actions:
            raise InventoryActionError(f"{item_id!r} cannot be tossed")
        quantity = _positive_int(quantity, "toss quantity")
        owned = _quantity(state.inventory.get(item_id, 0))
        if owned < quantity:
            raise InventoryActionError(f"Not enough {item_id!r} to toss")
        state.remove_item(item_id, quantity)
        remaining = _quantity(state.inventory.get(item_id, 0))
        unequipped: list[str] = []
        if remaining == 0:
            for slot, equipped_id in tuple(state.equipment.items()):
                if equipped_id == item_id:
                    state.unequip_item(slot)
                    unequipped.append(slot)
            self._clamp_current_hp(state)
        return TossResult(item_id, quantity, remaining, tuple(unequipped))

    def use(self, state: GameState, item_id: str,
            action_handler: Callable[[Mapping[str, Any]], None] | None = None) -> ItemUseResult:
        """Apply local ``use.actions`` and consume one item.

        The optional handler is the bridge for a future asynchronous event
        runner.  It receives unsupported actions (for example ``dialog``) in
        authored order.  Without one, unsupported actions fail before the item
        is consumed, avoiding silent loss of a key item.
        """
        item = self.definition(item_id)
        if "use" not in item.actions:
            raise InventoryActionError(f"{item_id!r} cannot be used")
        if not state.has_item(item_id):
            raise InventoryActionError(f"{item_id!r} is not in the inventory")
        unsupported = [action for action in item.use_actions if _action_type(action) not in _LOCAL_USE_ACTIONS]
        if unsupported and action_handler is None:
            raise InventoryActionError(
                f"{item_id!r} requires an event action handler for {_action_type(unsupported[0])!r}"
            )

        healed = damaged = 0
        changed_stats: dict[str, int] = {}
        changed_flags: dict[str, bool] = {}
        for action in item.use_actions:
            action_type = _action_type(action)
            if action_type not in _LOCAL_USE_ACTIONS:
                assert action_handler is not None
                action_handler(action)
                continue
            local = self._apply_local_use_action(state, action)
            healed += local["healed"]
            damaged += local["damaged"]
            changed_stats.update(local["changed_stats"])
            changed_flags.update(local["changed_flags"])
        state.remove_item(item_id)
        return ItemUseResult(item_id, 1, healed, damaged, changed_stats, changed_flags)

    def effective_stats(self, state: GameState) -> dict[str, int]:
        return effective_stats(state, self._items)

    def _clamp_current_hp(self, state: GameState) -> None:
        maximum = self.effective_stats(state)["max_hp"]
        current = _state_int(state, "hp", maximum)
        if current > maximum:
            state.set_stat("hp", maximum)

    def _apply_local_use_action(self, state: GameState, action: Mapping[str, Any]) -> dict[str, Any]:
        action_type = _action_type(action)
        result: dict[str, Any] = {"healed": 0, "damaged": 0, "changed_stats": {}, "changed_flags": {}}
        if action_type == "heal":
            amount = _positive_or_zero_int(action.get("amount"), "use.actions[].amount")
            before = max(0, _state_int(state, "hp", 0))
            after = min(self.effective_stats(state)["max_hp"], before + amount)
            state.set_stat("hp", after)
            result["healed"] = after - before
        elif action_type == "damage":
            amount = _positive_or_zero_int(action.get("amount"), "use.actions[].amount")
            before = max(0, _state_int(state, "hp", 0))
            after = max(0, before - amount)
            state.set_stat("hp", after)
            result["damaged"] = before - after
        elif action_type == "change_stat":
            stat = action.get("stat")
            if not isinstance(stat, str) or not stat:
                raise InventoryActionError("change_stat requires a non-empty stat")
            delta = action.get("amount", action.get("delta"))
            delta = _integer(delta, "change_stat amount")
            state.add_stat(stat, delta)
            result["changed_stats"][stat] = state.get_stat(stat)
        elif action_type == "set_flag":
            flag = action.get("flag")
            if not isinstance(flag, str) or not flag:
                raise InventoryActionError("set_flag requires a non-empty flag")
            value = bool(action.get("value", True))
            state.set_flag(flag, value)
            result["changed_flags"][flag] = value
        elif action_type == "clear_flag":
            flag = action.get("flag")
            if not isinstance(flag, str) or not flag:
                raise InventoryActionError("clear_flag requires a non-empty flag")
            state.set_flag(flag, False)
            result["changed_flags"][flag] = False
        elif action_type == "give_item":
            target = action.get("item")
            if not isinstance(target, str) or not target:
                raise InventoryActionError("give_item requires a non-empty item")
            state.add_item(target, _positive_int(action.get("quantity", 1), "give_item quantity"))
        elif action_type == "remove_item":
            target = action.get("item")
            if not isinstance(target, str) or not target:
                raise InventoryActionError("remove_item requires a non-empty item")
            state.remove_item(target, _positive_int(action.get("quantity", 1), "remove_item quantity"))
        return result


_LOCAL_USE_ACTIONS = frozenset({"heal", "damage", "change_stat", "set_flag", "clear_flag", "give_item", "remove_item"})


def _normalise_item_stats(raw: Mapping[str, Any], source: str) -> tuple[dict[str, int], bool]:
    declared = raw.get("stats")
    if declared is not None and not isinstance(declared, Mapping):
        raise InventorySchemaError(f"{source}.stats must be a mapping")
    declared = declared or {}
    equipment = raw.get("equipment", {})
    if equipment is not None and not isinstance(equipment, Mapping):
        raise InventorySchemaError(f"{source}.equipment must be a mapping")
    equipment = equipment or {}
    legacy_bonuses = equipment.get("bonuses", raw.get("bonuses", {}))
    if legacy_bonuses is None:
        legacy_bonuses = {}
    if not isinstance(legacy_bonuses, Mapping):
        raise InventorySchemaError(f"{source}.equipment.bonuses must be a mapping")
    result: dict[str, int] = {}
    for stat in _DISPLAY_STATS:
        if stat in declared:
            value = declared[stat]
        elif stat == "hp" and "max_hp" in declared:
            value = declared["max_hp"]
        elif stat in legacy_bonuses:
            value = legacy_bonuses[stat]
        elif stat == "hp" and "max_hp" in legacy_bonuses:
            value = legacy_bonuses["max_hp"]
        else:
            value = 0
        result[stat] = _integer(value, f"{source}.stats.{stat}")
    return result, bool(legacy_bonuses)


def _normalise_equipment_slot(raw: Mapping[str, Any], item_type: str, source: str) -> tuple[str | None, bool]:
    slot = raw.get("equipment_slot")
    equipment = raw.get("equipment", {})
    if slot is None and isinstance(equipment, Mapping):
        slot = equipment.get("slot")
    inferred = False
    if slot is None and item_type in _INFERRED_EQUIPMENT_TYPES:
        slot = item_type
        inferred = True
    if slot is not None and (not isinstance(slot, str) or not slot):
        raise InventorySchemaError(f"{source}.equipment_slot must be a non-empty string")
    return slot, inferred


def _normalise_item_actions(raw: Mapping[str, Any], slot: str | None, source: str) -> tuple[tuple[str, ...], bool]:
    if "actions" not in raw:
        return (), False
    actions = raw["actions"]
    if not isinstance(actions, (list, tuple)):
        raise InventorySchemaError(f"{source}.actions must be a list")
    result: list[str] = []
    for index, action in enumerate(actions):
        if not isinstance(action, str) or action not in VALID_ITEM_ACTIONS:
            raise InventorySchemaError(f"{source}.actions[{index}] is not a known item action: {action!r}")
        if action in result:
            raise InventorySchemaError(f"{source}.actions contains duplicate action {action!r}")
        result.append(action)
    if {"equip", "unequip"} & set(result):
        equipment = raw.get("equipment")
        explicit_slot = "equipment_slot" in raw or (isinstance(equipment, Mapping) and "slot" in equipment)
        # Old items infer their conventional slot because they did not have an
        # actions menu at all.  New authored action menus must state their
        # intended slot, preventing a typo from silently equipping armor as a
        # weapon solely because of its type label.
        if slot is None or not explicit_slot:
            raise InventorySchemaError(f"{source}.equipment_slot is required for equip/unequip actions")
    return tuple(result), True


def _normalise_use_actions(raw: Mapping[str, Any], source: str) -> tuple[tuple[Mapping[str, Any], ...], bool]:
    use = raw.get("use")
    if use is not None:
        if not isinstance(use, Mapping):
            raise InventorySchemaError(f"{source}.use must be a mapping")
        actions = use.get("actions", [])
        return _normalise_action_list(actions, f"{source}.use.actions"), False

    combat = raw.get("combat")
    if not isinstance(combat, Mapping) or not combat.get("usable"):
        return (), False
    legacy_actions: list[Mapping[str, Any]] = []
    effects = combat.get("effects", [])
    if not isinstance(effects, list):
        raise InventorySchemaError(f"{source}.combat.effects must be a list")
    for index, effect in enumerate(effects):
        if not isinstance(effect, Mapping) or len(effect) != 1:
            raise InventorySchemaError(f"{source}.combat.effects[{index}] must be a one-effect mapping")
        effect_name, value = next(iter(effect.items()))
        if effect_name == "heal":
            legacy_actions.append({"type": "heal", "amount": value})
        else:
            # Preserve unsupported combat effects as authored action requests;
            # a caller can provide an event handler or decline to expose Use.
            legacy_actions.append({"type": f"combat_{effect_name}", "value": value})
    return tuple(legacy_actions), True


def _normalise_action_list(actions: Any, source: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(actions, (list, tuple)):
        raise InventorySchemaError(f"{source} must be a list")
    result: list[Mapping[str, Any]] = []
    for index, action in enumerate(actions):
        if not isinstance(action, Mapping):
            raise InventorySchemaError(f"{source}[{index}] must be a mapping")
        action_type = action.get("type")
        if not isinstance(action_type, str) or not action_type:
            raise InventorySchemaError(f"{source}[{index}].type must be a non-empty string")
        normalized = dict(action)
        _validate_local_use_action(normalized, f"{source}[{index}]")
        result.append(normalized)
    return tuple(result)


def _action_type(action: Mapping[str, Any]) -> str:
    value = action.get("type")
    return value if isinstance(value, str) else ""


def _validate_local_use_action(action: Mapping[str, Any], source: str) -> None:
    """Catch malformed built-in effects while preserving extensible events."""
    action_type = _action_type(action)
    if action_type in {"heal", "damage"}:
        _schema_nonnegative_int(action.get("amount"), f"{source}.amount")
    elif action_type == "change_stat":
        stat = action.get("stat")
        if not isinstance(stat, str) or not stat:
            raise InventorySchemaError(f"{source}.stat must be a non-empty string")
        _integer(action.get("amount", action.get("delta")), f"{source}.amount")
    elif action_type in {"set_flag", "clear_flag"}:
        flag = action.get("flag")
        if not isinstance(flag, str) or not flag:
            raise InventorySchemaError(f"{source}.flag must be a non-empty string")
    elif action_type in {"give_item", "remove_item"}:
        item_id = action.get("item")
        if not isinstance(item_id, str) or not item_id:
            raise InventorySchemaError(f"{source}.item must be a non-empty string")
        _schema_positive_int(action.get("quantity", 1), f"{source}.quantity")


def _integer(value: Any, source: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InventorySchemaError(f"{source} must be an integer")
    return int(value)


def _positive_int(value: Any, source: str) -> int:
    result = _integer(value, source)
    if result <= 0:
        raise InventoryActionError(f"{source} must be positive")
    return result


def _schema_positive_int(value: Any, source: str) -> int:
    result = _integer(value, source)
    if result <= 0:
        raise InventorySchemaError(f"{source} must be positive")
    return result


def _schema_nonnegative_int(value: Any, source: str) -> int:
    result = _integer(value, source)
    if result < 0:
        raise InventorySchemaError(f"{source} cannot be negative")
    return result


def _positive_or_zero_int(value: Any, source: str) -> int:
    result = _integer(value, source)
    if result < 0:
        raise InventoryActionError(f"{source} cannot be negative")
    return result


def _quantity(value: Any) -> int:
    """Defensively read old save quantities without crashing the Bag UI."""
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _state_int(state: GameState, stat: str, default: int) -> int:
    value = state.get_stat(stat, default)
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
