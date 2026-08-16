"""Static, compatibility-preserving action adapters.

Actions are intentionally scoped.  A legacy scene action mutates persistent
story state, an exploration event action can also request presentation work,
an inventory-use action has its own lifecycle, and battle actions may target
transient fight state.  The core describes those forms without asking the
pygame runtime to consume a new universal action protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

from .models import freeze_value, thaw_value


class ActionError(ValueError):
    """An authored action does not have a supported structural form."""


class ActionScope(str, Enum):
    """The state/lifetime in which an action is meaningful."""

    STORY = "story"
    EXPLORATION = "exploration"
    INVENTORY_USE = "inventory_use"
    BATTLE = "battle"

    # Descriptive aliases used by callers that prefer the language from the
    # architecture audit.
    PERSISTENT_STORY = "story"
    BATTLE_LOCAL = "battle"


class ActionForm(str, Enum):
    LEGACY = "legacy"
    TYPED = "typed"


@dataclass(frozen=True)
class ActionEditorField:
    """Editor-facing metadata for one authored action parameter.

    This is deliberately descriptive only.  Runtime validation and execution
    remain in the exploration action adapter; the Designer uses this metadata
    to choose a native control and to create the canonical typed shape.
    """

    key: str
    display_name: str
    kind: str = "string"
    default: Any = ""
    required: bool = False
    reference_target: str | None = None
    description: str = ""
    options: tuple[Any, ...] = ()
    asset_kind: str | None = None


@dataclass(frozen=True)
class ActionEditorSpec:
    """Small, reusable action vocabulary shared by editor presentations."""

    type: str
    display_name: str
    fields: tuple[ActionEditorField, ...] = ()
    description: str = ""
    scope: ActionScope = ActionScope.EXPLORATION

    def minimal_mapping(self) -> dict[str, Any]:
        result: dict[str, Any] = {"type": self.type}
        result.update({field.key: thaw_value(field.default) for field in self.fields})
        return result


_EXPLORATION_ACTION_EDITOR_SPECS: tuple[ActionEditorSpec, ...] = (
    ActionEditorSpec("dialog", "Dialogue", (
        ActionEditorField("text", "Text", "multiline", ""),
    )),
    ActionEditorSpec("sound", "Play Sound", (
        ActionEditorField("file", "Sound file", "asset", "", asset_kind="sfx"),
    )),
    ActionEditorSpec("music", "Play Music", (
        ActionEditorField("file", "Music file", "asset", "", asset_kind="music"),
        ActionEditorField("stop", "Stop", "boolean", False),
    )),
    ActionEditorSpec("set_flag", "Set Flag", (
        ActionEditorField("flag", "Flag", "string", "", True),
        ActionEditorField("value", "Value", "boolean", True),
    )),
    ActionEditorSpec("clear_flag", "Clear Flag", (
        ActionEditorField("flag", "Flag", "string", "", True),
    )),
    ActionEditorSpec("give_item", "Give Item", (
        ActionEditorField("item", "Item", "reference", "", True, "item"),
        ActionEditorField("quantity", "Quantity", "integer", 1),
    )),
    ActionEditorSpec("remove_item", "Remove Item", (
        ActionEditorField("item", "Item", "reference", "", True, "item"),
        ActionEditorField("quantity", "Quantity", "integer", 1),
    )),
    ActionEditorSpec("animation", "Play Animation", (
        ActionEditorField("target", "Object", "reference", "", True, "scene_object"),
        ActionEditorField("animation", "Animation", "reference", "", True, "animation", asset_kind="animation"),
    )),
    # The explicit object-* names are the canonical vocabulary for new
    # authoring.  The shorter forms below remain supported for old stories.
    ActionEditorSpec("move_object", "Move Object", (
        ActionEditorField("target", "Object", "reference", "", True, "scene_object"),
        ActionEditorField("position", "Position", "point", [0, 0]),
        ActionEditorField("duration", "Duration (seconds)", "number", 0.0),
    )),
    ActionEditorSpec("rotate_object", "Rotate Object", (
        ActionEditorField("target", "Object", "reference", "", True, "scene_object"),
        ActionEditorField("angle", "Angle (degrees)", "number", 0.0),
        ActionEditorField("duration", "Duration (seconds)", "number", 0.0),
    )),
    ActionEditorSpec("change_object_sprite", "Change Object Sprite", (
        ActionEditorField("target", "Object", "reference", "", True, "scene_object"),
        ActionEditorField("sprite", "Sprite", "asset", "", asset_kind="sprites"),
    )),
    ActionEditorSpec("play_object_animation", "Play Object Animation", (
        ActionEditorField("target", "Object", "reference", "", True, "scene_object"),
        ActionEditorField("animation", "Animation", "reference", "", True, "animation", asset_kind="animation"),
    )),
    ActionEditorSpec("destroy_object", "Destroy Object", (
        ActionEditorField("target", "Object", "reference", "", True, "scene_object"),
    )),
    ActionEditorSpec("show_object", "Show Object", (
        ActionEditorField("target", "Object", "reference", "", True, "scene_object"),
    )),
    ActionEditorSpec("hide_object", "Hide Object", (
        ActionEditorField("target", "Object", "reference", "", True, "scene_object"),
    )),
    ActionEditorSpec("change_sprite", "Change Sprite", (
        ActionEditorField("target", "Object", "reference", "", True, "scene_object"),
        ActionEditorField("sprite", "Sprite", "asset", "", asset_kind="sprites"),
    )),
    ActionEditorSpec("heal", "Heal", (
        ActionEditorField("amount", "Amount", "integer", 0),
    )),
    ActionEditorSpec("damage", "Damage", (
        ActionEditorField("amount", "Amount", "integer", 0),
    )),
    ActionEditorSpec("change_stat", "Change Stat", (
        ActionEditorField("stat", "Stat", "string", "", True),
        ActionEditorField("amount", "Amount", "integer", 0),
    )),
    ActionEditorSpec("wait", "Wait", (
        ActionEditorField("seconds", "Seconds", "number", 0.0),
    )),
    ActionEditorSpec("scene_transition", "Go To Scene", (
        ActionEditorField("scene", "Scene", "reference", "", True, "scene"),
    )),
    ActionEditorSpec("trigger_event", "Trigger Event", (
        ActionEditorField("event", "Look Event", "string", "", True),
    )),
)

_EXPLORATION_ACTION_EDITOR_BY_TYPE = {spec.type: spec for spec in _EXPLORATION_ACTION_EDITOR_SPECS}


def action_editor_specs(scope: ActionScope | str = ActionScope.EXPLORATION) -> tuple[ActionEditorSpec, ...]:
    """Return metadata for actions authorable in the selected scope."""

    selected_scope = ActionScope(scope)
    if selected_scope is ActionScope.EXPLORATION:
        return _EXPLORATION_ACTION_EDITOR_SPECS
    return ()


def action_editor_spec(action_type: str, scope: ActionScope | str = ActionScope.EXPLORATION) -> ActionEditorSpec | None:
    """Look up one editor spec without changing the authored action."""

    if not isinstance(action_type, str) or not action_type:
        return None
    return next((spec for spec in action_editor_specs(scope) if spec.type == action_type), None)


def minimal_authored_action(action_type: str, scope: ActionScope | str = ActionScope.EXPLORATION) -> dict[str, Any]:
    """Create the canonical typed skeleton for a newly authored action."""

    spec = action_editor_spec(action_type, scope)
    if spec is None:
        raise ActionError(f"Unsupported {ActionScope(scope).value} action type {action_type!r}")
    return spec.minimal_mapping()


_EXPLORATION_TYPED_ACTION_ALIASES = {
    "play_sfx": "sound",
    "play_sound": "sound",
    "play_music": "music",
    "goto": "scene_transition",
}

_EXPLORATION_LEGACY_ACTION_ALIASES = {
    "play_sfx": "sound",
    "add_item": "give_item",
    "goto": "scene_transition",
}

# ``StoryInterpreter.run_actions`` is a deliberately small, fixed legacy
# vocabulary.  Unlike the extensible exploration event registry, an unknown
# one-key scene action is guaranteed to fail when a player reaches it.
_STORY_ACTION_TYPES = frozenset({
    "set_flag", "set_variable", "add_variable", "add_item", "remove_item",
    "equip_item", "play_sfx",
})


@dataclass(frozen=True)
class StoryAction:
    """One authored action plus a scope-aware canonical description."""

    scope: ActionScope
    action_type: str
    payload: Any = None
    form: ActionForm = ActionForm.TYPED
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        # A frozen dataclass alone does not protect nested authored payloads.
        # Freeze both views so actions can safely be created directly from a
        # definition's immutable authored mapping or from a mutable caller
        # mapping without exposing either object graph.
        object.__setattr__(self, "scope", ActionScope(self.scope))
        object.__setattr__(self, "payload", freeze_value(self.payload))
        frozen_raw = freeze_value(self.raw)
        if not isinstance(frozen_raw, Mapping):
            frozen_raw = freeze_value({})
        object.__setattr__(self, "raw", frozen_raw)

    @property
    def type(self) -> str:
        """Convenience alias for tools which use ``type`` terminology."""

        return self.action_type

    def to_mapping(self) -> dict[str, Any]:
        """Return a fresh legacy-compatible authored mapping."""

        value = thaw_value(self.raw)
        return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class ActionReferences:
    """Static references discoverable from an action payload."""

    scenes: frozenset[str] = frozenset()
    items: frozenset[str] = frozenset()
    moves: frozenset[str] = frozenset()
    battles: frozenset[str] = frozenset()
    animations: frozenset[str] = frozenset()
    flags: frozenset[str] = frozenset()
    variables: frozenset[str] = frozenset()
    fight_flags: frozenset[str] = frozenset()

    def merged(self, other: "ActionReferences") -> "ActionReferences":
        return ActionReferences(
            scenes=self.scenes | other.scenes,
            items=self.items | other.items,
            moves=self.moves | other.moves,
            battles=self.battles | other.battles,
            animations=self.animations | other.animations,
            flags=self.flags | other.flags,
            variables=self.variables | other.variables,
            fight_flags=self.fight_flags | other.fight_flags,
        )


def parse_action(raw: Any, scope: ActionScope | str) -> StoryAction:
    """Adapt one legacy one-key or modern typed action without executing it."""

    selected_scope = ActionScope(scope)
    if not isinstance(raw, Mapping):
        raise ActionError(f"{selected_scope.value} action must be a mapping, got {raw!r}")
    raw_copy = thaw_value(raw)
    if not isinstance(raw_copy, dict):  # Mapping protocol contract fallback.
        raw_copy = dict(raw)
    action_type = raw.get("type")
    if isinstance(action_type, str) and action_type:
        if selected_scope in {ActionScope.STORY, ActionScope.BATTLE}:
            raise ActionError(
                f"{selected_scope.value} actions use legacy one-key mappings, not a typed 'type' field"
            )
        payload = {key: thaw_value(value) for key, value in raw.items() if key != "type"}
        return StoryAction(
            selected_scope,
            _canonical_action_type(action_type, selected_scope, ActionForm.TYPED),
            payload,
            ActionForm.TYPED,
            raw_copy,
        )
    if len(raw) != 1:
        raise ActionError(
            f"{selected_scope.value} legacy action must contain exactly one key, got {raw!r}"
        )
    action_type, payload = next(iter(raw.items()))
    if not isinstance(action_type, str) or not action_type:
        raise ActionError(f"{selected_scope.value} action type must be a non-empty string")
    if selected_scope is ActionScope.INVENTORY_USE:
        raise ActionError("inventory_use actions require a typed non-empty 'type' field")
    if selected_scope is ActionScope.STORY and action_type not in _STORY_ACTION_TYPES:
        raise ActionError(f"Unknown story action type {action_type!r}")
    if selected_scope is ActionScope.STORY:
        _validate_story_payload(action_type, payload)
    return StoryAction(
        selected_scope,
        _canonical_action_type(action_type, selected_scope, ActionForm.LEGACY),
        thaw_value(payload),
        ActionForm.LEGACY,
        raw_copy,
    )


def parse_actions(raw: Any, scope: ActionScope | str) -> tuple[StoryAction, ...]:
    """Adapt a list of authored actions, preserving authored order."""

    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ActionError(f"{ActionScope(scope).value} actions must be a list")
    return tuple(parse_action(action, scope) for action in raw)


def _validate_story_payload(action_type: str, payload: Any) -> None:
    """Reject only payload shapes that ``StoryInterpreter`` cannot run.

    Story actions deliberately retain the original one-key YAML form.  The
    runtime has no universal normalizer, so accepting a known action name is
    not enough: several handlers immediately call ``.items()`` or require a
    string identifier.  Keep this check structural rather than inventing
    stricter value semantics (for example, variable values remain authored
    ``Any`` just as they are in :class:`GameState`).
    """

    if action_type in {"set_flag", "set_variable", "add_variable"}:
        if not isinstance(payload, Mapping):
            raise ActionError(f"{action_type} action payload must be a mapping")
        return

    if action_type == "equip_item":
        if not isinstance(payload, Mapping):
            raise ActionError("equip_item action must map an equipment slot to an item id")
        if any(not isinstance(slot, str) or not isinstance(item_id, str) for slot, item_id in payload.items()):
            raise ActionError("equip_item action slots and item ids must be strings")
        return

    if action_type in {"add_item", "remove_item"}:
        values = payload if isinstance(payload, (list, tuple)) else (payload,)
        if not all(isinstance(item_id, str) and item_id for item_id in values):
            raise ActionError(f"{action_type} action must contain an item id or a list of item ids")
        return

    if action_type == "play_sfx" and (not isinstance(payload, str) or not payload):
        raise ActionError("play_sfx action payload must be a non-empty filename")


def action_references(action: StoryAction | Mapping[str, Any], scope: ActionScope | str | None = None) -> ActionReferences:
    """Collect references while retaining the intentionally local semantics.

    Unknown/custom action types are left untouched rather than rejected: many
    current action areas are extensible, and a future schema registry can add
    richer metadata without deleting source data.
    """

    if not isinstance(action, StoryAction):
        if scope is None:
            raise TypeError("scope is required when reading a raw action mapping")
        action = parse_action(action, scope)
    payload = action.payload
    mapping = payload if isinstance(payload, Mapping) else {}
    refs = ActionReferences()

    def add(field: str, value: Any, target: str) -> None:
        nonlocal refs
        values = _string_values(value)
        if not values:
            return
        kwargs = {target: getattr(refs, target) | frozenset(values)}
        refs = ActionReferences(
            scenes=kwargs.get("scenes", refs.scenes),
            items=kwargs.get("items", refs.items),
            moves=kwargs.get("moves", refs.moves),
            battles=kwargs.get("battles", refs.battles),
            animations=kwargs.get("animations", refs.animations),
            flags=kwargs.get("flags", refs.flags),
            variables=kwargs.get("variables", refs.variables),
            fight_flags=kwargs.get("fight_flags", refs.fight_flags),
        )

    kind = action.action_type
    if action.form is ActionForm.LEGACY:
        if kind in {"add_item", "give_item", "remove_item"}:
            add("value", payload, "items")
        elif kind == "equip_item" and isinstance(payload, Mapping):
            add("value", tuple(payload.values()), "items")
        elif kind == "set_flag" and isinstance(payload, Mapping):
            add("value", tuple(payload.keys()), "flags")
        elif kind in {"set_variable", "add_variable"} and isinstance(payload, Mapping):
            add("value", tuple(payload.keys()), "variables")
        elif kind in {"scene_transition", "goto"}:
            add("value", payload, "scenes")
        elif kind == "set_fight_flag" and isinstance(payload, Mapping):
            add("value", tuple(payload.keys()), "fight_flags")
        return refs

    if kind in {"give_item", "remove_item"}:
        add("item", mapping.get("item"), "items")
    elif kind in {"scene_transition", "goto"}:
        add("scene", mapping.get("scene", mapping.get("goto")), "scenes")
    elif kind in {"start_battle", "battle"}:
        add("battle", mapping.get("battle"), "battles")
    elif kind in {"animation", "play_object_animation"}:
        add("animation", mapping.get("animation"), "animations")
    elif kind in {"set_flag", "clear_flag"}:
        add("flag", mapping.get("flag"), "flags")
    elif kind in {"set_variable", "add_variable", "change_variable"}:
        add("variable", mapping.get("variable", mapping.get("var")), "variables")
    elif kind == "set_fight_flag":
        value = mapping.get("flag")
        if value is None and isinstance(mapping.get("flags"), Mapping):
            value = mapping["flags"].keys()
        add("flag", value, "fight_flags")
    elif kind in {
        "add_player_move", "remove_player_move", "replace_player_move",
        "augment_player_move",
    }:
        add("move", mapping.get("move"), "moves")
    return refs


def actions_references(actions: Iterable[StoryAction | Mapping[str, Any]], scope: ActionScope | str | None = None) -> ActionReferences:
    """Combine references from an action collection."""

    result = ActionReferences()
    for action in actions:
        result = result.merged(action_references(action, scope))
    return result


def _string_values(value: Any) -> tuple[str, ...]:
    if isinstance(value, str) and value:
        return (value,)
    if isinstance(value, (list, tuple, set)):
        return tuple(item for item in value if isinstance(item, str) and item)
    return ()


def _canonical_action_type(action_type: str, scope: ActionScope, form: ActionForm) -> str:
    """Normalize only documented exploration aliases, retaining raw syntax."""

    if scope is ActionScope.EXPLORATION:
        aliases = (
            _EXPLORATION_TYPED_ACTION_ALIASES
            if form is ActionForm.TYPED
            else _EXPLORATION_LEGACY_ACTION_ALIASES
        )
        return aliases.get(action_type, action_type)
    return action_type
