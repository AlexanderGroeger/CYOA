"""
engine/core/game_state.py

GameState is the one piece of shared mutable data in the engine. The
interpreter, battle system, and save system all read and write it, but it
knows nothing about scenes, battles, or rendering itself -- it's plain data
plus small helper methods, so it's trivial to serialize (save/load) and
trivial to unit test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GameState:
    current_scene: str = ""
    flags: dict[str, bool] = field(default_factory=dict)
    variables: dict[str, Any] = field(default_factory=dict)
    inventory: dict[str, int] = field(default_factory=dict)  # item_id -> quantity
    stats: dict[str, Any] = field(default_factory=dict)      # hp, max_hp, attack, defense, ...
    equipment: dict[str, str] = field(default_factory=dict)  # slot -> item_id
    known_moves: list[str] = field(default_factory=list)     # learned move ids, in menu order
    # Move id -> {current_level: int, recent_scores: list[int]}.  Kept apart
    # from ``known_moves`` so learning order remains a simple menu concern.
    known_combat_moves: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Every scene id ever entered, in order. Not used for branching logic
    # (that would make behavior depend on history, which conditions don't
    # support) -- just useful for a debug/back-trace feature later.
    history: list[str] = field(default_factory=list)
    ending_reached: str | None = None

    # -- flags -----------------------------------------------------------
    def get_flag(self, name: str) -> bool:
        return bool(self.flags.get(name, False))

    def set_flag(self, name: str, value: bool) -> None:
        self.flags[name] = bool(value)

    # -- variables ---------------------------------------------------------
    def get_var(self, name: str, default: Any = 0) -> Any:
        return self.variables.get(name, default)

    def set_var(self, name: str, value: Any) -> None:
        self.variables[name] = value

    def add_var(self, name: str, delta: Any) -> None:
        """Numeric increment/decrement. If the variable doesn't exist yet,
        it's created starting from 0 -- lets story authors do
        `add_variable: {gold: 5}` on a var they never explicitly initialized."""
        self.variables[name] = self.variables.get(name, 0) + delta

    # -- inventory ---------------------------------------------------------
    def has_item(self, item_id: str, qty: int = 1) -> bool:
        return self.inventory.get(item_id, 0) >= qty

    def add_item(self, item_id: str, qty: int = 1) -> None:
        self.inventory[item_id] = self.inventory.get(item_id, 0) + qty

    def remove_item(self, item_id: str, qty: int = 1) -> None:
        remaining = self.inventory.get(item_id, 0) - qty
        if remaining > 0:
            self.inventory[item_id] = remaining
        else:
            self.inventory.pop(item_id, None)

    # -- equipment ---------------------------------------------------------
    def equip_item(self, slot: str, item_id: str) -> None:
        """Record an equipped item without changing inventory quantities.

        Stories may treat equipped gear as permanent or as normal inventory;
        battle item filtering checks the item definition rather than assuming
        all inventory entries are consumable.
        """
        self.equipment[slot] = item_id

    def get_equipped(self, slot: str, default: str | None = None) -> str | None:
        return self.equipment.get(slot, default)

    def unequip_item(self, slot: str) -> str | None:
        """Clear one equipment slot and return the item that was in it.

        Equipment remains normal inventory by design, so unequipping never
        changes an item quantity.  Keeping this small operation on the plain
        state object lets exploration and battle-adjacent systems share the
        same persistent representation.
        """
        return self.equipment.pop(slot, None)

    # -- learned moves ------------------------------------------------------
    def knows_move(self, move_id: str) -> bool:
        return move_id in self.known_moves

    def learn_move(self, move_id: str, initial_level: int = 1) -> None:
        if move_id not in self.known_moves:
            self.known_moves.append(move_id)
        self.known_combat_moves.setdefault(move_id, {
            "current_level": int(initial_level),
            "recent_scores": [],
        })

    def forget_move(self, move_id: str) -> None:
        if move_id in self.known_moves:
            self.known_moves.remove(move_id)

    # -- stats -------------------------------------------------------------
    def get_stat(self, name: str, default: Any = 0) -> Any:
        return self.stats.get(name, default)

    def set_stat(self, name: str, value: Any) -> None:
        self.stats[name] = value

    def add_stat(self, name: str, delta: Any) -> None:
        self.stats[name] = self.stats.get(name, 0) + delta

    # -- scene tracking ------------------------------------------------------
    def enter_scene(self, scene_id: str) -> None:
        self.current_scene = scene_id
        self.history.append(scene_id)

    # -- serialization -------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "current_scene": self.current_scene,
            "flags": dict(self.flags),
            "variables": dict(self.variables),
            "inventory": dict(self.inventory),
            "stats": dict(self.stats),
            "equipment": dict(self.equipment),
            "known_moves": list(self.known_moves),
            "known_combat_moves": {
                move_id: {
                    "current_level": data.get("current_level", 1),
                    "recent_scores": list(data.get("recent_scores", [])) if isinstance(data, dict) else [],
                }
                for move_id, data in self.known_combat_moves.items()
            },
            "history": list(self.history),
            "ending_reached": self.ending_reached,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GameState":
        """Load additive save data with safe defaults for older profiles.

        Exploration deliberately adds no derived-stat cache or transient scene
        state to saves.  Older v1 saves therefore remain valid: absent
        inventory/equipment/skill fields become empty containers and are
        rebuilt from current story data where appropriate.
        """
        def mapping(value: Any) -> dict[str, Any]:
            return dict(value) if isinstance(value, dict) else {}

        def sequence(value: Any) -> list[Any]:
            return list(value) if isinstance(value, list) else []

        return cls(
            current_scene=data.get("current_scene", ""),
            flags=mapping(data.get("flags", {})),
            variables=mapping(data.get("variables", {})),
            inventory=mapping(data.get("inventory", {})),
            stats=mapping(data.get("stats", {})),
            equipment=mapping(data.get("equipment", {})),
            known_moves=sequence(data.get("known_moves", [])),
            known_combat_moves=mapping(data.get("known_combat_moves", {})),
            history=sequence(data.get("history", [])),
            ending_reached=data.get("ending_reached"),
        )

    @classmethod
    def new_from_manifest(cls, manifest: dict[str, Any], player: dict[str, Any] | None = None) -> "GameState":
        """Build starting state from story-level values and ``player.yaml``.

        The optional profile preserves the old manifest-only form for legacy
        stories, but new stories keep stats, equipment, inventory, and learned
        moves in their player profile instead of encounter YAML.
        """
        player = player or {}
        state = cls(
            current_scene=manifest.get("start_scene", ""),
            flags=dict(manifest.get("starting_flags", {})),
            variables=dict(manifest.get("starting_variables", {})),
            stats=dict(player.get("stats", manifest.get("starting_stats", {}))),
        )
        inventory = player.get("inventory", manifest.get("starting_inventory", []))
        if isinstance(inventory, dict) and {"columns", "rows"} & set(inventory):
            # Alternate UI shape: ``inventory: {columns, rows, items: ...}``.
            # Layout keys are configuration, never carried-item ids.
            inventory = inventory.get("items", [])
        if isinstance(inventory, dict):
            for item_id, quantity in inventory.items():
                state.add_item(item_id, int(quantity))
        else:
            for item_id in inventory:
                state.add_item(item_id)
        for slot, item_id in player.get("equipment", manifest.get("starting_equipment", {})).items():
            state.equip_item(slot, item_id)
        authored_levels = player.get("move_skill_levels", {})
        if not isinstance(authored_levels, dict):
            authored_levels = {}
        for move_id in player.get("known_moves", []):
            if isinstance(move_id, dict):
                identifier = move_id.get("id")
                level = move_id.get("initial_level", 1)
            else:
                identifier, level = move_id, authored_levels.get(move_id, 1)
            if isinstance(identifier, str):
                state.learn_move(identifier, int(level) if isinstance(level, int) and not isinstance(level, bool) else 1)
        return state
