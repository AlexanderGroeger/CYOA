"""Pure display configuration and geometry helpers.

Keeping these calculations independent from pygame makes story validation and
letterboxing behaviour easy to test without creating a window.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from engine.errors import StoryValidationError


@dataclass(frozen=True)
class DisplayConfig:
    width: int
    height: int


def parse_display_config(manifest: dict[str, Any]) -> DisplayConfig:
    """Read and validate the required logical canvas dimensions."""
    raw = manifest.get("display")
    if not isinstance(raw, dict):
        raise StoryValidationError(
            "story.yaml requires display.width and display.height for the pygame logical canvas"
        )
    width, height = raw.get("width"), raw.get("height")
    # bool is an int subclass, but never a meaningful screen dimension.
    if isinstance(width, bool) or isinstance(height, bool) or not isinstance(width, int) or not isinstance(height, int):
        raise StoryValidationError("display.width and display.height must be positive integers")
    if width <= 0 or height <= 0:
        raise StoryValidationError("display.width and display.height must be greater than zero")
    return DisplayConfig(width, height)


def integer_scale(display_width: int, display_height: int, game_width: int, game_height: int) -> int:
    if min(display_width, display_height, game_width, game_height) <= 0:
        raise ValueError("display and game dimensions must all be positive")
    scale = min(display_width // game_width, display_height // game_height)
    if scale < 1:
        raise ValueError("logical game surface is larger than the desktop display")
    return scale


def centered_rect(display_width: int, display_height: int, game_width: int, game_height: int, scale: int) -> tuple[int, int, int, int]:
    if scale < 1:
        raise ValueError("scale must be at least 1")
    width, height = game_width * scale, game_height * scale
    return ((display_width - width) // 2, (display_height - height) // 2, width, height)


def chunk_lines(lines: list[str], capacity: int) -> list[list[str]]:
    """Split pre-wrapped dialogue lines into non-empty screenfuls."""
    if capacity <= 0:
        raise ValueError("text page capacity must be positive")
    return [lines[index:index + capacity] for index in range(0, len(lines), capacity)] or [[]]
