"""
engine/core/story_interpreter.py

Runs the branching-narrative logic: entering a scene, executing its entry
actions, filtering choices by condition, and resolving a chosen choice into
a Transition describing what happens next. Deliberately knows nothing about
rendering or audio playback -- it returns *requests* (which sfx to play,
where to go next) and lets the engine (game_engine.py) actually dispatch
them to the renderer/audio system. This keeps the interpreter fully
testable without a terminal or audio device.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine.core.asset_loader import AssetLoader
from engine.core.condition_eval import evaluate_condition
from engine.core.game_state import GameState
from engine.errors import StoryValidationError
from engine.story_core.compat.legacy_views import LegacyProjectView


@dataclass
class Transition:
    """What should happen after a choice is resolved. Exactly one of
    scene_id / battle_id / event_pool_id will be set, depending on `kind`."""
    kind: str  # "goto" | "battle" | "random_event"
    scene_id: str | None = None
    battle_id: str | None = None
    on_win: str | None = None
    on_lose: str | None = None
    event_pool_id: str | None = None
    test_sequence: dict[str, Any] | None = None
    sfx: list[str] = field(default_factory=list)


class StoryInterpreter:
    def __init__(
        self,
        assets: AssetLoader,
        state: GameState,
        *,
        project_view: LegacyProjectView | None = None,
    ):
        self.assets = assets
        self.state = state
        # The game session supplies a view over its already-loaded
        # StoryProject.  Keeping the optional legacy fallback preserves the
        # small standalone interpreter API used by headless callers while
        # avoiding a second project load here.
        self.project_view = project_view

    def _load_scene(self, scene_id: str) -> dict[str, Any]:
        if self.project_view is not None:
            return self.project_view.load_scene(scene_id)
        return self.assets.load_scene(scene_id)

    # -- scene entry -----------------------------------------------------
    def enter_scene(
        self,
        scene_id: str | None = None,
        *,
        scene: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        """Loads a scene, records it as current, runs its entry actions.
        Returns (scene_data, sfx_requested_by_entry_actions)."""
        scene_id = scene_id or self.state.current_scene
        scene = scene if scene is not None else self._load_scene(scene_id)
        self.state.enter_scene(scene_id)
        sfx = self.run_actions(scene.get("actions", []))
        return scene, sfx

    def available_choices(self, scene: dict[str, Any]) -> list[dict[str, Any]]:
        """Choices whose `condition:` (if any) currently evaluates true."""
        result = []
        for choice in scene.get("choices", []):
            if evaluate_condition(choice.get("condition"), self.state):
                result.append(choice)
        return result

    def is_ending(self, scene: dict[str, Any]) -> bool:
        """A scene with no available choices at all is treated as an
        ending -- either explicitly marked `ending: true`, or implicitly
        because the author gave it no choices block."""
        return bool(scene.get("ending")) or not scene.get("choices")

    # -- choice resolution -------------------------------------------------
    def resolve_choice(self, choice: dict[str, Any]) -> Transition:
        """Runs the choice's own actions (if any), then figures out what
        kind of transition it produces. Exactly one of goto/battle/
        random_event must be present on a valid choice."""
        sfx = self.run_actions(choice.get("actions", []))

        if "battle" in choice:
            test_sequence = choice.get("test_sequence")
            if test_sequence is not None and not isinstance(test_sequence, dict):
                raise StoryValidationError("test_sequence must be a mapping")
            return Transition(
                kind="battle",
                battle_id=choice["battle"],
                on_win=choice.get("on_win"),
                on_lose=choice.get("on_lose"),
                test_sequence=dict(test_sequence) if test_sequence is not None else None,
                sfx=sfx,
            )
        if "random_event" in choice:
            return Transition(kind="random_event", event_pool_id=choice["random_event"], sfx=sfx)
        if "goto" in choice:
            return Transition(kind="goto", scene_id=choice["goto"], sfx=sfx)

        raise StoryValidationError(
            f"Choice {choice.get('text')!r} has none of goto/battle/random_event"
        )

    # -- actions -------------------------------------------------------------
    def run_actions(self, actions: list[dict[str, Any]]) -> list[str]:
        """Executes a list of action dicts (each a single {action_type: value}
        pair) against game state. Returns any sfx filenames requested via
        play_sfx, so the caller can hand them to the audio system."""
        sfx: list[str] = []
        for action in actions:
            if len(action) != 1:
                raise StoryValidationError(f"Malformed action (expected one key): {action}")
            (key, value), = action.items()

            if key == "set_flag":
                for name, val in value.items():
                    self.state.set_flag(name, val)
            elif key == "set_variable":
                for name, val in value.items():
                    self.state.set_var(name, val)
            elif key == "add_variable":
                for name, val in value.items():
                    self.state.add_var(name, val)
            elif key == "add_item":
                for item_id in (value if isinstance(value, list) else [value]):
                    self.state.add_item(item_id)
            elif key == "remove_item":
                for item_id in (value if isinstance(value, list) else [value]):
                    self.state.remove_item(item_id)
            elif key == "equip_item":
                if not isinstance(value, dict):
                    raise StoryValidationError("equip_item must map an equipment slot to an item id")
                for slot, item_id in value.items():
                    if not isinstance(slot, str) or not isinstance(item_id, str):
                        raise StoryValidationError("equip_item slots and item ids must be strings")
                    self.state.equip_item(slot, item_id)
            elif key == "play_sfx":
                sfx.append(value)
            else:
                raise StoryValidationError(f"Unknown action type: {key!r}")
        return sfx

    def apply_rewards(self, rewards: dict[str, Any] | None) -> None:
        """Shared helper for battle-victory / random-event rewards blocks:
        {variables: {gold: 5}, items: [wolf_pelt]}."""
        if not rewards:
            return
        for name, delta in rewards.get("variables", {}).items():
            self.state.add_var(name, delta)
        for item_id in rewards.get("items", []):
            self.state.add_item(item_id)
