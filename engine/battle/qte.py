"""Reusable, frame-rate independent player attack QTEs.

The classes in this module intentionally know nothing about pygame, enemies,
or damage formulas.  They receive discrete engine actions and :class:`BattleInput`
snapshots, update from elapsed seconds, and produce one shared
:class:`QTEResult`.  The renderer uses ``presentation()`` only; all scoring
remains deterministic and unit-testable here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import inspect
import math
import random
from typing import Any, Callable, get_args, get_origin

from engine.battle.controls import BattleInput


QTE_TYPES = {
    "precision_bar", "charge_release", "shrinking_ring", "rotating_strike",
    "directional_combo", "rhythm_combo", "moving_weak_point", "stability",
    "rapid_slash",
}
QTE_TYPE_ALIASES = {
    "timing_bar": "precision_bar",
    "position_target": "moving_weak_point",
    "timing_sequence": "rhythm_combo",
}
TIERS = ("miss", "weak", "strong", "critical")
DEFAULT_THRESHOLDS = {"weak": 0.25, "strong": 0.70, "critical": 0.95}
DEFAULT_DAMAGE_MULTIPLIERS = {"miss": 0.0, "weak": 0.5, "strong": 1.0, "critical": 1.25}
DIFFICULTY_MODIFIERS = {
    "easy": {"window_scale": 1.25, "speed_scale": 0.85, "force_scale": 0.80},
    "normal": {"window_scale": 1.00, "speed_scale": 1.00, "force_scale": 1.00},
    "hard": {"window_scale": 0.76, "speed_scale": 1.20, "force_scale": 1.25},
}


@dataclass(frozen=True)
class QTEFieldSpec:
    """Runtime-owned authoring metadata for one QTE configuration field.

    This is intentionally a small semantic description rather than a Qt
    widget contract.  The Story Designer consumes it to generate controls,
    while the battle runtime remains the authority for defaults and behavior.
    """

    key: str
    label: str = ""
    description: str = ""
    value_type: str = "number"
    default: Any = None
    has_default: bool = False
    minimum: int | float | None = None
    maximum: int | float | None = None
    enum_values: tuple[Any, ...] = ()
    asset_kind: str | None = None
    group: str = "Parameters"
    authored_section: str = "tuning_parameters"
    editor_hint: str | None = None
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", str(self.key))
        object.__setattr__(self, "label", self.label or self.key.replace("_", " ").title())
        object.__setattr__(self, "description", str(self.description or ""))
        object.__setattr__(self, "enum_values", tuple(self.enum_values or ()))
        object.__setattr__(self, "aliases", tuple(str(value) for value in self.aliases))


@dataclass(frozen=True)
class QTEEditorSpec:
    """Complete authoring metadata for one registered QTE type."""

    type: str
    display_name: str
    description: str
    fields: tuple[QTEFieldSpec, ...] = ()
    supported: bool = True
    unsupported_reason: str | None = None

    @property
    def field_map(self) -> dict[str, QTEFieldSpec]:
        result: dict[str, QTEFieldSpec] = {}
        for field_spec in self.fields:
            result[field_spec.key] = field_spec
            result.update({alias: field_spec for alias in field_spec.aliases})
        return result


def _qte_field_metadata(key: str, default: Any, annotation: Any) -> dict[str, Any]:
    """Infer safe control metadata from a registered QTE constructor."""

    lower = key.lower()
    annotation_text = str(annotation).lower()
    origin = get_origin(annotation)
    args = get_args(annotation)
    if isinstance(default, bool) or annotation is bool or "bool" in annotation_text and "list" not in annotation_text:
        value_type = "boolean"
    elif isinstance(default, int) and not isinstance(default, bool) or annotation is int or "int" in annotation_text and "list" not in annotation_text and "float" not in annotation_text:
        value_type = "integer"
    elif isinstance(default, (list, tuple)) or origin in (list, tuple) or "list[" in annotation_text or "tuple[" in annotation_text:
        value_type = "list"
    elif annotation is str or isinstance(default, str):
        value_type = "string"
    else:
        # Union[int/float, None] and postponed annotations are most useful as
        # numeric controls in this registry.  The runtime constructor still
        # performs the final defensive conversion.
        value_type = "float"
        if args and any(value is int for value in args) and not any(value is float for value in args):
            value_type = "integer"
    minimum: int | float | None = None
    if value_type in {"integer", "float"}:
        minimum = 0 if any(token in lower for token in ("duration", "speed", "radius", "width", "height", "count", "hits", "delay", "gravity", "tolerance", "window", "angle")) else None
    group = "Scoring" if any(token in lower for token in ("threshold", "window", "tolerance", "radius", "multiplier", "score")) else "Timing"
    if any(token in lower for token in ("x", "y", "position", "offset", "height", "width", "radius", "angle", "region", "arc")):
        group = "Geometry"
    if any(token in lower for token in ("sound", "animation", "pitch")):
        group = "Animation"
    if any(token in lower for token in ("input", "prompt", "direction")):
        group = "Input"
    enum_values: tuple[Any, ...] = ()
    if key == "contraction_curve":
        value_type, enum_values = "enum", ("linear",)
    if key == "block_spacing":
        value_type = "list"
    return {
        "value_type": value_type,
        "minimum": minimum,
        "group": group,
        "editor_hint": "scalar_list" if value_type == "list" else None,
        "enum_values": enum_values,
    }


def _build_qte_editor_spec(type_name: str, qte_class: Callable[..., AttackQTE]) -> QTEEditorSpec:
    fields: list[QTEFieldSpec] = []
    common = {"duration", "thresholds", "multipliers", "label", "sound", "animation", "allowed_inputs", "rng"}
    for key, parameter in inspect.signature(qte_class.__init__).parameters.items():
        if key in {"self", *common} or parameter.kind in (parameter.VAR_KEYWORD, parameter.VAR_POSITIONAL):
            continue
        default = parameter.default
        metadata = _qte_field_metadata(key, default, parameter.annotation)
        fields.append(QTEFieldSpec(
            key=key,
            default=None if default is inspect.Parameter.empty else default,
            has_default=default is not inspect.Parameter.empty,
            **metadata,
        ))
    return QTEEditorSpec(
        type_name,
        type_name.replace("_", " ").title(),
        f"Authoring fields for the registered {type_name.replace('_', ' ')} QTE.",
        tuple(fields),
    )


def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def canonical_qte_type(value: str) -> str:
    """Return the supported name for modern and legacy pattern identifiers."""
    return QTE_TYPE_ALIASES.get(value, value)


@dataclass(frozen=True)
class QTEResult:
    """Normalized outcome returned by every player attack QTE.

    ``score`` is always in ``0.0..1.0``.  ``multiplier`` belongs to the QTE
    configuration and is consumed by the battle damage formula; it is not a
    second damage calculation.
    """

    tier: str = "miss"
    score: float = 0.0
    multiplier: float = 0.0
    metrics: dict[str, float | int | str | bool] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.tier.title()


def tier_for_score(score: float, thresholds: dict[str, float] | None = None) -> str:
    thresholds = thresholds or DEFAULT_THRESHOLDS
    score = clamp(score)
    if score >= float(thresholds["critical"]):
        return "critical"
    if score >= float(thresholds["strong"]):
        return "strong"
    if score >= float(thresholds["weak"]):
        return "weak"
    return "miss"


def result_for_score(score: float, thresholds: dict[str, float] | None = None,
                     multipliers: dict[str, float] | None = None,
                     metrics: dict[str, float | int | str | bool] | None = None) -> QTEResult:
    thresholds = thresholds or DEFAULT_THRESHOLDS
    multipliers = multipliers or DEFAULT_DAMAGE_MULTIPLIERS
    score = clamp(score)
    tier = tier_for_score(score, thresholds)
    return QTEResult(tier, score, float(multipliers[tier]), metrics or {})


def score_from_error(error: float, critical_error: float, strong_error: float,
                     weak_error: float, thresholds: dict[str, float]) -> float:
    """Map a distance/timing error to the shared score bands.

    Interpolating inside a score band keeps scores useful for debugging while
    exact thresholds still describe every QTE's resulting tiers.
    """
    error = max(0.0, error)
    weak, strong, critical = (float(thresholds[key]) for key in ("weak", "strong", "critical"))
    if error > weak_error:
        return 0.0
    if error <= critical_error:
        fraction = 1.0 if critical_error <= 0 else 1.0 - error / critical_error
        return critical + (1.0 - critical) * fraction
    if error <= strong_error:
        fraction = (error - critical_error) / max(1e-9, strong_error - critical_error)
        return critical + (strong - critical) * fraction
    fraction = (error - strong_error) / max(1e-9, weak_error - strong_error)
    return strong + (weak - strong) * fraction


class AttackQTE:
    """Small lifecycle interface shared by all player attack QTEs."""

    qte_type = "attack_qte"
    tutorial_instruction = "Press A / Enter"

    def __init__(self, duration: float, thresholds: dict[str, float] | None = None,
                 multipliers: dict[str, float] | None = None, label: str | None = None,
                 sound: str | None = None, animation: str | None = None,
                 allowed_inputs: list[str] | None = None):
        self.duration = max(0.01, float(duration))
        self.thresholds = dict(DEFAULT_THRESHOLDS)
        self.thresholds.update(thresholds or {})
        self.multipliers = dict(DEFAULT_DAMAGE_MULTIPLIERS)
        self.multipliers.update(multipliers or {})
        self.label = label or ""
        self.sound = sound
        self.animation = animation
        self.allowed_inputs = tuple(allowed_inputs or ())
        self.elapsed = 0.0
        self.done = False
        self.result = QTEResult(multiplier=self.multipliers["miss"])

    # ``score``/``outcome`` retain the old sequence surface for external
    # callers while the structured ``result`` exposes normalized scoring.
    @property
    def score(self) -> float:
        return self.result.multiplier

    @property
    def outcome(self) -> str:
        return self.result.label

    def start(self) -> None:
        """Lifecycle hook for a future delayed-start presentation."""

    def update(self, dt: float, movement: BattleInput = BattleInput()) -> None:
        del movement
        if self.done:
            return
        self.elapsed += max(0.0, dt)
        if self.elapsed >= self.duration:
            self.complete(0.0, {"timeout": True, "elapsed": self.elapsed})

    def handle_action(self, action: str) -> bool:
        del action
        return False

    def allows_action(self, action: str) -> bool:
        """Apply an optional configured input allow-list consistently."""
        if not self.allowed_inputs:
            return True
        normalized = "SELECT" if action == "SELECT_RELEASE" else action
        return normalized in self.allowed_inputs

    def confirm(self) -> bool:
        """Compatibility alias for a normal attack-button press."""
        return self.handle_action("SELECT")

    def complete(self, score: float, metrics: dict[str, float | int | str | bool] | None = None) -> None:
        if self.done:
            return
        self.result = result_for_score(score, self.thresholds, self.multipliers, metrics)
        self.done = True

    def complete_tier(self, tier: str, metrics: dict[str, float | int | str | bool] | None = None) -> None:
        """Finish at a named tier while retaining configurable thresholds."""
        if tier == "critical":
            score = 1.0
        elif tier == "strong":
            score = (self.thresholds["strong"] + self.thresholds["critical"]) / 2
        elif tier == "weak":
            score = (self.thresholds["weak"] + self.thresholds["strong"]) / 2
        else:
            score = 0.0
        self.complete(score, metrics)

    def presentation(self) -> dict[str, Any]:
        return {"kind": self.qte_type, "tutorial_instruction": self.tutorial_instruction, "progress": clamp(self.elapsed / self.duration),
                "label": self.label, "animation": self.animation}


class PrecisionBarQTE(AttackQTE):
    """Ping-pong timing bar, refactored from the original attack sequence."""

    qte_type = "precision_bar"
    tutorial_instruction = "Press A / Enter at the target"
    TARGET_APPROACH_FRACTION = 0.75

    def __init__(self, duration: float = 1.0, target_position: float = 0.5,
                 critical_window: float = 0.008, strong_window: float = 0.04,
                 weak_window: float = 0.16, speed_multiplier: float = 1.0,
                 **kwargs: Any):
        super().__init__(duration, **kwargs)
        self.target_position = clamp(target_position)
        self.critical_window = max(0.0001, critical_window)
        self.strong_window = max(self.critical_window, strong_window)
        self.weak_window = max(self.strong_window, weak_window)
        # The marker starts at zero.  Guarantee enough travel speed to cover
        # the target distance before the final quarter of the QTE, leaving a
        # render frame in which the target can be shown and pressed.
        minimum_target_speed = self.target_position / self.TARGET_APPROACH_FRACTION
        self.speed_multiplier = max(0.01, speed_multiplier, minimum_target_speed)
        # The marker starts at the left edge and initially travels right.
        # Retain one exact target frame when a render update would otherwise
        # move it from one side of the critical strip to the other.
        self.center_frame_shown = self.target_position == 0.0
        self.center_frame_visible = self.center_frame_shown

    @property
    def target_cross_time(self) -> float:
        """Time at which the initial left-to-right pass reaches the target."""
        return self.target_position * self.duration / self.speed_multiplier

    @property
    def indicator_position(self) -> float:
        if self.center_frame_visible:
            return self.target_position
        cycle = (self.elapsed * self.speed_multiplier / self.duration) % 2.0
        return cycle if cycle <= 1.0 else 2.0 - cycle

    def update(self, dt: float, movement: BattleInput = BattleInput()) -> None:
        del movement
        if self.done:
            return
        dt = max(0.0, dt)
        self.center_frame_visible = False
        next_elapsed = self.elapsed + dt
        # Like rotating_strike, preserve a hittable frame at the center of
        # the critical strip even if the marker moves farther than its width
        # during one render update.  The crossing time derives from the
        # marker's starting edge, target distance, and current speed.
        if (not self.center_frame_shown
                and self.elapsed < self.target_cross_time <= next_elapsed):
            self.center_frame_shown = True
            self.center_frame_visible = True
        self.elapsed = next_elapsed
        if self.elapsed >= self.duration:
            self.complete(0.0, {"timeout": True, "elapsed": self.elapsed})

    def handle_action(self, action: str) -> bool:
        if self.done or action != "SELECT" or not self.allows_action(action):
            return False
        distance = abs(self.indicator_position - self.target_position)
        self.complete(score_from_error(distance, self.critical_window, self.strong_window, self.weak_window, self.thresholds),
                      {"timing_error": distance, "indicator_position": self.indicator_position})
        return True

    def presentation(self) -> dict[str, Any]:
        data = super().presentation()
        data.update({"target": self.target_position, "indicator": self.indicator_position,
                     "critical_window": self.critical_window, "strong_window": self.strong_window,
                     "weak_window": self.weak_window})
        return data


class ChargeReleaseQTE(AttackQTE):
    """Tap-to-charge mallet swing with an inactivity-triggered release.

    The charged angle is deliberately discrete and authoritative.  Rendering
    may use the accompanying presentation data however it likes, but scoring
    never depends on an in-between animation frame.
    """

    qte_type = "charge_release"
    tutorial_instruction = ""

    CHARGING = "CHARGING"
    RELEASING = "RELEASING"
    FAILED_RELEASE = "FAILED_RELEASE"
    COMPLETE = "COMPLETE"
    MAX_CHARGE_ANGLE_DEGREES = 180.0
    DEFAULT_CHARGE_TWEEN_DURATION_SECONDS = .5
    FAILED_RELEASE_DURATION_SECONDS = .25

    def __init__(self, duration: float = .833, charge_step_degrees: float = 15.0,
                 charge_step_decrement_degrees: float = 1.0,
                 minimum_charge_step_degrees: float = 3.0,
                 charge_tween_duration_seconds: float = DEFAULT_CHARGE_TWEEN_DURATION_SECONDS,
                 release_delay_seconds: float = .333, swing_duration_seconds: float = .5,
                 release_strike_arc_start_degrees: float = 5.0,
                 release_strike_arc_end_degrees: float = 20.0,
                 arc_start_min_degrees: float = 100.0, arc_start_max_degrees: float = 130.0,
                 weak_arc_width_degrees: float = 30.0, strong_arc_width_degrees: float = 10.0,
                 critical_arc_width_degrees: float = 5.0, rng: random.Random | None = None,
                 **kwargs: Any):
        # ``duration`` remains an accepted common QTE field for content
        # compatibility, but this pattern's actual lifetime is controlled by
        # its inactivity delay and fixed release swing.
        super().__init__(duration, **kwargs)
        self.minimum_charge_step_degrees = max(.0001, min(self.MAX_CHARGE_ANGLE_DEGREES,
                                                          float(minimum_charge_step_degrees)))
        self.charge_step_degrees = max(self.minimum_charge_step_degrees,
                                       min(self.MAX_CHARGE_ANGLE_DEGREES, float(charge_step_degrees)))
        self.charge_step_decrement_degrees = max(.0001, float(charge_step_decrement_degrees))
        self.charge_tween_duration_seconds = max(.0001, float(charge_tween_duration_seconds))
        self.release_delay_seconds = max(.0001, float(release_delay_seconds))
        self.swing_duration_seconds = max(.0001, float(swing_duration_seconds))
        self.release_strike_arc_start_degrees = clamp(float(release_strike_arc_start_degrees), 0.0,
                                                      self.MAX_CHARGE_ANGLE_DEGREES)
        self.release_strike_arc_end_degrees = clamp(max(self.release_strike_arc_start_degrees,
                                                        float(release_strike_arc_end_degrees)),
                                                    self.release_strike_arc_start_degrees,
                                                    self.MAX_CHARGE_ANGLE_DEGREES)
        self.weak_arc_width_degrees = max(.0001, float(weak_arc_width_degrees))
        self.strong_arc_width_degrees = max(.0001, float(strong_arc_width_degrees))
        self.critical_arc_width_degrees = max(.0001, float(critical_arc_width_degrees))
        self.rng = rng or random.Random()

        total_arc_width = (self.weak_arc_width_degrees + self.strong_arc_width_degrees
                           + self.critical_arc_width_degrees)
        largest_valid_arc_start = max(0.0, self.MAX_CHARGE_ANGLE_DEGREES - total_arc_width)
        requested_minimum = float(arc_start_min_degrees)
        requested_maximum = max(requested_minimum, float(arc_start_max_degrees))
        self.arc_start_min_degrees = clamp(requested_minimum, 0.0, largest_valid_arc_start)
        self.arc_start_max_degrees = clamp(requested_maximum, self.arc_start_min_degrees, largest_valid_arc_start)
        self.arc_start_degrees = self.rng.uniform(self.arc_start_min_degrees, self.arc_start_max_degrees)
        self.weak_arc = (self.arc_start_degrees, self.arc_start_degrees + self.weak_arc_width_degrees)
        self.strong_arc = (self.weak_arc[1], self.weak_arc[1] + self.strong_arc_width_degrees)
        self.critical_arc = (self.strong_arc[1], self.strong_arc[1] + self.critical_arc_width_degrees)

        self.state = self.CHARGING
        self.started_at = self.elapsed
        self.last_press_at: float | None = None
        self.accepted_press_count = 0
        self.target_charge_angle = 0.0
        self.current_charge_step_degrees = self.charge_step_degrees
        self.last_charge_step_degrees = 0.0
        self.rendered_charge_angle = 0.0
        self.charge_tween_start_angle = 0.0
        self.charge_tween_started_at = self.elapsed
        self.release_started_at: float | None = None
        self.release_start_angle = 0.0
        self.release_elapsed = 0.0
        self.released_tier: str | None = None
        self.strike_confirmed = False
        self.head_detached = False
        self.head_detach_angle = 0.0
        self.failed_release_started_at: float | None = None
        self.failed_release_elapsed = 0.0
        self.failed_handle_start_angle = 0.0
        # Key-up resets this guard.  It keeps operating-system key repeat
        # from becoming free charge while still accepting press/release pairs
        # that arrive within a single rendered frame.
        self._enter_held = False

    @property
    def charged_angle(self) -> float:
        """Return the deterministic sum of accepted, decreasing charge steps."""
        return self.target_charge_angle

    @property
    def mallet_angle(self) -> float:
        if self.state == self.CHARGING:
            return self.rendered_charge_angle
        if self.state == self.FAILED_RELEASE:
            progress = clamp(self.failed_release_elapsed / self.FAILED_RELEASE_DURATION_SECONDS)
            return self.failed_handle_start_angle * (1.0 - progress ** 3)
        if self.state != self.RELEASING:
            return self.target_charge_angle
        return self._release_mallet_angle(self.release_elapsed)

    def _release_mallet_angle(self, release_elapsed: float) -> float:
        progress = clamp(release_elapsed / self.swing_duration_seconds)
        # Cubic ease-in makes the return accelerate sharply into its impact
        # without ever overshooting the 0-degree resting position.
        return self.release_start_angle * (1.0 - progress ** 3)

    @property
    def detached_head_drop(self) -> float:
        """Detached-head fall in meter radii, independent of display size."""
        if not self.head_detached:
            return 0.0
        return .72 * self.release_elapsed + .9 * self.release_elapsed ** 2

    @property
    def release_strike_deadline_elapsed(self) -> float:
        """Last release-animation instant at which the strike window is open."""
        if self.release_start_angle <= self.release_strike_arc_start_degrees:
            return 0.0
        progress = (1.0 - self.release_strike_arc_start_degrees / self.release_start_angle) ** (1.0 / 3.0)
        return self.swing_duration_seconds * clamp(progress)

    def _tier_for_angle(self, angle: float) -> str:
        weak_start, weak_end = self.weak_arc
        strong_start, strong_end = self.strong_arc
        critical_start, critical_end = self.critical_arc
        if weak_start <= angle < weak_end:
            return "weak"
        if strong_start <= angle < strong_end:
            return "strong"
        if critical_start <= angle <= critical_end:
            return "critical"
        return "miss"

    def _update_charge_tween(self) -> None:
        """Advance the visual hammer angle toward its authoritative target.

        A new tap starts a fresh half-second tween from the currently drawn
        angle. Its speed is therefore proportional to the new gap: continued
        tapping pulls the target farther away and visibly accelerates the
        hammer without changing discrete scoring.
        """
        progress = clamp((self.elapsed - self.charge_tween_started_at)
                         / self.charge_tween_duration_seconds)
        self.rendered_charge_angle = (self.charge_tween_start_angle
                                      + (self.target_charge_angle - self.charge_tween_start_angle) * progress)

    def _release_metrics(self) -> dict[str, float | int | str | bool]:
        return {
            "charged_angle_degrees": self.release_start_angle,
            "accepted_presses": self.accepted_press_count,
            "last_charge_step_degrees": self.last_charge_step_degrees,
            "arc_start_degrees": self.arc_start_degrees,
            "released": True,
            "release_strike_confirmed": self.strike_confirmed,
            "timeout": self.accepted_press_count == 0,
            "head_detached": self.head_detached,
        }

    def _begin_release(self, started_at: float) -> None:
        """Freeze charge/scoring exactly once before the visual swing."""
        if self.state != self.CHARGING:
            return
        self.release_started_at = started_at
        self.release_start_angle = self.target_charge_angle
        self.released_tier = self._tier_for_angle(self.release_start_angle)
        self.head_detached = False
        self.state = self.RELEASING
        self.release_elapsed = max(0.0, self.elapsed - started_at)

    def _finish_release_if_ready(self) -> None:
        if self.release_elapsed + 1e-9 < self.swing_duration_seconds:
            return
        self.release_elapsed = self.swing_duration_seconds
        tier = self.released_tier or "miss"
        self.state = self.COMPLETE
        self.complete_tier(tier, self._release_metrics())

    def _begin_failed_release(self, started_at: float) -> None:
        """Detach at the strike-window exit and fly the head up-left."""
        if self.state != self.RELEASING:
            return
        self.head_detach_angle = self._release_mallet_angle(self.release_strike_deadline_elapsed)
        self.failed_handle_start_angle = self.head_detach_angle
        self.head_detached = True
        self.failed_release_started_at = started_at
        self.failed_release_elapsed = max(0.0, self.elapsed - started_at)
        self.state = self.FAILED_RELEASE

    def _finish_failed_release_if_ready(self) -> None:
        if self.failed_release_elapsed + 1e-9 < self.FAILED_RELEASE_DURATION_SECONDS:
            return
        self.failed_release_elapsed = self.FAILED_RELEASE_DURATION_SECONDS
        self.state = self.COMPLETE
        self.complete_tier("miss", self._release_metrics())

    def update(self, dt: float, movement: BattleInput = BattleInput()) -> None:
        del movement
        if self.done:
            return
        self.elapsed += max(0.0, dt)
        if self.state == self.CHARGING and self.last_press_at is not None:
            self._update_charge_tween()
            reference_time = self.last_press_at
            release_at = reference_time + self.release_delay_seconds
            if self.elapsed + 1e-9 >= release_at:
                # Beginning at the actual deadline, rather than the end of a
                # long frame, lets a stalled frame also consume the correct
                # amount of the fixed 0.5-second release animation.
                self._begin_release(release_at)
        if self.state == self.RELEASING and self.release_started_at is not None:
            self.release_elapsed = min(self.swing_duration_seconds,
                                       max(0.0, self.elapsed - self.release_started_at))
            if not self.strike_confirmed:
                failed_at = self.release_started_at + self.release_strike_deadline_elapsed
                if self.elapsed + 1e-9 >= failed_at:
                    self._begin_failed_release(failed_at)
            if self.state == self.RELEASING:
                self._finish_release_if_ready()
        if self.state == self.FAILED_RELEASE and self.failed_release_started_at is not None:
            self.failed_release_elapsed = min(self.FAILED_RELEASE_DURATION_SECONDS,
                                               max(0.0, self.elapsed - self.failed_release_started_at))
            self._finish_failed_release_if_ready()

    def handle_action(self, action: str) -> bool:
        if self.done or not self.allows_action(action):
            return False
        if self.state == self.RELEASING:
            if action != "SELECT":
                return action == "SELECT_RELEASE"
            if self.release_strike_arc_start_degrees <= self.mallet_angle <= self.release_strike_arc_end_degrees:
                self.strike_confirmed = True
            return True
        if self.state != self.CHARGING:
            return False
        if action == "SELECT_RELEASE":
            self._enter_held = False
            return True
        if action != "SELECT":
            return False
        if self._enter_held:
            # Consume a repeated KEYDOWN but do not count it as a new tap.
            return True
        self._update_charge_tween()
        self._enter_held = True
        self.accepted_press_count += 1
        self.last_charge_step_degrees = self.current_charge_step_degrees
        self.target_charge_angle = min(self.MAX_CHARGE_ANGLE_DEGREES,
                                       self.target_charge_angle + self.last_charge_step_degrees)
        self.current_charge_step_degrees = max(
            self.minimum_charge_step_degrees,
            self.current_charge_step_degrees - self.charge_step_decrement_degrees,
        )
        self.charge_tween_start_angle = self.rendered_charge_angle
        self.charge_tween_started_at = self.elapsed
        self.last_press_at = self.elapsed
        return True

    def presentation(self) -> dict[str, Any]:
        data = super().presentation()
        release_progress = (clamp(self.release_elapsed / self.swing_duration_seconds)
                            if self.state == self.RELEASING else 0.0)
        data.update({
            "progress": release_progress if self.state == self.RELEASING else 0.0,
            "state": self.state,
            "mallet_angle": self.mallet_angle,
            "charged_angle": self.target_charge_angle,
            "next_charge_step_degrees": self.current_charge_step_degrees,
            "last_charge_step_degrees": self.last_charge_step_degrees,
            "charge_tween_duration_seconds": self.charge_tween_duration_seconds,
            "accepted_presses": self.accepted_press_count,
            "scoring_arcs": {"weak": self.weak_arc, "strong": self.strong_arc,
                             "critical": self.critical_arc},
            "release_progress": release_progress,
            "result_tier": self.released_tier,
            "release_strike_arc": (self.release_strike_arc_start_degrees, self.release_strike_arc_end_degrees),
            "strike_confirmed": self.strike_confirmed,
            "head_detached": self.head_detached,
            "detached_head": ({"angle": self.head_detach_angle,
                                "offset_x": -1.15 * clamp(self.failed_release_elapsed / self.FAILED_RELEASE_DURATION_SECONDS),
                                "offset_y": -.85 * clamp(self.failed_release_elapsed / self.FAILED_RELEASE_DURATION_SECONDS),
                                "rotation": self.failed_release_elapsed * 720.0}
                               if self.head_detached else None),
        })
        return data


class ShrinkingRingQTE(AttackQTE):
    qte_type = "shrinking_ring"
    tutorial_instruction = "Guide the shrinking ring onto the bullseye with the D-pad / WASD"

    def __init__(self, duration: float = 1.5, starting_radius: float = 0.48,
                 target_radius: float = 0.090, critical_tolerance: float = 0.012,
                 strong_tolerance: float = 0.040, weak_tolerance: float = 0.090,
                 target_x: float = 0.5, target_y: float = 0.5,
                 ring_x: float = 0.20, ring_y: float = 0.78,
                 movement_speed: float = 0.60, collapse_hold: float = 0.18,
                 contraction_curve: str = "linear", **kwargs: Any):
        super().__init__(duration, **kwargs)
        self.starting_radius = max(0.001, starting_radius)
        self.target_radius = max(0.001, target_radius)
        self.critical_tolerance = critical_tolerance
        self.strong_tolerance = max(critical_tolerance, strong_tolerance)
        self.weak_tolerance = max(strong_tolerance, weak_tolerance, self.target_radius)
        self.target_x, self.target_y = clamp(target_x), clamp(target_y)
        self.ring_x, self.ring_y = clamp(ring_x), clamp(ring_y)
        self.movement_speed = max(0.01, movement_speed)
        self.collapse_hold = max(0.0, collapse_hold)
        self.collapse_remaining: float | None = None

    @property
    def moving_radius(self) -> float:
        # The collapse is deliberately linear: it is an easily readable
        # countdown, while the player's spatial correction supplies challenge.
        return self.starting_radius * (1 - clamp(self.elapsed / self.duration))

    def update(self, dt: float, movement: BattleInput = BattleInput()) -> None:
        if self.done:
            return
        dt = max(0.0, dt)
        if self.collapse_remaining is not None:
            self.collapse_remaining -= dt
            if self.collapse_remaining <= 0:
                distance = math.hypot(self.ring_x - self.target_x, self.ring_y - self.target_y)
                self.complete(score_from_error(distance, self.critical_tolerance, self.strong_tolerance, self.weak_tolerance, self.thresholds),
                              {"distance": distance, "collapsed": True, "timeout": True})
            return
        length = math.hypot(movement.move_x, movement.move_y)
        move_x, move_y = movement.move_x, movement.move_y
        if length > 1:
            move_x, move_y = move_x / length, move_y / length
        self.ring_x = clamp(self.ring_x + move_x * self.movement_speed * dt)
        self.ring_y = clamp(self.ring_y + move_y * self.movement_speed * dt)
        self.elapsed += dt
        if self.elapsed >= self.duration:
            self.elapsed = self.duration
            self.collapse_remaining = self.collapse_hold

    def handle_action(self, action: str) -> bool:
        del action
        # This QTE resolves at collapse; Enter is intentionally not a second
        # timing test after the player has already steered the ring.
        return False

    def presentation(self) -> dict[str, Any]:
        data = super().presentation()
        data.update({"moving_radius": self.moving_radius, "ring": (self.ring_x, self.ring_y),
                     "target": (self.target_x, self.target_y), "critical_radius": self.critical_tolerance,
                     "strong_radius": self.strong_tolerance, "weak_radius": self.weak_tolerance,
                     "collapsing": self.collapse_remaining is not None})
        return data


class RotatingStrikeQTE(AttackQTE):
    qte_type = "rotating_strike"
    tutorial_instruction = "Strike each colored arc as the pointer crosses it"
    MISS_GRACE_SECONDS = 0.25
    STRIKE_SPEED_MULTIPLIER = 1.25

    # Three full laps used to complete in 1.45 seconds, which moved the hand
    # roughly 745 degrees per second.  Keep the three opportunities, but give
    # each arc a readable approach at a little under 35% of that old speed.
    def __init__(self, duration: float = 4.2, target_angle: float = 35.0,
                 rotations: float = 1.0, critical_window: float = 3.0,
                 strong_window: float = 12.0, weak_window: float = 30.0,
                 **kwargs: Any):
        super().__init__(duration, **kwargs)
        self.target_angle = target_angle % 360
        self.rotations = max(3.0, rotations)
        self.critical_window = critical_window
        self.strong_window = max(critical_window, strong_window)
        self.weak_window = max(strong_window, weak_window)
        self.stage = 0
        self.achieved_stage = -1
        self.success_flash = 0.0
        self.last_hit_tier: str | None = None
        # The final strike resolves the QTE immediately.  Locking its last
        # rendered frame to the arc center makes the impact unambiguous.
        self.final_strike_angle: float | None = None
        # In mathematical (pygame arc) angles, decreasing angles move
        # clockwise.  Starting 270 degrees counter-clockwise from the arc
        # gives the player a clear three-quarter-turn approach.
        self.start_angle = (self.target_angle + 270) % 360
        self.stage_started_at = 0.0
        self.stage_start_angle = self.start_angle
        self.center_frame_shown = False
        self.center_frame_visible = False

    @property
    def speed_multiplier(self) -> float:
        """Speed for the active strike, compounded after each success."""
        return self.STRIKE_SPEED_MULTIPLIER ** self.stage

    @property
    def angular_speed(self) -> float:
        return 360 * self.rotations * self.speed_multiplier / self.duration

    @property
    def angle(self) -> float:
        if self.final_strike_angle is not None:
            return self.final_strike_angle
        if self.center_frame_visible:
            return self.target_angle
        return (self.stage_start_angle - self.angular_speed * (self.elapsed - self.stage_started_at)) % 360

    @property
    def turns_elapsed(self) -> float:
        return self.angular_speed * (self.elapsed - self.stage_started_at) / 360

    @property
    def target_turn(self) -> float:
        """Turns from the active strike's start to its target arc."""
        turns = (self.stage_start_angle - self.target_angle) % 360 / 360
        # After the first hit, each successive strike gets a full new lap.
        # This also ensures early or late valid hits still approach the next
        # target rather than merely returning to the prior hit angle.
        return turns if self.stage == 0 else turns + 1.0

    @property
    def target_cross_time(self) -> float:
        """Time at which the pointer crosses the center of the active arc."""
        return self.stage_started_at + self.target_turn * 360 / self.angular_speed

    @property
    def arc_pass_time(self) -> float:
        """Time at which the pointer clears the active arc's far edge."""
        return self.target_cross_time + self.current_window / self.angular_speed

    @property
    def pointer_vector(self) -> tuple[float, float]:
        """Convert the model angle to pygame's mathematical arc convention.

        Pygame draws positive arc angles toward the top of the display.  Its
        screen y-axis still points down, so the pointer must invert sine too.
        Keeping this conversion with the QTE model prevents the renderer from
        silently using a mirrored coordinate system.
        """
        radians = math.radians(self.angle)
        return math.cos(radians), -math.sin(radians)

    @property
    def current_window(self) -> float:
        return (self.weak_window, self.strong_window, self.critical_window)[min(self.stage, 2)]

    @property
    def current_tier(self) -> str:
        return ("weak", "strong", "critical")[min(self.stage, 2)]

    def update(self, dt: float, movement: BattleInput = BattleInput()) -> None:
        del movement
        if self.done:
            return
        dt = max(0.0, dt)
        self.success_flash = max(0.0, self.success_flash - dt)
        self.center_frame_visible = False
        next_elapsed = self.elapsed + dt
        # Render updates are discrete, so a fast spinner can otherwise jump
        # from one side of the arc to the other without ever being drawn at
        # its center.  Stop once at the exact crossing and let the next update
        # resume movement; this guarantees a visible, hittable center frame.
        if (not self.center_frame_shown
                and self.elapsed < self.target_cross_time <= next_elapsed):
            self.elapsed = next_elapsed
            self.center_frame_shown = True
            self.center_frame_visible = True
        else:
            self.elapsed = next_elapsed
        if self.elapsed >= self.duration:
            self.complete_tier(("miss", "weak", "strong", "critical")[self.achieved_stage + 1],
                               {"opportunities_hit": self.achieved_stage + 1, "timeout": True})
            return
        # A stage has exactly one pass.  Once the pointer clears the far edge
        # of its arc, preserve any prior success and resolve immediately.
        if self.elapsed > self.arc_pass_time + self.MISS_GRACE_SECONDS:
            self.complete_tier(("miss", "weak", "strong", "critical")[self.achieved_stage + 1],
                               {"opportunities_hit": self.achieved_stage + 1,
                                "missed_stage": self.stage, "passed_arc": True,
                                "miss_grace": self.MISS_GRACE_SECONDS})

    def handle_action(self, action: str) -> bool:
        if self.done or action != "SELECT" or not self.allows_action(action):
            return False
        difference = abs((self.angle - self.target_angle + 180) % 360 - 180)
        # A late press in the brief post-arc grace period saves the current
        # opportunity.  This absorbs the natural delay between seeing the
        # pointer clear the arc and pressing Enter.
        in_late_grace = self.arc_pass_time < self.elapsed <= self.arc_pass_time + self.MISS_GRACE_SECONDS
        if difference > self.current_window and not in_late_grace:
            self.complete_tier(("miss", "weak", "strong", "critical")[self.achieved_stage + 1],
                               {"angle_error": difference, "pointer_angle": self.angle,
                                "opportunities_hit": self.achieved_stage + 1, "missed_stage": self.stage})
            return True
        struck_angle = self.angle
        self.achieved_stage = self.stage
        self.last_hit_tier = self.current_tier
        self.stage += 1
        # Leave a clear confirmation behind while the next, tighter arc is
        # armed.  Without it a valid first strike looked like it vanished and
        # was later scored as a miss.
        self.success_flash = 0.35
        if self.stage >= 3:
            self.final_strike_angle = self.target_angle
            self.complete_tier("critical", {"angle_error": difference, "pointer_angle": struck_angle,
                                             "opportunities_hit": 3})
        else:
            # Each successful strike starts a fresh lap at 25% more speed.
            # Retaining the actual strike angle makes early and late valid
            # inputs flow naturally into the next arc's approach.
            self.stage_started_at = self.elapsed
            self.stage_start_angle = struck_angle
            self.center_frame_shown = False
            self.center_frame_visible = False
        return True

    def presentation(self) -> dict[str, Any]:
        data = super().presentation()
        data.update({"angle": self.angle, "pointer_vector": self.pointer_vector,
                     "target_angle": self.target_angle, "window": self.current_window,
                     "stage": self.stage, "achieved_stage": self.achieved_stage, "success_flash": self.success_flash,
                     "last_hit_tier": self.last_hit_tier, "stage_ready": True})
        return data


class DirectionalComboQTE(AttackQTE):
    qte_type = "directional_combo"
    tutorial_instruction = "Press the matching direction as the target crosses its region"
    DIRECTION_ORDER = ("UP", "DOWN", "LEFT", "RIGHT")
    DIRECTION_VECTORS = {
        "UP": (0.0, -1.0), "DOWN": (0.0, 1.0),
        "LEFT": (-1.0, 0.0), "RIGHT": (1.0, 0.0),
    }
    CENTER = (0.5, 0.5)

    def __init__(self, duration: float = 4.8, required_hits: int = 4,
                 initial_speed: float = 0.44, speed_increase: float = 0.08,
                 max_speed_multiplier: float = 3.0, strong_threshold_ratio: float = 0.70,
                 striking_region_size: float = 0.18, striking_region_inset: float = 0.07,
                 strike_flash_duration: float = 0.14, final_critical_pause: float = 0.35,
                 target_radius: float = 0.025, rng: random.Random | None = None,
                 # These were the ordered-prompt fields used by the prior
                 # implementation.  Keep accepting them so existing move YAML
                 # remains loadable; directional striking deliberately ignores
                 # their values in favor of a random outbound direction.
                 prompts: list[str] | None = None, prompt_count: int | None = None,
                 response_window: float | None = None, **kwargs: Any):
        super().__init__(duration, **kwargs)
        del prompts, prompt_count, response_window
        self.required_hits = max(1, int(required_hits))
        self.initial_speed = max(0.001, float(initial_speed))
        self.speed_increase = max(0.0, float(speed_increase))
        self.max_speed_multiplier = max(1.0, float(max_speed_multiplier))
        self.strong_threshold_ratio = clamp(float(strong_threshold_ratio), 0.0, 1.0)
        self.target_radius = min(0.24, max(0.001, float(target_radius)))
        self.striking_region_inset = min(0.5 - self.target_radius - 0.002,
                                         max(0.0, float(striking_region_inset)))
        # Regions must remain separate and leave the exact centre outside the
        # target circle.  Configuration validation reports authored values;
        # this defensive cap also makes direct construction safe for tools.
        maximum_region_size = max(0.001, min(
            (0.5 - self.striking_region_inset) / 1.5,
            0.5 - self.striking_region_inset - self.target_radius - 1e-6,
        ))
        self.striking_region_size = min(maximum_region_size, max(0.001, float(striking_region_size)))
        self.strike_flash_duration = max(0.0, float(strike_flash_duration))
        self.final_critical_pause = max(0.0, float(final_critical_pause))
        self.rng = rng or random.Random()
        self.target_x, self.target_y = self.CENTER
        self.outbound_direction = self.rng.choice(self.DIRECTION_ORDER)
        self.phase = "outbound"
        self.hits = 0
        self.held_directions: tuple[str, ...] = ()
        self.attempted_directions: tuple[str, ...] = ()
        self.strike_flash_remaining = 0.0
        self.final_pause_remaining: float | None = None
        self._latched_directions: set[str] = set()

    @property
    def maximum_speed(self) -> float:
        return self.initial_speed * self.max_speed_multiplier

    @property
    def current_speed(self) -> float:
        return min(self.maximum_speed, self.initial_speed + self.hits * self.speed_increase)

    @property
    def strong_required_hits(self) -> int:
        """Hits needed for strong, with useful rounding at every total.

        A partial result cannot be critical.  For totals above one, clamp the
        ceiling of the configured ratio into the available partial-hit range,
        which keeps a strong result achievable even when a ratio rounds up to
        the total (for example, 70% of two hits).
        """
        if self.required_hits <= 1:
            return 1
        return min(self.required_hits - 1, max(1, math.ceil(self.required_hits * self.strong_threshold_ratio)))

    @property
    def hit_ratio(self) -> float:
        return self.hits / self.required_hits

    @property
    def provisional_tier(self) -> str:
        if self.hits <= 0:
            return "miss"
        if self.hits >= self.required_hits:
            return "critical"
        if self.hits >= self.strong_required_hits:
            return "strong"
        return "weak"

    @property
    def input_locked(self) -> bool:
        return self.done or self.final_pause_remaining is not None

    def region_rect(self, direction: str) -> tuple[float, float, float, float]:
        """Return one normalized square (left, top, width, height)."""
        size, inset = self.striking_region_size, self.striking_region_inset
        if direction == "UP":
            return (0.5 - size / 2, inset, size, size)
        if direction == "DOWN":
            return (0.5 - size / 2, 1 - inset - size, size, size)
        if direction == "LEFT":
            return (inset, 0.5 - size / 2, size, size)
        if direction == "RIGHT":
            return (1 - inset - size, 0.5 - size / 2, size, size)
        raise ValueError(f"unknown directional region {direction!r}")

    def target_overlaps_region(self, direction: str) -> bool:
        """Circle-vs-rectangle collision in the same normalized space as UI."""
        left, top, width, height = self.region_rect(direction)
        nearest_x = clamp(self.target_x, left, left + width)
        nearest_y = clamp(self.target_y, top, top + height)
        return math.hypot(self.target_x - nearest_x, self.target_y - nearest_y) <= self.target_radius

    def _set_held_directions(self, movement: BattleInput) -> None:
        held: list[str] = []
        if movement.move_y < 0:
            held.append("UP")
        elif movement.move_y > 0:
            held.append("DOWN")
        if movement.move_x < 0:
            held.append("LEFT")
        elif movement.move_x > 0:
            held.append("RIGHT")
        self.held_directions = tuple(held)
        self._latched_directions.intersection_update(self.held_directions)

    def _move_target(self, direction: str, distance: float) -> None:
        vector_x, vector_y = self.DIRECTION_VECTORS[direction]
        self.target_x += vector_x * distance
        self.target_y += vector_y * distance

    def _distance_to_edge(self) -> float:
        if self.outbound_direction == "UP":
            return self.target_y
        if self.outbound_direction == "DOWN":
            return 1 - self.target_y
        if self.outbound_direction == "LEFT":
            return self.target_x
        return 1 - self.target_x

    def _start_next_outbound(self) -> None:
        self.target_x, self.target_y = self.CENTER
        # A fresh direction keeps the rhythm readable; do not immediately
        # repeat the region that was just struck.
        choices = tuple(direction for direction in self.DIRECTION_ORDER if direction != self.outbound_direction)
        self.outbound_direction = self.rng.choice(choices)
        self.phase = "outbound"

    def _metrics(self, **extra: float | int | str | bool) -> dict[str, float | int | str | bool]:
        metrics: dict[str, float | int | str | bool] = {
            "hits": self.hits,
            "required_hits": self.required_hits,
            "strong_required_hits": self.strong_required_hits,
            "completion": self.hit_ratio,
        }
        metrics.update(extra)
        return metrics

    def _complete_for_hits(self, **extra: float | int | str | bool) -> None:
        self.complete_tier(self.provisional_tier, self._metrics(**extra))

    def update(self, dt: float, movement: BattleInput = BattleInput()) -> None:
        if self.done:
            return
        dt = max(0.0, dt)
        self.elapsed += dt
        self._set_held_directions(movement)
        self.strike_flash_remaining = max(0.0, self.strike_flash_remaining - dt)
        if self.final_pause_remaining is not None:
            # Freeze directional feedback with the final critical strike so
            # no subsequent held key appears to be accepted during the pause.
            self.held_directions = ()
            self.final_pause_remaining -= dt
            if self.final_pause_remaining <= 1e-9:
                self.final_pause_remaining = 0.0
                self._complete_for_hits(final_pause=True)
            return

        # Consume an unusually long frame without changing the result: a
        # return can reach centre and begin the next outbound path in that
        # same frame, while a target escaping still ends immediately.
        remaining = dt
        while remaining > 1e-9 and not self.done:
            speed = self.current_speed
            if self.phase == "outbound":
                distance_to_edge = max(0.0, self._distance_to_edge())
                travel = speed * remaining
                if travel > distance_to_edge:
                    self._move_target(self.outbound_direction, travel)
                    self._complete_for_hits(escaped=True, timeout=True, direction=self.outbound_direction)
                    return
                self._move_target(self.outbound_direction, travel)
                return

            distance_to_center = math.hypot(self.target_x - self.CENTER[0], self.target_y - self.CENTER[1])
            if distance_to_center <= 1e-9:
                self._start_next_outbound()
                continue
            travel = speed * remaining
            if travel < distance_to_center:
                self._move_target(self.outbound_direction, -travel)
                return
            self._move_target(self.outbound_direction, -distance_to_center)
            remaining -= distance_to_center / speed
            self._start_next_outbound()

    def handle_action(self, action: str) -> bool:
        if self.input_locked or action not in self.DIRECTION_ORDER or not self.allows_action(action):
            return False
        # Consume repeated KEYDOWN events while the same physical direction
        # remains held.  A release is observed through the held-input snapshot.
        if action in self._latched_directions:
            return True
        self._latched_directions.add(action)
        self.attempted_directions = (action,)
        self.strike_flash_remaining = self.strike_flash_duration
        # The return trip is visible feedback for a landed strike, not a
        # second scoring pass through the same region.
        if self.phase != "outbound":
            return True
        if self.target_overlaps_region(action):
            self.hits += 1
            if self.hits >= self.required_hits:
                self.final_pause_remaining = self.final_critical_pause
                # Keep the final region visibly struck throughout the
                # configurable critical display, even if normal strike
                # flashes are deliberately shorter.
                self.strike_flash_remaining = max(self.strike_flash_remaining, self.final_critical_pause)
                if self.final_pause_remaining <= 0:
                    self.final_pause_remaining = 0.0
                    self._complete_for_hits(final_pause=True)
            else:
                self.phase = "returning"
            return True
        return True

    def presentation(self) -> dict[str, Any]:
        data = super().presentation()
        data.update({
            "target": (self.target_x, self.target_y),
            "target_radius": self.target_radius,
            "target_tier": self.provisional_tier,
            "regions": [{
                "direction": direction,
                "rect": self.region_rect(direction),
                "held": direction in self.held_directions,
                "flashing": direction in self.attempted_directions and self.strike_flash_remaining > 0,
            } for direction in self.DIRECTION_ORDER],
            "outbound_direction": self.outbound_direction,
            "phase": self.phase,
            "hits": self.hits,
            "required_hits": self.required_hits,
            "strong_required_hits": self.strong_required_hits,
            "current_speed": self.current_speed,
            "maximum_speed": self.maximum_speed,
            "strike_flash_remaining": self.strike_flash_remaining,
            "final_pause_remaining": self.final_pause_remaining,
            "input_locked": self.input_locked,
        })
        return data


class QuickSlashQTE(AttackQTE):
    """Alternating slashes through a staggered sequence of falling blocks."""

    qte_type = "rapid_slash"
    tutorial_instruction = "Slash left and right alternately through the falling blocks"
    HIT_PITCH_STEP = 1.059463
    MAX_PENALTIES = 5

    def __init__(self, duration: float = 5.0, block_count: int = 10,
                 block_fall_speed: float = 1.05, block_height: float = 0.14,
                 block_width: float = 0.16, block_spacing: float | list[float] = (1.0, 2.0),
                 block_horizontal_offset: float = 0.16,
                 half_separation_speed: float = 0.09, cut_gravity: float = 1.60,
                 cut_horizontal_speed: float = 0.12, slash_animation_duration: float = 0.05,
                 slash_region_height: float = 0.024, slash_region_vertical_position: float = 0.72,
                 minimum_half_height: float = 0.03,
                 strong_threshold: int = 7, hit_sound_pitch_progression: bool = True,
                 hit_sound_pitch_progression_enabled: bool | None = None,
                 rng: random.Random | None = None, **kwargs: Any):
        super().__init__(duration, **kwargs)
        self.block_count = max(1, int(block_count))
        self.block_fall_speed = max(0.001, float(block_fall_speed))
        self.block_height = min(1.0, max(0.001, float(block_height)))
        self.block_width = min(1.0, max(0.001, float(block_width)))
        spacing_values = block_spacing if isinstance(block_spacing, (list, tuple)) else (block_spacing,)
        if not spacing_values:
            raise ValueError("block_spacing must contain at least one multiplier")
        self.block_spacing = tuple(max(0.0, float(value)) for value in spacing_values)
        self.block_horizontal_offset = max(0.0, float(block_horizontal_offset))
        self.half_separation_speed = max(0.0, float(half_separation_speed))
        self.cut_gravity = max(0.0, float(cut_gravity))
        self.cut_horizontal_speed = max(0.0, float(cut_horizontal_speed))
        self.slash_animation_duration = max(0.0, float(slash_animation_duration))
        self.slash_region_height = min(1.0, max(0.001, float(slash_region_height)))
        half_region = self.slash_region_height / 2
        self.slash_region_vertical_position = min(1.0 - half_region, max(half_region, float(slash_region_vertical_position)))
        self.minimum_half_height = min(self.block_height / 2, max(0.001, float(minimum_half_height)))
        self.strong_threshold = min(self.block_count, max(1, int(strong_threshold)))
        if hit_sound_pitch_progression_enabled is not None:
            hit_sound_pitch_progression = hit_sound_pitch_progression_enabled
        self.hit_sound_pitch_progression = bool(hit_sound_pitch_progression)
        self.rng = rng or random.Random()

        self.blocks = self._make_blocks()
        self.hits = 0
        self.penalties = 0
        self.next_direction: str | None = None
        self.last_slash_direction: str | None = None
        self.slash_remaining = 0.0
        self.last_hit_pitch = 1.0
        self.last_slash_pitch = 1.0
        self.last_slash_hit = False

    def _make_blocks(self) -> list[dict[str, float | int | str | bool]]:
        blocks: list[dict[str, float | int | str | bool]] = []
        top = -self.block_height - 0.02
        for index in range(self.block_count):
            x = clamp(.5 + self.rng.uniform(-self.block_horizontal_offset, self.block_horizontal_offset),
                      self.block_width / 2, 1 - self.block_width / 2)
            blocks.append({"index": index, "top": top, "x": x, "cut": False,
                           "direction": "", "cut_offset": self.block_height / 2, "separation": 0.0,
                           "vertical_velocity": self.block_fall_speed, "horizontal_velocity": 0.0})
            spacing = self.rng.choice(self.block_spacing) * self.block_height
            top -= self.block_height + spacing
        return blocks

    @property
    def slash_region_bounds(self) -> tuple[float, float]:
        half = self.slash_region_height / 2
        return self.slash_region_vertical_position - half, self.slash_region_vertical_position + half

    @property
    def slash_progress(self) -> float:
        if self.slash_remaining <= 0 or self.slash_animation_duration <= 0:
            return 1.0
        return clamp(1.0 - self.slash_remaining / self.slash_animation_duration)

    @property
    def performance_tier(self) -> str:
        if self.hits >= self.block_count and self.penalties == 0:
            return "critical"
        if self.hits >= self.strong_threshold:
            return "strong"
        if self.hits > 0:
            return "weak"
        return "miss"

    def _cut_offset_for_block(self, block: dict[str, float | int | str | bool]) -> float | None:
        """Return an in-region cut height that leaves both pieces readable."""
        region_top, region_bottom = self.slash_region_bounds
        block_top = float(block["top"])
        block_bottom = block_top + self.block_height
        intersection_top = max(block_top, region_top)
        intersection_bottom = min(block_bottom, region_bottom)
        if intersection_top > intersection_bottom:
            return None
        safe_top = block_top + self.minimum_half_height
        safe_bottom = block_bottom - self.minimum_half_height
        cut_top = max(intersection_top, safe_top)
        cut_bottom = min(intersection_bottom, safe_bottom)
        if cut_top > cut_bottom:
            return None
        return clamp((intersection_top + intersection_bottom) / 2, cut_top, cut_bottom) - block_top

    def _overlapping_uncut_blocks(self) -> list[dict[str, float | int | str | bool]]:
        return [block for block in self.blocks
                if not bool(block["cut"]) and self._cut_offset_for_block(block) is not None]

    def _metrics(self, **extra: float | int | str | bool) -> dict[str, float | int | str | bool]:
        metrics: dict[str, float | int | str | bool] = {
                "hits": self.hits, "block_count": self.block_count,
                "strong_threshold": self.strong_threshold,
                "hit_ratio": self.hits / self.block_count,
                "pitch_progression": self.hit_sound_pitch_progression,
                "penalties": self.penalties,
                "max_penalties": self.MAX_PENALTIES,
        }
        metrics.update(extra)
        return metrics

    def update(self, dt: float, movement: BattleInput = BattleInput()) -> None:
        del movement
        if self.done:
            return
        dt = max(0.0, dt)
        self.elapsed += dt
        self.slash_remaining = max(0.0, self.slash_remaining - dt)
        for block in self.blocks:
            if bool(block["cut"]):
                # A successful slash arrests the block's downward motion for
                # an instant. Gravity then pulls its two visible halves down
                # while the input direction carries them sideways.
                block["vertical_velocity"] = float(block["vertical_velocity"]) + self.cut_gravity * dt
                block["top"] = float(block["top"]) + float(block["vertical_velocity"]) * dt
                block["x"] = float(block["x"]) + float(block["horizontal_velocity"]) * dt
                block["separation"] = float(block["separation"]) + self.half_separation_speed * dt
            else:
                block["top"] = float(block["top"]) + self.block_fall_speed * dt
        if all(float(block["top"]) >= 1.0 for block in self.blocks):
            self.complete_tier(self.performance_tier, self._metrics(exited=True, timeout=True))

    def handle_action(self, action: str) -> bool:
        if self.done or action not in {"LEFT", "RIGHT"} or not self.allows_action(action):
            return False
        if self.next_direction is not None and action != self.next_direction:
            return False
        self.last_slash_direction = action
        self.next_direction = "RIGHT" if action == "LEFT" else "LEFT"
        self.slash_remaining = self.slash_animation_duration
        self.last_slash_pitch = self.HIT_PITCH_STEP ** self.hits if self.hit_sound_pitch_progression else 1.0
        self.last_slash_hit = False
        candidates = self._overlapping_uncut_blocks()
        if not candidates:
            self.penalties = min(self.MAX_PENALTIES, self.penalties + 1)
            if self.penalties >= self.MAX_PENALTIES:
                self.complete_tier(self.performance_tier, self._metrics(penalty_limit=True))
            return True
        block = min(candidates, key=lambda item: abs(float(item["top"]) + self.block_height / 2
                                                       - self.slash_region_vertical_position))
        cut_offset = self._cut_offset_for_block(block)
        assert cut_offset is not None
        block["cut"] = True
        block["direction"] = action
        block["cut_offset"] = cut_offset
        self.last_slash_hit = True
        block["vertical_velocity"] = 0.0
        block["horizontal_velocity"] = -self.cut_horizontal_speed if action == "LEFT" else self.cut_horizontal_speed
        self.last_hit_pitch = self.last_slash_pitch
        self.hits += 1
        return True

    def presentation(self) -> dict[str, Any]:
        data = super().presentation()
        data.update({
            "blocks": [dict(block) for block in self.blocks],
            "block_height": self.block_height,
            "block_width": self.block_width,
            "slash_region": (self.slash_region_vertical_position, self.slash_region_height),
            "available_directions": ("LEFT", "RIGHT") if self.next_direction is None else (self.next_direction,),
            "slash_direction": self.last_slash_direction,
            "slash_active": self.slash_remaining > 0,
            "slash_progress": self.slash_progress,
            "hits": self.hits,
            "block_count": self.block_count,
            "strong_threshold": self.strong_threshold,
            "penalty_markers": [index < self.penalties for index in range(self.MAX_PENALTIES)],
            "penalties": self.penalties,
            "performance_tier": self.performance_tier,
            "last_hit_pitch": self.last_hit_pitch,
            "last_slash_pitch": self.last_slash_pitch,
            "last_slash_hit": self.last_slash_hit,
            "pitch_progression": self.hit_sound_pitch_progression,
        })
        return data


class RhythmComboQTE(AttackQTE):
    qte_type = "rhythm_combo"
    tutorial_instruction = "Press A / Enter as each incoming bar enters the striking box"
    MAX_PENALTIES = 3
    STRIKING_X = 0.16
    TIMING_BAR_WIDTH = 0.02
    RHYTHM_BAR_WIDTH = 0.02
    BLOCK_BAR_COUNTS = (1, 1, 2, 2, 3)
    LEAD_IN_FRACTION = 0.30
    GROUP_TIME_FRACTION = 0.50
    ACTIVATION_FLASH_DURATION = 0.16
    HIT_PITCH_STEP = 1.059463

    def __init__(self, duration: float = 4.8, beats: list[float] | None = None,
                 beat_count: int = 4, tolerance: float = 0.14,
                 approach_speed: float = 0.30, fade_duration: float = 0.25,
                 rng: random.Random | None = None, **kwargs: Any):
        super().__init__(duration, **kwargs)
        self.tolerance = max(0.01, tolerance)
        self.approach_speed = max(0.01, approach_speed)
        self.fade_duration = max(0.01, fade_duration)
        del beat_count  # Retained for backward-compatible configuration parsing.
        if beats is not None:
            self.block_counts: list[int] = []
            self.beats = sorted(beats)
        else:
            self.block_counts = list(self.BLOCK_BAR_COUNTS)
            (rng or random.Random()).shuffle(self.block_counts)
            self.lead_in = duration * self.LEAD_IN_FRACTION
            self.beats = self._beats_for_blocks(duration, self.block_counts,
                                                self.TIMING_BAR_WIDTH / self.approach_speed,
                                                self.lead_in, self.GROUP_TIME_FRACTION)
        if beats is not None:
            self.lead_in = 0.0
        # Explicitly authored beat lists predate block groups, so retain their
        # one-bar-at-a-time behavior. Generated patterns retain the group that
        # produced each beat, which lets presentation effects stay local to a
        # block.
        bar_blocks = (
            [block_index for block_index, count in enumerate(self.block_counts) for _ in range(count)]
            if self.block_counts else list(range(len(self.beats)))
        )
        self.bars = [
            {"beat": beat, "block": bar_blocks[index], "state": "approaching",
             "fade": 0.0, "position": None}
            for index, beat in enumerate(self.beats)
        ]
        self._block_hits = [0] * max(1, len(self.block_counts) or len(self.beats))
        self.penalties = 0
        self.cleared = 0
        self._finalized = False
        self.timed_out = False
        self.activation_flash = 0.0
        self.last_activation_hit: bool | None = None
        self.last_hit_pitch = 1.0

    @staticmethod
    def _beats_for_blocks(duration: float, block_counts: list[int], bar_duration: float,
                          lead_in: float, group_time_fraction: float) -> list[float]:
        """Center equally spaced bar groups inside equally long time blocks."""
        block_duration = (duration - lead_in) / len(block_counts)
        beats: list[float] = []
        for index, count in enumerate(block_counts):
            block_start = lead_in + index * block_duration
            block_center = block_start + block_duration / 2
            if count == 1:
                beats.append(block_center)
                continue
            # Keep the group centered while widening its timing span. Account
            # for the visible bar widths so the outer bar edges stay within
            # the selected portion of the block. Double bars use the same
            # adjacent-bar spacing as triple bars, rather than stretching to
            # the full group width.
            group_span = max(0.0, block_duration * group_time_fraction - bar_duration)
            spacing = group_span / 2 if count == 2 else group_span / (count - 1)
            first = block_center - spacing * (count - 1) / 2
            beats.extend(first + spacing * bar_index for bar_index in range(count))
        return beats

    def _resolve(self) -> None:
        if self._finalized:
            return
        self._finalized = True
        missed = sum(bar["state"] == "missed" for bar in self.bars)
        hit_ratio = self.cleared / max(1, len(self.bars))
        if self.penalties >= self.MAX_PENALTIES or self.cleared == 0:
            tier = "miss"
        elif self.cleared == len(self.bars) and self.penalties == 0:
            tier = "critical"
        elif hit_ratio < self.thresholds["strong"]:
            # One or more hits are still worthwhile, but do not meet the
            # authored strong-hit threshold.
            tier = "weak"
        else:
            tier = "strong"
        self.complete_tier(tier, {"cleared": self.cleared, "penalties": self.penalties,
                                  "missed": missed, "hit_ratio": hit_ratio,
                                  "timeout": self.timed_out})

    def _mark_missed(self, bar: dict[str, Any]) -> None:
        if bar["state"] != "approaching":
            return
        bar["state"] = "missed"
        bar["fade"] = 0.0
        bar["position"] = self._bar_position(bar["beat"])

    def _add_penalty(self) -> None:
        """Spend one of the three visible rhythm-combo mistake markers."""
        self.penalties = min(self.MAX_PENALTIES, self.penalties + 1)
        if self.penalties >= self.MAX_PENALTIES:
            self._resolve()

    def _bar_position(self, beat: float) -> float:
        return self.STRIKING_X + (beat - self.elapsed) * self.approach_speed

    @property
    def _hit_window(self) -> float:
        """Allow an action only while the two visible bars overlap."""
        overlap_distance = (self.TIMING_BAR_WIDTH + self.RHYTHM_BAR_WIDTH) / 2
        return min(self.tolerance, overlap_distance / self.approach_speed)

    def update(self, dt: float, movement: BattleInput = BattleInput()) -> None:
        del movement
        if self.done:
            return
        dt = max(0.0, dt)
        self.elapsed += dt
        self.activation_flash = max(0.0, self.activation_flash - dt)
        for bar in self.bars:
            if self.done:
                break
            if bar["state"] == "approaching" and self.elapsed > bar["beat"] + self._hit_window:
                self._mark_missed(bar)
            if bar["state"] in {"cleared", "missed"}:
                bar["fade"] += dt
        if self.done:
            return
        unresolved = any(bar["state"] == "approaching" for bar in self.bars)
        fading = any(bar["state"] in {"cleared", "missed"} and bar["fade"] < self.fade_duration for bar in self.bars)
        if not unresolved and not fading:
            self._resolve()
        elif self.elapsed >= self.duration:
            self.timed_out = True
            for bar in self.bars:
                self._mark_missed(bar)

    def handle_action(self, action: str) -> bool:
        if self.done or action != "SELECT" or not self.allows_action(action):
            return False
        candidates = [bar for bar in self.bars if bar["state"] == "approaching" and abs(self.elapsed - bar["beat"]) <= self._hit_window]
        if candidates:
            self.last_activation_hit = True
            self.activation_flash = self.ACTIVATION_FLASH_DURATION
            bar = min(candidates, key=lambda candidate: abs(self.elapsed - candidate["beat"]))
            bar["state"] = "cleared"
            bar["fade"] = 0.0
            bar["position"] = self._bar_position(bar["beat"])
            prior_hits = self._block_hits[bar["block"]]
            self.last_hit_pitch = self.HIT_PITCH_STEP ** prior_hits
            self._block_hits[bar["block"]] += 1
            self.cleared += 1
        else:
            self.last_activation_hit = False
            self.activation_flash = self.ACTIVATION_FLASH_DURATION
            self._add_penalty()
        return True

    def presentation(self) -> dict[str, Any]:
        data = super().presentation()
        visible = []
        for bar in self.bars:
            position = self._bar_position(bar["beat"]) if bar["position"] is None else bar["position"]
            hit_progress = clamp(bar["fade"] / self.fade_duration)
            visible.append({"position": position, "state": bar["state"], "fade": bar["fade"],
                            # Every timing bar shares one horizontal track.
                            "lane": 0, "y": 0.5,
                            # A successful bar stays at its hit position while
                            # its vertical hit flourish grows.
                            "vertical_scale": 1.0 + (hit_progress if bar["state"] == "cleared" else 0.0)})
        data.update({"bars": visible, "striking_x": self.STRIKING_X,
                     "timing_bar_width": self.TIMING_BAR_WIDTH, "rhythm_bar_width": self.RHYTHM_BAR_WIDTH,
                     "fade_duration": self.fade_duration,
                     "cleared": self.cleared, "penalties": self.penalties,
                     "block_counts": self.block_counts, "lead_in": self.lead_in,
                     "activation_flash": self.activation_flash, "last_activation_hit": self.last_activation_hit,
                     "last_hit_pitch": self.last_hit_pitch,
                     "penalty_markers": [index < self.penalties for index in range(self.MAX_PENALTIES)]})
        return data


class MovingWeakPointQTE(AttackQTE):
    qte_type = "moving_weak_point"
    tutorial_instruction = "Aim with Left / Right, then fire once with A / Enter"

    def __init__(self, duration: float = 1.65, target_x: float = 0.0, target_y: float = 0.28,
                 target_radius: float = 0.11, speed: float = 0.80, target_speed: float = 0.20,
                 aim_angle: float = -90.0, aim_speed: float = 95.0,
                 arrow_speed: float = 1.80, launch_x: float = 0.50, launch_y: float = 0.86,
                 impact_hold: float = 0.25, reticle_x: float = 0.15, reticle_y: float = 0.5,
                 critical_radius: float | None = None, strong_radius: float | None = None,
                 **kwargs: Any):
        super().__init__(duration, **kwargs)
        del target_x, target_speed, reticle_x, reticle_y
        self.base_target_y = clamp(target_y)
        self.target_radius = max(0.01, target_radius)
        # ``speed`` is a multiplier of the original full-field crossing rate.
        # The default deliberately keeps the familiar left-to-right motion but
        # gives the player a little more time to line up a shot.
        self.target_speed = max(0.01, speed)
        # The score bands are individually authorable.  Retain the original
        # proportional defaults for older move files, while allowing adaptive
        # levels to make critical precision the primary pressure point.
        self.critical_radius = max(0.001, min(self.target_radius,
                                               self.target_radius * 0.12 if critical_radius is None else critical_radius))
        self.strong_radius = max(self.critical_radius, min(self.target_radius,
                                                            self.target_radius * 0.42 if strong_radius is None else strong_radius))
        self.aim_angle = aim_angle
        self.aim_speed = max(1.0, aim_speed)
        self.arrow_speed = max(0.01, arrow_speed)
        self.launch_x, self.launch_y = clamp(launch_x), clamp(launch_y)
        self.projectile_x, self.projectile_y = self.launch_x, self.launch_y
        self.fired = False
        self.passed_target_at: float | None = None
        self.impact_hold = max(0.0, impact_hold)
        self.impact_remaining: float | None = None
        self._impact_score = 0.0
        self._impact_metrics: dict[str, float | int | str | bool] = {}

    @property
    def target_x(self) -> float:
        # The target steadily crosses from left to right.  It moves slightly
        # slower than a complete field crossing during the QTE duration.
        progress = clamp(self.elapsed * self.target_speed / self.duration)
        return -self.target_radius + (1 + self.target_radius * 2) * progress

    @property
    def target_y(self) -> float:
        return self.base_target_y

    @property
    def aim_vector(self) -> tuple[float, float]:
        radians = math.radians(self.aim_angle)
        return math.cos(radians), math.sin(radians)

    def _impact(self, distance: float, projectile_x: float, projectile_y: float) -> None:
        # Preserve the arrow-tip contact point.  Moving it to the target
        # center makes a successful arrow visibly snap after collision.
        self.projectile_x, self.projectile_y = projectile_x, projectile_y
        self.impact_remaining = self.impact_hold
        self._impact_score = score_from_error(distance, self.critical_radius, self.strong_radius, self.target_radius, self.thresholds)
        self._impact_metrics = {"distance": distance, "hit": True, "aim_angle": self.aim_angle,
                                "target_x": self.target_x, "target_y": self.target_y}

    def update(self, dt: float, movement: BattleInput = BattleInput()) -> None:
        if self.done:
            return
        dt = max(0.0, dt)
        if self.impact_remaining is not None:
            self.impact_remaining -= dt
            if self.impact_remaining <= 0:
                self.complete(self._impact_score, self._impact_metrics)
            return
        self.elapsed += dt
        if not self.fired:
            # -90 is straight up in screen coordinates.  The player can tilt
            # the centered launcher to either side of that neutral position.
            self.aim_angle = max(-172.0, min(-8.0, self.aim_angle + movement.move_x * self.aim_speed * dt))
        else:
            previous_x, previous_y = self.projectile_x, self.projectile_y
            direction_x, direction_y = self.aim_vector
            self.projectile_x += direction_x * self.arrow_speed * dt
            self.projectile_y += direction_y * self.arrow_speed * dt
            if self.passed_target_at is None and previous_y > self.target_y >= self.projectile_y:
                # The target is a horizontal depth plane.  Only when the
                # arrow tip reaches that plane can its horizontal width count
                # as a hit; passing through its lower visual area does not.
                crossing = (previous_y - self.target_y) / (previous_y - self.projectile_y)
                impact_x = previous_x + (self.projectile_x - previous_x) * crossing
                horizontal_error = abs(impact_x - self.target_x)
                if horizontal_error <= self.target_radius:
                    self._impact(horizontal_error, impact_x, self.target_y)
                    return
                self.passed_target_at = self.elapsed
            if self.passed_target_at is not None:
                if self.elapsed - self.passed_target_at >= 0.25:
                    self.complete(0.0, {"missed_arrow": True, "passed_target": True,
                                        "aim_angle": self.aim_angle, "timeout": False})
                    return
            elif self.projectile_x > 1.08 or self.projectile_y < -0.08 or self.projectile_y > 1.08:
                self.complete(0.0, {"missed_arrow": True, "aim_angle": self.aim_angle, "timeout": False})
                return
        if self.elapsed >= self.duration and self.passed_target_at is None:
            self.complete(0.0, {"timeout": True, "target_escaped": True, "fired": self.fired})

    def handle_action(self, action: str) -> bool:
        if self.done or action != "SELECT" or not self.allows_action(action):
            return False
        if self.fired or self.impact_remaining is not None:
            return False
        self.fired = True
        return True

    def presentation(self) -> dict[str, Any]:
        data = super().presentation()
        data.update({"target": (self.target_x, self.target_y), "critical_radius": self.critical_radius,
                     "strong_radius": self.strong_radius, "weak_radius": self.target_radius,
                     "launch": (self.launch_x, self.launch_y), "projectile": (self.projectile_x, self.projectile_y),
                     "aim_angle": self.aim_angle, "fired": self.fired, "impact": self.impact_remaining is not None,
                     "impact_distance": self._impact_metrics.get("distance")})
        return data


class StabilityQTE(AttackQTE):
    qte_type = "stability"
    tutorial_instruction = "Keep the marker centered with Left / Right"

    def __init__(self, duration: float = 2.5, force: float = 0.55, correction_speed: float = 1.35,
                 center_width: float = 0.12, **kwargs: Any):
        super().__init__(duration, **kwargs)
        self.force, self.correction_speed, self.center_width = force, correction_speed, center_width
        self.position = 0.0
        self.velocity = 0.0
        self.stability_total = 0.0
        self.optimal_time = 0.0
        self.participated = False

    def update(self, dt: float, movement: BattleInput = BattleInput()) -> None:
        if self.done:
            return
        dt = max(0.0, dt)
        self.participated = self.participated or abs(movement.move_x) > 1e-6
        # Deterministic mixed-frequency force reads as recoil without RNG and
        # therefore gives identical results at any render rate.
        push = (math.sin(self.elapsed * 7.1) + math.sin(self.elapsed * 3.7 + 0.8) * 0.55) * self.force
        self.velocity += (push + movement.move_x * self.correction_speed) * dt
        self.velocity *= max(0.0, 1 - 2.2 * dt)
        self.position = clamp(self.position + self.velocity * dt, -1.0, 1.0)
        stability = 1 - abs(self.position)
        self.stability_total += stability * dt
        if abs(self.position) <= self.center_width:
            self.optimal_time += dt
        self.elapsed += dt
        if self.elapsed >= self.duration:
            average = self.stability_total / max(1e-9, self.elapsed)
            optimal = self.optimal_time / max(1e-9, self.elapsed)
            final = 1 - abs(self.position)
            self.complete((average * 0.55 + optimal * 0.25 + final * 0.20) if self.participated else 0.0,
                          {"average_stability": average, "optimal_time_ratio": optimal, "final_position": self.position,
                           "participated": self.participated, "timeout": not self.participated})

    def presentation(self) -> dict[str, Any]:
        data = super().presentation()
        data.update({"position": self.position, "center_width": self.center_width,
                     "average_stability": self.stability_total / max(1e-9, self.elapsed)})
        return data


QTE_REGISTRY: dict[str, Callable[..., AttackQTE]] = {
    "precision_bar": PrecisionBarQTE,
    "charge_release": ChargeReleaseQTE,
    "shrinking_ring": ShrinkingRingQTE,
    "rotating_strike": RotatingStrikeQTE,
    "directional_combo": DirectionalComboQTE,
    "rapid_slash": QuickSlashQTE,
    "rhythm_combo": RhythmComboQTE,
    "moving_weak_point": MovingWeakPointQTE,
    "stability": StabilityQTE,
}


# The registry is the source of truth for coverage.  Keeping this derived
# table beside it means a newly registered constructor cannot silently
# disappear from the Designer's QTE editor.
QTE_EDITOR_SPECS: dict[str, QTEEditorSpec] = {
    name: _build_qte_editor_spec(name, qte_class)
    for name, qte_class in QTE_REGISTRY.items()
}


def qte_editor_spec(type_name: str) -> QTEEditorSpec | None:
    """Return metadata for a canonical or legacy QTE identifier."""

    return QTE_EDITOR_SPECS.get(canonical_qte_type(str(type_name)))


def qte_editor_specs() -> tuple[QTEEditorSpec, ...]:
    """Return every registered QTE's authoring metadata in registry order."""

    return tuple(QTE_EDITOR_SPECS.values())


def minimal_qte_payload(type_name: str) -> dict[str, Any] | None:
    """Return the smallest valid modern QTE envelope for a known type."""

    canonical = canonical_qte_type(str(type_name))
    if canonical not in QTE_REGISTRY:
        return None
    return {"type": canonical}


def registered_qte_types() -> tuple[str, ...]:
    return tuple(QTE_REGISTRY)


# Descriptive aliases make the registry boundary discoverable to tools that
# prefer registry terminology over the plural constant name.
QTE_EDITOR_REGISTRY = QTE_EDITOR_SPECS
get_qte_editor_spec = qte_editor_spec


def _difficulty_values(difficulty: str) -> dict[str, float]:
    return dict(DIFFICULTY_MODIFIERS.get(difficulty, DIFFICULTY_MODIFIERS["normal"]))


def _source_qte_config(move: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Adapt modern ``qte`` data and the older ``pattern_config`` schema."""
    if isinstance(move.get("qte"), dict):
        raw = dict(move["qte"])
        qte_type = canonical_qte_type(str(raw.pop("type", "")))
        parameters = dict(raw.pop("parameters", {}))
        # Direct fields are deliberately accepted for concise YAML examples.
        parameters.update({key: value for key, value in raw.items()
                           if key not in {"duration", "difficulty", "thresholds", "damage_multipliers", "label", "sound", "animation", "allowed_inputs"}})
        common = {key: raw[key] for key in ("duration", "difficulty", "thresholds", "damage_multipliers", "label", "sound", "animation", "allowed_inputs") if key in raw}
        return qte_type, parameters, common
    qte_type = canonical_qte_type(str(move.get("pattern", "precision_bar")))
    return qte_type, dict(move.get("pattern_config", {})), {
        "difficulty": move.get("difficulty", "normal"),
        "thresholds": move.get("thresholds", {}),
        "damage_multipliers": move.get("damage_multipliers", {}),
        "label": move.get("qte_label", ""), "sound": move.get("qte_sound"), "animation": move.get("qte_animation"),
    }


def create_attack_qte(move: dict[str, Any], rng: random.Random | None = None) -> AttackQTE:
    """Create an attack QTE from a validated move definition and registry."""
    qte_type, parameters, common = _source_qte_config(move)
    modern_qte = isinstance(move.get("qte"), dict)
    try:
        qte_class = QTE_REGISTRY[qte_type]
    except KeyError as exc:
        raise ValueError(f"unknown attack QTE type {qte_type!r}") from exc
    rng = rng or random.Random()
    difficulty = _difficulty_values(str(common.get("difficulty", "normal")))
    windows = difficulty["window_scale"]
    speed_scale = difficulty["speed_scale"]
    for key in ("critical_window", "strong_window", "weak_window", "critical_tolerance", "strong_tolerance", "weak_tolerance", "target_radius", "center_width", "tolerance", "slash_region_height"):
        if key in parameters:
            parameters[key] = float(parameters[key]) * windows
    for key in ("speed_multiplier", "target_speed", "block_fall_speed", "rotations", "movement_speed", "aim_speed", "arrow_speed", "approach_speed", "initial_speed", "speed_increase"):
        if key in parameters:
            parameters[key] = float(parameters[key]) * speed_scale
    if "force" in parameters:
        parameters["force"] = float(parameters["force"]) * difficulty["force_scale"]
    if qte_type == "precision_bar":
        # Original battle files called the innermost and outer timing bands
        # ``perfect_window`` and ``good_window``.
        if modern_qte:
            if "perfect_window" in parameters:
                parameters.setdefault("critical_window", parameters.pop("perfect_window"))
            if "good_window" in parameters:
                good_window = parameters.pop("good_window")
                parameters.setdefault("weak_window", good_window)
                parameters.setdefault("strong_window", float(good_window) * 0.25)
        else:
            # The previous timing-bar factory intentionally used its fixed
            # nested bands even when these descriptive legacy fields existed.
            parameters.pop("perfect_window", None)
            parameters.pop("good_window", None)
        target_range = parameters.pop("target_position_range", (0.42, 0.75))
        if "target_position" not in parameters:
            parameters["target_position"] = rng.uniform(float(target_range[0]), float(target_range[1]))
    if qte_type == "charge_release":
        # Arc placement is sampled once by the QTE, using the battle RNG so
        # a seeded battle remains replayable.
        parameters["rng"] = rng
    if qte_type == "directional_combo":
        # Direction selection happens each time the target returns to centre;
        # pass the seeded battle RNG through so runs remain reproducible.
        parameters["rng"] = rng
    if qte_type == "rapid_slash":
        # Randomized block spacing and offsets belong to the seeded battle
        # RNG, keeping a replayable attack sequence for a given battle seed.
        parameters["rng"] = rng
    if qte_type == "rhythm_combo" and "stages" in parameters:
        # Old timing_sequence data listed one timing bar per stage.  A rhythm
        # beat at each stage midpoint is the closest modern representation.
        stages = parameters.pop("stages")
        durations = [float(stage.get("duration", 1.0)) for stage in stages] if stages else [1.0]
        total = sum(durations)
        elapsed = 0.0
        parameters.setdefault("duration", total)
        parameters.setdefault("beats", [elapsed + duration / 2 for elapsed, duration in _running_durations(durations)])
    if qte_type == "rhythm_combo" and not parameters.get("beats"):
        # Use the battle RNG so the five-block arrangement is reproducible
        # for seeded battles and still changes between regular attacks.
        parameters["rng"] = rng
    if qte_type == "rotating_strike" and "target_angle" not in parameters:
        target_range = parameters.pop("target_angle_range", (0, 360))
        parameters["target_angle"] = rng.uniform(float(target_range[0]), float(target_range[1]))
    if qte_type == "moving_weak_point":
        # Vary depth (the target's distance down from the top) and the
        # launcher's horizontal origin once per attack.  The supplied battle
        # RNG keeps this variation reproducible for a seeded battle.
        target_y = float(parameters.get("target_y", 0.28))
        target_y_variance = float(parameters.pop("target_y_variance", 0.07))
        launch_x = float(parameters.get("launch_x", 0.50))
        launch_x_variance = float(parameters.pop("launch_x_variance", 0.13))
        parameters["target_y"] = clamp(target_y + rng.uniform(-target_y_variance, target_y_variance))
        parameters["launch_x"] = clamp(launch_x + rng.uniform(-launch_x_variance, launch_x_variance))
    if qte_type == "shrinking_ring":
        # Place the bullseye near, but not always exactly at, the center of
        # the field.  The initial ring is then sampled far enough from it to
        # make the steering portion of the QTE meaningful.  Authored exact
        # coordinates remain useful for bespoke encounters and tests.
        target_x = float(parameters.get("target_x", 0.50))
        target_y = float(parameters.get("target_y", 0.50))
        target_x_variance = float(parameters.pop("target_x_variance", 0.16))
        target_y_variance = float(parameters.pop("target_y_variance", 0.13))
        ring_min_distance = float(parameters.pop("ring_min_distance", 0.45))
        if "target_x" not in parameters:
            parameters["target_x"] = clamp(target_x + rng.uniform(-target_x_variance, target_x_variance))
        if "target_y" not in parameters:
            parameters["target_y"] = clamp(target_y + rng.uniform(-target_y_variance, target_y_variance))

        if "ring_x" not in parameters and "ring_y" not in parameters:
            target_x, target_y = parameters["target_x"], parameters["target_y"]
            for _ in range(32):
                ring_x, ring_y = rng.uniform(0.0, 1.0), rng.uniform(0.0, 1.0)
                if math.hypot(ring_x - target_x, ring_y - target_y) >= ring_min_distance:
                    parameters["ring_x"], parameters["ring_y"] = ring_x, ring_y
                    break
            else:
                # The configured target ranges leave a valid point, but use
                # the furthest corner as a deterministic safe fallback.
                parameters["ring_x"], parameters["ring_y"] = max(
                    ((0.0, 0.0), (0.0, 1.0), (1.0, 0.0), (1.0, 1.0)),
                    key=lambda point: math.hypot(point[0] - target_x, point[1] - target_y),
                )
    duration = parameters.pop("duration", common.get("duration", None))
    if duration is not None:
        parameters["duration"] = duration
    thresholds = common.get("thresholds") or {}
    multipliers = common.get("damage_multipliers") or {}
    parameters.update({"thresholds": thresholds, "multipliers": multipliers, "label": common.get("label", ""),
                       "sound": common.get("sound"), "animation": common.get("animation"),
                       "allowed_inputs": common.get("allowed_inputs")})
    return qte_class(**parameters)


def _running_durations(durations: list[float]) -> list[tuple[float, float]]:
    elapsed = 0.0
    result: list[tuple[float, float]] = []
    for duration in durations:
        result.append((elapsed, duration))
        elapsed += duration
    return result


# Compatibility names for integrations which imported the original patterns.
TimingBarSequence = PrecisionBarQTE
PositionTargetSequence = MovingWeakPointQTE
AttackSequence = AttackQTE
TimingSequence = RhythmComboQTE
