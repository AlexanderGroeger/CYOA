"""Static item-definition envelope."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import (
    DefinitionEnvelope,
    FieldPath,
    FrozenMapping,
    as_frozen_mapping,
    as_frozen_sequence,
    mapping_value,
    sequence_value,
    string_value,
)


@dataclass(frozen=True)
class ItemDefinition(DefinitionEnvelope):
    """An item registry entry keyed by its type-local item ID.

    ``combat`` and ``equipment`` are retained as immutable envelope fields
    because battle compatibility still reads portions of their legacy shape
    directly.  The concise canonical fields are intended for future tooling,
    not as a lossy replacement for that authored payload.
    """

    id: str
    source: Path
    authored: FrozenMapping = field(default_factory=dict, repr=False)
    field_path: FieldPath = ()
    declared_id: str | None = None
    name: str = ""
    description: str = ""
    item_type: str = "item"
    icon: str | None = None
    stats: FrozenMapping = field(default_factory=dict)
    equipment_slot: str | None = None
    actions: tuple[Any, ...] = ()
    use_actions: tuple[Any, ...] = ()
    equipment: FrozenMapping = field(default_factory=dict)
    combat: FrozenMapping = field(default_factory=dict)
    legacy: bool = False

    def __post_init__(self) -> None:
        self._freeze_envelope()
        object.__setattr__(self, "id", str(self.id))
        object.__setattr__(self, "declared_id", self.declared_id if isinstance(self.declared_id, str) else None)
        object.__setattr__(self, "name", self.name if isinstance(self.name, str) and self.name else self.id)
        object.__setattr__(self, "description", self.description if isinstance(self.description, str) else "")
        object.__setattr__(self, "item_type", self.item_type if isinstance(self.item_type, str) and self.item_type else "item")
        object.__setattr__(self, "icon", self.icon if isinstance(self.icon, str) else None)
        object.__setattr__(self, "equipment_slot", self.equipment_slot if isinstance(self.equipment_slot, str) else None)
        for name in ("stats", "equipment", "combat"):
            object.__setattr__(self, name, as_frozen_mapping(getattr(self, name)))
        for name in ("actions", "use_actions"):
            object.__setattr__(self, name, as_frozen_sequence(getattr(self, name)))
        object.__setattr__(self, "legacy", bool(self.legacy))

    @classmethod
    def from_mapping(
        cls,
        data: Mapping[str, Any],
        source: Path | str,
        *,
        identifier: str | None = None,
        field_path: FieldPath = (),
    ) -> "ItemDefinition":
        if not isinstance(data, Mapping):
            raise TypeError("Item definition must be a mapping")
        source_path = Path(source)
        declared_id = string_value(data, "id")
        item_id = identifier or declared_id or source_path.stem
        use = data.get("use")
        use_actions = use.get("actions") if isinstance(use, Mapping) else ()
        has_legacy_fields = isinstance(data.get("equipment"), Mapping) or isinstance(data.get("combat"), Mapping)
        return cls(
            id=item_id,
            source=source_path,
            authored=data,
            field_path=field_path,
            declared_id=declared_id,
            name=string_value(data, "name", item_id) or item_id,
            description=string_value(data, "description", "") or "",
            item_type=string_value(data, "type", "item") or "item",
            icon=string_value(data, "icon"),
            stats=mapping_value(data, "stats"),
            equipment_slot=string_value(data, "equipment_slot"),
            actions=sequence_value(data, "actions"),
            use_actions=as_frozen_sequence(use_actions),
            equipment=mapping_value(data, "equipment"),
            combat=mapping_value(data, "combat"),
            legacy=has_legacy_fields,
        )

    @property
    def type(self) -> str:
        """Compatibility-friendly alias for the serialized ``type`` field."""

        return self.item_type

    @property
    def use(self) -> FrozenMapping:
        value = self.authored.get("use")
        return as_frozen_mapping(value)

    def stat_bonus(self, name: str) -> int:
        """Return a best-effort canonical stat bonus for editor previews."""

        if name == "max_hp":
            name = "hp"
        value = self.stats.get(name, 0)
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0


# The shorter name is intentionally shared with the existing inventory model;
# callers can use this explicit alias to avoid importing the runtime module.
StoryItemDefinition = ItemDefinition


__all__ = ["ItemDefinition", "StoryItemDefinition"]
