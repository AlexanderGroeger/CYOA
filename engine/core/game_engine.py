"""Pygame application coordinator.

Story interpretation and save data remain renderer-independent; this module
only turns discrete pygame actions into state transitions and draw requests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import re
import random

from engine.audio.audio_system import AudioSystem
from engine.battle.config import load_battle_config
from engine.battle.controller import BattleController
from engine.battle.controls import held_battle_input
from engine.battle.move_progression import initial_difficulty_level
from engine.core.asset_loader import AssetLoader
from engine.core.exploration import (
    EventRunner,
    ExplorationMode,
    SceneRuntime,
    available_navigation,
    exploration_config,
    look_event_actions,
    look_target_at,
    resolve_dialogue,
    resolve_look_targets,
    resolve_scene_objects,
)
from engine.core.game_over import GameOverPresentation, GameOverStage
from engine.core.game_state import GameState
from engine.core.developer_test import (
    BattleTestConfiguration,
    DeveloperTestConfigError,
    SceneTestConfiguration,
    apply_developer_test_configuration,
    load_developer_test_configuration,
)
from engine.core.inventory import InventoryActionError, InventoryGrid, InventoryLayout, InventoryService
from engine.core.story_interpreter import StoryInterpreter, Transition
from engine.errors import AssetNotFoundError, SaveVersionError, StoryValidationError
from engine.events.random_events import maybe_trigger
from engine.render.display import parse_display_config
from engine.render.renderer import Renderer
from engine.render.terminal_input import BACK, DOWN, LEFT, LOAD, QUIT, RIGHT, SAVE, SELECT, SELECT_RELEASE, UP, action_from_event, move_selection
from engine.save.save_system import load_game, save_game


class GameEngine:
    DIALOGUE_CHARACTER_DELAY_MS = 25
    DIALOGUE_BLIP_EVERY_LETTERS = 2
    DIALOGUE_BLIP_SFX = "dialog_blip.wav"
    DIALOGUE_PAGE_FINISHED_SFX = "dialog_page_finished.wav"
    MENU_CURSOR_SFX = "menu_cursor.wav"
    MENU_SELECT_SFX = "menu_select.wav"
    SAVE_SFX = "save.wav"
    CHECKPOINT_SLOT_SUFFIX = "_checkpoint"
    ROTATING_STRIKE_HIT_SFX = "hit.wav"
    OPTION_SELECT_DELAY_MS = 1_000
    DIALOGUE_PAGE_DELAY_MS = 200

    def __init__(
        self,
        story_dir: str,
        shared_dir: str = "shared_assets",
        save_slot: str = "slot1",
        disable_animation_delay: bool = False,
        *,
        developer_mode: bool = False,
        start_scene_override: str | None = None,
        start_battle_override: str | None = None,
        developer_test_config_path: str | Path | None = None,
        developer_test_config: SceneTestConfiguration | BattleTestConfiguration | None = None,
    ):
        self.story_dir = Path(story_dir)
        self.assets = AssetLoader(story_dir, shared_dir)
        self.manifest = self.assets.load_manifest()
        self.audio_preferences = self.assets.load_audio_config()
        self.player_profile = self.assets.load_player()
        # Keep one canonical, headless project snapshot with the game
        # session.  Runtime consumers that still require raw YAML-shaped
        # dictionaries receive isolated copies through this shared view.
        self.story_project = self.assets.load_project()
        # Core diagnostics are deliberately advisory at this compatibility
        # boundary.  Existing loader validation and runtime error timing stay
        # authoritative; no Core diagnostic is promoted to a startup failure.
        self.story_project_diagnostics = self.story_project.validate()
        self.story_view = self.story_project.legacy_view()
        self.items = self.story_view.load_items()
        # Validate only the opt-in exploration vocabulary before pygame opens;
        # legacy stories retain their existing permissive scene/item loading.
        self.assets.validate_exploration_content(item_registry=self.items)
        self.inventory = InventoryService(self.items)
        self.inventory_layout = InventoryLayout.from_profile(self.player_profile)
        self.inventory_grid = InventoryGrid(self.inventory_layout)
        self.combat_move_config = self._load_combat_move_config()
        self.moves = self.combat_move_config["moves"]
        self.story_id = self.manifest.get("id", self.story_dir.name)
        self.story_version = str(self.manifest.get("version", "0.0"))
        self.save_slot, self.save_dir = save_slot, self.story_dir / "saves"
        self.state = GameState.new_from_manifest(self.manifest, self.player_profile)
        if developer_test_config_path is not None and developer_test_config is not None:
            raise DeveloperTestConfigError(
                "Provide only one of developer_test_config_path and developer_test_config"
            )
        if developer_test_config_path is not None:
            if not developer_mode:
                raise DeveloperTestConfigError("Developer test configuration requires developer mode")
            developer_test_config = load_developer_test_configuration(developer_test_config_path)
        if developer_test_config is not None:
            if not developer_mode:
                raise DeveloperTestConfigError("Developer test configuration requires developer mode")
            apply_developer_test_configuration(
                self.state,
                developer_test_config,
                known_items=self.items,
            )
        config_battle_id = (
            developer_test_config.battle_id
            if isinstance(developer_test_config, BattleTestConfiguration)
            else None
        )
        config_scene_id = (
            developer_test_config.scene_id
            if developer_test_config is not None
            else None
        )
        if (start_scene_override is not None or config_scene_id is not None) and (
            start_battle_override is not None or config_battle_id is not None
        ):
            raise DeveloperTestConfigError("Developer test startup cannot specify both a scene and a battle")
        if start_battle_override is not None and config_battle_id is not None and start_battle_override != config_battle_id:
            raise DeveloperTestConfigError("--battle does not match the battle in the developer test configuration")
        self._developer_battle_id = start_battle_override or config_battle_id
        # This is intentionally a state-startup override rather than a
        # mutation of the authored manifest.  All other fresh-game values
        # still come from the normal manifest/profile initialization path.
        self.developer_mode = bool(
            developer_mode or start_scene_override is not None or self._developer_battle_id is not None
            or developer_test_config is not None
        )
        selected_scene = start_scene_override
        if selected_scene is None and developer_test_config is not None:
            selected_scene = developer_test_config.scene_id
        if selected_scene is not None:
            self.state.current_scene = str(selected_scene)
        self._initialize_move_skill_defaults()
        self.interpreter = StoryInterpreter(
            self.assets,
            self.state,
            project_view=self.story_view,
        )
        self.renderer = Renderer(self.assets, parse_display_config(self.manifest), self.manifest.get("render", {}))
        self.audio = AudioSystem(
            self.assets,
            master_volume=self.audio_preferences.get("master_volume", 0.8),
            music_volume=self.audio_preferences.get("music_volume", 1.0),
            effects_volume=self.audio_preferences.get("effects_volume", 1.0),
        )
        # The rotating-strike QTE's hit cue is tied directly to A/Enter. Decode
        # it before combat so the first successful strike has no disk/decode
        # delay.
        self.audio.preload_sfx(self.ROTATING_STRIKE_HIT_SFX)
        self.running = True
        self.scene: dict[str, Any] | None = None
        self.choices: list[dict[str, Any]] = []
        self.selected = 0
        self.message: str | None = None
        self.text_pages: list[str] = []
        self.text_page_index = 0
        self.visible_characters = 0
        self._next_dialogue_character_ms = 0
        self._word_letter_count = 0
        self._dialogue_input_unlock_ms = 0
        self._pending_selection: str | None = None
        self._selection_advance_ms = 0
        self.ending = False
        self.battle: BattleController | None = None
        self.game_over: GameOverPresentation | None = None
        self._game_over_text = "Game over"
        self._battle_on_win: str | None = None
        self._battle_on_lose: str | None = None
        self._battle_test_sequence: dict[str, Any] | None = None
        self._scene_history: list[str] = []
        self._battle_dt = 0.0
        # Exploration has an explicit, non-blocking UI state alongside the
        # existing battle state.  All fields are reset on scene transition.
        self._exploration_mode: ExplorationMode | None = None
        self._exploration_runtime: SceneRuntime | None = None
        self._exploration_runner: EventRunner | None = None
        self._exploration_return_mode = ExplorationMode.EXPLORATION_MENU
        self._exploration_dialogue_active = False
        self._exploration_dialogue_actions: list[dict[str, Any]] = []
        self._exploration_dialogue_return_mode = ExplorationMode.EXPLORATION_MENU
        self._exploration_action_selected = 0
        self._exploration_move_selected = 0
        self._exploration_modal_selected = 0
        self._exploration_item_actions: tuple[str, ...] = ()
        self._exploration_cursor_x = 0
        self._exploration_cursor_y = 0
        self._exploration_cursor_x_float = 0.0
        self._exploration_cursor_y_float = 0.0
        self._exploration_main_button_down = False
        self._exploration_pressed_target_id: str | None = None
        self._exploration_cursor_animation_phase: int | None = None

    def run(self) -> None:
        """Run a capped event loop. No terminal output or stdin is involved."""
        try:
            if self._developer_battle_id is not None:
                # Load a renderer context without executing scene entry
                # actions.  Direct battle testing must begin from the fresh
                # profile/manifest state plus explicit overrides, then enter
                # combat through the same transition path used by gameplay.
                self._prepare_battle_test_context()
                self._start_battle(Transition(
                    kind="battle",
                    battle_id=self._developer_battle_id,
                ))
                # A direct test has no source choice to supply outcome
                # destinations: victory/escape return to this fresh test
                # scene, while defeat follows the battle's own completion
                # policy and exits when no destination exists.
                self._render()
            else:
                self._enter_scene(self.state.current_scene)
            while self.running:
                changed = False
                for event in self.renderer.events():
                    changed |= self.handle_action(action_from_event(event))
                for action in self.renderer.controller_navigation_actions():
                    changed |= self.handle_action(action)
                changed |= self._process_pending_selection()
                if self.battle is not None:
                    self.renderer.prepare_battle_dialogue(self.battle)
                    changed |= self.battle.update(
                        self._battle_dt, held_battle_input(self.renderer.pygame, self.renderer.controller_input)
                    )
                    self._dispatch_battle_audio()
                    if self.battle.game_over_menu_ready:
                        self._begin_game_over_menu()
                        changed = True
                    elif self.battle.finished:
                        self._finish_battle()
                        changed = True
                elif self.game_over is not None:
                    changed |= self._update_game_over(self._battle_dt)
                elif self.scene is not None:
                    if self._exploration_mode is not None:
                        changed |= self._update_exploration()
                    else:
                        changed |= self.renderer.animation_changed(self.scene)
                        changed |= self._advance_dialogue_animation()
                if changed:
                    self._render()
                self._battle_dt = self.renderer.tick() / 1000.0
        finally:
            self.audio.stop_music()
            self.renderer.shutdown()

    def _prepare_battle_test_context(self) -> None:
        """Provide the normal scene/render context without scene side effects."""

        self.scene = self._load_scene_definition(self.state.current_scene)
        self.choices = []
        self.selected = 0
        self.message = None
        self.ending = False
        self._pending_selection = None
        self._reset_exploration_state()

    def _enter_scene(self, scene_id: str) -> None:
        self.game_over = None
        self._game_over_text = "Game over"
        self._reset_exploration_state()
        if hasattr(self, "story_view"):
            scene_definition = self._load_scene_definition(scene_id)
            self.scene, entry_sfx = self.interpreter.enter_scene(scene_id, scene=scene_definition)
        else:
            # Preserve the lightweight __new__-constructed engine doubles used
            # by headless tests and older embedding callers.  A normally
            # initialized GameEngine always has story_view and takes the
            # canonical project-backed path above.
            self.scene, entry_sfx = self.interpreter.enter_scene(scene_id)
        if self.scene.get("checkpoint") is True:
            self._save_checkpoint()
        default_background = self.manifest.get("default_scene_background")
        if isinstance(default_background, str) and default_background:
            self.scene = {**self.scene, "background": default_background}
        self._dispatch_sfx(entry_sfx)
        if self.scene.get("music"):
            self.audio.play_music(self.scene["music"])
        if exploration_config(self.scene) is not None:
            self._setup_exploration_scene()
            self._render()
            return
        self.choices = self.interpreter.available_choices(self.scene)
        self.selected, self.message = 0, None
        self._pending_selection = None
        self.text_page_index = 0
        self.text_pages = self.renderer.paginate_text(
            self._format_scene_text(self.scene.get("text", "")),
            self.scene.get("font_size", self.manifest.get("render", {}).get("font_size", 14)),
        )
        self.ending = self.interpreter.is_ending(self.scene) and not (
            self._history_navigation_enabled() and self._scene_history
        )
        self._reset_dialogue_animation()
        if self._instant_scene_text_enabled():
            self._finish_dialogue_page()
        if not self.ending and not self.choices and not (
            self._history_navigation_enabled() and self._scene_history
        ):
            self.ending, self.message = True, "No choices are available in this scene."
        if self.ending:
            self.state.ending_reached = self.scene.get("ending_id", scene_id)
        self._render()

    def _load_scene_definition(self, scene_id: str) -> dict[str, Any]:
        """Return one isolated legacy scene mapping from the canonical project."""
        return self.story_view.load_scene(scene_id)

    def _load_combat_move_config(self) -> dict[str, Any]:
        """Return the shared runtime move envelope from the canonical project.

        Fully initialized gameplay always has ``story_view`` and therefore
        receives a fresh compatibility mapping from the one StoryProject
        owned by this engine.  The AssetLoader branch is intentionally narrow:
        older ``__new__``-constructed engine doubles may not initialize the
        project boundary at all.
        """
        if hasattr(self, "story_view"):
            return self.story_view.load_combat_move_config()
        return self.assets.load_combat_move_config()

    def _load_event_pool_definition(self, event_pool_id: str) -> dict[str, Any]:
        """Return one isolated legacy event-pool mapping from the project.

        A normally initialized engine always has ``story_view``.  The
        AssetLoader fallback keeps lightweight ``__new__``-constructed engine
        doubles and older embedding callers compatible without making that
        legacy path an authority for normal gameplay.
        """
        if hasattr(self, "story_view"):
            return self.story_view.load_event_pool(event_pool_id)
        return self.assets.load_event_pool(event_pool_id)

    def _load_battle_definition(self, battle_id: str) -> dict[str, Any]:
        """Return one isolated legacy battle mapping from the project.

        A normally initialized engine always has ``story_view``.  The
        AssetLoader fallback keeps lightweight ``__new__``-constructed engine
        doubles and older embedding callers compatible without making that
        legacy path an authority for normal gameplay.
        """
        if hasattr(self, "story_view"):
            return self.story_view.load_battle(battle_id)
        return self.assets.load_battle(battle_id)

    def _render(self) -> None:
        """Pass the active isolated scene mapping to the presentation layer.

        ``self.scene`` is the mapping loaded at scene entry from ``story_view``
        and shared with the interpreter.  Renderer asset helpers may still
        resolve/load binary or text assets, but they do not look up authored
        scenes independently.
        """
        assert self.scene is not None
        if self.battle:
            self.renderer.render_battle(self.battle)
        elif self.game_over is not None:
            self.renderer.render_game_over(
                self.game_over, self._game_over_text,
                [{"text": "Get up"}, {"text": "Die"}], self.selected,
            )
        elif self._exploration_mode is not None:
            self.renderer.render_exploration(self.scene, self._exploration_render_view())
        else:
            page = self.text_pages[self.text_page_index] if self.text_pages else ""
            page_complete = self.visible_characters >= len(page)
            final_page = self.text_page_index >= len(self.text_pages) - 1 and page_complete
            message = self.message
            if self.ending and final_page and message is None:
                page = f"{page}\n\nTHE END — press A / Enter or B / Backspace to exit"
            self.renderer.render(
                self.scene,
                self.choices,
                self.selected,
                message,
                text_page=page[:self.visible_characters] if message is None else None,
                show_options=final_page and not self.ending,
            )

    def handle_action(self, action: str) -> bool:
        """Apply one discrete input action; public for headless logic tests."""
        if action == QUIT:
            self.running = False
            return True
        if getattr(self, "game_over", None) is not None:
            return self._handle_game_over_action(action)
        if self._pending_selection is not None:
            return False
        if self.battle is None and self.renderer.pygame.time.get_ticks() < self._dialogue_input_unlock_ms:
            return False
        if action == SAVE:
            self._handle_save()
            return True
        if action == LOAD:
            return self._handle_load()
        if self.battle is not None:
            active_attack = getattr(self.battle, "active_attack", None)
            player_qte_input = self.battle.state.name == "PLAYER_ATTACK"
            changed = self.battle.handle_action(action)
            rapid_slash_input = (active_attack is not None and active_attack.qte_type == "rapid_slash"
                                 and action in (LEFT, RIGHT))
            if changed and action in (UP, DOWN, LEFT, RIGHT, BACK) and not rapid_slash_input:
                self.audio.play_sfx(self.MENU_CURSOR_SFX)
            elif changed and action == SELECT and not player_qte_input:
                self.audio.play_sfx(self.MENU_SELECT_SFX)
            self._dispatch_battle_audio()
            if self.battle.finished:
                self._finish_battle()
            return changed
        if getattr(self, "_exploration_mode", None) is not None:
            return self._handle_exploration_action(action)
        if self.battle is None and self.visible_characters < len(self._current_dialogue_page()):
            if action == SELECT:
                self._finish_dialogue_page()
                return True
            return False
        if self.battle is None and self.text_page_index < len(self.text_pages) - 1:
            if action == SELECT:
                self.audio.play_sfx(self.DIALOGUE_PAGE_FINISHED_SFX)
                self.text_page_index += 1
                self._reset_dialogue_animation()
                self._dialogue_input_unlock_ms = self.renderer.pygame.time.get_ticks() + self.DIALOGUE_PAGE_DELAY_MS
                return True
            return False
        if self.ending:
            if action in (SELECT, BACK):
                self.running = False
            return action != "UNKNOWN"
        options = self.choices
        if action in (UP, DOWN, LEFT, RIGHT):
            next_selected = move_selection(self.selected, action, len(options))
            changed, self.selected = next_selected != self.selected, next_selected
            if changed:
                self.audio.play_sfx(self.MENU_CURSOR_SFX)
            return changed
        if action == BACK:
            if self._history_navigation_enabled() and self._scene_history:
                self._enter_scene(self._scene_history.pop())
                return True
            self.running = False
            return True
        if action == SELECT and options:
            self.audio.play_sfx(self.MENU_SELECT_SFX)
            if self._instant_menu_selection_enabled():
                self._choose_scene_option()
                return True
            self._pending_selection = "scene"
            self._selection_advance_ms = self.renderer.pygame.time.get_ticks() + self.OPTION_SELECT_DELAY_MS
            return True
        return False

    def _handle_game_over_action(self, action: str) -> bool:
        """Keep game-over input inert until its timed choice menu appears."""
        assert self.game_over is not None
        if not self.game_over.show_menu:
            return False
        if action in (UP, DOWN, LEFT, RIGHT):
            next_selected = move_selection(self.selected, action, 2)
            changed, self.selected = next_selected != self.selected, next_selected
            if changed:
                self.audio.play_sfx(self.MENU_CURSOR_SFX)
            return changed
        if action != SELECT:
            return False
        action_name = ("get_up", "die")[self.selected]
        if action_name == "get_up":
            changed = self.game_over.choose_get_up()
        elif action_name == "die":
            changed = self.game_over.choose_die()
        else:
            raise StoryValidationError(f"Unknown game-over action: {action_name!r}")
        if changed:
            self.audio.play_sfx(self.MENU_SELECT_SFX)
        return changed

    # -- exploration -------------------------------------------------------
    def _reset_exploration_state(self) -> None:
        """Clear the active scene's transient exploration/UI state."""
        self._exploration_mode = None
        self._exploration_runtime = None
        self._exploration_runner = None
        self._exploration_dialogue_active = False
        self._exploration_dialogue_actions = []
        self._exploration_return_mode = ExplorationMode.EXPLORATION_MENU
        self._exploration_dialogue_return_mode = ExplorationMode.EXPLORATION_MENU
        self._exploration_action_selected = 0
        self._exploration_move_selected = 0
        self._exploration_modal_selected = 0
        self._exploration_item_actions = ()
        self._exploration_main_button_down = False
        self._exploration_pressed_target_id = None
        self._exploration_cursor_animation_phase = None

    def _setup_exploration_scene(self) -> None:
        """Initialize an opt-in scene after normal interpreter entry actions."""
        assert self.scene is not None
        config = exploration_config(self.scene)
        assert config is not None
        self.choices, self.selected, self.message = [], 0, None
        self.ending = False
        self._pending_selection = None
        self._exploration_runtime = SceneRuntime()
        self._exploration_mode = ExplorationMode.EXPLORATION_MENU
        width, height = self.renderer.config.width, self.renderer.config.height
        start = config.get("cursor_start", [width // 2, height // 2])
        if isinstance(start, (list, tuple)) and len(start) == 2:
            try:
                self._exploration_cursor_x = max(0, min(width - 1, int(start[0])))
                self._exploration_cursor_y = max(0, min(height - 1, int(start[1])))
            except (TypeError, ValueError):
                self._exploration_cursor_x, self._exploration_cursor_y = width // 2, height // 2
        else:
            self._exploration_cursor_x, self._exploration_cursor_y = width // 2, height // 2
        self._exploration_cursor_x_float = float(self._exploration_cursor_x)
        self._exploration_cursor_y_float = float(self._exploration_cursor_y)
        sequence = resolve_dialogue(self.scene, self.state)
        visit_flag = config.get("visit_flag")
        if isinstance(visit_flag, str) and visit_flag:
            # Resolve the dialogue before marking so an initial condition can
            # naturally check this flag as false on the first entry.
            self.state.set_flag(visit_flag, True)
        if sequence is not None:
            self._begin_exploration_dialogue(sequence, ExplorationMode.EXPLORATION_MENU,
                                             ExplorationMode.SCENE_DIALOG)

    def _begin_exploration_dialogue(self, sequence: Any, return_mode: ExplorationMode,
                                    mode: ExplorationMode, *, include_actions: bool = True) -> None:
        """Reuse the established typewriter/pagination flow for exploration."""
        assert self.scene is not None
        self._exploration_mode = mode
        self._exploration_dialogue_return_mode = return_mode
        if getattr(sequence, "seen_flag", None):
            self.state.set_flag(sequence.seen_flag, True)
        self._exploration_dialogue_actions = list(sequence.actions) if include_actions else []
        self._exploration_dialogue_active = True
        self.text_page_index = 0
        self.text_pages = self.renderer.paginate_text(
            self._format_scene_text(sequence.text),
            self.scene.get("font_size", self.manifest.get("render", {}).get("font_size", 14)),
        )
        self._reset_dialogue_animation()
        if self._instant_scene_text_enabled():
            self._finish_dialogue_page()

    def _update_exploration(self) -> bool:
        """Advance typewriter text, event waits, and scene animations per frame."""
        assert self.scene is not None
        changed = self.renderer.animation_changed(self.scene)
        if self._exploration_runtime is not None:
            object_animation_changed = getattr(self.renderer, "exploration_animation_changed", None)
            if object_animation_changed is not None:
                changed |= object_animation_changed(set(self._exploration_runtime.object_animations.values()))
        if self._exploration_mode == ExplorationMode.LOOK_MODE:
            changed |= self._update_look_cursor()
        if self._exploration_dialogue_active:
            return self._advance_dialogue_animation() or changed
        if self._exploration_runner is not None:
            return self._advance_exploration_runner() or changed
        return changed

    def _update_look_cursor(self) -> bool:
        """Move Look's cursor from held input and advance its hover animation."""
        assert self.scene is not None
        changed = False
        try:
            held = held_battle_input(self.renderer.pygame, getattr(self.renderer, "controller_input", None))
        except AttributeError:
            # Lightweight headless renderers do not expose pygame's key state.
            held = None
        if held is not None and (held.move_x or held.move_y):
            config = exploration_config(self.scene) or {}
            speed = float(config.get("cursor_speed", 180))
            dt = max(0.0, float(getattr(self, "_battle_dt", 0.0)))
            width, height = self.renderer.config.width, self.renderer.config.height
            old_x, old_y = self._exploration_cursor_x, self._exploration_cursor_y
            self._exploration_cursor_x_float = max(0.0, min(width - 1.0,
                self._exploration_cursor_x_float + held.move_x * speed * dt))
            self._exploration_cursor_y_float = max(0.0, min(height - 1.0,
                self._exploration_cursor_y_float + held.move_y * speed * dt))
            self._exploration_cursor_x = round(self._exploration_cursor_x_float)
            self._exploration_cursor_y = round(self._exploration_cursor_y_float)
            changed |= (old_x, old_y) != (self._exploration_cursor_x, self._exploration_cursor_y)

        target = look_target_at(resolve_look_targets(self.scene, self.state, self._exploration_runtime),
                                self._exploration_cursor_x, self._exploration_cursor_y)
        if target is not None and target.interaction in {"inspect", "action"} and not self._exploration_main_button_down:
            phase = self.renderer.pygame.time.get_ticks() // 500
            changed |= phase != self._exploration_cursor_animation_phase
            self._exploration_cursor_animation_phase = phase
        else:
            self._exploration_cursor_animation_phase = None
        return changed

    def _start_exploration_event(self, actions: list[dict[str, Any]], return_mode: ExplorationMode) -> bool:
        assert self.scene is not None and self._exploration_runtime is not None
        config = exploration_config(self.scene) or {}
        events = config.get("look_events", {})
        self._exploration_runner = EventRunner(
            actions, self.state, self._exploration_runtime,
            events if isinstance(events, dict) else {}, set(self.items),
            max_hp_resolver=lambda: self.inventory.effective_stats(self.state)["max_hp"],
        )
        self._exploration_return_mode = return_mode
        self._exploration_mode = ExplorationMode.LOOK_EVENT
        return self._advance_exploration_runner()

    def _advance_exploration_runner(self, signals: list[Any] | None = None) -> bool:
        """Dispatch one non-blocking batch of registry-owned event requests."""
        runner = self._exploration_runner
        if runner is None:
            return False
        changed = False
        if signals is None:
            signals = runner.advance(self.renderer.pygame.time.get_ticks())
        for signal in signals:
            changed = True
            if signal.kind == "sound":
                self.audio.play_sfx(str(signal.data["file"]))
            elif signal.kind == "music":
                if signal.data.get("stop"):
                    self.audio.stop_music()
                elif signal.data.get("file"):
                    self.audio.play_music(str(signal.data["file"]))
            elif signal.kind == "dialog":
                assert self.scene is not None
                sequence = resolve_dialogue(self.scene, self.state, signal.data["dialog"])
                if sequence is None:
                    raise StoryValidationError("Exploration dialog action resolved no dialogue")
                # Sequence actions happen after its text but before the next
                # event action, preserving the YAML list's authored order.
                runner.insert_next(sequence.actions)
                self._begin_exploration_dialogue(sequence, ExplorationMode.LOOK_EVENT,
                                                 ExplorationMode.LOOK_EVENT, include_actions=False)
                return True
            elif signal.kind == "scene_transition":
                from engine.core.story_interpreter import Transition
                self._dispatch_transition(Transition(kind="goto", scene_id=str(signal.data["scene"])))
                return True
        if runner.finished:
            self._exploration_runner = None
            self._exploration_mode = self._exploration_return_mode
            changed = True
        return changed

    def _complete_exploration_dialogue(self) -> bool:
        """Return a finished typewriter session to its owning exploration state."""
        self._exploration_dialogue_active = False
        if self._exploration_runner is not None:
            return self._advance_exploration_runner_after_dialogue()
        if self._exploration_dialogue_actions:
            actions, self._exploration_dialogue_actions = self._exploration_dialogue_actions, []
            return self._start_exploration_event(actions, self._exploration_dialogue_return_mode)
        self._exploration_mode = self._exploration_dialogue_return_mode
        return True

    def _advance_exploration_runner_after_dialogue(self) -> bool:
        runner = self._exploration_runner
        if runner is None:
            return False
        # resume_dialogue can itself emit another dialogue signal, so feed its
        # exact batch through the same dispatcher rather than losing it.
        return self._advance_exploration_runner(
            runner.resume_dialogue(self.renderer.pygame.time.get_ticks())
        )

    def _exploration_render_view(self) -> dict[str, Any]:
        """Build a renderer-only view model from pure scene/inventory rules."""
        assert self.scene is not None
        config = exploration_config(self.scene) or {}
        runtime = self._exploration_runtime or SceneRuntime()
        mode = self._exploration_mode or ExplorationMode.EXPLORATION_MENU
        view: dict[str, Any] = {
            "mode": mode.value,
            "objects": resolve_scene_objects(self.scene, self.state, runtime),
            "sprite_overrides": runtime.sprite_overrides,
            "object_animations": runtime.object_animations,
        }
        if self._exploration_dialogue_active:
            page = self._current_dialogue_page()
            view["dialogue"] = page[:self.visible_characters]
            return view
        if mode == ExplorationMode.EXPLORATION_MENU:
            view["selected"] = self._exploration_action_selected
        elif mode == ExplorationMode.MOVE_MENU:
            view["selected"] = self._exploration_move_selected
            view["destinations"] = available_navigation(self.scene, self.state)
        elif mode in {ExplorationMode.LOOK_MODE, ExplorationMode.LOOK_EVENT}:
            if mode == ExplorationMode.LOOK_MODE:
                target = look_target_at(resolve_look_targets(self.scene, self.state, runtime),
                                        self._exploration_cursor_x, self._exploration_cursor_y)
                view["cursor"] = {
                    "x": self._exploration_cursor_x,
                    "y": self._exploration_cursor_y,
                    "interaction": target.interaction if target is not None else None,
                    "pressed": self._exploration_main_button_down,
                }
        elif mode in {ExplorationMode.BAG, ExplorationMode.ITEM_ACTION_MENU, ExplorationMode.TOSS_CONFIRMATION}:
            view["inventory"] = self._inventory_render_data()
            view["item_actions"] = self._exploration_item_actions
            view["modal_selected"] = self._exploration_modal_selected
        return view

    def _inventory_item_ids(self) -> list[str]:
        ids = self.inventory.owned_item_ids(self.state)
        self.inventory_grid.normalize(len(ids))
        return ids

    def _selected_inventory_item_id(self) -> str | None:
        ids = self._inventory_item_ids()
        return ids[self.inventory_grid.selected] if ids else None

    def _inventory_render_data(self) -> dict[str, Any]:
        ids = self._inventory_item_ids()
        entries: list[dict[str, Any]] = []
        for item_id in ids:
            definition = self.inventory.definition(item_id)
            entries.append({
                "id": item_id,
                "name": definition.name,
                "type": definition.item_type,
                "description": definition.description,
                "icon": definition.icon,
                "stats": dict(definition.stats),
                "quantity": self.state.inventory.get(item_id, 0),
                "equipped": item_id in self.state.equipment.values(),
            })
        selected_id = self._selected_inventory_item_id()
        selected = next((entry for entry in entries if entry["id"] == selected_id), None)
        return {
            "columns": self.inventory_layout.columns,
            "rows": self.inventory_layout.rows,
            "items": entries,
            "selected": self.inventory_grid.selected,
            "page": self.inventory_grid.page,
            "item": selected,
        }

    def _handle_exploration_action(self, action: str) -> bool:
        """Route one semantic input action through the current UI state."""
        if self._exploration_dialogue_active:
            return self._handle_exploration_dialogue_action(action)
        if self._exploration_runner is not None:
            # An authored wait or non-dialogue event owns control until it
            # resolves on a future frame.
            return False
        mode = self._exploration_mode
        if mode == ExplorationMode.EXPLORATION_MENU:
            return self._handle_exploration_root_action(action)
        if mode == ExplorationMode.MOVE_MENU:
            return self._handle_exploration_move_action(action)
        if mode == ExplorationMode.LOOK_MODE:
            return self._handle_exploration_look_action(action)
        if mode == ExplorationMode.BAG:
            return self._handle_exploration_bag_action(action)
        if mode == ExplorationMode.ITEM_ACTION_MENU:
            return self._handle_exploration_item_action(action)
        if mode == ExplorationMode.TOSS_CONFIRMATION:
            return self._handle_exploration_toss_action(action)
        return False

    def _handle_exploration_dialogue_action(self, action: str) -> bool:
        if self.visible_characters < len(self._current_dialogue_page()):
            if action == SELECT:
                self._finish_dialogue_page()
                return True
            return False
        if self.text_page_index < len(self.text_pages) - 1:
            if action == SELECT:
                self.audio.play_sfx(self.DIALOGUE_PAGE_FINISHED_SFX)
                self.text_page_index += 1
                self._reset_dialogue_animation()
                self._dialogue_input_unlock_ms = self.renderer.pygame.time.get_ticks() + self.DIALOGUE_PAGE_DELAY_MS
                return True
            return False
        if action == SELECT:
            return self._complete_exploration_dialogue()
        # Back intentionally leaves an active dialogue untouched.  This is
        # safer than accidentally unwinding an event's state mid-sequence.
        return False

    def _handle_exploration_root_action(self, action: str) -> bool:
        if action in (LEFT, RIGHT):
            before = self._exploration_action_selected
            delta = -1 if action == LEFT else 1
            self._exploration_action_selected = (before + delta) % 3
            if self._exploration_action_selected != before:
                self.audio.play_sfx(self.MENU_CURSOR_SFX)
                return True
        if action == SELECT:
            self.audio.play_sfx(self.MENU_SELECT_SFX)
            if self._exploration_action_selected == 0:
                self._exploration_move_selected = 0
                self._exploration_mode = ExplorationMode.MOVE_MENU
            elif self._exploration_action_selected == 1:
                self._exploration_mode = ExplorationMode.LOOK_MODE
            else:
                self.inventory_grid.normalize(len(self.inventory.owned_item_ids(self.state)))
                self._exploration_mode = ExplorationMode.BAG
            return True
        if action == BACK and self._history_navigation_enabled() and self._scene_history:
            self._enter_scene(self._scene_history.pop())
            return True
        # Root Back is deliberately inert without history: it should not exit
        # a player from an exploration scene unexpectedly.
        return False

    def _handle_exploration_move_action(self, action: str) -> bool:
        assert self.scene is not None
        destinations = available_navigation(self.scene, self.state)
        self._exploration_move_selected = min(self._exploration_move_selected, max(0, len(destinations) - 1))
        if action in (UP, DOWN, LEFT, RIGHT):
            next_selected = move_selection(self._exploration_move_selected, action, len(destinations))
            changed = next_selected != self._exploration_move_selected
            self._exploration_move_selected = next_selected
            if changed:
                self.audio.play_sfx(self.MENU_CURSOR_SFX)
            return changed
        if action == BACK:
            self._exploration_mode = ExplorationMode.EXPLORATION_MENU
            return True
        if action == SELECT and destinations:
            self.audio.play_sfx(self.MENU_SELECT_SFX)
            from engine.core.story_interpreter import Transition
            destination = destinations[self._exploration_move_selected]
            if "battle" in destination:
                self._dispatch_transition(Transition(
                    kind="battle",
                    battle_id=destination["battle"],
                    on_win=destination.get("on_win"),
                    on_lose=destination.get("on_lose"),
                ))
            else:
                self._dispatch_transition(Transition(kind="goto", scene_id=destination["scene"]))
            return True
        return False

    def _handle_exploration_look_action(self, action: str) -> bool:
        assert self.scene is not None
        if action in (UP, DOWN, LEFT, RIGHT):
            # Movement is sampled every frame from held keyboard/controller
            # state. Discrete direction actions stay consumed so they cannot
            # navigate another UI while Look mode is active.
            return False
        if action == BACK:
            self._exploration_mode = ExplorationMode.EXPLORATION_MENU
            self._exploration_main_button_down = False
            self._exploration_pressed_target_id = None
            return True
        if action == SELECT:
            self._exploration_main_button_down = True
            target = look_target_at(resolve_look_targets(self.scene, self.state, self._exploration_runtime),
                                    self._exploration_cursor_x, self._exploration_cursor_y)
            self._exploration_pressed_target_id = target.id if target is not None else None
            return True
        if action == SELECT_RELEASE:
            was_down = self._exploration_main_button_down
            pressed_target_id = self._exploration_pressed_target_id
            self._exploration_main_button_down = False
            self._exploration_pressed_target_id = None
            if not was_down or pressed_target_id is None:
                return was_down
            target = look_target_at(resolve_look_targets(self.scene, self.state, self._exploration_runtime),
                                    self._exploration_cursor_x, self._exploration_cursor_y)
            if target is None or target.id != pressed_target_id:
                return True
            self.audio.play_sfx(self.MENU_SELECT_SFX)
            return self._start_exploration_event(look_event_actions(self.scene, target.event), ExplorationMode.LOOK_MODE)
        return False

    def _handle_exploration_bag_action(self, action: str) -> bool:
        ids = self._inventory_item_ids()
        if action in (UP, DOWN, LEFT, RIGHT):
            changed = self.inventory_grid.move(action, len(ids))
            if changed:
                self.audio.play_sfx(self.MENU_CURSOR_SFX)
            return changed
        if action == BACK:
            self._exploration_mode = ExplorationMode.EXPLORATION_MENU
            return True
        if action == SELECT:
            item_id = self._selected_inventory_item_id()
            if item_id is None:
                return False
            actions = self.inventory.available_actions(self.state, item_id)
            if not actions:
                return False
            self.audio.play_sfx(self.MENU_SELECT_SFX)
            self._exploration_item_actions = actions
            self._exploration_modal_selected = 0
            self._exploration_mode = ExplorationMode.ITEM_ACTION_MENU
            return True
        return False

    def _handle_exploration_item_action(self, action: str) -> bool:
        if action in (UP, DOWN, LEFT, RIGHT):
            next_selected = move_selection(self._exploration_modal_selected, action, len(self._exploration_item_actions))
            changed = next_selected != self._exploration_modal_selected
            self._exploration_modal_selected = next_selected
            if changed:
                self.audio.play_sfx(self.MENU_CURSOR_SFX)
            return changed
        if action == BACK:
            self._exploration_mode = ExplorationMode.BAG
            return True
        if action != SELECT or not self._exploration_item_actions:
            return False
        item_id = self._selected_inventory_item_id()
        if item_id is None:
            self._exploration_mode = ExplorationMode.BAG
            return True
        selected_action = self._exploration_item_actions[self._exploration_modal_selected]
        if selected_action == "toss":
            self.audio.play_sfx(self.MENU_SELECT_SFX)
            self._exploration_modal_selected = 0  # No is always the safe default.
            self._exploration_mode = ExplorationMode.TOSS_CONFIRMATION
            return True
        try:
            if selected_action == "equip":
                self.inventory.equip(self.state, item_id)
            elif selected_action == "unequip":
                self.inventory.unequip(self.state, item_id)
            elif selected_action == "use":
                item = self.inventory.definition(item_id)
                # Use effects share the same ordered event runner as Look
                # interactions.  Appending removal preserves consumption for
                # asynchronous dialogue/sound effects as well as simple heal.
                actions = [dict(event) for event in item.use_actions]
                actions.append({"type": "remove_item", "item": item_id, "quantity": 1})
                self.audio.play_sfx(self.MENU_SELECT_SFX)
                return self._start_exploration_event(actions, ExplorationMode.BAG)
            else:
                return False
        except InventoryActionError:
            return False
        self.audio.play_sfx(self.MENU_SELECT_SFX)
        self._exploration_mode = ExplorationMode.BAG
        return True

    def _handle_exploration_toss_action(self, action: str) -> bool:
        if action in (UP, DOWN, LEFT, RIGHT):
            next_selected = move_selection(self._exploration_modal_selected, action, 2)
            changed = next_selected != self._exploration_modal_selected
            self._exploration_modal_selected = next_selected
            if changed:
                self.audio.play_sfx(self.MENU_CURSOR_SFX)
            return changed
        if action == BACK:
            self._exploration_mode = ExplorationMode.ITEM_ACTION_MENU
            return True
        if action != SELECT:
            return False
        if self._exploration_modal_selected == 0:
            self._exploration_mode = ExplorationMode.ITEM_ACTION_MENU
            return True
        item_id = self._selected_inventory_item_id()
        if item_id is None:
            self._exploration_mode = ExplorationMode.BAG
            return True
        try:
            self.inventory.toss(self.state, item_id)
        except InventoryActionError:
            return False
        self.audio.play_sfx(self.MENU_SELECT_SFX)
        self.inventory_grid.normalize(len(self.inventory.owned_item_ids(self.state)))
        self._exploration_mode = ExplorationMode.BAG
        return True

    def _update_game_over(self, dt: float) -> bool:
        """Advance the presentation and carry out its terminal requests."""
        assert self.game_over is not None
        changed, fade_music = self.game_over.update(dt)
        for filename in self.game_over.consume_audio_events():
            self.audio.play_sfx(filename)
        if fade_music:
            self.audio.fadeout_music(self.game_over.GET_UP_FADE_DURATION)
        if self.game_over.finished:
            self.running = False
            return True
        if self.game_over.load_ready:
            self.game_over.load_ready = False
            if not self._handle_load_checkpoint():
                # A missing/corrupt save should leave a recoverable game-over
                # menu instead of repeatedly trying to load every frame.
                self.game_over.stage = GameOverStage.MENU
            return True
        return changed

    def _begin_game_over_menu(self) -> None:
        """Detach a configured loss sequence once its timed menu is ready."""
        assert self.battle is not None
        cutscene = self.battle.game_over_cutscene
        on_lose = self.battle.config.on_lose
        assert cutscene is not None and on_lose is not None and on_lose.game_over is not None
        self._game_over_text = on_lose.game_over.text
        self.game_over = GameOverPresentation(cutscene.x, cutscene.y, random.Random(), self._game_over_text,
                                              stage=GameOverStage.MENU)
        self.selected = 0
        self._pending_selection = None
        self.battle = None
        self._battle_on_win = self._battle_on_lose = None
        self._battle_test_sequence = None

    def _current_dialogue_page(self) -> str:
        return self.text_pages[self.text_page_index] if self.text_pages else ""

    def _reset_dialogue_animation(self) -> None:
        self.visible_characters = 0
        self._word_letter_count = 0
        self._next_dialogue_character_ms = self.renderer.pygame.time.get_ticks()

    def _finish_dialogue_page(self) -> None:
        self.visible_characters = len(self._current_dialogue_page())

    def _advance_dialogue_animation(self) -> bool:
        """Reveal pre-wrapped dialogue characters on a timed cadence."""
        if self.renderer.pygame.time.get_ticks() < self._dialogue_input_unlock_ms:
            return False
        page = self._current_dialogue_page()
        if self.visible_characters >= len(page):
            return False
        now = self.renderer.pygame.time.get_ticks()
        if now < self._next_dialogue_character_ms:
            return False
        # Catch up after a slow frame, but cap work to preserve responsiveness.
        revealed = 0
        while self.visible_characters < len(page) and now >= self._next_dialogue_character_ms and revealed < 8:
            char = page[self.visible_characters]
            self.visible_characters += 1
            self._next_dialogue_character_ms += self.DIALOGUE_CHARACTER_DELAY_MS
            revealed += 1
            if char.isalpha():
                self._word_letter_count += 1
                if self._word_letter_count % self.DIALOGUE_BLIP_EVERY_LETTERS == 0:
                    self.audio.play_sfx(self.DIALOGUE_BLIP_SFX)
            else:
                self._word_letter_count = 0
        return True

    def _process_pending_selection(self) -> bool:
        """Execute a menu choice after its select sound has time to play."""
        if self._pending_selection is None:
            return False
        if self.renderer.pygame.time.get_ticks() < self._selection_advance_ms:
            return False
        pending, self._pending_selection = self._pending_selection, None
        self._choose_scene_option()
        return True

    def _choose_scene_option(self) -> None:
        choice = self.choices[self.selected]
        transition = self.interpreter.resolve_choice(choice)
        self._dispatch_sfx(transition.sfx)
        self._dispatch_transition(transition)

    def _dispatch_sfx(self, filenames: list[str]) -> None:
        for filename in filenames:
            self.audio.play_sfx(filename)

    def _dispatch_battle_audio(self) -> None:
        """Execute controller-owned presentation cues exactly once."""
        if self.battle is None:
            return
        for event in self.battle.consume_audio_events():
            kind, filename = event[:2]
            pitch = event[2] if len(event) == 3 else 1.0
            if kind == "stop_music":
                self.audio.stop_music()
            elif kind == "music_sequence" and filename and isinstance(pitch, str):
                self.audio.play_music_sequence(filename, pitch)
            elif kind == "fade_music":
                self.audio.fadeout_music(float(pitch))
            elif kind == "music" and filename:
                self.audio.play_music(filename, fade_in=float(pitch))
            elif kind == "sfx" and filename:
                self.audio.play_sfx(filename, pitch=pitch)

    def _dispatch_transition(self, transition: Transition) -> None:
        if transition.kind == "goto":
            target = transition.scene_id or self.state.current_scene
            if self._history_navigation_enabled() and self.scene is not None and target != self.state.current_scene:
                self._scene_history.append(self.state.current_scene)
            self._enter_scene(target)
        elif transition.kind == "random_event":
            if not self._run_random_event(transition.event_pool_id or ""):
                self._enter_scene(self.state.current_scene)
        elif transition.kind == "battle":
            self._start_battle(transition)

    def _start_battle(self, transition: Transition) -> None:
        data = self._load_battle_definition(transition.battle_id or "")
        def defense_sprite_exists(filename: str) -> bool:
            try:
                self.assets.resolve_asset_path("sprites", filename)
                return True
            except AssetNotFoundError:
                return False
        config = load_battle_config(data, self.items, f"battles/{transition.battle_id}.yaml",
                                    self.combat_move_config, sprite_exists=defense_sprite_exists)
        if data.get("music"):
            self.audio.play_music(data["music"])
        self.battle = BattleController(config, self.state, self.items)
        sequence = transition.test_sequence
        if sequence and sequence.get("replay"):
            sequence = dict(self._battle_test_sequence or {})
            sequence["difficulty"] = transition.test_sequence.get("difficulty", sequence.get("difficulty", 1))
        if sequence and not self.battle.start_test_sequence(sequence):
            raise StoryValidationError(f"Invalid test sequence for battle {transition.battle_id!r}: {sequence!r}")
        if (sequence and not transition.test_sequence.get("replay")
                and transition.on_win != self.state.current_scene
                and self._history_navigation_enabled() and self.scene is not None):
            self._scene_history.append(self.state.current_scene)
        self.battle.debug_enabled = bool(self.manifest.get("debug", {}).get("battle", False))
        self._battle_on_win, self._battle_on_lose = transition.on_win, transition.on_lose
        self._battle_test_sequence = dict(sequence) if sequence else None
        self._battle_dt = 0.0
        self.message = None

    def _finish_battle(self) -> None:
        """Apply story-level battle outcomes after the controller is confirmed."""
        assert self.battle is not None
        outcome = self.battle.outcome
        if self.battle.test_result:
            for name, value in self.battle.test_result.items():
                self.state.set_var(f"test_{name}", value)
        if outcome == "win":
            self.interpreter.apply_rewards(self.battle.config.victory.get("rewards"))
            target = self._battle_on_win or self.state.current_scene
        elif outcome == "lose":
            target = self._battle_on_lose
        else:  # configured escape returns to the scene that initiated combat
            target = self.state.current_scene
        self.battle = None
        self._battle_on_win = self._battle_on_lose = None
        if target:
            self._enter_scene(target)
        else:
            self.running = False

    def _format_scene_text(self, text: Any) -> str:
        """Resolve lightweight ``{{variable}}`` tokens for report scenes."""
        if not isinstance(text, str):
            return ""
        return re.sub(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}",
                      lambda match: str(self.state.get_var(match.group(1), "-")), text)

    def _history_navigation_enabled(self) -> bool:
        return self.manifest.get("navigation", {}).get("back") == "history"

    def _instant_scene_text_enabled(self) -> bool:
        return bool(self.scene and (self.scene.get("instant_text")
                                    or self.manifest.get("navigation", {}).get("instant_scene_text")))

    def _instant_menu_selection_enabled(self) -> bool:
        return bool(self.manifest.get("navigation", {}).get("instant_menu_selection"))

    def _run_random_event(self, event_pool_id: str) -> bool:
        """Enter the selected event scene; return False when chance misses."""
        event_scene_id = maybe_trigger(self._load_event_pool_definition(event_pool_id))
        if event_scene_id is None:
            return False
        self._enter_scene(event_scene_id)
        return True

    def _handle_save(self) -> None:
        path = save_game(self.state, self.story_id, self.story_version, self.save_dir, self.save_slot)
        self.audio.play_sfx(self.SAVE_SFX)
        self.message = f"Game saved ({path.name})."

    def _handle_load(self) -> bool:
        return self._load_from_slot(self.save_slot, show_error=True)

    def _checkpoint_slot(self) -> str:
        """Keep automatic recovery data separate from a player's save slot."""
        return f"{self.save_slot}{self.CHECKPOINT_SLOT_SUFFIX}"

    def _save_checkpoint(self) -> None:
        """Persist a recovery snapshot after entering an authored checkpoint."""
        save_game(self.state, self.story_id, self.story_version, self.save_dir, self._checkpoint_slot())

    def _handle_load_checkpoint(self) -> bool:
        """Restore the most recently authored checkpoint after a game over."""
        return self._load_from_slot(self._checkpoint_slot(), show_error=False)

    def _load_from_slot(self, slot: str, *, show_error: bool) -> bool:
        try:
            self.state = load_game(self.save_dir, slot, self.story_id)
            # Saves created before learned moves were introduced receive the
            # profile's authored starting set when first loaded.
            if not self.state.known_moves:
                profile_levels = self.player_profile.get("move_skill_levels", {})
                if not isinstance(profile_levels, dict):
                    profile_levels = {}
                for move_data in self.player_profile.get("known_moves", []):
                    if isinstance(move_data, dict):
                        move_id, level = move_data.get("id"), move_data.get("initial_level", 1)
                    else:
                        move_id, level = move_data, profile_levels.get(move_data, 1)
                    if isinstance(move_id, str):
                        self.state.learn_move(move_id, int(level) if isinstance(level, int) and not isinstance(level, bool) else 1)
            self._initialize_move_skill_defaults()
            self.interpreter.state = self.state
            self.battle = None
            self._scene_history = []
            self._enter_scene(self.state.current_scene)
            return True
        except SaveVersionError as error:
            if show_error:
                self.message = f"Couldn't load: {error}"
            return False

    def _initialize_move_skill_defaults(self) -> None:
        """Give newly known moves their authored starting level before save.

        Validation still happens when a battle is configured; this small
        bootstrap only keeps a just-created save complete even before the
        player has entered combat for the first time.
        """
        for move in self.moves:
            move_id = move.get("id") if isinstance(move, dict) else None
            if not isinstance(move_id, str) or move_id not in self.state.known_moves:
                continue
            if move_id not in self.state.known_combat_moves:
                self.state.known_combat_moves[move_id] = {
                    "current_level": initial_difficulty_level(move),
                    "recent_scores": [],
                }
