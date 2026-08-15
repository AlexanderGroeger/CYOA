"""Qt-independent presentation of authored battle documents.

The battle runtime accepts both the original ``enemy.moves`` form and the
modern move/pattern/phase form.  This module deliberately presents those
forms without normalising them or importing any runtime controller.  Paths
always point at the current Designer working mapping so commands can edit the
document without reconstructing it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import json
from typing import Any

from engine.story_core import Diagnostics, StoryProject

PropertyPath = tuple[str | int, ...]


@dataclass(frozen=True)
class BattleElementSelection:
    """Stable editor identity for one nested battle element."""

    battle_id: str
    kind: str
    path: PropertyPath
    identifier: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "battle_id", str(self.battle_id))
        object.__setattr__(self, "kind", str(self.kind))
        object.__setattr__(self, "path", tuple(self.path))
        object.__setattr__(self, "identifier", str(self.identifier) if self.identifier is not None else None)

    @property
    def id(self) -> str | None:
        return self.identifier


@dataclass(frozen=True)
class BattleElementPresentation:
    selection: BattleElementSelection
    label: str
    summary: str
    authored: Any = None
    supported: bool = True
    editable: bool = False
    unsupported_reason: str | None = None
    diagnostics: tuple[Any, ...] = ()


@dataclass(frozen=True)
class BattleSection:
    id: str
    label: str
    path: PropertyPath
    summary: str
    elements: tuple[BattleElementPresentation, ...] = ()
    supported: bool = True
    diagnostics: tuple[Any, ...] = ()


class BattleDocumentModel:
    """Read-only structural adapter over one battle working mapping."""

    def __init__(
        self,
        battle_id: str,
        mapping: Mapping[str, Any],
        project: StoryProject | None = None,
        diagnostics: Diagnostics | None = None,
    ) -> None:
        self.battle_id = str(battle_id)
        self.mapping = mapping
        self.project = project
        self.diagnostics = diagnostics or Diagnostics()
        self._sections = self._discover()

    @property
    def sections(self) -> tuple[BattleSection, ...]:
        return self._sections

    def section(self, section_id: str) -> BattleSection | None:
        return next((section for section in self._sections if section.id == section_id), None)

    def element(self, selection: BattleElementSelection | None) -> BattleElementPresentation | None:
        if selection is None:
            return None
        for section in self._sections:
            for element in section.elements:
                if element.selection == selection:
                    return element
        return None

    def diagnostics_for(self, value: BattleSection | BattleElementSelection | PropertyPath) -> tuple[Any, ...]:
        """Return diagnostics whose source/path belongs to a section or element."""

        if isinstance(value, BattleSection):
            prefix = value.path
        elif isinstance(value, BattleElementSelection):
            prefix = value.path
        else:
            prefix = tuple(value)
        return tuple(item for item in self.diagnostics if _path_related(item.path, prefix))

    def overview(self) -> BattleSection:
        return self.section("overview") or self._sections[0]

    @property
    def global_move_ids(self) -> tuple[str, ...]:
        """Project-index move IDs, including IDs not currently resolved."""

        if self.project is None:
            return ()
        return tuple(str(identifier) for identifier in getattr(self.project, "moves", {}).keys())

    @property
    def item_ids(self) -> tuple[str, ...]:
        if self.project is None:
            return ()
        return tuple(str(identifier) for identifier in getattr(self.project, "items", {}).keys())

    @property
    def scene_ids(self) -> tuple[str, ...]:
        if self.project is None:
            return ()
        return tuple(str(identifier) for identifier in getattr(self.project, "scenes", {}).keys())

    def _discover(self) -> tuple[BattleSection, ...]:
        sections: list[BattleSection] = []
        sections.append(self._overview_section())
        if isinstance(self.mapping.get("enemy"), Mapping):
            sections.append(self._enemy_section())

        player_paths = [key for key in ("initial_player_moves", "player_moves") if key in self.mapping]
        if player_paths or self.project is not None and getattr(self.project, "moves", {}):
            sections.append(self._reference_section(
                "player_moves", "Player Moves", tuple(player_paths[0] if player_paths else "initial_player_moves" for _ in [0]),
                self._player_move_elements(),
                "Global combat move references used at battle start.",
            ))

        if "enemy_moves" in self.mapping or _legacy_moves(self.mapping) is not None:
            path = ("enemy_moves",) if "enemy_moves" in self.mapping else ("enemy", "moves")
            sections.append(self._collection_section("enemy_moves", "Enemy Moves", path, "Enemy move selection and references.", "enemy_move"))

        defense_key = _first_key(self.mapping, "defense_sequences", "enemy_patterns")
        if defense_key is not None:
            sections.append(self._collection_section("defense", "Defense", (defense_key,), "Configured defense sequences/patterns; nested payloads are preserved.", "defense_pattern"))

        if "phases" in self.mapping:
            sections.append(self._collection_section("phases", "Phases", ("phases",), "Phase triggers and authored phase actions.", "phase"))
        if "dialogue" in self.mapping:
            sections.append(self._collection_section("dialogue", "Dialogue", ("dialogue",), "Battle-triggered dialogue blocks.", "dialogue"))

        reward_paths = _reward_paths(self.mapping)
        if reward_paths:
            elements: list[BattleElementPresentation] = []
            for outcome, path in reward_paths:
                value = _path_value(self.mapping, path)
                for index, reward in enumerate(_entries(value)):
                    element_path = path + (index,)
                    elements.append(self._element("reward", element_path, reward, f"{outcome.title()} reward {index + 1}"))
                if not _entries(value):
                    elements.append(self._element("reward", path, value, f"{outcome.title()} rewards"))
            sections.append(BattleSection("rewards", "Rewards", reward_paths[0][1], "Victory/defeat rewards and outcome payloads.", tuple(elements), diagnostics=self.diagnostics_for(reward_paths[0][1])))

        if "escape" in self.mapping:
            sections.append(BattleSection("escape", "Escape", ("escape",), _escape_summary(self.mapping["escape"]), diagnostics=self.diagnostics_for(("escape",))))
        if "on_lose" in self.mapping:
            sections.append(BattleSection("lose", "Lose Behavior", ("on_lose",), _lose_summary(self.mapping["on_lose"]), diagnostics=self.diagnostics_for(("on_lose",))))
        return tuple(sections)

    def _overview_section(self) -> BattleSection:
        enemy = self.mapping.get("enemy")
        enemy_name = enemy.get("name", "Unknown") if isinstance(enemy, Mapping) else "Missing"
        phases = _entries(self.mapping.get("phases"))
        enemy_moves = _entries(self.mapping.get("enemy_moves")) or _entries(_legacy_moves(self.mapping))
        defense = _entries(_path_value(self.mapping, (_first_key(self.mapping, "defense_sequences", "enemy_patterns") or "")))
        initial = self.mapping.get("initial_player_moves", "all global moves")
        rewards = len(_reward_paths(self.mapping))
        lines = [
            f"Enemy: {enemy_name}",
            f"Arena: {_summary(self.mapping.get('arena', 'default'))}",
            f"Initial player moves: {_summary(initial)}",
            f"Initial enemy moves: {len(_entries(self.mapping.get('initial_enemy_moves'))) or len(enemy_moves)}",
            f"Enemy moves: {len(enemy_moves)}",
            f"Defense patterns/sequences: {len(defense)}",
            f"Phases: {len(phases)}",
            f"Escape: {_escape_summary(self.mapping.get('escape')) if 'escape' in self.mapping else 'not configured'}",
            f"Rewards: {'configured' if rewards else 'none'}",
            f"Lose behavior: {_lose_summary(self.mapping.get('on_lose')) if 'on_lose' in self.mapping else 'default'}",
        ]
        return BattleSection("overview", "Overview", (), "\n".join(lines), diagnostics=self.diagnostics)

    def _enemy_section(self) -> BattleSection:
        enemy = self.mapping.get("enemy")
        elements = []
        if isinstance(enemy, Mapping):
            for key in ("name", "hp", "attack", "defense", "sprite", "animation"):
                if key in enemy:
                    elements.append(self._element("enemy", ("enemy", key), enemy[key], key.replace("_", " ").title(), editable=key in {"name", "hp", "attack", "defense", "sprite", "animation"}))
        return BattleSection("enemy", "Enemy", ("enemy",), _summary(enemy), tuple(elements), diagnostics=self.diagnostics_for(("enemy",)))

    def _player_move_elements(self) -> tuple[BattleElementPresentation, ...]:
        path = ("initial_player_moves",) if "initial_player_moves" in self.mapping else ("player_moves",)
        value = self.mapping.get(path[0])
        editable = path[0] in self.mapping
        if value is None and self.project is not None:
            profile = getattr(self.project, "player_profile", None)
            value = getattr(profile, "known_moves", None) or self.global_move_ids
        return tuple(self._element("player_move", path + (index,), item, str(item), editable=editable) for index, item in enumerate(_entries(value)))

    def _reference_section(self, section_id: str, label: str, path: PropertyPath, elements: tuple[BattleElementPresentation, ...], summary: str) -> BattleSection:
        return BattleSection(section_id, label, path, summary, elements, diagnostics=self.diagnostics_for(path))

    def _collection_section(self, section_id: str, label: str, path: PropertyPath, summary: str, kind: str) -> BattleSection:
        raw = _path_value(self.mapping, path)
        elements = []
        for index, item in enumerate(_entries(raw)):
            identifier = item.get("id") if isinstance(item, Mapping) else None
            label_value = str(identifier or (item.get("name") if isinstance(item, Mapping) else f"Entry {index + 1}"))
            elements.append(self._element(kind, path + (index,), item, label_value))
        return BattleSection(section_id, label, path, f"{len(elements)} configured entries. {summary}", tuple(elements), diagnostics=self.diagnostics_for(path))

    def _element(self, kind: str, path: PropertyPath, value: Any, label: str, *, editable: bool = False) -> BattleElementPresentation:
        identifier = value.get("id") if isinstance(value, Mapping) and isinstance(value.get("id"), str) else None
        supported = isinstance(value, (Mapping, str, int, float, bool))
        return BattleElementPresentation(
            BattleElementSelection(self.battle_id, kind, path, identifier),
            str(label), _summary(value), value, supported, editable,
            None if supported else f"Unsupported authored value: {type(value).__name__}",
            self.diagnostics_for(path),
        )


def _first_key(mapping: Mapping[str, Any], *keys: str) -> str | None:
    return next((key for key in keys if key in mapping), None)


def _legacy_moves(mapping: Mapping[str, Any]) -> Any:
    enemy = mapping.get("enemy")
    return enemy.get("moves") if isinstance(enemy, Mapping) and "moves" in enemy else None


def _reward_paths(mapping: Mapping[str, Any]) -> tuple[tuple[str, PropertyPath], ...]:
    result = []
    for outcome in ("victory", "defeat"):
        value = mapping.get(outcome)
        if isinstance(value, Mapping) and "rewards" in value:
            result.append((outcome, (outcome, "rewards")))
    return tuple(result)


def _entries(value: Any) -> tuple[Any, ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, Mapping)):
        return tuple(value)
    return ()


def _path_value(mapping: Mapping[str, Any], path: PropertyPath) -> Any:
    current: Any = mapping
    for component in path:
        if isinstance(current, Mapping) and component in current:
            current = current[component]
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes, Mapping)) and isinstance(component, int) and component < len(current):
            current = current[component]
        else:
            return None
    return current


def _path_related(path: Any, prefix: PropertyPath) -> bool:
    path = tuple(path or ())
    return not prefix or path[:len(prefix)] == prefix or prefix[:len(path)] == path


def _summary(value: Any) -> str:
    try:
        return json.dumps(value, indent=2, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(value)


def _escape_summary(value: Any) -> str:
    if isinstance(value, Mapping):
        enabled = value.get("enabled", False)
        details = [f"enabled={bool(enabled)}"]
        for key in ("chance", "cost", "destination"):
            if key in value:
                details.append(f"{key}={value[key]!r}")
        return ", ".join(details)
    return f"enabled={bool(value)}" if isinstance(value, bool) else _summary(value)


def _lose_summary(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(value.get("type", "configured"))
    return _summary(value)


__all__ = [
    "BattleDocumentModel",
    "BattleElementPresentation",
    "BattleElementSelection",
    "BattleSection",
]
