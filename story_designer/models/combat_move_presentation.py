"""Qt-independent presentation of one global combat move.

The model is deliberately a view over an authored mapping.  It does not
normalize a move, flatten QTE sections, or write inherited values back into a
difficulty level.  Runtime resolution remains in ``move_progression``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any

from engine.battle.move_progression import (
    DEFAULT_SKILL_PROGRESSION,
    SkillProgressionConfig,
    resolve_combat_move,
)
from engine.battle.qte import (
    QTEFieldSpec,
    QTEEditorSpec,
    QTE_EDITOR_SPECS,
    QTE_TYPE_ALIASES,
    canonical_qte_type,
    qte_editor_spec,
)
from engine.story_core import ContentKind, Diagnostics, StoryProject
from engine.story_core.schema import MISSING


PropertyPath = tuple[str | int, ...]


@dataclass(frozen=True)
class CombatMoveElementSelection:
    move_id: str
    kind: str
    path: PropertyPath = ()
    level: int | None = None


@dataclass(frozen=True)
class CombatMoveField:
    """One generated QTE/progression field and its authored/effective values."""

    spec: QTEFieldSpec | None
    key: str
    label: str
    path: PropertyPath
    authored_value: Any = MISSING
    effective_value: Any = MISSING
    group: str = "Parameters"
    supported: bool = True
    description: str = ""
    unknown: bool = False

    @property
    def is_authored(self) -> bool:
        """Whether this value is authored at the selected difficulty level."""

        return self.authored_value is not MISSING and len(self.path) >= 2 and self.path[0] == "difficulty_levels"

    @property
    def is_inherited(self) -> bool:
        return self.authored_value is not MISSING and not self.is_authored


@dataclass(frozen=True)
class CombatMoveSection:
    id: str
    label: str
    summary: str
    path: PropertyPath = ()
    elements: tuple[CombatMoveElementSelection, ...] = ()


@dataclass(frozen=True)
class MoveReferenceUsage:
    kind: str
    identifier: str
    source: str
    path: PropertyPath = ()
    label: str = ""


class CombatMoveDocumentModel:
    """Presentation model for modern, legacy, and opaque move documents."""

    COMMON_QTE_KEYS = {
        "type", "duration", "difficulty", "thresholds", "damage_multipliers",
        "multipliers", "label", "sound", "animation", "allowed_inputs",
        "parameters", "pattern_parameters", "tuning_parameters",
    }
    COMMON_FIELDS = (
        ("duration", "Duration", "Timing"),
        ("difficulty", "Runtime difficulty", "Timing"),
        ("thresholds", "Score thresholds", "Scoring"),
        ("damage_multipliers", "Damage multipliers", "Scoring"),
        ("label", "QTE label", "Animation"),
        ("sound", "Sound", "Animation"),
        ("animation", "Animation", "Animation"),
        ("allowed_inputs", "Allowed inputs", "Input"),
    )

    def __init__(
        self,
        move_id: str,
        mapping: Mapping[str, Any],
        project: StoryProject | None = None,
        diagnostics: Diagnostics | None = None,
        *,
        skill_progression: Mapping[str, Any] | None = None,
    ) -> None:
        self.move_id = str(move_id)
        self.mapping = mapping
        self.project = project
        self.diagnostics = diagnostics or Diagnostics()
        self.skill_progression_authored = deepcopy(dict(skill_progression or {}))
        self._sections = self._discover_sections()

    @property
    def sections(self) -> tuple[CombatMoveSection, ...]:
        return self._sections

    def section(self, section_id: str) -> CombatMoveSection | None:
        return next((section for section in self.sections if section.id == section_id), None)

    @property
    def authored_levels(self) -> tuple[int, ...]:
        raw = self.mapping.get("difficulty_levels")
        if not isinstance(raw, Mapping):
            return ()
        return tuple(sorted(key for key in raw if isinstance(key, int) and not isinstance(key, bool)))

    @property
    def levels(self) -> tuple[int, ...]:
        """Visible runtime levels, retaining a legacy move's implicit level 1."""

        return self.authored_levels or (1,)

    available_levels = levels

    @property
    def tutorial_level(self) -> int | None:
        return 0 if 0 in self.levels else None

    @property
    def is_legacy(self) -> bool:
        return "difficulty_levels" not in self.mapping

    @property
    def qte_type_authored(self) -> str | None:
        value = self._authored_qte_value("type")
        if isinstance(value, str) and value:
            return value
        pattern = self.mapping.get("pattern")
        return str(pattern) if isinstance(pattern, str) and pattern else None

    @property
    def qte_type(self) -> str | None:
        value = self.qte_type_authored
        return canonical_qte_type(value) if value is not None else None

    @property
    def qte_spec(self) -> QTEEditorSpec | None:
        return qte_editor_spec(self.qte_type or "")

    @property
    def qte_is_supported(self) -> bool:
        return self.qte_spec is not None

    @property
    def qte_source_path(self) -> PropertyPath | None:
        for path in (("common", "qte"), ("qte",)):
            if _path_value(self.mapping, path) is not MISSING:
                return path
        return None

    def authored_level(self, level: int) -> Mapping[str, Any]:
        if self.is_legacy:
            return {}
        value = self.mapping.get("difficulty_levels", {}).get(level, {})
        return value if isinstance(value, Mapping) else {}

    def effective_level(self, level: int) -> dict[str, Any]:
        return resolve_combat_move(self.mapping, int(level))

    def effective_preview(self, level: int) -> str:
        try:
            return json.dumps(self.effective_level(level), indent=2, sort_keys=True, default=str)
        except Exception as exc:  # validation diagnostics remain visible in UI
            return f"Unable to resolve difficulty {level}: {exc}"

    def qte_fields(self, level: int = 1, *, include_unknown: bool = True) -> tuple[CombatMoveField, ...]:
        fields: list[CombatMoveField] = []
        spec = self.qte_spec
        if spec is not None:
            fields.extend(self._fields_for_specs(spec.fields, level))
        fields.extend(self._common_qte_fields(level))
        if include_unknown:
            fields.extend(self._unknown_qte_fields(level, {field.key for field in fields}))
        return tuple(fields)

    def progression_values(self) -> tuple[dict[str, Any], dict[str, Any]]:
        authored = deepcopy(self.skill_progression_authored)
        effective = deepcopy(DEFAULT_SKILL_PROGRESSION)
        effective.update(authored)
        return authored, effective

    def progression_config(self) -> SkillProgressionConfig | None:
        try:
            return SkillProgressionConfig.from_data(self.skill_progression_authored or None)
        except Exception:
            return None

    def references(self) -> tuple[MoveReferenceUsage, ...]:
        if self.project is None:
            return ()
        return discover_move_references(self.project, self.move_id)

    def diagnostics_for(self, path: PropertyPath = ()) -> tuple[Any, ...]:
        return tuple(item for item in self.diagnostics if _related(item.path, path))

    def refresh(self, mapping: Mapping[str, Any], *, skill_progression: Mapping[str, Any] | None = None) -> None:
        self.mapping = mapping
        if skill_progression is not None:
            self.skill_progression_authored = deepcopy(dict(skill_progression))
        self._sections = self._discover_sections()

    def _discover_sections(self) -> tuple[CombatMoveSection, ...]:
        levels = ", ".join(f"{level} (Tutorial)" if level == 0 else str(level) for level in self.levels)
        qte_label = self.qte_type_authored or "Unknown / legacy"
        return (
            CombatMoveSection("overview", "Overview", f"Move ID: {self.move_id}\nQTE: {qte_label}\nLevels: {levels}"),
            CombatMoveSection("difficulty_levels", "Difficulty Levels", f"{len(self.authored_levels)} authored level(s). Inherited values are not materialized."),
            CombatMoveSection("qte", "QTE", f"{qte_label}: {len(self.qte_fields(self.levels[0]))} metadata field(s)", self.qte_source_path or ()),
            CombatMoveSection("skill_progression", "Skill Progression", "Global SkillProgressionConfig defaults plus authored overrides."),
            CombatMoveSection("references", "Usage / References", f"{len(self.references())} discovered reference(s)."),
            CombatMoveSection("advanced", "Advanced", "Unknown and legacy payloads remain in the authored mapping."),
        )

    def _fields_for_specs(self, specs: Sequence[QTEFieldSpec], level: int) -> list[CombatMoveField]:
        result: list[CombatMoveField] = []
        for spec in specs:
            path, authored = self._field_authored_location(spec.key, level, spec)
            effective = self._effective_qte_value(spec.key, level)
            result.append(CombatMoveField(spec, spec.key, spec.label, path, authored, effective, spec.group, True, spec.description))
        return result

    def _common_qte_fields(self, level: int) -> list[CombatMoveField]:
        result = []
        spec_keys = {field.key for field in (self.qte_spec.fields if self.qte_spec is not None else ())}
        for key, label, group in self.COMMON_FIELDS:
            if key in spec_keys:
                continue
            path, authored = self._field_authored_location(key, level, None)
            effective = self._effective_qte_value(key, level)
            value_type = "list" if key == "allowed_inputs" else "asset" if key in {"sound", "animation"} else "float" if key == "duration" else "mapping" if key in {"thresholds", "damage_multipliers"} else "string"
            common_spec = QTEFieldSpec(key, label, "Runtime QTE common field.", value_type=value_type, group=group, asset_kind="sfx" if key == "sound" else "animations" if key == "animation" else None, editor_hint="scalar_list" if value_type == "list" else None)
            result.append(CombatMoveField(common_spec, key, label, path, authored, effective, group, True, common_spec.description))
        return result

    def _field_authored_location(self, key: str, level: int, spec: QTEFieldSpec | None) -> tuple[PropertyPath, Any]:
        candidates: list[PropertyPath] = []
        level_qte = ("difficulty_levels", level, "qte")
        common_qte = self.qte_source_path or ("common", "qte")
        qte_paths: list[PropertyPath] = [level_qte, common_qte]
        if self.qte_source_path is None:
            qte_paths.extend([("pattern_config",), ()])
        for qte_path in qte_paths:
            raw = _path_value(self.mapping, qte_path)
            if qte_path and not isinstance(raw, Mapping):
                continue
            for section in ("parameters", "pattern_parameters", "tuning_parameters", ""):
                path = qte_path + ((section, key) if section else (key,))
                value = _path_value(self.mapping, path)
                if value is not MISSING:
                    return path, deepcopy(value)
            candidates.append(qte_path + ((spec.authored_section if spec else "tuning_parameters"), key))
        if candidates:
            return candidates[0], MISSING
        return (("difficulty_levels", level, "qte", spec.authored_section if spec else "tuning_parameters", key), MISSING)

    def _effective_qte_value(self, key: str, level: int) -> Any:
        try:
            resolved = self.effective_level(level)
        except Exception:
            return MISSING
        qte = resolved.get("qte")
        if not isinstance(qte, Mapping):
            if key in resolved:
                return deepcopy(resolved[key])
            legacy = resolved.get("pattern_config")
            return deepcopy(legacy.get(key, MISSING)) if isinstance(legacy, Mapping) else MISSING
        if key in qte:
            return deepcopy(qte[key])
        params = qte.get("parameters")
        return deepcopy(params.get(key, MISSING)) if isinstance(params, Mapping) else MISSING

    def _authored_qte_value(self, key: str) -> Any:
        path = self.qte_source_path
        value = _path_value(self.mapping, path + (key,)) if path else MISSING
        if value is not MISSING:
            return value
        common = self.mapping.get("common")
        return common.get(key, MISSING) if isinstance(common, Mapping) else MISSING

    def _unknown_qte_fields(self, level: int, known: set[str]) -> list[CombatMoveField]:
        result: list[CombatMoveField] = []
        roots = [self.qte_source_path, ("difficulty_levels", level, "qte")]
        if isinstance(self.mapping.get("pattern_config"), Mapping):
            roots.append(("pattern_config",))
        for root_path in filter(None, roots):
            raw = _path_value(self.mapping, root_path)
            if not isinstance(raw, Mapping):
                continue
            for key, value in raw.items():
                if key in self.COMMON_QTE_KEYS or key in known:
                    continue
                result.append(CombatMoveField(None, str(key), str(key).replace("_", " ").title(), root_path + (key,), deepcopy(value), deepcopy(value), "Advanced", False, "Unsupported QTE payload is preserved.", True))
        return result


def discover_move_references(project: StoryProject, move_id: str) -> tuple[MoveReferenceUsage, ...]:
    """Find useful read-only battle, item, and profile references."""

    result: list[MoveReferenceUsage] = []
    profile = getattr(project, "player_profile", None)
    profile_raw = profile.to_mapping() if profile is not None else {}
    known = profile_raw.get("known_moves", []) if isinstance(profile_raw, Mapping) else []
    if _sequence_contains_move(known, move_id):
        result.append(MoveReferenceUsage("player profile", move_id, str(getattr(profile, "source", "player.yaml")), ("known_moves",), "Starting move"))
    for item_id, item in getattr(project, "items", {}).items():
        raw = item.to_mapping()
        combat = raw.get("combat") if isinstance(raw, Mapping) else None
        grants = combat.get("move_grants", []) if isinstance(combat, Mapping) else []
        if _sequence_contains_move(grants, move_id):
            result.append(MoveReferenceUsage("item", str(item_id), str(item.source), ("combat", "move_grants"), "Move grant"))
    for battle_id, battle in getattr(project, "battles", {}).items():
        raw = battle.to_mapping()
        values = tuple(_battle_move_reference_values(raw))
        if not values and getattr(project, "moves", {}).get(move_id) is not None:
            # Modern battles consume the global move registry when they do
            # not author a local player-move subset.
            result.append(MoveReferenceUsage("battle", str(battle_id), str(battle.source), (), "All global moves"))
        for path, value in values:
            if _sequence_contains_move(value, move_id):
                result.append(MoveReferenceUsage("battle", str(battle_id), str(battle.source), path, "Battle move reference"))
    return tuple(result)


def _battle_move_reference_values(raw: Mapping[str, Any]):
    for key in ("initial_player_moves", "player_moves"):
        if key in raw:
            yield (key,), raw[key]
    enemy = raw.get("enemy")
    if isinstance(enemy, Mapping) and isinstance(enemy.get("moves"), Sequence):
        yield ("enemy", "moves"), enemy["moves"]


def _sequence_contains_move(value: Any, move_id: str) -> bool:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, Mapping)):
        return False
    return any((entry.get("id") if isinstance(entry, Mapping) else entry) == move_id for entry in value)


def _path_value(mapping: Any, path: PropertyPath) -> Any:
    current = mapping
    for component in path:
        if isinstance(current, Mapping) and component in current:
            current = current[component]
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes, Mapping)) and isinstance(component, int) and 0 <= component < len(current):
            current = current[component]
        else:
            return MISSING
    return current


def _related(path: Any, prefix: PropertyPath) -> bool:
    actual = tuple(path or ())
    return not prefix or actual[:len(prefix)] == prefix or prefix[:len(actual)] == actual


__all__ = [
    "CombatMoveDocumentModel", "CombatMoveElementSelection", "CombatMoveField",
    "CombatMoveSection", "MoveReferenceUsage", "discover_move_references",
]
