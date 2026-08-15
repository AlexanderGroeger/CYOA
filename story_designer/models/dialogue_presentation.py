"""Qt-independent presentation of the scene-local dialogue vocabulary.

The runtime deliberately accepts a few historical shapes for exploration
dialogue.  This module only gives the editor stable paths into those shapes;
it is not a second schema or serializer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from engine.story_core import ActionForm, ActionScope, action_editor_spec, parse_action
from engine.story_core.schema import MISSING


PropertyPath = tuple[str | int, ...]


@dataclass(frozen=True)
class DialogueEntrySelection:
    """Editor-local identity for an entry; serialized IDs are never added."""

    scene_id: str
    source_id: str
    path: PropertyPath
    index: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "scene_id", str(self.scene_id))
        object.__setattr__(self, "source_id", str(self.source_id))
        object.__setattr__(self, "path", tuple(self.path))
        object.__setattr__(self, "index", int(self.index))


@dataclass(frozen=True)
class DialogueEntryPresentation:
    selection: DialogueEntrySelection
    text: str | None
    text_path: PropertyPath | None
    authored: Any = None
    supported: bool = True
    unsupported_reason: str | None = None
    condition: Any = MISSING
    metadata: Mapping[str, Any] = field(default_factory=dict, repr=False)
    actions: tuple["DialogueActionPresentation", ...] = ()


@dataclass(frozen=True)
class DialogueSourcePresentation:
    id: str
    label: str
    kind: str
    collection_path: PropertyPath
    entries: tuple[DialogueEntryPresentation, ...] = ()
    referenced_by: tuple[str, ...] = ()
    shape: str = "missing"


@dataclass(frozen=True)
class DialogueActionPresentation:
    """A non-mutating view of one authored dialogue/event action."""

    path: PropertyPath
    index: int
    raw: Any
    action_type: str | None
    display_name: str
    summary: str
    supported: bool
    editable: bool
    scope: ActionScope = ActionScope.EXPLORATION
    unsupported_reason: str | None = None


class DialogueDocumentModel:
    """Read-only editor adapter over one mutable scene working mapping."""

    def __init__(self, scene_id: str, mapping: Mapping[str, Any]) -> None:
        self.scene_id = str(scene_id)
        self.mapping = mapping
        self._sources = self._discover()

    @property
    def sources(self) -> tuple[DialogueSourcePresentation, ...]:
        return self._sources

    def source(self, source_id: str) -> DialogueSourcePresentation | None:
        return next((source for source in self._sources if source.id == source_id), None)

    def source_for_selection(self, selection: DialogueEntrySelection | None) -> DialogueSourcePresentation | None:
        return self.source(selection.source_id) if selection is not None else None

    def _discover(self) -> tuple[DialogueSourcePresentation, ...]:
        result: list[DialogueSourcePresentation] = []
        dialog_path = _authored_path(self.mapping, "dialog")
        if dialog_path is not None:
            result.append(_source_from_value(
                self.scene_id, "scene_entry", "Scene Entry", "scene_entry", dialog_path,
                _path_value(self.mapping, dialog_path), self._references_for("scene_entry"),
                reference_names=self._sequence_names(),
            ))
        elif "text" in self.mapping:
            result.append(_source_from_value(
                self.scene_id, "scene_text", "Scene Text (legacy)", "scene_text", ("text",),
                self.mapping.get("text"), (),
            ))
        else:
            # This is the canonical insertion point for a new exploration
            # scene-entry list.  It is absent until the first add command.
            result.append(DialogueSourcePresentation(
                "scene_entry", "Scene Entry", "scene_entry", _canonical_dialog_path(self.mapping), (), (), "missing"
            ))

        sequences_path = _authored_path(self.mapping, "dialogue_sequences")
        sequences = _path_value(self.mapping, sequences_path) if sequences_path is not None else MISSING
        if isinstance(sequences, Mapping):
            references = self._sequence_references()
            for identifier, value in sequences.items():
                if not isinstance(identifier, str):
                    continue
                result.append(_source_from_value(
                    self.scene_id, f"sequence:{identifier}", identifier, "sequence",
                    (*sequences_path, identifier), value, references.get(identifier, ()),
                ))
        elif sequences is not MISSING:
            result.append(DialogueSourcePresentation(
                "sequences_invalid", "Named Sequences (invalid)", "invalid",
                sequences_path or _canonical_sequences_path(self.mapping), (), (), type(sequences).__name__,
            ))
        return tuple(result)

    def _references_for(self, source_id: str) -> tuple[str, ...]:
        refs: list[str] = []
        config = _exploration_config(self.mapping)
        entries = config.get("dialog", []) if isinstance(config, Mapping) else []
        if source_id == "scene_entry" and isinstance(entries, Sequence) and not isinstance(entries, (str, bytes, Mapping)):
            refs.append("scene entry")
        return tuple(refs)

    def _sequence_references(self) -> dict[str, tuple[str, ...]]:
        names = self._sequence_names()
        return {name: dialogue_sequence_references(self.mapping, name) for name in names}

    def _sequence_names(self) -> tuple[str, ...]:
        path = _authored_path(self.mapping, "dialogue_sequences")
        value = _path_value(self.mapping, path)
        return tuple(str(key) for key in value) if isinstance(value, Mapping) else ()


def _source_from_value(
    scene_id: str,
    source_id: str,
    label: str,
    kind: str,
    path: PropertyPath,
    value: Any,
    referenced_by: Sequence[str],
    *,
    reference_names: Sequence[str] = (),
) -> DialogueSourcePresentation:
    entries: list[DialogueEntryPresentation] = []
    shape = _shape(value)
    if isinstance(value, list):
        for index, item in enumerate(value):
            entries.extend(_entry_from_value(scene_id, source_id, path + (index,), index, item, reference_names=reference_names))
    elif isinstance(value, str):
        entries.extend(_entry_from_value(scene_id, source_id, path, 0, value, reference_names=reference_names))
    elif isinstance(value, Mapping):
        text_key = "text" if "text" in value else ("dialog" if "dialog" in value else None)
        text_value = value.get(text_key) if text_key is not None else MISSING
        if isinstance(text_value, list):
            for index, item in enumerate(text_value):
                entries.extend(_entry_from_value(scene_id, source_id, path + (text_key, index), index, item, reference_names=reference_names))
        else:
            entries.extend(_entry_from_value(scene_id, source_id, path, 0, value, reference_names=reference_names))
    elif value is not MISSING and value is not None:
        entries.extend(_entry_from_value(
            scene_id, source_id, path, 0, value, supported=False,
            reason=f"Unsupported dialogue value: {type(value).__name__}", reference_names=reference_names,
        ))
    return DialogueSourcePresentation(
        source_id, label, kind, path, tuple(entries), tuple(referenced_by), shape
    )


def _entry_from_value(
    scene_id: str,
    source_id: str,
    path: PropertyPath,
    index: int,
    value: Any,
    *,
    supported: bool = True,
    reason: str | None = None,
    reference_names: Sequence[str] = (),
) -> tuple[DialogueEntryPresentation, ...]:
    selection = DialogueEntrySelection(scene_id, source_id, path, index)
    if isinstance(value, str):
        return (DialogueEntryPresentation(selection, value, path, value),)
    if isinstance(value, Mapping):
        text_key = "text" if "text" in value else ("dialog" if "dialog" in value else None)
        text = value.get(text_key) if text_key is not None else None
        if source_id == "scene_entry" and text_key == "dialog" and isinstance(text, str) and reference_names:
            return (DialogueEntryPresentation(
                selection, None, None, value, False,
                f"References named sequence {text!r}; edit that sequence instead.",
                value.get("conditions", value.get("condition", MISSING)), value,
                present_dialogue_actions(value.get("actions"), path + ("actions",)),
            ),)
        if isinstance(text, str):
            condition = value.get("conditions", value.get("condition", MISSING))
            return (DialogueEntryPresentation(
                selection, text, path + (text_key,), path and value,
                True, None, condition, value,
                present_dialogue_actions(value.get("actions"), path + ("actions",)),
            ),)
        if text_key is not None and isinstance(text, list):
            return tuple(
                _entry_from_value(scene_id, source_id, path + (text_key, offset), offset, item)
                for offset, item in enumerate(text)
            )
        summary = ", ".join(str(key) for key in value.keys()) or "mapping"
        return (DialogueEntryPresentation(
            selection, None, None, value, False,
            reason or f"No editable text field (authored keys: {summary})",
            value.get("conditions", value.get("condition", MISSING)), value,
            present_dialogue_actions(value.get("actions"), path + ("actions",)),
        ),)
    return (DialogueEntryPresentation(
        selection, None, None, value, False,
        reason or f"Unsupported dialogue entry: {type(value).__name__}",
    ),)


def _exploration_config(mapping: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = mapping.get("exploration")
    if isinstance(raw, Mapping):
        return raw
    return mapping


def _authored_path(mapping: Mapping[str, Any], key: str) -> PropertyPath | None:
    exploration = mapping.get("exploration")
    if isinstance(exploration, Mapping) and key in exploration:
        return ("exploration", key)
    if key in mapping:
        return (key,)
    return None


def _canonical_dialog_path(mapping: Mapping[str, Any]) -> PropertyPath:
    return ("exploration", "dialog") if "exploration" not in mapping or isinstance(mapping.get("exploration"), Mapping) else ("dialog",)


def _canonical_sequences_path(mapping: Mapping[str, Any]) -> PropertyPath:
    return ("exploration", "dialogue_sequences") if "exploration" not in mapping or isinstance(mapping.get("exploration"), Mapping) else ("dialogue_sequences",)


def _path_value(mapping: Any, path: PropertyPath | None) -> Any:
    if path is None:
        return MISSING
    current = mapping
    for component in path:
        if isinstance(current, Mapping) and component in current:
            current = current[component]
        elif isinstance(current, list) and isinstance(component, int) and 0 <= component < len(current):
            current = current[component]
        else:
            return MISSING
    return current


def _shape(value: Any) -> str:
    if value is MISSING:
        return "missing"
    if isinstance(value, list):
        return "list"
    if isinstance(value, Mapping):
        return "mapping"
    if isinstance(value, str):
        return "string"
    return type(value).__name__


def present_dialogue_actions(
    raw: Any,
    path: PropertyPath,
    *,
    scope: ActionScope | str = ActionScope.EXPLORATION,
) -> tuple[DialogueActionPresentation, ...]:
    """Adapt an authored action list for compact editor presentation."""

    selected_scope = ActionScope(scope)
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, Mapping)):
        return ()
    result: list[DialogueActionPresentation] = []
    for index, item in enumerate(raw):
        action_path = path + (index,)
        action_type: str | None = None
        summary = _safe_summary(item)
        supported = False
        editable = False
        reason: str | None = None
        display_name = "Unsupported Action"
        try:
            adapted = parse_action(item, selected_scope)
        except (TypeError, ValueError) as exc:
            reason = str(exc)
        else:
            action_type = adapted.action_type
            spec = action_editor_spec(action_type, selected_scope)
            if spec is not None:
                supported = True
                display_name = spec.display_name
                editable = adapted.form is ActionForm.TYPED and isinstance(item, Mapping)
                if isinstance(adapted.payload, Mapping):
                    values = []
                    for field_spec in spec.fields:
                        if field_spec.key in adapted.payload:
                            values.append(f"{field_spec.display_name} = {adapted.payload[field_spec.key]!r}")
                    summary = ", ".join(values) if values else "No parameters"
                elif adapted.payload is not None:
                    summary = repr(adapted.payload)
                if not editable:
                    reason = "Legacy action syntax is preserved and read-only."
            else:
                reason = f"No editor metadata for action type {action_type!r}."
                display_name = str(action_type or "Unsupported Action").replace("_", " ").title()
        result.append(DialogueActionPresentation(
            action_path, index, item, action_type, display_name, summary,
            supported, editable, selected_scope, reason,
        ))
    return tuple(result)


def dialogue_sequence_references(mapping: Mapping[str, Any], sequence_id: str) -> tuple[str, ...]:
    """Find known local references without attempting a project-wide graph."""

    target = str(sequence_id)
    config = _exploration_config(mapping)
    result: list[str] = []
    entries = config.get("dialog", []) if isinstance(config, Mapping) else []
    if isinstance(entries, Sequence) and not isinstance(entries, (str, bytes, Mapping)):
        for index, entry in enumerate(entries):
            if isinstance(entry, Mapping):
                for key in ("sequence", "dialog"):
                    if entry.get(key) == target:
                        result.append(f"scene entry {index + 1}")
    events = config.get("look_events", {}) if isinstance(config, Mapping) else {}
    if isinstance(events, Mapping):
        for event_id, event in events.items():
            actions = event.get("actions") if isinstance(event, Mapping) else None
            if _contains_dialogue_reference(actions, target):
                result.append(f"look event: {event_id}")
    for namespace in ("objects", "look_regions"):
        values = config.get(namespace, []) if isinstance(config, Mapping) else []
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes, Mapping)):
            for index, value in enumerate(values):
                if _contains_dialogue_reference(value, target):
                    identifier = value.get("id", index + 1) if isinstance(value, Mapping) else index + 1
                    result.append(f"{namespace[:-1]}: {identifier}")
    sequences = config.get("dialogue_sequences", {}) if isinstance(config, Mapping) else {}
    if isinstance(sequences, Mapping):
        for sequence in sequences.values():
            actions = sequence.get("actions") if isinstance(sequence, Mapping) else None
            if _contains_dialogue_reference(actions, target):
                result.append("named sequence action")
    return tuple(dict.fromkeys(result))


def _contains_dialogue_reference(value: Any, target: str) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in {"sequence", "dialog"} and child == target:
                return True
            if _contains_dialogue_reference(child, target):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, Mapping)):
        return any(_contains_dialogue_reference(child, target) for child in value)
    return False


def _safe_summary(value: Any) -> str:
    try:
        import json
        return json.dumps(value, indent=2, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(value)


__all__ = [
    "DialogueDocumentModel",
    "DialogueEntryPresentation",
    "DialogueEntrySelection",
    "DialogueActionPresentation",
    "DialogueSourcePresentation",
    "dialogue_sequence_references",
    "present_dialogue_actions",
]
