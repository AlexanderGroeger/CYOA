"""Renderer coverage for Look mode's built-in cursor sprite set."""

from __future__ import annotations

from engine.render.renderer import Renderer


class _Time:
    def __init__(self, ticks):
        self.ticks = ticks

    def get_ticks(self):
        return self.ticks


class _Pygame:
    def __init__(self, ticks):
        self.time = _Time(ticks)


class _Image:
    def get_rect(self, **kwargs):
        return kwargs


class _Surface:
    def __init__(self):
        self.blits = []

    def blit(self, image, rect):
        self.blits.append((image, rect))


def _renderer(ticks=0):
    renderer = Renderer.__new__(Renderer)
    renderer.pygame = _Pygame(ticks)
    renderer.surface = _Surface()
    renderer.loaded = []

    def image_reference(category, filename):
        renderer.loaded.append((category, filename))
        return filename, _Image()

    renderer._image_reference = image_reference
    return renderer


def test_look_cursor_uses_centered_builtin_sprites_and_half_second_frames():
    renderer = _renderer(500)
    renderer._draw_exploration_cursor({"x": 12, "y": 34, "interaction": "inspect"})
    assert renderer.loaded == [("sprites", "cursor/inspect2.png")]
    assert renderer.surface.blits[-1][1] == {"center": (12, 34)}

    renderer = _renderer(0)
    renderer._draw_exploration_cursor({"x": 12, "y": 34, "interaction": "action"})
    assert renderer.loaded == [("sprites", "cursor/activate1.png")]

    renderer._draw_exploration_cursor({"x": 12, "y": 34, "interaction": "action", "pressed": True})
    assert renderer.loaded[-1] == ("sprites", "cursor/click.png")

    renderer._draw_exploration_cursor({"x": 12, "y": 34})
    assert renderer.loaded[-1] == ("sprites", "cursor/default.png")
