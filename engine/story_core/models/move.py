"""Static combat-move definition envelope."""

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
    mapping_value,
    string_value,
)


@dataclass(frozen=True)
class MoveDefinition(DefinitionEnvelope):
    """A global combat-move declaration.

    QTE parameters and difficulty-level payloads are deliberately kept as
    immutable authored mappings.  They are variant-heavy and already have a
    tested compatibility resolver in ``engine.battle.move_progression``.
    """

    id: str
    source: Path
    authored: FrozenMapping = field(default_factory=dict, repr=False)
    field_path: FieldPath = ()
    declared_id: str | None = None
    name: str = ""
    description: str = ""
    common: FrozenMapping = field(default_factory=dict)
    difficulty_levels: FrozenMapping = field(default_factory=dict)
    initial_level: int = 1
    tutorial_records_skill: bool = False
    availability: Any = None
    qte: FrozenMapping = field(default_factory=dict)
    legacy: bool = False

    def __post_init__(self) -> None:
        self._freeze_envelope()
        object.__setattr__(self, "id", str(self.id))
        object.__setattr__(self, "declared_id", self.declared_id if isinstance(self.declared_id, str) else None)
        object.__setattr__(self, "name", self.name if isinstance(self.name, str) and self.name else self.id)
        object.__setattr__(self, "description", self.description if isinstance(self.description, str) else "")
        for name in ("common", "difficulty_levels", "qte"):
            object.__setattr__(self, name, as_frozen_mapping(getattr(self, name)))
        level = self.initial_level
        object.__setattr__(self, "initial_level", level if isinstance(level, int) and not isinstance(level, bool) else 1)
        object.__setattr__(self, "tutorial_records_skill", bool(self.tutorial_records_skill))
        # ``availability`` may be a string condition or a structured mapping.
        from . import freeze_value

        object.__setattr__(self, "availability", freeze_value(self.availability))
        object.__setattr__(self, "legacy", bool(self.legacy))

    @classmethod
    def from_mapping(
        cls,
        data: Mapping[str, Any],
        source: Path | str,
        *,
        identifier: str | None = None,
        field_path: FieldPath = (),
    ) -> "MoveDefinition":
        if not isinstance(data, Mapping):
            raise TypeError("Move definition must be a mapping")
        source_path = Path(source)
        declared_id = string_value(data, "id")
        move_id = identifier or declared_id or source_path.stem
        common = mapping_value(data, "common")
        qte = mapping_value(common, "qte") if common else mapping_value(data, "qte")
        initial_level = data.get("initial_level", 1)
        return cls(
            id=move_id,
            source=source_path,
            authored=data,
            field_path=field_path,
            declared_id=declared_id,
            name=string_value(data, "name", move_id) or move_id,
            description=string_value(data, "description", "") or "",
            common=common,
            difficulty_levels=mapping_value(data, "difficulty_levels"),
            initial_level=initial_level if isinstance(initial_level, int) and not isinstance(initial_level, bool) else 1,
            tutorial_records_skill=bool(data.get("tutorial_records_skill", False)),
            availability=data.get("availability"),
            qte=qte,
            legacy="difficulty_levels" not in data,
        )

    @property
    def adaptive(self) -> bool:
        return not self.legacy

    @property
    def available_levels(self) -> tuple[int, ...]:
        """The authored numeric difficulty keys, without claiming validity."""

        return tuple(sorted(level for level in self.difficulty_levels if isinstance(level, int) and not isinstance(level, bool)))

    def resolve(self, level: int) -> dict[str, Any]:
        """Use the existing pure move resolver for a runtime-compatible view."""

        # Delayed import avoids making the static model package depend on the
        # battle package at import time while retaining one merge authority.
        from engine.battle.move_progression import resolve_combat_move

        return resolve_combat_move(self.to_mapping(), level)


CombatMoveDefinition = MoveDefinition


__all__ = ["CombatMoveDefinition", "MoveDefinition"]
