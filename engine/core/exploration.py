"""Pure, opt-in exploration rules for data-driven scenes.

The pygame frontend owns timing, input, and drawing.  This module owns the
author-facing scene vocabulary: structured flag conditions, resolved object
visibility/look targets, and a small registry-backed event runner.  Keeping
those pieces independent of pygame makes scene progression testable and keeps
individual story scenes out of Python control flow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence

from engine.core.game_state import GameState
from engine.errors import ConditionError, StoryValidationError
from engine.story_core.conditions import (
    ConditionError as StoryCoreConditionError,
    evaluate_structured_condition as _evaluate_structured_condition,
    validate_structured_condition as _validate_structured_condition,
)


INTERACTION_TYPES = frozenset({"inspect", "action"})


class ExplorationMode(str, Enum):
    """Explicit UI modes used by :class:`GameEngine`'s exploration state."""

    SCENE_DIALOG = "SCENE_DIALOG"
    EXPLORATION_MENU = "EXPLORATION_MENU"
    MOVE_MENU = "MOVE_MENU"
    LOOK_MODE = "LOOK_MODE"
    LOOK_EVENT = "LOOK_EVENT"
    BAG = "BAG"
    ITEM_ACTION_MENU = "ITEM_ACTION_MENU"
    TOSS_CONFIRMATION = "TOSS_CONFIRMATION"


@dataclass(frozen=True)
class DialogueSequence:
    """Resolved text plus post-dialogue actions for an authored sequence."""

    identifier: str | None
    text: str
    actions: tuple[dict[str, Any], ...] = ()
    seen_flag: str | None = None


@dataclass
class ObjectRuntimeState:
    """Mutable presentation state for one authored scene object.

    The definition loaded from Story Core is never changed by gameplay.  A
    state is created lazily when an action first transforms an object, so
    ordinary scenes pay no extra setup cost.
    """

    x: int | None = None
    y: int | None = None
    rotation: float | None = None
    sprite: str | None = None
    animation: str | None = None
    visible: bool | None = None
    destroyed: bool = False


@dataclass
class SceneRuntime:
    """Non-persistent object presentation state for one active scene.

    Story progression belongs in flags and is re-resolved whenever a scene is
    entered.  This runtime layer is reserved for temporary event presentation
    such as a sprite replacement or an object animation.
    """

    hidden_objects: set[str] = field(default_factory=set)
    shown_objects: set[str] = field(default_factory=set)
    sprite_overrides: dict[str, str] = field(default_factory=dict)
    object_animations: dict[str, str] = field(default_factory=dict)
    object_states: dict[str, ObjectRuntimeState] = field(default_factory=dict)
    _transitions: dict[str, tuple[str, str, float, float, int, int]] = field(default_factory=dict, repr=False)

    def state_for(self, object_id: str) -> ObjectRuntimeState:
        return self.object_states.setdefault(object_id, ObjectRuntimeState())

    def update(self, now_ms: int) -> None:
        """Advance timed transforms without mutating authored definitions."""

        for transition_key, (object_id, kind, start, target, began_ms, duration_ms) in tuple(self._transitions.items()):
            elapsed = max(0, now_ms - began_ms)
            progress = 1.0 if duration_ms <= 0 else min(1.0, elapsed / duration_ms)
            value = start + (target - start) * progress
            state = self.state_for(object_id)
            if kind == "x":
                state.x = round(value)
            elif kind == "y":
                state.y = round(value)
            else:
                state.rotation = value
            if progress >= 1.0:
                self._transitions.pop(transition_key, None)

    def move_object(self, object_id: str, x: int, y: int, *, duration_ms: int = 0, now_ms: int = 0) -> None:
        state = self.state_for(object_id)
        current_x = state.x if state.x is not None else x
        current_y = state.y if state.y is not None else y
        if duration_ms > 0:
            self._transitions.pop(f"{object_id}:x", None)
            self._transitions.pop(f"{object_id}:y", None)
            self._transitions[f"{object_id}:x"] = (object_id, "x", float(current_x), float(x), now_ms, duration_ms)
            self._transitions[f"{object_id}:y"] = (object_id, "y", float(current_y), float(y), now_ms, duration_ms)
        else:
            state.x, state.y = int(x), int(y)

    def rotate_object(self, object_id: str, angle: float, *, duration_ms: int = 0, now_ms: int = 0) -> None:
        state = self.state_for(object_id)
        current = state.rotation if state.rotation is not None else 0.0
        if duration_ms > 0:
            self._transitions[f"{object_id}:rotation"] = (object_id, "rotation", float(current), float(angle), now_ms, duration_ms)
        else:
            state.rotation = float(angle)

    def destroy_object(self, object_id: str) -> None:
        self.state_for(object_id).destroyed = True
        self.hidden_objects.discard(object_id)
        self.shown_objects.discard(object_id)

    def hide_object(self, object_id: str) -> None:
        self.state_for(object_id).visible = False
        self.hidden_objects.add(object_id)
        self.shown_objects.discard(object_id)

    def show_object(self, object_id: str) -> None:
        state = self.state_for(object_id)
        if state.destroyed:
            return
        state.visible = True
        self.shown_objects.add(object_id)
        self.hidden_objects.discard(object_id)


@dataclass(frozen=True)
class LookTarget:
    """One resolved hit target in logical scene coordinates."""

    id: str
    rect: tuple[int, int, int, int]
    interaction: str
    event: str
    priority: int = 0
    z: int = 0
    order: int = 0
    object_id: str | None = None

    def contains(self, x: int | float, y: int | float) -> bool:
        left, top, width, height = self.rect
        return left <= x < left + width and top <= y < top + height


@dataclass(frozen=True)
class EventSignal:
    """A presentation/transition request emitted by :class:`EventRunner`."""

    kind: str
    data: Mapping[str, Any] = field(default_factory=dict)


class EventRunner:
    """Run data-authored event actions until an asynchronous boundary.

    ``advance`` executes ordinary state mutations immediately and emits
    signals for audio, dialogue, music, and scene transitions.  A ``dialog``
    action pauses the runner until the caller invokes ``resume_dialogue``;
    ``wait`` pauses it until a later call to ``advance(now_ms)``.  The handler
    table is intentionally a registry rather than a scene-specific chain.
    """

    def __init__(self, actions: Sequence[Mapping[str, Any]] | None, state: GameState,
                 runtime: SceneRuntime | None = None, events: Mapping[str, Any] | None = None,
                 item_ids: Iterable[str] | None = None,
                 max_hp_resolver: Callable[[], int] | None = None):
        self.actions = [dict(action) for action in actions or []]
        self.state = state
        self.runtime = runtime or SceneRuntime()
        self.events = events or {}
        self.item_ids = set(item_ids) if item_ids is not None else None
        self.max_hp_resolver = max_hp_resolver
        self.index = 0
        self.wait_until_ms: int | None = None
        self.waiting_for_dialogue = False
        self.finished = False
        self._handlers: dict[str, Callable[[dict[str, Any], int], list[EventSignal]]] = {
            "dialog": self._dialog,
            "sound": self._sound,
            "music": self._music,
            "animation": self._animation,
            "set_flag": self._set_flag,
            "clear_flag": self._clear_flag,
            "give_item": self._give_item,
            "remove_item": self._remove_item,
            "heal": self._heal,
            "damage": self._damage,
            "change_stat": self._change_stat,
            "show_object": self._show_object,
            "hide_object": self._hide_object,
            "change_sprite": self._change_sprite,
            "change_object_sprite": self._change_sprite,
            "move_object": self._move_object,
            "rotate_object": self._rotate_object,
            "play_object_animation": self._play_object_animation,
            "destroy_object": self._destroy_object,
            "wait": self._wait,
            "scene_transition": self._scene_transition,
            "trigger_event": self._trigger_event,
        }

    def advance(self, now_ms: int = 0) -> list[EventSignal]:
        """Execute as much of the action list as safely possible this frame."""
        self.runtime.update(now_ms)
        if self.finished or self.waiting_for_dialogue:
            return []
        if self.wait_until_ms is not None:
            if now_ms < self.wait_until_ms:
                return []
            self.wait_until_ms = None
        signals: list[EventSignal] = []
        while self.index < len(self.actions) and not self.waiting_for_dialogue and self.wait_until_ms is None:
            action = normalise_event_action(self.actions[self.index])
            self.index += 1
            action_type = action["type"]
            handler = self._handlers.get(action_type)
            if handler is None:
                raise StoryValidationError(f"Unknown exploration event action type {action_type!r}")
            signals.extend(handler(action, now_ms))
            if self.finished:
                break
        if self.index >= len(self.actions) and not self.waiting_for_dialogue and self.wait_until_ms is None:
            self.finished = True
        return signals

    def resume_dialogue(self, now_ms: int = 0) -> list[EventSignal]:
        """Resume after the frontend has completed the emitted dialogue."""
        if not self.waiting_for_dialogue:
            return []
        self.waiting_for_dialogue = False
        return self.advance(now_ms)

    def insert_next(self, actions: Sequence[Mapping[str, Any]]) -> None:
        """Insert actions immediately after the current asynchronous boundary.

        Dialogue sequences may carry follow-up actions (for example setting a
        first-visit flag).  The frontend calls this before resuming the
        paused event, preserving authored order without a nested event loop.
        """
        self.actions[self.index:self.index] = [dict(action) for action in actions]

    def _dialog(self, action: dict[str, Any], _now_ms: int) -> list[EventSignal]:
        if "dialog" in action:
            dialogue = action["dialog"]
        elif "sequence" in action:
            dialogue = action["sequence"]
        elif "text" in action:
            dialogue = {"text": action["text"]}
        else:
            dialogue = None
        if not isinstance(dialogue, (str, dict, list)):
            raise StoryValidationError("exploration dialog action requires dialog, sequence, or text")
        self.waiting_for_dialogue = True
        return [EventSignal("dialog", {"dialog": dialogue})]

    @staticmethod
    def _sound(action: dict[str, Any], _now_ms: int) -> list[EventSignal]:
        filename = action.get("file", action.get("sound"))
        if not isinstance(filename, str) or not filename:
            raise StoryValidationError("exploration sound action requires file")
        return [EventSignal("sound", {"file": filename})]

    @staticmethod
    def _music(action: dict[str, Any], _now_ms: int) -> list[EventSignal]:
        filename = action.get("file", action.get("music"))
        if filename is not None and (not isinstance(filename, str) or not filename):
            raise StoryValidationError("exploration music action file must be a non-empty string")
        return [EventSignal("music", {"file": filename, "stop": bool(action.get("stop", False))})]

    def _animation(self, action: dict[str, Any], _now_ms: int) -> list[EventSignal]:
        target, animation = _object_target(action), action.get("animation")
        if not isinstance(target, str) or not target or not isinstance(animation, str) or not animation:
            raise StoryValidationError("exploration animation action requires target and animation")
        self.runtime.object_animations[target] = animation
        self.runtime.state_for(target).animation = animation
        return [EventSignal("animation", {"target": target, "animation": animation})]

    def _play_object_animation(self, action: dict[str, Any], now_ms: int) -> list[EventSignal]:
        return self._animation(action, now_ms)

    def _set_flag(self, action: dict[str, Any], _now_ms: int) -> list[EventSignal]:
        flag = _required_string(action, "flag", "set_flag")
        self.state.set_flag(flag, bool(action.get("value", True)))
        return []

    def _clear_flag(self, action: dict[str, Any], _now_ms: int) -> list[EventSignal]:
        self.state.set_flag(_required_string(action, "flag", "clear_flag"), False)
        return []

    def _give_item(self, action: dict[str, Any], _now_ms: int) -> list[EventSignal]:
        item = _required_string(action, "item", "give_item")
        if self.item_ids is not None and item not in self.item_ids:
            raise StoryValidationError(f"exploration give_item references unknown item {item!r}")
        self.state.add_item(item, _positive_int(action.get("quantity", 1), "give_item quantity"))
        return []

    def _remove_item(self, action: dict[str, Any], _now_ms: int) -> list[EventSignal]:
        item = _required_string(action, "item", "remove_item")
        self.state.remove_item(item, _positive_int(action.get("quantity", 1), "remove_item quantity"))
        return []

    def _heal(self, action: dict[str, Any], _now_ms: int) -> list[EventSignal]:
        amount = _nonnegative_int(action.get("amount"), "heal amount")
        maximum = (self.max_hp_resolver() if self.max_hp_resolver is not None
                   else _state_int(self.state, "max_hp", _state_int(self.state, "hp", 1)))
        maximum = max(1, int(maximum))
        self.state.set_stat("hp", min(maximum, max(0, _state_int(self.state, "hp", 0)) + amount))
        return []

    def _damage(self, action: dict[str, Any], _now_ms: int) -> list[EventSignal]:
        amount = _nonnegative_int(action.get("amount"), "damage amount")
        self.state.set_stat("hp", max(0, _state_int(self.state, "hp", 0) - amount))
        return []

    def _change_stat(self, action: dict[str, Any], _now_ms: int) -> list[EventSignal]:
        stat = _required_string(action, "stat", "change_stat")
        delta = action.get("amount", action.get("delta"))
        if isinstance(delta, bool) or not isinstance(delta, int):
            raise StoryValidationError("exploration change_stat requires integer amount or delta")
        self.state.add_stat(stat, delta)
        return []

    def _show_object(self, action: dict[str, Any], _now_ms: int) -> list[EventSignal]:
        self.runtime.show_object(_object_target(action, "show_object"))
        return []

    def _hide_object(self, action: dict[str, Any], _now_ms: int) -> list[EventSignal]:
        self.runtime.hide_object(_object_target(action, "hide_object"))
        return []

    def _change_sprite(self, action: dict[str, Any], _now_ms: int) -> list[EventSignal]:
        target = _object_target(action, "change_sprite")
        sprite = _required_string(action, "sprite", "change_sprite")
        self.runtime.sprite_overrides[target] = sprite
        self.runtime.state_for(target).sprite = sprite
        return []

    def _move_object(self, action: dict[str, Any], now_ms: int) -> list[EventSignal]:
        target = _object_target(action, "move_object")
        position = action.get("position")
        if isinstance(position, (list, tuple)) and len(position) == 2:
            x, y = position
        else:
            x, y = action.get("x"), action.get("y")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in (x, y)):
            raise StoryValidationError("move_object requires numeric x and y or a two-value position")
        duration = _duration_ms(action, "move_object")
        self.runtime.move_object(target, round(float(x)), round(float(y)), duration_ms=duration, now_ms=now_ms)
        if duration:
            self.wait_until_ms = now_ms + duration
        return []

    def _rotate_object(self, action: dict[str, Any], now_ms: int) -> list[EventSignal]:
        target = _object_target(action, "rotate_object")
        angle = action.get("angle", action.get("rotation"))
        if isinstance(angle, bool) or not isinstance(angle, (int, float)):
            raise StoryValidationError("rotate_object requires a numeric angle in degrees")
        duration = _duration_ms(action, "rotate_object")
        self.runtime.rotate_object(target, float(angle), duration_ms=duration, now_ms=now_ms)
        if duration:
            self.wait_until_ms = now_ms + duration
        return []

    def _destroy_object(self, action: dict[str, Any], _now_ms: int) -> list[EventSignal]:
        self.runtime.destroy_object(_object_target(action, "destroy_object"))
        return []

    def _wait(self, action: dict[str, Any], now_ms: int) -> list[EventSignal]:
        seconds = action.get("seconds", action.get("duration", action.get("duration_seconds")))
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds < 0:
            raise StoryValidationError("exploration wait requires non-negative seconds or duration")
        self.wait_until_ms = now_ms + round(float(seconds) * 1000)
        return []

    def _scene_transition(self, action: dict[str, Any], _now_ms: int) -> list[EventSignal]:
        scene = action.get("scene", action.get("goto"))
        if not isinstance(scene, str) or not scene:
            raise StoryValidationError("exploration scene_transition requires scene")
        self.finished = True
        return [EventSignal("scene_transition", {"scene": scene})]

    def _trigger_event(self, action: dict[str, Any], _now_ms: int) -> list[EventSignal]:
        event_id = action.get("event", action.get("id"))
        if not isinstance(event_id, str) or not event_id:
            raise StoryValidationError("exploration trigger_event requires event")
        event = self.events.get(event_id)
        if not isinstance(event, Mapping) or not isinstance(event.get("actions"), Sequence):
            raise StoryValidationError(f"exploration trigger_event references unknown event {event_id!r}")
        nested = [dict(item) for item in event["actions"]]
        self.actions[self.index:self.index] = nested
        return []


def exploration_config(scene: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the opt-in config, accepting root aliases only when enabled."""
    raw = scene.get("exploration")
    if raw is None or raw is False:
        return None
    if raw is True:
        config: dict[str, Any] = {}
    elif isinstance(raw, Mapping):
        config = dict(raw)
    else:
        raise StoryValidationError("scene.exploration must be a mapping or true")
    for key in ("dialog", "dialogue_sequences", "navigation", "objects", "look_regions", "look_events", "cursor_speed", "cursors"):
        if key not in config and key in scene:
            config[key] = scene[key]
    return config


def evaluate_conditions(raw: Any, state: GameState) -> bool:
    """Evaluate both established condition dialects through Story/Core.

    The engine-facing exception class remains unchanged, while Story/Core is
    now the shared parser/evaluator used by headless tooling and runtime
    exploration alike.
    """
    try:
        return _evaluate_structured_condition(raw, state)
    except StoryCoreConditionError as exc:
        raise ConditionError(str(exc)) from None


def validate_conditions(raw: Any, context: str = "conditions") -> None:
    """Validate structured condition shape through the shared Story/Core."""
    try:
        _validate_structured_condition(raw, context)
    except StoryCoreConditionError as exc:
        raise StoryValidationError(str(exc)) from None


def resolve_dialogue(scene: Mapping[str, Any], state: GameState, reference: Any | None = None) -> DialogueSequence | None:
    """Resolve an intro or event dialogue reference against the scene config."""
    config = exploration_config(scene) or {}
    sequences = config.get("dialogue_sequences", {})
    if sequences is None:
        sequences = {}
    if not isinstance(sequences, Mapping):
        raise StoryValidationError("exploration.dialogue_sequences must be a mapping")
    if reference is None:
        choices = config.get("dialog", [])
        if not isinstance(choices, Sequence) or isinstance(choices, (str, bytes, Mapping)):
            raise StoryValidationError("exploration.dialog must be a list")
        for index, entry in enumerate(choices):
            if not isinstance(entry, Mapping):
                raise StoryValidationError(f"exploration.dialog[{index}] must be a mapping")
            condition = entry.get("conditions", entry.get("condition"))
            seen_flag = _dialogue_seen_flag(scene, index) if entry.get("once", False) else None
            if seen_flag is not None and state.get_flag(seen_flag):
                continue
            if evaluate_conditions(condition, state):
                if "sequence" in entry:
                    reference = entry["sequence"]
                elif "dialog" in entry:
                    reference = entry["dialog"]
                elif "text" in entry:
                    reference = {"text": entry["text"], "actions": entry.get("actions", [])}
                else:
                    reference = None
                if reference is None:
                    raise StoryValidationError(f"exploration.dialog[{index}] requires sequence, text, or dialog")
                sequence = _dialogue_from_reference(reference, sequences)
                return DialogueSequence(sequence.identifier, sequence.text, sequence.actions, seen_flag)
        else:
            return None
    return _dialogue_from_reference(reference, sequences)


def _dialogue_seen_flag(scene: Mapping[str, Any], index: int) -> str:
    scene_id = scene.get("id", "scene")
    safe_id = "".join(character if character.isalnum() or character == "_" else "_" for character in str(scene_id))
    return f"_exploration_dialog_{safe_id}_{index}_seen"


def _dialogue_from_reference(reference: Any, sequences: Mapping[str, Any]) -> DialogueSequence:
    identifier: str | None = None
    raw = reference
    if isinstance(reference, str) and reference in sequences:
        identifier, raw = reference, sequences[reference]
    elif isinstance(reference, str) and sequences:
        # A bare string in a configured sequence table is a sequence id, not
        # an accidental silent line of text.  Inline text remains available
        # by using ``text:`` explicitly.
        raise StoryValidationError(f"Exploration dialogue sequence {reference!r} does not exist")
    if isinstance(raw, str):
        return DialogueSequence(identifier, raw)
    if isinstance(raw, Mapping):
        text = raw.get("text", raw.get("dialog", ""))
        if isinstance(text, list):
            text = "\n\n".join(_required_text(value, "dialogue text") for value in text)
        if not isinstance(text, str):
            raise StoryValidationError("Exploration dialogue sequence text must be a string or list of strings")
        actions = raw.get("actions", [])
        if not isinstance(actions, Sequence) or isinstance(actions, (str, bytes, Mapping)):
            raise StoryValidationError("Exploration dialogue sequence actions must be a list")
        return DialogueSequence(identifier, text, tuple(dict(action) for action in actions))
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, Mapping)):
        return DialogueSequence(identifier, "\n\n".join(_required_text(value, "dialogue sequence") for value in raw))
    raise StoryValidationError("Exploration dialogue sequence must be a string, mapping, or list of strings")


def available_navigation(scene: Mapping[str, Any], state: GameState) -> list[dict[str, Any]]:
    config = exploration_config(scene) or {}
    raw = config.get("navigation", [])
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, Mapping)):
        raise StoryValidationError("exploration.navigation must be a list")
    result: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            raise StoryValidationError(f"exploration.navigation[{index}] must be a mapping")
        if evaluate_conditions(entry.get("conditions", entry.get("condition")), state):
            result.append(dict(entry))
    return result


def resolve_scene_objects(scene: Mapping[str, Any], state: GameState,
                          runtime: SceneRuntime | None = None) -> list[dict[str, Any]]:
    """Return visible object copies with runtime overrides applied.

    The returned dictionaries are transient renderer input.  Authored object
    definitions remain untouched even after transforms, sprite changes, or
    destruction.
    """
    config = exploration_config(scene) or {}
    raw = config.get("objects", [])
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, Mapping)):
        raise StoryValidationError("exploration.objects must be a list")
    runtime = runtime or SceneRuntime()
    result: list[dict[str, Any]] = []
    for order, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            raise StoryValidationError(f"exploration.objects[{order}] must be a mapping")
        object_id = entry.get("id")
        if not isinstance(object_id, str) or not object_id:
            raise StoryValidationError(f"exploration.objects[{order}].id must be a non-empty string")
        object_state = runtime.object_states.get(object_id)
        if object_state is not None and object_state.destroyed:
            continue
        if object_state is not None and object_state.x is None and object_state.y is None:
            position = entry.get("position")
            if isinstance(position, (list, tuple)) and len(position) == 2:
                object_state.x, object_state.y = int(position[0]), int(position[1])
        if object_state is not None and object_state.rotation is None:
            rotation = entry.get("rotation")
            if isinstance(rotation, (int, float)) and not isinstance(rotation, bool):
                object_state.rotation = float(rotation)
        visible = bool(entry.get("visible", True)) and evaluate_conditions(entry.get("visible_when", entry.get("conditions")), state)
        if object_state is not None and object_state.visible is not None:
            visible = object_state.visible
        if object_id in runtime.shown_objects:
            visible = True
        if object_id in runtime.hidden_objects:
            visible = False
        if visible:
            copy = dict(entry)
            if object_state is not None:
                if object_state.x is not None or object_state.y is not None:
                    original = entry.get("position", (0, 0))
                    ox, oy = original if isinstance(original, (list, tuple)) and len(original) == 2 else (0, 0)
                    copy["position"] = [object_state.x if object_state.x is not None else ox,
                                        object_state.y if object_state.y is not None else oy]
                if object_state.rotation is not None:
                    copy["rotation"] = object_state.rotation
                if object_state.sprite is not None:
                    copy["sprite"] = object_state.sprite
                if object_state.animation is not None:
                    copy["animation"] = object_state.animation
            if object_id in runtime.sprite_overrides:
                copy["sprite"] = runtime.sprite_overrides[object_id]
            if object_id in runtime.object_animations:
                copy["animation"] = runtime.object_animations[object_id]
            copy["_order"] = order
            copy["visible"] = True
            result.append(copy)
    return result


def resolve_look_targets(scene: Mapping[str, Any], state: GameState,
                         runtime: SceneRuntime | None = None) -> list[LookTarget]:
    """Resolve visible object targets and invisible regions in stable order."""
    # Direct headless callers may still hand the runtime a raw legacy scene.
    # Normalize it at the compatibility boundary; the normal game path has
    # already done this through AssetLoader/LegacyProjectView.
    from engine.story_core.compat import migrate_legacy_object_interactions
    scene = migrate_legacy_object_interactions(scene, preserve_object_ids=True)
    config = exploration_config(scene) or {}
    targets: list[LookTarget] = []
    order = 0
    regions = config.get("look_regions", [])
    if not isinstance(regions, Sequence) or isinstance(regions, (str, bytes, Mapping)):
        raise StoryValidationError("exploration.look_regions must be a list")
    for index, region in enumerate(regions):
        if not isinstance(region, Mapping):
            raise StoryValidationError(f"exploration.look_regions[{index}] must be a mapping")
        if region.get("visible", True) is False:
            continue
        if not evaluate_conditions(region.get("visible_when", region.get("conditions")), state):
            continue
        look = _resolved_look_spec(region.get("look", region), state)
        if look is not None:
            target = _make_look_target(region.get("id"), look, region, order)
            if target is not None:
                targets.append(target)
                order += 1
    return targets


def _resolved_look_spec(raw: Any, state: GameState) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    base = {key: value for key, value in raw.items() if key != "states"}
    states = raw.get("states")
    if states is not None:
        if not isinstance(states, Sequence) or isinstance(states, (str, bytes, Mapping)):
            raise StoryValidationError("look.states must be a list")
        for index, variant in enumerate(states):
            if not isinstance(variant, Mapping):
                raise StoryValidationError(f"look.states[{index}] must be a mapping")
            if evaluate_conditions(variant.get("conditions", variant.get("condition")), state):
                base.update({key: value for key, value in variant.items() if key not in {"conditions", "condition"}})
                break
    return base if isinstance(base.get("event"), str) and base.get("event") else None


def _make_look_target(identifier: Any, look: Mapping[str, Any], owner: Mapping[str, Any], order: int) -> LookTarget | None:
    if not isinstance(identifier, str) or not identifier:
        raise StoryValidationError("look target id must be a non-empty string")
    rect = look.get("rect")
    if rect is None:
        rect = look.get("hitbox", owner.get("hitbox"))
        if rect is not None and owner.get("position") is not None and "rect" not in look:
            # Object hitboxes are local by default; explicit look.rect stays
            # absolute, which is convenient for YAML examples and regions.
            position = owner.get("position")
            if isinstance(position, (list, tuple)) and len(position) == 2:
                rect = [int(position[0]) + int(rect[0]), int(position[1]) + int(rect[1]), rect[2], rect[3]]
    if rect is None:
        position, size = owner.get("position"), owner.get("size")
        if isinstance(position, (list, tuple)) and len(position) == 2 and isinstance(size, (list, tuple)) and len(size) == 2:
            rect = [position[0], position[1], size[0], size[1]]
    if rect is None:
        return None
    normalized = _rect(rect, f"look target {identifier!r}")
    interaction = look.get("interaction", owner.get("interaction", "inspect"))
    event = look.get("event")
    if interaction not in INTERACTION_TYPES:
        raise StoryValidationError(
            f"look target {identifier!r} interaction must be one of: {', '.join(sorted(INTERACTION_TYPES))}"
        )
    if not isinstance(event, str) or not event:
        raise StoryValidationError(f"look target {identifier!r} event must be a non-empty string")
    priority = _integer_default(look.get("priority", owner.get("priority", 0)), f"look target {identifier!r} priority")
    z = _integer_default(owner.get("z", 0), f"look target {identifier!r} z")
    return LookTarget(identifier, normalized, interaction, event, priority, z, order,
                      identifier if "position" in owner else None)


def look_target_at(targets: Sequence[LookTarget], x: int | float, y: int | float) -> LookTarget | None:
    """Return the deterministic winner: priority, z, then declaration order."""
    candidates = [target for target in targets if target.contains(x, y)]
    return max(candidates, key=lambda target: (target.priority, target.z, target.order), default=None)


def look_event_actions(scene: Mapping[str, Any], event_id: str) -> list[dict[str, Any]]:
    config = exploration_config(scene) or {}
    events = config.get("look_events", {})
    if not isinstance(events, Mapping):
        raise StoryValidationError("exploration.look_events must be a mapping")
    event = events.get(event_id)
    if not isinstance(event, Mapping) or not isinstance(event.get("actions"), Sequence):
        raise StoryValidationError(f"Exploration event {event_id!r} does not exist or has no actions list")
    return [dict(action) for action in event["actions"]]


def normalise_event_action(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Accept modern typed actions plus simple legacy one-key action forms."""
    if not isinstance(raw, Mapping):
        raise StoryValidationError("exploration event action must be a mapping")
    if "type" in raw:
        action = dict(raw)
        if not isinstance(action["type"], str) or not action["type"]:
            raise StoryValidationError("exploration event action type must be a non-empty string")
        aliases = {"play_sfx": "sound", "play_sound": "sound", "play_music": "music", "goto": "scene_transition",
                   "play_animation": "play_object_animation"}
        action["type"] = aliases.get(action["type"], action["type"])
        return action
    if len(raw) != 1:
        raise StoryValidationError("exploration event action needs type or one legacy action key")
    key, value = next(iter(raw.items()))
    aliases = {"play_sfx": "sound", "add_item": "give_item", "goto": "scene_transition",
               "play_animation": "play_object_animation"}
    action_type = aliases.get(key, key)
    if action_type == "set_flag" and isinstance(value, Mapping) and len(value) == 1:
        flag, flag_value = next(iter(value.items()))
        return {"type": "set_flag", "flag": flag, "value": flag_value}
    if action_type in {"give_item", "remove_item"}:
        if isinstance(value, Mapping):
            return {"type": action_type, **value}
        return {"type": action_type, "item": value}
    if action_type == "sound":
        return {"type": "sound", "file": value}
    if action_type == "scene_transition":
        return {"type": "scene_transition", "scene": value}
    if action_type == "dialog":
        return {"type": "dialog", "dialog": value}
    if action_type in {"show_object", "hide_object", "destroy_object", "move_object", "rotate_object",
                       "change_sprite", "change_object_sprite", "animation", "play_object_animation"}:
        if isinstance(value, Mapping):
            return {"type": action_type, **value}
        return {"type": action_type, "target": value}
    return {"type": action_type, "value": value}


def validate_exploration_scene(scene: Mapping[str, Any], scene_id: str, *, known_scene_ids: set[str] | None = None,
                               known_battle_ids: set[str] | None = None,
                               item_ids: set[str] | None = None) -> None:
    """Validate opt-in YAML with actionable scene/field error messages."""
    config = exploration_config(scene)
    if config is None:
        return
    prefix = f"scenes/{scene_id}.yaml exploration"
    speed = config.get("cursor_speed")
    if speed is not None and (isinstance(speed, bool) or not isinstance(speed, (int, float)) or speed <= 0):
        raise StoryValidationError(f"{prefix}.cursor_speed must be a positive number")
    visit_flag = config.get("visit_flag")
    if visit_flag is not None and (not isinstance(visit_flag, str) or not visit_flag):
        raise StoryValidationError(f"{prefix}.visit_flag must be a non-empty string")
    cursors = config.get("cursors", {})
    if not isinstance(cursors, Mapping):
        raise StoryValidationError(f"{prefix}.cursors must be a mapping")
    for name, sprite in cursors.items():
        if not isinstance(name, str) or not name or not isinstance(sprite, str) or not sprite:
            raise StoryValidationError(f"{prefix}.cursors entries must map non-empty names to sprite strings")

    sequences = config.get("dialogue_sequences", {})
    if not isinstance(sequences, Mapping):
        raise StoryValidationError(f"{prefix}.dialogue_sequences must be a mapping")
    for name, raw in sequences.items():
        if not isinstance(name, str) or not name:
            raise StoryValidationError(f"{prefix}.dialogue_sequences has an invalid id")
        _dialogue_from_reference(raw, {})

    dialog = config.get("dialog", [])
    if not isinstance(dialog, Sequence) or isinstance(dialog, (str, bytes, Mapping)):
        raise StoryValidationError(f"{prefix}.dialog must be a list")
    for index, entry in enumerate(dialog):
        if not isinstance(entry, Mapping):
            raise StoryValidationError(f"{prefix}.dialog[{index}] must be a mapping")
        validate_conditions(entry.get("conditions", entry.get("condition")), f"{prefix}.dialog[{index}].conditions")
        if "once" in entry and not isinstance(entry["once"], bool):
            raise StoryValidationError(f"{prefix}.dialog[{index}].once must be true or false")
        sequence = entry.get("sequence")
        if sequence is not None and (not isinstance(sequence, str) or sequence not in sequences):
            raise StoryValidationError(f"{prefix}.dialog[{index}].sequence references unknown dialogue {sequence!r}")
        if "text" in entry and not isinstance(entry["text"], str):
            raise StoryValidationError(f"{prefix}.dialog[{index}].text must be a string")
        if "actions" in entry and (not isinstance(entry["actions"], Sequence)
                                  or isinstance(entry["actions"], (str, bytes, Mapping))):
            raise StoryValidationError(f"{prefix}.dialog[{index}].actions must be a list")

    navigation = config.get("navigation", [])
    if not isinstance(navigation, Sequence) or isinstance(navigation, (str, bytes, Mapping)):
        raise StoryValidationError(f"{prefix}.navigation must be a list")
    for index, entry in enumerate(navigation):
        if not isinstance(entry, Mapping):
            raise StoryValidationError(f"{prefix}.navigation[{index}] must be a mapping")
        scene_target = entry.get("scene")
        battle_target = entry.get("battle")
        target_count = int(scene_target is not None) + int(battle_target is not None)
        if target_count != 1:
            raise StoryValidationError(f"{prefix}.navigation[{index}] requires exactly one of scene or battle")
        if scene_target is not None:
            if not isinstance(scene_target, str) or not scene_target:
                raise StoryValidationError(f"{prefix}.navigation[{index}].scene must be a non-empty string")
            if known_scene_ids is not None and scene_target not in known_scene_ids:
                raise StoryValidationError(f"{prefix}.navigation[{index}].scene references nonexistent scene {scene_target!r}")
            for outcome in ("on_win", "on_lose"):
                if outcome in entry:
                    raise StoryValidationError(f"{prefix}.navigation[{index}].{outcome} is only valid for a battle target")
        else:
            if not isinstance(battle_target, str) or not battle_target:
                raise StoryValidationError(f"{prefix}.navigation[{index}].battle must be a non-empty string")
            if known_battle_ids is not None and battle_target not in known_battle_ids:
                raise StoryValidationError(f"{prefix}.navigation[{index}].battle references nonexistent battle {battle_target!r}")
            for outcome in ("on_win", "on_lose"):
                target = entry.get(outcome)
                if target is None:
                    continue
                if not isinstance(target, str) or not target:
                    raise StoryValidationError(f"{prefix}.navigation[{index}].{outcome} must be a non-empty scene id")
                if known_scene_ids is not None and target not in known_scene_ids:
                    raise StoryValidationError(f"{prefix}.navigation[{index}].{outcome} references nonexistent scene {target!r}")
        if "label" in entry and (not isinstance(entry["label"], str) or not entry["label"]):
            raise StoryValidationError(f"{prefix}.navigation[{index}].label must be a non-empty string")
        validate_conditions(entry.get("conditions", entry.get("condition")), f"{prefix}.navigation[{index}].conditions")

    object_ids: set[str] = set()
    target_specs: list[tuple[str, Mapping[str, Any], Mapping[str, Any]]] = []
    objects = config.get("objects", [])
    if not isinstance(objects, Sequence) or isinstance(objects, (str, bytes, Mapping)):
        raise StoryValidationError(f"{prefix}.objects must be a list")
    for index, obj in enumerate(objects):
        if not isinstance(obj, Mapping):
            raise StoryValidationError(f"{prefix}.objects[{index}] must be a mapping")
        object_id = obj.get("id")
        if not isinstance(object_id, str) or not object_id or object_id in object_ids:
            raise StoryValidationError(f"{prefix}.objects[{index}].id is missing or duplicated")
        object_ids.add(object_id)
        if "sprite" in obj and (not isinstance(obj["sprite"], str) or not obj["sprite"]):
            raise StoryValidationError(f"{prefix}.objects[{index}].sprite must be a non-empty string")
        if "position" in obj:
            _point(obj["position"], f"{prefix}.objects[{index}].position")
        if "size" in obj:
            _size(obj["size"], f"{prefix}.objects[{index}].size")
        for numeric_key in ("z", "rotation"):
            if numeric_key in obj and (isinstance(obj[numeric_key], bool) or not isinstance(obj[numeric_key], (int, float))):
                raise StoryValidationError(f"{prefix}.objects[{index}].{numeric_key} must be numeric")
        if "visible" in obj and not isinstance(obj["visible"], bool):
            raise StoryValidationError(f"{prefix}.objects[{index}].visible must be true or false")
        validate_conditions(obj.get("visible_when", obj.get("conditions")), f"{prefix}.objects[{index}].visible_when")
        if isinstance(obj.get("look"), Mapping):
            target_specs.append((object_id, obj["look"], obj))

    regions = config.get("look_regions", [])
    if not isinstance(regions, Sequence) or isinstance(regions, (str, bytes, Mapping)):
        raise StoryValidationError(f"{prefix}.look_regions must be a list")
    region_ids: set[str] = set()
    for index, region in enumerate(regions):
        if not isinstance(region, Mapping):
            raise StoryValidationError(f"{prefix}.look_regions[{index}] must be a mapping")
        region_id = region.get("id")
        if not isinstance(region_id, str) or not region_id or region_id in object_ids or region_id in region_ids:
            raise StoryValidationError(f"{prefix}.look_regions[{index}].id is missing or duplicated")
        region_ids.add(region_id)
        validate_conditions(region.get("visible_when", region.get("conditions")), f"{prefix}.look_regions[{index}].conditions")
        target_specs.append((region_id, region.get("look", region), region))

    events = config.get("look_events", {})
    if not isinstance(events, Mapping):
        raise StoryValidationError(f"{prefix}.look_events must be a mapping")
    for target_id, look, owner in target_specs:
        _validate_look_spec(look, owner, target_id, prefix, set(events))
    for event_id, event in events.items():
        if not isinstance(event_id, str) or not event_id or not isinstance(event, Mapping):
            raise StoryValidationError(f"{prefix}.look_events has an invalid event mapping")
        actions = event.get("actions")
        if not isinstance(actions, Sequence) or isinstance(actions, (str, bytes, Mapping)):
            raise StoryValidationError(f"{prefix}.look_events.{event_id}.actions must be a list")
        for index, action in enumerate(actions):
            _validate_event_action(action, f"{prefix}.look_events.{event_id}.actions[{index}]", sequences, object_ids,
                                   set(events), item_ids, known_scene_ids)


def _validate_look_spec(look: Any, owner: Mapping[str, Any], target_id: str, prefix: str,
                        event_ids: set[str]) -> None:
    if not isinstance(look, Mapping):
        return
    variants = [look]
    states = look.get("states", [])
    if states:
        if not isinstance(states, Sequence) or isinstance(states, (str, bytes, Mapping)):
            raise StoryValidationError(f"{prefix} look target {target_id}.states must be a list")
        variants.extend(states)
    for variant in variants:
        if not isinstance(variant, Mapping):
            raise StoryValidationError(f"{prefix} look target {target_id} state must be a mapping")
        validate_conditions(variant.get("conditions", variant.get("condition")), f"{prefix} look target {target_id}.conditions")
        event = variant.get("event", look.get("event"))
        if event is not None and (not isinstance(event, str) or event not in event_ids):
            raise StoryValidationError(f"{prefix} look target {target_id} references nonexistent event {event!r}")
        interaction = variant.get("interaction", look.get("interaction", owner.get("interaction")))
        if interaction not in INTERACTION_TYPES:
            raise StoryValidationError(
                f"{prefix} look target {target_id}.interaction must be one of: "
                f"{', '.join(sorted(INTERACTION_TYPES))}"
            )
    rect = look.get("rect", look.get("hitbox", owner.get("hitbox")))
    if rect is not None:
        _rect(rect, f"{prefix} look target {target_id}.rect")


def _validate_event_action(raw: Any, context: str, sequences: Mapping[str, Any], object_ids: set[str],
                           event_ids: set[str], item_ids: set[str] | None,
                           known_scene_ids: set[str] | None) -> None:
    action = normalise_event_action(raw)
    action_type = action["type"]
    allowed = {"dialog", "sound", "music", "animation", "play_object_animation", "set_flag", "clear_flag", "give_item", "remove_item",
               "heal", "damage", "change_stat", "show_object", "hide_object", "change_sprite", "change_object_sprite",
               "move_object", "rotate_object", "destroy_object", "wait",
               "scene_transition", "trigger_event"}
    if action_type not in allowed:
        raise StoryValidationError(f"{context}.type has unknown action {action_type!r}")
    if action_type == "dialog":
        if "dialog" not in action and "sequence" not in action and "text" in action:
            _required_text(action["text"], f"{context}.text")
        else:
            reference = action.get("dialog", action.get("sequence"))
            if isinstance(reference, str) and reference not in sequences:
                raise StoryValidationError(f"{context}.dialog references unknown dialogue {reference!r}")
    elif action_type == "sound":
        _required_string(action, "file", context)
    elif action_type == "music":
        if not action.get("stop"):
            _required_string(action, "file", context)
    elif action_type in {"animation", "play_object_animation"}:
        target = _object_target(action, context)
        if target not in object_ids:
            raise StoryValidationError(f"{context}.target references nonexistent object {target!r}")
        _required_string(action, "animation", context)
    elif action_type in {"show_object", "hide_object", "change_sprite", "change_object_sprite", "destroy_object"}:
        target = _object_target(action, context)
        if target not in object_ids:
            raise StoryValidationError(f"{context}.target references nonexistent object {target!r}")
        if action_type in {"change_sprite", "change_object_sprite"}:
            _required_string(action, "sprite", context)
    elif action_type == "move_object":
        target = _object_target(action, context)
        if target not in object_ids:
            raise StoryValidationError(f"{context}.target references nonexistent object {target!r}")
        position = action.get("position")
        if isinstance(position, (list, tuple)) and len(position) == 2:
            _point(position, f"{context}.position")
        else:
            for key in ("x", "y"):
                if isinstance(action.get(key), bool) or not isinstance(action.get(key), (int, float)):
                    raise StoryValidationError(f"{context}.{key} must be numeric")
        _duration_ms(action, context)
    elif action_type == "rotate_object":
        target = _object_target(action, context)
        if target not in object_ids:
            raise StoryValidationError(f"{context}.target references nonexistent object {target!r}")
        angle = action.get("angle", action.get("rotation"))
        if isinstance(angle, bool) or not isinstance(angle, (int, float)):
            raise StoryValidationError(f"{context}.angle must be numeric degrees")
        _duration_ms(action, context)
    elif action_type in {"give_item", "remove_item"}:
        item = _required_string(action, "item", context)
        if item_ids is not None and item not in item_ids:
            raise StoryValidationError(f"{context}.item references nonexistent item {item!r}")
        _positive_int(action.get("quantity", 1), f"{context}.quantity")
    elif action_type in {"set_flag", "clear_flag"}:
        _required_string(action, "flag", context)
    elif action_type in {"heal", "damage"}:
        _nonnegative_int(action.get("amount"), f"{context}.amount")
    elif action_type == "change_stat":
        _required_string(action, "stat", context)
        delta = action.get("amount", action.get("delta"))
        if isinstance(delta, bool) or not isinstance(delta, int):
            raise StoryValidationError(f"{context} requires integer amount or delta")
    elif action_type == "wait":
        duration = action.get("seconds", action.get("duration", action.get("duration_seconds")))
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration < 0:
            raise StoryValidationError(f"{context} requires non-negative seconds or duration")
    elif action_type == "scene_transition":
        scene = action.get("scene", action.get("goto"))
        if not isinstance(scene, str) or not scene:
            raise StoryValidationError(f"{context}.scene must be a non-empty string")
        if known_scene_ids is not None and scene not in known_scene_ids:
            raise StoryValidationError(f"{context}.scene references nonexistent scene {scene!r}")
    elif action_type == "trigger_event":
        event = action.get("event", action.get("id"))
        if not isinstance(event, str) or event not in event_ids:
            raise StoryValidationError(f"{context}.event references nonexistent event {event!r}")


def _required_string(data: Mapping[str, Any], key: str, context: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise StoryValidationError(f"{context}.{key} must be a non-empty string")
    return value


def _object_target(data: Mapping[str, Any], context: str = "object action") -> str:
    """Read both the current ``target`` spelling and the documented ``object`` spelling."""

    value = data.get("target", data.get("object", data.get("object_id")))
    if not isinstance(value, str) or not value:
        raise StoryValidationError(f"{context} requires an object reference")
    return value


def _duration_ms(data: Mapping[str, Any], context: str) -> int:
    value = data.get("duration", data.get("duration_seconds", 0))
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise StoryValidationError(f"{context} duration must be a non-negative number of seconds")
    return round(float(value) * 1000)


def _required_text(value: Any, context: str) -> str:
    if not isinstance(value, str):
        raise StoryValidationError(f"{context} must be a string")
    return value


def _positive_int(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise StoryValidationError(f"{context} must be a positive integer")
    return value


def _nonnegative_int(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StoryValidationError(f"{context} must be a non-negative integer")
    return value


def _integer_default(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StoryValidationError(f"{context} must be an integer")
    return value


def _state_int(state: GameState, name: str, default: int) -> int:
    value = state.get_stat(name, default)
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _point(raw: Any, context: str) -> tuple[int, int]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise StoryValidationError(f"{context} must be [x, y]")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in raw):
        raise StoryValidationError(f"{context} values must be integers")
    return int(raw[0]), int(raw[1])


def _size(raw: Any, context: str) -> tuple[int, int]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise StoryValidationError(f"{context} must contain width and height")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in raw):
        raise StoryValidationError(f"{context} values must be numeric")
    if raw[0] <= 0 or raw[1] <= 0:
        raise StoryValidationError(f"{context} width and height must be positive")
    return int(raw[0]), int(raw[1])


def _rect(raw: Any, context: str) -> tuple[int, int, int, int]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        raise StoryValidationError(f"{context} must be [x, y, width, height]")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in raw):
        raise StoryValidationError(f"{context} values must be integers")
    x, y, width, height = (int(value) for value in raw)
    if width <= 0 or height <= 0:
        raise StoryValidationError(f"{context} width and height must be positive")
    return x, y, width, height
