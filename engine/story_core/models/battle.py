"""Static battle-definition envelope."""

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
class BattleDefinition(DefinitionEnvelope):
    """A battle file keyed by its filename-derived, type-local ID.

    Active battle state and detailed QTE/defense interpretation intentionally
    remain outside Story/Core.  This model gives tooling a stable outer shape
    while preserving every specialized nested payload verbatim in
    :attr:`authored`.
    """

    id: str
    source: Path
    authored: FrozenMapping = field(default_factory=dict, repr=False)
    field_path: FieldPath = ()
    declared_id: str | None = None
    enemy: FrozenMapping = field(default_factory=dict)
    arena: FrozenMapping = field(default_factory=dict)
    player_moves: tuple[Any, ...] = ()
    enemy_moves: tuple[Any, ...] = ()
    enemy_patterns: tuple[Any, ...] = ()
    initial_player_moves: tuple[Any, ...] = ()
    initial_enemy_moves: tuple[Any, ...] = ()
    enemy_sequence: tuple[Any, ...] = ()
    dialogue: tuple[Any, ...] = ()
    phases: tuple[Any, ...] = ()
    victory: FrozenMapping = field(default_factory=dict)
    defeat: FrozenMapping = field(default_factory=dict)
    on_lose: FrozenMapping = field(default_factory=dict)
    background: str | None = None
    music: str | None = None
    escape_enabled: bool = False
    legacy: bool = False

    def __post_init__(self) -> None:
        self._freeze_envelope()
        object.__setattr__(self, "id", str(self.id))
        object.__setattr__(self, "declared_id", self.declared_id if isinstance(self.declared_id, str) else None)
        for name in ("enemy", "arena", "victory", "defeat", "on_lose"):
            object.__setattr__(self, name, as_frozen_mapping(getattr(self, name)))
        for name in (
            "player_moves",
            "enemy_moves",
            "enemy_patterns",
            "initial_player_moves",
            "initial_enemy_moves",
            "enemy_sequence",
            "dialogue",
            "phases",
        ):
            object.__setattr__(self, name, as_frozen_sequence(getattr(self, name)))
        for name in ("background", "music"):
            value = getattr(self, name)
            object.__setattr__(self, name, value if isinstance(value, str) else None)
        object.__setattr__(self, "escape_enabled", bool(self.escape_enabled))
        object.__setattr__(self, "legacy", bool(self.legacy))

    @classmethod
    def from_mapping(
        cls,
        data: Mapping[str, Any],
        source: Path | str,
        *,
        identifier: str | None = None,
    ) -> "BattleDefinition":
        if not isinstance(data, Mapping):
            raise TypeError("Battle definition must be a mapping")
        source_path = Path(source)
        declared_id = string_value(data, "id")
        # AssetLoader identifies battles by filename and deliberately does not
        # enforce a matching YAML ``id``.  Keep both values for diagnostics.
        battle_id = identifier or source_path.stem
        escape = data.get("escape", {})
        if isinstance(escape, Mapping):
            escape_enabled = bool(escape.get("enabled", False))
        else:
            escape_enabled = bool(escape) if isinstance(escape, bool) else False
        canonical_patterns = data.get("defense_sequences") if "defense_sequences" in data else data.get("enemy_patterns")
        enemy = data.get("enemy")
        legacy = "player_moves" not in data and "enemy_moves" not in data and isinstance(enemy, Mapping) and "moves" in enemy
        return cls(
            id=battle_id,
            source=source_path,
            authored=data,
            declared_id=declared_id,
            enemy=as_frozen_mapping(enemy),
            arena=mapping_value(data, "arena"),
            player_moves=sequence_value(data, "player_moves"),
            enemy_moves=sequence_value(data, "enemy_moves"),
            enemy_patterns=as_frozen_sequence(canonical_patterns),
            initial_player_moves=sequence_value(data, "initial_player_moves"),
            initial_enemy_moves=sequence_value(data, "initial_enemy_moves"),
            enemy_sequence=sequence_value(data, "enemy_sequence"),
            dialogue=sequence_value(data, "dialogue"),
            phases=sequence_value(data, "phases"),
            victory=mapping_value(data, "victory"),
            defeat=mapping_value(data, "defeat"),
            on_lose=mapping_value(data, "on_lose"),
            background=string_value(data, "background"),
            music=string_value(data, "music"),
            escape_enabled=escape_enabled,
            legacy=legacy or bool(data.get("_legacy_adapter", False)),
        )

    @property
    def defense_sequences(self) -> tuple[Any, ...]:
        """Canonical alias for either supported defense-sequence key."""

        return self.enemy_patterns

    @property
    def escape(self) -> Any:
        """The original boolean/mapping escape syntax for a legacy adapter."""

        return self.authored.get("escape", {})


__all__ = ["BattleDefinition"]
