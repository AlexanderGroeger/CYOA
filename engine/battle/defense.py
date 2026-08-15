"""Configurable enemy-defense patterns for the battle dodge phase.

This module is deliberately independent from pygame and from the player's
attack QTE implementation.  It owns the *enemy offensive turn*: a scheduler
starts configured defend patterns, patterns create reusable hazards, and one
``DefenseSequence`` owns movement, collision, invulnerability, and timing.

The public API is intentionally small:

``DefenseSequence``
    Runtime used by :class:`engine.battle.controller.BattleController`.
``PATTERN_TYPES`` / ``register_defense_pattern``
    Registry used to add a YAML-facing pattern without editing a conditional.
``validate_defense_sequence``
    Lightweight structural validation used by the battle-config loader.

All simulation coordinates are logical, absolute canvas coordinates at
runtime.  New YAML patterns use arena-local coordinates by default; the
single coordinate resolver below turns them into absolute positions.  The
older ``timeline`` format remains supported as a compatibility adapter.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import math
import random
from typing import Any, Callable, Iterable, Mapping, Protocol

from engine.battle.controls import BattleInput


EPSILON = 1e-8


class DefenseConfigError(ValueError):
    """Raised for an invalid defense-sequence configuration.

    ``config.py`` wraps this in ``BattleConfigError`` so story authors still
    receive the battle source and YAML path in the final diagnostic.
    """


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def lerp(start: float, end: float, amount: float) -> float:
    return start + (end - start) * clamp(amount, 0.0, 1.0)


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _positive(value: Any, default: float) -> float:
    return max(0.0, _number(value, default))


def _vector_length(x: float, y: float) -> float:
    return math.hypot(x, y)


def _normal(x: float, y: float) -> tuple[float, float]:
    length = _vector_length(x, y)
    return (x / length, y / length) if length > EPSILON else (0.0, 0.0)


def _angle_vector(angle_degrees: float) -> tuple[float, float]:
    radians = math.radians(angle_degrees)
    return math.cos(radians), math.sin(radians)


def _vector_angle(x: float, y: float) -> float:
    return math.degrees(math.atan2(y, x))


def _turn_toward(current: float, target: float, maximum_delta: float) -> float:
    """Move an angle toward another across the short arc, in degrees."""
    delta = (target - current + 180.0) % 360.0 - 180.0
    return current + clamp(delta, -maximum_delta, maximum_delta)


def _distance_to_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    denominator = dx * dx + dy * dy
    if denominator <= EPSILON:
        return math.hypot(px - ax, py - ay)
    amount = clamp(((px - ax) * dx + (py - ay) * dy) / denominator, 0.0, 1.0)
    return math.hypot(px - (ax + dx * amount), py - (ay + dy * amount))


def _point_in_polygon(x: float, y: float, points: list[tuple[float, float]]) -> bool:
    """Even-odd polygon hit test; adequate for simple authored zones."""
    inside = False
    previous = len(points) - 1
    for index, (point_x, point_y) in enumerate(points):
        previous_x, previous_y = points[previous]
        crosses = (point_y > y) != (previous_y > y)
        if crosses and x < (previous_x - point_x) * (y - point_y) / ((previous_y - point_y) or EPSILON) + point_x:
            inside = not inside
        previous = index
    return inside


def resolve_random_value(value: Any, rng: random.Random) -> Any:
    """Resolve the compact random-value forms accepted in defense YAML.

    ``{min: 70, max: 100}`` samples a number; ``{choices: [...]}`` samples
    one authored choice.  Other mappings/lists are traversed recursively so
    random projectile fields are sampled *when a hazard is spawned*, rather
    than once when a sequence is loaded.
    """
    if isinstance(value, Mapping):
        if "choices" in value:
            choices = value["choices"]
            if not isinstance(choices, (list, tuple)) or not choices:
                raise DefenseConfigError("random choices must be a non-empty list")
            return resolve_random_value(rng.choice(list(choices)), rng)
        if "min" in value or "max" in value:
            if "min" not in value or "max" not in value:
                raise DefenseConfigError("random range needs both min and max")
            lower, upper = _number(value["min"]), _number(value["max"])
            if lower > upper:
                raise DefenseConfigError("random range min cannot exceed max")
            return rng.uniform(lower, upper)
        return {key: resolve_random_value(item, rng) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_random_value(item, rng) for item in value]
    if isinstance(value, tuple):
        return tuple(resolve_random_value(item, rng) for item in value)
    return value


# A less surprising alias for authors and integrations that prefer a verb.
resolve_random = resolve_random_value


def _deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, Mapping) and isinstance(override, Mapping):
        result = {key: deepcopy(value) for key, value in base.items()}
        for key, value in override.items():
            result[key] = _deep_merge(result[key], value) if key in result else deepcopy(value)
        return result
    return deepcopy(override)


def apply_difficulty_overrides(data: Mapping[str, Any], level: str | int | None = None) -> dict[str, Any]:
    """Return a definition with a named/numbered ``difficulty`` override.

    The base mapping is retained and only fields present in the selected
    override replace it.  ``default`` is applied first when present.  This
    works at the sequence level and at individual pattern entries.
    """
    result = deepcopy(dict(data))
    overrides = result.pop("difficulty", None)
    if not isinstance(overrides, Mapping):
        return result
    selected = level if level is not None else result.get("difficulty_level")
    default = overrides.get("default")
    if isinstance(default, Mapping):
        result = _deep_merge(result, default)
    if selected is None:
        return result
    candidates = (selected, str(selected))
    selected_override: Any = None
    for candidate in candidates:
        if candidate in overrides:
            selected_override = overrides[candidate]
            break
    if selected_override is None:
        return result
    if not isinstance(selected_override, Mapping):
        raise DefenseConfigError(f"difficulty override {selected!r} must be a mapping")
    return _deep_merge(result, selected_override)


def _bounds_from_arena(arena: Mapping[str, Any]) -> tuple[float, float, float, float]:
    return (
        _number(arena.get("x", 0.0)),
        _number(arena.get("y", 0.0)),
        max(1.0, _number(arena.get("width", 220.0))),
        max(1.0, _number(arena.get("height", 110.0))),
    )


def resolve_position(value: Any, arena: Mapping[str, Any], default: tuple[float, float] | None = None,
                     *, legacy_absolute: bool = False) -> tuple[float, float]:
    """Resolve an authored position relative to an arena.

    Lists/tuples are local pixels for modern patterns and absolute pixels for
    the legacy timeline adapter.  Mapping values support ``normalized: true``
    (0..1 on each axis), or a per-axis ``x``/``y`` local position.
    """
    left, top, width, height = _bounds_from_arena(arena)
    fallback = default or (left + width / 2.0, top + height / 2.0)
    if value is None:
        return fallback
    if isinstance(value, str):
        named = {
            "center": (0.5, 0.5), "top": (0.5, 0.0), "bottom": (0.5, 1.0),
            "left": (0.0, 0.5), "right": (1.0, 0.5),
        }.get(value.lower())
        if named is not None:
            return left + named[0] * width, top + named[1] * height
        raise DefenseConfigError(f"unknown position {value!r}")
    if isinstance(value, Mapping):
        x, y = value.get("x", 0.5 if value.get("normalized") else width / 2), value.get("y", 0.5 if value.get("normalized") else height / 2)
        normalized = bool(value.get("normalized", False))
        if normalized:
            return left + _number(x) * width, top + _number(y) * height
        if legacy_absolute or bool(value.get("absolute", False)):
            return _number(x), _number(y)
        return left + _number(x), top + _number(y)
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        x, y = _number(value[0]), _number(value[1])
        return (x, y) if legacy_absolute else (left + x, top + y)
    raise DefenseConfigError("position must be a mapping, [x, y], or named position")


def _color(value: Any, fallback: tuple[int, int, int] = (255, 105, 105)) -> tuple[int, int, int]:
    if isinstance(value, str):
        names = {
            "red": (255, 100, 100), "orange": (255, 170, 75), "yellow": (255, 220, 95),
            "blue": (100, 170, 255), "purple": (190, 115, 255), "green": (100, 225, 145),
            "white": (245, 245, 255), "cyan": (95, 225, 235),
        }
        return names.get(value.lower(), fallback)
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        return tuple(max(0, min(255, int(_number(channel)))) for channel in value[:3])  # type: ignore[return-value]
    return fallback


class Hazard(Protocol):
    damage: int
    age: float

    @property
    def active(self) -> bool: ...

    @property
    def expired(self) -> bool: ...

    def update(self, dt: float, arena: tuple[float, float, float, float],
               player_position: tuple[float, float] | None = None) -> None: ...

    def hits(self, x: float, y: float, radius: float) -> bool: ...

    def render_data(self) -> dict[str, Any]: ...


@dataclass
class Projectile:
    """A reusable moving circular/rectangular hazard.

    The first fields intentionally preserve the original public projectile
    constructor used by older tests and integrations.  Extra motion fields
    make one primitive cover straight, homing, curved, accelerated, wave,
    and bouncing bullets.
    """

    x: float
    y: float
    width: float
    height: float
    damage: int
    lifetime: float
    vx: float = 0.0
    vy: float = 0.0
    shape: str = "circle"
    behavior: str = "straight"
    delay: float = 0.0
    acceleration_x: float = 0.0
    acceleration_y: float = 0.0
    sine_amplitude: float = 0.0
    sine_frequency: float = 0.0
    base_y: float = 0.0
    age: float = 0.0
    collision_radius: float | None = None
    sprite: str | None = None
    sprite_scale: float = 1.0
    rotation: float = 0.0
    rotation_mode: str = "none"
    angular_velocity: float = 0.0
    angular_acceleration: float = 0.0
    orbital_center: tuple[float, float] | None = None
    orbital_speed: float = 0.0
    orbital_angle: float = 0.0
    orbital_radius: float = 0.0
    orbital_radial_speed: float = 0.0
    scalar_acceleration: float = 0.0
    min_speed: float | None = None
    max_speed: float | None = None
    drag: float = 0.0
    homing_turn_rate: float = 0.0
    homing_duration: float | None = None
    bounce_count: int = 0
    restitution: float = 1.0
    destroy_outside_arena: bool = True
    wave_amplitude: float = 0.0
    wave_frequency: float = 0.0
    wave_phase: float = 0.0
    color: tuple[int, int, int] = (255, 105, 105)
    hit_once: bool = True
    has_hit: bool = False
    destroyed: bool = False
    bounces: int = 0
    _wave_offset: float = 0.0

    def __post_init__(self) -> None:
        if not self.base_y:
            self.base_y = self.y
        self.width, self.height = max(1.0, self.width), max(1.0, self.height)
        self.lifetime = max(0.0, self.lifetime)
        self.delay = max(0.0, self.delay)
        if self.collision_radius is None:
            self.collision_radius = min(self.width, self.height) / 2.0 if self.shape == "circle" else 0.0

    @property
    def active(self) -> bool:
        return self.age >= self.delay and not self.expired

    @property
    def expired(self) -> bool:
        return self.destroyed or self.age >= self.lifetime

    @property
    def speed(self) -> float:
        return math.hypot(self.vx, self.vy)

    def update(self, dt: float, arena: tuple[float, float, float, float] | None = None,
               player_position: tuple[float, float] | None = None) -> None:
        dt = max(0.0, dt)
        previous_age = self.age
        self.age += dt
        active_dt = max(0.0, min(self.age, self.lifetime) - max(previous_age, self.delay))
        if active_dt <= EPSILON or self.expired:
            return
        if self.orbital_center is not None:
            # Radial-burst orbital motion is expressed in polar coordinates so
            # bullets keep expanding away from their common center while their
            # angle advances. Turning their velocity alone would make them
            # follow a circle instead of an outward spiral.
            previous_x, previous_y = self.x, self.y
            radial_speed = self.orbital_radial_speed
            radial_speed = max(0.0, radial_speed + self.scalar_acceleration * active_dt)
            if self.drag:
                radial_speed = max(0.0, radial_speed * (1.0 - self.drag * active_dt))
            if self.min_speed is not None or self.max_speed is not None:
                radial_speed = clamp(
                    radial_speed,
                    self.min_speed if self.min_speed is not None else 0.0,
                    self.max_speed if self.max_speed is not None else float("inf"),
                )
            self.orbital_radial_speed = radial_speed
            self.orbital_radius += radial_speed * active_dt
            self.orbital_angle += self.orbital_speed * active_dt
            direction_x, direction_y = _angle_vector(self.orbital_angle)
            self.x = self.orbital_center[0] + direction_x * self.orbital_radius
            self.y = self.orbital_center[1] + direction_y * self.orbital_radius
            self.vx = (self.x - previous_x) / active_dt
            self.vy = (self.y - previous_y) / active_dt
            if self.rotation_mode == "velocity" and self.speed > EPSILON:
                self.rotation = _vector_angle(self.vx, self.vy)
            else:
                self.rotation += self.angular_velocity * active_dt
            if arena is not None:
                self._apply_bounds(arena)
            return
        if player_position is not None and self.homing_turn_rate > 0 and (self.homing_duration is None or self.age - self.delay <= self.homing_duration):
            desired = _vector_angle(player_position[0] - self.x, player_position[1] - self.y)
            current = _vector_angle(self.vx, self.vy) if self.speed > EPSILON else desired
            angle = _turn_toward(current, desired, self.homing_turn_rate * active_dt)
            speed = self.speed
            self.vx, self.vy = _angle_vector(angle)
            self.vx *= speed
            self.vy *= speed
        self.vx += self.acceleration_x * active_dt
        self.vy += self.acceleration_y * active_dt
        speed = self.speed
        if self.scalar_acceleration and speed > EPSILON:
            new_speed = max(0.0, speed + self.scalar_acceleration * active_dt)
            self.vx, self.vy = self.vx / speed * new_speed, self.vy / speed * new_speed
            speed = new_speed
        if self.drag and speed > EPSILON:
            damped = max(0.0, speed * (1.0 - self.drag * active_dt))
            self.vx, self.vy = self.vx / speed * damped, self.vy / speed * damped
            speed = damped
        if self.min_speed is not None or self.max_speed is not None:
            speed = self.speed
            if speed > EPSILON:
                limited = clamp(speed, self.min_speed if self.min_speed is not None else 0.0,
                                self.max_speed if self.max_speed is not None else float("inf"))
                self.vx, self.vy = self.vx / speed * limited, self.vy / speed * limited
        if self.angular_velocity or self.angular_acceleration:
            self.angular_velocity += self.angular_acceleration * active_dt
            angle = math.radians(self.angular_velocity * active_dt)
            cosine, sine = math.cos(angle), math.sin(angle)
            self.vx, self.vy = self.vx * cosine - self.vy * sine, self.vx * sine + self.vy * cosine
        self.x += self.vx * active_dt
        self.y += self.vy * active_dt
        if self.behavior == "sine":
            # Legacy vertical sine behavior.  New patterns use wave_* to
            # oscillate perpendicular to any travel direction.
            self.y = self.base_y + self.vy * max(0.0, self.age - self.delay) + self.sine_amplitude * math.sin(self.sine_frequency * self.age)
        if self.wave_amplitude and self.wave_frequency and self.speed > EPSILON:
            direction_x, direction_y = _normal(self.vx, self.vy)
            offset = self.wave_amplitude * math.sin(math.tau * self.wave_frequency * (self.age - self.delay) + self.wave_phase)
            delta = offset - self._wave_offset
            self.x += -direction_y * delta
            self.y += direction_x * delta
            self._wave_offset = offset
        if self.rotation_mode == "velocity" and self.speed > EPSILON:
            self.rotation = _vector_angle(self.vx, self.vy)
        else:
            self.rotation += self.angular_velocity * active_dt
        if arena is not None:
            self._apply_bounds(arena)

    def _apply_bounds(self, arena: tuple[float, float, float, float]) -> None:
        left, top, width, height = arena
        right, bottom = left + width, top + height
        half_x, half_y = self.width / 2.0, self.height / 2.0
        crossed = False
        if self.bounce_count > self.bounces:
            if self.x - half_x < left:
                self.x, self.vx, crossed = left + half_x, abs(self.vx) * self.restitution, True
            elif self.x + half_x > right:
                self.x, self.vx, crossed = right - half_x, -abs(self.vx) * self.restitution, True
            if self.y - half_y < top:
                self.y, self.vy, crossed = top + half_y, abs(self.vy) * self.restitution, True
            elif self.y + half_y > bottom:
                self.y, self.vy, crossed = bottom - half_y, -abs(self.vy) * self.restitution, True
            if crossed:
                self.bounces += 1
        if self.destroy_outside_arena and self.bounce_count <= self.bounces:
            margin = max(self.width, self.height) * 1.5
            if self.x < left - margin or self.x > right + margin or self.y < top - margin or self.y > bottom + margin:
                self.destroyed = True

    def hits(self, x: float, y: float, radius: float) -> bool:
        if not self.active or self.has_hit and self.hit_once:
            return False
        if self.shape == "circle":
            return math.hypot(x - self.x, y - self.y) <= radius + float(self.collision_radius or 0.0)
        nearest_x = clamp(x, self.x - self.width / 2.0, self.x + self.width / 2.0)
        nearest_y = clamp(y, self.y - self.height / 2.0, self.y + self.height / 2.0)
        return math.hypot(x - nearest_x, y - nearest_y) <= radius

    def mark_hit(self) -> None:
        if self.hit_once:
            self.has_hit = True

    def render_data(self) -> dict[str, Any]:
        return {
            "kind": "projectile", "x": self.x, "y": self.y, "width": self.width, "height": self.height,
            "shape": self.shape, "active": self.active, "telegraph": False, "damage": self.damage,
            "sprite": self.sprite, "sprite_scale": self.sprite_scale, "rotation": self.rotation,
            "color": self.color,
        }


@dataclass
class BeamHazard:
    """A line/laser hazard with a shared warning and active phase."""

    origin: tuple[float, float]
    length: float
    width: float
    damage: int
    active_duration: float
    telegraph_duration: float = 0.0
    start_angle: float = 0.0
    end_angle: float | None = None
    sweep_duration: float | None = None
    color: tuple[int, int, int] = (255, 100, 100)
    telegraph_color: tuple[int, int, int] = (255, 205, 90)
    linear_end: tuple[float, float] | None = None
    line_angle: float | None = None
    age: float = 0.0
    hit_once: bool = False
    has_hit: bool = False
    telegraph_alpha: int = 105
    flash_rate: float = 0.0

    @property
    def active(self) -> bool:
        return self.telegraph_duration <= self.age < self.telegraph_duration + self.active_duration

    @property
    def expired(self) -> bool:
        return self.age >= self.telegraph_duration + self.active_duration

    @property
    def telegraphing(self) -> bool:
        return self.age < self.telegraph_duration

    def update(self, dt: float, arena: tuple[float, float, float, float],
               player_position: tuple[float, float] | None = None) -> None:
        del arena, player_position
        self.age += max(0.0, dt)

    def _progress(self) -> float:
        active_age = max(0.0, self.age - self.telegraph_duration)
        return clamp(active_age / max(EPSILON, self.sweep_duration or self.active_duration), 0.0, 1.0)

    def endpoints(self) -> tuple[float, float, float, float]:
        progress = self._progress()
        origin_x, origin_y = self.origin
        angle = self.start_angle
        if self.linear_end is not None:
            origin_x = lerp(origin_x, self.linear_end[0], progress)
            origin_y = lerp(origin_y, self.linear_end[1], progress)
            angle = self.line_angle if self.line_angle is not None else self.start_angle
        elif self.end_angle is not None:
            angle = lerp(self.start_angle, self.end_angle, progress)
        direction_x, direction_y = _angle_vector(angle)
        return origin_x, origin_y, origin_x + direction_x * self.length, origin_y + direction_y * self.length

    def hits(self, x: float, y: float, radius: float) -> bool:
        if not self.active or self.has_hit and self.hit_once:
            return False
        ax, ay, bx, by = self.endpoints()
        return _distance_to_segment(x, y, ax, ay, bx, by) <= self.width / 2.0 + radius

    def mark_hit(self) -> None:
        if self.hit_once:
            self.has_hit = True

    def render_data(self) -> dict[str, Any]:
        ax, ay, bx, by = self.endpoints()
        alpha = 220 if self.active else self.telegraph_alpha
        if self.telegraphing and self.flash_rate > 0 and int(self.age * self.flash_rate) % 2:
            alpha = round(alpha * .45)
        return {
            "kind": "beam", "x1": ax, "y1": ay, "x2": bx, "y2": by, "width": self.width,
            "active": self.active, "telegraph": self.telegraphing, "damage": self.damage,
            "color": self.color if self.active else self.telegraph_color,
            "alpha": alpha,
        }


@dataclass
class ZoneHazard:
    """A reusable circle, rectangle, line, or polygon danger region."""

    shape: str
    x: float
    y: float
    damage: int
    active_duration: float
    telegraph_duration: float = 0.0
    width: float = 0.0
    height: float = 0.0
    radius: float = 0.0
    angle: float = 0.0
    length: float = 0.0
    points: list[tuple[float, float]] = field(default_factory=list)
    color: tuple[int, int, int] = (255, 95, 95)
    telegraph_color: tuple[int, int, int] = (255, 205, 90)
    vx: float = 0.0
    vy: float = 0.0
    hit_once: bool = False
    has_hit: bool = False
    age: float = 0.0
    telegraph_alpha: int = 85
    flash_rate: float = 0.0

    @property
    def active(self) -> bool:
        return self.telegraph_duration <= self.age < self.telegraph_duration + self.active_duration

    @property
    def expired(self) -> bool:
        return self.age >= self.telegraph_duration + self.active_duration

    @property
    def telegraphing(self) -> bool:
        return self.age < self.telegraph_duration

    def update(self, dt: float, arena: tuple[float, float, float, float],
               player_position: tuple[float, float] | None = None) -> None:
        del arena, player_position
        dt = max(0.0, dt)
        self.age += dt
        self.x += self.vx * dt
        self.y += self.vy * dt
        if self.points and (self.vx or self.vy):
            self.points = [(point_x + self.vx * dt, point_y + self.vy * dt) for point_x, point_y in self.points]

    def hits(self, x: float, y: float, radius: float) -> bool:
        if not self.active or self.has_hit and self.hit_once:
            return False
        if self.shape == "circle":
            return math.hypot(x - self.x, y - self.y) <= self.radius + radius
        if self.shape in {"rectangle", "rect", "strip"}:
            nearest_x = clamp(x, self.x - self.width / 2.0, self.x + self.width / 2.0)
            nearest_y = clamp(y, self.y - self.height / 2.0, self.y + self.height / 2.0)
            return math.hypot(x - nearest_x, y - nearest_y) <= radius
        if self.shape == "line":
            dx, dy = _angle_vector(self.angle)
            return _distance_to_segment(x, y, self.x, self.y, self.x + dx * self.length, self.y + dy * self.length) <= self.width / 2.0 + radius
        if self.shape == "polygon":
            return _point_in_polygon(x, y, self.points)
        return False

    def mark_hit(self) -> None:
        if self.hit_once:
            self.has_hit = True

    def render_data(self) -> dict[str, Any]:
        alpha = 190 if self.active else self.telegraph_alpha
        if self.telegraphing and self.flash_rate > 0 and int(self.age * self.flash_rate) % 2:
            alpha = round(alpha * .45)
        return {
            "kind": "zone", "shape": self.shape, "x": self.x, "y": self.y, "width": self.width,
            "height": self.height, "radius": self.radius, "angle": self.angle, "length": self.length,
            "points": list(self.points), "active": self.active, "telegraph": self.telegraphing,
            "damage": self.damage, "color": self.color if self.active else self.telegraph_color,
            "alpha": alpha,
        }


@dataclass
class MovingGapWallHazard:
    """One moving wall whose safe opening changes while it crosses the arena."""

    bounds: tuple[float, float, float, float]
    direction: str
    speed: float
    thickness: float
    gap_width: float
    gap_center: float
    gap_motion_speed: float
    gap_style: str
    gap_bounds: tuple[float, float]
    damage: int
    lifetime: float
    color: tuple[int, int, int] = (255, 105, 105)
    age: float = 0.0
    hit_once: bool = False
    has_hit: bool = False

    @property
    def active(self) -> bool:
        return not self.expired

    @property
    def expired(self) -> bool:
        return self.age >= self.lifetime

    @property
    def horizontal(self) -> bool:
        return self.direction in {"top_to_bottom", "bottom_to_top", "down", "up"}

    def _wall_axis(self) -> float:
        left, top, width, height = self.bounds
        if self.direction in {"bottom_to_top", "up"}:
            return top + height + self.thickness / 2.0 - self.speed * self.age
        if self.direction in {"right_to_left", "left"}:
            return left + width + self.thickness / 2.0 - self.speed * self.age
        if self.horizontal:
            return top - self.thickness / 2.0 + self.speed * self.age
        return left - self.thickness / 2.0 + self.speed * self.age

    def current_gap_center(self) -> float:
        lower, upper = self.gap_bounds
        if self.gap_style in {"random", "jitter"}:
            # Deterministic, smoothly changing pseudo-random motion avoids
            # surprise teleports while retaining an authored random feel.
            value = math.sin(self.age * self.gap_motion_speed * 2.17 + self.gap_center * .13) * .5 + .5
            return lerp(lower, upper, value)
        if self.gap_style in {"linear", "slide"}:
            span = max(EPSILON, upper - lower)
            return lower + (self.gap_center - lower + self.gap_motion_speed * self.age) % span
        amplitude = min((upper - lower) / 2.0, max(0.0, (upper - lower) / 2.0))
        frequency = abs(self.gap_motion_speed) / max(1.0, upper - lower)
        return clamp(self.gap_center + math.sin(math.tau * frequency * self.age) * amplitude, lower, upper)

    def _pieces(self) -> list[tuple[float, float, float, float]]:
        left, top, width, height = self.bounds
        cross_length = width if self.horizontal else height
        gap_center = self.current_gap_center()
        safe_start = clamp(gap_center - self.gap_width / 2.0, 0.0, cross_length)
        safe_end = clamp(gap_center + self.gap_width / 2.0, 0.0, cross_length)
        ranges = [(0.0, safe_start), (safe_end, cross_length)]
        axis = self._wall_axis()
        pieces: list[tuple[float, float, float, float]] = []
        for start, end in ranges:
            if end <= start + EPSILON:
                continue
            if self.horizontal:
                pieces.append((left + start, axis - self.thickness / 2.0, end - start, self.thickness))
            else:
                pieces.append((axis - self.thickness / 2.0, top + start, self.thickness, end - start))
        return pieces

    def update(self, dt: float, arena: tuple[float, float, float, float],
               player_position: tuple[float, float] | None = None) -> None:
        del arena, player_position
        self.age += max(0.0, dt)

    def hits(self, x: float, y: float, radius: float) -> bool:
        if not self.active or self.has_hit and self.hit_once:
            return False
        for piece_x, piece_y, piece_width, piece_height in self._pieces():
            nearest_x = clamp(x, piece_x, piece_x + piece_width)
            nearest_y = clamp(y, piece_y, piece_y + piece_height)
            if math.hypot(x - nearest_x, y - nearest_y) <= radius:
                return True
        return False

    def mark_hit(self) -> None:
        if self.hit_once:
            self.has_hit = True

    def render_data(self) -> dict[str, Any]:
        return {"kind": "moving_gap_wall", "pieces": self._pieces(), "active": self.active,
                "telegraph": False, "damage": self.damage, "color": self.color, "alpha": 215}


@dataclass
class RingHazard:
    """An annular expanding or contracting danger zone."""

    center: tuple[float, float]
    radius: float
    expansion_speed: float
    thickness: float
    damage: int
    active_duration: float
    telegraph_duration: float = 0.0
    gaps: list[tuple[float, float]] = field(default_factory=list)
    color: tuple[int, int, int] = (255, 105, 105)
    telegraph_color: tuple[int, int, int] = (255, 205, 90)
    age: float = 0.0
    hit_once: bool = False
    has_hit: bool = False
    telegraph_alpha: int = 90
    flash_rate: float = 0.0

    @property
    def active(self) -> bool:
        return self.telegraph_duration <= self.age < self.telegraph_duration + self.active_duration

    @property
    def expired(self) -> bool:
        return self.age >= self.telegraph_duration + self.active_duration

    @property
    def telegraphing(self) -> bool:
        return self.age < self.telegraph_duration

    @property
    def current_radius(self) -> float:
        return max(0.0, self.radius + self.expansion_speed * max(0.0, self.age - self.telegraph_duration))

    def update(self, dt: float, arena: tuple[float, float, float, float],
               player_position: tuple[float, float] | None = None) -> None:
        del arena, player_position
        self.age += max(0.0, dt)

    def _in_gap(self, angle: float) -> bool:
        value = angle % 360.0
        for start, end in self.gaps:
            start, end = start % 360.0, end % 360.0
            if start <= end and start <= value <= end:
                return True
            if start > end and (value >= start or value <= end):
                return True
        return False

    def hits(self, x: float, y: float, radius: float) -> bool:
        if not self.active or self.has_hit and self.hit_once:
            return False
        dx, dy = x - self.center[0], y - self.center[1]
        if self._in_gap(_vector_angle(dx, dy)):
            return False
        return abs(math.hypot(dx, dy) - self.current_radius) <= self.thickness / 2.0 + radius

    def mark_hit(self) -> None:
        if self.hit_once:
            self.has_hit = True

    def render_data(self) -> dict[str, Any]:
        alpha = 210 if self.active else self.telegraph_alpha
        if self.telegraphing and self.flash_rate > 0 and int(self.age * self.flash_rate) % 2:
            alpha = round(alpha * .45)
        return {
            "kind": "ring", "x": self.center[0], "y": self.center[1], "radius": self.current_radius,
            "thickness": self.thickness, "gaps": list(self.gaps), "active": self.active,
            "telegraph": self.telegraphing, "damage": self.damage,
            "color": self.color if self.active else self.telegraph_color,
            "alpha": alpha,
        }


@dataclass
class OrbitingHazard:
    """A circular hazard rotating around a fixed point or the player."""

    center: tuple[float, float]
    orbit_radius: float
    angle: float
    angular_speed: float
    radius: float
    damage: int
    lifetime: float
    follow_player: bool = False
    color: tuple[int, int, int] = (190, 115, 255)
    age: float = 0.0
    hit_once: bool = False
    has_hit: bool = False

    @property
    def active(self) -> bool:
        return not self.expired

    @property
    def expired(self) -> bool:
        return self.age >= self.lifetime

    @property
    def position(self) -> tuple[float, float]:
        dx, dy = _angle_vector(self.angle)
        return self.center[0] + dx * self.orbit_radius, self.center[1] + dy * self.orbit_radius

    def update(self, dt: float, arena: tuple[float, float, float, float],
               player_position: tuple[float, float] | None = None) -> None:
        del arena
        dt = max(0.0, dt)
        self.age += dt
        self.angle += self.angular_speed * dt
        if self.follow_player and player_position is not None:
            self.center = player_position

    def hits(self, x: float, y: float, radius: float) -> bool:
        if not self.active or self.has_hit and self.hit_once:
            return False
        px, py = self.position
        return math.hypot(x - px, y - py) <= self.radius + radius

    def mark_hit(self) -> None:
        if self.hit_once:
            self.has_hit = True

    def render_data(self) -> dict[str, Any]:
        x, y = self.position
        return {"kind": "orbit", "x": x, "y": y, "radius": self.radius, "active": self.active,
                "telegraph": False, "damage": self.damage, "color": self.color, "alpha": 220}


@dataclass
class ArenaConstraint:
    """A temporary movement-boundary modifier; it never mutates battle YAML."""

    base_bounds: tuple[float, float, float, float]
    target_bounds: tuple[float, float, float, float]
    shrink_duration: float
    hold_duration: float
    restoration_duration: float
    age: float = 0.0

    damage: int = 0

    @property
    def active(self) -> bool:
        return not self.expired

    @property
    def expired(self) -> bool:
        return self.age >= self.shrink_duration + self.hold_duration + self.restoration_duration

    def update(self, dt: float, arena: tuple[float, float, float, float],
               player_position: tuple[float, float] | None = None) -> None:
        del arena, player_position
        self.age += max(0.0, dt)

    def bounds(self) -> tuple[float, float, float, float]:
        if self.shrink_duration > EPSILON and self.age < self.shrink_duration:
            amount = self.age / self.shrink_duration
        elif self.age < self.shrink_duration + self.hold_duration or self.restoration_duration <= EPSILON:
            amount = 1.0
        else:
            amount = 1.0 - (self.age - self.shrink_duration - self.hold_duration) / self.restoration_duration
        return tuple(lerp(start, end, amount) for start, end in zip(self.base_bounds, self.target_bounds))  # type: ignore[return-value]

    def hits(self, x: float, y: float, radius: float) -> bool:
        del x, y, radius
        return False

    def render_data(self) -> dict[str, Any]:
        x, y, width, height = self.bounds()
        return {"kind": "arena_constraint", "x": x, "y": y, "width": width, "height": height,
                "active": self.active, "telegraph": False, "damage": 0, "color": (135, 190, 255), "alpha": 155}


@dataclass(order=True)
class ScheduledEvent:
    """Compatibility timeline event, retained for older battle YAML."""

    at: float
    action: dict[str, Any] = field(compare=False)


@dataclass
class DefenseResult:
    completed: bool = False
    hit_damage: list[int] = field(default_factory=list)
    dialogue: list[str] = field(default_factory=list)


@dataclass(order=True)
class PatternEvent:
    """One compiled, scheduled YAML pattern entry."""

    start: float
    config: dict[str, Any] = field(compare=False)


def _duration_for_entry(entry: Mapping[str, Any]) -> float:
    value = entry.get("duration")
    if value is not None:
        return max(0.0, _number(value))
    # A single-shot pattern is still visible for enough time to be useful;
    # sustained emitter defaults are deliberately short but nonzero.
    return 0.0 if entry.get("type") in {"radial_burst", "telegraph_strike", "expanding_ring", "contracting_ring", "shrinking_arena", "orbiting_hazards", "maze_corridor"} else 1.5


def _repeat_entries(entry: Mapping[str, Any]) -> Iterable[tuple[float, dict[str, Any]]]:
    """Yield copies of an entry at each repeat offset.

    ``count`` means total occurrences, not extra repeats.  A bare entry is
    therefore equivalent to ``count: 1``.  ``delay`` is an optional offset
    before the first occurrence and makes delayed groups readable.
    """
    repeat = entry.get("repeat")
    base = deepcopy(dict(entry))
    base.pop("repeat", None)
    if repeat is None:
        yield 0.0, base
        return
    if not isinstance(repeat, Mapping):
        raise DefenseConfigError("repeat must be a mapping")
    count = int(_number(repeat.get("count", 1), 1))
    interval = _positive(repeat.get("interval", 0.0), 0.0)
    delay = _positive(repeat.get("delay", 0.0), 0.0)
    if count < 0:
        raise DefenseConfigError("repeat.count must be non-negative")
    if count > 1 and interval <= EPSILON:
        raise DefenseConfigError("repeat.interval must be positive when count is greater than one")
    for index in range(count):
        yield delay + index * interval, deepcopy(base)


def compile_pattern_events(sequence: Mapping[str, Any], difficulty: str | int | None = None) -> list[PatternEvent]:
    """Flatten pattern groups and repeats into independent scheduled events."""
    resolved_sequence = apply_difficulty_overrides(sequence, difficulty)
    groups = resolved_sequence.get("pattern_groups", {})
    if groups is None:
        groups = {}
    if not isinstance(groups, Mapping):
        raise DefenseConfigError("pattern_groups must be a mapping")
    entries = resolved_sequence.get("patterns", [])
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise DefenseConfigError("patterns must be a list")
    events: list[PatternEvent] = []

    def expand(raw_entries: list[Any], parent_start: float, stack: tuple[str, ...]) -> None:
        for raw_entry in raw_entries:
            if not isinstance(raw_entry, Mapping):
                raise DefenseConfigError("pattern entry must be a mapping")
            authored = apply_difficulty_overrides(raw_entry, difficulty)
            for repeat_offset, entry in _repeat_entries(authored):
                start = parent_start + _positive(entry.get("start", 0.0), 0.0) + repeat_offset
                group_name = entry.get("group")
                if group_name is not None:
                    if not isinstance(group_name, str) or not group_name:
                        raise DefenseConfigError("pattern group must be a non-empty string")
                    if group_name not in groups:
                        raise DefenseConfigError(f"unknown pattern group {group_name!r}")
                    if group_name in stack:
                        chain = " -> ".join((*stack, group_name))
                        raise DefenseConfigError(f"pattern group recursion: {chain}")
                    group_entries = groups[group_name]
                    if not isinstance(group_entries, list):
                        raise DefenseConfigError(f"pattern group {group_name!r} must be a list")
                    expand(group_entries, start, (*stack, group_name))
                    continue
                pattern_type = entry.get("type")
                if not isinstance(pattern_type, str) or not pattern_type:
                    raise DefenseConfigError("pattern entry needs type or group")
                events.append(PatternEvent(start, entry))

    expand(entries, 0.0, ())
    return sorted(events)


class BaseDefensePattern:
    """Pure runtime contract shared by every registered defend pattern."""

    type_name = "base"
    default_duration = 1.5

    def __init__(self, sequence: "DefenseSequence", config: Mapping[str, Any]):
        self.sequence = sequence
        self.config = dict(config)
        self.elapsed = 0.0
        self.duration = max(0.0, _number(self.config.get("duration", self.default_duration), self.default_duration))
        self.started = False
        self.finished = False

    def start(self) -> None:
        self.started = True
        if self.duration <= EPSILON:
            self.finished = True

    def update(self, dt: float) -> None:
        self.elapsed += max(0.0, dt)
        if self.elapsed >= self.duration - EPSILON:
            self.finished = True

    def finish(self) -> None:
        self.finished = True

    def is_finished(self) -> bool:
        return self.finished

    def next_event_delta(self) -> float | None:
        """Return the next internal spawn boundary, relative to now."""
        return None

    def position(self, value: Any = None, default: tuple[float, float] | None = None) -> tuple[float, float]:
        return self.sequence.resolve_position(resolve_random_value(value, self.sequence.rng), default)

    def value(self, value: Any) -> Any:
        """Sample an authored range/choice at the point it is used."""
        return resolve_random_value(value, self.sequence.rng)

    def projectile_spec(self) -> dict[str, Any]:
        value = self.config.get("projectile", {})
        if not isinstance(value, Mapping):
            raise DefenseConfigError(f"{self.type_name}.projectile must be a mapping")
        return dict(value)


class PeriodicDefensePattern(BaseDefensePattern):
    """Shared timer for emitters and repeated telegraph patterns."""

    interval_keys: tuple[str, ...] = ("fire_interval", "interval")
    start_immediately = True

    def __init__(self, sequence: "DefenseSequence", config: Mapping[str, Any]):
        super().__init__(sequence, config)
        self.interval = self._interval()
        self._next_fire: float | None = None
        self._fire_count = 0

    def _interval(self) -> float:
        for key in self.interval_keys:
            if key in self.config:
                return max(0.0, _number(self.config[key]))
        return 0.0

    def start(self) -> None:
        super().start()
        initial_delay = max(0.0, _number(self.config.get("initial_delay", 0.0)))
        self._next_fire = initial_delay
        if self.start_immediately and initial_delay <= EPSILON:
            self.fire(0.0)
            self._fire_count += 1
            self._next_fire = self.interval if self.interval > EPSILON else None

    def fire(self, at: float) -> None:
        del at

    def next_event_delta(self) -> float | None:
        if self._next_fire is None or self._next_fire > self.duration + EPSILON:
            return None
        return max(0.0, self._next_fire - self.elapsed)

    def update(self, dt: float) -> None:
        old = self.elapsed
        new = min(self.duration, old + max(0.0, dt))
        self.elapsed = new
        if self._next_fire is not None:
            while self._next_fire <= new + EPSILON and self._next_fire <= self.duration + EPSILON:
                if self._next_fire > old + EPSILON or (not self.start_immediately and self._fire_count == 0):
                    self.fire(self._next_fire)
                    self._fire_count += 1
                if self.interval <= EPSILON:
                    self._next_fire = None
                    break
                self._next_fire += self.interval
        if self.elapsed >= self.duration - EPSILON:
            self.finished = True


PATTERN_TYPES: dict[str, type[BaseDefensePattern]] = {}
# A more explicit public spelling for new integrations.
DEFENSE_PATTERN_TYPES = PATTERN_TYPES


def register_defense_pattern(*names: str) -> Callable[[type[BaseDefensePattern]], type[BaseDefensePattern]]:
    """Register a class under one primary YAML type and optional aliases."""
    def decorate(pattern_class: type[BaseDefensePattern]) -> type[BaseDefensePattern]:
        if not names:
            raise ValueError("a defense pattern needs at least one registry name")
        for name in names:
            PATTERN_TYPES[name] = pattern_class
        pattern_class.type_name = names[0]
        return pattern_class
    return decorate


def create_defense_pattern(sequence: "DefenseSequence", config: Mapping[str, Any]) -> BaseDefensePattern:
    pattern_type = config.get("type")
    if not isinstance(pattern_type, str):
        raise DefenseConfigError("pattern type must be a string")
    try:
        pattern_class = PATTERN_TYPES[pattern_type]
    except KeyError as exc:
        raise DefenseConfigError(f"unknown defense pattern type {pattern_type!r}") from exc
    return pattern_class(sequence, config)


def _split_spread(center: float, count: int, spread: float) -> list[float]:
    if count <= 1:
        return [center]
    return [center - spread / 2.0 + spread * index / (count - 1) for index in range(count)]


class ProjectileEmitterPattern(PeriodicDefensePattern):
    """Base helper for pattern families that create one or more bullets."""

    def _projectile_count(self) -> int:
        return max(1, int(_number(self.value(self.config.get("projectile_count", self.config.get("count", 1))), 1)))

    def _spread(self) -> float:
        return max(0.0, _number(self.value(self.config.get("spread", self.config.get("spread_degrees", 0.0)))))

    def _origin(self) -> tuple[float, float]:
        value = self.config.get("origin", self.config.get("position"))
        return self.position(value)

    def _spawn_angles(self, origin: tuple[float, float], center_angle: float, spec: Mapping[str, Any]) -> None:
        for angle in _split_spread(center_angle, self._projectile_count(), self._spread()):
            self.sequence.spawn_projectile(spec, origin, angle=angle)


@register_defense_pattern("aimed_stream", "aimed_projectile_stream")
class AimedStreamPattern(ProjectileEmitterPattern):
    """Repeated bullets aimed at current (or lightly randomized) player position."""

    def fire(self, at: float) -> None:
        del at
        origin = self._origin()
        target_x, target_y = self.sequence.player_x, self.sequence.player_y
        jitter = _number(self.value(self.config.get("aim_jitter", self.config.get("random_aim", 0.0))))
        if jitter:
            target_x += self.sequence.rng.uniform(-jitter, jitter)
            target_y += self.sequence.rng.uniform(-jitter, jitter)
        angle = _vector_angle(target_x - origin[0], target_y - origin[1])
        self._spawn_burst(origin, angle, self.projectile_spec())

    def _spawn_burst(self, origin: tuple[float, float], angle: float, spec: Mapping[str, Any]) -> None:
        burst_count = max(1, int(_number(self.value(self.config.get("burst_count", 1)), 1)))
        burst_interval = max(0.0, _number(self.value(self.config.get("burst_interval", 0.0))))
        for index in range(burst_count):
            shot = dict(spec)
            if burst_interval:
                shot["delay"] = _number(shot.get("delay", 0.0)) + index * burst_interval
            self._spawn_angles(origin, angle, shot)


@register_defense_pattern("predictive_stream", "leading_stream")
class PredictiveStreamPattern(AimedStreamPattern):
    """Aimed stream that leads the player's actual recent velocity."""

    def fire(self, at: float) -> None:
        del at
        origin = self._origin()
        spec = self.projectile_spec()
        speed = max(1.0, _number(spec.get("speed", 90.0), 90.0))
        strength = _number(self.config.get("prediction_strength", 1.0), 1.0)
        weighting = _number(self.config.get("player_velocity_weighting", self.config.get("player_velocity_weight", 1.0)), 1.0)
        lead = min(1.5, math.hypot(self.sequence.player_x - origin[0], self.sequence.player_y - origin[1]) / speed) * strength
        target_x = self.sequence.player_x + self.sequence.player_velocity_x * lead * weighting
        target_y = self.sequence.player_y + self.sequence.player_velocity_y * lead * weighting
        angle = _vector_angle(target_x - origin[0], target_y - origin[1])
        self._spawn_burst(origin, angle, spec)


@register_defense_pattern("radial_burst")
class RadialBurstPattern(PeriodicDefensePattern):
    interval_keys = ("burst_interval", "fire_interval", "interval")
    default_duration = 0.0
    _VECTOR_FIELDS = frozenset({
        "acceleration", "color", "direction", "origin", "position", "size",
        "spawn", "target", "velocity",
    })

    def __init__(self, sequence: "DefenseSequence", config: Mapping[str, Any]):
        super().__init__(sequence, config)
        self.duration = max(0.0, _number(config.get("duration", 0.0)))
        raw_bursts = config.get("bursts", [])
        if not isinstance(raw_bursts, list) or any(not isinstance(burst, Mapping) for burst in raw_bursts):
            raise DefenseConfigError("radial_burst.bursts must be a list of mappings")
        self.bursts = [dict(burst) for burst in raw_bursts]
        self.initial_rotation_angle = self._burst_number(config.get("initial_rotation_angle", 0.0))
        self.orbital_speed = self._burst_number(config.get("orbital_speed", 0.0))
        self.repetitions = (
            max(1, int(_number(config["repetitions"], 1)))
            if "repetitions" in config else None
        )
        if self.repetitions is not None and not self.bursts:
            raise DefenseConfigError("radial_burst.repetitions requires at least one bursts entry")
        self.emission_count = len(self.bursts) * (self.repetitions or 1) if self.bursts else None
        if self.emission_count is not None and self.emission_count > 1:
            if self.interval <= EPSILON:
                raise DefenseConfigError("radial_burst with multiple bursts requires burst_interval")
            self.duration = max(self.duration, self.interval * (self.emission_count - 1))
        # An omitted duration is an intentionally one-shot burst.

    def _resolve_burst_ranges(self, value: Any, key: str | None = None) -> Any:
        """Resolve `[minimum, maximum]` ranges used by radial-burst overrides."""
        value = resolve_random_value(value, self.sequence.rng)
        if (
            key not in self._VECTOR_FIELDS
            and isinstance(value, (list, tuple))
            and len(value) == 2
            and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)
        ):
            return self.sequence.rng.uniform(float(value[0]), float(value[1]))
        if isinstance(value, Mapping):
            return {name: self._resolve_burst_ranges(item, str(name)) for name, item in value.items()}
        if isinstance(value, list):
            return [self._resolve_burst_ranges(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._resolve_burst_ranges(item) for item in value)
        return value

    def _burst_number(self, value: Any, default: float = 0.0) -> float:
        return _number(self._resolve_burst_ranges(value), default)

    def _burst_override(self) -> Mapping[str, Any]:
        return self.bursts[self._fire_count % len(self.bursts)] if self.bursts else {}

    def fire(self, at: float) -> None:
        del at
        if self.emission_count is not None and self._fire_count >= self.emission_count:
            return
        override = self._burst_override()
        count = max(1, int(_number(self.value(self.config.get("projectile_count", self.config.get("count", 12))), 12)))
        start = _number(self.value(self.config.get("starting_angle", self.config.get("start_angle", self.config.get("angle", 0.0)))))
        start += self._burst_number(override["initial_rotation_angle"]) if "initial_rotation_angle" in override else self.initial_rotation_angle
        start += _number(self.value(self.config.get("angular_offset", 0.0))) * self._fire_count
        start += _number(self.config.get("rotation_per_burst", 0.0)) * self._fire_count
        origin = self.position(self.config.get("origin", self.config.get("position")))
        spec = self.projectile_spec()
        if "projectile" in override:
            if not isinstance(override["projectile"], Mapping):
                burst_index = self._fire_count % len(self.bursts)
                raise DefenseConfigError(f"radial_burst.bursts[{burst_index}].projectile must be a mapping")
            spec = _deep_merge(spec, override["projectile"])
        spec = self._resolve_burst_ranges(spec)
        orbital_speed = self._burst_number(override["orbital_speed"]) if "orbital_speed" in override else self.orbital_speed
        if orbital_speed:
            spec = dict(spec)
            motion = spec.get("motion", {})
            if not isinstance(motion, Mapping):
                motion = {}
            spec["motion"] = {
                **motion,
                "orbital_center": origin,
                "orbital_speed": orbital_speed,
            }
        for index in range(count):
            self.sequence.spawn_projectile(spec, origin, angle=start + 360.0 * index / count)


@register_defense_pattern("spiral", "rotating_emitter")
class SpiralPattern(ProjectileEmitterPattern):
    def fire(self, at: float) -> None:
        origin = self._origin()
        start = _number(self.config.get("angle_offset", self.config.get("starting_angle", 0.0)))
        clockwise = self.config.get("clockwise")
        direction = -1.0 if (clockwise is False or str(self.config.get("direction", "clockwise")).lower() in {"counterclockwise", "ccw", "reverse"}) else 1.0
        angular_speed = _number(self.config.get("angular_speed", 90.0)) * direction
        angular_acceleration = _number(self.config.get("angular_acceleration", 0.0)) * direction
        center = start + angular_speed * at + angular_acceleration * at * at / 2.0
        arms = max(1, int(_number(self.config.get("arms", self.config.get("projectile_count", 1)), 1)))
        spec = self.projectile_spec()
        for index in range(arms):
            self.sequence.spawn_projectile(spec, origin, angle=center + index * 360.0 / arms)


@register_defense_pattern("falling_rain", "rising_rain", "rain")
class RainPattern(PeriodicDefensePattern):
    interval_keys = ("spawn_interval", "fire_interval", "interval")

    def _direction(self) -> str:
        raw = str(self.config.get("direction", "top_to_bottom")).lower()
        if self.type_name == "rising_rain" and "direction" not in self.config:
            return "bottom_to_top"
        return {"falling": "top_to_bottom", "rising": "bottom_to_top", "down": "top_to_bottom", "up": "bottom_to_top"}.get(raw, raw)

    def fire(self, at: float) -> None:
        del at
        left, top, width, height = self.sequence.base_bounds
        direction = self._direction()
        spread = max(0.0, _number(self.config.get("spread", 0.0)))
        distribution = str(self.config.get("spawn_distribution", "random")).lower()

        def sampled(length: float) -> float:
            roll = self.sequence.rng.random()
            if distribution in {"left_bias", "top_bias"}:
                return length * roll * roll
            if distribution in {"right_bias", "bottom_bias"}:
                return length * (1.0 - (1.0 - roll) * (1.0 - roll))
            if distribution in {"center", "center_bias"}:
                return length * clamp(.5 + (roll - .5) * (roll if roll > .5 else 1.0 - roll), 0.0, 1.0)
            return length * roll
        if direction in {"top_to_bottom", "down"}:
            origin = (left + sampled(width), top - 2)
            angle = 90.0 + self.sequence.rng.uniform(-spread, spread)
        elif direction in {"bottom_to_top", "up"}:
            origin = (left + sampled(width), top + height + 2)
            angle = -90.0 + self.sequence.rng.uniform(-spread, spread)
        elif direction in {"left_to_right", "right"}:
            origin = (left - 2, top + sampled(height))
            angle = 0.0 + self.sequence.rng.uniform(-spread, spread)
        else:
            origin = (left + width + 2, top + sampled(height))
            angle = 180.0 + self.sequence.rng.uniform(-spread, spread)
        self.sequence.spawn_projectile(self.projectile_spec(), origin, angle=angle)


@register_defense_pattern("crossfire")
class CrossfirePattern(PeriodicDefensePattern):
    interval_keys = ("fire_interval", "spawn_interval", "interval")

    def fire(self, at: float) -> None:
        del at
        sides = self.config.get("enabled_sides", self.config.get("sides", ["top", "bottom", "left", "right"]))
        if not isinstance(sides, list):
            sides = [sides]
        spec = self.projectile_spec()
        stagger = max(0.0, _number(self.config.get("stagger", self.config.get("stagger_timing", 0.0)), 0.0))
        configured_angles = self.config.get("angles", [])
        for index, side in enumerate(sides):
            origin = self.sequence.edge_position(str(side), random_axis=True)
            targeting = str(self.config.get("targeting", self.config.get("aiming", "player_position")))
            if targeting in {"player", "player_position", "aimed"}:
                angle = _vector_angle(self.sequence.player_x - origin[0], self.sequence.player_y - origin[1])
            else:
                angle = (_number(configured_angles[index]) if isinstance(configured_angles, list) and index < len(configured_angles)
                         else {"top": 90.0, "bottom": -90.0, "left": 0.0, "right": 180.0}.get(str(side).lower(), 90.0))
            shot_spec = dict(spec)
            if stagger:
                shot_spec["delay"] = _number(shot_spec.get("delay", 0.0)) + index * stagger
            self.sequence.spawn_projectile(shot_spec, origin, angle=angle)


class GapWallPattern(PeriodicDefensePattern):
    interval_keys = ("wall_interval", "spawn_interval", "interval")
    default_duration = 0.0

    def _direction(self) -> str:
        return str(self.config.get("direction", "top_to_bottom")).lower()

    def _gap_center(self, at: float, cross_length: float) -> float:
        del at
        value = self.value(self.config.get("gap_position", "random" if self.config.get("random_gap_position") else 0.5))
        if value == "random":
            return self.sequence.rng.uniform(0.15 * cross_length, 0.85 * cross_length)
        if isinstance(value, Mapping) and value.get("normalized"):
            return clamp(_number(value.get("value", value.get("x", value.get("y", 0.5)))) * cross_length, 0.0, cross_length)
        number = _number(value, cross_length / 2.0)
        return number * cross_length if 0.0 <= number <= 1.0 else clamp(number, 0.0, cross_length)

    def fire(self, at: float) -> None:
        left, top, width, height = self.sequence.base_bounds
        direction = self._direction()
        horizontal = direction in {"top_to_bottom", "bottom_to_top", "down", "up"}
        cross_length = width if horizontal else height
        gap_width = max(1.0, _number(self.config.get("gap_width", 22.0), 22.0))
        number_of_gaps = max(1, int(_number(self.config.get("number_of_gaps", self.config.get("gaps", 1)), 1)))
        centers = [self._gap_center(at + index * .13, cross_length) for index in range(number_of_gaps)]
        safe_ranges = sorted((clamp(center - gap_width / 2, 0, cross_length), clamp(center + gap_width / 2, 0, cross_length)) for center in centers)
        merged: list[tuple[float, float]] = []
        for start, end in safe_ranges:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        pieces: list[tuple[float, float]] = []
        cursor = 0.0
        for start, end in merged:
            if start > cursor + EPSILON:
                pieces.append((cursor, start))
            cursor = max(cursor, end)
        if cursor < cross_length - EPSILON:
            pieces.append((cursor, cross_length))
        thickness = max(2.0, _number(self.config.get("wall_thickness", self.config.get("projectile_size", 10.0)), 10.0))
        speed = max(1.0, _number(self.config.get("wall_speed", self.config.get("speed", 75.0)), 75.0))
        damage = int(_number(self.config.get("damage", self.projectile_spec().get("damage", 3)), 3))
        lifetime = _number(self.config.get("lifetime", (height if horizontal else width) / speed + 0.5))
        color = _color(self.config.get("color"))
        for start, end in pieces:
            span = max(1.0, end - start)
            if horizontal:
                x = left + start + span / 2.0
                if direction in {"bottom_to_top", "up"}:
                    y, vx, vy = top + height + thickness / 2.0, 0.0, -speed
                else:
                    y, vx, vy = top - thickness / 2.0, 0.0, speed
                projectile = Projectile(x, y, span, thickness, damage, lifetime, vx, vy, "rectangle", color=color,
                                        destroy_outside_arena=True, hit_once=False)
            else:
                y = top + start + span / 2.0
                if direction in {"right_to_left", "left"}:
                    x, vx, vy = left + width + thickness / 2.0, -speed, 0.0
                else:
                    x, vx, vy = left - thickness / 2.0, speed, 0.0
                projectile = Projectile(x, y, thickness, span, damage, lifetime, vx, vy, "rectangle", color=color,
                                        destroy_outside_arena=True, hit_once=False)
            self.sequence.add_hazard(projectile)


@register_defense_pattern("gap_wall")
class RegisteredGapWallPattern(GapWallPattern):
    pass


@register_defense_pattern("moving_gap_wall")
class MovingGapWallPattern(GapWallPattern):
    def _gap_center(self, at: float, cross_length: float) -> float:
        base = super()._gap_center(0.0, cross_length)
        speed = _number(self.config.get("gap_movement_speed", self.config.get("gap_speed", 0.25)), 0.25)
        style = str(self.config.get("movement_style", self.config.get("gap_movement", self.config.get("movement", "oscillate")))).lower()
        bounds = self.config.get("gap_bounds")
        lower, upper = 0.0, cross_length
        if isinstance(bounds, (list, tuple)) and len(bounds) >= 2:
            lower, upper = clamp(_number(bounds[0]), 0.0, cross_length), clamp(_number(bounds[1]), 0.0, cross_length)
            if lower > upper:
                lower, upper = upper, lower
        if style in {"random", "jitter"}:
            return self.sequence.rng.uniform(max(lower, 0.12 * cross_length), min(upper, 0.88 * cross_length))
        if style in {"linear", "slide"}:
            span = max(EPSILON, upper - lower)
            return lower + (base - lower + speed * at * cross_length) % span
        amplitude = _number(self.config.get("gap_movement_amplitude", cross_length * 0.32), cross_length * 0.32)
        return clamp(base + math.sin(at * math.tau * speed) * amplitude, lower, upper)

    def fire(self, at: float) -> None:
        del at
        left, top, width, height = self.sequence.base_bounds
        direction = self._direction()
        horizontal = direction in {"top_to_bottom", "bottom_to_top", "down", "up"}
        cross_length = width if horizontal else height
        gap_width = max(1.0, _number(self.config.get("gap_width", 22.0), 22.0))
        initial_gap = GapWallPattern._gap_center(self, 0.0, cross_length)
        bounds_value = self.config.get("gap_bounds")
        lower, upper = 0.0, cross_length
        if isinstance(bounds_value, (list, tuple)) and len(bounds_value) >= 2:
            lower, upper = clamp(_number(bounds_value[0]), 0, cross_length), clamp(_number(bounds_value[1]), 0, cross_length)
            if lower > upper:
                lower, upper = upper, lower
        speed = max(1.0, _number(self.config.get("wall_speed", self.config.get("speed", 75.0)), 75.0))
        thickness = max(2.0, _number(self.config.get("wall_thickness", self.config.get("projectile_size", 10.0)), 10.0))
        lifetime = max(.01, _number(self.config.get("lifetime", (height if horizontal else width) / speed + 0.5)))
        self.sequence.add_hazard(MovingGapWallHazard(
            self.sequence.base_bounds, direction, speed, thickness, gap_width, initial_gap,
            _number(self.config.get("gap_movement_speed", self.config.get("gap_speed", 18.0)), 18.0),
            str(self.config.get("movement_style", self.config.get("gap_movement", "oscillate"))).lower(),
            (lower, upper), int(_number(self.config.get("damage", self.projectile_spec().get("damage", 3)), 3)),
            lifetime, color=_color(self.config.get("color")), hit_once=bool(self.config.get("hit_once", False)),
        ))


@register_defense_pattern("sweeping_beam", "beam")
class SweepingBeamPattern(BaseDefensePattern):
    default_duration = 1.5

    def start(self) -> None:
        super().start()
        left, top, width, height = self.sequence.base_bounds
        telegraph = self.config.get("telegraph", {})
        if isinstance(telegraph, Mapping):
            warning = _positive(telegraph.get("duration", 0.0), 0.0)
            telegraph_color = _color(telegraph.get("color"), (255, 205, 90))
            telegraph_alpha = int(clamp(_number(telegraph.get("alpha", 105), 105), 0, 255))
            flash_rate = max(0.0, _number(telegraph.get("flash_rate", 0.0), 0.0))
        else:
            warning, telegraph_color, telegraph_alpha, flash_rate = _positive(self.config.get("warning_duration", 0.0), 0.0), (255, 205, 90), 105, 0.0
        active_duration = max(0.01, _number(self.config.get("active_duration", self.config.get("sweep_duration", self.duration)), self.duration))
        direction = str(self.config.get("direction", "rotational")).lower()
        length = max(width, height) * 1.6 if "length" not in self.config else _number(self.config["length"])
        beam_width = max(1.0, _number(self.config.get("width", 10.0), 10.0))
        origin = self.position(self.config.get("origin"), (left + width / 2.0, top + height / 2.0))
        kwargs: dict[str, Any] = {}
        if direction in {"left_to_right", "right_to_left", "top_to_bottom", "bottom_to_top", "linear"}:
            if direction in {"left_to_right", "right_to_left"}:
                first_x, last_x = (left, left + width) if direction != "right_to_left" else (left + width, left)
                origin, end, line_angle, length = (first_x, top + height / 2.0), (last_x, top + height / 2.0), 90.0, height * 1.25
            else:
                first_y, last_y = (top, top + height) if direction != "bottom_to_top" else (top + height, top)
                origin, end, line_angle, length = (left + width / 2.0, first_y), (left + width / 2.0, last_y), 0.0, width * 1.25
            kwargs = {"linear_end": end, "line_angle": line_angle}
        self.sequence.add_hazard(BeamHazard(
            origin=origin, length=length, width=beam_width, damage=int(_number(self.config.get("damage", 6), 6)),
            active_duration=active_duration, telegraph_duration=warning,
            start_angle=_number(self.config.get("starting_angle", self.config.get("start_angle", self.config.get("angle", -90.0))), -90.0),
            end_angle=_number(self.config.get("ending_angle", self.config.get("end_angle"))) if ("ending_angle" in self.config or "end_angle" in self.config) else None,
            sweep_duration=_number(self.config.get("sweep_duration", active_duration), active_duration),
            color=_color(self.config.get("color")), telegraph_color=telegraph_color,
            telegraph_alpha=telegraph_alpha, flash_rate=flash_rate, **kwargs,
        ))


class ZonePattern(PeriodicDefensePattern):
    """Common builder for telegraphed region patterns."""

    interval_keys = ("placement_interval", "interval")

    def _telegraph(self) -> tuple[float, tuple[int, int, int], int, float]:
        raw = self.config.get("telegraph", {})
        if isinstance(raw, Mapping):
            return (
                _positive(raw.get("duration", self.config.get("warning_duration", 0.0)), 0.0),
                _color(raw.get("color"), (255, 205, 90)),
                int(clamp(_number(raw.get("alpha", 85), 85), 0, 255)),
                max(0.0, _number(raw.get("flash_rate", 0.0), 0.0)),
            )
        return _positive(self.config.get("warning_duration", 0.0), 0.0), (255, 205, 90), 85, 0.0

    def _zone(self, *, shape: str, position: tuple[float, float], width: float = 0.0, height: float = 0.0,
              radius: float = 0.0, angle: float = 0.0, length: float = 0.0,
              points: list[tuple[float, float]] | None = None, vx: float = 0.0, vy: float = 0.0) -> ZoneHazard:
        warning, telegraph_color, telegraph_alpha, flash_rate = self._telegraph()
        return ZoneHazard(
            shape, position[0], position[1], int(_number(self.config.get("damage", 4), 4)),
            max(0.01, _number(self.config.get("active_duration", self.config.get("attack_duration", 0.30)), .30)), warning,
            width, height, radius, angle, length, points or [], _color(self.config.get("color")), telegraph_color, vx, vy,
            bool(self.config.get("hit_once", False)), telegraph_alpha=telegraph_alpha, flash_rate=flash_rate,
        )


@register_defense_pattern("telegraph_strike", "telegraphed_strike")
class TelegraphStrikePattern(ZonePattern):
    default_duration = 0.0

    def __init__(self, sequence: "DefenseSequence", config: Mapping[str, Any]):
        super().__init__(sequence, config)
        self.duration = max(0.0, _number(config.get("duration", 0.0)))

    def fire(self, at: float) -> None:
        del at
        shape = str(self.config.get("shape", "circle")).lower()
        count = max(1, int(_number(self.config.get("region_count", self.config.get("count", 1)), 1)))
        left, top, width, height = self.sequence.base_bounds
        for _ in range(count):
            if self.config.get("randomized_positions", self.config.get("random_position", False)) or self.config.get("position") == "random":
                position = (left + self.sequence.rng.uniform(0, width), top + self.sequence.rng.uniform(0, height))
            else:
                position = self.position(self.config.get("position", self.config.get("origin")))
            if shape in {"vertical_strip", "vertical"}:
                self.sequence.add_hazard(self._zone(shape="rectangle", position=(position[0], top + height / 2), width=_number(self.config.get("width", 16), 16), height=height))
            elif shape in {"horizontal_strip", "horizontal"}:
                self.sequence.add_hazard(self._zone(shape="rectangle", position=(left + width / 2, position[1]), width=width, height=_number(self.config.get("height", 16), 16)))
            elif shape in {"rectangle", "rect"}:
                self.sequence.add_hazard(self._zone(shape="rectangle", position=position, width=_number(self.config.get("width", 24), 24), height=_number(self.config.get("height", 24), 24)))
            elif shape == "line":
                self.sequence.add_hazard(self._zone(shape="line", position=position, width=_number(self.config.get("width", 7), 7), length=_number(self.config.get("length", max(width, height)), max(width, height)), angle=_number(self.config.get("angle", 0), 0)))
            else:
                self.sequence.add_hazard(self._zone(shape="circle", position=position, radius=_number(self.config.get("radius", 16), 16)))


@register_defense_pattern("lane_attack")
class LaneAttackPattern(ZonePattern):
    interval_keys = ("lane_interval", "interval")

    def __init__(self, sequence: "DefenseSequence", config: Mapping[str, Any]):
        super().__init__(sequence, config)
        raw_sequence = config.get("sequence", [])
        self.lane_sequence = raw_sequence if isinstance(raw_sequence, list) else []
        self._timed_lanes = [dict(item) for item in self.lane_sequence if isinstance(item, Mapping) and "time" in item]
        self._timed_lanes.sort(key=lambda item: _number(item.get("time", 0.0)))
        self._lane_index = 0

    def start(self) -> None:
        if not self._timed_lanes:
            super().start()
            return
        BaseDefensePattern.start(self)
        self._fire_timed_lanes(0.0)

    def next_event_delta(self) -> float | None:
        if not self._timed_lanes:
            return super().next_event_delta()
        if self._lane_index >= len(self._timed_lanes):
            return None
        return max(0.0, _number(self._timed_lanes[self._lane_index].get("time", 0.0)) - self.elapsed)

    def update(self, dt: float) -> None:
        if not self._timed_lanes:
            super().update(dt)
            return
        self.elapsed = min(self.duration, self.elapsed + max(0.0, dt))
        self._fire_timed_lanes(self.elapsed)
        if self.elapsed >= self.duration - EPSILON:
            self.finished = True

    def _fire_timed_lanes(self, now: float) -> None:
        while self._lane_index < len(self._timed_lanes) and _number(self._timed_lanes[self._lane_index].get("time", 0.0)) <= now + EPSILON:
            beat = self._timed_lanes[self._lane_index]
            self._lane_index += 1
            original = self.config
            self.config = _deep_merge(self.config, beat)
            try:
                self._emit_lanes(self.config.get("lanes", self.config.get("active_lanes", [])))
            finally:
                self.config = original

    def fire(self, at: float) -> None:
        del at
        selected: Any
        if self.lane_sequence:
            selected = self.lane_sequence[self._fire_count % len(self.lane_sequence)]
            if isinstance(selected, Mapping):
                selected = selected.get("lanes", selected.get("active_lanes", []))
        else:
            selected = self.config.get("active_lanes")
        self._emit_lanes(selected)

    def _emit_lanes(self, selected: Any) -> None:
        left, top, width, height = self.sequence.base_bounds
        lane_setting = self.config.get("lane_count", self.config.get("lanes", 4))
        if isinstance(lane_setting, (list, tuple, Mapping)):
            lane_setting = 4
        lanes = max(1, int(_number(lane_setting, 4)))
        direction = str(self.config.get("direction", "vertical")).lower()
        if selected is None:
            safe = self.config.get("safe_lanes", [])
            if not isinstance(safe, list):
                safe = [safe]
            selected = [index for index in range(lanes) if index not in safe]
        selected = self.value(selected)
        if selected == "random" or self.config.get("randomization"):
            active_count = max(1, int(_number(self.config.get("active_lane_count", lanes - 1), lanes - 1)))
            selected = self.sequence.rng.sample(list(range(lanes)), min(lanes, active_count))
        if not isinstance(selected, list):
            selected = [selected]
        for lane in selected:
            index = int(_number(lane, -1))
            if not 0 <= index < lanes:
                continue
            if direction in {"horizontal", "rows"}:
                lane_height = height / lanes
                position = (left + width / 2, top + lane_height * (index + .5))
                zone = self._zone(shape="rectangle", position=position, width=width, height=lane_height)
            else:
                lane_width = width / lanes
                position = (left + lane_width * (index + .5), top + height / 2)
                zone = self._zone(shape="rectangle", position=position, width=lane_width, height=height)
            self.sequence.add_hazard(zone)


@register_defense_pattern("mine", "persistent_mine", "delayed_hazard")
class MinePattern(ZonePattern):
    interval_keys = ("placement_interval", "spawn_interval", "interval")

    def fire(self, at: float) -> None:
        del at
        placement = str(self.config.get("placement", "player")).lower()
        if placement in {"player", "target", "player_position"}:
            position = (self.sequence.player_x, self.sequence.player_y)
        elif placement == "random":
            left, top, width, height = self.sequence.base_bounds
            position = (left + self.sequence.rng.uniform(0, width), top + self.sequence.rng.uniform(0, height))
        else:
            position = self.position(self.config.get("position", self.config.get("origin")))
        warning, telegraph_color, telegraph_alpha, flash_rate = self._telegraph()
        persistence = max(.01, _number(self.config.get("persistence_duration", self.config.get("active_duration", 1.5)), 1.5))
        self.sequence.add_hazard(ZoneHazard(
            "circle", position[0], position[1], int(_number(self.config.get("damage", 5), 5)), persistence,
            warning, radius=max(1.0, _number(self.config.get("activation_radius", self.config.get("radius", 13)), 13)),
            color=_color(self.config.get("color"), (255, 135, 75)), telegraph_color=telegraph_color,
            hit_once=bool(self.config.get("hit_once", False)), telegraph_alpha=telegraph_alpha, flash_rate=flash_rate,
        ))


@register_defense_pattern("chaser", "homing_chaser")
class ChaserPattern(PeriodicDefensePattern):
    interval_keys = ("spawn_interval", "placement_interval", "interval")
    default_duration = 0.0

    def __init__(self, sequence: "DefenseSequence", config: Mapping[str, Any]):
        super().__init__(sequence, config)
        self.duration = max(0.0, _number(config.get("duration", 0.0)))
        self._remaining = max(1, int(_number(config.get("number_of_chasers", config.get("count", 1)), 1)))

    def fire(self, at: float) -> None:
        del at
        if self._remaining <= 0:
            return
        self._remaining -= 1
        spawn_position = str(self.config.get("spawn_position", "")).lower()
        if "origin" in self.config:
            origin = self.position(self.config.get("origin"))
        elif spawn_position in {"edge_random", "random_edge"}:
            origin = self.sequence.edge_position(self.sequence.rng.choice(["top", "bottom", "left", "right"]), random_axis=True)
        else:
            origin = self.sequence.edge_position(str(self.config.get("spawn_edge", "top")), random_axis=True)
        spec = self.projectile_spec()
        spec.setdefault("speed", self.config.get("movement_speed", self.config.get("speed", 55)))
        spec.setdefault("lifetime", self.config.get("lifetime", 3.0))
        spec.setdefault("radius", self.config.get("radius", 5))
        spec.setdefault("damage", self.config.get("damage", 3))
        spec.setdefault("color", self.config.get("color", "green"))
        motion = dict(spec.get("motion", {})) if isinstance(spec.get("motion"), Mapping) else {}
        motion.setdefault("homing_turn_rate", self.config.get("turning_rate", self.config.get("turn_rate", 95)))
        motion.setdefault("homing_duration", self.config.get("homing_duration"))
        motion.setdefault("acceleration", self.config.get("acceleration", 0.0))
        spec["motion"] = motion
        angle = _vector_angle(self.sequence.player_x - origin[0], self.sequence.player_y - origin[1])
        self.sequence.spawn_projectile(spec, origin, angle=angle)


@register_defense_pattern("expanding_ring")
class ExpandingRingPattern(BaseDefensePattern):
    default_duration = 0.0

    def __init__(self, sequence: "DefenseSequence", config: Mapping[str, Any]):
        super().__init__(sequence, config)
        self.duration = max(0.0, _number(config.get("duration", 0.0)))

    def start(self) -> None:
        super().start()
        self._spawn(1.0)

    def _spawn(self, sign: float) -> None:
        telegraph = self.config.get("telegraph", {})
        warning = _positive(telegraph.get("duration", 0.0), 0.0) if isinstance(telegraph, Mapping) else _positive(self.config.get("warning_duration", 0.0), 0.0)
        telegraph_alpha = int(clamp(_number(telegraph.get("alpha", 90), 90), 0, 255)) if isinstance(telegraph, Mapping) else 90
        flash_rate = max(0.0, _number(telegraph.get("flash_rate", 0.0), 0.0)) if isinstance(telegraph, Mapping) else 0.0
        gaps = self.config.get("gaps", [])
        normalized_gaps: list[tuple[float, float]] = []
        if isinstance(gaps, list):
            for value in gaps:
                if isinstance(value, (list, tuple)) and len(value) >= 2:
                    normalized_gaps.append((_number(value[0]), _number(value[1])))
                elif isinstance(value, Mapping):
                    normalized_gaps.append((_number(value.get("start", value.get("start_angle"))), _number(value.get("end", value.get("end_angle")))))
        gap_count = max(0, int(_number(self.config.get("gap_count", 0), 0)))
        if gap_count and not normalized_gaps:
            gap_angle = self.value(self.config.get("gap_angle", 0.0))
            gap_width = max(1.0, _number(self.config.get("gap_width_degrees", self.config.get("gap_width", 28.0)), 28.0))
            for index in range(gap_count):
                center = _number(gap_angle) + index * 360.0 / gap_count
                normalized_gaps.append((center - gap_width / 2.0, center + gap_width / 2.0))
        radius = max(0.0, _number(self.config.get("starting_radius", self.config.get("radius", 0.0))))
        speed = abs(_number(self.config.get("expansion_speed", self.config.get("contraction_speed", self.config.get("speed", 55.0))), 55.0)) * sign
        duration = max(.01, _number(self.config.get("active_duration", self.config.get("ring_duration", 2.0)), 2.0))
        self.sequence.add_hazard(RingHazard(
            self.position(self.config.get("center", self.config.get("origin"))), radius, speed,
            max(1.0, _number(self.config.get("thickness", 7.0), 7.0)), int(_number(self.config.get("damage", 4), 4)),
            duration, warning, normalized_gaps, _color(self.config.get("color")),
            _color(telegraph.get("color"), (255, 205, 90)) if isinstance(telegraph, Mapping) else (255, 205, 90),
            hit_once=bool(self.config.get("hit_once", False)), telegraph_alpha=telegraph_alpha, flash_rate=flash_rate,
        ))


@register_defense_pattern("contracting_ring", "encircling_ring")
class ContractingRingPattern(ExpandingRingPattern):
    def start(self) -> None:
        BaseDefensePattern.start(self)
        if "starting_radius" not in self.config and "radius" not in self.config:
            _, _, width, height = self.sequence.base_bounds
            self.config["starting_radius"] = max(width, height) * .72
        self._spawn(-1.0)


@register_defense_pattern("bouncing_projectiles", "bouncing_bullets")
class BouncingProjectilesPattern(ProjectileEmitterPattern):
    default_duration = 0.0

    def __init__(self, sequence: "DefenseSequence", config: Mapping[str, Any]):
        super().__init__(sequence, config)
        self.duration = max(0.0, _number(config.get("duration", 0.0)))

    def fire(self, at: float) -> None:
        origin = self._origin()
        spec = self.projectile_spec()
        spec.setdefault("bounce_count", self.config.get("bounce_count", 3))
        spec.setdefault("lifetime", self.config.get("lifetime", 4.0))
        start = _number(self.value(self.config.get("initial_angle", self.config.get("angle", at * 37.0))))
        self._spawn_angles(origin, start, spec)


@register_defense_pattern("curving_projectiles", "curving_bullets")
class CurvingProjectilesPattern(ProjectileEmitterPattern):
    default_duration = 0.0

    def __init__(self, sequence: "DefenseSequence", config: Mapping[str, Any]):
        super().__init__(sequence, config)
        self.duration = max(0.0, _number(config.get("duration", 0.0)))

    def fire(self, at: float) -> None:
        origin = self._origin()
        spec = self.projectile_spec()
        motion = dict(spec.get("motion", {})) if isinstance(spec.get("motion"), Mapping) else {}
        motion.setdefault("angular_velocity", self.value(self.config.get("angular_velocity", 80.0)))
        motion.setdefault("angular_acceleration", self.value(self.config.get("angular_acceleration", 0.0)))
        spec["motion"] = motion
        spec.setdefault("lifetime", self.config.get("lifetime", 3.0))
        self._spawn_angles(origin, _number(self.value(self.config.get("initial_angle", self.config.get("angle", 0.0)))), spec)


@register_defense_pattern("accelerating_stream", "decelerating_stream", "accelerating_projectiles")
class AcceleratingStreamPattern(ProjectileEmitterPattern):
    def fire(self, at: float) -> None:
        del at
        origin = self._origin()
        spec = self.projectile_spec()
        motion = dict(spec.get("motion", {})) if isinstance(spec.get("motion"), Mapping) else {}
        acceleration = self.config.get("acceleration", self.config.get("speed_acceleration", 20.0))
        if self.config.get("type") == "decelerating_stream" and _number(acceleration) > 0:
            acceleration = -_number(acceleration)
        motion.setdefault("acceleration", acceleration)
        motion.setdefault("max_speed", self.config.get("maximum_speed"))
        motion.setdefault("min_speed", self.config.get("minimum_speed"))
        spec["motion"] = motion
        target = (self.sequence.player_x, self.sequence.player_y)
        self._spawn_angles(origin, _vector_angle(target[0] - origin[0], target[1] - origin[1]), spec)


@register_defense_pattern("wave_stream", "oscillating_projectiles", "wave_projectiles")
class WaveStreamPattern(ProjectileEmitterPattern):
    def fire(self, at: float) -> None:
        del at
        origin = self._origin()
        spec = self.projectile_spec()
        motion = dict(spec.get("motion", {})) if isinstance(spec.get("motion"), Mapping) else {}
        motion.setdefault("wave_amplitude", self.config.get("wave_amplitude", self.config.get("amplitude", 12.0)))
        motion.setdefault("wave_frequency", self.config.get("wave_frequency", self.config.get("frequency", 1.0)))
        motion.setdefault("wave_phase", self._fire_count * _number(self.config.get("phase_offset", .65), .65))
        spec["motion"] = motion
        angle_value = self.config.get("initial_angle", self.config.get("angle", self.config.get("direction", 0.0)))
        angle = _number(angle_value) if not isinstance(angle_value, str) else {"right": 0.0, "down": 90.0, "left": 180.0, "up": -90.0}.get(angle_value.lower(), 0.0)
        self._spawn_angles(origin, angle, spec)


@register_defense_pattern("orbiting_hazards", "orbit")
class OrbitingHazardsPattern(BaseDefensePattern):
    default_duration = 0.0

    def __init__(self, sequence: "DefenseSequence", config: Mapping[str, Any]):
        super().__init__(sequence, config)
        self.duration = max(0.0, _number(config.get("duration", 0.0)))

    def start(self) -> None:
        super().start()
        count = max(1, int(_number(self.config.get("count", self.config.get("projectile_count", 3)), 3)))
        center = self.position(self.config.get("center", self.config.get("origin")))
        radius = max(1.0, _number(self.config.get("orbit_radius", 28.0), 28.0))
        angular_speed = _number(self.config.get("angular_speed", 100.0), 100.0)
        if self.config.get("clockwise") is False or str(self.config.get("direction", "clockwise")).lower() in {"counterclockwise", "ccw", "reverse"}:
            angular_speed = -abs(angular_speed)
        lifetime = max(.01, _number(self.config.get("orbit_duration", self.config.get("lifetime", 3.0)), 3.0))
        for index in range(count):
            self.sequence.add_hazard(OrbitingHazard(
                center, radius, _number(self.config.get("angle_offset", 0.0)) + index * 360.0 / count,
                angular_speed, max(1.0, _number(self.config.get("projectile_radius", self.config.get("hazard_radius", self.config.get("radius", 5.0))), 5.0)),
                int(_number(self.config.get("damage", 3), 3)), lifetime,
                bool(self.config.get("follow_player", False)), _color(self.config.get("color"), (190, 115, 255)),
                hit_once=bool(self.config.get("hit_once", False)),
            ))


@register_defense_pattern("shrinking_arena", "moving_arena", "arena_constraint")
class ShrinkingArenaPattern(BaseDefensePattern):
    default_duration = 0.0

    def __init__(self, sequence: "DefenseSequence", config: Mapping[str, Any]):
        super().__init__(sequence, config)
        self.duration = max(0.0, _number(config.get("duration", 0.0)))

    def start(self) -> None:
        super().start()
        left, top, width, height = self.sequence.base_bounds
        start_raw = self.config.get("starting_bounds", self.config.get("start_bounds", {}))
        if not isinstance(start_raw, Mapping):
            start_raw = {}
        start_width = max(2 * self.sequence.player_radius + 1, _number(start_raw.get("width", width), width))
        start_height = max(2 * self.sequence.player_radius + 1, _number(start_raw.get("height", height), height))
        start_x = _number(start_raw.get("x", 0.0), 0.0)
        start_y = _number(start_raw.get("y", 0.0), 0.0)
        if bool(start_raw.get("normalized", False)):
            start_x, start_y = left + start_x * width, top + start_y * height
            start_width *= width if "width" in start_raw else 1.0
            start_height *= height if "height" in start_raw else 1.0
        elif not bool(start_raw.get("absolute", False)):
            start_x, start_y = left + start_x, top + start_y
        base = (start_x, start_y, start_width, start_height)
        target_raw = self.config.get("ending_bounds", self.config.get("end_bounds", self.config.get("target_bounds", {})))
        if not isinstance(target_raw, Mapping):
            target_raw = {}
        target_width = max(2 * self.sequence.player_radius + 1, _number(target_raw.get("width", self.config.get("ending_width", width * .65)), width * .65))
        target_height = max(2 * self.sequence.player_radius + 1, _number(target_raw.get("height", self.config.get("ending_height", height * .65)), height * .65))
        target_x = _number(target_raw.get("x", (width - target_width) / 2), (width - target_width) / 2)
        target_y = _number(target_raw.get("y", (height - target_height) / 2), (height - target_height) / 2)
        if bool(target_raw.get("normalized", False)):
            target_x, target_y = left + target_x * width, top + target_y * height
            target_width *= width if "width" in target_raw else 1.0
            target_height *= height if "height" in target_raw else 1.0
        elif not bool(target_raw.get("absolute", False)):
            target_x, target_y = left + target_x, top + target_y
        # moving_arena supports an offset/movement target while retaining the
        # same temporary-constraint lifecycle.
        offset = self.config.get("offset")
        if isinstance(offset, (list, tuple)) and len(offset) >= 2:
            target_x += _number(offset[0])
            target_y += _number(offset[1])
        shrink = max(0.0, _number(self.config.get("shrink_duration", self.config.get("move_duration", 1.0)), 1.0))
        hold = max(0.0, _number(self.config.get("hold_duration", 1.0), 1.0))
        restore = max(0.0, _number(self.config.get("restoration_duration", self.config.get("restore_duration", 1.0)), 1.0))
        self.sequence.add_hazard(ArenaConstraint(base, (target_x, target_y, target_width, target_height), shrink, hold, restore))


@register_defense_pattern("maze_corridor", "corridor", "maze")
class MazeCorridorPattern(ZonePattern):
    default_duration = 0.0

    def __init__(self, sequence: "DefenseSequence", config: Mapping[str, Any]):
        super().__init__(sequence, config)
        self.duration = max(0.0, _number(config.get("duration", 0.0)))
        raw_segments = config.get("segments", [])
        self.segments = [dict(segment) for segment in raw_segments if isinstance(segment, Mapping)] if isinstance(raw_segments, list) else []
        self.segments.sort(key=lambda segment: _number(segment.get("time", 0.0)))
        self._segment_index = 0

    def start(self) -> None:
        BaseDefensePattern.start(self)
        if self.segments:
            self._spawn_due_segments(0.0)
            return
        left, top, width, height = self.sequence.base_bounds
        layout = self.config.get("layout", [])
        if isinstance(layout, str):
            layout = [line for line in layout.strip("\n").splitlines() if line]
        if not isinstance(layout, list) or not layout:
            # A concise fallback: two wall segments leave the authored gap.
            gap = clamp(_number(self.config.get("gap_position", width / 2), width / 2), 0, width)
            gap_width = max(1.0, _number(self.config.get("gap_width", 30.0), 30.0))
            layout = ["#"]
            cell_w, cell_h = width, max(2.0, _number(self.config.get("wall_thickness", 12), 12))
            ranges = [(0.0, max(0.0, gap - gap_width / 2)), (min(width, gap + gap_width / 2), width)]
            for start, end in ranges:
                if end > start:
                    self.sequence.add_hazard(self._zone(shape="rectangle", position=(left + (start + end) / 2, top + height / 2), width=end - start, height=cell_h))
            return
        rows = [str(row) for row in layout]
        columns = max(len(row) for row in rows)
        cell_w, cell_h = width / columns, height / len(rows)
        move_speed = _number(self.config.get("movement_speed", self.config.get("speed", 0.0)), 0.0)
        direction = str(self.config.get("direction", "static")).lower()
        vx, vy = ({"left_to_right": (move_speed, 0), "right_to_left": (-move_speed, 0), "top_to_bottom": (0, move_speed), "bottom_to_top": (0, -move_speed)}.get(direction, (0.0, 0.0)))
        for row_index, row in enumerate(rows):
            for column_index, character in enumerate(row):
                if character not in {"#", "X", "1"}:
                    continue
                position = (left + (column_index + .5) * cell_w, top + (row_index + .5) * cell_h)
                self.sequence.add_hazard(self._zone(shape="rectangle", position=position, width=cell_w + 1, height=cell_h + 1, vx=vx, vy=vy))

    def next_event_delta(self) -> float | None:
        if not self.segments or self._segment_index >= len(self.segments):
            return None
        return max(0.0, _number(self.segments[self._segment_index].get("time", 0.0)) - self.elapsed)

    def _spawn_due_segments(self, now: float) -> None:
        while self._segment_index < len(self.segments) and _number(self.segments[self._segment_index].get("time", 0.0)) <= now + EPSILON:
            segment = self.segments[self._segment_index]
            self._segment_index += 1
            wall_config = _deep_merge(self.config, segment)
            wall_config.pop("segments", None)
            wall_config["duration"] = 0.0
            wall = GapWallPattern(self.sequence, wall_config)
            wall.start()

    def update(self, dt: float) -> None:
        if not self.segments:
            BaseDefensePattern.update(self, dt)
            return
        self.elapsed = min(self.duration, self.elapsed + max(0.0, dt))
        self._spawn_due_segments(self.elapsed)
        if self.elapsed >= self.duration - EPSILON:
            self.finished = True


@register_defense_pattern("rhythm", "sequence", "rhythm_sequence")
class RhythmPattern(ZonePattern):
    default_duration = 0.0

    def __init__(self, sequence: "DefenseSequence", config: Mapping[str, Any]):
        super().__init__(sequence, config)
        beats = config.get("beats", [])
        if not isinstance(beats, list):
            raise DefenseConfigError("rhythm.beats must be a list")
        self.beats = sorted((dict(beat) for beat in beats if isinstance(beat, Mapping)), key=lambda beat: _number(beat.get("time", 0.0)))
        inferred = max((_number(beat.get("time", 0.0)) for beat in self.beats), default=0.0)
        self.duration = max(0.0, _number(config.get("duration", inferred), inferred))
        self._beat_index = 0

    def start(self) -> None:
        BaseDefensePattern.start(self)
        self._fire_due(0.0)

    def next_event_delta(self) -> float | None:
        if self._beat_index >= len(self.beats):
            return None
        return max(0.0, _number(self.beats[self._beat_index].get("time", 0.0)) - self.elapsed)

    def _fire_due(self, now: float) -> None:
        while self._beat_index < len(self.beats) and _number(self.beats[self._beat_index].get("time", 0.0)) <= now + EPSILON:
            beat = self.beats[self._beat_index]
            self._beat_index += 1
            # Reuse lane geometry while letting an individual beat override
            # which lanes, warning, active duration, or damage are used.
            merged = _deep_merge(self.config, beat)
            merged.pop("beats", None)
            temporary = LaneAttackPattern(self.sequence, merged)
            temporary._fire_count = self._beat_index - 1
            temporary.fire(_number(beat.get("time", 0.0)))

    def update(self, dt: float) -> None:
        self.elapsed = min(self.duration, self.elapsed + max(0.0, dt))
        self._fire_due(self.elapsed)
        if self.elapsed >= self.duration - EPSILON:
            self.finished = True


class DefenseSequence:
    """Concurrent scheduler for a single enemy dodge/defense turn.

    It is intentionally the only object that can move the player or invoke
    the controller's player-damage callback.  Patterns own neither a game
    loop nor HP; they merely add hazards through this sequence.
    """

    player_radius = 4.0
    hurt_animation_duration = 0.30

    def __init__(self, pattern: Mapping[str, Any], default_arena: Mapping[str, Any],
                 rng: random.Random | None = None, difficulty: str | int | None = None):
        if not isinstance(pattern, Mapping):
            raise DefenseConfigError("defense sequence must be a mapping")
        selected_difficulty = difficulty if difficulty is not None else pattern.get("difficulty_level")
        self.pattern = apply_difficulty_overrides(pattern, selected_difficulty)
        self.arena = dict(default_arena)
        sequence_arena = self.pattern.get("arena", {})
        if isinstance(sequence_arena, Mapping):
            self.arena.update(sequence_arena)
        self.x, self.y, self.width, self.height = _bounds_from_arena(self.arena)
        self.base_bounds = (self.x, self.y, self.width, self.height)
        self.player_speed = max(0.0, _number(self.arena.get("player_speed", 120), 120))
        self.player_x, self.player_y = self.x + self.width / 2.0, self.y + self.height / 2.0
        self.player_velocity_x = 0.0
        self.player_velocity_y = 0.0
        player = self.pattern.get("player", {})
        if not isinstance(player, Mapping):
            player = {}
        self.invulnerability_time = max(0.0, _number(player.get("invulnerability_time", self.pattern.get("invulnerability_time", self.pattern.get("hit_invulnerability", .55))), .55))
        self.player_invulnerable_for = 0.0
        self.player_hurt_for = 0.0
        self.elapsed = 0.0
        # A sequence-local seed intentionally overrides the battle RNG for
        # reproducible authoring/debugging.  Otherwise all selection remains
        # tied to the controller's seeded battle RNG.
        self.rng = random.Random(self.pattern["seed"]) if "seed" in self.pattern else (rng or random.Random())
        self.projectiles: list[Projectile] = []
        self.hazards: list[Hazard] = []
        self.active_patterns: list[BaseDefensePattern] = []
        self._pattern_events = compile_pattern_events(self.pattern, selected_difficulty)
        self._pattern_index = 0
        self._events = self._expand_timeline(self.pattern.get("timeline", []))
        self._event_index = 0
        inferred_duration = max(
            [event.start + _duration_for_entry(event.config) for event in self._pattern_events]
            + [event.at for event in self._events] + [0.0]
        )
        duration_value = self.pattern.get("duration", inferred_duration)
        self.duration = max(0.0, _number(duration_value, inferred_duration))

    @property
    def effective_bounds(self) -> tuple[float, float, float, float]:
        left, top, width, height = self.base_bounds
        right, bottom = left + width, top + height
        for hazard in self.hazards:
            if isinstance(hazard, ArenaConstraint) and not hazard.expired:
                inner_left, inner_top, inner_width, inner_height = hazard.bounds()
                left, top = max(left, inner_left), max(top, inner_top)
                right, bottom = min(right, inner_left + inner_width), min(bottom, inner_top + inner_height)
        return left, top, max(1.0, right - left), max(1.0, bottom - top)

    @property
    def renderables(self) -> list[dict[str, Any]]:
        return [hazard.render_data() for hazard in self.hazards if not hazard.expired]

    def resolve_position(self, value: Any, default: tuple[float, float] | None = None) -> tuple[float, float]:
        return resolve_position(value, self.arena, default)

    def edge_position(self, side: str, *, random_axis: bool = False, offset: float = 2.0) -> tuple[float, float]:
        left, top, width, height = self.base_bounds
        side = side.lower()
        if side == "top":
            return left + (self.rng.uniform(0, width) if random_axis else width / 2), top - offset
        if side == "bottom":
            return left + (self.rng.uniform(0, width) if random_axis else width / 2), top + height + offset
        if side == "left":
            return left - offset, top + (self.rng.uniform(0, height) if random_axis else height / 2)
        if side == "right":
            return left + width + offset, top + (self.rng.uniform(0, height) if random_axis else height / 2)
        raise DefenseConfigError(f"unknown arena edge {side!r}")

    def add_hazard(self, hazard: Hazard) -> None:
        self.hazards.append(hazard)
        if isinstance(hazard, Projectile):
            self.projectiles.append(hazard)

    def spawn_projectile(self, spec: Mapping[str, Any], position: tuple[float, float] | None = None,
                         *, angle: float | None = None, target: tuple[float, float] | None = None,
                         legacy_absolute: bool = False) -> Projectile:
        """Create a projectile once, resolving random YAML fields at spawn."""
        resolved = resolve_random_value(deepcopy(dict(spec)), self.rng)
        if position is None:
            position = self._spawn_position(resolved.get("spawn", resolved.get("origin", resolved.get("position", None))), legacy_absolute=legacy_absolute)
        velocity = resolved.get("velocity", {})
        if not isinstance(velocity, Mapping):
            velocity = {}
        vx, vy = _number(velocity.get("x", 0.0)), _number(velocity.get("y", 0.0))
        behavior = str(resolved.get("behavior", "straight"))
        raw_motion = resolved.get("motion", {})
        if not isinstance(raw_motion, Mapping):
            raw_motion = {}
        speed = max(0.0, _number(resolved.get("speed", resolved.get("initial_speed", raw_motion.get("initial_speed", resolved.get("forward_speed", raw_motion.get("forward_speed", math.hypot(vx, vy) or 90.0))))), 90.0))
        if angle is not None:
            direction_x, direction_y = _angle_vector(angle)
            vx, vy = direction_x * speed, direction_y * speed
        elif target is not None or behavior in {"toward_player", "toward_target", "aimed", "homing"}:
            target_x, target_y = target or (self.player_x, self.player_y)
            direction_x, direction_y = _normal(target_x - position[0], target_y - position[1])
            vx, vy = direction_x * speed, direction_y * speed
        elif "direction" in resolved:
            direction = resolved["direction"]
            if isinstance(direction, str):
                direction_x, direction_y = {
                    "right": (1.0, 0.0), "down": (0.0, 1.0), "left": (-1.0, 0.0), "up": (0.0, -1.0),
                }.get(direction.lower(), _angle_vector(_number(direction)))
            elif isinstance(direction, Mapping):
                direction_x, direction_y = _normal(_number(direction.get("x", 0.0)), _number(direction.get("y", 0.0)))
            elif isinstance(direction, (list, tuple)) and len(direction) >= 2:
                direction_x, direction_y = _normal(_number(direction[0]), _number(direction[1]))
            else:
                direction_x, direction_y = _angle_vector(_number(direction))
            vx, vy = direction_x * speed, direction_y * speed
        elif "angle" in resolved:
            direction_x, direction_y = _angle_vector(_number(resolved["angle"]))
            vx, vy = direction_x * speed, direction_y * speed
        size = resolved.get("size", resolved.get("radius", 4.0) * 2)
        if isinstance(size, (list, tuple)) and len(size) >= 2:
            projectile_width, projectile_height = max(1.0, _number(size[0], 8)), max(1.0, _number(size[1], 8))
        else:
            projectile_width = projectile_height = max(1.0, _number(size, 8))
        motion = raw_motion
        acceleration = motion.get("acceleration", resolved.get("acceleration", {}))
        if isinstance(acceleration, Mapping):
            acceleration_x, acceleration_y, scalar_acceleration = _number(acceleration.get("x", 0.0)), _number(acceleration.get("y", 0.0)), _number(acceleration.get("speed", 0.0))
        else:
            acceleration_x, acceleration_y, scalar_acceleration = 0.0, 0.0, _number(acceleration)
        projectile = Projectile(
            x=position[0], y=position[1], width=projectile_width, height=projectile_height,
            damage=max(0, int(_number(resolved.get("damage", 1), 1))),
            lifetime=max(.01, _number(resolved.get("lifetime", max(1.0, self.duration - self.elapsed + 1.0)), 1.0)),
            vx=vx, vy=vy, shape=str(resolved.get("shape", "circle")), behavior=behavior,
            delay=max(0.0, _number(resolved.get("delay", 0.0))),
            acceleration_x=acceleration_x, acceleration_y=acceleration_y,
            sine_amplitude=_number(resolved.get("sine_amplitude", 0.0)), sine_frequency=_number(resolved.get("sine_frequency", 0.0)),
            base_y=position[1], collision_radius=_number(resolved.get("collision_radius", resolved.get("radius", min(projectile_width, projectile_height) / 2.0))),
            sprite=resolved.get("sprite") if isinstance(resolved.get("sprite"), str) else None,
            sprite_scale=max(.01, _number(resolved.get("scale", resolved.get("sprite_scale", 1.0)), 1.0)),
            rotation=_number(resolved.get("rotation", 0.0)), rotation_mode=str(resolved.get("rotation_mode", "none")),
            angular_velocity=_number(motion.get("angular_velocity", resolved.get("angular_velocity", 0.0))),
            angular_acceleration=_number(motion.get("angular_acceleration", resolved.get("angular_acceleration", 0.0))),
            orbital_center=(
                (_number(motion["orbital_center"][0]), _number(motion["orbital_center"][1]))
                if isinstance(motion.get("orbital_center"), (list, tuple)) and len(motion["orbital_center"]) >= 2
                else None
            ),
            orbital_speed=_number(motion.get("orbital_speed", 0.0)),
            orbital_angle=_vector_angle(vx, vy) if math.hypot(vx, vy) > EPSILON else 0.0,
            orbital_radial_speed=math.hypot(vx, vy),
            scalar_acceleration=scalar_acceleration,
            min_speed=_number(motion["min_speed"]) if motion.get("min_speed") is not None else (_number(resolved.get("minimum_speed", resolved.get("min_speed"))) if (resolved.get("minimum_speed") is not None or resolved.get("min_speed") is not None) else None),
            max_speed=_number(motion["max_speed"]) if motion.get("max_speed") is not None else (_number(resolved.get("maximum_speed", resolved.get("max_speed"))) if (resolved.get("maximum_speed") is not None or resolved.get("max_speed") is not None) else None),
            drag=max(0.0, _number(motion.get("drag", resolved.get("drag", 0.0)))),
            homing_turn_rate=max(
                0.0,
                _number(motion.get(
                    "homing_turn_rate",
                    motion.get("turn_rate", motion.get("homing_strength", resolved.get("turning_rate", 0.0))),
                )),
            ),
            homing_duration=_number(motion["homing_duration"]) if motion.get("homing_duration") is not None else None,
            bounce_count=max(0, int(_number(resolved.get("bounce_count", resolved.get("bounces", 0)), 0))),
            restitution=max(0.0, _number(resolved.get("restitution", 1.0), 1.0)),
            destroy_outside_arena=bool(resolved.get("destroy_outside_arena", True)),
            wave_amplitude=_number(motion.get("wave_amplitude", resolved.get("wave_amplitude", 0.0))),
            wave_frequency=_number(motion.get("wave_frequency", resolved.get("wave_frequency", 0.0))),
            wave_phase=_number(motion.get("wave_phase", resolved.get("wave_phase", 0.0))),
            color=_color(resolved.get("color")),
            hit_once=bool(resolved.get("hit_once", not bool(resolved.get("piercing", False)))),
        )
        self.add_hazard(projectile)
        return projectile

    def _spawn_position(self, spawn: Any, *, legacy_absolute: bool = False) -> tuple[float, float]:
        if isinstance(spawn, Mapping):
            if "origin" in spawn:
                return resolve_position(spawn["origin"], self.arena, legacy_absolute=legacy_absolute)
            edge = spawn.get("edge")
            if edge is not None:
                side = str(edge).lower()
                x, y = self.edge_position(side, random_axis=False, offset=0.0)
                left, top, width, height = self.base_bounds
                raw_x, raw_y = spawn.get("x"), spawn.get("y")
                if raw_x == "random":
                    x = self.rng.uniform(left, left + width)
                elif raw_x is not None:
                    x = _number(raw_x) if legacy_absolute else left + _number(raw_x)
                if raw_y == "random":
                    y = self.rng.uniform(top, top + height)
                elif raw_y is not None:
                    y = _number(raw_y) if legacy_absolute else top + _number(raw_y)
                return x, y
            return resolve_position(spawn, self.arena, legacy_absolute=legacy_absolute)
        return resolve_position(spawn, self.arena, legacy_absolute=legacy_absolute)

    def _expand_timeline(self, timeline: Any) -> list[ScheduledEvent]:
        if timeline is None:
            return []
        if not isinstance(timeline, list):
            raise DefenseConfigError("timeline must be a list")
        events: list[ScheduledEvent] = []
        for event in timeline:
            if not isinstance(event, Mapping):
                raise DefenseConfigError("timeline event must be a mapping")
            base_at = max(0.0, _number(event.get("at", 0.0)))
            action = event.get("action")
            if action in {"spawn_repeated", "spawn_sweep", "spawn_rotating"}:
                repeat = event.get("repeat", {})
                if not isinstance(repeat, Mapping):
                    raise DefenseConfigError("timeline repeat must be a mapping")
                count, interval = max(0, int(_number(repeat.get("count", 0)))), max(0.0, _number(repeat.get("interval", 0.0)))
                for index in range(count):
                    repeated = deepcopy(dict(event))
                    if action in {"spawn_repeated", "spawn_sweep"}:
                        repeated["action"] = "spawn"
                    else:
                        repeated["_angle"] = _number(event.get("start_angle", 0.0)) + _number(event.get("angular_speed", 90.0)) * index * interval
                    events.append(ScheduledEvent(base_at + index * interval, repeated))
            else:
                events.append(ScheduledEvent(base_at, dict(event)))
        return sorted(events)

    def _spawn_legacy_event(self, event: Mapping[str, Any], result: DefenseResult) -> None:
        action = event.get("action")
        if action == "dialogue":
            result.dialogue.append(str(event.get("text", "")))
        elif action == "spawn_radial":
            count = max(1, int(_number(event.get("count", 1), 1)))
            start = _number(event.get("start_angle", 0.0))
            projectile = dict(event.get("projectile", {}))
            if "spawn" not in projectile:
                projectile["spawn"] = event.get("spawn", {"origin": [self.x + self.width / 2, self.y + self.height / 2]})
            origin = self._spawn_position(projectile.get("spawn"), legacy_absolute=True)
            for index in range(count):
                self.spawn_projectile(projectile, origin, angle=start + index * 360.0 / count, legacy_absolute=True)
        elif action == "spawn_rotating":
            self.spawn_projectile(dict(event.get("projectile", {})), angle=_number(event.get("_angle", 0.0)), legacy_absolute=True)
        elif action in {"spawn", "spawn_sweep"}:
            self.spawn_projectile(dict(event.get("projectile", {})), legacy_absolute=True)

    def _activate_due(self) -> None:
        while self._pattern_index < len(self._pattern_events) and self._pattern_events[self._pattern_index].start <= self.elapsed + EPSILON:
            config = self._pattern_events[self._pattern_index].config
            self._pattern_index += 1
            pattern = create_defense_pattern(self, config)
            pattern.start()
            if not pattern.is_finished():
                self.active_patterns.append(pattern)

    def _legacy_due(self, result: DefenseResult) -> None:
        while self._event_index < len(self._events) and self._events[self._event_index].at <= self.elapsed + EPSILON:
            event = self._events[self._event_index].action
            self._event_index += 1
            self._spawn_legacy_event(event, result)

    def _next_boundary(self, remaining: float) -> float:
        next_time = self.elapsed + remaining
        if self._pattern_index < len(self._pattern_events):
            next_time = min(next_time, self._pattern_events[self._pattern_index].start)
        if self._event_index < len(self._events):
            next_time = min(next_time, self._events[self._event_index].at)
        for pattern in self.active_patterns:
            boundary = pattern.next_event_delta()
            if boundary is not None:
                next_time = min(next_time, self.elapsed + boundary)
            if not pattern.is_finished():
                next_time = min(next_time, self.elapsed + max(0.0, pattern.duration - pattern.elapsed))
        return min(next_time, self.duration)

    def _move_player(self, dt: float, movement: BattleInput) -> None:
        move_x, move_y = movement.move_x, movement.move_y
        length = math.hypot(move_x, move_y)
        if length > 1.0:
            move_x, move_y = move_x / length, move_y / length
        previous_x, previous_y = self.player_x, self.player_y
        left, top, width, height = self.effective_bounds
        minimum_x, maximum_x = left + self.player_radius, left + width - self.player_radius
        minimum_y, maximum_y = top + self.player_radius, top + height - self.player_radius
        if minimum_x > maximum_x:
            minimum_x = maximum_x = left + width / 2.0
        if minimum_y > maximum_y:
            minimum_y = maximum_y = top + height / 2.0
        self.player_x = clamp(self.player_x + move_x * self.player_speed * dt, minimum_x, maximum_x)
        self.player_y = clamp(self.player_y + move_y * self.player_speed * dt, minimum_y, maximum_y)
        if dt > EPSILON:
            self.player_velocity_x = (self.player_x - previous_x) / dt
            self.player_velocity_y = (self.player_y - previous_y) / dt

    def _update_hazards(self, dt: float, hazards_before: list[Hazard]) -> None:
        bounds = self.effective_bounds
        player_position = (self.player_x, self.player_y)
        for hazard in hazards_before:
            hazard.update(dt, bounds, player_position)

    def _collide(self, result: DefenseResult, damage_player: Callable[[int], None]) -> None:
        self.player_invulnerable_for = max(0.0, self.player_invulnerable_for)
        if self.player_invulnerable_for > EPSILON:
            return
        for hazard in self.hazards:
            if hazard.expired or not hazard.active or hazard.damage <= 0:
                continue
            if hazard.hits(self.player_x, self.player_y, self.player_radius):
                damage = max(0, int(hazard.damage))
                if damage:
                    damage_player(damage)
                    result.hit_damage.append(damage)
                    self.player_invulnerable_for = self.invulnerability_time
                    self.player_hurt_for = self.hurt_animation_duration
                    marker = getattr(hazard, "mark_hit", None)
                    if callable(marker):
                        marker()
                return

    def _cull_hazards(self) -> None:
        self.hazards = [hazard for hazard in self.hazards if not hazard.expired]
        self.projectiles = [projectile for projectile in self.projectiles if not projectile.expired]

    def update(self, dt: float, movement: BattleInput, damage_player: Callable[[int], None]) -> DefenseResult:
        """Advance by elapsed seconds, preserving exact spawn boundaries.

        A projectile scheduled at 1.0 seconds in a 1.5-second update moves
        for only .5 seconds.  This is the important difference from the old
        timeline implementation and keeps patterns stable at varying FPS.
        """
        result = DefenseResult()
        remaining = max(0.0, dt)
        self._activate_due()
        self._legacy_due(result)
        # Process zero-duration sequences too: newly spawned t=0 hazards can
        # still be rendered during the closing transition.
        while remaining > EPSILON and self.elapsed < self.duration - EPSILON:
            next_time = self._next_boundary(remaining)
            slice_dt = max(0.0, next_time - self.elapsed)
            if slice_dt <= EPSILON:
                before_pattern, before_event = self._pattern_index, self._event_index
                self._activate_due()
                self._legacy_due(result)
                if before_pattern == self._pattern_index and before_event == self._event_index:
                    # A pattern internal boundary at the current time should
                    # be consumed by a zero update rather than spin forever.
                    for pattern in self.active_patterns:
                        if pattern.next_event_delta() is not None and pattern.next_event_delta() <= EPSILON:
                            pattern.update(0.0)
                    if all(pattern.next_event_delta() is None or pattern.next_event_delta() > EPSILON for pattern in self.active_patterns):
                        break
                continue
            self.player_invulnerable_for = max(0.0, self.player_invulnerable_for - slice_dt)
            self.player_hurt_for = max(0.0, self.player_hurt_for - slice_dt)
            self._move_player(slice_dt, movement)
            hazards_before = list(self.hazards)
            for active_pattern in list(self.active_patterns):
                active_pattern.update(slice_dt)
            self._update_hazards(slice_dt, hazards_before)
            self.elapsed += slice_dt
            remaining -= slice_dt
            self._collide(result, damage_player)
            self._cull_hazards()
            self.active_patterns = [pattern for pattern in self.active_patterns if not pattern.is_finished()]
            self._activate_due()
            self._legacy_due(result)
        # If a pattern lives exactly until sequence end, resolve all events
        # at that instant but do not advance newly spawned hazards past it.
        if self.elapsed >= self.duration - EPSILON:
            self.elapsed = self.duration
            self._activate_due()
            self._legacy_due(result)
        result.completed = self.elapsed >= self.duration - EPSILON
        return result


def _validate_number(value: Any, path: str, *, non_negative: bool = True) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DefenseConfigError(f"{path} must be a number")
    if non_negative and value < 0:
        raise DefenseConfigError(f"{path} must be non-negative")


def _validate_random_value(value: Any, path: str) -> None:
    if isinstance(value, Mapping) and ("min" in value or "max" in value):
        if set(value) - {"min", "max"}:
            # A regular configuration mapping can legitimately contain min
            # elsewhere; this branch only applies to leaf random values.
            return
        if "min" not in value or "max" not in value:
            raise DefenseConfigError(f"{path} random range needs min and max")
        _validate_number(value["min"], f"{path}.min", non_negative=False)
        _validate_number(value["max"], f"{path}.max", non_negative=False)
        if value["min"] > value["max"]:
            raise DefenseConfigError(f"{path}.min cannot exceed {path}.max")
    if isinstance(value, Mapping) and "choices" in value:
        choices = value["choices"]
        if not isinstance(choices, list) or not choices:
            raise DefenseConfigError(f"{path}.choices must be a non-empty list")


def _validate_position_value(value: Any, path: str) -> None:
    if isinstance(value, str):
        if value.lower() not in {"center", "top", "bottom", "left", "right", "random"}:
            raise DefenseConfigError(f"{path} has an unknown named position {value!r}")
        return
    if isinstance(value, (list, tuple)):
        if len(value) < 2:
            raise DefenseConfigError(f"{path} must contain x and y")
        for index, coordinate in enumerate(value[:2]):
            _validate_random_value(coordinate, f"{path}[{index}]")
            if not isinstance(coordinate, Mapping):
                _validate_number(coordinate, f"{path}[{index}]", non_negative=False)
        return
    if not isinstance(value, Mapping):
        raise DefenseConfigError(f"{path} must be a position mapping, [x, y], or named position")
    if "normalized" in value and not isinstance(value["normalized"], bool):
        raise DefenseConfigError(f"{path}.normalized must be true or false")
    for key in ("x", "y"):
        if key in value:
            _validate_random_value(value[key], f"{path}.{key}")
            if not isinstance(value[key], Mapping):
                _validate_number(value[key], f"{path}.{key}", non_negative=False)


def validate_defense_sequence(sequence: Mapping[str, Any], path: str = "defense_sequence") -> None:
    """Validate common YAML mistakes before a battle is entered.

    This intentionally validates shape/timing/registry names and leaves
    pattern-specific optional tuning permissive.  New pattern types can add
    fields without a second giant validation conditional.
    """
    if not isinstance(sequence, Mapping):
        raise DefenseConfigError(f"{path} must be a mapping")
    if "duration" in sequence:
        _validate_number(sequence["duration"], f"{path}.duration")
    if "seed" in sequence and not isinstance(sequence["seed"], (int, str, bytes)):
        raise DefenseConfigError(f"{path}.seed must be an integer or string")
    if "arena" in sequence:
        arena = sequence["arena"]
        if not isinstance(arena, Mapping):
            raise DefenseConfigError(f"{path}.arena must be a mapping")
        for name in ("width", "height", "player_speed"):
            if name in arena:
                _validate_number(arena[name], f"{path}.arena.{name}")
    difficulty = sequence.get("difficulty")
    if difficulty is not None:
        if not isinstance(difficulty, Mapping):
            raise DefenseConfigError(f"{path}.difficulty must be a mapping")
        for level, override in difficulty.items():
            if not isinstance(override, Mapping):
                raise DefenseConfigError(f"{path}.difficulty.{level} must be a mapping")
    groups = sequence.get("pattern_groups", {})
    if groups is not None and not isinstance(groups, Mapping):
        raise DefenseConfigError(f"{path}.pattern_groups must be a mapping")

    def validate_entries(entries: Any, entry_path: str) -> None:
        if not isinstance(entries, list):
            raise DefenseConfigError(f"{entry_path} must be a list")
        for index, raw in enumerate(entries):
            item_path = f"{entry_path}[{index}]"
            if not isinstance(raw, Mapping):
                raise DefenseConfigError(f"{item_path} must be a mapping")
            has_type, has_group = "type" in raw, "group" in raw
            if has_type == has_group:
                raise DefenseConfigError(f"{item_path} needs exactly one of type or group")
            if has_type:
                value = raw["type"]
                if not isinstance(value, str) or value not in PATTERN_TYPES:
                    raise DefenseConfigError(f"{item_path}.type {value!r} is unsupported")
            if has_group and (not isinstance(raw["group"], str) or raw["group"] not in groups):
                raise DefenseConfigError(f"{item_path}.group {raw.get('group')!r} is unknown")
            for name in ("start", "duration", "fire_interval", "spawn_interval", "interval", "warning_duration", "active_duration"):
                if name in raw:
                    _validate_number(raw[name], f"{item_path}.{name}")
            repeat = raw.get("repeat")
            if repeat is not None:
                if not isinstance(repeat, Mapping):
                    raise DefenseConfigError(f"{item_path}.repeat must be a mapping")
                count = repeat.get("count", 1)
                if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                    raise DefenseConfigError(f"{item_path}.repeat.count must be a non-negative integer")
                if "interval" in repeat:
                    _validate_number(repeat["interval"], f"{item_path}.repeat.interval")
                if count > 1 and _number(repeat.get("interval", 0.0)) <= 0:
                    raise DefenseConfigError(f"{item_path}.repeat.interval must be positive when count is greater than one")
            if "projectile" in raw and not isinstance(raw["projectile"], Mapping):
                raise DefenseConfigError(f"{item_path}.projectile must be a mapping")
            if isinstance(raw.get("projectile"), Mapping):
                projectile = raw["projectile"]
                for name in ("speed", "initial_speed", "forward_speed", "radius", "collision_radius", "damage", "lifetime", "delay"):
                    if name in projectile:
                        _validate_random_value(projectile[name], f"{item_path}.projectile.{name}")
                        if not isinstance(projectile[name], Mapping):
                            _validate_number(projectile[name], f"{item_path}.projectile.{name}")
                if "sprite" in projectile and (not isinstance(projectile["sprite"], str) or not projectile["sprite"]):
                    raise DefenseConfigError(f"{item_path}.projectile.sprite must be a non-empty string")
                if "size" in projectile and not isinstance(projectile["size"], (int, float, list, tuple, Mapping)):
                    raise DefenseConfigError(f"{item_path}.projectile.size must be a number or [width, height]")
            for name in ("origin", "position", "center"):
                if name in raw:
                    _validate_position_value(raw[name], f"{item_path}.{name}")
            pattern_difficulty = raw.get("difficulty")
            if pattern_difficulty is not None and not isinstance(pattern_difficulty, Mapping):
                raise DefenseConfigError(f"{item_path}.difficulty must be a mapping")

    validate_entries(sequence.get("patterns", []), f"{path}.patterns")
    if isinstance(groups, Mapping):
        for name, entries in groups.items():
            if not isinstance(name, str) or not name:
                raise DefenseConfigError(f"{path}.pattern_groups has an invalid name")
            validate_entries(entries, f"{path}.pattern_groups.{name}")
    # Let the compiler detect recursive groups and repeat details early.
    compile_pattern_events(sequence, sequence.get("difficulty_level"))


def normalize_sprite_reference(sprite: str) -> str:
    """Accept both engine-relative and illustrative ``sprites/...`` paths."""
    result = sprite.replace("\\", "/")
    for prefix in ("assets/sprites/", "sprites/"):
        if result.startswith(prefix):
            return result[len(prefix):]
    return result


def validate_defense_sprites(sequence: Mapping[str, Any], sprite_exists: Callable[[str], bool],
                             path: str = "defense_sequence") -> None:
    """Preflight optional projectile/hazard art when an asset resolver exists.

    The pure config loader deliberately has no filesystem/story dependency,
    so callers such as ``GameEngine`` provide the resolver.  Headless tools
    may omit this check and still validate all structural YAML fields.
    """
    def visit(value: Any, current_path: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                child_path = f"{current_path}.{key}"
                if key == "sprite" and isinstance(child, str):
                    if not sprite_exists(normalize_sprite_reference(child)):
                        raise DefenseConfigError(f"{child_path} references missing sprite {child!r}")
                else:
                    visit(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{current_path}[{index}]")
    visit(sequence, path)


# Re-export authoring metadata from the runtime discovery module. The metadata
# module imports this module only when its lookup function is called, so this
# late import does not change runtime initialization.
from .defense_metadata import (  # noqa: E402
    DefensePatternEditorSpec,
    DefensePatternFieldSpec,
    defense_pattern_editor_spec,
    defense_pattern_editor_specs,
    minimal_defense_pattern,
)
