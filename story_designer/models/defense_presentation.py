"""Qt-independent presentation/model helpers for battle defense authoring."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from typing import Any

from engine.battle.defense_metadata import (
    DefensePatternEditorSpec,
    DefensePatternFieldSpec,
    defense_pattern_editor_spec,
    defense_pattern_editor_specs,
)

from .battle_presentation import BattleElementSelection, _path_related
from .project_session import DefinitionSelection
from .editing import defense_pattern_references

PropertyPath = tuple[str | int, ...]


@dataclass(frozen=True)
class DefensePatternPresentation:
    selection: BattleElementSelection
    label: str
    type_name: str | None
    display_name: str
    summary: str
    authored: Any
    supported: bool
    editable: bool
    unsupported_reason: str | None = None
    diagnostics: tuple[Any, ...] = ()


@dataclass(frozen=True)
class DefenseSequencePresentation:
    selection: BattleElementSelection
    sequence_id: str | None
    label: str
    path: PropertyPath
    summary: str
    patterns: tuple[DefensePatternPresentation, ...] = ()
    diagnostics: tuple[Any, ...] = ()


class DefensePatternEditorModel:
    """Presentation model over the battle working mapping.

    It does not normalize aliases or copy data into a second document. Paths
    point directly at the current ``ProjectSession.working_mapping``.
    """

    def __init__(
        self,
        battle_id: str,
        mapping: Mapping[str, Any],
        diagnostics: Sequence[Any] = (),
    ) -> None:
        self.battle_id = str(battle_id)
        self.mapping = mapping
        self.diagnostics = tuple(diagnostics)
        self.collection_key = next((key for key in ("defense_sequences", "enemy_patterns") if key in mapping), None)
        self.collection_path: PropertyPath = (self.collection_key,) if self.collection_key else ()
        self._sequences = self._discover()

    @property
    def sequences(self) -> tuple[DefenseSequencePresentation, ...]:
        return self._sequences

    @property
    def registered_specs(self) -> Mapping[str, DefensePatternEditorSpec]:
        return defense_pattern_editor_specs()

    @property
    def registered_pattern_types(self) -> tuple[str, ...]:
        return tuple(self.registered_specs.keys())

    def sequence(self, selection: BattleElementSelection | None) -> DefenseSequencePresentation | None:
        return next((item for item in self._sequences if item.selection == selection), None)

    def pattern(self, selection: BattleElementSelection | None) -> DefensePatternPresentation | None:
        for sequence in self._sequences:
            for pattern in sequence.patterns:
                if pattern.selection == selection:
                    return pattern
        return None

    def field_entries(self, selection: BattleElementSelection) -> tuple[tuple[DefensePatternFieldSpec, PropertyPath, Any], ...]:
        pattern = self.pattern(selection)
        if pattern is None or not isinstance(pattern.authored, Mapping) or not pattern.type_name:
            return ()
        spec = defense_pattern_editor_spec(pattern.type_name)
        if spec is None or not spec.supported:
            return ()
        result = []
        for field in spec.fields:
            relative = field.path
            if len(relative) == 1 and isinstance(pattern.authored, Mapping):
                key = relative[0]
                if key not in pattern.authored:
                    alias = next((candidate for candidate in field.field.aliases if candidate in pattern.authored), None)
                    if alias is not None:
                        relative = (alias,)
            path = pattern.selection.path + relative
            result.append((field, path, _path_value(self.mapping, path)))
        return tuple(result)

    def references_for(self, selection: BattleElementSelection) -> tuple[str, ...]:
        pattern = self.pattern(selection)
        if pattern is None or not isinstance(pattern.authored, Mapping):
            return ()
        identifier = pattern.authored.get("id")
        return defense_pattern_references(self.mapping, identifier) if isinstance(identifier, str) and identifier else ()

    def _discover(self) -> tuple[DefenseSequencePresentation, ...]:
        raw = self.mapping.get(self.collection_key) if self.collection_key else None
        if not isinstance(raw, list):
            return ()
        result = []
        for index, value in enumerate(raw):
            path = self.collection_path + (index,)
            if isinstance(value, Mapping):
                identifier = value.get("id") if isinstance(value.get("id"), str) else None
                label = identifier or f"Sequence {index + 1}"
                patterns = self._patterns(path, value)
                summary = _summary(value)
            else:
                identifier = None
                label = f"Sequence {index + 1}"
                patterns = ()
                summary = _summary(value)
            selection = BattleElementSelection(self.battle_id, "defense_sequence", path, identifier)
            result.append(DefenseSequencePresentation(
                selection, identifier, str(label), path, summary, patterns,
                self._diagnostics(path),
            ))
        return tuple(result)

    def _patterns(self, sequence_path: PropertyPath, sequence: Mapping[str, Any]) -> tuple[DefensePatternPresentation, ...]:
        raw = sequence.get("patterns")
        if not isinstance(raw, list):
            return ()
        result = []
        for index, value in enumerate(raw):
            path = sequence_path + ("patterns", index)
            if isinstance(value, Mapping) and isinstance(value.get("type"), str):
                type_name = value["type"]
                spec = defense_pattern_editor_spec(type_name)
                supported = bool(spec is not None and spec.supported)
                display_name = spec.display_name if spec is not None else type_name.replace("_", " ").title()
                identifier = value.get("id") if isinstance(value.get("id"), str) else None
                label = str(identifier or type_name)
                reason = None if supported else "No editor metadata is registered for this runtime type."
            elif isinstance(value, Mapping) and isinstance(value.get("group"), str):
                type_name = None
                display_name = f"Group: {value['group']}"
                identifier = value.get("id") if isinstance(value.get("id"), str) else None
                label = str(identifier or value["group"])
                supported = False
                reason = "Group references preserve composition and are shown read-only in Step 12B."
            else:
                type_name = None
                display_name = "Unknown Pattern"
                identifier = None
                label = f"Pattern {index + 1}"
                supported = False
                reason = "Malformed or legacy pattern payload."
            result.append(DefensePatternPresentation(
                BattleElementSelection(self.battle_id, "defense_pattern", path, identifier),
                label, type_name, display_name, _summary(value), value,
                supported, supported, reason, self._diagnostics(path),
            ))
        return tuple(result)

    def _diagnostics(self, path: PropertyPath) -> tuple[Any, ...]:
        return tuple(item for item in self.diagnostics if _path_related(getattr(item, "path", ()), path))


def _path_value(mapping: Mapping[str, Any], path: PropertyPath) -> Any:
    current: Any = mapping
    for component in path:
        if isinstance(current, Mapping) and component in current:
            current = current[component]
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes, Mapping)) and isinstance(component, int) and 0 <= component < len(current):
            current = current[component]
        else:
            return _missing()
    return current


def _missing() -> Any:
    from engine.story_core.schema import MISSING
    return MISSING


def _summary(value: Any) -> str:
    try:
        return json.dumps(value, indent=2, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(value)


__all__ = ["DefensePatternEditorModel", "DefensePatternPresentation", "DefenseSequencePresentation"]
