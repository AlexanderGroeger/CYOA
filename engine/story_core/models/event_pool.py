"""Static random-event-pool definition envelope."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import DefinitionEnvelope, FieldPath, FrozenMapping, as_frozen_sequence, sequence_value, string_value


@dataclass(frozen=True)
class EventPoolDefinition(DefinitionEnvelope):
    """A filename-addressed weighted random-event pool."""

    id: str
    source: Path
    authored: FrozenMapping = field(default_factory=dict, repr=False)
    field_path: FieldPath = ()
    declared_id: str | None = None
    chance: float = 0.0
    events: tuple[Any, ...] = ()

    def __post_init__(self) -> None:
        self._freeze_envelope()
        object.__setattr__(self, "id", str(self.id))
        object.__setattr__(self, "declared_id", self.declared_id if isinstance(self.declared_id, str) else None)
        chance = self.chance
        if isinstance(chance, bool) or not isinstance(chance, (int, float)):
            chance = 0.0
        object.__setattr__(self, "chance", float(chance))
        object.__setattr__(self, "events", as_frozen_sequence(self.events))

    @classmethod
    def from_mapping(
        cls,
        data: Mapping[str, Any],
        source: Path | str,
        *,
        identifier: str | None = None,
    ) -> "EventPoolDefinition":
        if not isinstance(data, Mapping):
            raise TypeError("Event pool definition must be a mapping")
        source_path = Path(source)
        declared_id = string_value(data, "id")
        pool_id = identifier or source_path.stem
        chance = data.get("chance", 0.0)
        return cls(
            id=pool_id,
            source=source_path,
            authored=data,
            declared_id=declared_id,
            chance=chance if isinstance(chance, (int, float)) and not isinstance(chance, bool) else 0.0,
            events=sequence_value(data, "events"),
        )

    @property
    def event_ids(self) -> tuple[str, ...]:
        """Static scene IDs referenced by well-formed weighted entries."""

        return tuple(
            event_id
            for event in self.events
            if isinstance(event, Mapping) and isinstance((event_id := event.get("id")), str) and event_id
        )


__all__ = ["EventPoolDefinition"]
