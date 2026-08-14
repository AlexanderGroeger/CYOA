"""Static initial-player-profile model."""

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
    freeze_value,
    mapping_value,
)


@dataclass(frozen=True)
class PlayerProfile(DefinitionEnvelope):
    """Authored initial state, distinct from the mutable runtime ``GameState``.

    A player profile intentionally does not model current scene, flags, or
    other save data.  It is only the optional static ``player.yaml`` overlay
    that the runtime combines with legacy manifest starting values.
    """

    id: str
    source: Path
    authored: FrozenMapping = field(default_factory=dict, repr=False)
    field_path: FieldPath = ()
    present: bool = True
    stats: FrozenMapping = field(default_factory=dict)
    inventory: Any = ()
    equipment: FrozenMapping = field(default_factory=dict)
    known_moves: tuple[Any, ...] = ()
    move_skill_levels: FrozenMapping = field(default_factory=dict)
    inventory_ui: FrozenMapping = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._freeze_envelope()
        object.__setattr__(self, "id", str(self.id))
        object.__setattr__(self, "present", bool(self.present))
        for name in ("stats", "equipment", "move_skill_levels", "inventory_ui"):
            object.__setattr__(self, name, as_frozen_mapping(getattr(self, name)))
        object.__setattr__(self, "inventory", freeze_value(self.inventory))
        object.__setattr__(self, "known_moves", as_frozen_sequence(self.known_moves))

    @classmethod
    def from_mapping(
        cls,
        data: Mapping[str, Any],
        source: Path | str,
        *,
        identifier: str = "player",
        present: bool | None = None,
    ) -> "PlayerProfile":
        if not isinstance(data, Mapping):
            raise TypeError("Player profile must be a mapping")
        source_path = Path(source)
        return cls(
            id=identifier,
            source=source_path,
            authored=data,
            present=source_path.exists() if present is None else present,
            stats=mapping_value(data, "stats"),
            inventory=freeze_value(data.get("inventory", ())),
            equipment=mapping_value(data, "equipment"),
            known_moves=as_frozen_sequence(data.get("known_moves")),
            move_skill_levels=mapping_value(data, "move_skill_levels"),
            inventory_ui=mapping_value(data, "inventory_ui"),
        )

    @property
    def profile_id(self) -> str:
        return self.id

    @property
    def exists(self) -> bool:
        """Whether the profile came from a present ``player.yaml`` document."""

        return self.present


PlayerProfileDefinition = PlayerProfile


__all__ = ["PlayerProfile", "PlayerProfileDefinition"]
