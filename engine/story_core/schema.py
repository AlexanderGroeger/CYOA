"""Small, declarative authoring-schema metadata for Story Core.

This is intentionally metadata, not a replacement for the detailed runtime
validators for QTEs, defense patterns, or active battle state.  It gives a
future editor and project validator one shared description of the high-value
top-level fields, defaults, aliases, and picker targets.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, TypeAlias, overload


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


MISSING = _Missing()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, frozenset):
        return {_thaw(item) for item in value}
    return value


@dataclass(frozen=True)
class TypeSpec:
    """A recursively composable type description for one serialized field."""

    kind: str
    element_type: "TypeSpec | None" = None
    value_type: "TypeSpec | None" = None
    object_schema: str | None = None
    reference_target: str | None = None
    asset_kind: str | None = None
    enum_values: tuple[Any, ...] = ()
    discriminator: str | None = None
    variants: Mapping[str, "TypeSpec"] = field(default_factory=dict)
    nullable: bool = False
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", str(self.kind))
        if self.element_type is not None and not isinstance(self.element_type, TypeSpec):
            raise TypeError("element_type must be a TypeSpec")
        if self.value_type is not None and not isinstance(self.value_type, TypeSpec):
            raise TypeError("value_type must be a TypeSpec")
        object.__setattr__(self, "object_schema", str(self.object_schema) if self.object_schema is not None else None)
        object.__setattr__(self, "reference_target", str(self.reference_target) if self.reference_target is not None else None)
        object.__setattr__(self, "asset_kind", str(self.asset_kind) if self.asset_kind is not None else None)
        object.__setattr__(self, "enum_values", tuple(_freeze(value) for value in self.enum_values))
        if self.variants and not all(isinstance(value, TypeSpec) for value in self.variants.values()):
            raise TypeError("TypeSpec variants must contain TypeSpec values")
        object.__setattr__(self, "variants", MappingProxyType(dict(self.variants)))
        object.__setattr__(self, "nullable", bool(self.nullable))
        object.__setattr__(self, "options", _freeze(self.options))

    @staticmethod
    def _nullable(options: dict[str, Any]) -> bool:
        """Remove the common nullability option from factory metadata."""

        return bool(options.pop("nullable", False))

    @classmethod
    def string(cls, *, multiline: bool = False, **options: Any) -> "TypeSpec":
        return cls("multiline_string" if multiline else "string", nullable=cls._nullable(options), options=options)

    @classmethod
    def multiline_string(cls, **options: Any) -> "TypeSpec":
        return cls.string(multiline=True, **options)

    @classmethod
    def integer(cls, **options: Any) -> "TypeSpec":
        return cls("integer", nullable=cls._nullable(options), options=options)

    @classmethod
    def float(cls, **options: Any) -> "TypeSpec":
        return cls("float", nullable=cls._nullable(options), options=options)

    @classmethod
    def number(cls, **options: Any) -> "TypeSpec":
        return cls("number", nullable=cls._nullable(options), options=options)

    @classmethod
    def boolean(cls, **options: Any) -> "TypeSpec":
        return cls("boolean", nullable=cls._nullable(options), options=options)

    @classmethod
    def enum(cls, values: Iterable[Any], **options: Any) -> "TypeSpec":
        return cls("enum", enum_values=tuple(values), nullable=cls._nullable(options), options=options)

    @classmethod
    def object(cls, schema: str | None = None, **options: Any) -> "TypeSpec":
        return cls("object", object_schema=schema, nullable=cls._nullable(options), options=options)

    @classmethod
    def list(cls, element_type: "TypeSpec | None" = None, **options: Any) -> "TypeSpec":
        return cls("list", element_type=element_type, nullable=cls._nullable(options), options=options)

    @classmethod
    def mapping(cls, value_type: "TypeSpec | None" = None, **options: Any) -> "TypeSpec":
        return cls("mapping", value_type=value_type, nullable=cls._nullable(options), options=options)

    @classmethod
    def reference(cls, target: str, **options: Any) -> "TypeSpec":
        return cls("reference", reference_target=target, nullable=cls._nullable(options), options=options)

    @classmethod
    def asset(cls, category: str | None = None, **options: Any) -> "TypeSpec":
        return cls("asset", asset_kind=category, nullable=cls._nullable(options), options=options)

    @classmethod
    def condition(cls, *, dialect: str | None = None, **options: Any) -> "TypeSpec":
        nullable = cls._nullable(options)
        metadata = dict(options)
        if dialect is not None:
            metadata["dialect"] = dialect
        return cls("condition", nullable=nullable, options=metadata)

    @classmethod
    def union(cls, *variants: "TypeSpec", **options: Any) -> "TypeSpec":
        return cls(
            "union",
            variants={str(index): variant for index, variant in enumerate(variants)},
            nullable=cls._nullable(options),
            options=options,
        )

    @classmethod
    def discriminated_union(
        cls,
        discriminator: str,
        variants: Mapping[str, "TypeSpec"],
        **options: Any,
    ) -> "TypeSpec":
        return cls(
            "discriminated_union",
            discriminator=discriminator,
            variants=variants,
            nullable=cls._nullable(options),
            options=options,
        )

    @property
    def target(self) -> str | None:
        return self.reference_target

    @property
    def asset_category(self) -> str | None:
        return self.asset_kind

    @property
    def values(self) -> tuple[Any, ...]:
        return self.enum_values


@dataclass(frozen=True)
class FieldSpec:
    """Editor- and validator-friendly description of one serialized key."""

    key: str
    display_name: str | None = None
    type: TypeSpec = field(default_factory=TypeSpec.string)
    description: str = ""
    required: bool = False
    default: Any = MISSING
    aliases: tuple[str, ...] = ()
    deprecated: bool | str = False
    deprecation_message: str | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None
    allowed_values: tuple[Any, ...] = ()
    element_type: TypeSpec | None = None
    object_schema: str | None = None
    reference_target: str | None = None
    asset_kind: str | None = None
    ui_hint: str | None = None
    applicable_when: Mapping[str, Any] = field(default_factory=dict)
    read_only: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key:
            raise ValueError("FieldSpec key must be a non-empty string")
        object.__setattr__(self, "display_name", self.display_name if isinstance(self.display_name, str) else self.key.replace("_", " ").title())
        if not isinstance(self.type, TypeSpec):
            raise TypeError("FieldSpec type must be a TypeSpec")
        object.__setattr__(self, "description", self.description if isinstance(self.description, str) else "")
        object.__setattr__(self, "required", bool(self.required))
        object.__setattr__(self, "default", self.default if self.default is MISSING else _freeze(self.default))
        aliases = tuple(alias for alias in self.aliases if isinstance(alias, str) and alias and alias != self.key)
        object.__setattr__(self, "aliases", tuple(dict.fromkeys(aliases)))
        deprecated = self.deprecated
        if isinstance(deprecated, str) and deprecated:
            object.__setattr__(self, "deprecated", True)
            if self.deprecation_message is None:
                object.__setattr__(self, "deprecation_message", deprecated)
        else:
            object.__setattr__(self, "deprecated", bool(deprecated))
        object.__setattr__(self, "minimum", self.minimum if isinstance(self.minimum, (int, float)) and not isinstance(self.minimum, bool) else None)
        object.__setattr__(self, "maximum", self.maximum if isinstance(self.maximum, (int, float)) and not isinstance(self.maximum, bool) else None)
        allowed_values = self.allowed_values or self.type.enum_values
        object.__setattr__(self, "allowed_values", tuple(_freeze(value) for value in allowed_values))
        if self.element_type is not None and not isinstance(self.element_type, TypeSpec):
            raise TypeError("FieldSpec element_type must be a TypeSpec")
        if self.element_type is None:
            object.__setattr__(self, "element_type", self.type.element_type)
        object.__setattr__(self, "object_schema", str(self.object_schema) if self.object_schema is not None else self.type.object_schema)
        object.__setattr__(self, "reference_target", str(self.reference_target) if self.reference_target is not None else self.type.reference_target)
        object.__setattr__(self, "asset_kind", str(self.asset_kind) if self.asset_kind is not None else self.type.asset_kind)
        object.__setattr__(self, "ui_hint", self.ui_hint if isinstance(self.ui_hint, str) else None)
        object.__setattr__(self, "applicable_when", _freeze(self.applicable_when))
        object.__setattr__(self, "read_only", bool(self.read_only))

    @property
    def has_default(self) -> bool:
        return self.default is not MISSING

    @property
    def serialized_keys(self) -> tuple[str, ...]:
        return (self.key, *self.aliases)

    def default_value(self) -> Any:
        """Return an isolated default suitable for parser/editor use."""

        return MISSING if self.default is MISSING else _thaw(self.default)

    def accepts_key(self, key: str) -> bool:
        return key == self.key or key in self.aliases

    def is_applicable(self, values: Mapping[str, Any] | None = None) -> bool:
        """Test a simple equality-based field applicability rule."""

        if not self.applicable_when:
            return True
        values = values or {}
        return all(values.get(key) == expected for key, expected in self.applicable_when.items())

    @property
    def help_text(self) -> str:
        return self.description

    @property
    def asset_category(self) -> str | None:
        return self.asset_kind


@dataclass(frozen=True)
class Schema:
    """A named collection of field metadata for one authoring shape."""

    name: str
    fields: tuple[FieldSpec, ...] = ()
    description: str = ""
    extra_fields_allowed: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("Schema name must be a non-empty string")
        fields = tuple(self.fields)
        if not all(isinstance(field_spec, FieldSpec) for field_spec in fields):
            raise TypeError("Schema fields must be FieldSpec instances")
        seen: set[str] = set()
        for field_spec in fields:
            for key in field_spec.serialized_keys:
                if key in seen:
                    raise ValueError(f"Schema {self.name!r} has duplicate field/alias {key!r}")
                seen.add(key)
        object.__setattr__(self, "fields", fields)
        object.__setattr__(self, "description", self.description if isinstance(self.description, str) else "")
        object.__setattr__(self, "extra_fields_allowed", bool(self.extra_fields_allowed))

    @property
    def field_map(self) -> Mapping[str, FieldSpec]:
        return MappingProxyType({field_spec.key: field_spec for field_spec in self.fields})

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(field_spec.key for field_spec in self.fields)

    def field(self, key: str, *, include_aliases: bool = True) -> FieldSpec | None:
        for field_spec in self.fields:
            if key == field_spec.key or (include_aliases and key in field_spec.aliases):
                return field_spec
        return None

    get_field = field

    def canonical_key(self, key: str) -> str | None:
        field_spec = self.field(key)
        return field_spec.key if field_spec is not None else None

    def defaults(self) -> dict[str, Any]:
        return {
            field_spec.key: field_spec.default_value()
            for field_spec in self.fields
            if field_spec.has_default
        }

    def normalize_mapping(self, data: Mapping[str, Any] | None, *, apply_defaults: bool = True) -> dict[str, Any]:
        """Canonicalize known aliases while retaining tolerated unknown keys."""

        result = dict(data or {})
        for field_spec in self.fields:
            if field_spec.key not in result:
                for alias in field_spec.aliases:
                    if alias in result:
                        result[field_spec.key] = result[alias]
                        break
                else:
                    if apply_defaults and field_spec.has_default:
                        result[field_spec.key] = field_spec.default_value()
            for alias in field_spec.aliases:
                result.pop(alias, None)
        return result

    apply_defaults = normalize_mapping


SchemaSpec = Schema


class SchemaRegistry:
    """Registry of named schemas and type aliases, extensible by subsystems."""

    def __init__(self, schemas: Iterable[Schema] = ()) -> None:
        self._schemas: dict[str, Schema] = {}
        self._aliases: dict[str, str] = {}
        for schema in schemas:
            self.register(schema)

    @staticmethod
    def _name(value: str | Any) -> str:
        raw = getattr(value, "value", value)
        return str(raw)

    def register(
        self,
        schema_or_name: Schema | str,
        schema: Schema | None = None,
        *,
        aliases: Iterable[str] = (),
        replace: bool = False,
    ) -> Schema:
        """Register ``Schema`` or ``register(name, schema)`` form."""

        if isinstance(schema_or_name, Schema):
            if schema is not None:
                raise TypeError("Do not pass schema twice")
            value = schema_or_name
            name = value.name
        else:
            if not isinstance(schema, Schema):
                raise TypeError("register(name, schema) requires a Schema")
            name = self._name(schema_or_name)
            value = schema if schema.name == name else Schema(name, schema.fields, schema.description, schema.extra_fields_allowed)
        if name in self._schemas and not replace:
            raise ValueError(f"Schema {name!r} is already registered")
        self._schemas[name] = value
        for alias in aliases:
            alias_name = self._name(alias)
            existing = self._aliases.get(alias_name)
            if existing is not None and existing != name and not replace:
                raise ValueError(f"Schema alias {alias_name!r} is already registered")
            self._aliases[alias_name] = name
        return value

    def get(self, name: str | Any, default: Schema | None = None) -> Schema | None:
        normalized = self._name(name)
        return self._schemas.get(self._aliases.get(normalized, normalized), default)

    schema_for = get

    def require(self, name: str | Any) -> Schema:
        schema = self.get(name)
        if schema is None:
            raise KeyError(f"No schema registered for {self._name(name)!r}")
        return schema

    def unregister(self, name: str | Any) -> Schema | None:
        normalized = self._name(name)
        canonical = self._aliases.pop(normalized, normalized)
        removed = self._schemas.pop(canonical, None)
        if removed is not None:
            self._aliases = {alias: target for alias, target in self._aliases.items() if target != canonical}
        return removed

    def __contains__(self, name: object) -> bool:
        try:
            return self.get(name) is not None
        except (TypeError, ValueError):
            return False

    def __iter__(self) -> Iterator[str]:
        return iter(self._schemas)

    def __len__(self) -> int:
        return len(self._schemas)

    def items(self) -> tuple[tuple[str, Schema], ...]:
        return tuple(self._schemas.items())

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._schemas)

    @property
    def schemas(self) -> Mapping[str, Schema]:
        return MappingProxyType(dict(self._schemas))

    def copy(self) -> "SchemaRegistry":
        registry = type(self)()
        registry._schemas = dict(self._schemas)
        registry._aliases = dict(self._aliases)
        return registry


def _field(key: str, type_spec: TypeSpec, **kwargs: Any) -> FieldSpec:
    return FieldSpec(key=key, type=type_spec, **kwargs)


def default_schema_registry() -> SchemaRegistry:
    """Return a fresh registry for the principal shipped story content types."""

    string = TypeSpec.string
    integer = TypeSpec.integer
    boolean = TypeSpec.boolean
    mapping = TypeSpec.mapping
    list_of = TypeSpec.list
    registry = SchemaRegistry()

    registry.register(
        Schema(
            "exploration_object",
            (
                _field("id", string(), required=True, description="Stable scene-local object identity."),
                _field("name", string(), description="Human-readable object name."),
                _field("position", list_of(integer()), description="Top-left [x, y] position."),
                _field("size", list_of(integer()), description="Authored [width, height]."),
                _field("z", integer(), default=0, description="Scene draw order."),
                _field("rotation", TypeSpec.number(), default=0, description="Rotation in degrees around the object center."),
                _field("sprite", TypeSpec.asset("sprites"), asset_kind="sprites"),
                _field("visible", boolean(), default=True),
                _field("visible_when", TypeSpec.condition(), aliases=("conditions",)),
                _field("animation", TypeSpec.reference("animation"), reference_target="animation"),
                _field("animations", mapping(TypeSpec.reference("animation")), reference_target="animation", description="Named global animations available to this object."),
                _field("look", mapping(), deprecated="Legacy object-owned interaction; prefer Look Regions."),
                _field("actions", list_of(TypeSpec.object("exploration_action")), deprecated="Legacy object-owned interaction actions."),
            ),
            "Visual/runtime entity placed in an exploration scene.",
        ),
    )
    registry.register(
        Schema(
            "look_region",
            (
                _field("id", string(), required=True, description="Stable scene-local interaction identity."),
                _field("name", string(), description="Human-readable region name."),
                _field("rect", list_of(integer()), aliases=("hitbox",), description="Absolute [x, y, width, height] input rectangle."),
                _field("interaction", TypeSpec.enum(("inspect", "action")), default="inspect"),
                _field("event", string()),
                _field("priority", integer(), default=0),
                _field("z", integer(), default=0),
                _field("visible", boolean(), default=True),
                _field("visible_when", TypeSpec.condition(), aliases=("conditions",)),
            ),
            "Player input surface; it may overlap any number of Scene Objects.",
        ),
    )
    registry.register(
        Schema(
            "manifest",
            (
                _field("id", string(), required=False, description="Stable story identifier."),
                _field("title", string(), default="", description="Player-facing story title."),
                _field("version", string(), default="0.0"),
                _field("start_scene", TypeSpec.reference("scene"), reference_target="scene", description="Initial scene ID."),
                _field("display", TypeSpec.object("display"), object_schema="display", required=True),
                _field("starting_flags", mapping(), default={}),
                _field("starting_variables", mapping(), default={}),
                _field("starting_stats", mapping(), default={}),
                _field("starting_inventory", TypeSpec.union(list_of(string()), mapping()), default=[]),
                _field("starting_equipment", mapping(string()), default={}),
                _field("render", mapping(), default={}),
                _field("navigation", mapping(), default={}),
                _field("debug", mapping(), default={}),
                _field("default_scene_background", TypeSpec.asset("backgrounds"), asset_kind="backgrounds"),
            ),
            "Story-wide metadata and legacy starting-state fallback.",
        ),
        aliases=("story", "story_manifest"),
    )
    registry.register(
        Schema(
            "player",
            (
                _field("stats", mapping(), default={}),
                _field("inventory", TypeSpec.union(list_of(string()), mapping()), default=[]),
                _field("equipment", mapping(string()), default={}),
                _field("known_moves", list_of(TypeSpec.reference("move")), reference_target="move", default=[]),
                _field("move_skill_levels", mapping(integer()), default={}),
                _field("inventory_ui", TypeSpec.object("inventory_ui"), object_schema="inventory_ui", default={}),
            ),
            "Optional static initial-player profile.",
        ),
        aliases=("player_profile",),
    )
    registry.register(
        Schema(
            "scene",
            (
                _field("id", string(), description="Optional ID; scene lookup is filename-based."),
                _field("text", TypeSpec.multiline_string(), default="", ui_hint="multiline"),
                _field("background", TypeSpec.asset("backgrounds"), asset_kind="backgrounds"),
                _field("sprite", TypeSpec.asset("sprites"), asset_kind="sprites"),
                _field("music", TypeSpec.asset("music"), asset_kind="music"),
                _field("actions", list_of(TypeSpec.object("story_action")), default=[]),
                _field("choices", list_of(TypeSpec.object("scene_choice")), default=[]),
                _field("exploration", TypeSpec.union(boolean(), TypeSpec.object("exploration")), default=False),
                _field("navigation", list_of(TypeSpec.object("exploration_navigation")), default=[], applicable_when={"exploration": True}),
                _field("dialogue_sequences", mapping(), default={}),
                _field("objects", list_of(TypeSpec.object("exploration_object")), default=[]),
                _field("look_regions", list_of(TypeSpec.object("look_region")), default=[]),
                _field("look_events", mapping(TypeSpec.object("event")), default={}),
                _field("checkpoint", boolean(), default=False),
                _field("animation", TypeSpec.reference("animation"), reference_target="animation"),
            ),
            "Legacy narrative or opt-in exploration scene.",
        ),
        aliases=("scenes",),
    )
    registry.register(
        Schema(
            "item",
            (
                _field("name", string(), description="Display name."),
                _field("description", TypeSpec.multiline_string(), default="", ui_hint="multiline"),
                _field("type", string(), default="item"),
                _field("icon", TypeSpec.asset("items"), asset_kind="items"),
                _field("stats", mapping(integer()), default={}),
                _field("equipment_slot", string()),
                _field("actions", list_of(string()), default=[]),
                _field("use", TypeSpec.object("item_use")),
                _field("equipment", mapping(), deprecated="Legacy equipment.bonuses remains supported."),
                _field("combat", mapping(), deprecated="Legacy combat fields remain supported."),
            ),
            "Registry entry keyed by item ID.",
        ),
        aliases=("items",),
    )
    registry.register(
        Schema(
            "move",
            (
                _field("id", string(), required=True),
                _field("name", string()),
                _field("common", mapping(), default={}),
                _field("difficulty_levels", mapping(), description="Difficulty-specific overlays."),
                _field("initial_level", integer(), default=1, minimum=0, ui_hint="spinbox"),
                _field("tutorial_records_skill", boolean(), default=False),
                _field("availability", TypeSpec.condition()),
            ),
            "Global combat-move declaration; QTE subtypes remain extension-backed.",
        ),
        aliases=("moves", "combat_move"),
    )
    registry.register(Schema(
            "battle_enemy",
            (
                _field("name", string(), required=True, description="Opponent display name."),
                _field("hp", integer(), minimum=1, ui_hint="spinbox"),
                _field("attack", integer(), minimum=0, ui_hint="spinbox"),
                _field("defense", integer(), minimum=0, ui_hint="spinbox"),
                _field("sprite", TypeSpec.asset("sprites"), asset_kind="sprites"),
                _field("animation", TypeSpec.reference("animation"), reference_target="animation"),
            ),
            "Safe static enemy presentation and combat values.",
        ))
    registry.register(Schema(
            "battle_arena",
            (
                _field("x", integer(), ui_hint="spinbox"),
                _field("y", integer(), ui_hint="spinbox"),
                _field("width", integer(), minimum=1, ui_hint="spinbox"),
                _field("height", integer(), minimum=1, ui_hint="spinbox"),
                _field("player_speed", TypeSpec.number(), minimum=0, ui_hint="spinbox"),
            ),
            "Battle arena geometry and player movement speed.",
        ))
    registry.register(Schema(
            "battle_enemy_move",
            (
                _field("id", string(), required=True),
                _field("name", string()),
                _field("pattern", string(), description="Referenced defense sequence/pattern ID."),
                _field("weight", TypeSpec.number(), minimum=0, ui_hint="spinbox"),
                _field("cooldown", integer(), minimum=0, ui_hint="spinbox"),
                _field("telegraph_duration", TypeSpec.number(), minimum=0, ui_hint="spinbox"),
            ),
            "Safe summary fields for an enemy move; specialized payloads remain opaque.",
        ))
    registry.register(Schema(
            "battle_phase",
            (
                _field("id", string()),
                _field("name", string()),
                _field("when", TypeSpec.condition(dialect="battle"), read_only=True),
                _field("actions", list_of(TypeSpec.object()), read_only=True),
            ),
            "Battle phase metadata and opaque transition actions.",
        ))
    registry.register(Schema(
            "battle_dialogue",
            (
                _field("trigger", string()),
                _field("text", TypeSpec.multiline_string(), ui_hint="multiline"),
                _field("type", string()),
                _field("once", boolean()),
                _field("pause", TypeSpec.number(), minimum=0, ui_hint="spinbox"),
                _field("when", TypeSpec.condition(dialect="battle"), read_only=True),
            ),
            "Simple battle dialogue presentation fields; triggers and metadata are preserved.",
        ))
    registry.register(
        Schema(
            "battle",
            (
                _field("id", string(), description="Informational; battle lookup is filename-based."),
                _field("enemy", TypeSpec.object("battle_enemy"), object_schema="battle_enemy", required=True),
                _field("arena", TypeSpec.object("battle_arena"), object_schema="battle_arena"),
                _field("enemy_moves", list_of(TypeSpec.object("battle_enemy_move")), default=[]),
                _field("defense_sequences", list_of(TypeSpec.object()), aliases=("enemy_patterns",), default=[]),
                _field("initial_player_moves", list_of(TypeSpec.reference("move")), reference_target="move"),
                _field("initial_enemy_moves", list_of(string())),
                _field("enemy_sequence", list_of(string())),
                _field("dialogue", list_of(TypeSpec.object("battle_dialogue")), default=[]),
                _field("phases", list_of(TypeSpec.object("battle_phase")), default=[]),
                _field("escape", TypeSpec.union(boolean(), mapping()), default={}),
                _field("background", TypeSpec.asset("backgrounds"), asset_kind="backgrounds"),
                _field("music", TypeSpec.asset("music"), asset_kind="music"),
                _field("victory", mapping(), default={}),
                _field("defeat", mapping(), default={}),
                _field("on_lose", mapping()),
                _field("test_sequences_restore_hp", boolean(), default=True),
            ),
            "Battle envelope; detailed QTE and defense validation remains specialized.",
        ),
        aliases=("battles",),
    )
    registry.register(
        Schema(
            "event_pool",
            (
                _field("id", string(), description="Informational; event-pool lookup is filename-based."),
                _field("chance", TypeSpec.float(), default=0.0, minimum=0, maximum=1, ui_hint="spinbox"),
                _field("events", list_of(TypeSpec.object("weighted_event")), default=[]),
            ),
            "Filename-addressed weighted random-event pool.",
        ),
        aliases=("events", "event"),
    )
    registry.register(
        Schema(
            "animation",
            (
                _field("frames", list_of(string()), required=True),
                _field("frame_delay_ms", integer(), default=300, minimum=1, ui_hint="spinbox"),
                _field("loop", boolean(), default=True),
            ),
            "Image frame animation envelope.",
        ),
        aliases=("animations",),
    )
    registry.register(
        Schema(
            "audio",
            (
                _field("master_volume", TypeSpec.float(), default=0.8, minimum=0, maximum=1, ui_hint="spinbox"),
                _field("music_volume", TypeSpec.float(), default=1.0, minimum=0, maximum=1, ui_hint="spinbox"),
                _field("effects_volume", TypeSpec.float(), default=1.0, minimum=0, maximum=1, ui_hint="spinbox"),
            ),
            "Optional story-wide mixer preference data.",
        ),
    )
    return registry


create_default_schema_registry = default_schema_registry


__all__ = [
    "create_default_schema_registry",
    "default_schema_registry",
    "FieldSpec",
    "MISSING",
    "Schema",
    "SchemaRegistry",
    "SchemaSpec",
    "TypeSpec",
]
