"""Ephemeral developer-test state for fresh runtime launches.

The objects in this module are deliberately independent of Qt, pygame, YAML,
and authored project documents.  They are only a transport and application
boundary for the Designer's launch-time state injection.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any

from engine.errors import EngineError


class DeveloperTestConfigError(EngineError, ValueError):
    """A developer test configuration is malformed or cannot be applied."""


_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_ALLOWED_KEYS = frozenset({"scene", "scene_id", "flags", "variables", "inventory", "stats"})
_BATTLE_ALLOWED_KEYS = _ALLOWED_KEYS | frozenset({"battle", "battle_id"})
_QTE_ALLOWED_KEYS = frozenset({"qte_move", "move_id", "qte_level", "difficulty_level", "seed"})
_JSON_SCALARS = (str, int, float, bool)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DeveloperTestConfigError(f"{label} must be an object")
    result = dict(value)
    for key in result:
        if not isinstance(key, str) or not key:
            raise DeveloperTestConfigError(f"{label} keys must be non-empty strings")
    return result


def _validate_name(name: str, label: str) -> None:
    if not _NAME_RE.fullmatch(name):
        raise DeveloperTestConfigError(
            f"{label} {name!r} must start with a letter and contain only letters, numbers, and underscores"
        )


def _validate_scalar(value: Any, label: str) -> None:
    if value is not None and (not isinstance(value, _JSON_SCALARS) or isinstance(value, (list, dict))):
        raise DeveloperTestConfigError(f"{label} must be a JSON scalar (string, integer, float, boolean, or null)")


@dataclass
class SceneTestConfiguration:
    """Safe, Qt-independent launch-time overrides for a fresh ``GameState``."""

    scene_id: str | None = None
    flags: dict[str, bool] = field(default_factory=dict)
    variables: dict[str, Any] = field(default_factory=dict)
    inventory: dict[str, int] = field(default_factory=dict)
    stats: dict[str, int | float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.flags = dict(self.flags)
        self.variables = dict(self.variables)
        self.inventory = dict(self.inventory)
        self.stats = dict(self.stats)
        self.validate()

    def validate(self, *, known_items: Mapping[str, Any] | set[str] | None = None) -> None:
        if self.scene_id is not None and (not isinstance(self.scene_id, str) or not self.scene_id):
            raise DeveloperTestConfigError("scene_id must be a non-empty string when provided")
        for name, value in self.flags.items():
            if not isinstance(name, str) or not name:
                raise DeveloperTestConfigError("flags keys must be non-empty strings")
            _validate_name(name, "Flag name")
            if not isinstance(value, bool):
                raise DeveloperTestConfigError(f"Flag {name!r} must be boolean")
        for name, value in self.variables.items():
            if not isinstance(name, str) or not name:
                raise DeveloperTestConfigError("variables keys must be non-empty strings")
            _validate_name(name, "Variable name")
            _validate_scalar(value, f"Variable {name!r}")
        for item_id, quantity in self.inventory.items():
            if not isinstance(item_id, str) or not item_id:
                raise DeveloperTestConfigError("inventory keys must be non-empty item IDs")
            if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
                raise DeveloperTestConfigError(f"Inventory quantity for {item_id!r} must be a non-negative integer")
            if known_items is not None and item_id not in known_items:
                raise DeveloperTestConfigError(f"Unknown item in test configuration: {item_id}")
        for name, value in self.stats.items():
            if not isinstance(name, str) or not name:
                raise DeveloperTestConfigError("stats keys must be non-empty strings")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise DeveloperTestConfigError(f"Stat {name!r} must be an integer or float")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        result: dict[str, Any] = {
            "flags": dict(self.flags),
            "variables": dict(self.variables),
            "inventory": dict(self.inventory),
        }
        if self.scene_id is not None:
            result["scene"] = self.scene_id
        if self.stats:
            result["stats"] = dict(self.stats)
        return result

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "SceneTestConfiguration":
        if not isinstance(raw, Mapping):
            raise DeveloperTestConfigError("Developer test configuration root must be an object")
        unknown = set(raw) - _ALLOWED_KEYS
        if unknown:
            raise DeveloperTestConfigError(f"Unknown developer test configuration field(s): {', '.join(sorted(map(str, unknown)))}")
        scene_id = raw.get("scene", raw.get("scene_id"))
        flags = _mapping(raw.get("flags", {}), "flags")
        variables = _mapping(raw.get("variables", {}), "variables")
        inventory = _mapping(raw.get("inventory", {}), "inventory")
        stats = _mapping(raw.get("stats", {}), "stats")
        return cls(scene_id=scene_id, flags=flags, variables=variables, inventory=inventory, stats=stats)

    @classmethod
    def from_json(cls, path: str | Path) -> "SceneTestConfiguration":
        try:
            with Path(path).open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except json.JSONDecodeError as exc:
            raise DeveloperTestConfigError(f"Malformed developer test configuration JSON: {exc.msg}") from exc
        except OSError as exc:
            raise DeveloperTestConfigError(f"Could not read developer test configuration: {exc}") from exc
        return cls.from_dict(raw)

    def write_json(self, path: str | Path) -> None:
        self.validate()
        try:
            with Path(path).open("w", encoding="utf-8") as handle:
                json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
        except OSError as exc:
            raise DeveloperTestConfigError(f"Could not write developer test configuration: {exc}") from exc


@dataclass
class BattleTestConfiguration(SceneTestConfiguration):
    """Launch-time battle selection plus the normal fresh-state overrides.

    Battle tests deliberately wrap the same flags, variables, inventory, and
    numeric stats used by scene tests.  The battle identifier is transport
    metadata only; it never becomes authored story data.
    """

    battle_id: str | None = None

    def validate(self, *, known_items: Mapping[str, Any] | set[str] | None = None) -> None:
        super().validate(known_items=known_items)
        if self.scene_id is not None:
            raise DeveloperTestConfigError("A battle test configuration cannot also specify a scene")
        if self.battle_id is None or not isinstance(self.battle_id, str) or not self.battle_id:
            raise DeveloperTestConfigError("battle_id must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        result = super().to_dict()
        result.pop("scene", None)
        result["battle"] = self.battle_id
        return result

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "BattleTestConfiguration":
        if not isinstance(raw, Mapping):
            raise DeveloperTestConfigError("Developer test configuration root must be an object")
        unknown = set(raw) - _BATTLE_ALLOWED_KEYS
        if unknown:
            raise DeveloperTestConfigError(
                f"Unknown developer test configuration field(s): {', '.join(sorted(map(str, unknown)))}"
            )
        battle_id = raw.get("battle", raw.get("battle_id"))
        scene_id = raw.get("scene", raw.get("scene_id"))
        return cls(
            battle_id=battle_id,
            scene_id=scene_id,
            flags=_mapping(raw.get("flags", {}), "flags"),
            variables=_mapping(raw.get("variables", {}), "variables"),
            inventory=_mapping(raw.get("inventory", {}), "inventory"),
            stats=_mapping(raw.get("stats", {}), "stats"),
        )


@dataclass(frozen=True)
class QteTestConfiguration:
    """Ephemeral configuration for one isolated global combat-move test."""

    move_id: str
    difficulty_level: int
    seed: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.move_id, str) or not self.move_id:
            raise DeveloperTestConfigError("move_id must be a non-empty string")
        if isinstance(self.difficulty_level, bool) or not isinstance(self.difficulty_level, int) or self.difficulty_level < 0:
            raise DeveloperTestConfigError("difficulty_level must be a non-negative integer")
        if self.seed is not None and (isinstance(self.seed, bool) or not isinstance(self.seed, int)):
            raise DeveloperTestConfigError("seed must be an integer when provided")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"qte_move": self.move_id, "qte_level": self.difficulty_level}
        if self.seed is not None:
            result["seed"] = self.seed
        return result

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "QteTestConfiguration":
        if not isinstance(raw, Mapping):
            raise DeveloperTestConfigError("QTE test configuration root must be an object")
        unknown = set(raw) - _QTE_ALLOWED_KEYS
        if unknown:
            raise DeveloperTestConfigError(
                f"Unknown QTE test configuration field(s): {', '.join(sorted(map(str, unknown)))}"
            )
        move_id = raw.get("qte_move", raw.get("move_id"))
        level = raw.get("qte_level", raw.get("difficulty_level"))
        return cls(move_id=move_id, difficulty_level=level, seed=raw.get("seed"))

    @classmethod
    def from_json(cls, path: str | Path) -> "QteTestConfiguration":
        try:
            with Path(path).open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except json.JSONDecodeError as exc:
            raise DeveloperTestConfigError(f"Malformed developer test configuration JSON: {exc.msg}") from exc
        except OSError as exc:
            raise DeveloperTestConfigError(f"Could not read developer test configuration: {exc}") from exc
        return cls.from_dict(raw)

    def write_json(self, path: str | Path) -> None:
        try:
            with Path(path).open("w", encoding="utf-8") as handle:
                json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
        except OSError as exc:
            raise DeveloperTestConfigError(f"Could not write developer test configuration: {exc}") from exc


def load_developer_test_configuration(path: str | Path) -> SceneTestConfiguration | BattleTestConfiguration | QteTestConfiguration:
    """Load either the established scene config or a battle config by shape."""

    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except json.JSONDecodeError as exc:
        raise DeveloperTestConfigError(f"Malformed developer test configuration JSON: {exc.msg}") from exc
    except OSError as exc:
        raise DeveloperTestConfigError(f"Could not read developer test configuration: {exc}") from exc
    if isinstance(raw, Mapping) and ("qte_move" in raw or "qte_level" in raw or "move_id" in raw and "difficulty_level" in raw):
        return QteTestConfiguration.from_dict(raw)
    if isinstance(raw, Mapping) and ("battle" in raw or "battle_id" in raw):
        return BattleTestConfiguration.from_dict(raw)
    return SceneTestConfiguration.from_dict(raw)


def apply_developer_test_configuration(
    state: Any,
    configuration: SceneTestConfiguration,
    *,
    known_items: Mapping[str, Any] | set[str] | None = None,
) -> None:
    """Apply only requested values to a normally initialized fresh state."""

    if not isinstance(configuration, SceneTestConfiguration):
        raise DeveloperTestConfigError("Developer test configuration has an invalid type")
    configuration.validate(known_items=known_items)
    state.flags.update(configuration.flags)
    state.variables.update(configuration.variables)
    for item_id, quantity in configuration.inventory.items():
        if quantity == 0:
            state.inventory.pop(item_id, None)
        else:
            state.inventory[item_id] = quantity
    for name, value in configuration.stats.items():
        state.stats[name] = value


__all__ = [
    "BattleTestConfiguration",
    "DeveloperTestConfigError",
    "QteTestConfiguration",
    "SceneTestConfiguration",
    "apply_developer_test_configuration",
    "load_developer_test_configuration",
]
