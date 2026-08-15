"""Authoring metadata for the registered defense-pattern vocabulary.

The battle runtime remains the authority for construction and validation.  This
module only describes the useful, scalar authoring surface so tools can build a
form without importing pygame or duplicating a second runtime implementation.
Complex values (telegraphs, geometry objects, beats, ranges, and group
definitions) deliberately remain visible but read-only until they have a
dedicated schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from engine.story_core.schema import FieldSpec, MISSING, TypeSpec


@dataclass(frozen=True)
class DefensePatternFieldSpec:
    """One editor field, possibly below a nested authored mapping."""

    path: tuple[str, ...]
    field: FieldSpec
    group: str = "Pattern"
    editor_hint: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", tuple(str(part) for part in self.path))

    @property
    def key(self) -> str:
        return self.path[-1]


@dataclass(frozen=True)
class DefensePatternEditorSpec:
    """Registry-backed authoring description for one serialized pattern type."""

    type: str
    display_name: str
    fields: tuple[DefensePatternFieldSpec, ...] = ()
    description: str = ""
    aliases: tuple[str, ...] = ()
    supported: bool = True

    def field(self, path: Iterable[str]) -> DefensePatternFieldSpec | None:
        wanted = tuple(path)
        return next((item for item in self.fields if item.path == wanted), None)


def _field(
    key: str,
    type_spec: TypeSpec,
    *,
    display_name: str | None = None,
    description: str = "",
    required: bool = False,
    default: Any = MISSING,
    minimum: int | float | None = None,
    maximum: int | float | None = None,
    allowed_values: tuple[Any, ...] = (),
    aliases: tuple[str, ...] = (),
    group: str = "Pattern",
    editor_hint: str | None = None,
) -> DefensePatternFieldSpec:
    return DefensePatternFieldSpec(
        (key,),
        FieldSpec(
            key,
            display_name=display_name,
            type=type_spec,
            description=description,
            required=required,
            default=default,
            minimum=minimum,
            maximum=maximum,
            allowed_values=allowed_values,
            aliases=aliases,
            ui_hint=editor_hint,
        ),
        group,
        editor_hint,
    )


def _nested(
    parent: str,
    key: str,
    type_spec: TypeSpec,
    *,
    display_name: str | None = None,
    description: str = "",
    minimum: int | float | None = None,
    maximum: int | float | None = None,
    allowed_values: tuple[Any, ...] = (),
    group: str = "Projectile",
    editor_hint: str | None = None,
) -> DefensePatternFieldSpec:
    return DefensePatternFieldSpec(
        (parent, key),
        FieldSpec(
            key,
            display_name=display_name,
            type=type_spec,
            description=description,
            minimum=minimum,
            maximum=maximum,
            allowed_values=allowed_values,
            ui_hint=editor_hint,
        ),
        group,
        editor_hint,
    )


_NUMBER = TypeSpec.number()
_NON_NEGATIVE = dict(minimum=0)
_POSITIVE_INT = dict(minimum=1)
_DIRECTION = ("top_to_bottom", "bottom_to_top", "left_to_right", "right_to_left")
_SHAPE = ("circle", "vertical_strip", "horizontal_strip", "rectangle", "line")
_PROJECTILE_FIELDS = (
    _nested("projectile", "speed", _NUMBER, **_NON_NEGATIVE),
    _nested("projectile", "initial_speed", _NUMBER, **_NON_NEGATIVE),
    _nested("projectile", "forward_speed", _NUMBER, **_NON_NEGATIVE),
    _nested("projectile", "radius", _NUMBER, minimum=0),
    _nested("projectile", "collision_radius", _NUMBER, minimum=0),
    _nested("projectile", "damage", TypeSpec.integer(), minimum=0),
    _nested("projectile", "lifetime", _NUMBER, minimum=0, group="Timing"),
    _nested("projectile", "delay", _NUMBER, minimum=0, group="Timing"),
    _nested("projectile", "bounce_count", TypeSpec.integer(), minimum=0),
    _nested("projectile", "sprite", TypeSpec.asset("sprites"), group="Appearance", editor_hint="asset_picker"),
    _nested("projectile", "rotation_mode", TypeSpec.enum(("none", "velocity", "fixed")), group="Appearance"),
    _nested("projectile", "destroy_outside_arena", TypeSpec.boolean(), group="Appearance"),
)
_COMMON = (
    _field("start", _NUMBER, minimum=0, group="Timing", default=0.0),
    _field("duration", _NUMBER, minimum=0, group="Timing"),
)


def _spec(type_name: str, display_name: str, *fields: DefensePatternFieldSpec, aliases: tuple[str, ...] = (), description: str = "") -> DefensePatternEditorSpec:
    return DefensePatternEditorSpec(type_name, display_name, _COMMON + tuple(fields), description, aliases)


_SPECS: dict[str, DefensePatternEditorSpec] = {
    "aimed_stream": _spec("aimed_stream", "Aimed Stream",
        _field("origin", TypeSpec.mapping(), group="Geometry"),
        _field("fire_interval", _NUMBER, minimum=0, group="Timing"),
        _field("initial_delay", _NUMBER, minimum=0, group="Timing"),
        _field("projectile_count", TypeSpec.integer(), **_POSITIVE_INT, group="Projectile"),
        _field("spread", _NUMBER, minimum=0, group="Projectile"),
        _field("burst_count", TypeSpec.integer(), **_POSITIVE_INT, group="Projectile"),
        _field("burst_interval", _NUMBER, minimum=0, group="Timing"),
        _field("aim_jitter", _NUMBER, minimum=0, group="Projectile"),
        *_PROJECTILE_FIELDS, aliases=("aimed_projectile_stream",)),
    "predictive_stream": _spec("predictive_stream", "Predictive Stream",
        _field("origin", TypeSpec.mapping(), group="Geometry"),
        _field("fire_interval", _NUMBER, minimum=0, group="Timing"),
        _field("prediction_strength", _NUMBER, minimum=0, group="Projectile"),
        _field("player_velocity_weighting", _NUMBER, minimum=0, aliases=("player_velocity_weight",), group="Projectile"),
        _field("spread", _NUMBER, minimum=0, group="Projectile"),
        *_PROJECTILE_FIELDS, aliases=("leading_stream",)),
    "radial_burst": _spec("radial_burst", "Radial Burst",
        _field("origin", TypeSpec.mapping(), group="Geometry"),
        _field("projectile_count", TypeSpec.integer(), **_POSITIVE_INT, group="Projectile"),
        _field("burst_interval", _NUMBER, minimum=0, group="Timing"),
        _field("starting_angle", _NUMBER, group="Geometry"),
        _field("initial_rotation_angle", _NUMBER, group="Geometry"),
        _field("angular_offset", _NUMBER, group="Geometry"),
        _field("rotation_per_burst", _NUMBER, group="Geometry"),
        _field("orbital_speed", _NUMBER, group="Projectile"),
        _field("repetitions", TypeSpec.integer(), **_POSITIVE_INT, group="Timing"),
        _field("bursts", TypeSpec.list(), group="Advanced", editor_hint="read_only"),
        *_PROJECTILE_FIELDS),
    "spiral": _spec("spiral", "Spiral",
        _field("origin", TypeSpec.mapping(), group="Geometry"),
        _field("arms", TypeSpec.integer(), **_POSITIVE_INT, group="Projectile"),
        _field("angular_speed", _NUMBER, group="Geometry"),
        _field("angular_acceleration", _NUMBER, group="Geometry"),
        _field("fire_interval", _NUMBER, minimum=0, group="Timing"),
        _field("angle_offset", _NUMBER, group="Geometry"),
        _field("clockwise", TypeSpec.boolean(), group="Geometry"),
        _field("direction", TypeSpec.enum(("clockwise", "counterclockwise", "ccw", "reverse")), group="Geometry"),
        *_PROJECTILE_FIELDS, aliases=("rotating_emitter",)),
    "falling_rain": _spec("falling_rain", "Falling Rain",
        _field("direction", TypeSpec.enum(_DIRECTION), group="Geometry"),
        _field("spawn_interval", _NUMBER, minimum=0, group="Timing"),
        _field("spawn_distribution", TypeSpec.enum(("random", "left_bias", "right_bias", "top_bias", "bottom_bias", "center")), group="Geometry"),
        _field("horizontal_spread", _NUMBER, minimum=0, group="Geometry"),
        _field("spread", _NUMBER, minimum=0, group="Geometry"),
        *_PROJECTILE_FIELDS, aliases=("rising_rain", "rain")),
    "gap_wall": _spec("gap_wall", "Gap Wall",
        _field("direction", TypeSpec.enum(_DIRECTION), group="Geometry"),
        _field("wall_speed", _NUMBER, minimum=0, group="Timing"),
        _field("spacing", _NUMBER, minimum=0, group="Geometry"),
        _field("gap_width", _NUMBER, minimum=0, group="Geometry"),
        _field("gap_position", TypeSpec.union(_NUMBER, TypeSpec.string(), TypeSpec.mapping()), group="Geometry"),
        _field("wall_thickness", _NUMBER, minimum=0, group="Geometry"),
        _field("damage", TypeSpec.integer(), minimum=0, group="Projectile"),
        *_PROJECTILE_FIELDS, aliases=("moving_gap_wall",)),
    "sweeping_beam": _spec("sweeping_beam", "Sweeping Beam",
        _field("origin", TypeSpec.mapping(), group="Geometry"),
        _field("start_angle", _NUMBER, group="Geometry"),
        _field("end_angle", _NUMBER, group="Geometry"),
        _field("sweep_duration", _NUMBER, minimum=0, group="Timing"),
        _field("width", _NUMBER, minimum=0, group="Geometry"),
        _field("length", _NUMBER, minimum=0, group="Geometry"),
        _field("damage", TypeSpec.integer(), minimum=0, group="Projectile"),
        _field("telegraph", TypeSpec.mapping(), group="Advanced", editor_hint="read_only"),
        aliases=("beam",)),
    "lane_attack": _spec("lane_attack", "Lane Attack",
        _field("direction", TypeSpec.enum(("vertical", "horizontal")), group="Geometry"),
        _field("lane_count", TypeSpec.integer(), **_POSITIVE_INT, group="Geometry"),
        _field("warning_duration", _NUMBER, minimum=0, group="Timing"),
        _field("active_duration", _NUMBER, minimum=0, group="Timing"),
        _field("damage", TypeSpec.integer(), minimum=0, group="Projectile"),
        _field("active_lanes", TypeSpec.list(), group="Advanced", editor_hint="read_only"),
        _field("sequence", TypeSpec.list(), group="Advanced", editor_hint="read_only"),),
    "telegraph_strike": _spec("telegraph_strike", "Telegraph Strike",
        _field("shape", TypeSpec.enum(_SHAPE), group="Geometry"),
        _field("region_count", TypeSpec.integer(), **_POSITIVE_INT, group="Projectile"),
        _field("placement_interval", _NUMBER, minimum=0, group="Timing"),
        _field("position", TypeSpec.union(_NUMBER, TypeSpec.string(), TypeSpec.mapping()), group="Geometry"),
        _field("radius", _NUMBER, minimum=0, group="Geometry"),
        _field("width", _NUMBER, minimum=0, group="Geometry"),
        _field("height", _NUMBER, minimum=0, group="Geometry"),
        _field("warning_duration", _NUMBER, minimum=0, group="Timing"),
        _field("active_duration", _NUMBER, minimum=0, group="Timing"),
        _field("damage", TypeSpec.integer(), minimum=0, group="Projectile"),
        _field("telegraph", TypeSpec.mapping(), group="Advanced", editor_hint="read_only"),
        aliases=("telegraphed_strike",)),
    "crossfire": _spec("crossfire", "Crossfire",
        _field("fire_interval", _NUMBER, minimum=0, group="Timing"),
        _field("stagger", _NUMBER, minimum=0, group="Timing"),
        _field("aiming", TypeSpec.enum(("player_position", "fixed")), group="Geometry"),
        _field("spread", _NUMBER, minimum=0, group="Geometry"),
        _field("sides", TypeSpec.list(), group="Geometry", editor_hint="read_only"),
        _field("angles", TypeSpec.list(), group="Geometry", editor_hint="read_only"),
        *_PROJECTILE_FIELDS),
    "chaser": _spec("chaser", "Chaser",
        _field("count", TypeSpec.integer(), **_POSITIVE_INT, group="Projectile"),
        _field("spawn_interval", _NUMBER, minimum=0, group="Timing"),
        _field("spawn_position", TypeSpec.enum(("edge_random", "random_edge")), group="Geometry"),
        _field("speed", _NUMBER, minimum=0, group="Projectile"),
        _field("turning_rate", _NUMBER, minimum=0, group="Projectile"),
        _field("acceleration", _NUMBER, group="Projectile"),
        _field("lifetime", _NUMBER, minimum=0, group="Timing"),
        _field("radius", _NUMBER, minimum=0, group="Geometry"),
        _field("damage", TypeSpec.integer(), minimum=0, group="Projectile"),
        *_PROJECTILE_FIELDS, aliases=("homing_chaser",)),
    "mine": _spec("mine", "Mine",
        _field("placement", TypeSpec.enum(("player", "target", "player_position", "random")), group="Geometry"),
        _field("placement_interval", _NUMBER, minimum=0, group="Timing"),
        _field("warning_duration", _NUMBER, minimum=0, group="Timing"),
        _field("activation_radius", _NUMBER, minimum=0, group="Geometry"),
        _field("persistence_duration", _NUMBER, minimum=0, group="Timing"),
        _field("damage", TypeSpec.integer(), minimum=0, group="Projectile"),
        _field("telegraph", TypeSpec.mapping(), group="Advanced", editor_hint="read_only"),
        aliases=("persistent_mine", "delayed_hazard")),
    "expanding_ring": _spec("expanding_ring", "Expanding Ring",
        _field("center", TypeSpec.mapping(), group="Geometry"),
        _field("starting_radius", _NUMBER, minimum=0, group="Geometry"),
        _field("expansion_speed", _NUMBER, minimum=0, group="Timing"),
        _field("thickness", _NUMBER, minimum=0, group="Geometry"),
        _field("gap_count", TypeSpec.integer(), minimum=0, group="Geometry"),
        _field("gap_angle", _NUMBER, group="Geometry"),
        _field("gap_width", _NUMBER, minimum=0, group="Geometry"),
        _field("damage", TypeSpec.integer(), minimum=0, group="Projectile"),
        _field("gaps", TypeSpec.list(), group="Advanced", editor_hint="read_only"),
        _field("telegraph", TypeSpec.mapping(), group="Advanced", editor_hint="read_only")),
    "contracting_ring": _spec("contracting_ring", "Contracting Ring",
        _field("center", TypeSpec.mapping(), group="Geometry"),
        _field("starting_radius", _NUMBER, minimum=0, group="Geometry"),
        _field("contraction_speed", _NUMBER, minimum=0, group="Timing"),
        _field("thickness", _NUMBER, minimum=0, group="Geometry"),
        _field("damage", TypeSpec.integer(), minimum=0, group="Projectile"),
        _field("gaps", TypeSpec.list(), group="Advanced", editor_hint="read_only"),
        aliases=("encircling_ring",)),
    "bouncing_projectiles": _spec("bouncing_projectiles", "Bouncing Projectiles",
        _field("origin", TypeSpec.mapping(), group="Geometry"),
        _field("spawn_interval", _NUMBER, minimum=0, group="Timing"),
        _field("initial_angle", _NUMBER, group="Geometry"),
        _field("bounce_count", TypeSpec.integer(), minimum=0, group="Projectile"),
        _field("lifetime", _NUMBER, minimum=0, group="Timing"),
        *_PROJECTILE_FIELDS, aliases=("bouncing_bullets",)),
    "curving_projectiles": _spec("curving_projectiles", "Curving Projectiles",
        _field("origin", TypeSpec.mapping(), group="Geometry"),
        _field("fire_interval", _NUMBER, minimum=0, group="Timing"),
        _field("initial_angle", _NUMBER, group="Geometry"),
        _field("spread", _NUMBER, minimum=0, group="Geometry"),
        _field("angular_velocity", _NUMBER, group="Geometry"),
        _field("angular_acceleration", _NUMBER, group="Geometry"),
        *_PROJECTILE_FIELDS, aliases=("curving_bullets",)),
    "accelerating_stream": _spec("accelerating_stream", "Accelerating Stream",
        _field("origin", TypeSpec.mapping(), group="Geometry"),
        _field("fire_interval", _NUMBER, minimum=0, group="Timing"),
        _field("acceleration", _NUMBER, group="Projectile"),
        _field("minimum_speed", _NUMBER, minimum=0, group="Projectile"),
        _field("maximum_speed", _NUMBER, minimum=0, group="Projectile"),
        *_PROJECTILE_FIELDS, aliases=("decelerating_stream", "accelerating_projectiles")),
    "wave_stream": _spec("wave_stream", "Wave Stream",
        _field("origin", TypeSpec.mapping(), group="Geometry"),
        _field("fire_interval", _NUMBER, minimum=0, group="Timing"),
        _field("direction", TypeSpec.union(_NUMBER, TypeSpec.string()), group="Geometry"),
        _field("initial_angle", _NUMBER, group="Geometry"),
        _field("phase_offset", _NUMBER, group="Geometry"),
        _field("wave_amplitude", _NUMBER, minimum=0, group="Projectile"),
        _field("wave_frequency", _NUMBER, minimum=0, group="Projectile"),
        *_PROJECTILE_FIELDS, aliases=("oscillating_projectiles", "wave_projectiles")),
    "orbiting_hazards": _spec("orbiting_hazards", "Orbiting Hazards",
        _field("center", TypeSpec.mapping(), group="Geometry"),
        _field("count", TypeSpec.integer(), **_POSITIVE_INT, group="Projectile"),
        _field("orbit_radius", _NUMBER, minimum=0, group="Geometry"),
        _field("angular_speed", _NUMBER, group="Geometry"),
        _field("clockwise", TypeSpec.boolean(), group="Geometry"),
        _field("angle_offset", _NUMBER, group="Geometry"),
        _field("hazard_radius", _NUMBER, minimum=0, group="Geometry"),
        _field("damage", TypeSpec.integer(), minimum=0, group="Projectile"),
        _field("follow_player", TypeSpec.boolean(), group="Geometry"),
        aliases=("orbit",)),
    "shrinking_arena": _spec("shrinking_arena", "Shrinking Arena",
        _field("start_bounds", TypeSpec.mapping(), group="Geometry", editor_hint="read_only"),
        _field("end_bounds", TypeSpec.mapping(), group="Geometry", editor_hint="read_only"),
        _field("shrink_duration", _NUMBER, minimum=0, group="Timing"),
        _field("hold_duration", _NUMBER, minimum=0, group="Timing"),
        _field("restore_duration", _NUMBER, minimum=0, group="Timing"),
        aliases=("moving_arena", "arena_constraint")),
    "maze_corridor": _spec("maze_corridor", "Maze Corridor",
        _field("direction", TypeSpec.enum(_DIRECTION + ("static",)), group="Geometry"),
        _field("wall_speed", _NUMBER, minimum=0, group="Timing"),
        _field("wall_thickness", _NUMBER, minimum=0, group="Geometry"),
        _field("gap_width", _NUMBER, minimum=0, group="Geometry"),
        _field("segments", TypeSpec.list(), group="Advanced", editor_hint="read_only"),
        _field("damage", TypeSpec.integer(), minimum=0, group="Projectile"),
        aliases=("corridor", "maze")),
    "rhythm": _spec("rhythm", "Rhythm",
        _field("direction", TypeSpec.enum(("vertical", "horizontal")), group="Geometry"),
        _field("lane_count", TypeSpec.integer(), **_POSITIVE_INT, group="Geometry"),
        _field("warning_duration", _NUMBER, minimum=0, group="Timing"),
        _field("active_duration", _NUMBER, minimum=0, group="Timing"),
        _field("beats", TypeSpec.list(), group="Advanced", editor_hint="read_only"),
        _field("damage", TypeSpec.integer(), minimum=0, group="Projectile"),
        aliases=("sequence", "rhythm_sequence")),
}

# ``moving_gap_wall`` is a distinct registered subclass, not merely an alias
# of ``gap_wall``; keep its movement controls discoverable as well.
_SPECS["moving_gap_wall"] = _spec(
    "moving_gap_wall", "Moving Gap Wall",
    _field("direction", TypeSpec.enum(_DIRECTION), group="Geometry"),
    _field("wall_speed", _NUMBER, minimum=0, group="Timing"),
    _field("spacing", _NUMBER, minimum=0, group="Geometry"),
    _field("gap_width", _NUMBER, minimum=0, group="Geometry"),
    _field("gap_position", TypeSpec.union(_NUMBER, TypeSpec.string(), TypeSpec.mapping()), group="Geometry"),
    _field("gap_movement", TypeSpec.enum(("oscillate", "linear", "random")), group="Geometry"),
    _field("gap_speed", _NUMBER, minimum=0, group="Timing"),
    _field("gap_bounds", TypeSpec.list(), group="Geometry", editor_hint="read_only"),
    _field("damage", TypeSpec.integer(), minimum=0, group="Projectile"),
    *_PROJECTILE_FIELDS,
)


def defense_pattern_editor_specs() -> Mapping[str, DefensePatternEditorSpec]:
    """Return canonical metadata plus aliases for every registered type."""

    # Import lazily to keep this metadata module independent of the runtime's
    # module initialization order.
    from .defense import PATTERN_TYPES

    result: dict[str, DefensePatternEditorSpec] = {}
    for registered_name, pattern_class in PATTERN_TYPES.items():
        canonical = getattr(pattern_class, "type_name", registered_name)
        spec = _SPECS.get(canonical)
        if spec is None:
            result[registered_name] = DefensePatternEditorSpec(
                registered_name, registered_name.replace("_", " ").title(), supported=False,
                description="Registered runtime pattern has no authoring schema; payload is preserved read-only.",
            )
        else:
            result[registered_name] = spec
    return result


def defense_pattern_editor_spec(type_name: str) -> DefensePatternEditorSpec | None:
    return defense_pattern_editor_specs().get(str(type_name))


def minimal_defense_pattern(type_name: str) -> dict[str, Any]:
    """Create the minimum authored payload accepted by the runtime."""

    spec = defense_pattern_editor_spec(type_name)
    if spec is None or not spec.supported:
        raise KeyError(f"No editable defense pattern metadata for {type_name!r}")
    return {"type": str(type_name)}


__all__ = [
    "DefensePatternEditorSpec",
    "DefensePatternFieldSpec",
    "defense_pattern_editor_spec",
    "defense_pattern_editor_specs",
    "minimal_defense_pattern",
]
