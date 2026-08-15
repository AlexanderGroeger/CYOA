"""Standalone pygame runner for one real player-attack QTE.

This module is intentionally not a second QTE implementation.  It resolves a
move through the normal progression boundary and feeds the resulting object
the same actions, held movement, clock time, and renderer used by battle.
"""

from __future__ import annotations

from collections.abc import Mapping
import random
from typing import Any

from engine.audio.audio_system import AudioSystem
from engine.battle.controls import held_battle_input
from engine.battle.move_progression import resolve_combat_move, result_score
from engine.battle.qte import AttackQTE, create_attack_qte
from engine.core.asset_loader import AssetLoader
from engine.core.developer_test import QteTestConfiguration
from engine.errors import EngineError
from engine.render.display import parse_display_config
from engine.render.renderer import BATTLE_SMALL_TEXT_SIZE, BATTLE_TITLE_SIZE, Renderer
from engine.render.terminal_input import QUIT, SELECT, SELECT_RELEASE, action_from_event


class QteTestRuntimeError(EngineError):
    """A selected QTE could not be resolved or run."""


def find_test_move(move_config: Mapping[str, Any], move_id: str) -> Mapping[str, Any]:
    """Find a global move without interpreting its authored root shape."""

    entries = move_config.get("moves", ())
    if isinstance(entries, Mapping):
        entries = entries.values()
    try:
        candidates = iter(entries)
    except TypeError:
        candidates = iter(())
    for move in candidates:
        if isinstance(move, Mapping) and move.get("id") == move_id:
            return move
    raise QteTestRuntimeError(f"Combat move {move_id!r} was not found in the global move registry")


def resolve_test_qte(move_config: Mapping[str, Any], configuration: QteTestConfiguration) -> dict[str, Any]:
    """Resolve the effective difficulty through the authoritative runtime path."""

    move = find_test_move(move_config, configuration.move_id)
    try:
        return resolve_combat_move(move, configuration.difficulty_level)
    except Exception as exc:  # validation/runtime errors must reach Designer stderr
        if isinstance(exc, QteTestRuntimeError):
            raise
        raise QteTestRuntimeError(
            f"Could not resolve QTE move {configuration.move_id!r} at level {configuration.difficulty_level}: {exc}"
        ) from exc


def create_test_qte(move_config: Mapping[str, Any], configuration: QteTestConfiguration) -> AttackQTE:
    """Construct one QTE with the real registry factory."""

    resolved = resolve_test_qte(move_config, configuration)
    rng = random.Random(configuration.seed) if configuration.seed is not None else random.Random()
    try:
        return create_attack_qte(resolved, rng)
    except Exception as exc:
        raise QteTestRuntimeError(
            f"Could not construct QTE for move {configuration.move_id!r}: {exc}"
        ) from exc


def run_qte_test(story_dir: str, shared_dir: str, configuration: QteTestConfiguration) -> int:
    """Run the isolated QTE process; return a normal process exit code."""

    try:
        assets = AssetLoader(story_dir, shared_dir)
        manifest = assets.load_manifest()
        move_config = assets.load_combat_move_config()
        qte = create_test_qte(move_config, configuration)
        renderer = Renderer(assets, parse_display_config(manifest), manifest.get("render", {}))
        preferences = assets.load_audio_config()
        audio = AudioSystem(
            assets,
            master_volume=preferences.get("master_volume", 0.8),
            music_volume=preferences.get("music_volume", 1.0),
            effects_volume=preferences.get("effects_volume", 1.0),
        )
    except QteTestRuntimeError:
        raise
    except Exception as exc:
        raise QteTestRuntimeError(f"Could not initialize QTE test runtime: {exc}") from exc

    pygame = renderer.pygame
    result = None
    running = True
    try:
        while running:
            for event in renderer.events():
                action = action_from_event(event)
                if action == QUIT:
                    running = False
                    break
                if result is not None:
                    if action == SELECT:
                        qte = create_test_qte(move_config, configuration)
                        result = None
                    continue
                if action in {SELECT, SELECT_RELEASE}:
                    qte.handle_action(action)
                    _dispatch_input_sound(audio, qte)

            if not running:
                break
            if result is None:
                before_done = qte.done
                qte.update(renderer.tick() / 1000.0, held_battle_input(pygame, renderer.controller_input))
                if qte.done and not before_done:
                    result = qte.result
                    audio.play_sfx(
                        qte.sound or {
                            "miss": "fall.wav", "weak": "damage.wav",
                            "strong": "damage.wav", "critical": "slash.wav",
                        }[result.tier]
                    )
                    score = result_score(result.tier)
                    print(f"QTE_RESULT {result.tier} {score}", flush=True)
            else:
                renderer.tick()
            if result is None:
                _render_qte(renderer, qte, configuration)
            else:
                _render_result(renderer, qte, configuration, result)
    finally:
        audio.stop_music()
        renderer.shutdown()
    return 0


def _dispatch_input_sound(audio: AudioSystem, qte: AttackQTE) -> None:
    """Keep the small QTE interaction cues present in normal battle play."""

    if qte.qte_type == "moving_weak_point":
        audio.play_sfx("arrow.wav")
    elif qte.qte_type in {"directional_combo", "rotating_strike"}:
        audio.play_sfx("hit.wav")
    elif qte.qte_type == "rhythm_combo":
        audio.play_sfx("hit.wav" if getattr(qte, "last_hit_tier", None) else "swallow.wav")
    elif qte.qte_type == "rapid_slash":
        audio.play_sfx("slash.wav" if getattr(qte, "last_slash_hit", False) else "arrow.wav")


def _qte_canvas(renderer: Renderer, qte: AttackQTE):
    pg = renderer.pygame
    width, height = renderer.config.width, renderer.config.height
    if qte.qte_type in {"precision_bar", "rhythm_combo"}:
        canvas_width, canvas_height = min(480, width - 48), 80
        return pg.Rect(width // 2 - canvas_width // 2, height // 2 - canvas_height // 2,
                       canvas_width, canvas_height)
    if qte.qte_type == "rapid_slash":
        canvas_height = min(330, height - 130)
        canvas_width = max(110, min(160, round(canvas_height * .46)))
        return pg.Rect(width // 2 - canvas_width // 2, 62, canvas_width, canvas_height)
    canvas_size = min(230, height - 130)
    return pg.Rect(width // 2 - canvas_size // 2, 62, canvas_size, canvas_size)


def _render_qte(renderer: Renderer, qte: AttackQTE, configuration: QteTestConfiguration) -> None:
    renderer.surface.fill((12, 12, 28))
    title = renderer._text_surface(
        f"{configuration.move_id}  /  Level {configuration.difficulty_level}",
        BATTLE_TITLE_SIZE, (255, 240, 190),
    )
    renderer.surface.blit(title, title.get_rect(centerx=renderer.config.width // 2, y=16))
    renderer._draw_attack_qte(qte, _qte_canvas(renderer, qte))
    hint = renderer._text_surface(qte.tutorial_instruction, BATTLE_SMALL_TEXT_SIZE, (245, 245, 255))
    renderer.surface.blit(hint, hint.get_rect(centerx=renderer.config.width // 2, bottom=renderer.config.height - 16))
    renderer._present()


def _render_result(renderer: Renderer, qte: AttackQTE, configuration: QteTestConfiguration, result: Any) -> None:
    renderer.surface.fill((12, 12, 28))
    width = renderer.config.width
    lines = (
        (f"{configuration.move_id}  /  Level {configuration.difficulty_level}", BATTLE_SMALL_TEXT_SIZE, (180, 185, 220)),
        (f"Result: {result.label}", BATTLE_TITLE_SIZE, (255, 230, 135)),
        (f"Score: {result_score(result.tier)}", BATTLE_SMALL_TEXT_SIZE, (245, 245, 255)),
        ("Enter — Retry", BATTLE_SMALL_TEXT_SIZE, (245, 245, 255)),
        ("Escape — Exit", BATTLE_SMALL_TEXT_SIZE, (245, 245, 255)),
    )
    y = 58
    for text, size, color in lines:
        glyph = renderer._text_surface(text, size, color)
        renderer.surface.blit(glyph, glyph.get_rect(centerx=width // 2, y=y))
        y += glyph.get_height() + 12
    # Metrics are owned by QTEResult.  Show only values that the real QTE
    # supplied, without creating harness-specific scoring fields.
    metrics = ", ".join(f"{key}: {value}" for key, value in result.metrics.items())
    if metrics:
        glyph = renderer._text_surface(metrics[:100], 10, (170, 200, 220))
        renderer.surface.blit(glyph, glyph.get_rect(centerx=width // 2, y=y + 8))
    renderer._present()


__all__ = [
    "QteTestRuntimeError", "create_test_qte", "find_test_move", "resolve_test_qte", "run_qte_test",
]
