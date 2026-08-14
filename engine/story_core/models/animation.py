"""Static renderer-animation definition envelope."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import DefinitionEnvelope, FieldPath, FrozenMapping, as_frozen_sequence, sequence_value, string_value


@dataclass(frozen=True)
class AnimationDefinition(DefinitionEnvelope):
    """An ``assets/animations/<name>/anim.yaml`` definition.

    Frame paths are intentionally relative strings.  Resolving them is an
    asset/source concern and must retain story-local-before-shared precedence.
    """

    id: str
    source: Path
    authored: FrozenMapping = field(default_factory=dict, repr=False)
    field_path: FieldPath = ()
    declared_id: str | None = None
    frames: tuple[Any, ...] = ()
    frame_delay_ms: int = 300
    loop: bool = True

    def __post_init__(self) -> None:
        self._freeze_envelope()
        object.__setattr__(self, "id", str(self.id))
        object.__setattr__(self, "declared_id", self.declared_id if isinstance(self.declared_id, str) else None)
        object.__setattr__(self, "frames", as_frozen_sequence(self.frames))
        delay = self.frame_delay_ms
        object.__setattr__(self, "frame_delay_ms", delay if isinstance(delay, int) and not isinstance(delay, bool) else 300)
        object.__setattr__(self, "loop", bool(self.loop))

    @classmethod
    def from_mapping(
        cls,
        data: Mapping[str, Any],
        source: Path | str,
        *,
        identifier: str | None = None,
    ) -> "AnimationDefinition":
        if not isinstance(data, Mapping):
            raise TypeError("Animation definition must be a mapping")
        source_path = Path(source)
        declared_id = string_value(data, "id")
        animation_id = identifier or source_path.parent.name
        delay = data.get("frame_delay_ms", 300)
        return cls(
            id=animation_id,
            source=source_path,
            authored=data,
            declared_id=declared_id,
            frames=sequence_value(data, "frames"),
            frame_delay_ms=delay if isinstance(delay, int) and not isinstance(delay, bool) else 300,
            loop=bool(data.get("loop", True)),
        )

    @property
    def animation_name(self) -> str:
        return self.id

    @property
    def delay_ms(self) -> int:
        return self.frame_delay_ms

    @property
    def directory(self) -> Path:
        return self.source.parent


__all__ = ["AnimationDefinition"]
