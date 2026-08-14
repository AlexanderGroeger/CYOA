"""Integration coverage for the StoryProject-backed renderer scene boundary."""

from __future__ import annotations

from pathlib import Path

from engine.core import game_engine as game_engine_module
from engine.core.game_engine import GameEngine
from story_core_fixture import write_fixture_story


class _RecordingRenderer:
    def __init__(self, assets, display_config, render_config):
        self.assets = assets
        self.config = display_config
        self.render_config = render_config
        self.rendered_scene = None

    def paginate_text(self, text, _font_size):
        return [text]

    def render(self, scene, *_args, **_kwargs):
        self.rendered_scene = scene


class _Audio:
    def __init__(self, assets, **_kwargs):
        self.assets = assets

    def preload_sfx(self, _filename: str) -> None:
        pass


def test_renderer_receives_current_project_scene_mapping_without_definition_reload(
    tmp_path: Path, monkeypatch
) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    monkeypatch.setattr(game_engine_module, "Renderer", _RecordingRenderer)
    monkeypatch.setattr(game_engine_module, "AudioSystem", _Audio)

    engine = GameEngine(str(story_root), str(shared_root))

    def fail_legacy_scene(_scene_id):
        raise AssertionError("renderer path must not reload authored scene YAML")

    monkeypatch.setattr(engine.assets, "load_scene", fail_legacy_scene)
    monkeypatch.setattr(engine, "_reset_dialogue_animation", lambda: None)

    engine._enter_scene("intro")

    assert engine.renderer.rendered_scene is engine.scene
    assert engine.renderer.rendered_scene["text"] == "Welcome."
    assert engine.renderer.rendered_scene["choices"][0]["text"] == "Continue"

    # The renderer-facing mapping is intentionally mutable for legacy runtime
    # consumers, while the canonical StoryProject remains isolated.
    engine.renderer.rendered_scene["text"] = "runtime presentation mutation"
    assert engine.story_project.scene("intro").text == "Welcome."
